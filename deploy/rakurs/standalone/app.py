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
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pymysql
from flask import Flask, abort, g, jsonify, render_template, request
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
DEMO_DEVICE_KEY = "simulated_deye_sg05lp3"
DEMO_INGEST_SOURCE = "rakurs_demo_one_shot"
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
    """One stable device for legacy single-device KPI and trend routes.

    This never aggregates two inverters. Individual device routes display
    both units independently. After DB cleanup the first ingested device
    becomes the primary source automatically.
    """
    if not hasattr(g, "primary_device"):
        found = one(
            "SELECT device_id AS id FROM rakurs_samples ORDER BY id LIMIT 1"
        )
        g.primary_device = found["id"] if found else None
    return g.primary_device

@app.get("/")
def index():
    selected = primary_device_id()
    readings = []
    if selected is not None:
        keys = tuple(key for key, _, _ in HOME_METRICS)
        placeholders = ",".join(["%s"] * len(keys))
        readings = rows(
            "SELECT s.signal_key,s.unit,m.value_decimal,m.quality "
            "FROM rakurs_signals AS s "
            "JOIN rakurs_samples AS m ON m.id=("
            "SELECT mx.id FROM rakurs_samples AS mx "
            "WHERE mx.device_id=%s AND mx.signal_id=s.id "
            "ORDER BY mx.observed_at_utc DESC,mx.id DESC LIMIT 1) "
            "WHERE s.signal_key IN (" + placeholders + ") "
            "ORDER BY s.signal_key",
            (selected,) + keys,
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
    return render_template("index.html", initial_cards=cards, initial_simulated=False)


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
    device_id = primary_device_id()
    result = []
    if device_id is not None:
        result = rows(
            "SELECT s.signal_key,s.label_uk,s.unit,m.value_decimal,"
            "m.quality,m.observed_at_utc FROM rakurs_signals AS s "
            "JOIN rakurs_samples AS m ON m.id=("
            "SELECT mx.id FROM rakurs_samples AS mx "
            "WHERE mx.device_id=%s AND mx.signal_id=s.id "
            "ORDER BY mx.observed_at_utc DESC,mx.id DESC LIMIT 1) "
            "ORDER BY s.label_uk LIMIT 500",
            (device_id,),
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
    device_id = primary_device_id()
    params = (device_id,) if device_id is not None else (-1,)
    result = rows(
        "SELECT f.field_key,f.group_name,f.field_name,f.unit,"
        "f.registers_json,m.value_decimal,m.value_text,m.quality "
        "FROM rakurs_modbus_fields AS f "
        "LEFT JOIN rakurs_modbus_field_samples AS m ON m.id=("
        "SELECT mx.id FROM rakurs_modbus_field_samples AS mx "
        "WHERE mx.device_id=%s AND mx.field_id=f.id "
        "ORDER BY mx.observed_at_utc DESC,mx.id DESC LIMIT 1) "
        "ORDER BY f.id LIMIT 700", params,
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

@app.get("/api/v1/catalog")
def catalog():
    # Unverified planned catalog, NOT actual measured values.
    with CATALOG_FILE.open("r", encoding="utf-8") as source:
        return jsonify(json.load(source))


def demo_mode() -> bool:
    """Deprecated compatibility hook. RAKURS has one operational data mode."""
    return False


@app.get("/api/v1/status")
def status():
    devices = one("SELECT COUNT(*) AS n FROM rakurs_devices")
    signals = one(
        "SELECT COUNT(DISTINCT signal_id) AS n FROM rakurs_samples"
    )
    sample = one("SELECT 1 AS n FROM rakurs_samples LIMIT 1")
    total_signals = signals["n"] if signals else 0
    return jsonify({
        "data_source": "mysql", "mode": "real", "database": SCHEMA,
        "mysql_host": HOST,
        "registered_devices": devices["n"] if devices else 0,
        "verified_signals": total_signals,
        "displayed_signals": total_signals,
        "has_real_samples": bool(sample), "collector_active": False,
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
    device_id = primary_device_id()
    found = {}
    if device_id is not None:
        for key in keys:
            result = one(
                "SELECT s.signal_key,s.label_uk,s.unit,m.value_decimal,"
                "m.raw_value,m.quality,m.observed_at_utc "
                "FROM rakurs_samples AS m "
                "JOIN rakurs_signals AS s ON s.id=m.signal_id "
                "WHERE m.device_id=%s AND s.signal_key=%s "
                "ORDER BY m.observed_at_utc DESC,m.id DESC LIMIT 1",
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


@app.get("/api/v1/history")
def history():
    key = checked_key()
    try:
        hours = int(request.args.get("hours", "24"))
    except ValueError:
        return jsonify({"error": "Некоректний проміжок часу"}), 400
    if not 1 <= hours <= 168:
        return jsonify({"error": "Доступно 1–168 годин"}), 400
    device_id = primary_device_id()
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
                        "hours": hours, "points": [], "truncated": False})
    data = rows(
        "SELECT m.observed_at_utc,m.received_at_utc,m.value_decimal,"
        "m.raw_value,m.quality FROM rakurs_samples AS m "
        "JOIN rakurs_signals AS s ON s.id=m.signal_id "
        "WHERE m.device_id=%s AND s.signal_key=%s "
        "AND m.observed_at_utc>=UTC_TIMESTAMP(6)-INTERVAL %s HOUR "
        "ORDER BY m.observed_at_utc DESC,m.id DESC LIMIT %s",
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


@app.get("/api/v1/events")
def events():
    result = rows(
        "SELECT e.observed_at_utc,e.severity,e.event_code,e.message,"
        "d.device_key FROM rakurs_device_events AS e "
        "JOIN rakurs_devices AS d ON d.id=e.device_id "
        "ORDER BY e.observed_at_utc DESC LIMIT 50"
    )
    return jsonify({"data_source": "mysql", "events": serialize(result)})


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
    return jsonify({"data_source": "mysql", "batches": serialize(result)}

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
        "ORDER BY pm.slot_no,CASE WHEN d.kind='inverter' THEN 0 ELSE 1 END,"
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


# The twin simulator is separate from physical equipment inventory.
# These endpoints never change the existing real-only /parallel/latest route.
PARALLEL_DEMO_KEYS = ("sim_deye20k_parallel_1", "sim_deye20k_parallel_2")
PARALLEL_DEMO_SOURCE = "rakurs_parallel_sim_seed_v1"


def parallel_demo_storage_ready():
    found = one(
        "SELECT COUNT(*) AS n FROM information_schema.tables "
        "WHERE table_schema=%s AND table_name IN "
        "('rakurs_parallel_members','rakurs_modbus_poll_runs',"
        "'rakurs_modbus_register_samples')",
        (SCHEMA,),
    )
    return found is not None and found["n"] == 3


def parallel_demo_selected():
    key = request.args.get("device", "")
    if key not in PARALLEL_DEMO_KEYS:
        abort(404, description="Невідомий архівний профіль інвертора")
    if not parallel_demo_storage_ready():
        abort(404, description="Архів двох інверторів ще не створено")
    result = one(
        "SELECT d.id,d.device_key,pm.slot_no FROM rakurs_devices AS d "
        "JOIN rakurs_parallel_members AS pm ON pm.device_id=d.id "
        "JOIN rakurs_parallel_groups AS pg ON pg.id=pm.group_id "
        "WHERE d.device_key=%s AND d.kind='simulation' "
        "AND pm.member_kind='simulation' AND pg.model=%s",
        (key, PARALLEL_MODEL),
    )
    if result is None:
        abort(404, description="Архівний профіль інвертора не знайдено")
    return result


@app.get("/api/v1/parallel/simulation")
def parallel_simulation_overview():
    if not parallel_demo_storage_ready():
        return jsonify({
            "data_source": "mysql", "simulation": True,
            "storage_ready": False, "devices": [],
            "expected_devices": 2, "profile_register_addresses": 815,
            "reason": "Структуру збереження сирих регістрів ще не встановлено",
        })
    result = rows(
        "SELECT pm.slot_no,d.device_key,d.model,"
        "(SELECT COUNT(DISTINCT r.function_code,r.register_address) FROM rakurs_modbus_register_samples AS r "
        " WHERE r.device_id=d.id AND r.quality='simulated') AS raw_registers,"
        "(SELECT COUNT(DISTINCT f.field_id) FROM rakurs_modbus_field_samples AS f "
        " WHERE f.device_id=d.id AND f.quality='simulated') AS decoded_fields,"
        "(SELECT COUNT(DISTINCT s.signal_id) FROM rakurs_samples AS s "
        " WHERE s.device_id=d.id AND s.quality='simulated') AS ui_fields "
        "FROM rakurs_parallel_members AS pm "
        "JOIN rakurs_parallel_groups AS pg ON pg.id=pm.group_id "
        "JOIN rakurs_devices AS d ON d.id=pm.device_id "
        "WHERE pg.group_key=%s AND pm.member_kind='simulation' "
        "AND d.kind='simulation' ORDER BY pm.slot_no",
        ("deye_sun20k_parallel_shared_battery",),
    )
    return jsonify({
        "data_source": "mysql", "simulation": True,
        "storage_ready": True, "devices": serialize(result),
        "expected_devices": 2, "profile_register_addresses": 815,
        "physical_inverters_registered_here": 0,
        "warning": "Походження архівних значень із фізичного Deye не підтверджене",
    })


@app.get("/api/v1/parallel/simulation/latest")
def parallel_simulation_latest():
    selected = parallel_demo_selected()
    placeholders = ",".join("%s" for _ in PARALLEL_SIGNAL_KEYS)
    samples = rows(
        "SELECT s.signal_key,s.label_uk,s.unit,m.value_decimal,m.quality,"
        "m.observed_at_utc FROM rakurs_samples AS m "
        "JOIN rakurs_signals AS s ON s.id=m.signal_id "
        "WHERE m.device_id=%s AND s.signal_key IN (" + placeholders + ") "
        "AND m.quality='simulated' AND m.ingest_source=%s "
        "ORDER BY m.observed_at_utc DESC",
        (selected["id"],) + PARALLEL_SIGNAL_KEYS + (PARALLEL_DEMO_SOURCE,),
    )
    result = {}
    for row in samples:
        result.setdefault(row["signal_key"], row)
    return jsonify({
        "data_source": "mysql", "simulation": True,
        "device_key": selected["device_key"], "values": serialize(result),
        "aggregate_verified": False, "shared_battery_soc_verified": False,
    })


@app.get("/api/v1/parallel/simulation/raw")
def parallel_simulation_raw():
    selected = parallel_demo_selected()
    try:
        offset = int(request.args.get("offset", "0"))
        limit = int(request.args.get("limit", "200"))
    except ValueError:
        abort(400, description="Некоректний розмір сторінки")
    if not 0 <= offset <= 100000 or not 1 <= limit <= 500:
        abort(400, description="Допустимо 1–500 регістрів на сторінку")
    poll = one(
        "SELECT id,observed_at_utc FROM rakurs_modbus_poll_runs "
        "WHERE device_id=%s AND quality='simulated' AND ingest_source=%s "
        "ORDER BY observed_at_utc DESC LIMIT 1",
        (selected["id"], PARALLEL_DEMO_SOURCE),
    )
    if poll is None:
        return jsonify({"data_source": "mysql", "simulation": True,
                        "device_key": selected["device_key"],
                        "raw_registers": [], "total": 0})
    count = one(
        "SELECT COUNT(*) AS n FROM rakurs_modbus_register_samples "
        "WHERE poll_id=%s", (poll["id"],),
    )
    readings = rows(
        "SELECT function_code,register_address,raw_uint16,quality,"
        "observed_at_utc FROM rakurs_modbus_register_samples "
        "WHERE poll_id=%s ORDER BY function_code,register_address,frame_id "
        "LIMIT %s OFFSET %s", (poll["id"], limit, offset),
    )
    return jsonify({
        "data_source": "mysql", "simulation": True,
        "device_key": selected["device_key"], "total": count["n"],
        "offset": offset, "limit": limit, "raw_registers": serialize(readings),
        "note": "Архівні регістри; походження з фізичного Modbus не підтверджене",
    })


@app.get("/api/v1/parallel/simulation/fields")
def parallel_simulation_fields():
    """Paginated full decoded Modbus field catalogue for ONE simulated unit.

    Never substitutes demo values for physical telemetry. Read-only SELECT.
    """
    selected = parallel_demo_selected()
    try:
        offset = int(request.args.get("offset", "0"))
        limit = int(request.args.get("limit", "80"))
    except ValueError:
        abort(400, description="Некоректний розмір сторінки")
    if not 0 <= offset <= 100000 or not 1 <= limit <= 100:
        abort(400, description="Доступно 1–100 полів на сторінку")
    total = one(
        "SELECT COUNT(*) AS n FROM rakurs_modbus_field_samples AS m "
        "WHERE m.device_id=%s AND m.quality='simulated' "
        "AND m.ingest_source=%s AND m.observed_at_utc=("
        "SELECT MAX(x.observed_at_utc) FROM rakurs_modbus_field_samples AS x "
        "WHERE x.device_id=m.device_id AND x.quality='simulated' "
        "AND x.ingest_source=%s)",
        (selected["id"], PARALLEL_DEMO_SOURCE, PARALLEL_DEMO_SOURCE),
    )
    records = rows(
        "SELECT f.field_key,f.field_name,f.group_name,f.unit,"
        "m.value_decimal,m.value_text,m.observed_at_utc,m.quality "
        "FROM rakurs_modbus_field_samples AS m "
        "JOIN rakurs_modbus_fields AS f ON f.id=m.field_id "
        "WHERE m.device_id=%s AND m.quality='simulated' "
        "AND m.ingest_source=%s AND m.observed_at_utc=("
        "SELECT MAX(x.observed_at_utc) FROM rakurs_modbus_field_samples AS x "
        "WHERE x.device_id=m.device_id AND x.quality='simulated' "
        "AND x.ingest_source=%s) "
        "ORDER BY f.field_key LIMIT %s OFFSET %s",
        (selected["id"], PARALLEL_DEMO_SOURCE, PARALLEL_DEMO_SOURCE,
         limit, offset),
    )
    return jsonify({
        "data_source": "mysql", "simulation": True,
        "device_key": selected["device_key"],
        "offset": offset, "limit": limit, "total": total["n"] if total else 0,
        "fields": serialize(records),
        "warning": "Походження значень із фізичного Deye не підтверджене",
    })


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
        "data_source": "mysql", "simulation": False,
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
    data = rows(
        "SELECT m.observed_at_utc, m.value_decimal, m.quality, s.unit "
        "FROM rakurs_samples AS m "
        "JOIN rakurs_signals AS s ON s.id=m.signal_id "
        "WHERE m.device_id=%s AND s.signal_key=%s "
        ""
        "AND m.observed_at_utc >= UTC_TIMESTAMP(6) - INTERVAL %s HOUR "
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
        "simulation": False, "collector_process_confirmed": False,
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
        "data_source":"mysql","simulation":False,
        "device_key":selected["device_key"],"offset":offset,"limit":limit,
        "total":total["n"] if total else 0,"polls":serialize(polls),
    })


@app.get("/api/v1/parallel/raw")
def parallel_live_raw():
    selected, poll = parallel_live_poll()
    offset, limit = parallel_page_options(500)
    if not poll:
        return jsonify({"device_key":selected["device_key"],"poll":None,
                        "total":0,"raw_registers":[],"simulation":False})
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
    return jsonify({"device_key":selected["device_key"],"simulation":False,
                    "poll":serialize(poll),"offset":offset,"limit":limit,
                    "total":total["n"] if total else 0,
                    "raw_registers":serialize(records)})


@app.get("/api/v1/parallel/bits")
def parallel_live_bits():
    selected, poll = parallel_live_poll()
    offset, limit = parallel_page_options(500)
    if not poll:
        return jsonify({"device_key":selected["device_key"],"poll":None,
                        "total":0,"bits":[],"simulation":False})
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
    return jsonify({"device_key":selected["device_key"],"simulation":False,
                    "poll":serialize(poll),"offset":offset,"limit":limit,
                    "total":total["n"] if total else 0,"bits":serialize(records)})


@app.get("/api/v1/parallel/frames")
def parallel_live_frames():
    selected, poll = parallel_live_poll()
    offset, limit = parallel_page_options(100)
    if not poll:
        return jsonify({"device_key":selected["device_key"],"poll":None,
                        "total":0,"frames":[],"simulation":False})
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
    return jsonify({"device_key":selected["device_key"],"simulation":False,
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
        "LEFT JOIN rakurs_modbus_field_samples AS m ON m.id=("
        "SELECT ms.id FROM rakurs_modbus_field_samples AS ms "
        "WHERE ms.device_id=%s AND ms.field_id=f.id "
        "ORDER BY ms.observed_at_utc DESC,ms.id DESC LIMIT 1) "
        "ORDER BY f.id LIMIT 700",
        (selected["id"],),
    )
    return jsonify({
        "data_source": "mysql", "device_key": selected["device_key"],
        "fields": serialize(data), "count": len(data),
        "verified_only": False, "aggregation_verified": False,
    })


@app.get("/api/v1/modbus-fields")
def modbus_fields():
    selected = primary_device_id()
    device_id = selected if selected is not None else -1
    data = rows(
        "SELECT f.field_key,f.group_name,f.field_name,f.platform,"
        "f.unit,f.registers_json,f.derived,f.verified,"
        "m.value_decimal,m.value_text,m.quality,m.observed_at_utc "
        "FROM rakurs_modbus_fields AS f "
        "LEFT JOIN rakurs_modbus_field_samples AS m ON m.id=("
        "SELECT ms.id FROM rakurs_modbus_field_samples AS ms "
        "WHERE ms.device_id=%s AND ms.field_id=f.id "
        "ORDER BY ms.observed_at_utc DESC,ms.id DESC LIMIT 1) "
        "ORDER BY f.id LIMIT 700",
        (device_id,),
    )
    found = one("SELECT COUNT(*) AS n FROM rakurs_modbus_register_inventory")
    return jsonify({
        "data_source": "mysql", "count": len(data),
        "register_count": found["n"] if found else 0,
        "fields": serialize(data), "primary_device_id": selected,
        "aggregate_verified": False,
    })


# Register MySQL-backed authorization only after all read-only routes exist.
from .auth import init_auth
init_auth(app, lambda sql, params=(): one(sql, params), lambda: conn())
