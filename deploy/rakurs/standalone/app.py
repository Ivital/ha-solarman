"""RAKURS standalone read-only API backed EXCLUSIVELY by LAN MySQL rakurs.

No Home Assistant, Recorder, SQLite or Modbus polling. Auth writes only to MySQL users/attempts.
Localhost-only backend. User authentication is mandatory before any data read.
"""
from __future__ import annotations

import json
import logging
import os
import re
import stat
from datetime import datetime, timezone, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo
from decimal import Decimal
from pathlib import Path

import pymysql
from flask import Flask, abort, g, jsonify, render_template, request, redirect, url_for
from .parallel_support import MODEL as PARALLEL_MODEL, SIGNAL_KEYS as PARALLEL_SIGNAL_KEYS, describe_topology
from .mysql_transport import mysql_tls_options, mysql_transport_state

ROOT = Path(__file__).resolve().parents[1]
CATALOG_FILE = ROOT / "signal_catalog.json"
HOST = "192.168.5.30"
PORT = 3306
SCHEMA = "rakurs"
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
MAX_KEYS = 24
MAX_HISTORY = 1000
LOGGER = logging.getLogger("rakurs.standalone")

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config.update(JSON_SORT_KEYS=False, DEBUG=False)


class ConfigurationError(Exception):
    """Missing or unsafe application database credentials."""


def _password() -> str:
    secret_path = os.environ.get("RAKURS_DB_PASSWORD_FILE")
    if not secret_path:
        raise ConfigurationError("Password file path not configured")
    path = Path(secret_path).expanduser()
    try:
        meta = path.stat()
    except OSError as exc:
        raise ConfigurationError("Password file unavailable") from exc
    if not stat.S_ISREG(meta.st_mode) or meta.st_mode & 0o077:
        raise ConfigurationError("Password file must be regular with mode 0600")
    if meta.st_uid != os.geteuid():
        raise ConfigurationError("Password file owner must equal service UID")
    password = path.read_text(encoding="utf-8").rstrip("\r\n")
    if not password:
        raise ConfigurationError("Empty password file")
    return password


def open_db():
    user = os.environ.get("RAKURS_DB_USER", "").strip()
    if not user or not re.fullmatch(r"[A-Za-z0-9_]{1,32}", user):
        raise ConfigurationError("Dedicated read-only database user required")
    try:
        transport_options = mysql_tls_options()
    except RuntimeError as exc:
        raise ConfigurationError('MySQL trust anchor invalid') from exc
    return pymysql.connect(
        host=HOST, port=PORT, database=SCHEMA, user=user, password=_password(),
        cursorclass=pymysql.cursors.DictCursor, charset="utf8mb4",
        autocommit=True, connect_timeout=4, read_timeout=6, write_timeout=4,
        **transport_options,
    )


def conn():
    if "mysql" not in g:
        g.mysql = open_db()
    return g.mysql


@app.teardown_appcontext
def close_connection(_error):
    db = g.pop("mysql", None)
    if db is not None:
        db.close()


def rows(sql: str, params=()):
    with conn().cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def one(sql: str, params=()):
    with conn().cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def serialize(value):
    if isinstance(value, dict):
        return {k: serialize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [serialize(v) for v in value]
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return str(value)
    return value


@app.before_request
def localhost_only():
    if request.remote_addr not in {"127.0.0.1", "::1"}:
        abort(403)


@app.after_request
def secure_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; "
         "font-src 'self'; worker-src 'self'; manifest-src 'self'; base-uri 'none'; form-action 'self'; "
        "frame-ancestors 'none'"
    )
    if request.path == "/static/rakurs-sw.js":
        response.headers["Service-Worker-Allowed"] = "/"
    return response


@app.errorhandler(pymysql.MySQLError)
@app.errorhandler(ConfigurationError)
@app.errorhandler(OSError)
def database_error(error):
    LOGGER.warning("MySQL unavailable: %s", type(error).__name__)
    return jsonify({"data_source": "mysql", "error": "База Rakurs недоступна"}), 503


# Primary Rakurs page contains server-rendered MySQL readings as well as
# the existing JavaScript dashboard. It is never a separate demo page.
HOME_METRICS = (
    ("pv_power", "Генерація PV", "☀"),
    ("load_power", "Навантаження", "⌂"),
    ("grid_power", "Потужність мережі", "⌁"),
    ("battery_soc", "Заряд АКБ", "▣"),
    ("battery_power", "Потужність АКБ", "↯"),
    ("inverter_temperature", "Температура інвертора", "♨"),
    ("pv_energy_today", "Виробіток за добу", "◉"),
    ("load_energy_today", "Споживання за добу", "▥"),
)


def parallel_aggregate_requires_device_selection() -> bool:
    """Do not produce a false combined total from independent device samples."""
    if not hasattr(g, "parallel_aggregate_blocked"):
        found = one(
            "SELECT COUNT(DISTINCT pm.device_id) AS n "
            "FROM rakurs_parallel_members AS pm "
            "JOIN rakurs_parallel_groups AS pg ON pg.id=pm.group_id "
            "WHERE pg.group_key=%s",
            ("deye_sun20k_parallel_shared_battery",),
        )
        g.parallel_aggregate_blocked = bool(found and found["n"] >= 2)
    return g.parallel_aggregate_blocked


def primary_device_id():
    """Default single-device overview is slot 1 of the SAME two-Deye group.

    A legacy standalone device, even with an earlier numeric id, never
    becomes the source for home KPIs, history, charts or Modbus. Never
    add the readings of two Deye units without verified topology.
    """
    if not hasattr(g, "primary_device"):
        found = one(
            "SELECT d.id AS id FROM rakurs_devices AS d "
            "JOIN rakurs_parallel_members AS pm ON pm.device_id=d.id "
            "JOIN rakurs_parallel_groups AS pg ON pg.id=pm.group_id "
            "WHERE pg.group_key=%s AND pm.slot_no IN (1,2) "
            "AND d.model LIKE %s "
            "ORDER BY pm.slot_no, "
            "CASE WHEN pm.member_kind='physical' AND pm.member_verified=1 "
            "THEN 0 WHEN pm.member_kind='physical' THEN 1 ELSE 2 END, "
            "CASE WHEN d.kind='inverter' THEN 0 ELSE 1 END, d.id LIMIT 1",
            ("deye_sun20k_parallel_shared_battery", "%" + PARALLEL_MODEL + "%"),
        )
        g.primary_device = found["id"] if found else None
    return g.primary_device

def selected_overview_device_id():
    """Select one of the two registered Deye slots for legacy metrics APIs.

    A missing parameter means slot 1. No other database device may be
    selected, so an old standalone Deye is never mixed into these views.
    """
    key = request.args.get("device", "").strip()
    if not key:
        return primary_device_id()
    if not KEY_RE.fullmatch(key):
        abort(400, description="Некоректний ключ інвертора")
    candidate = next(
        (item for item in parallel_registered_devices()
         if item["device_key"] == key),
        None,
    )
    if candidate is None:
        abort(404, description="Інвертор відсутній у парі Deye")
    return candidate["id"]


@app.get("/")
def index():
    if "ux" in request.args:
        from urllib.parse import urlencode
        args = [(key, value) for key, value in request.args.items(multi=True) if key != "ux" and not key.startswith("utm_")]
        return redirect(url_for("index") + ("?" + urlencode(args) if args else ""), code=302)
    selected = selected_overview_device_id()
    readings = []
    if selected is not None:
        keys = tuple(key for key, _, _ in HOME_METRICS)
        placeholders = ",".join(["%s"] * len(keys))
        readings = rows(
            "SELECT s.signal_key,s.unit,m.value_decimal,m.quality "
            "FROM rakurs_signals AS s "
            "JOIN (SELECT signal_id,MAX(observed_at_utc) AS latest_at "
            "FROM rakurs_samples WHERE device_id=%s "
            "GROUP BY signal_id) AS recent ON recent.signal_id=s.id "
            "JOIN rakurs_samples AS m ON m.device_id=%s "
            "AND m.signal_id=recent.signal_id "
            "AND m.observed_at_utc=recent.latest_at "
            "WHERE s.signal_key IN (" + placeholders + ") "
            "ORDER BY s.signal_key LIMIT 32",
            (selected, selected) + keys,
        )
    latest = {item["signal_key"]: item for item in readings}
    cards = []
    for key, label, icon in HOME_METRICS:
        item = latest.get(key)
        cards.append({
            "label": label, "icon": icon,
            "value": _display_decimal(item["value_decimal"])
            if item and item["value_decimal"] is not None else "—",
            "unit": (item["unit"] or "") if item else "",
            "quality": item["quality"] if item else None,
        })
    return render_template("index.html", initial_cards=cards)


# Standalone HTML snapshot sourced from the same MySQL device as the overview.
DEYE_SUMMARY = (
    ("pv_power", "Сонячна генерація"),
    ("load_power", "Навантаження"),
    ("grid_power", "Мережа"),
    ("battery_soc", "Заряд АКБ"),
    ("battery_power", "Потужність АКБ"),
    ("inverter_temperature", "Температура інвертора"),
    ("pv_energy_today", "Генерація за добу"),
    ("load_energy_today", "Споживання за добу"),
)


def _display_decimal(value):
    if value is None:
        return "—"
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return str(value)

@app.get("/deye")
def deye_snapshot():
    device_id = selected_overview_device_id()
    result = []
    if device_id is not None:
        result = rows(
            "SELECT s.signal_key,s.label_uk,s.unit,m.value_decimal,"
            "m.quality,m.observed_at_utc FROM rakurs_signals AS s "
            "JOIN (SELECT signal_id,MAX(observed_at_utc) AS latest_at "
            "FROM rakurs_samples WHERE device_id=%s GROUP BY signal_id) AS recent "
            "ON recent.signal_id=s.id "
            "JOIN rakurs_samples AS m ON m.device_id=%s "
            "AND m.signal_id=recent.signal_id AND m.observed_at_utc=recent.latest_at "
            "ORDER BY s.label_uk LIMIT 500",
            (device_id, device_id),
        )
    lookup = {item["signal_key"]: item for item in result}
    summary = [{
        "label": label,
        "value": _display_decimal(lookup[key]["value_decimal"]) if key in lookup else "—",
        "unit": (lookup[key]["unit"] or "") if key in lookup else "",
    } for key, label in DEYE_SUMMARY]
    for item in result:
        item["display_value"] = _display_decimal(item["value_decimal"])
    return render_template(
        "deye_server.html", section="overview", title="Огляд Deye",
        summary=summary, fields=result, count=len(result),
        timestamp=result[0]["observed_at_utc"] if result else None,
    )


@app.get("/deye/modbus")
def deye_modbus_snapshot():
    device_id = selected_overview_device_id()
    params = (device_id,) if device_id is not None else (-1,)
    result = rows(
        "SELECT f.field_key,f.group_name,f.field_name,f.unit,"
        "f.registers_json,m.value_decimal,m.value_text,m.quality "
        "FROM rakurs_modbus_fields AS f "
        "LEFT JOIN (SELECT field_id,MAX(observed_at_utc) AS latest_at "
        "FROM rakurs_modbus_field_samples WHERE device_id=%s GROUP BY field_id) "
        "AS recent ON recent.field_id=f.id "
        "LEFT JOIN rakurs_modbus_field_samples AS m ON m.device_id=%s "
        "AND m.field_id=recent.field_id AND m.observed_at_utc=recent.latest_at "
        "ORDER BY f.id LIMIT 700", (params[0], params[0]),
    )
    for item in result:
        addresses = json.loads(item["registers_json"] or "[]")
        item["register_display"] = ", ".join(
            f"0x{int(address):04X}" for address in addresses
        ) or "Розрахунковий"
        item["display_value"] = (
            str(item["value_text"]) if item["value_text"] is not None
            else _display_decimal(item["value_decimal"])
        )
    return render_template(
        "deye_server.html", section="modbus", title="Усі параметри Modbus",
        fields=result, count=len(result), summary=[], timestamp=None,
    )

@lru_cache(maxsize=1)
def catalog_data():
    with CATALOG_FILE.open("r", encoding="utf-8") as source:
        return json.load(source)


@app.get("/api/v1/catalog")
def catalog():
    return jsonify(catalog_data())


def collector_health():
    """Distinguish a running probe from decoded, verified system telemetry."""
    try:
        runtime = one(
            "SELECT p.enabled,p.interval_seconds,r.worker_heartbeat_utc,r.last_attempt_utc,"
            "r.last_success_utc,r.last_device_id,r.state,r.last_error "
            "FROM rakurs_poll_settings p LEFT JOIN rakurs_poll_runtime r ON r.id=p.id WHERE p.id=1")
    except pymysql.ProgrammingError as error:
        if not error.args or error.args[0] != 1146:
            raise
        runtime = None
    runtime = runtime or {}
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    def age(key):
        stamp = runtime.get(key)
        if not isinstance(stamp, datetime):
            return None
        if stamp.tzinfo is not None:
            stamp = stamp.astimezone(timezone.utc).replace(tzinfo=None)
        return (now-stamp).total_seconds()
    heartbeat, success = age("worker_heartbeat_utc"), age("last_success_utc")
    online = heartbeat is not None and -30 <= heartbeat < 90
    enabled = bool(runtime.get("enabled"))
    interval = int(runtime.get("interval_seconds") or 60)
    active = (enabled and online and runtime.get("state") == "active" and
              success is not None and -30 <= success <= max(180,2*interval+10))
    return {"enabled":enabled,"worker_online":online,"collector_active":active,
            "interval_seconds":interval,"state":runtime.get("state") or "not_configured",
            "last_attempt_utc":runtime.get("last_attempt_utc"),
            "last_success_utc":runtime.get("last_success_utc"),
            "last_device_id":runtime.get("last_device_id"),
            "worker_heartbeat_utc":runtime.get("worker_heartbeat_utc"),
            "last_error":runtime.get("last_error"),
            "telemetry_decoder_ready":False}



@app.get("/api/v1/status")
def status():
    devices = one("SELECT COUNT(*) AS n FROM rakurs_devices")
    signals = one(
        "SELECT COUNT(DISTINCT signal_id) AS n FROM rakurs_samples"
    )
    sample = one("SELECT 1 AS n FROM rakurs_samples LIMIT 1")
    total_signals = signals["n"] if signals else 0
    collector = collector_health()
    return jsonify({
        "data_source": "mysql", "mode": "real", "database": SCHEMA,
        "mysql_host": HOST,
        "registered_devices": devices["n"] if devices else 0,
        "verified_signals": total_signals,
        "displayed_signals": total_signals,
        "has_real_samples": bool(sample), "collector_active": collector["collector_active"],
        "collector": serialize(collector), "physical_topology_verified": False,
        "register_semantics_verified": False,
        "home_assistant_dependency": False,
        "mysql_transport": mysql_transport_state(),
    })


@app.get("/api/v1/signals")
def signals():
    result = rows(
        "SELECT DISTINCT s.signal_key,s.label_uk,s.unit "
        "FROM rakurs_signals AS s "
        "JOIN rakurs_samples AS m ON m.signal_id=s.id "
        "ORDER BY s.label_uk LIMIT 500"
    )
    return jsonify({"data_source": "mysql", "signals": serialize(result)})


@app.get("/api/v1/parallel/comparison")
def parallel_comparison():
    devices = parallel_registered_devices()
    empty = {"frames": [], "peak": None, "start": None, "end": None}
    if not devices:
        return jsonify(empty)
    ids = [d["id"] for d in devices]
    keys = ("inverter_temperature", "output_power", "pv_power")
    filters = (" FROM rakurs_samples m JOIN rakurs_signals s ON s.id=m.signal_id "
               "WHERE m.device_id IN (" + ",".join(["%s"]*len(ids)) + ") "
               "AND s.signal_key IN (%s,%s,%s) AND m.value_decimal IS NOT NULL "
               "AND ((s.signal_key='inverter_temperature' AND s.unit IN ('°C','°С')) "
               "OR (s.signal_key<>'inverter_temperature' AND s.unit IN ('кВт','kW'))) ")
    args = tuple(ids) + keys
    end = one("SELECT MAX(m.observed_at_utc) AS t" + filters, args)["t"]
    if end is None:
        return jsonify(empty)
    start = end - timedelta(hours=24)
    samples = rows("SELECT m.observed_at_utc AS t,m.device_id,s.signal_key,m.value_decimal AS v" +
                   filters + "AND m.observed_at_utc BETWEEN %s AND %s ORDER BY m.observed_at_utc",
                   args + (start, end))
    slots = {d["id"]: d["slot_no"]-1 for d in devices}
    aligned = {}
    for row in samples:
        values = aligned.setdefault(row["t"], [None]*6)
        values[keys.index(row["signal_key"])*2+slots[row["device_id"]]] = row["v"]
    # Retain actual frames including per-channel extrema, transitions to missing
    # and the largest simultaneous temperature difference before thinning.
    selected, buckets = set(), {}
    peak, largest, previous = None, None, None
    for stamp, values in aligned.items():
        if None not in values[:2]:
            delta = abs(values[0]-values[1])
            if largest is None or delta > largest:
                peak, largest = stamp, delta
        if previous is not None and any((a is None)!=(b is None) for a,b in zip(aligned[previous],values)):
            selected.update((previous,stamp))
        buckets.setdefault(stamp.replace(second=0,microsecond=0), []).append(stamp)
        previous = stamp
    for group in buckets.values():
        selected.update((group[0],group[-1]))
        for i in range(6):
            valid = [s for s in group if aligned[s][i] is not None]
            if valid:
                selected.update((min(valid,key=lambda s:aligned[s][i]),max(valid,key=lambda s:aligned[s][i])))
    if peak is not None:
        selected.add(peak)
    return jsonify(serialize({"start":start,"end":end,"peak":peak,
                              "frames":[{"t":s,"v":aligned[s]} for s in sorted(selected)]}))


LOAD_KEYS = ("load_power", "load_l1_power", "load_l2_power", "load_l3_power")


@app.get("/api/v1/load/profile")
def load_profile():
    try:
        day = datetime.strptime(request.args.get("day", ""), "%Y-%m-%d")
    except ValueError:
        abort(400, description="Оберіть добу")
    zone = ZoneInfo("Europe/Kyiv")

    def utc_start(local_day):
        return local_day.replace(tzinfo=zone).astimezone(timezone.utc).replace(tzinfo=None)

    start, end = utc_start(day), utc_start(day + timedelta(days=1))
    days = [(day - timedelta(days=n)).date().isoformat() for n in range(6, -1, -1)]
    result = {"start": start, "end": end, "days": days, "hours": [], "band": [], "frames": []}
    devices = parallel_registered_devices()
    if not devices:
        return jsonify(serialize(result))
    device = devices[0]["id"]
    source = (" FROM rakurs_samples m JOIN rakurs_signals s ON s.id=m.signal_id "
              "WHERE m.device_id=%s AND m.observed_at_utc >= %s AND m.observed_at_utc < %s "
              "AND s.unit IN ('кВт','kW') AND m.value_decimal IS NOT NULL ")
    hourly = rows("SELECT DATE(m.observed_at_utc) AS d,HOUR(m.observed_at_utc) AS h,"
                  "MAX(m.value_decimal) AS peak,AVG(m.value_decimal) AS avg_kw" + source +
                  "AND s.signal_key='load_power' GROUP BY DATE(m.observed_at_utc),HOUR(m.observed_at_utc)",
                  (device, utc_start(day - timedelta(days=28)), end))
    maxima, references = {}, {}
    for row in hourly:
        stamp = datetime.combine(row["d"], datetime.min.time()).replace(hour=row["h"], tzinfo=timezone.utc)
        local = stamp.astimezone(zone)
        key = local.date().isoformat(), local.hour
        if key[0] in days:
            maxima[key] = max(maxima.get(key, row["peak"]), row["peak"])
        if local.date() < day.date() and (local.weekday() < 5) == (day.weekday() < 5):
            references.setdefault(local.hour, {}).setdefault(local.date(), []).append(float(row["avg_kw"]))
    result["hours"] = [dict(day=d, hour=h, peak=v) for (d, h), v in sorted(maxima.items())]
    hour = start
    while hour < end:
        h = hour.replace(tzinfo=timezone.utc).astimezone(zone).hour
        values = sorted(sum(v)/len(v) for v in references.get(h, {}).values())
        if len(values) >= 3:
            from statistics import quantiles
            q1, _, q3 = quantiles(values, method="inclusive")
            result["band"].append(dict(start=hour, end=hour+timedelta(hours=1), low=q1, high=q3))
        hour += timedelta(hours=1)
    columns = ",".join("MAX(CASE WHEN s.signal_key=%s THEN m.value_decimal END) AS v"+str(i)
                       for i in range(len(LOAD_KEYS)))
    frames = rows("SELECT m.observed_at_utc AS t," + columns + source +
                  "AND s.signal_key IN (%s,%s,%s,%s) GROUP BY m.observed_at_utc "
                  "HAVING v0 IS NOT NULL ORDER BY m.observed_at_utc",
                  LOAD_KEYS + (device, start, end) + LOAD_KEYS)
    # Preserve the actual timestamp and phase readings of every retained extremum.
    buckets = {}
    for row in frames:
        bucket = row["t"].replace(second=0, microsecond=0)
        buckets.setdefault(bucket, []).append(row)
    for group in buckets.values():
        chosen = [group[0], min(group, key=lambda r:r["v0"]), max(group, key=lambda r:r["v0"]), group[-1]]
        for t, row in sorted({r["t"]:r for r in chosen}.items()):
            result["frames"].append(dict(t=t, v=[row["v"+str(i)] for i in range(4)]))
    return jsonify(serialize(result))


GRID_ANALYSIS_SIGNALS = (
    ("grid_l1_voltage", "В", "V"), ("grid_l2_voltage", "В", "V"),
    ("grid_l3_voltage", "В", "V"), ("grid_frequency", "Гц", "Hz"),
    ("load_power", "кВт", "kW"), ("battery_power", "кВт", "kW"),
)


@app.get("/api/v1/grid/day")
def grid_day():
    try:
        day = datetime.strptime(request.args.get("day", ""), "%Y-%m-%d")
    except ValueError:
        abort(400, description="Оберіть добу")
    zone = ZoneInfo("Europe/Kyiv")
    start, end = (d.replace(tzinfo=zone).astimezone(timezone.utc).replace(tzinfo=None)
                  for d in (day, day+timedelta(days=1)))
    devices = parallel_registered_devices()
    result = {"start":start, "end":end, "frames":[], "plot":[], "gaps":[], "peak":None}
    if not devices:
        return jsonify(serialize(result))
    # A row is one exact UTC timestamp. Each value keeps its physical unit.
    columns = ",".join("MAX(CASE WHEN s.signal_key=%s AND s.unit IN (%s,%s) "
                       "THEN m.value_decimal END) AS v"+str(i) for i in range(6))
    keys = tuple(entry[0] for entry in GRID_ANALYSIS_SIGNALS)
    frames = rows("SELECT m.observed_at_utc AS t,"+columns+
                  " FROM rakurs_samples m JOIN rakurs_signals s ON s.id=m.signal_id "
                  "WHERE m.device_id=%s AND m.observed_at_utc >= %s AND m.observed_at_utc < %s "
                  "AND s.signal_key IN (%s,%s,%s,%s,%s,%s) AND m.value_decimal IS NOT NULL "
                  "GROUP BY m.observed_at_utc HAVING v0 IS NOT NULL OR v1 IS NOT NULL OR v2 IS NOT NULL "
                  "ORDER BY m.observed_at_utc",
                  tuple(v for entry in GRID_ANALYSIS_SIGNALS for v in entry)+(devices[0]["id"],start,end)+keys)
    if not frames:
        return jsonify(serialize(result))
    intervals = sorted((b["t"]-a["t"]).total_seconds() for a,b in zip(frames,frames[1:]))
    cadence = intervals[(len(intervals)-1)//2] if intervals else None
    threshold = 2.5*cadence if cadence else None
    breaks = [set() for _ in frames]
    chosen = {0,len(frames)-1}
    gaps = []
    prev = [None]*3
    best = peak = None
    for i,row in enumerate(frames):
        if i and threshold and (row["t"]-frames[i-1]["t"]).total_seconds()>threshold:
            gaps.append([i-1,i])
            chosen.update([i-1,i])
        volts = [row["v"+str(k)] for k in range(3)]
        for k,value in enumerate(volts):
            if value is None:
                continue
            j = prev[k]
            if j is not None and (i!=j+1 or (threshold and (row["t"]-frames[j]["t"]).total_seconds()>threshold)):
                breaks[i].add(k)
                chosen.update([j,i])
            prev[k]=i
        if None not in volts:
            spread=max(volts)-min(volts)
            if best is None or spread>best:
                best,peak=spread,i
    # Keep extrema, gap boundaries and the true daily peak before reducing the plot.
    if len(frames)<=960:
        chosen.update(range(len(frames)))
    else:
        size=(len(frames)+119)//120
        for lo in range(0,len(frames),size):
            hi=min(lo+size,len(frames))
            chosen.update((lo,hi-1))
            for k in range(3):
                key="v"+str(k)
                valid=[i for i in range(lo,hi) if frames[i][key] is not None]
                if valid:
                    chosen.update((min(valid,key=lambda i:frames[i][key]),max(valid,key=lambda i:frames[i][key])))
    if peak is not None:
        chosen.add(peak)
    needed=sorted({j for i in chosen for j in (i-1,i,i+1) if 0<=j<len(frames)})
    mapping={source:i for i,source in enumerate(needed)}
    result.update(frames=[{"t":frames[i]["t"],"v":[frames[i]["v"+str(k)] for k in range(6)],
                           "breaks":sorted(breaks[i])} for i in needed],
                  plot=[mapping[i] for i in sorted(chosen)],
                  gaps=[[mapping[a],mapping[b]] for a,b in gaps],
                  peak=mapping[peak] if peak is not None else None)
    return jsonify(serialize(result))


CELL_FRAME_PATTERN = r"^bms_([1-9]|1[0-9]|20)_((min|max)_cell_voltage|cell_([1-9]|[1-5][0-9]|6[0-4])_voltage)$"


@app.get("/api/v1/bms/history")
def battery_history():
    devices = parallel_registered_devices()
    if not devices:
        return jsonify({"modules": {}, "peak": None})
    device_id = devices[0]["id"]
    # Convert each original unit before comparing synchronous values.
    criteria = (" FROM rakurs_samples m JOIN rakurs_signals s ON s.id=m.signal_id "
                "WHERE m.device_id=%s AND REGEXP_LIKE(s.signal_key,%s,'c') "
                "AND s.unit IN ('В','V','мВ','mV') AND m.value_decimal IS NOT NULL ")
    scale = "m.value_decimal * CASE WHEN s.unit IN ('В','V') THEN 1000 ELSE 1 END"
    if request.args.get("at"):
        stamp = checked_utc("at")
        records = rows("SELECT s.signal_key," + scale + " AS mv" + criteria +
                       "AND m.observed_at_utc=%s", (device_id, CELL_FRAME_PATTERN, stamp))
        modules = {}
        for record in records:
            _, module, key = record["signal_key"].split("_", 2)
            modules.setdefault(module, {})[key] = record["mv"]
        return jsonify(serialize({"at": stamp, "modules": modules}))
    start, end = checked_utc("start"), checked_utc("end")
    if not 0 < (end-start).total_seconds() <= 25*3600:
        abort(400, description="Оберіть одну добу")
    peak = one(
        "WITH samples AS (SELECT m.observed_at_utc AS stamp,"
        "SUBSTRING_INDEX(SUBSTRING_INDEX(s.signal_key,'_',2),'_',-1) AS module,"
        "SUBSTRING(s.signal_key,LOCATE('_',s.signal_key,5)+1) AS field_key,"
        + scale + " AS mv" + criteria + "AND m.observed_at_utc BETWEEN %s AND %s),"
        "frames AS (SELECT stamp,module,"
        "MIN(CASE WHEN field_key='min_cell_voltage' THEN mv END) AS lo,"
        "MAX(CASE WHEN field_key='max_cell_voltage' THEN mv END) AS hi,"
        "COUNT(DISTINCT CASE WHEN LEFT(field_key,5)='cell_' THEN field_key END) AS n,"
        "MIN(CASE WHEN LEFT(field_key,5)='cell_' THEN mv END) AS cell_lo,"
        "MAX(CASE WHEN LEFT(field_key,5)='cell_' THEN mv END) AS cell_hi "
        "FROM samples GROUP BY stamp,module),"
        "spreads AS (SELECT stamp,module,CASE WHEN hi>=lo THEN hi-lo "
        "WHEN n>=2 THEN cell_hi-cell_lo END AS spread FROM frames) "
        "SELECT stamp,module,spread FROM spreads WHERE spread IS NOT NULL "
        "ORDER BY spread DESC,stamp DESC,CAST(module AS UNSIGNED) LIMIT 1",
        (device_id, CELL_FRAME_PATTERN, start, end))
    return jsonify(serialize({"peak": peak}))


@app.get("/api/v1/bms/cells")
def bms_cells():
    """Stored cell-level readings only, independent of the generic signal list limit."""
    device_id=selected_overview_device_id()
    if device_id is None:
        return jsonify({"values":{}})
    pattern=r"^bms_([1-9]|1[0-9]|20)_cell_([1-9]|[1-5][0-9]|6[0-4])_voltage$"
    samples=rows(
        "SELECT s.signal_key,s.unit,m.value_decimal,m.quality,m.observed_at_utc "
        "FROM rakurs_signals s JOIN rakurs_samples m ON m.signal_id=s.id "
        "AND m.device_id=%s AND m.observed_at_utc=(SELECT MAX(mx.observed_at_utc) "
        "FROM rakurs_samples mx WHERE mx.device_id=%s AND mx.signal_id=s.id) "
        "WHERE REGEXP_LIKE(s.signal_key,%s,'c') ORDER BY s.signal_key LIMIT 1280",
        (device_id,device_id,pattern))
    return jsonify({"values":{r["signal_key"]:serialize(r) for r in samples},
                    "primary_device_id":device_id})


@app.get("/api/v1/latest")
def latest():
    value = request.args.get("keys", "")
    keys = list(dict.fromkeys(k.strip() for k in value.split(",") if k.strip()))
    if not keys or len(keys) > MAX_KEYS or any(not KEY_RE.fullmatch(k) for k in keys):
        return jsonify({"error": "Некоректний перелік сигналів"}), 400
    device_id = selected_overview_device_id()
    found = {}
    if device_id is not None and len(keys) > 1:
        placeholders = ",".join("%s" for _ in keys)
        result = rows(
            "SELECT s.signal_key,s.label_uk,s.unit,m.value_decimal,"
            "m.raw_value,m.quality,m.observed_at_utc FROM rakurs_signals s "
            "JOIN rakurs_samples m ON m.signal_id=s.id AND m.device_id=%s "
            "AND m.observed_at_utc=(SELECT MAX(mx.observed_at_utc) FROM rakurs_samples mx "
            "WHERE mx.device_id=%s AND mx.signal_id=s.id) "
            "WHERE s.signal_key IN (" + placeholders + ")",
            (device_id, device_id) + tuple(keys))
        found = {item["signal_key"]: serialize(item) for item in result}
    elif device_id is not None:
        for key in keys:
            result = one(
                "SELECT s.signal_key,s.label_uk,s.unit,m.value_decimal,"
                "m.raw_value,m.quality,m.observed_at_utc "
                "FROM rakurs_samples AS m "
                "JOIN rakurs_signals AS s ON s.id=m.signal_id "
                "WHERE m.device_id=%s AND s.signal_key=%s "
                "ORDER BY m.observed_at_utc DESC LIMIT 1",
                (device_id, key),
            )
            if result is not None:
                found[key] = serialize(result)
    return jsonify({"data_source": "mysql", "values": found,
                    "primary_device_id": device_id,
                    "aggregate_verified": False})


def checked_key():
    key = request.args.get("key", "")
    if not KEY_RE.fullmatch(key):
        abort(400, description="Некоректний сигнал")
    return key


def checked_utc(name):
    value = request.args.get(name, "")
    if len(value) > 40:
        abort(400, description="Некоректний часовий інтервал")
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.tzinfo is None or date.utcoffset() is None:
            raise ValueError("timezone required")
        return date.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, OverflowError):
        abort(400, description="Неправильна дата " + name)


def device_archive_end(device_id):
    item = one("SELECT MAX(observed_at_utc) AS stamp FROM rakurs_samples WHERE device_id=%s", (device_id,))
    return item.get("stamp") if item else None


def chart_points(device_id, key, start, end):
    """Keep actual first/last/min/max samples per time bucket; never invent averages.
    At most 960 points across the entire requested interval.
    Full raw history remains available separately for audit and CSV export.
    """
    empty = {"points": [], "truncated": False, "sample_count": 0, "resolution": "raw",
             "bucket_seconds": None, "start": serialize(start), "end": serialize(end)}
    if device_id is None or not start or not end or end < start:
        return empty
    where = (" FROM rakurs_samples AS m JOIN rakurs_signals AS s ON s.id=m.signal_id "
             "WHERE m.device_id=%s AND s.signal_key=%s "
             "AND m.observed_at_utc >= %s AND m.observed_at_utc <= %s")
    params = (device_id,key,start,end)
    count = one("SELECT COUNT(*) AS n" + where,params)
    n = int(count["n"]) if count else 0
    if n <= MAX_HISTORY:
        data = rows("SELECT m.observed_at_utc,m.value_decimal,m.raw_value,m.quality,s.unit" +
                    where + " ORDER BY m.observed_at_utc ASC LIMIT %s",params+(MAX_HISTORY,))
        return {**empty,"points":serialize(data),"sample_count":n}
    bucket_us = max(1, (int((end-start).total_seconds()*1000000)+240)//240)
    sql = (
        "WITH samples AS (SELECT m.observed_at_utc,m.value_decimal,m.raw_value,m.quality,s.unit,"
        "FLOOR(TIMESTAMPDIFF(MICROSECOND,%s,m.observed_at_utc)/%s) AS bucket_id" + where +
        "), ranked AS (SELECT samples.*,"
        "ROW_NUMBER() OVER (PARTITION BY bucket_id ORDER BY observed_at_utc) AS first_rank,"
        "ROW_NUMBER() OVER (PARTITION BY bucket_id ORDER BY observed_at_utc DESC) AS last_rank,"
        "ROW_NUMBER() OVER (PARTITION BY bucket_id ORDER BY value_decimal IS NULL,value_decimal,observed_at_utc) AS min_rank,"
        "ROW_NUMBER() OVER (PARTITION BY bucket_id ORDER BY value_decimal IS NULL,value_decimal DESC,observed_at_utc) AS max_rank "
        "FROM samples) SELECT observed_at_utc,value_decimal,raw_value,quality,unit,bucket_id "
        "FROM ranked WHERE first_rank=1 OR last_rank=1 OR min_rank=1 OR max_rank=1 ORDER BY observed_at_utc")
    data = rows(sql,(start,bucket_us)+params)
    return {**empty,"points":serialize(data),"sample_count":n,"resolution":"extrema",
            "bucket_seconds":bucket_us/1000000}


@app.get("/api/v1/history")
def history():
    key = checked_key()
    try:
        hours = int(request.args.get("hours", "24"))
    except ValueError:
        return jsonify({"error": "Некоректний проміжок часу"}), 400
    if not 1 <= hours <= 168:
        return jsonify({"error": "Доступно 1–168 годин"}), 400
    anchor = request.args.get("anchor", "now")
    if anchor not in {"now", "latest"}:
        abort(400, description="Некоректна прив'язка періоду")
    device_id = selected_overview_device_id()
    if device_id is None:
        return jsonify({"data_source": "mysql", "signal": None,
                        "hours": hours, "points": [], "truncated": False})
    signal = one(
        "SELECT s.signal_key,s.label_uk,s.unit FROM rakurs_signals AS s "
        "JOIN rakurs_samples AS m ON m.signal_id=s.id "
        "WHERE m.device_id=%s AND s.signal_key=%s LIMIT 1",
        (device_id, key),
    )
    if signal is None:
        return jsonify({"data_source": "mysql", "signal": None,
                        "hours": hours, "points": [], "truncated": False,
                        "primary_device_id": device_id,
                        "aggregate_verified": False})
    if request.args.get("resolution") == "auto":
        end = device_archive_end(device_id) if anchor == "latest" else datetime.now(timezone.utc).replace(tzinfo=None)
        start = end-timedelta(hours=hours) if end else None
        return jsonify({"data_source":"mysql","signal":signal,"hours":hours,
                        "primary_device_id":device_id,"aggregate_verified":False,
                        **chart_points(device_id,key,start,end)})
    # All signals of the selected device share one archive endpoint; do not
    # move the window independently for every graph.
    cutoff = ("(SELECT MAX(mx.observed_at_utc) FROM rakurs_samples AS mx "
              "WHERE mx.device_id=m.device_id)" if anchor == "latest" else "UTC_TIMESTAMP(6)")
    data = rows(
        "SELECT m.observed_at_utc,m.received_at_utc,m.value_decimal,"
        "m.raw_value,m.quality FROM rakurs_samples AS m "
        "JOIN rakurs_signals AS s ON s.id=m.signal_id "
        "WHERE m.device_id=%s AND s.signal_key=%s "
        "AND m.observed_at_utc>=" + cutoff + "-INTERVAL %s HOUR "
        "ORDER BY m.observed_at_utc DESC LIMIT %s",
        (device_id, key, hours, MAX_HISTORY + 1),
    )
    truncated = len(data) > MAX_HISTORY
    points = data[:MAX_HISTORY]
    points.reverse()
    return jsonify({
        "data_source": "mysql", "signal": signal, "hours": hours,
        "points": serialize(points), "truncated": truncated,
        "primary_device_id": device_id, "aggregate_verified": False,
    })


ENERGY_COUNTERS = ("pv_energy_total", "load_energy_total", "grid_import_total",
                   "grid_export_total", "battery_charge_total", "battery_discharge_total")


@app.get("/api/v1/energy/hourly")
def hourly_balance():
    try:
        day = datetime.strptime(request.args.get("day", ""), "%Y-%m-%d")
    except ValueError:
        abort(400, description="Оберіть добу")
    if not 2000 <= day.year <= 2100:
        abort(400, description="Дата поза діапазоном")
    zone = ZoneInfo("Europe/Kyiv")
    hour = timedelta(hours=1)
    def midnight(d):
        return d.replace(tzinfo=zone).astimezone(timezone.utc).replace(tzinfo=None)
    start, end = midnight(day), midnight(day+timedelta(days=1))
    prior = midnight(day-timedelta(days=1))
    compare = request.args.get("compare") == "1"
    devices = parallel_registered_devices()
    channels = {"pv1":None,"pv2":None}
    for d in devices:
        channels["pv"+str(d["slot_no"])] = (d,"pv_energy_total")
    for k in ENERGY_COUNTERS[1:]:
        channels[k] = (devices[0], k) if devices else None
    first = prior if compare else start
    by_hour = {}
    for device in devices:
        keys = [source[1] for source in channels.values() if source and source[0]["id"]==device["id"]]
        marks = ",".join(["%s"]*len(keys))
        samples = rows("SELECT s.signal_key,s.unit,m.observed_at_utc AS t,m.value_decimal AS v "
                       "FROM rakurs_samples m JOIN rakurs_signals s ON s.id=m.signal_id "
                       "WHERE m.device_id=%s AND s.signal_key IN ("+marks+") "
                       "AND m.observed_at_utc BETWEEN %s AND %s ORDER BY m.observed_at_utc",
                       (device["id"],)+tuple(keys)+(first,end))
        for r in samples:
            t = r["t"]
            h = t.replace(minute=0,second=0,microsecond=0)
            channel = "pv"+str(device["slot_no"]) if r["signal_key"]=="pv_energy_total" else r["signal_key"]
            valid = r["unit"] in {"кВт·год","кВт⋅год","кВт*год","kWh","кВт·г"}
            for bucket in ([h-hour,h] if t==h else [h]):
                by_hour.setdefault((bucket,channel),{})[t-bucket] = r["v"] if valid else None

    def delta(samples, lo=None, hi=None):
        items = [(t,v) for t,v in samples.items() if lo is None or lo<=t<=hi]
        result = {"value":None,"status":"missing"}
        if not items:
            return result
        a,opening = items[0]
        b,closing = items[-1]
        result.update(opening=opening,closing=closing)
        if any(v is None for t,v in items):
            result["status"]="invalid"
        elif any(y[1]<x[1] for x,y in zip(items,items[1:])):
            result["status"]="reset"
        elif b>a:
            result.update(value=closing-opening,status="full" if a==timedelta() and b==hour else "partial")
        return result

    # Match Kyiv wall-clock hours, including the fold on the autumn transition.
    def key(h):
        local = h.replace(tzinfo=timezone.utc).astimezone(zone)
        return local.hour,local.fold
    prior_hours = {}
    h = prior
    while h<start:
        prior_hours[key(h)] = h
        h += hour
    output = []
    h = start
    while h<end:
        local = h.replace(tzinfo=timezone.utc).astimezone(zone)
        bucket = {"start":h,"end":h+hour,"label":local.strftime("%H:%M"),"offset":local.strftime("%z"),
                  "channels":{},"comparison":{}}
        previous = prior_hours.get(key(h))
        for channel in channels:
            samples = by_hour.get((h,channel),{})
            reading = delta(samples)
            if samples:
                reading.update(first_at=h+min(samples),last_at=h+max(samples))
            bucket["channels"][channel] = reading
        if compare and previous is not None:
            for name,members in [("pv",["pv1","pv2"])]+[(c,[c]) for c in ENERGY_COUNTERS[1:]]:
                sets = [by_hour.get((base,c),{}) for base in (h,previous) for c in members]
                common = set.intersection(*(set(s) for s in sets))
                if len(common)<2:
                    continue
                lo,hi = min(common),max(common)
                vals = [delta(s,lo,hi)["value"] for s in sets]
                if None not in vals:
                    n=len(members)
                    bucket["comparison"][name]={"current":sum(vals[:n]),"previous":sum(vals[n:]),
                        "seconds":(hi-lo).total_seconds(),"first_at":h+lo,"last_at":h+hi,
                        "prior_first":previous+lo,"prior_last":previous+hi}
        output.append(bucket)
        h += hour
    sources = {c:{"device":d[0]["device_key"],"key":d[1]} for c,d in channels.items() if d}
    return jsonify(serialize({"hours":output,"sources":sources,"prior_day":(day-timedelta(days=1)).date().isoformat()}))


def energy_period(anchor, period):
    local = anchor.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Europe/Kyiv"))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "month":
        start = start.replace(day=1)
    elif period == "year":
        start = start.replace(month=1, day=1)
    return start.astimezone(timezone.utc).replace(tzinfo=None)


@app.get("/api/v1/energy")
def energy():
    """Read-only observed counter differences; never sum shared device reports."""
    period = request.args.get("period", "day")
    if period not in {"day", "month", "year", "all", "range"}:
        abort(400, description="Некоректний період енергії")
    device_id = selected_overview_device_id()
    if device_id is None:
        return jsonify({"period": period, "counters": [], "anchor": None})
    latest_row = one("SELECT MAX(observed_at_utc) AS stamp FROM rakurs_samples "
                     "WHERE device_id=%s", (device_id,))
    anchor = latest_row.get("stamp") if latest_row else None
    if not isinstance(anchor, datetime):
        return jsonify({"period": period, "counters": [], "anchor": None})
    if anchor.tzinfo is not None:
        anchor = anchor.astimezone(timezone.utc).replace(tzinfo=None)
    if period == "range":
        start, anchor = checked_utc("start"), checked_utc("end")
        if not 0 < (anchor-start).total_seconds() <= 31*86400:
            abort(400, description="Некоректний часовий інтервал")
    elif period == "all":
        opening = one("SELECT MIN(observed_at_utc) AS stamp FROM rakurs_samples WHERE device_id=%s",(device_id,))
        start = (opening or {}).get("stamp") or anchor
    else:
        start = energy_period(anchor, period)
    placeholders = ",".join("%s" for _ in ENERGY_COUNTERS)
    # MySQL 8 window functions: inspect all stored numeric counter samples
    # within the requested period, including any decreases/resets.
    data = rows(
        "WITH ordered AS ("
        "SELECT s.signal_key,s.unit,m.observed_at_utc,m.value_decimal,"
        "LAG(m.value_decimal) OVER (PARTITION BY s.id ORDER BY m.observed_at_utc) AS prev,"
        "FIRST_VALUE(m.value_decimal) OVER (PARTITION BY s.id ORDER BY m.observed_at_utc) AS opening_value,"
        "ROW_NUMBER() OVER (PARTITION BY s.id ORDER BY m.observed_at_utc DESC) AS last_rank "
        "FROM rakurs_signals s JOIN rakurs_samples m ON m.signal_id=s.id "
        "WHERE m.device_id=%s AND s.signal_key IN (" + placeholders + ") "
        "AND m.observed_at_utc BETWEEN %s AND %s AND m.value_decimal IS NOT NULL) "
        "SELECT signal_key,unit,COUNT(*) AS samples,MIN(observed_at_utc) AS first_at,"
        "MAX(observed_at_utc) AS last_at,MAX(CASE WHEN last_rank=1 THEN value_decimal END) AS closing_value,"
        "MAX(opening_value) AS opening_value,SUM(CASE WHEN prev>value_decimal THEN 1 ELSE 0 END) AS decreases "
        "FROM ordered GROUP BY signal_key,unit",
        (device_id,) + ENERGY_COUNTERS + (start, anchor))
    result = []
    for item in data:
        first, last = item["opening_value"], item["closing_value"]
        valid = (item["samples"] >= 2 and not item["decreases"] and
                 first is not None and last is not None and last >= first and
                 item["unit"] in {"кВт·год", "кВт⋅год", "кВт*год", "kWh", "кВт·г"})
        result.append({"key": item["signal_key"], "unit": item["unit"],
                       "value": last-first if valid else None,
                       "samples": item["samples"], "first_at": item["first_at"],
                       "last_at": item["last_at"], "decreases": item["decreases"],
                       "coverage_seconds": max(0,(item["last_at"]-item["first_at"]).total_seconds()),
                       "requested_seconds": max(0,(anchor-start).total_seconds())})
    daily = []
    if period == "range":
        zone = ZoneInfo("Europe/Kyiv")
        local = start.replace(tzinfo=timezone.utc).astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0)
        while local.astimezone(timezone.utc).replace(tzinfo=None) <= anchor:
            following = local + timedelta(days=1)
            lower = max(start, local.astimezone(timezone.utc).replace(tzinfo=None))
            upper = following.astimezone(timezone.utc).replace(tzinfo=None)
            reading = one("SELECT m.value_decimal AS value FROM rakurs_samples m "
                          "JOIN rakurs_signals s ON s.id=m.signal_id "
                          "WHERE m.device_id=%s AND s.signal_key='pv_energy_today' "
                          "AND m.value_decimal IS NOT NULL AND m.observed_at_utc >= %s "
                          "AND m.observed_at_utc < %s AND m.observed_at_utc <= %s "
                          "ORDER BY m.observed_at_utc DESC LIMIT 1", (device_id, lower, upper, anchor))
            if reading:
                daily.append({"day": local.date().isoformat(), "value": reading["value"]})
            local = following
    return jsonify(serialize({"period": period, "anchor": anchor, "start": start,
                              "daily": daily, "counters": result, "aggregate_verified": False}))


@app.get("/api/v1/solar/calendar")
def solar_calendar():
    month = request.args.get("month", "")
    if not re.fullmatch(r"(?:19|20)\d{2}-(?:0[1-9]|1[0-2])", month):
        abort(400, description="Некоректний місяць")
    zone = ZoneInfo("Europe/Kyiv")
    opening = datetime.strptime(month, "%Y-%m").replace(tzinfo=zone)
    following = (opening + timedelta(days=32)).replace(day=1)
    devices = parallel_registered_devices()
    ids = tuple(d["id"] for d in devices)
    days = {}
    if ids:
        # UTC hour buckets remain distinct during the autumn clock change.
        utc_bounds = tuple(d.astimezone(timezone.utc).replace(tzinfo=None) for d in (opening, following))
        hourly = rows(
            "WITH ranked AS (SELECT m.device_id,m.observed_at_utc,m.value_decimal,"
            "ROW_NUMBER() OVER (PARTITION BY m.device_id,DATE(m.observed_at_utc),"
            "HOUR(m.observed_at_utc) ORDER BY m.observed_at_utc DESC) AS ordinal "
            "FROM rakurs_samples m JOIN rakurs_signals s ON s.id=m.signal_id "
            "WHERE s.signal_key='pv_energy_today' AND m.value_decimal IS NOT NULL "
            "AND m.observed_at_utc >= %s AND m.observed_at_utc < %s AND m.device_id IN ("
            + ",".join(["%s"]*len(ids)) + ")) SELECT device_id,observed_at_utc,value_decimal "
            "FROM ranked WHERE ordinal=1 ORDER BY observed_at_utc", utc_bounds + ids)
        slots = {d["id"]:str(d["slot_no"]) for d in devices}
        for row in hourly:
            local_day = row["observed_at_utc"].replace(tzinfo=timezone.utc).astimezone(zone).date().isoformat()
            item = days.setdefault(local_day, {}).setdefault(slots[row["device_id"]], {"hours":0})
            item["hours"] += 1
            item["kwh"] = row["value_decimal"]
    from .solar_forecast import load_config
    try:
        cfg = load_config()
    except (OSError, ValueError):
        cfg = None
    return jsonify(serialize({"days":days, "kwp":{str(a["slot"]):a["kwp"] for a in (cfg or {}).get("arrays", [])}}))


@app.get("/api/v1/solar-forecast")
def solar_forecast():
    from .solar_forecast import forecast
    return jsonify(forecast())


@app.get("/api/v1/history/range")
def history_range():
    """Bounded historical export, preserving the selected Deye device."""
    key = checked_key()
    start = checked_utc("start")
    end = checked_utc("end")
    seconds = (end-start).total_seconds()
    if seconds <= 0 or seconds > 31*86400:
        abort(400, description="Діапазон має бути 1 секунда — 31 доба")
    selected = selected_overview_device_id()
    if selected is None:
        return jsonify({"data_source":"mysql","signal_key":key,
                        "points":[],"truncated":False})
    if request.args.get("resolution") == "auto":
        return jsonify({"data_source":"mysql","signal_key":key,"primary_device_id":selected,
                        **chart_points(selected,key,start,end)})
    before = checked_utc("before") if request.args.get("before") else None
    if before is not None and not start < before <= end:
        abort(400, description="Некоректний курсор архіву")
    data=rows(
        "SELECT m.observed_at_utc,m.value_decimal,m.raw_value,m.quality,s.unit "
        "FROM rakurs_samples AS m "
        "JOIN rakurs_signals AS s ON s.id=m.signal_id "
        "WHERE m.device_id=%s AND s.signal_key=%s "
        "AND m.observed_at_utc >= %s AND m.observed_at_utc <= %s "
        + ("AND m.observed_at_utc < %s " if before is not None else "") +
        "ORDER BY m.observed_at_utc DESC LIMIT %s",
        (selected,key,start,end)+((before,) if before is not None else ())+(MAX_HISTORY+1,))
    truncated=len(data)>MAX_HISTORY
    result=data[:MAX_HISTORY]
    result.reverse()
    return jsonify({"data_source":"mysql","signal_key":key,
                    "points":serialize(result),"truncated":truncated,
                    "next_before":serialize(result[0]["observed_at_utc"]) if truncated and result else None,
                    "primary_device_id":selected,
                    "start":serialize(start),"end":serialize(end)})


@app.get("/api/v1/alerts/timeline-rules")
def timeline_rules():
    from .alert_monitor import load_rules
    return jsonify({"rules":list(load_rules().values())})


@app.get("/api/v1/alerts")
def alerts():
    from .alert_monitor import public_status
    return jsonify(public_status())


@app.get("/api/v1/events")
def events():
    offset, limit = parallel_page_options(100)
    severity = request.args.get("severity","")
    if severity not in {"","info","warning","critical"}:
        abort(400,description="Некоректний рівень події")
    device = request.args.get("device","")
    if device and not KEY_RE.fullmatch(device):
        abort(400,description="Некоректний пристрій")
    filters, args = [], []
    if device:
        filters.append("d.device_key=%s"); args.append(device)
    if severity:
        levels = {"critical":("error","critical","alarm"),"warning":("warning","warn"),"info":("info",)}[severity]
        filters.append("LOWER(e.severity) IN ("+",".join(["%s"]*len(levels))+")"); args.extend(levels)
    if request.args.get("start"):
        filters.append("e.observed_at_utc >= %s"); args.append(checked_utc("start"))
    if request.args.get("end"):
        filters.append("e.observed_at_utc <= %s"); args.append(checked_utc("end"))
    where = "WHERE "+" AND ".join(filters)+" " if filters else ""
    result = rows(
        "SELECT e.observed_at_utc,e.severity,e.event_code,e.message,"
        "d.device_key FROM rakurs_device_events AS e "
        "JOIN rakurs_devices AS d ON d.id=e.device_id "
        + where + "ORDER BY e.observed_at_utc DESC,e.id DESC LIMIT %s OFFSET %s",
        tuple(args)+(limit+1,offset)
    )
    return jsonify({"data_source": "mysql", "events": serialize(result[:limit]),
                    "limit":limit,"offset":offset,"has_more":len(result)>limit})


@app.get("/api/v1/devices")
def devices():
    result = rows(
        "SELECT device_key,kind,model,commissioned_at_utc "
        "FROM rakurs_devices ORDER BY device_key LIMIT 100"
    )
    return jsonify({"data_source": "mysql", "devices": serialize(result)})


@app.get("/api/v1/batches")
def batches():
    result = rows(
        "SELECT b.started_at_utc,b.finished_at_utc,b.received_count,"
        "b.accepted_count,b.rejected_count,b.status,b.error_code,d.device_key "
        "FROM rakurs_ingest_batches AS b "
        "JOIN rakurs_devices AS d ON d.id=b.device_id "
        "ORDER BY b.started_at_utc DESC LIMIT 50"
    )
    return jsonify({"data_source": "mysql", "batches": serialize(result)})

# Parallel group: observation only. Do not infer physical wiring from Modbus
# addresses, and do not sum values across devices until provenance is verified.
def parallel_registered_devices():
    """Two Deye slots from MySQL, irrespective of legacy import classification.

    Prefer physical registrations when an archive and live device occupy
    the same slot. Rows are never treated as verified electrical topology.
    """
    entries = rows(
        "SELECT d.id,d.device_key,d.kind,d.model,d.serial_number,"
        "pm.slot_no,pm.parallel_role,pm.modbus_address,"
        "pm.member_verified,pm.transport_verified "
        "FROM rakurs_devices AS d "
        "JOIN rakurs_parallel_members AS pm ON pm.device_id=d.id "
        "JOIN rakurs_parallel_groups AS pg ON pg.id=pm.group_id "
        "WHERE d.model LIKE %s AND pg.group_key=%s "
        "ORDER BY pm.slot_no,"
        "CASE WHEN pm.member_kind='physical' AND pm.member_verified=1 THEN 0 "
        "WHEN pm.member_kind='physical' THEN 1 ELSE 2 END,"
        "CASE WHEN d.kind='inverter' THEN 0 ELSE 1 END,"
        "d.id LIMIT 20",
        ("%" + PARALLEL_MODEL + "%", "deye_sun20k_parallel_shared_battery"),
    )
    result = []
    seen = set()
    for item in entries:
        slot = item.get("slot_no")
        if slot not in seen and slot in (1, 2):
            result.append(item)
            seen.add(slot)
    return result


@app.get("/api/v1/parallel")
def parallel_topology():
    devices = parallel_registered_devices()
    return jsonify({
        "data_source": "mysql",
        "profile": "deye_sun20k_sg05lp3_eu_sm2_parallel2_shared_battery",
        **describe_topology(devices),
    })


def parallel_selected_device():
    key = request.args.get("device", "")
    if not KEY_RE.fullmatch(key):
        abort(400, description="Оберіть зареєстрований інвертор")
    devices = parallel_registered_devices()
    found = next((item for item in devices if item["device_key"] == key), None)
    if found is None:
        abort(404, description="Інвертор не зареєстровано у профілі")
    return found


@app.get("/api/v1/parallel/frame")
def parallel_archive_frame():
    """Exact timestamp, both registered slots. No interpolation or nearest row."""
    at = checked_utc("at")
    devices = parallel_registered_devices()
    output = [{"device_key":d["device_key"],"slot_no":d["slot_no"],"values":{}} for d in devices]
    keys = PARALLEL_SIGNAL_KEYS
    if request.args.get("group") == "mppt":
        keys = tuple(f"pv{n}_{field}" for n in (1, 2) for field in ("power", "voltage", "current"))
    if devices:
        ids = tuple(d["id"] for d in devices)
        samples = rows(
            "SELECT m.device_id,s.signal_key,s.unit,m.value_decimal,m.quality,m.observed_at_utc "
            "FROM rakurs_samples AS m JOIN rakurs_signals AS s ON s.id=m.signal_id "
            "WHERE m.device_id IN ("+",".join(["%s"]*len(ids))+") "
            "AND m.observed_at_utc=%s AND s.signal_key IN ("+
            ",".join(["%s"]*len(keys))+")",
            ids+(at,)+keys)
        by_id = {d["id"]:item for d,item in zip(devices,output)}
        for sample in samples:
            item = by_id.get(sample["device_id"])
            if item is not None:
                item["values"][sample["signal_key"]] = sample
    return jsonify(serialize({"data_source":"mysql","at":at,"devices":output,
                              "aggregate_verified":False}))


@app.get("/api/v1/parallel/latest")
def parallel_device_latest():
    selected = parallel_selected_device()
    placeholders = ",".join("%s" for _ in PARALLEL_SIGNAL_KEYS)
    samples = rows(
        "SELECT s.signal_key, s.label_uk, s.unit, m.value_decimal, "
        "m.quality, m.observed_at_utc, d.device_key "
        "FROM rakurs_samples AS m "
        "JOIN rakurs_signals AS s ON s.id=m.signal_id "
        "JOIN rakurs_devices AS d ON d.id=m.device_id "
        "WHERE m.device_id=%s AND s.signal_key IN (" + placeholders + ") "
        ""
        "AND m.observed_at_utc=("
        "SELECT MAX(mx.observed_at_utc) FROM rakurs_samples AS mx "
        "WHERE mx.device_id=m.device_id AND mx.signal_id=m.signal_id "
        ") "
        "ORDER BY s.signal_key LIMIT 32",
        (selected["id"],) + PARALLEL_SIGNAL_KEYS,
    )
    latest = {}
    for sample in samples:
        latest.setdefault(sample["signal_key"], sample)
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    for sample in latest.values():
        stamp = sample.get("observed_at_utc")
        if isinstance(stamp, datetime):
            if stamp.tzinfo is not None:
                stamp = stamp.astimezone(timezone.utc).replace(tzinfo=None)
            age = (now_utc - stamp).total_seconds()
            sample["age_seconds"] = max(0, int(age))
            sample["freshness_state"] = (
                "invalid_time" if age < -60 else
                "online" if age <= 120 else
                "warning" if age <= 600 else "offline"
            )
        else:
            sample["age_seconds"] = None
            sample["freshness_state"] = "unknown"
    return jsonify({
        "data_source": "mysql", 
        "device_key": selected["device_key"],
        "values": serialize(latest),
        "total_values": None,
        "shared_battery_soc": None,
        "aggregation_verified": False,
    })


@app.get("/api/v1/parallel/events")
def parallel_device_events():
    selected = parallel_selected_device()
    entries = rows(
        "SELECT e.observed_at_utc, e.severity, e.event_code, e.message, "
        "e.source, d.device_key "
        "FROM rakurs_device_events AS e "
        "JOIN rakurs_devices AS d ON d.id=e.device_id "
        "WHERE e.device_id=%s ORDER BY e.observed_at_utc DESC LIMIT 50",
        (selected["id"],),
    )
    return jsonify({
        "data_source": "mysql", "device_key": selected["device_key"],
        "events": serialize(entries),
    })


@app.get("/api/v1/parallel/history")
def parallel_device_history():
    selected = parallel_selected_device()
    key = checked_key()
    if key not in PARALLEL_SIGNAL_KEYS:
        abort(400, description="Непідтверджений сигнал профілю")
    try:
        hours = int(request.args.get("hours", "24"))
    except ValueError:
        abort(400, description="Некоректний проміжок часу")
    if not 1 <= hours <= 168:
        abort(400, description="Доступно 1–168 годин")
    anchor = request.args.get("anchor","now")
    if anchor not in {"now","latest"}:
        abort(400,description="Некоректний період")
    if request.args.get("resolution") == "auto":
        end = device_archive_end(selected["id"]) if anchor == "latest" else datetime.now(timezone.utc).replace(tzinfo=None)
        return jsonify({"data_source":"mysql","device_key":selected["device_key"],"signal_key":key,
                        "hours":hours,"aggregation_verified":False,
                        **chart_points(selected["id"],key,end-timedelta(hours=hours) if end else None,end)})
    data = rows(
        "SELECT m.observed_at_utc, m.value_decimal, m.quality, s.unit "
        "FROM rakurs_samples AS m "
        "JOIN rakurs_signals AS s ON s.id=m.signal_id "
        "WHERE m.device_id=%s AND s.signal_key=%s "
        ""
        "AND m.observed_at_utc >= " + ("(SELECT MAX(mx.observed_at_utc) FROM rakurs_samples mx WHERE mx.device_id=m.device_id)" if anchor=="latest" else "UTC_TIMESTAMP(6)") + " - INTERVAL %s HOUR "
        "ORDER BY m.observed_at_utc DESC LIMIT %s",
        (selected["id"], key, hours, MAX_HISTORY + 1),
    )
    truncated = len(data) > MAX_HISTORY
    points = data[:MAX_HISTORY]
    points.reverse()
    return jsonify({
        "data_source": "mysql", "device_key": selected["device_key"],
        "signal_key": key, "hours": hours,
        "points": serialize(points), "truncated": truncated,
        "aggregation_verified": False,
    })


POLL_UID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def parallel_live_poll():
    selected = parallel_selected_device()
    poll_uid = request.args.get("poll_uid", "")
    if poll_uid and not POLL_UID_RE.fullmatch(poll_uid):
        abort(400, description="Некоректний ідентифікатор опитування")
    where = "AND poll_uid=%s " if poll_uid else ""
    params = ((selected["id"], poll_uid) if poll_uid else (selected["id"],))
    poll = one(
        "SELECT id,poll_uid,observed_at_utc,status,quality,request_count,"
        "response_count,exception_count FROM rakurs_modbus_poll_runs "
        "WHERE device_id=%s " + where +
        "ORDER BY observed_at_utc DESC,id DESC LIMIT 1", params,
    )
    return selected, poll


def parallel_page_options(max_limit):
    try:
        offset = int(request.args.get("offset", "0"))
        limit = int(request.args.get("limit", "100"))
    except ValueError:
        abort(400, description="Некоректні параметри сторінки")
    if not 0 <= offset <= 100000 or not 1 <= limit <= max_limit:
        abort(400, description="Некоректний діапазон сторінки")
    return offset, limit


@app.get("/api/v1/parallel/health")
def parallel_live_health():
    """Observation-only health: no connection/collector activity is inferred.

    A recent poll is evidence of archived communication, NOT proof that a
    continuously running collector process is healthy.
    """
    devices = parallel_registered_devices()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    entries = []
    for device in devices:
        last = one(
            "SELECT observed_at_utc,finished_at_utc,status,quality,"
            "response_count,exception_count,transport,slave_id "
            "FROM rakurs_modbus_poll_runs "
            "WHERE device_id=%s "
            "ORDER BY observed_at_utc DESC,id DESC LIMIT 1",
            (device["id"],),
        )
        age = None
        freshness = "no_data"
        if last and isinstance(last.get("observed_at_utc"), datetime):
            observed = last["observed_at_utc"]
            if observed.tzinfo is not None:
                observed = observed.astimezone(timezone.utc).replace(tzinfo=None)
            age = (now - observed).total_seconds()
            freshness = ("invalid_time" if age < -60 else
                         "recent" if age <= 120 else
                         "delayed" if age <= 600 else "stale")
        entries.append({
            "device_key": device["device_key"],
            "slot_no": device.get("slot_no"),
            "member_verified": bool(device.get("member_verified")),
            "transport_verified": bool(device.get("transport_verified")),
            "age_seconds": max(0, int(age)) if age is not None else None,
            "freshness_state": freshness,
            "last_poll": serialize(last),
            "collector_process_confirmed": False,
        })
    return jsonify({
        "data_source": "mysql", "expected_inverters": 2,
         "collector_process_confirmed": False,
        "devices": entries, "shared_battery_soc_verified": False,
        "warning": "Recent polls do not confirm collector process health; "
                   "no physical Master/Slave or BMS source is inferred.",
    })


@app.get("/api/v1/parallel/polls")
def parallel_live_polls():
    selected = parallel_selected_device()
    offset, limit = parallel_page_options(100)
    total = one(
        "SELECT COUNT(*) AS n FROM rakurs_modbus_poll_runs "
        "WHERE device_id=%s ", (selected["id"],),
    )
    polls = rows(
        "SELECT poll_uid,observed_at_utc,finished_at_utc,status,quality,"
        "transport,slave_id,request_count,response_count,exception_count "
        "FROM rakurs_modbus_poll_runs "
        "WHERE device_id=%s "
        "ORDER BY observed_at_utc DESC,id DESC LIMIT %s OFFSET %s",
        (selected["id"], limit, offset),
    )
    return jsonify({
        "data_source":"mysql",
        "device_key":selected["device_key"],"offset":offset,"limit":limit,
        "total":total["n"] if total else 0,"polls":serialize(polls),
    })


@app.get("/api/v1/parallel/raw")
def parallel_live_raw():
    selected, poll = parallel_live_poll()
    offset, limit = parallel_page_options(500)
    if not poll:
        return jsonify({"device_key":selected["device_key"],"poll":None,
                        "total":0,"raw_registers":[]})
    total = one(
        "SELECT COUNT(*) AS n FROM rakurs_modbus_register_samples "
        "WHERE device_id=%s AND poll_id=%s ",
        (selected["id"], poll["id"]),
    )
    records = rows(
        "SELECT frame_id,observed_at_utc,function_code,register_address,"
        "raw_uint16,quality,ingest_source "
        "FROM rakurs_modbus_register_samples "
        "WHERE device_id=%s AND poll_id=%s "
        "ORDER BY frame_id,register_address LIMIT %s OFFSET %s",
        (selected["id"], poll["id"], limit, offset),
    )
    return jsonify({"device_key":selected["device_key"],
                    "poll":serialize(poll),"offset":offset,"limit":limit,
                    "total":total["n"] if total else 0,
                    "raw_registers":serialize(records)})


@app.get("/api/v1/parallel/bits")
def parallel_live_bits():
    selected, poll = parallel_live_poll()
    offset, limit = parallel_page_options(500)
    if not poll:
        return jsonify({"device_key":selected["device_key"],"poll":None,
                        "total":0,"bits":[]})
    total = one(
        "SELECT COUNT(*) AS n FROM rakurs_modbus_bit_samples "
        "WHERE device_id=%s AND poll_id=%s ",
        (selected["id"], poll["id"]),
    )
    records = rows(
        "SELECT frame_id,observed_at_utc,function_code,bit_address,"
        "bit_value,quality,ingest_source FROM rakurs_modbus_bit_samples "
        "WHERE device_id=%s AND poll_id=%s "
        "ORDER BY frame_id,bit_address LIMIT %s OFFSET %s",
        (selected["id"], poll["id"], limit, offset),
    )
    return jsonify({"device_key":selected["device_key"],
                    "poll":serialize(poll),"offset":offset,"limit":limit,
                    "total":total["n"] if total else 0,"bits":serialize(records)})


@app.get("/api/v1/parallel/frames")
def parallel_live_frames():
    selected, poll = parallel_live_poll()
    offset, limit = parallel_page_options(100)
    if not poll:
        return jsonify({"device_key":selected["device_key"],"poll":None,
                        "total":0,"frames":[]})
    total = one(
        "SELECT COUNT(*) AS n FROM rakurs_modbus_frames "
        "WHERE device_id=%s AND poll_id=%s", (selected["id"], poll["id"]),
    )
    records = rows(
        "SELECT sequence_no,captured_at_utc,function_code,start_address,"
        "requested_count,status,exception_code,error_code,"
        "HEX(request_bytes) AS request_hex,HEX(response_bytes) AS response_hex "
        "FROM rakurs_modbus_frames "
        "WHERE device_id=%s AND poll_id=%s "
        "ORDER BY sequence_no LIMIT %s OFFSET %s",
        (selected["id"], poll["id"], limit, offset),
    )
    return jsonify({"device_key":selected["device_key"],
                    "poll":serialize(poll),"offset":offset,"limit":limit,
                    "total":total["n"] if total else 0,"frames":serialize(records)})


@app.get("/api/v1/parallel/modbus-fields")
def parallel_device_modbus():
    selected = parallel_selected_device()
    data = rows(
        "SELECT f.field_key, f.group_name, f.field_name, f.unit, "
        "f.registers_json, m.value_decimal, m.value_text, "
        "m.quality, m.observed_at_utc "
        "FROM rakurs_modbus_fields AS f "
        "LEFT JOIN (SELECT field_id,MAX(observed_at_utc) AS latest_at "
        "FROM rakurs_modbus_field_samples WHERE device_id=%s GROUP BY field_id) "
        "AS recent ON recent.field_id=f.id "
        "LEFT JOIN rakurs_modbus_field_samples AS m ON m.device_id=%s "
        "AND m.field_id=recent.field_id AND m.observed_at_utc=recent.latest_at "
        "ORDER BY f.id LIMIT 700",
        (selected["id"], selected["id"]),
    )
    return jsonify({
        "data_source": "mysql", "device_key": selected["device_key"],
        "fields": serialize(data), "count": len(data),
        "verified_only": False, "aggregation_verified": False,
    })


@app.get("/api/v1/modbus-fields")
def modbus_fields():
    selected = selected_overview_device_id()
    device_id = selected if selected is not None else -1
    data = rows(
        "SELECT f.field_key,f.group_name,f.field_name,f.platform,"
        "f.unit,f.registers_json,f.derived,f.verified,"
        "m.value_decimal,m.value_text,m.quality,m.observed_at_utc "
        "FROM rakurs_modbus_fields AS f "
        "LEFT JOIN (SELECT field_id,MAX(observed_at_utc) AS latest_at "
        "FROM rakurs_modbus_field_samples WHERE device_id=%s GROUP BY field_id) "
        "AS recent ON recent.field_id=f.id "
        "LEFT JOIN rakurs_modbus_field_samples AS m ON m.device_id=%s "
        "AND m.field_id=recent.field_id AND m.observed_at_utc=recent.latest_at "
        "ORDER BY f.id LIMIT 700",
        (device_id, device_id),
    )
    found = one("SELECT COUNT(*) AS n FROM rakurs_modbus_register_inventory")
    return jsonify({
        "data_source": "mysql", "count": len(data),
        "register_count": found["n"] if found else 0,
        "fields": serialize(data), "primary_device_id": selected,
        "aggregate_verified": False,
    })


# Administrative configuration writes are restricted to dedicated config
# tables; the web service never writes measurement or Modbus register tables.
from .config_api import register as register_config_api
register_config_api(app, rows=rows, one=one, conn=conn)

# Register MySQL-backed authorization after all application routes exist.
from .auth import init_auth
init_auth(app, lambda sql, params=(): one(sql, params), lambda: conn())
