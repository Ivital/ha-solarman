"""Read-only periodic RAKURS operational monitoring.
Only diagnostic episode metadata is persisted in the systemd StateDirectory.
No Modbus connection, no MySQL writes, no external notification channel.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import json
import math
import os
import re
import stat
import tempfile

STATE=Path("/var/lib/rakurs-alerts/state.json")
CONFIG=Path("/etc/rakurs/alert_rules.json")
KEY=re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_RULES=24
MAX_EVENTS=200

def utc_now():
    return datetime.now(timezone.utc)

def parse_time(value):
    if isinstance(value,str):
        value=datetime.fromisoformat(value.replace("Z","+00:00"))
    if not isinstance(value,datetime):
        raise ValueError("Invalid timestamp")
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

def iso(value):
    return parse_time(value).isoformat().replace("+00:00","Z")

def decimal(value):
    if value is None or isinstance(value,bool):
        raise ValueError("Missing number")
    try:
        n=Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid number") from exc
    if not n.is_finite() or abs(n)>Decimal("1000000000"):
        raise ValueError("Invalid number")
    return n

def rules_from_config(value):
    if not isinstance(value,dict) or not isinstance(value.get("rules",[]),list):
        raise ValueError("Invalid alarm rules")
    rules=value.get("rules",[])
    if len(rules)>MAX_RULES:
        raise ValueError("Too many rules")
    result={}
    for rule in rules:
        if not isinstance(rule,dict):
            raise ValueError("Invalid rule")
        key=rule.get("id")
        signal=rule.get("signal")
        if not isinstance(key,str) or not KEY.fullmatch(key) or key in result or not isinstance(signal,str) or not KEY.fullmatch(signal):
            raise ValueError("Invalid or duplicate rule identifier")
        unit=rule.get("unit")
        if not isinstance(unit,str) or not unit or len(unit)>32 or type(rule.get("slot")) is not int or rule["slot"] not in (1,2):
            raise ValueError("Invalid rule device or unit")
        direction=rule.get("direction")
        if direction not in ("above","below"):
            raise ValueError("Invalid direction")
        threshold,hysteresis=decimal(rule.get("threshold")),decimal(rule.get("hysteresis"))
        delay,gap=rule.get("delay_seconds"),rule.get("max_gap_seconds")
        if hysteresis<0 or type(delay) is not int or type(gap) is not int or not 0<=delay<=86400 or not 60<=gap<=86400:
            raise ValueError("Invalid alarm timing")
        result[key]={"slot":rule["slot"],"signal":signal,"unit":unit,"direction":direction,
                     "threshold":str(threshold),"hysteresis":str(hysteresis),"delay":delay,"gap":gap}
    return result

def load_rules():
    try:
        with CONFIG.open("r",encoding="utf-8") as stream:
            meta=os.fstat(stream.fileno())
            if meta.st_uid!=0 or not stat.S_ISREG(meta.st_mode) or meta.st_mode&0o037 or meta.st_size>65536:
                raise ValueError("Unsafe rules file")
            return rules_from_config(json.load(stream))
    except FileNotFoundError:
        return {}

def empty_state():
    return {"version":1,"checked_at_utc":None,"rules":{},"events":[]}

def read_state(path=STATE):
    try:
        with path.open("r",encoding="utf-8") as stream:
            if os.fstat(stream.fileno()).st_size>1048576:
                raise ValueError("State too large")
            value=json.load(stream)
        if value.get("version")!=1 or not isinstance(value.get("rules"),dict) or not isinstance(value.get("events"),list):
            raise ValueError("Invalid state")
        return value
    except FileNotFoundError:
        return empty_state()

def write_state(value,path=STATE):
    fd,name=tempfile.mkstemp(prefix=".state-",dir=path.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as stream:
            json.dump(value,stream,ensure_ascii=False,separators=(",",":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name,0o640)
        os.replace(name,path)
    finally:
        if os.path.exists(name):
            os.unlink(name)

def transition(state,key,label,condition,stamp,delay,gap,definition):
    """Condition: True = enter, False = clear, None = unknown, 'hold' = hysteresis.
    Use strictly advancing observation timestamps; never count repeated old samples.
    """
    at=iso(stamp)
    old=state["rules"].get(key,{})
    if old.get("definition")!=definition:
        if old.get("phase")=="active":
            state["events"].append({"key":key,"label":old["label"],"kind":"unknown","at":at,"start":old.get("start"),"reason":"rule_changed"})
        old={}
    entry={**old,"label":label,"definition":definition}
    last=old.get("last")
    phase=old.get("phase","normal")
    def event(kind,reason=None):
        state["events"].append({"key":key,"label":label,"kind":kind,"at":at,
                               "start":entry.get("start"),"reason":reason})
    if condition is None:
        if phase=="active":
            event("unknown","data_unavailable")
        entry.update(phase="unknown",start=None,last=None)
    elif last and parse_time(at)<=parse_time(last):
        return
    else:
        if last and (parse_time(at)-parse_time(last)).total_seconds()>gap:
            if phase=="active":
                event("unknown","observation_gap")
            phase="normal"
            entry["start"]=None
        if condition is False:
            if phase=="active":
                event("recovered")
            entry.update(phase="normal",start=None)
        elif condition is True or (condition=="hold" and phase in ("pending","active")):
            if phase not in ("pending","active"):
                entry["start"]=at
                phase="pending"
            if phase=="pending" and (parse_time(at)-parse_time(entry["start"])).total_seconds()>=delay:
                phase="active"
                event("active")
            entry["phase"]=phase
        else:
            entry.update(phase="normal",start=None)
        entry["last"]=at
    state["rules"][key]=entry
    state["events"]=state["events"][-MAX_EVENTS:]

def condition_for(rule,reading,now):
    if not reading or reading.get("unit")!=rule["unit"] or reading.get("value_decimal") is None:
        return None,now
    try:
        stamp=parse_time(reading.get("observed_at_utc"))
        age=(now-stamp).total_seconds()
        if not -30<=age<=rule["gap"]:
            return None,now
        n=decimal(reading["value_decimal"])
        t,h=decimal(rule["threshold"]),decimal(rule["hysteresis"])
    except (TypeError,ValueError,OverflowError):
        return None,now
    if rule["direction"]=="above":
        return (True if n>=t else False if n<t-h else "hold"),stamp
    return (True if n<=t else False if n>t+h else "hold"),stamp

def run_once():
    from . import app as module
    now=utc_now()
    state=read_state()
    before=len(state["events"])
    last_event=state["events"][-1] if state["events"] else None
    try:
        rules=load_rules()
        transition(state,"configuration","Конфігурація діагностики",False,now,0,180,"config-v1")
    except (OSError,ValueError,TypeError):
        rules={}
        transition(state,"configuration","Конфігурація діагностики",True,now,0,180,"config-v1")
    active_keys={"configuration","mysql","worker"}
    try:
        with module.app.app_context():
            module.one("SELECT 1 AS n")
            health=module.collector_health()
            devices={d["slot_no"]:d for d in module.parallel_registered_devices()} if rules else {}
            transition(state,"mysql","Доступність MySQL",False,now,120,180,"mysql-v1")
            transition(state,"worker","Modbus-агент",not health["collector_active"] if health["enabled"] else None,now,120,180,"worker-v1")
            for key,rule in rules.items():
                rid="signal_"+key
                active_keys.add(rid)
                d=devices.get(rule["slot"])
                reading=module.one("SELECT m.value_decimal,m.observed_at_utc,s.unit FROM rakurs_samples m "
                    "JOIN rakurs_signals s ON s.id=m.signal_id WHERE m.device_id=%s AND s.signal_key=%s "
                    "ORDER BY m.observed_at_utc DESC LIMIT 1",(d["id"],rule["signal"])) if d else None
                label="Deye №"+str(rule["slot"])+" · "+rule["signal"]
                condition,stamp=condition_for(rule,reading,now)
                transition(state,rid,label,condition,stamp,rule["delay"],rule["gap"],rule)
    except Exception:
        transition(state,"mysql","Доступність MySQL",True,now,120,180,"mysql-v1")
        transition(state,"worker","Modbus-агент",None,now,120,180,"worker-v1")
        for key,rule in rules.items():
            rid="signal_"+key
            active_keys.add(rid)
            transition(state,rid,"Deye №"+str(rule["slot"])+" · "+rule["signal"],None,now,rule["delay"],rule["gap"],rule)
    for key in list(state["rules"]):
        if key not in active_keys:
            old=state["rules"].pop(key)
            if old.get("phase")=="active":
                state["events"].append({"key":key,"label":old["label"],"kind":"unknown","at":iso(now),"start":old.get("start"),"reason":"rule_removed"})
    state["checked_at_utc"]=iso(now)
    state["events"]=state["events"][-MAX_EVENTS:]
    write_state(state)
    # Journal only transitions, no credentials, addresses or raw telemetry.
    if state["events"] and state["events"][-1]!=last_event:
        for item in state["events"]:
            if item.get("at")==iso(now):
                print("RAKURS_ALERT",item["key"],item["kind"],flush=True)
    return state

def public_status():
    try:
        value=read_state()
        at=value.get("checked_at_utc")
        recent=bool(at and -30<=(utc_now()-parse_time(at)).total_seconds()<=180)
        return {"state":"running" if recent else "stale" if at else "not_installed",
                "checked_at_utc":at,"rules":[{"key":key,"label":item["label"],"phase":item["phase"],
                "start":item.get("start"),"last":item.get("last")} for key,item in value["rules"].items()],
                "events":value["events"][-100:]}
    except (ValueError,OSError,TypeError,KeyError):
        return {"state":"unavailable","rules":[],"events":[]}

if __name__=="__main__":
    run_once()
