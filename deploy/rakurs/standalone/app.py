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


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/v1/catalog")
def catalog():
    # Unverified planned catalog, NOT actual measured values.
    with CATALOG_FILE.open("r", encoding="utf-8") as source:
        return jsonify(json.load(source))


@app.get("/api/v1/status")
def status():
    devices = one("SELECT COUNT(*) AS n FROM rakurs_devices")
    verified = one("SELECT COUNT(*) AS n FROM rakurs_signals WHERE verified=1")
    sample_exists = one("SELECT 1 AS n FROM rakurs_samples LIMIT 1")
    return jsonify({
        "data_source": "mysql", "database": SCHEMA, "mysql_host": HOST,
        "registered_devices": devices["n"],
        "verified_signals": verified["n"],
        "has_real_samples": sample_exists is not None,
        "collector_active": False,
        "home_assistant_dependency": False,
    })


@app.get("/api/v1/signals")
def signals():
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
    found = {}
    for key in keys:
        result = one(
            "SELECT s.signal_key, s.label_uk, s.unit, m.value_decimal, "
            "m.raw_value, m.quality, m.observed_at_utc "
            "FROM rakurs_signals AS s JOIN rakurs_samples AS m ON m.signal_id=s.id "
            "WHERE s.signal_key=%s AND s.verified=1 "
            "ORDER BY m.observed_at_utc DESC LIMIT 1",
            (key,),
        )
        if result is not None:
            found[key] = serialize(result)
    return jsonify({"data_source": "mysql", "values": found})


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
    signal = one(
        "SELECT signal_key, label_uk, unit FROM rakurs_signals "
        "WHERE signal_key=%s AND verified=1", (key,),
    )
    if signal is None:
        return jsonify({"data_source": "mysql", "signal": None,
                        "points": [], "truncated": False})
    history_rows = rows(
        "SELECT m.observed_at_utc, m.received_at_utc, m.value_decimal, "
        "m.raw_value, m.quality FROM rakurs_samples AS m "
        "JOIN rakurs_signals AS s ON s.id=m.signal_id "
        "WHERE s.signal_key=%s AND s.verified=1 "
        "AND m.observed_at_utc >= UTC_TIMESTAMP(6) - INTERVAL %s HOUR "
        "ORDER BY m.observed_at_utc DESC LIMIT %s",
        (key, hours, MAX_HISTORY + 1),
    )
    truncated = len(history_rows) > MAX_HISTORY
    history_rows = history_rows[:MAX_HISTORY]
    history_rows.reverse()
    return jsonify({
        "data_source": "mysql", "signal": signal, "hours": hours,
        "points": serialize(history_rows), "truncated": truncated,
    })


@app.get("/api/v1/events")
def events():
    result = rows(
        "SELECT e.observed_at_utc, e.severity, e.event_code, e.message, "
        "d.device_key FROM rakurs_device_events AS e "
        "JOIN rakurs_devices AS d ON d.id=e.device_id "
        "ORDER BY e.observed_at_utc DESC LIMIT 50"
    )
    return jsonify({"data_source": "mysql", "events": serialize(result)})


@app.get("/api/v1/devices")
def devices():
    result = rows(
        "SELECT device_key, kind, model, commissioned_at_utc "
        "FROM rakurs_devices ORDER BY device_key LIMIT 100"
    )
    return jsonify({"data_source": "mysql", "devices": serialize(result)})


@app.get("/api/v1/batches")
def batches():
    result = rows(
        "SELECT b.started_at_utc, b.finished_at_utc, b.received_count, "
        "b.accepted_count, b.rejected_count, b.status, b.error_code, "
        "d.device_key FROM rakurs_ingest_batches AS b "
        "JOIN rakurs_devices AS d ON d.id=b.device_id "
        "ORDER BY b.started_at_utc DESC LIMIT 50"
    )
    return jsonify({"data_source": "mysql", "batches": serialize(result)})


# Register MySQL-backed authorization only after all read-only routes exist.
from .auth import init_auth
init_auth(app, lambda sql, params=(): one(sql, params), lambda: conn())
