#!/usr/bin/env python3
"""One-time least-privilege migration for an EXISTING Rakurs web SQL account.

No password in argv, log or Git. No operation with --check (default).
Requires verified TLS for any remote credential and explicit approval.
Requires a database backup/isolated restore rehearsal before --apply.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from mysql_transport import mysql_tls_options

ACCOUNT = "'rakurs_web'@'192.168.5.31'"
HOST = "192.168.5.30"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    statements = [
        f"REVOKE UPDATE ON rakurs.rakurs_users FROM {ACCOUNT}",
        f"GRANT UPDATE (last_login_at_utc) ON rakurs.rakurs_users TO {ACCOUNT}",
    ]
    if not args.apply:
        print("TARGET=rakurs_web@192.168.5.31")
        print("READ_ONLY_PLAN=REVOKE_TABLE_UPDATE_THEN_GRANT_SINGLE_COLUMN_UPDATE")
        print("MYSQL_WRITES=0")
        return 0
    if not sys.stdin.isatty() or os.environ.get(
        "RAKURS_ISOLATED_RESTORE_VERIFIED"
    ) != "YES":
        raise RuntimeError(
            "STOP: TTY and verified isolated MySQL restore rehearsal required"
        )
    tls = mysql_tls_options(require_verified=True)
    if input("Type EXACTLY HARDEN-RAKURS-GRANTS: ").strip() != (
        "HARDEN-RAKURS-GRANTS"
    ):
        raise RuntimeError("ABORTED")
    password = getpass.getpass("Rakurs provisioning MySQL password (hidden): ")
    import pymysql
    db = pymysql.connect(
        host=HOST, port=3306, database="rakurs", user="Ivital",
        password=password, autocommit=True, connect_timeout=6, read_timeout=10,
        cursorclass=pymysql.cursors.DictCursor, **tls
    )
    del password
    try:
        with db.cursor() as cur:
            # Check both statements are relevant before making the first
            # nontransactional GRANT/REVOKE change.
            cur.execute(f"SHOW GRANTS FOR {ACCOUNT}")
            before = [str(value) for row in cur.fetchall() for value in row.values()]
            if not any("UPDATE" in grant and "rakurs_users" in grant
                       for grant in before):
                raise RuntimeError("STOP: expected existing table UPDATE grant missing")
            for statement in statements:
                cur.execute(statement)
            cur.execute(f"SHOW GRANTS FOR {ACCOUNT}")
            after = [str(value) for row in cur.fetchall() for value in row.values()]
        if any("UPDATE ON" in grant and "rakurs_users" in grant
               and "last_login_at_utc" not in grant for grant in after):
            raise RuntimeError("STOP: broad user table UPDATE still present")
        print("RAKURS_WEB_COLUMN_PRIVILEGES=PASS")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
