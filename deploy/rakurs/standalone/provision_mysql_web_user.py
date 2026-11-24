#!/usr/bin/env python3
"""Provision ONLY rakurs_web@192.168.5.31 and local private password file.

Run with sudo after staging the standalone service; no secrets in Git,
arguments, stdout, shell history, or Home Assistant.
"""
from __future__ import annotations
import getpass
import os
from pathlib import Path
import pwd
import secrets
import sys

import pymysql

SECRET = Path("/etc/rakurs/mysql_password")


def main():
    if os.geteuid() != 0 or not sys.stdin.isatty():
        print("STOP: sudo and interactive TTY required")
        return 2
    try:
        service = pwd.getpwnam("rakurs")
    except KeyError:
        print("STOP: first install standalone service account rakurs")
        return 2
    if SECRET.exists() or SECRET.is_symlink():
        print("STOP: password file exists, no overwrite")
        return 2
    if not SECRET.parent.is_dir() or SECRET.parent.is_symlink():
        print("STOP: expected private /etc/rakurs directory missing")
        return 2

    print("Creates MySQL user rakurs_web@192.168.5.31 with least privileges.")
    if input("Type PROVISION-RAKURS to proceed: ").strip() != "PROVISION-RAKURS":
        print("ABORTED")
        return 2
    admin_pass = getpass.getpass("MySQL Ivital password (hidden): ")
    try:
        admin = pymysql.connect(
            host="192.168.5.30", port=3306, database="rakurs",
            user="Ivital", password=admin_pass, ssl_disabled=True,
            autocommit=True, connect_timeout=7, read_timeout=8,
            cursorclass=pymysql.cursors.DictCursor,
        )
    except pymysql.MySQLError as error:
        print("STOP: cannot connect as MySQL owner:", type(error).__name__)
        return 2
    finally:
        del admin_pass
    private_password = secrets.token_urlsafe(44)
    created = False
    try:
        with admin.cursor() as c:
            c.execute("SELECT COUNT(*) AS n FROM rakurs_users")
            c.fetchone()
            c.execute(
                "CREATE USER 'rakurs_web'@'192.168.5.31' IDENTIFIED BY %s",
                (private_password,),
            )
            created = True
            c.execute("GRANT SELECT ON rakurs.* TO 'rakurs_web'@'192.168.5.31'")
            c.execute(
                "GRANT INSERT,UPDATE ON rakurs.rakurs_login_attempts "
                "TO 'rakurs_web'@'192.168.5.31'"
            )
            c.execute(
                "GRANT UPDATE (last_login_at_utc) ON rakurs.rakurs_users "
                "TO 'rakurs_web'@'192.168.5.31'"
            )
        test = pymysql.connect(
            host="192.168.5.30", port=3306, database="rakurs",
            user="rakurs_web", password=private_password, ssl_disabled=True,
            autocommit=True, connect_timeout=7, read_timeout=8,
            cursorclass=pymysql.cursors.DictCursor,
        )
        try:
            with test.cursor() as c:
                c.execute("SELECT COUNT(*) AS n FROM rakurs_users")
                c.fetchone()
        finally:
            test.close()
        fd = os.open(SECRET, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.fchown(fd, service.pw_uid, service.pw_gid)
            os.write(fd, (private_password + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        print("RAKURS_WEB_MYSQL_ACCOUNT=PASS")
        print("PRIVATE_SECRET_FILE=PASS (owner=rakurs, mode=0600)")
        print("READ_ONLY_TELEMETRY=PASS; AUTH_TABLE_WRITES=LIMITED")
        return 0
    except (pymysql.MySQLError, OSError) as error:
        print("STOP: provisioning failed:", type(error).__name__)
        if created:
            print("Web user was created; inspect MySQL grants before retrying.")
        return 2
    finally:
        admin.close()


if __name__ == "__main__":
    raise SystemExit(main())
