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
    return pymysql.connect(
        host=HOST, port=PORT, database=SCHEMA, user=user, password=_password(),
        cursorclass=pymysql.cursors.DictCursor, charset="utf8mb4",
        autocommit=True, connect_timeout=4, read_timeout=6, write_timeout=4,
        ssl_disabled=True,  # explicit trusted-LAN decision
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
    """Prevent oldest 'latest signal wins' rule from mixing parallel devices.

    The only safe combined view is a provenance-verified system aggregator.
    Individual reads remain available under /api/v1/parallel/*.
    """
    if not hasattr(g, "parallel_aggregate_blocked"):
        result = one(
            "SELECT COUNT(*) AS n FROM rakurs_devices "
            "WHERE kind='inverter' AND model LIKE %s",
            ("%" + PARALLEL_MODEL + "%",),
        )
        g.parallel_aggregate_blocked = bool(result and result["n"] >= 2)
    return g.parallel_aggregate_blocked


@app.get("/")
def index():
    keys = tuple(key for key, _, _ in HOME_METRICS)
    placeholders = ",".join(["%s"] * len(keys))
    simulated = demo_mode()
    blocked = not simulated and parallel_aggregate_requires_device_selection()
    if blocked:
        # No unapproved aggregate is exposed; do not scan the history table.
        readings = []
    elif simulated:
        readings = rows(
            "SELECT s.signal_key, s.unit, m.value_decimal, m.quality "
            "FROM rakurs_signals AS s "
            "JOIN rakurs_samples AS m ON m.signal_id=s.id "
            "JOIN rakurs_devices AS d ON d.id=m.device_id "
            "WHERE s.signal_key IN (" + placeholders + ") "
            "AND d.device_key=%s AND m.quality='simulated' "
            "AND m.ingest_source=%s "
            "ORDER BY m.observed_at_utc DESC LIMIT 16",
            keys + (DEMO_DEVICE_KEY, DEMO_INGEST_SOURCE),
        )
    else:
        readings = rows(
            "SELECT s.signal_key, s.unit, m.value_decimal, m.quality "
            "FROM rakurs_signals AS s "
            "JOIN rakurs_samples AS m ON m.signal_id=s.id "
            "WHERE s.signal_key IN (" + placeholders + ") "
            "AND s.verified=1 AND m.quality<>'simulated' "
            "AND m.observed_at_utc=("
            "SELECT MAX(mx.observed_at_utc) FROM rakurs_samples AS mx "
            "WHERE mx.signal_id=m.signal_id AND mx.quality<>'simulated') "
            "ORDER BY m.observed_at_utc DESC LIMIT 16",
            keys,
        )
    latest = {}
    for reading in readings:
        latest.setdefault(reading["signal_key"], reading)
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
    return render_template(
        "index.html", initial_cards=cards, initial_simulated=simulated,
    )


# Server-rendered Deye snapshot, independent of all browser JavaScript and
# unaffected by legacy Home Assistant's cached SPA/asset bundle.
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
    result = rows(
        "SELECT s.signal_key, s.label_uk, s.unit, m.value_decimal, "
        "m.quality, m.observed_at_utc "
        "FROM rakurs_signals AS s "
        "JOIN rakurs_samples AS m ON m.signal_id=s.id "
        "JOIN rakurs_devices AS d ON d.id=m.device_id "
        "WHERE d.device_key=%s AND m.quality='simulated' "
        "AND m.ingest_source=%s ORDER BY s.label_uk",
        (DEMO_DEVICE_KEY, DEMO_INGEST_SOURCE),
    )
    lookup = {entry["signal_key"]: entry for entry in result}
    summary = [
        {
            "label": label,
            "value": _display_decimal(lookup[key]["value_decimal"]) if key in lookup else "—",
            "unit": lookup[key]["unit"] or "" if key in lookup else "",
        }
        for key, label in DEYE_SUMMARY
    ]
    for entry in result:
        entry["display_value"] = _display_decimal(entry["value_decimal"])
    return render_template(
        "deye_server.html", section="overview", title="Огляд Deye",
        summary=summary, fields=result, count=len(result),
        timestamp=result[0]["observed_at_utc"] if result else None,
    )


@app.get("/deye/modbus")
def deye_modbus_snapshot():
    result = rows(
        "SELECT f.field_key, f.group_name, f.field_name, f.unit, "
        "f.registers_json, m.value_decimal, m.value_text, m.quality "
        "FROM rakurs_modbus_fields AS f "
        "LEFT JOIN rakurs_modbus_field_samples AS m ON m.field_id=f.id "
        "AND m.quality='simulated' AND m.ingest_source=%s "
        "AND m.device_id=(SELECT id FROM rakurs_devices WHERE device_key=%s) "
        "AND m.observed_at_utc=(SELECT MAX(ms.observed_at_utc) "
        "FROM rakurs_modbus_field_samples AS ms "
        "WHERE ms.device_id=m.device_id AND ms.field_id=f.id "
        "AND ms.quality='simulated' AND ms.ingest_source=%s) "
        "ORDER BY f.id LIMIT 700",
        (DEMO_INGEST_SOURCE, DEMO_DEVICE_KEY, DEMO_INGEST_SOURCE),
    )
    for entry in result:
        addresses = json.loads(entry["registers_json"] or "[]")
        entry["register_display"] = ", ".join(
            f"0x{int(address):04X}" for address in addresses
        ) or "Розрахунковий"
        entry["display_value"] = (
            str(entry["value_text"])
            if entry["value_text"] is not None
            else _display_decimal(entry["value_decimal"])
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
    """Select stored sample data for the normal site without editing its provenance.

    The API prefers real telemetry if it exists; otherwise show the stored
    one-shot Deye samples on the normal routes. ?real=1 forces real-only;
    ?demo=1 explicitly selects synthetic data. Results are request-scoped.
    """
    if request.args.get("real") == "1":
        return False
    if request.args.get("demo") == "1":
        return True
    if not hasattr(g, "use_simulated_samples"):
        real = one(
            "SELECT 1 AS n FROM rakurs_samples "
            "WHERE quality<>'simulated' LIMIT 1"
        )
        if real is not None:
            g.use_simulated_samples = False
        else:
            synthetic = one(
                "SELECT 1 AS n FROM rakurs_samples "
                "WHERE quality='simulated' AND ingest_source=%s LIMIT 1",
                (DEMO_INGEST_SOURCE,),
            )
            g.use_simulated_samples = synthetic is not None
    return g.use_simulated_samples


@app.get("/api/v1/status")
def status():
    if demo_mode():
        devices = one(
            "SELECT COUNT(*) AS n FROM rakurs_devices WHERE device_key=%s",
            (DEMO_DEVICE_KEY,)
        )
        signals = one(
            "SELECT COUNT(DISTINCT m.signal_id) AS n FROM rakurs_samples AS m "
            "JOIN rakurs_devices AS d ON d.id=m.device_id "
            "WHERE d.device_key=%s AND m.quality='simulated' "
            "AND m.ingest_source=%s",
            (DEMO_DEVICE_KEY, DEMO_INGEST_SOURCE)
        )
        sample_exists = one(
            "SELECT 1 AS n FROM rakurs_samples AS m "
            "JOIN rakurs_devices AS d ON d.id=m.device_id "
            "WHERE d.device_key=%s AND m.quality='simulated' "
            "AND m.ingest_source=%s LIMIT 1",
            (DEMO_DEVICE_KEY, DEMO_INGEST_SOURCE)
        )
        return jsonify({
            "data_source": "mysql", "mode": "simulation", "database": SCHEMA,
            "registered_devices": devices["n"], "verified_signals": 0,
            "simulated_signals": signals["n"],
            "displayed_signals": signals["n"],
            "has_real_samples": False,
            "has_simulated_samples": sample_exists is not None,
            "collector_active": False, "home_assistant_dependency": False,
        })
    devices = one("SELECT COUNT(*) AS n FROM rakurs_devices WHERE device_key<>%s",
                  (DEMO_DEVICE_KEY,))
    verified = one("SELECT COUNT(*) AS n FROM rakurs_signals WHERE verified=1")
    sample_exists = one(
        "SELECT 1 AS n FROM rakurs_samples WHERE quality<>'simulated' LIMIT 1"
    )
    return jsonify({
        "data_source": "mysql", "mode": "real", "database": SCHEMA,
        "mysql_host": HOST, "registered_devices": devices["n"],
        "verified_signals": verified["n"], "displayed_signals": verified["n"],
        "has_real_samples": sample_exists is not None,
        "collector_active": False, "home_assistant_dependency": False,
    })

@app.get("/api/v1/signals")
def signals():
    if demo_mode():
        result = rows(
            "SELECT DISTINCT s.signal_key, s.label_uk, s.unit "
            "FROM rakurs_signals AS s "
            "JOIN rakurs_samples AS m ON m.signal_id=s.id "
            "JOIN rakurs_devices AS d ON d.id=m.device_id "
            "WHERE d.device_key=%s AND m.quality='simulated' "
            "AND m.ingest_source=%s ORDER BY s.label_uk LIMIT 500",
            (DEMO_DEVICE_KEY, DEMO_INGEST_SOURCE)
        )
        return jsonify({"data_source": "mysql", "simulation": True,
                        "signals": serialize(result)})
    result = rows(
        "SELECT signal_key, label_uk, unit FROM rakurs_signals "
        "WHERE verified=1 ORDER BY label_uk LIMIT 500"
    )
    return jsonify({"data_source": "mysql", "signals": serialize(result)})

@app.get("/api/v1/latest")
def latest():
    value = request.args.get("keys", "")
    keys = list(dict.fromkeys(k.strip() for k in value.split(",") if k.strip()))
    if not keys or len(keys) > MAX_KEYS or any(not KEY_RE.fullmatch(k) for k in keys):
        return jsonify({"error": "Некоректний перелік сигналів"}), 400
    if not demo_mode() and parallel_aggregate_requires_device_selection():
        return jsonify({
            "data_source": "mysql", "simulation": False, "values": {},
            "aggregate_verified": False, "device_selection_required": True,
            "warning": "Виберіть конкретний інвертор: сума системи не підтверджена",
        })
    found = {}
    for key in keys:
        if demo_mode():
            result = one(
                "SELECT s.signal_key, s.label_uk, s.unit, m.value_decimal, "
                "m.raw_value, m.quality, m.observed_at_utc "
                "FROM rakurs_signals AS s "
                "JOIN rakurs_samples AS m ON m.signal_id=s.id "
                "JOIN rakurs_devices AS d ON d.id=m.device_id "
                "WHERE s.signal_key=%s AND d.device_key=%s "
                "AND m.quality='simulated' AND m.ingest_source=%s "
                "ORDER BY m.observed_at_utc DESC LIMIT 1",
                (key, DEMO_DEVICE_KEY, DEMO_INGEST_SOURCE)
            )
        else:
            result = one(
                "SELECT s.signal_key, s.label_uk, s.unit, m.value_decimal, "
                "m.raw_value, m.quality, m.observed_at_utc "
                "FROM rakurs_signals AS s JOIN rakurs_samples AS m ON m.signal_id=s.id "
                "WHERE s.signal_key=%s AND s.verified=1 "
                "AND m.quality<>'simulated' "
                "ORDER BY m.observed_at_utc DESC LIMIT 1",
                (key,),
            )
        if result is not None:
            found[key] = serialize(result)
    return jsonify({"data_source": "mysql", "simulation": demo_mode(), "values": found})

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
    if hours < 1 or hours > 168:
        return jsonify({"error": "Доступно 1–168 годин"}), 400
    if not demo_mode() and parallel_aggregate_requires_device_selection():
        return jsonify({
            "data_source": "mysql", "simulation": False, "signal": None,
            "hours": hours, "points": [], "truncated": False,
            "aggregate_verified": False, "device_selection_required": True,
        })
    if demo_mode():
        signal = one(
            "SELECT s.signal_key, s.label_uk, s.unit FROM rakurs_signals AS s "
            "JOIN rakurs_samples AS m ON m.signal_id=s.id "
            "JOIN rakurs_devices AS d ON d.id=m.device_id "
            "WHERE s.signal_key=%s AND d.device_key=%s "
            "AND m.quality='simulated' AND m.ingest_source=%s LIMIT 1",
            (key, DEMO_DEVICE_KEY, DEMO_INGEST_SOURCE)
        )
    else:
        signal = one(
            "SELECT signal_key, label_uk, unit FROM rakurs_signals "
            "WHERE signal_key=%s AND verified=1", (key,)
        )
    if signal is None:
        return jsonify({"data_source": "mysql", "signal": None,
                        "points": [], "truncated": False})
    if demo_mode():
        history_rows = rows(
            "SELECT m.observed_at_utc, m.received_at_utc, m.value_decimal, "
            "m.raw_value, m.quality FROM rakurs_samples AS m "
            "JOIN rakurs_signals AS s ON s.id=m.signal_id "
            "JOIN rakurs_devices AS d ON d.id=m.device_id "
            "WHERE s.signal_key=%s AND d.device_key=%s "
            "AND m.quality='simulated' AND m.ingest_source=%s "
            "AND m.observed_at_utc >= UTC_TIMESTAMP(6) - INTERVAL %s HOUR "
            "ORDER BY m.observed_at_utc DESC LIMIT %s",
            (key, DEMO_DEVICE_KEY, DEMO_INGEST_SOURCE, hours, MAX_HISTORY + 1)
        )
    else:
        history_rows = rows(
            "SELECT m.observed_at_utc, m.received_at_utc, m.value_decimal, "
            "m.raw_value, m.quality FROM rakurs_samples AS m "
            "JOIN rakurs_signals AS s ON s.id=m.signal_id "
            "WHERE s.signal_key=%s AND s.verified=1 "
            "AND m.quality<>'simulated' "
            "AND m.observed_at_utc >= UTC_TIMESTAMP(6) - INTERVAL %s HOUR "
            "ORDER BY m.observed_at_utc DESC LIMIT %s",
            (key, hours, MAX_HISTORY + 1)
        )
    truncated = len(history_rows) > MAX_HISTORY
    history_rows = history_rows[:MAX_HISTORY]
    history_rows.reverse()
    return jsonify({
        "data_source": "mysql", "simulation": demo_mode(),
        "signal": signal, "hours": hours,
        "points": serialize(history_rows), "truncated": truncated,
    })

@app.get("/api/v1/events")
def events():
    qualifier = "=" if demo_mode() else "<>"
    result = rows(
        "SELECT e.observed_at_utc, e.severity, e.event_code, e.message, "
        "d.device_key FROM rakurs_device_events AS e "
        "JOIN rakurs_devices AS d ON d.id=e.device_id "
        f"WHERE d.device_key{qualifier}%s "
        "ORDER BY e.observed_at_utc DESC LIMIT 50",
        (DEMO_DEVICE_KEY,)
    )
    return jsonify({"data_source": "mysql", "simulation": demo_mode(),
                    "events": serialize(result)})

@app.get("/api/v1/devices")
def devices():
    qualifier = "=" if demo_mode() else "<>"
    result = rows(
        "SELECT device_key, kind, model, commissioned_at_utc "
        f"FROM rakurs_devices WHERE device_key{qualifier}%s "
        "ORDER BY device_key LIMIT 100",
        (DEMO_DEVICE_KEY,)
    )
    return jsonify({"data_source": "mysql", "simulation": demo_mode(),
                    "devices": serialize(result)})

@app.get("/api/v1/batches")
def batches():
    qualifier = "=" if demo_mode() else "<>"
    result = rows(
        "SELECT b.started_at_utc, b.finished_at_utc, b.received_count, "
        "b.accepted_count, b.rejected_count, b.status, b.error_code, "
        "d.device_key FROM rakurs_ingest_batches AS b "
        "JOIN rakurs_devices AS d ON d.id=b.device_id "
        f"WHERE d.device_key{qualifier}%s "
        "ORDER BY b.started_at_utc DESC LIMIT 50",
        (DEMO_DEVICE_KEY,)
    )
    return jsonify({"data_source": "mysql", "simulation": demo_mode(),
                    "batches": serialize(result)})

# Parallel group: observation only. Do not infer physical wiring from Modbus
# addresses, and do not sum values across devices until provenance is verified.
def parallel_registered_devices():
    return rows(
        "SELECT d.id,d.device_key,d.kind,d.model,d.serial_number,"
        "pm.slot_no,pm.parallel_role,pm.modbus_address,"
        "pm.member_verified,pm.transport_verified "
        "FROM rakurs_devices AS d "
        "JOIN rakurs_parallel_members AS pm ON pm.device_id=d.id "
        "JOIN rakurs_parallel_groups AS pg ON pg.id=pm.group_id "
        "WHERE d.device_key<>%s AND d.kind='inverter' AND d.model LIKE %s "
        "AND pm.member_kind='physical' AND pg.group_key=%s "
        "ORDER BY pm.slot_no LIMIT 20",
        (DEMO_DEVICE_KEY, "%" + PARALLEL_MODEL + "%",
         "deye_sun20k_parallel_shared_battery"),
    )


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
        abort(404, description="Невідомий тестовий інвертор")
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
        abort(404, description="Тестовий інвертор не завантажено")
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
        "(SELECT COUNT(*) FROM rakurs_modbus_register_samples AS r "
        " WHERE r.device_id=d.id AND r.quality='simulated') AS raw_registers,"
        "(SELECT COUNT(*) FROM rakurs_modbus_field_samples AS f "
        " WHERE f.device_id=d.id AND f.quality='simulated') AS decoded_fields,"
        "(SELECT COUNT(*) FROM rakurs_samples AS s "
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
        "warning": "Значення вигадані для тестування і не є показниками Deye",
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
        "note": "Синтетичні тестові слова, НЕ фактичний обмін Modbus",
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
        "AND m.ingest_source=%s",
        (selected["id"], PARALLEL_DEMO_SOURCE),
    )
    records = rows(
        "SELECT f.field_key,f.field_name,f.group_name,f.unit,"
        "m.value_decimal,m.value_text,m.observed_at_utc,m.quality "
        "FROM rakurs_modbus_field_samples AS m "
        "JOIN rakurs_modbus_fields AS f ON f.id=m.field_id "
        "WHERE m.device_id=%s AND m.quality='simulated' "
        "AND m.ingest_source=%s "
        "ORDER BY f.field_key,m.observed_at_utc DESC LIMIT %s OFFSET %s",
        (selected["id"], PARALLEL_DEMO_SOURCE, limit, offset),
    )
    return jsonify({
        "data_source": "mysql", "simulation": True,
        "device_key": selected["device_key"],
        "offset": offset, "limit": limit, "total": total["n"] if total else 0,
        "fields": serialize(records),
        "warning": "Значення є тестовими та не походять від фізичного Deye",
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
        "AND s.verified=1 AND m.quality<>'simulated' "
        "AND m.observed_at_utc=("
        "SELECT MAX(mx.observed_at_utc) FROM rakurs_samples AS mx "
        "WHERE mx.device_id=m.device_id AND mx.signal_id=m.signal_id "
        "AND mx.quality<>'simulated') "
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
        "AND s.verified=1 AND m.quality<>'simulated' "
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


@app.get("/api/v1/parallel/modbus-fields")
def parallel_device_modbus():
    selected = parallel_selected_device()
    data = rows(
        "SELECT f.field_key, f.group_name, f.field_name, f.unit, "
        "f.registers_json, m.value_decimal, m.value_text, "
        "m.quality, m.observed_at_utc "
        "FROM rakurs_modbus_fields AS f "
        "LEFT JOIN rakurs_modbus_field_samples AS m "
        "ON m.field_id=f.id AND m.device_id=%s "
        "AND m.quality<>'simulated' "
        "AND m.observed_at_utc=("
        "SELECT MAX(ms.observed_at_utc) "
        "FROM rakurs_modbus_field_samples AS ms "
        "WHERE ms.device_id=%s AND ms.field_id=f.id "
        "AND ms.quality<>'simulated') "
        "WHERE f.verified=1 ORDER BY f.id LIMIT 700",
        (selected["id"], selected["id"]),
    )
    return jsonify({
        "data_source": "mysql", "device_key": selected["device_key"],
        "fields": serialize(data), "count": len(data),
        "verified_only": True, "aggregation_verified": False,
    })


@app.get("/api/v1/modbus-fields")
def modbus_fields():
    if not demo_mode() and parallel_aggregate_requires_device_selection():
        return jsonify({
            "data_source": "mysql", "simulation": False,
            "fields": [], "register_count": 0,
            "aggregate_verified": False, "device_selection_required": True,
        })
    if demo_mode():
        sample_join = (
            "LEFT JOIN rakurs_modbus_field_samples AS m ON m.field_id=f.id "
            "AND m.quality='simulated' AND m.ingest_source=%s "
            "AND m.device_id=(SELECT id FROM rakurs_devices WHERE device_key=%s) "
            "AND m.observed_at_utc=(SELECT MAX(ms.observed_at_utc) "
            "FROM rakurs_modbus_field_samples AS ms "
            "WHERE ms.device_id=m.device_id AND ms.field_id=f.id "
            "AND ms.quality='simulated' AND ms.ingest_source=%s)"
        )
        params = (DEMO_INGEST_SOURCE, DEMO_DEVICE_KEY, DEMO_INGEST_SOURCE)
    else:
        sample_join = (
            "LEFT JOIN rakurs_modbus_field_samples AS m ON m.field_id=f.id "
            "AND m.quality<>'simulated' "
            "AND m.observed_at_utc=(SELECT MAX(ms.observed_at_utc) "
            "FROM rakurs_modbus_field_samples AS ms "
            "WHERE ms.device_id=m.device_id AND ms.field_id=f.id "
            "AND ms.quality<>'simulated')"
        )
        params = ()
    data = rows(
        "SELECT f.field_key, f.group_name, f.field_name, f.platform, "
        "f.unit, f.registers_json, f.derived, f.verified, "
        "m.value_decimal, m.value_text, m.quality, m.observed_at_utc "
        "FROM rakurs_modbus_fields AS f " + sample_join +
        " ORDER BY f.id LIMIT 700",
        params
    )
    reg_count = one("SELECT COUNT(*) AS n FROM rakurs_modbus_register_inventory")
    return jsonify({
        "data_source": "mysql", "simulation": demo_mode(),
        "count": len(data), "register_count": reg_count["n"],
        "fields": serialize(data),
    })


# Register MySQL-backed authorization only after all read-only routes exist.
from .auth import init_auth
init_auth(app, lambda sql, params=(): one(sql, params), lambda: conn())
