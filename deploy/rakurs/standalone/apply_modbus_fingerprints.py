#!/usr/bin/env python3
"""Safely stage the additive real-Modbus poll fingerprint table.

--check is fully offline; --apply is explicitly gated on an independently
verified database restore, verified MySQL TLS and interactive approval.
No existing telemetry rows or columns are changed.
"""
from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path
import re
import sys
from mysql_transport import mysql_tls_options

SQL_FILE = Path(__file__).with_name("modbus_poll_fingerprints.sql")


def approved_statement():
    sql = "\n".join(
        line for line in SQL_FILE.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("--")
    ).strip()
    if not re.fullmatch(
        r"CREATE TABLE IF NOT EXISTS rakurs_modbus_poll_fingerprints\s*"
        r"\([\s\S]*\) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 "
        r"COLLATE=utf8mb4_unicode_ci;", sql,
        flags=re.IGNORECASE,
    ):
        raise RuntimeError("STOP: SQL is not the approved additive table DDL")
    if re.search(r"\b(DROP|ALTER|DELETE|UPDATE|REPLACE|TRUNCATE|GRANT|REVOKE)\b",
                 sql, flags=re.IGNORECASE):
        raise RuntimeError("STOP: potentially destructive SQL")
    return sql[:-1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    ddl = approved_statement()
    print("FINGERPRINT_SCHEMA_PREFLIGHT=PASS")
    if not args.apply:
        print("DRY_RUN=PASS MYSQL_WRITES=0")
        return 0
    if not sys.stdin.isatty() or os.environ.get(
        "RAKURS_ISOLATED_RESTORE_VERIFIED"
    ) != "YES":
        raise RuntimeError("STOP: isolated restore rehearsal must be verified")
    tls = mysql_tls_options(require_verified=True)
    if input("Type EXACTLY APPLY-RAKURS-FINGERPRINTS: ").strip() != (
        "APPLY-RAKURS-FINGERPRINTS"
    ):
        raise RuntimeError("ABORTED")
    pw = getpass.getpass("MySQL owner password (hidden): ")
    import pymysql
    db = pymysql.connect(
        host="192.168.5.30", port=3306, database="rakurs", user="Ivital",
        password=pw, autocommit=True, connect_timeout=6, read_timeout=10,
        cursorclass=pymysql.cursors.DictCursor, **tls,
    )
    del pw
    try:
        with db.cursor() as cur:
            cur.execute(ddl)
            cur.execute(
                "SELECT COUNT(*) AS n FROM information_schema.tables "
                "WHERE table_schema='rakurs' AND "
                "table_name='rakurs_modbus_poll_fingerprints'"
            )
            if cur.fetchone()["n"] != 1:
                raise RuntimeError("STOP: fingerprint table not present")
        print("MODBUS_FINGERPRINT_TABLE=PASS")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
