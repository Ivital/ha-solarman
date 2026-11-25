"""Admin-only settings API; separate from raw telemetry and electrical control.

Editing settings changes only config tables and metadata. It NEVER opens
a gateway socket and never alters existing readings or Modbus registers.
Physical polling is gated separately by verified transport and a live worker.
"""
from __future__ import annotations

import hmac
import ipaddress
import re
from datetime import datetime, timezone
from flask import abort, g, jsonify, request, session
import pymysql

KEY = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
TEXT = re.compile(r"^[^\x00-\x1f\x7f]{1,96}$")
MODEL = "SUN-20K-SG05LP3-EU-SM2"
GROUP = "deye_sun20k_parallel_shared_battery"


def validate_ip(value):
    if value is None or value == "":
        return None
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise ValueError("Некоректна IP-адреса")
    if (address.version != 4 or not address.is_private or address.is_loopback
            or address.is_link_local or address.is_multicast
            or address.is_unspecified):
        raise ValueError("Дозволена лише приватна адреса IPv4 обладнання")
    return str(address)


def valid_device(payload):
    if not isinstance(payload, dict):
        raise ValueError("Очікується об'єкт налаштувань")
    name = payload.get("display_name")
    if not isinstance(name, str) or not TEXT.fullmatch(name.strip()):
        raise ValueError("Назва пристрою має містити 1–96 друкованих символів")
    transport = payload.get("transport", "unconfigured")
    if transport not in ("unconfigured", "modbus_tcp", "rtu_tcp"):
        raise ValueError("Непідтримуваний транспорт")
    ip = validate_ip(payload.get("gateway_ip"))
    def numeric(key, lower, upper, default=None):
        value = payload.get(key, default)
        if value is None:
            return None
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError("Некоректний параметр " + key)
        return value
    port = numeric("gateway_port", 1, 65535)
    slave = numeric("slave_id", 1, 247)
    fc = numeric("function_code", 3, 4, 3)
    if fc not in (3,4):
        raise ValueError("Лише функції читання FC03/FC04")
    addr = numeric("register_address", 0, 65535, 0)
    count = numeric("register_count", 1, 8, 1)
    if addr + count > 65536:
        raise ValueError("Адреси регістрів поза дозволеним діапазоном")
    timeout = numeric("timeout_ms", 200, 5000, 1500)
    enabled = payload.get("enabled", False)
    if type(enabled) is not bool:
        raise ValueError("enabled має бути логічним значенням")
    if enabled and (transport == "unconfigured" or not ip or not port or not slave):
        raise ValueError("Увімкнення потребує адреси, порту та Modbus ID")
    model = payload.get("model", MODEL)
    serial = payload.get("serial_number")
    if not isinstance(model, str) or not TEXT.fullmatch(model.strip()):
        raise ValueError("Некоректна модель")
    if serial is not None and (not isinstance(serial,str) or
                               len(serial) > 160 or any(ord(x)<32 for x in serial)):
        raise ValueError("Некоректний серійний номер")
    return {
        "display_name":name.strip(), "model":model.strip(),
        "serial_number": serial or None, "transport":transport,
        "gateway_ip":ip, "gateway_port":port, "slave_id":slave,
        "function_code":fc, "register_address":addr, "register_count":count,
        "timeout_ms":timeout, "enabled":enabled
    }



def shared_gateway_groups(devices):
    """Read-only topology diagnostics; database settings are not physical proof."""
    physical = [d for d in devices if d.get("member_kind") == "physical"
                and d.get("slot_no") in (1, 2)]
    groups = {}
    for d in physical:
        key = (d.get("gateway_ip") or "", d.get("gateway_port") or 0)
        groups.setdefault(key, []).append(d)
    result = []
    for (ip, port), members in sorted(groups.items()):
        ids = [d.get("slave_id") for d in members if d.get("slave_id") is not None]
        transports = sorted({d.get("transport") for d in members
                             if d.get("transport") not in (None, "unconfigured")})
        issues = []
        if not ip or not port:
            issues.append("Адресу або порт шлюзу ще не налаштовано")
        if len(ids) != len(members):
            issues.append("Не всі Modbus Unit ID задані")
        if len(set(ids)) != len(ids):
            issues.append("Однакові Modbus Unit ID на спільній шині")
        if len(transports) > 1:
            issues.append("Різні режими Modbus TCP та RTU-over-TCP на одному порту")
        if len(members) != 2:
            issues.append("Не обидва фізичні інвертори прив'язані до цього шлюзу")
        if not all(d.get("member_verified") and d.get("transport_verified")
                   and d.get("verified_connection") for d in members):
            issues.append("Фізичне підключення не підтверджено")
        result.append({
            "gateway_ip": ip or None, "gateway_port": port or None,
            "transports": transports, "slave_ids": ids,
            "slots": sorted(d.get("slot_no") for d in members),
            "member_count": len(members), "issues": issues,
            "ready": not issues
        })
    return result


def ensure_unique_gateway_units(cur, config, exclude_device_id=None):
    """Reject conflicting addresses or transport modes on one shared port.

    The caller holds a transaction and the parallel-group row lock.
    """
    if not (config["gateway_ip"] and config["gateway_port"] and
            config["transport"] != "unconfigured"):
        return
    cur.execute(
        "SELECT DISTINCT c.device_id,c.slave_id,c.transport "
        "FROM rakurs_device_settings AS c "
        "JOIN rakurs_parallel_members AS pm ON pm.device_id=c.device_id "
        "WHERE pm.member_kind='physical' AND c.gateway_ip=%s "
        "AND c.gateway_port=%s AND c.device_id<>%s",
        (config["gateway_ip"], config["gateway_port"], exclude_device_id or 0))
    for other in cur.fetchall():
        if other["transport"] != config["transport"]:
            raise ValueError("На одному TCP-порту шлюзу не можна змішувати "
                             "Modbus TCP і RTU-over-TCP")
        if other["slave_id"] == config["slave_id"]:
            raise ValueError("Modbus Unit ID вже належить іншому Deye "
                             "на цьому шлюзі. Використайте різні адреси.")


def register(app, *, rows, one, conn):
    def administrator():
        if g.rakurs_user.get("role") != "admin":
            abort(403, description="Тільки адміністратор RAKURS")
        # A JSON header cannot be supplied by a cross-origin form.
        csrf = session.get("csrf", "")
        provided = request.headers.get("X-CSRF-Token", "")
        if not csrf or not provided or not hmac.compare_digest(csrf, provided):
            abort(403, description="Недійсний CSRF токен")
        if not request.is_json:
            abort(415, description="Потрібен JSON")
        if request.content_length is not None and request.content_length > 4096:
            abort(413)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="Некоректний JSON")
        return data

    @app.get("/api/v1/config")
    def config_read():
        try:
            settings = one(
                "SELECT enabled,interval_seconds,updated_at_utc "
                "FROM rakurs_poll_settings WHERE id=1")
            runtime = one(
                "SELECT worker_heartbeat_utc,last_attempt_utc,last_success_utc,"
                "last_device_id,state,last_error FROM rakurs_poll_runtime WHERE id=1")
            devices = rows(
                "SELECT d.id,d.device_key,d.kind,d.model,d.serial_number,"
                "c.display_name,c.enabled,c.transport,c.gateway_ip,c.gateway_port,"
                "c.slave_id,c.function_code,c.register_address,c.register_count,"
                "c.timeout_ms,c.verified_connection,c.updated_at_utc,"
                "pm.slot_no,pm.member_kind,pm.parallel_role,pm.member_verified,"
                "pm.transport_verified,pm.modbus_address FROM rakurs_devices AS d "
                "LEFT JOIN rakurs_device_settings AS c ON c.device_id=d.id "
                "LEFT JOIN rakurs_parallel_members AS pm ON pm.device_id=d.id "
                "ORDER BY d.id LIMIT 100")
        except pymysql.ProgrammingError as error:
            if error.args and error.args[0] == 1146:
                return jsonify({"available":False, "enabled":False,
                                "devices":[], "reason":"Схему налаштувань не встановлено"})
            raise
        def iso(value):
            if isinstance(value, datetime):
                return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00","Z")
            return value
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        beat = (runtime or {}).get("worker_heartbeat_utc")
        alive = bool(isinstance(beat,datetime) and
                     -30 < (now-beat).total_seconds() < 90)
        setting = settings or {"enabled":False,"interval_seconds":60}
        last_ok=(runtime or {}).get("last_success_utc")
        success_recent=bool(
            isinstance(last_ok,datetime) and
            -30 < (now-last_ok).total_seconds() <=
            max(180,2*int(setting["interval_seconds"])+10))
        return jsonify({
            "available":True, "enabled":bool(setting["enabled"]),
            "interval_seconds":setting["interval_seconds"],
            "worker_online":alive,
            "scheduler_active":bool(setting["enabled"]) and alive and
                               (runtime or {}).get("state")=="active",
            "actively_polling":bool(setting["enabled"]) and alive and
                               success_recent,
            "last_success_recent":success_recent,
            "runtime":{k:iso(v) for k,v in (runtime or {}).items()},
            "devices":[{k:iso(v) for k,v in d.items()} for d in devices],
            "shared_rs485_buses":shared_gateway_groups(devices),
        })

    @app.post("/api/v1/config/polling")
    def config_polling():
        data=administrator()
        enabled=data.get("enabled")
        interval=data.get("interval_seconds")
        if type(enabled) is not bool or type(interval) is not int or not 15<=interval<=3600:
            return jsonify({"error":"Інтервал 15–3600 с; enabled — true/false"}),400
        db=conn()
        try:
            db.begin()
            with db.cursor() as cur:
                cur.execute(
                    "SELECT enabled FROM rakurs_poll_settings WHERE id=1 FOR UPDATE")
                if not cur.fetchone():
                    raise ValueError("Схема опитування не встановлена")
                if enabled:
                    cur.execute(
                        "SELECT COUNT(*) AS n FROM rakurs_device_settings AS c "
                        "JOIN rakurs_devices AS d ON d.id=c.device_id "
                        "JOIN rakurs_parallel_members AS pm ON pm.device_id=d.id "
                        "WHERE c.enabled=1 AND c.verified_connection=1 "
                        "AND d.kind='inverter' AND pm.member_kind='physical' "
                        "AND pm.member_verified=1 AND pm.transport_verified=1 "
                        "AND pm.modbus_address=c.slave_id "
                        "AND c.transport<>'unconfigured' AND c.gateway_ip IS NOT NULL "
                        "AND c.slave_id IS NOT NULL")
                    if cur.fetchone()["n"]<1:
                        raise ValueError("Немає підтвердженого інвертора для опитування")
                    cur.execute(
                        "SELECT worker_heartbeat_utc FROM rakurs_poll_runtime WHERE id=1")
                    heartbeat=cur.fetchone()
                    observed=heartbeat and heartbeat["worker_heartbeat_utc"]
                    if not isinstance(observed,datetime) or not (
                        -30 < (datetime.now(timezone.utc).replace(tzinfo=None)-
                                observed).total_seconds() < 90):
                        raise ValueError("Агент опитування не запущений")
                cur.execute(
                    "UPDATE rakurs_poll_settings SET enabled=%s,"
                    "interval_seconds=%s,updated_by=%s,"
                    "updated_at_utc=UTC_TIMESTAMP(6) WHERE id=1",
                    (int(enabled),interval,g.rakurs_user["id"]))
            db.commit()
        except ValueError as error:
            db.rollback()
            return jsonify({"error":str(error)}),409
        except Exception:
            db.rollback()
            raise
        return jsonify({"saved":True,"enabled":enabled,
                        "interval_seconds":interval})

    @app.post("/api/v1/config/inverters")
    def config_create_device():
        payload=administrator()
        try:
            config=valid_device(payload)
        except ValueError as error:
            return jsonify({"error":str(error)}),400
        key=payload.get("device_key")
        slot=payload.get("slot_no")
        if not isinstance(key,str) or not KEY.fullmatch(key):
            return jsonify({"error":"Некоректний ключ нового пристрою"}),400
        if slot is not None and (type(slot) is not int or slot not in (1,2) or
                                 config["model"]!=MODEL):
            return jsonify({"error":"Паралельна пара підтримує лише два слоти Deye"}),400
        db=conn()
        try:
            db.begin()
            with db.cursor() as cur:
                if slot is not None:
                    cur.execute(
                        "SELECT pg.id FROM rakurs_parallel_groups AS pg "
                        "WHERE pg.group_key=%s FOR UPDATE",(GROUP,))
                    found=cur.fetchone()
                    if not found:
                        raise ValueError("Паралельна група не налаштована")
                    group_id=found["id"]
                    cur.execute(
                        "SELECT device_id FROM rakurs_parallel_members "
                        "WHERE group_id=%s AND slot_no=%s "
                        "AND member_kind='physical' FOR UPDATE",
                        (group_id,slot))
                    if cur.fetchone():
                        raise ValueError("Цей слот Deye вже зайнятий")
                    ensure_unique_gateway_units(cur, config)
                cur.execute(
                    "INSERT INTO rakurs_devices "
                    "(device_key,kind,model,serial_number,created_at_utc) "
                    "VALUES (%s,'inverter',%s,%s,UTC_TIMESTAMP(6))",
                    (key,config["model"],config["serial_number"]))
                device_id=cur.lastrowid
                if slot is not None:
                    cur.execute(
                        "INSERT INTO rakurs_parallel_members "
                        "(group_id,device_id,slot_no,member_kind,parallel_role,"
                        "transport_verified,member_verified) "
                        "VALUES (%s,%s,%s,'physical','unverified',0,0)",
                        (group_id,device_id,slot))
                cur.execute(
                    "INSERT INTO rakurs_device_settings "
                    "(device_id,display_name,enabled,transport,gateway_ip,"
                    "gateway_port,slave_id,function_code,register_address,"
                    "register_count,timeout_ms,updated_at_utc) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP(6))",
                    (device_id,config["display_name"],int(config["enabled"]),
                     config["transport"],config["gateway_ip"],
                     config["gateway_port"],config["slave_id"],
                     config["function_code"],config["register_address"],
                     config["register_count"],config["timeout_ms"]))
            db.commit()
        except pymysql.IntegrityError:
            db.rollback()
            return jsonify({"error":"Пристрій із таким ключем або слотом вже існує"}),409
        except ValueError as error:
            db.rollback()
            return jsonify({"error":str(error)}),409
        except Exception:
            db.rollback()
            raise
        return jsonify({"created":True,"device_id":device_id}),201

    @app.post("/api/v1/config/inverters/<int:device_id>")
    def config_update_device(device_id):
        payload=administrator()
        try:
            config=valid_device(payload)
        except ValueError as error:
            return jsonify({"error":str(error)}),400
        db=conn()
        try:
            db.begin()
            with db.cursor() as cur:
                cur.execute(
                    "SELECT id FROM rakurs_devices WHERE id=%s FOR UPDATE",
                    (device_id,))
                if not cur.fetchone():
                    db.rollback()
                    return jsonify({"error":"Пристрій не знайдено"}),404
                cur.execute(
                    "SELECT 1 AS linked FROM rakurs_parallel_members AS pm "
                    "JOIN rakurs_parallel_groups AS pg ON pg.id=pm.group_id "
                    "WHERE pm.device_id=%s AND pg.group_key=%s LIMIT 1",
                    (device_id,GROUP))
                linked = cur.fetchone()
                if linked and config["model"] != MODEL:
                    raise ValueError("Модель учасника паралельної системи має відповідати профілю Deye")
                if linked:
                    cur.execute("SELECT id FROM rakurs_parallel_groups "
                                "WHERE group_key=%s FOR UPDATE",(GROUP,))
                    ensure_unique_gateway_units(cur, config, device_id)
                cur.execute(
                    "SELECT transport,gateway_ip,gateway_port,slave_id,"
                    "function_code,register_address,register_count "
                    "FROM rakurs_device_settings WHERE device_id=%s FOR UPDATE",
                    (device_id,))
                before=cur.fetchone()
                changed=(not before or any(before[k]!=config[k] for k in (
                    "transport","gateway_ip","gateway_port","slave_id",
                    "function_code","register_address","register_count")))
                cur.execute(
                    "UPDATE rakurs_devices SET model=%s,serial_number=%s WHERE id=%s",
                    (config["model"],config["serial_number"],device_id))
                cur.execute(
                    "INSERT INTO rakurs_device_settings "
                    "(device_id,display_name,enabled,transport,gateway_ip,"
                    "gateway_port,slave_id,function_code,register_address,"
                    "register_count,timeout_ms,verified_connection,updated_at_utc) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,UTC_TIMESTAMP(6)) "
                    "ON DUPLICATE KEY UPDATE display_name=VALUES(display_name),"
                    "enabled=VALUES(enabled),transport=VALUES(transport),"
                    "gateway_ip=VALUES(gateway_ip),gateway_port=VALUES(gateway_port),"
                    "slave_id=VALUES(slave_id),function_code=VALUES(function_code),"
                    "register_address=VALUES(register_address),"
                    "register_count=VALUES(register_count),timeout_ms=VALUES(timeout_ms),"
                    "verified_connection=IF(%s,0,verified_connection),"
                    "updated_at_utc=UTC_TIMESTAMP(6)",
                    (device_id,config["display_name"],int(config["enabled"]),
                     config["transport"],config["gateway_ip"],config["gateway_port"],
                     config["slave_id"],config["function_code"],
                     config["register_address"],config["register_count"],
                     config["timeout_ms"],int(changed)))
                if changed:
                    # Prevent polling a changed physical endpoint until it has
                    # been commissioned again by local operator controls.
                    cur.execute("UPDATE rakurs_poll_settings SET enabled=0,"
                                "updated_at_utc=UTC_TIMESTAMP(6) WHERE id=1")
            db.commit()
        except ValueError as error:
            db.rollback()
            return jsonify({"error":str(error)}),409
        except Exception:
            db.rollback()
            raise
        return jsonify({"saved":True,"device_id":device_id,
                        "verification_reset":changed})
