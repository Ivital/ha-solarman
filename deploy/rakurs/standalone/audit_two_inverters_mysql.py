#!/usr/bin/env python3
"""Audit actual twin-Deye MySQL archive: SELECT only, do not trust UI claims."""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path

HOST = "192.168.5.30"
SCHEMA = "rakurs"
DEVICE_KEYS = ("sim_deye20k_parallel_1", "sim_deye20k_parallel_2")
REQUIRED = (
    "rakurs_parallel_groups", "rakurs_parallel_members",
    "rakurs_modbus_poll_runs", "rakurs_modbus_frames",
    "rakurs_modbus_register_samples", "rakurs_modbus_bit_samples",
)
EXPECTED = (815, 624, 316)


def audit(db):
    with db.cursor() as c:
        c.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema=%s AND table_name IN (" +
            ",".join(["%s"] * len(REQUIRED)) + ")",
            (SCHEMA,) + REQUIRED,
        )
        found = {row[0] for row in c.fetchall()}
        missing = sorted(set(REQUIRED) - found)
        if missing:
            print("PARALLEL_RAW_ARCHIVE_SCHEMA=NOT_INSTALLED")
            print("MISSING_TABLES=" + ",".join(missing))
            return False
        print("PARALLEL_RAW_ARCHIVE_SCHEMA=PASS")
        c.execute(
            "SELECT id,device_key,kind,model FROM rakurs_devices "
            "WHERE device_key IN (%s,%s) ORDER BY device_key",
            DEVICE_KEYS,
        )
        devices = c.fetchall()
        print("SIMULATED_TWIN_DEVICES=" + str(len(devices)))
        c.execute(
            "SELECT COUNT(*) FROM rakurs_parallel_groups "
            "WHERE group_key=%s AND expected_inverters=2 "
            "AND battery_mode='shared' AND topology_status='unverified'",
            ("deye_sun20k_parallel_shared_battery",),
        )
        planned = c.fetchone()[0]
        print("PLANNED_SHARED_BATTERY_GROUP=" + str(planned))
        if len(devices) != 2 or planned != 1:
            return False
        for _id, key, kind, model in devices:
            if kind != "simulation" or model != "SUN-20K-SG05LP3-EU-SM2":
                raise RuntimeError("Unsafe physical/simulation identity: " + key)
            c.execute(
                "SELECT COUNT(*) FROM rakurs_modbus_register_samples "
                "WHERE device_id=%s AND quality='simulated'", (_id,),
            )
            raw = c.fetchone()[0]
            c.execute(
                "SELECT COUNT(DISTINCT register_address) FROM "
                "rakurs_modbus_register_samples WHERE device_id=%s", (_id,),
            )
            unique = c.fetchone()[0]
            c.execute(
                "SELECT COUNT(*) FROM rakurs_modbus_field_samples "
                "WHERE device_id=%s AND quality='simulated'", (_id,),
            )
            decoded = c.fetchone()[0]
            c.execute(
                "SELECT COUNT(*) FROM rakurs_samples "
                "WHERE device_id=%s AND quality='simulated'", (_id,),
            )
            ui = c.fetchone()[0]
            c.execute(
                "SELECT COUNT(*) FROM rakurs_modbus_frames "
                "WHERE device_id=%s AND status='simulated'", (_id,),
            )
            frames = c.fetchone()[0]
            c.execute(
                "SELECT COUNT(*) FROM rakurs_modbus_poll_runs "
                "WHERE device_id=%s AND status='simulated'", (_id,),
            )
            polls = c.fetchone()[0]
            print(f"{key}_RAW={raw} UNIQUE_ADDRESSES={unique}"
                  f" DECODED={decoded} UI={ui} FRAMES={frames} POLLS={polls}")
            if (raw, unique, decoded, ui) != (*EXPECTED[:1], EXPECTED[0], *EXPECTED[1:]):
                raise RuntimeError("Incomplete two-inverter archive for " + key)
            if polls != 1 or frames < 1:
                raise RuntimeError("Missing Modbus poll/frame archive for " + key)
        c.execute(
            "SELECT COUNT(*) FROM rakurs_parallel_members AS m "
            "JOIN rakurs_devices AS d ON d.id=m.device_id "
            "WHERE m.member_kind='simulation' AND d.kind='simulation' "
            "AND m.modbus_address IS NULL AND m.parallel_role='unverified' "
            "AND m.transport_verified=0 AND m.member_verified=0 "
            "AND d.device_key IN (%s,%s)",
            DEVICE_KEYS,
        )
        assert c.fetchone()[0] == 2, "Missing separate unverified simulation members"
        print("TWIN_MODBUS_COVERAGE=PASS")
        print("SHARED_SOC_SOURCE=UNVERIFIED")
        print("REAL_MODBUS_FRAMES=NOT_CLAIMED")
        return True


def main():
    import pymysql
    name = os.environ.get("RAKURS_DB_USER", "rakurs_web")
    secret = Path(os.environ.get("RAKURS_DB_PASSWORD_FILE",
                                 "/etc/rakurs/mysql_password"))
    meta = secret.stat()
    if not stat.S_ISREG(meta.st_mode) or meta.st_mode & 0o077:
        raise RuntimeError("Unsafe password file permissions")
    if not re.fullmatch(r"[a-zA-Z0-9_]{1,32}", name):
        raise RuntimeError("Invalid MySQL username")
    db = pymysql.connect(
        host=HOST, database=SCHEMA, user=name, password=secret.read_text().strip(),
        ssl_disabled=True, charset="utf8mb4",
        connect_timeout=5, read_timeout=15, autocommit=True,
    )
    try:
        complete = audit(db)
    finally:
        db.close()
    print("AUDIT_READ_ONLY=PASS MYSQL_WRITES=0")
    print("TWIN_DEMO_ARCHIVE=" + ("COMPLETE" if complete else "NOT_READY"))
    return 0 if complete else 3


if __name__ == "__main__":
    raise SystemExit(main())
