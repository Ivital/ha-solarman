"""Local Rakurs password login, cookie sessions and MySQL-backed rate limits.

Standalone authorization. NO Home Assistant dependency or access.
An administrator is provisioned offline; never seed a default credential.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import stat
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import abort, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

LOG = logging.getLogger("rakurs.auth")
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")
SESSION_SECONDS = 8 * 60 * 60
LOCKOUT_ATTEMPTS = 5
LOCKOUT_SECONDS = 15 * 60
DUMMY_HASH = generate_password_hash(secrets.token_urlsafe(24), method="scrypt:32768:8:1")


def load_key() -> bytes | None:
    location = os.getenv("RAKURS_SESSION_SECRET_FILE")
    if not location:
        return None
    path = Path(location).expanduser()
    try:
        meta = path.stat()
        if not stat.S_ISREG(meta.st_mode) or (meta.st_mode & 0o077):
            raise ValueError("Session secret file must be mode 0600")
        if meta.st_uid != os.geteuid():
            raise ValueError("Session secret file has incorrect owner")
        secret = path.read_bytes().strip()
        if len(secret) < 32:
            raise ValueError("Session secret must be at least 32 bytes")
        return secret
    except (OSError, ValueError):
        LOG.error("Secure session secret missing or invalid; auth fails closed")
        return None


def _error_response():
    if request.path.startswith("/api/"):
        return jsonify({"error": "Потрібна авторизація"}), 401
    return redirect("/login", code=303)


def _csrf():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def _csrf_valid():
    original = session.get("csrf", "")
    provided = request.form.get("csrf", "")
    return bool(original) and bool(provided) and hmac.compare_digest(original, provided)


def _attempt_key(kind, value):
    # Never store attacker supplied IP or login strings unbounded in the DB.
    return hashlib.sha256((kind + ":" + value).encode("utf-8")).hexdigest()


def _client_addr():
    # Nginx ALWAYS replaces X-Real-IP with its own TCP peer and proxies
    # only to loopback:8765. Ignore arbitrary X-Forwarded-For chains.
    import ipaddress
    candidate = request.headers.get("X-Real-IP", "")
    try:
        if request.remote_addr in ("127.0.0.1", "::1") and candidate:
            return str(ipaddress.ip_address(candidate))
    except ValueError:
        pass
    return request.remote_addr or "unknown"


def init_auth(app, one, conn):
    secret = load_key()
    app.config.update(
        SECRET_KEY=secret,
        AUTH_READY=bool(secret),
        SESSION_COOKIE_NAME="rakurs_session",
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(seconds=SESSION_SECONDS),
        PREFERRED_URL_SCHEME="https",
    )

    @app.before_request
    def require_account():
        if request.endpoint == "static":
            return None
        if not app.config.get("AUTH_READY"):
            return jsonify({"error": "Rakurs не налаштовано: секрет сесії відсутній"}), 503
        if request.endpoint == "login":
            return None

        user_id = session.get("uid")
        authenticated_at = session.get("at")
        expected_version = session.get("ver")
        if not user_id or not authenticated_at or not expected_version:
            return _error_response()
        if not isinstance(authenticated_at, int) or (
            time.time() - authenticated_at > SESSION_SECONDS
            or authenticated_at > time.time() + 60
        ):
            session.clear()
            return _error_response()
        user = one(
            "SELECT id, username, role, is_active, session_version "
            "FROM rakurs_users WHERE id=%s LIMIT 1", (user_id,)
        )
        if (user is None or not user["is_active"]
                or user["session_version"] != expected_version):
            session.clear()
            return _error_response()
        g.rakurs_user = user
        return None

    def blocked(keys):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for key in keys:
            attempt = one(
                "SELECT failure_count, locked_until_utc "
                "FROM rakurs_login_attempts WHERE attempt_key=%s", (key,)
            )
            if attempt and attempt["locked_until_utc"] and attempt["locked_until_utc"] > now:
                return True
        return False

    def record_failure(keys):
        # Use database atomic upsert: throttling works with multiple workers.
        for key in keys:
            with conn().cursor() as cursor:
                cursor.execute(
                    "INSERT INTO rakurs_login_attempts "
                    "(attempt_key, failure_count, last_failed_at_utc, locked_until_utc) "
                    "VALUES (%s, 1, UTC_TIMESTAMP(6), NULL) "
                    "ON DUPLICATE KEY UPDATE "
                    "failure_count = IF(last_failed_at_utc < UTC_TIMESTAMP(6) "
                    "- INTERVAL 15 MINUTE, 1, failure_count + 1), "
                    "last_failed_at_utc = UTC_TIMESTAMP(6), "
                    "locked_until_utc = IF(failure_count >= 5, "
                    "UTC_TIMESTAMP(6) + INTERVAL 15 MINUTE, locked_until_utc)",
                    (key,),
                )

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            if session.get("uid"):
                return redirect("/", code=303)
            return render_template("login.html", csrf=_csrf(), error=None)

        if not _csrf_valid():
            abort(400)
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if len(username) > 64 or len(password) > 512:
            abort(400)

        keys = (_attempt_key("login", username.lower()),
                _attempt_key("source", _client_addr()))
        if blocked(keys):
            return render_template(
                "login.html", csrf=_csrf(),
                error="Тимчасове блокування після невдалих спроб. Спробуйте через 15 хвилин."
            ), 429

        user = None
        if USERNAME_PATTERN.fullmatch(username):
            user = one(
                "SELECT id, username, password_hash, is_active, session_version "
                "FROM rakurs_users WHERE username=%s LIMIT 1",
                (username,)
            )
        candidate_hash = user["password_hash"] if user else DUMMY_HASH
        valid = check_password_hash(candidate_hash, password)
        if not user or not user["is_active"] or not valid:
            record_failure(keys)
            LOG.warning("Invalid Rakurs login attempt")
            return render_template(
                "login.html", csrf=_csrf(), error="Неправильний логін або пароль."
            ), 401

        # Successful authentication rotates the signed browser session.
        session.clear()
        session.permanent = True
        session["uid"] = user["id"]
        session["ver"] = user["session_version"]
        session["at"] = int(time.time())
        session["csrf"] = secrets.token_urlsafe(32)
        with conn().cursor() as cursor:
            cursor.execute(
                "UPDATE rakurs_users SET last_login_at_utc=UTC_TIMESTAMP(6) WHERE id=%s",
                (user["id"],)
            )
            cursor.execute(
                "UPDATE rakurs_login_attempts "
                "SET failure_count=0, locked_until_utc=NULL "
                "WHERE attempt_key IN (%s,%s)", keys
            )
        return redirect("/", code=303)

    @app.post("/logout")
    def logout():
        if not _csrf_valid():
            abort(400)
        session.clear()
        return redirect("/login", code=303)

    @app.get("/api/v1/auth")
    def me():
        user = g.rakurs_user
        return jsonify({
            "authenticated": True,
            "username": user["username"],
            "role": user["role"],
            "csrf": _csrf(),
        })

