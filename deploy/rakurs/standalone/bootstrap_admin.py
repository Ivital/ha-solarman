#!/usr/bin/env python3
"""One-time RAKURS admin account bootstrap; password never shown/stored in script.

Requires pre-created auth tables and PyMySQL + Werkzeug installed.
Uses Ivital DB account ONLY for provisioning, NOT in the web service.
"""
from __future__ import annotations

import getpass
import re
import sys

import pymysql
from mysql_transport import mysql_tls_options
from werkzeug.security import generate_password_hash

HOST, DATABASE, DATABASE_USER = "192.168.5.30", "rakurs", "Ivital"
NAME = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")


def main():
    if not sys.stdin.isatty():
        print("STOP: interactive TTY required", file=sys.stderr)
        return 2
    username = input("Web admin login (3–64 ASCII letters/numbers/._-): ").strip()
    if not NAME.fullmatch(username):
        print("STOP: invalid login name", file=sys.stderr)
        return 2

    mysql_password = getpass.getpass("MySQL provisioning account password (hidden): ")
    try:
        db = pymysql.connect(
            host=HOST, port=3306, database=DATABASE, user=DATABASE_USER,
            password=mysql_password, **mysql_tls_options(require_verified=True),
            autocommit=False,
            connect_timeout=6, read_timeout=7,
            cursorclass=pymysql.cursors.DictCursor,
        )
    except pymysql.MySQLError as exc:
        print("STOP: cannot connect to MySQL:", type(exc).__name__, file=sys.stderr)
        return 2
    finally:
        del mysql_password
    try:
        with db.cursor() as c:
            c.execute("SELECT COUNT(*) AS n FROM rakurs_users")
            n = c.fetchone()["n"]
            if n:
                print("STOP: users already exist; bootstrap must not overwrite users")
                return 2
            c.execute("SELECT COUNT(*) AS n FROM rakurs_login_attempts")
            if c.fetchone()["n"]:
                print("STOP: existing login attempts; inspect before bootstrap")
                return 2

        password = getpass.getpass("NEW Rakurs web admin password (hidden, 16+ chars): ")
        repeat = getpass.getpass("Repeat NEW Rakurs web password (hidden): ")
        if len(password) < 16 or len(password) > 512 or password != repeat:
            print("STOP: password too short, too long or does not match", file=sys.stderr)
            return 2
        if username.lower() in password.lower():
            print("STOP: password must not contain login name", file=sys.stderr)
            return 2

        password_hash = generate_password_hash(password, method="scrypt:32768:8:1")
        del password, repeat
        with db.cursor() as c:
            c.execute(
                "INSERT INTO rakurs_users "
                "(username,password_hash,role,is_active,session_version,created_at_utc) "
                "VALUES (%s,%s,'admin',1,1,UTC_TIMESTAMP(6))",
                (username, password_hash)
            )
        db.commit()
        print("RAKURS_WEB_ADMIN_CREATED=PASS; login=" + username)
        print("No password or MySQL credential was printed.")
        return 0
    except pymysql.MySQLError as exc:
        db.rollback()
        print("RAKURS_ADMIN_BOOTSTRAP_FAILED:", type(exc).__name__, file=sys.stderr)
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
