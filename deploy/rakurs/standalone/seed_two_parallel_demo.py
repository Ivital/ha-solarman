#!/usr/bin/env python3
"""Add TWO independent simulated Deye-20K archives, never pretend to poll hardware.

Each synthetic device gets every 815 source-catalog register word, all 624
decoded-field placeholders, 316 dashboard aliases and full SQL provenance.
No Modbus network commands. Existing telemetry is never updated or deleted.
Run offline --check first; --execute requires explicit phrase and a compressed
logical backup before any CREATE TABLE or INSERT.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import getpass
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from modbus_archive import persist
from simulate_deye_once import _demo_value, prepare

HOST = "192.168.5.30"
DATABASE = "rakurs"
ADMIN_USER = "Ivital"
MODEL = "SUN-20K-SG05LP3-EU-SM2"
GROUP_KEY = "deye_sun20k_parallel_shared_battery"
SOURCE = "rakurs_parallel_sim_seed_v1"
DEVICE_KEYS = ("sim_deye20k_parallel_1", "sim_deye20k_parallel_2")
POLL_UIDS = ("simdeye20k_parallel_1_v1", "simdeye20k_parallel_2_v1")
SCHEMA = Path(__file__).with_name("parallel_raw_modbus_schema.sql")
EXPECTED = {"register_words": 815, "fields": 624, "signals": 316}


def rawword(slot: int, address: int) -> int:
    """Invented testing value; NEVER a captured wire register."""
    digest = hashlib.sha256(f"SIMULATED-DEYE20K:{slot}:{address}".encode()).digest()
    return int.from_bytes(digest[:2], "big")


def build_payload(slot: int, fields, metrics, addresses, observed: datetime):
    if slot not in (1, 2):
        raise ValueError("Only two test slots exist")
    raw = {int(addr): rawword(slot, int(addr)) for addr in addresses}
    # Contiguous runs only: a frame never crosses an unknown register gap.
    frames = []
    group = []
    last_addr = None
    for address in sorted(raw):
        if group and (address != last_addr + 1 or len(group) == 125):
            frames.append({
                "function_code": 3, "start_address": group[0],
                "requested_count": len(group), "words": [raw[a] for a in group],
                "status": "simulated",
            })
            group = []
        group.append(address)
        last_addr = address
    if group:
        frames.append({
            "function_code": 3, "start_address": group[0],
            "requested_count": len(group), "words": [raw[a] for a in group],
            "status": "simulated",
        })
    decoded = []
    for idx, field in enumerate(fields, 1):
        number, txt = _demo_value(
            field["key"], field["name"], field["unit"], idx + (slot - 1) * 3
        )
        decoded.append({
            "field_key": field["key"],
            "value_decimal": str(number) if number is not None else None,
            "value_text": (txt + f"-UNIT{slot}") if txt else None,
            "raw_registers": {str(a): raw[int(a)] for a in field["registers"]},
        })
    dashboard = []
    for idx, signal in enumerate(metrics, 1):
        number, _ = _demo_value(
            signal["key"], signal["label"], signal.get("unit"), idx + slot
        )
        dashboard.append({
            "signal_key": signal["key"],
            "value_decimal": str(number) if number is not None else None,
            "raw_value": None,
        })
    stamp = observed.isoformat().replace("+00:00", "Z")
    return {
        "device_key": DEVICE_KEYS[slot - 1],
        "poll_uid": POLL_UIDS[slot - 1],
        "observed_at_utc": stamp,
        "transport": "simulation",
        "slave_id": None,  # NO assumed Modbus address!
        "quality": "simulated",
        "ingest_source": SOURCE,
        "frames": frames,
        "fields": decoded,
        "signals": dashboard,
    }


def check_source():
    fields, metrics, addresses = prepare()
    if len(addresses) != EXPECTED["register_words"]:
        raise RuntimeError("Incomplete raw source register inventory")
    if len(fields) != EXPECTED["fields"] or len(metrics) != EXPECTED["signals"]:
        raise RuntimeError("Unexpected decoded catalog")
    ddl = SCHEMA.read_text(encoding="utf-8")
    for table in ("rakurs_parallel_groups", "rakurs_parallel_members",
                  "rakurs_modbus_poll_runs", "rakurs_modbus_frames",
                  "rakurs_modbus_register_samples", "rakurs_modbus_bit_samples"):
        if f"CREATE TABLE IF NOT EXISTS {table}" not in ddl:
            raise RuntimeError("Missing DDL: " + table)
    observed = datetime.now(timezone.utc)
    for slot in (1, 2):
        p = build_payload(slot, fields, metrics, addresses, observed)
        all_registers = [(f["start_address"] + i) for f in p["frames"]
                         for i in range(len(f["words"]))]
        if len(all_registers) != 815 or set(all_registers) != set(addresses):
            raise RuntimeError("Raw address coverage mismatch")
        if len(p["fields"]) != 624 or len(p["signals"]) != 316:
            raise RuntimeError("Decoded/demo count mismatch")
    return fields, metrics, addresses


def backup_database(password: str) -> Path:
    tool = shutil.which("mysqldump") or shutil.which("mariadb-dump")
    if not tool:
        raise RuntimeError(
            "STOP: mysqldump/mariadb-dump unavailable. Install client and retry; "
            "no database writes attempted."
        )
    home = Path("/var/backups")
    if not home.is_dir() or not os.access(home, os.W_OK):
        raise RuntimeError("STOP: /var/backups must be writable by administrator")
    folder = Path(tempfile.mkdtemp(prefix="rakurs-two-deye-", dir=str(home)))
    folder.chmod(0o700)
    conf = folder / "mysql.cnf"
    conf.write_text("[client]\nuser=" + ADMIN_USER +
                    "\npassword=" + password.replace("\\", "\\\\").replace('"', '\\"') +
                    "\nhost=" + HOST + "\n", encoding="utf-8")
    conf.chmod(0o600)
    dump = folder / "rakurs-before-two-deye.sql.gz"
    try:
        args = [tool, "--defaults-extra-file=" + str(conf),
                "--single-transaction", "--quick", "--skip-lock-tables",
                "--no-tablespaces", "--hex-blob", DATABASE]
        with gzip.open(dump, "wb", compresslevel=6) as sink:
            done = subprocess.run(args, stdout=sink, stderr=subprocess.PIPE,
                                  timeout=1800, check=False)
        if done.returncode != 0 or not dump.is_file() or dump.stat().st_size < 100:
            raise RuntimeError("STOP: backup failed: " +
                               done.stderr.decode(errors="replace")[-500:])
    finally:
        conf.unlink(missing_ok=True)
    dump.chmod(0o600)
    print("MYSQL_BACKUP_PATH=" + str(dump))
    print("MYSQL_BACKUP_SHA256=" + hashlib.sha256(dump.read_bytes()).hexdigest())
    return dump


def apply_schema(db):
    statements = SCHEMA.read_text(encoding="utf-8").split(";")
    with db.cursor() as cur:
        for statement in statements:
            clean = "\n".join(
                line for line in statement.splitlines()
                if line.strip() and not line.strip().startswith("--")
            ).strip()
            if clean:
                if not clean.startswith("CREATE TABLE IF NOT EXISTS rakurs_"):
                    raise RuntimeError("STOP: unsafe DDL")
                cur.execute(clean)


def preexisting_devices(db):
    with db.cursor() as cur:
        cur.execute(
            "SELECT device_key,kind FROM rakurs_devices "
            "WHERE device_key IN (%s,%s)", DEVICE_KEYS
        )
        return cur.fetchall()


def seed(db, fields, metrics, addresses):
    # All device records and poll/field/raw/UI values commit together.
    # CREATE TABLE happened beforehand because MySQL DDL commits implicitly.
    db.begin()
    try:
        with db.cursor() as cur:
            cur.execute(
                "SELECT id, model, expected_inverters, battery_mode, "
                "topology_status FROM rakurs_parallel_groups "
                "WHERE group_key=%s FOR UPDATE", (GROUP_KEY,)
            )
            group = cur.fetchone()
            if group:
                group_id, model, expected, battery, topology = group
                if model != MODEL or expected != 2 or battery != "shared" or topology != "unverified":
                    raise RuntimeError("STOP: conflicting parallel group configuration")
            else:
                cur.execute(
                    "INSERT INTO rakurs_parallel_groups "
                    "(group_key,model,expected_inverters,nominal_ac_w_each,"
                    "battery_mode,battery_nominal_v,topology_status,"
                    "bms_soc_source,created_at_utc) "
                    "VALUES (%s,%s,2,20000,'shared',48.00,'unverified',NULL,%s)",
                    (GROUP_KEY, MODEL, datetime.now(timezone.utc).replace(tzinfo=None)),
                )
                group_id = cur.lastrowid
            stamp = datetime.now(timezone.utc)
            receipts = []
            for slot, key in enumerate(DEVICE_KEYS, 1):
                cur.execute(
                    "INSERT INTO rakurs_devices "
                    "(device_key,kind,model,serial_number,commissioned_at_utc,created_at_utc) "
                    "VALUES (%s,'simulation',%s,NULL,NULL,%s)",
                    (key, MODEL, stamp.replace(tzinfo=None)),
                )
                device_id = cur.lastrowid
                cur.execute(
                    "INSERT INTO rakurs_parallel_members "
                    "(group_id,device_id,slot_no,member_kind,parallel_role,"
                    "modbus_address,transport_verified,member_verified) "
                    "VALUES (%s,%s,%s,'simulation','unverified',NULL,0,0)",
                    (group_id, device_id, slot),
                )
                payload = build_payload(slot, fields, metrics, addresses, stamp)
                receipt = persist(db, payload, manage_transaction=False)
                if receipt["status"] != "saved" or (
                    receipt["raw_registers"],receipt["decoded_fields"],
                    receipt["dashboard_signals"]
                ) != (815, 624, 316):
                    raise RuntimeError("STOP: incomplete synthetic Modbus snapshot")
                receipts.append(receipt)
            cur.execute("SELECT COUNT(*) FROM rakurs_parallel_members "
                        "WHERE group_id=%s AND member_kind='simulation'", (group_id,))
            if cur.fetchone()[0] != 2:
                raise RuntimeError("STOP: expected exactly two synthetic member slots")
            cur.execute("SELECT COUNT(*) FROM rakurs_modbus_register_samples "
                        "WHERE device_id IN ("
                        "SELECT device_id FROM rakurs_parallel_members "
                        "WHERE group_id=%s AND member_kind='simulation')",
                        (group_id,))
            if cur.fetchone()[0] != 2 * 815:
                raise RuntimeError("STOP: failed to save every synthetic raw register")
        db.commit()
        return receipts
    except Exception:
        db.rollback()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true",
                        help="backup, create additive schema and seed test data")
    args = parser.parse_args()
    fields, metrics, addresses = check_source()
    print(f"PARALLEL_TEST_DEVICES={len(DEVICE_KEYS)}")
    print(f"REGISTER_WORDS_PER_DEVICE={len(addresses)}")
    print(f"DECODED_FIELDS_PER_DEVICE={len(fields)}")
    print(f"UI_FIELDS_PER_DEVICE={len(metrics)}")
    print("PHYSICAL_DEVICES_CREATED=0 REAL_MODBUS_READS=0")
    if not args.execute:
        print("TWO_DEYE_SEED_PREFLIGHT=PASS")
        print("DRY_RUN=PASS MYSQL_CHANGED=NO")
        return 0
    if not sys.stdin.isatty():
        raise RuntimeError("STOP: execute only from interactive terminal")
    if input("Type exactly SEED-TWO-SIMULATED-DEYE: ").strip() != (
        "SEED-TWO-SIMULATED-DEYE"
    ):
        raise RuntimeError("Aborted: no DB mutations")
    password = getpass.getpass("MySQL Ivital password (hidden): ")
    try:
        import pymysql
        db = pymysql.connect(
            host=HOST, port=3306, database=DATABASE, user=ADMIN_USER,
            password=password, ssl_disabled=True, charset="utf8mb4",
            autocommit=False, connect_timeout=6, read_timeout=60,
            write_timeout=60,
        )
        try:
            with db.cursor() as cur:
                cur.execute("SELECT device_key FROM rakurs_devices WHERE "
                            "device_key IN (%s,%s)", DEVICE_KEYS)
                existing = cur.fetchall()
            if existing:
                raise RuntimeError(
                    "STOP: one or both twin-device keys already exist; "
                    "inspect manually before rerunning. No new rows inserted."
                )
            backup_database(password)
            apply_schema(db)
            receipts = seed(db, fields, metrics, addresses)
            print("SIMULATED_TWIN_SEED_COMMITTED=PASS")
            for n, rec in enumerate(receipts, 1):
                print(f"SIM_INVERTER_{n}_RAW_REGISTERS={rec['raw_registers']}")
                print(f"SIM_INVERTER_{n}_MODBUS_FIELDS={rec['decoded_fields']}")
                print(f"SIM_INVERTER_{n}_UI_FIELDS={rec['dashboard_signals']}")
                print(f"SIM_INVERTER_{n}_POLL_ID={rec['poll_id']}")
            print("BOTH_INVERTERS_SIMULATED=PASS MYSQL_REAL_ROWS_UNCHANGED=YES")
        finally:
            db.close()
    finally:
        del password
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
