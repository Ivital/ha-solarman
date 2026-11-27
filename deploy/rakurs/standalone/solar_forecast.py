"""Optional weather-based PV potential; separate from MySQL observations.
No request is sent until a root-provisioned configuration explicitly enables it.
Open-Meteo GTI is a preceding-hour mean. No guessed coordinates or DC capacity.
"""
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from urllib.parse import urlencode
from concurrent.futures import ThreadPoolExecutor
from math import isfinite
import json
import os
import stat
import threading
import time

CONFIG = Path("/etc/rakurs/solar_forecast.json")
_CACHE = {}
_LOCK = threading.Lock()
TTL = 1800

def bounded(value, lo, hi):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not isfinite(value) or not lo <= value <= hi:
        raise ValueError("Invalid forecast setting")
    return float(value)

def validate(config):
    if not isinstance(config,dict) or config.get("enabled") is not True:
        return None
    provider=config.get("provider")
    if provider not in ("open_meteo_noncommercial","open_meteo_customer") or config.get("external_location_sharing_approved") is not True:
        raise ValueError("Provider and location sharing require explicit approval")
    key=config.get("api_key")
    if provider=="open_meteo_customer" and (not isinstance(key,str) or not 1<=len(key)<=256):
        raise ValueError("Missing customer API key")
    arrays=config.get("arrays")
    if not isinstance(arrays,list) or len(arrays)!=2 or {a.get("slot") for a in arrays if isinstance(a,dict)}!={1,2}:
        raise ValueError("Exactly two configured PV arrays required")
    result={"provider":provider,"api_key":key if provider=="open_meteo_customer" else None,
            "latitude":bounded(config.get("latitude"),-90,90),"longitude":bounded(config.get("longitude"),-180,180),"arrays":[]}
    for a in sorted(arrays,key=lambda a:a["slot"]):
        result["arrays"].append({"slot":a["slot"],"kwp":bounded(a.get("kwp"),0.01,1000),
          "tilt":bounded(a.get("tilt"),0,90),"azimuth":bounded(a.get("azimuth"),-180,180),
          "performance_ratio":bounded(a.get("performance_ratio"),0.1,1),
          "ac_limit_kw":bounded(a.get("ac_limit_kw"),0.01,20)})
    return result

def load_config(path=CONFIG):
    try:
        with path.open("r",encoding="utf-8") as stream:
            meta=os.fstat(stream.fileno())
            if meta.st_uid!=0 or not stat.S_ISREG(meta.st_mode) or meta.st_mode & 0o037:
                raise ValueError("Forecast config must be root-owned and not world accessible")
            if meta.st_size>8192:
                raise ValueError("Forecast config too large")
            return validate(json.load(stream))
    except FileNotFoundError:
        return None

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None

def retrieve(config,array):
    host="customer-api.open-meteo.com" if config["provider"]=="open_meteo_customer" else "api.open-meteo.com"
    params={"latitude":config["latitude"],"longitude":config["longitude"],
            "tilt":array["tilt"],"azimuth":array["azimuth"],
            "hourly":"global_tilted_irradiance","timeformat":"unixtime","timezone":"UTC","forecast_hours":74}
    if config["api_key"]:
        params["apikey"]=config["api_key"]
    # Fixed hosts, no proxy/redirect to unexpected locations. No user-supplied URL.
    req=Request("https://"+host+"/v1/forecast?"+urlencode(params),headers={"User-Agent":"RAKURS forecast/1"})
    with build_opener(NoRedirect(),ProxyHandler({})).open(req,timeout=4) as response:
        raw=response.read(100001)
    if len(raw)>100000:
        raise ValueError("Forecast payload too large")
    return json.loads(raw)

def model_array(payload,array,now):
    h=payload["hourly"]
    if payload.get("hourly_units",{}).get("global_tilted_irradiance")!="W/m²":
        raise ValueError("Unexpected radiation units")
    stamps,rad=h["time"],h["global_tilted_irradiance"]
    if len(stamps)!=len(rad) or len(stamps)>80:
        raise ValueError("Invalid forecast length")
    first=(int(now)//3600+1)*3600
    result=[]
    for stamp,irr in zip(stamps,rad):
        if type(stamp) is not int or stamp%3600:
            raise ValueError("Invalid forecast timestamp")
        # stamp is end of preceding averaged hour; only whole future hours.
        if not first<=stamp-3600<first+48*3600:
            continue
        if irr is None:
            continue
        irr=bounded(irr,0,2000)
        kw=min(array["ac_limit_kw"],array["kwp"]*irr/1000*array["performance_ratio"])
        result.append({"start_utc":datetime.fromtimestamp(stamp-3600,timezone.utc).isoformat().replace("+00:00","Z"),
                       "end_utc":datetime.fromtimestamp(stamp,timezone.utc).isoformat().replace("+00:00","Z"),"kw":round(kw,4)})
    if len({r["start_utc"] for r in result})!=len(result):
        raise ValueError("Duplicate forecast timestamps")
    return sorted(result,key=lambda r:r["start_utc"])

def forecast():
    try:
        config=load_config()
    except (OSError,ValueError,TypeError,KeyError):
        return {"state":"configuration_error","arrays":[]}
    if config is None:
        return {"state":"not_configured","arrays":[]}
    # Do not expose installation coordinates, configuration contents or key.
    identity=json.dumps(config,sort_keys=True)
    now=time.time()
    with _LOCK:
        if _CACHE.get("identity")==identity and now-_CACHE.get("at",0)<TTL:
            return _CACHE["result"]
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                payloads=list(executor.map(lambda a:retrieve(config,a),config["arrays"]))
            arrays=[{"slot":a["slot"],"hours":model_array(p,a,now)} for a,p in zip(config["arrays"],payloads)]
            common=set.intersection(*[{r["start_utc"] for r in a["hours"]} for a in arrays])
            result={"state":"available" if common else "unavailable","arrays":arrays,"common_hours":len(common),
                    "estimated_kwh":round(sum(r["kw"] for a in arrays for r in a["hours"] if r["start_utc"] in common),2) if common else None,
                    "updated_at_utc":datetime.fromtimestamp(now,timezone.utc).isoformat().replace("+00:00","Z"),
                    "provider":"Open-Meteo","method":"GTI × installed DC kWp × configured performance ratio; per-inverter AC cap"}
        except Exception:
            # Never expose remote errors containing URL, API key or coordinates.
            result={"state":"unavailable","arrays":[]}
        _CACHE.update(identity=identity,at=now,result=result)
        return result
