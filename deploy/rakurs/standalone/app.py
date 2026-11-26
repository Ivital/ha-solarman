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
        "font-src 'self'; base-uri 'none'; form-action 'self'; "
        "frame-ancestors 'none'"
    )
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
    if period not in {"day", "month", "year", "all"}:
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
    if period == "all":
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
    return jsonify(serialize({"period": period, "anchor": anchor, "start": start,
                              "counters": result, "aggregate_verified": False}))


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
