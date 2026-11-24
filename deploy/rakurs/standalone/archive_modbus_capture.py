#!/usr/bin/env python3
"""Ingest an existing local read-only Modbus capture JSON, never poll hardware.

The future collector must supply *all* frames and their complete request/
response bytes, raw register words (FC03/04), bits (FC01/02), decoded fields
and signal aliases. Only verified physical group members can write live
quality. No RS485 socket/network client exists in this script.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys

from modbus_archive import normalize, persist

HOST = "192.168.5.30"
SCHEMA = "rakurs"


def load_capture(path: Path):
    meta = path.stat()
    if not stat.S_ISREG(meta.st_mode) or meta.st_size > 16 * 1024 * 1024:
        raise RuntimeError("Capture must be regular JSON file <=16 MiB")
    if meta.st_mode & 0o022:
        raise RuntimeError("Capture file must not be group/other writable")
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("capture", type=Path)
    p.add_argument("--apply", action="store_true",
                   help="only for provisioned live collector with approved wiring")
    opts = p.parse_args()
    item = normalize(load_capture(opts.capture))
    print("CAPTURE_DEVICE=" + item["device_key"])
    print("CAPTURE_FRAMES=" + str(len(item["frames"])))
    print("CAPTURE_RAW_WORDS=" + str(sum(len(x["words"]) for x in item["frames"])))
    print("CAPTURE_RAW_BITS=" + str(sum(len(x["bits"]) for x in item["frames"])))
    print("CAPTURE_DECODED_FIELDS=" + str(len(item["fields"])))
    print("CAPTURE_DASHBOARD_SIGNALS=" + str(len(item["signals"])))
    print("MODBUS_COMMANDS_SENT=0")
    if not opts.apply:
        print("CAPTURE_VALIDATION=PASS MYSQL_WRITES=0")
        return 0
    if item["quality"] == "simulated":
        raise RuntimeError("Use the dedicated twin-device simulation seed script")
    if os.environ.get("RAKURS_CAPTURE_APPLY_ACK") != "VERIFIED_READ_ONLY_COLLECTOR":
        raise RuntimeError("STOP: physical transport and collector not commissioned")
    name = os.environ.get("RAKURS_INGEST_DB_USER", "")
    secret = os.environ.get("RAKURS_INGEST_PASSWORD_FILE", "")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,32}", name) or not secret:
        raise RuntimeError("STOP: dedicated ingest account must be configured")
    path = Path(secret)
    meta = path.stat()
    if not stat.S_ISREG(meta.st_mode) or meta.st_mode & 0o077:
        raise RuntimeError("STOP: unsafe ingest password file")
    if meta.st_uid != os.geteuid():
        raise RuntimeError("STOP: secret owner does not match collector UID")
    import pymysql
    db = pymysql.connect(
        host=HOST, port=3306, database=SCHEMA, user=name,
        password=path.read_text(encoding="utf-8").rstrip("\r\n"),
        ssl_disabled=True, autocommit=False, charset="utf8mb4",
        connect_timeout=6, read_timeout=30, write_timeout=30,
    )
    try:
        receipt = persist(db, load_capture(opts.capture))
        print(json.dumps(receipt, sort_keys=True))
    finally:
        db.close()
    print("MODBUS_CAPTURE_ARCHIVED=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
