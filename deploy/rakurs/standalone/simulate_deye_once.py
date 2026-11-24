#!/usr/bin/env python3
"""Generate a single Deye P3 profile snapshot from complete source field catalogs.

One command produces one synthetic observation timestamp and one ingest batch,
including all 624 source-profile definitions and all 316 Ukrainian UI aliases.
No RS485/Modbus TCP socket, Teltonika, real device, or user data is modified.
All generated values retain quality='simulated', never verified=1.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import getpass
import json
from pathlib import Path
import sys

DEVICE_KEY = "simulated_deye_sg05lp3"
INGEST_SOURCE = "rakurs_demo_one_shot"
QUALITY = "simulated"
ROOT = Path(__file__).resolve().parents[1]
UI_CATALOG = ROOT / "signal_catalog.json"
FULL_CATALOG = ROOT / "modbus_full_catalog.json"
SCHEMA_FILE = Path(__file__).with_name("modbus_full_schema.sql")

# Hand-aligned illustrative headline readings, with PV + battery = load.
VALUES = {
    "pv_power": "5.10",
    "pv1_power": "2.50",
    "pv1_voltage": "315.2",
    "pv1_current": "7.93",
    "pv2_power": "2.60",
    "pv2_voltage": "322.0",
    "pv2_current": "8.07",
    "pv_energy_today": "21.40",
    "pv_energy_total": "4285.20",
    "dc_temperature": "30.7",
    "battery_soc": "72",
    "battery_soh": "96",
    "battery_voltage": "52.1",
    "battery_current": "24.95",
    "battery_power": "1.30",
    "battery_temperature": "26.4",
    "battery_capacity": "200",
    "battery_charge_today": "4.60",
    "battery_discharge_today": "3.20",
    "battery_charge_total": "315.8",
    "battery_discharge_total": "291.3",
    "bms_soc": "72",
    "bms_voltage": "52.1",
    "bms_current": "24.95",
    "grid_power": "0",
    "grid_frequency": "50.02",
    "grid_power_factor": "0.99",
    "grid_l1_voltage": "229.8",
    "grid_l2_voltage": "230.5",
    "grid_l3_voltage": "230.2",
    "grid_l1_power": "0",
    "grid_l2_power": "0",
    "grid_l3_power": "0",
    "grid_import_today": "1.70",
    "grid_export_today": "0.00",
    "grid_import_total": "551.2",
    "grid_export_total": "26.4",
    "load_power": "6.40",
    "load_l1_voltage": "229.8",
    "load_l2_voltage": "230.5",
    "load_l3_voltage": "230.2",
    "load_l1_power": "2.20",
    "load_l2_power": "2.10",
    "load_l3_power": "2.10",
    "load_frequency": "50.02",
    "load_energy_today": "34.30",
    "load_energy_total": "9455.60",
    "output_power": "6.40",
    "output_l1_voltage": "229.8",
    "output_l2_voltage": "230.5",
    "output_l3_voltage": "230.2",
    "output_l1_current": "9.57",
    "output_l2_current": "9.11",
    "output_l3_current": "9.12",
    "output_l1_power": "2.20",
    "output_l2_power": "2.10",
    "output_l3_power": "2.10",
    "output_frequency": "50.02",
    "inverter_temperature": "37.6",
    "inverter_power_rated": "16.0",
    "inverter_mppts": "2",
    "inverter_phases": "3",
}

def _demo_value(key: str, name: str, unit: str | None, order: int):
    """Synthetic sample, not a decoded Modbus register value.

    Both numeric and text/enum-style source fields have a storage column;
    no firmware/serial string is silently coerced to zero.
    """
    if key in VALUES:
        return Decimal(VALUES[key]), None
    k = (key + " " + name).lower()
    u = (unit or "").lower()
    if any(w in k for w in ("serial", "firmware", "software version",
                             "hardware version", "protocol version")):
        return None, "SIM-PROFILE-01"
    if "date" in k or "time zone" in k:
        return None, "2026-11-24 12:00:00"
    if "mode" in k or "status" in k or "state" in k:
        return Decimal("1"), None
    if "alarm" in k or "fault" in k or "error" in k:
        return Decimal("0"), None
    if "soc" in k or "%" in u:
        return Decimal("72"), None
    if "soh" in k:
        return Decimal("96"), None
    if "frequency" in k or "hz" in u:
        return Decimal("50.02"), None
    if "voltage" in k or u in {"v", "mv"}:
        if "cell" in k:
            return Decimal("3.31") if u != "mv" else Decimal("3310"), None
        if "battery" in k or "bms" in k:
            return Decimal("52.1"), None
        if "pv" in k:
            return Decimal("320.0"), None
        return Decimal("230.1"), None
    if "current" in k or u == "a":
        return Decimal("9.5"), None
    if "temperature" in k or "°c" in u:
        return Decimal("29.5"), None
    if "power" in k or "w" in u:
        if "pv" in k:
            return Decimal("5100") if "kw" not in u and "квт" not in u else Decimal("5.1"), None
        if "load" in k or "output" in k:
            return Decimal("6400") if "kw" not in u and "квт" not in u else Decimal("6.4"), None
        return Decimal("1300") if "kw" not in u and "квт" not in u else Decimal("1.3"), None
    if "energy" in k or "kwh" in u or "квт·год" in u:
        return Decimal("4200") if "total" in k else Decimal("25.4"), None
    if "capacity" in k or "ah" in u:
        return Decimal("200"), None
    if "cycles" in k or "cycle" in k:
        return Decimal("18"), None
    if "cell" in k or "module" in k:
        return Decimal("1"), None
    if "datetime" in k:
        return None, "2026-11-24 12:00:00"
    return Decimal(str(order % 9)), None


def prepare():
    full = json.loads(FULL_CATALOG.read_text(encoding="utf-8"))
    ui = json.loads(UI_CATALOG.read_text(encoding="utf-8"))
    fields = full["field_definitions"]
    ui_metrics = [x for g in ui["groups"].values() for x in g]
    reg_addresses = sorted({int(a) for x in fields for a in x["registers"]})
    if len(fields) != full["field_count"] or len(fields) != 624:
        raise RuntimeError("Unexpected full Modbus profile field count")
    if len({x["key"] for x in fields}) != len(fields):
        raise RuntimeError("Duplicate source-profile field key")
    if len(reg_addresses) != full["register_count"] or len(reg_addresses) != 815:
        raise RuntimeError("Unexpected source register count")
    if len(ui_metrics) != ui["all_metric_count"] or len(ui_metrics) != 316:
        raise RuntimeError("Unexpected UI catalog count")
    if len({x["key"] for x in ui_metrics}) != len(ui_metrics):
        raise RuntimeError("Duplicate UI key")
    return fields, ui_metrics, reg_addresses


def write_once(password: str, fields, ui_metrics, reg_addresses):
    import pymysql

    db = pymysql.connect(
        host="192.168.5.30", port=3306, database="rakurs",
        user="Ivital", password=password, ssl_disabled=True, autocommit=False,
        charset="utf8mb4", connect_timeout=6, read_timeout=20, write_timeout=20,
    )
    base = ("rakurs_devices", "rakurs_signals", "rakurs_samples",
            "rakurs_ingest_batches", "rakurs_device_events")
    extensions = ("rakurs_modbus_fields", "rakurs_modbus_field_samples",
                  "rakurs_modbus_register_inventory")
    observed = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        # All existing monitoring tables must be empty; auth tables are retained.
        with db.cursor() as cur:
            cur.execute("SELECT DATABASE()")
            if cur.fetchone()[0] != "rakurs":
                raise RuntimeError("Unexpected target schema")
            for table in base:
                cur.execute("SELECT COUNT(*) FROM " + table)
                if cur.fetchone()[0] != 0:
                    raise RuntimeError(f"STOP: {table} already contains data")
            for table in extensions:
                cur.execute(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema=DATABASE() AND table_name=%s", (table,)
                )
                if cur.fetchone()[0]:
                    cur.execute("SELECT COUNT(*) FROM " + table)
                    if cur.fetchone()[0]:
                        raise RuntimeError(f"STOP: {table} already populated")
        # Idempotent schema creation only. MySQL DDL commits implicitly.
        statements = [x.strip() for x in SCHEMA_FILE.read_text(encoding="utf-8").split(";")]
        with db.cursor() as cur:
            for stmt in statements:
                lines = [line for line in stmt.splitlines()
                         if line.strip() and not line.lstrip().startswith("--")]
                if lines:
                    sql = "\n".join(lines)
                    if not sql.startswith("CREATE TABLE IF NOT EXISTS rakurs_modbus_"):
                        raise RuntimeError("Unexpected SQL schema statement")
                    cur.execute(sql)
        db.begin()
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO rakurs_devices "
                "(device_key,kind,model,serial_number,commissioned_at_utc,created_at_utc) "
                "VALUES (%s,%s,%s,NULL,NULL,%s)",
                (DEVICE_KEY, "simulation", "Deye SG05LP3 (simulation)", observed)
            )
            device_id = cur.lastrowid
            for index, field in enumerate(fields, 1):
                cur.execute(
                    "INSERT INTO rakurs_modbus_fields "
                    "(field_key,profile_name,group_name,field_name,platform,unit,"
                    "rule_code,scale_definition,class_name,state_class,registers_json,"
                    "register_sets_json,derived,verified,created_at_utc) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s)",
                    (field["key"], field["source_profile"], field["group"],
                     field["name"], field["platform"], field["unit"],
                     field["rule"], field["scale"], field["class"],
                     field["state_class"],
                     json.dumps(field["registers"]),
                     json.dumps(field["register_sets"]),
                     int(field["derived"]), observed)
                )
                field_id = cur.lastrowid
                decimal, txt = _demo_value(
                    field["key"], field["name"], field["unit"], index
                )
                cur.execute(
                    "INSERT INTO rakurs_modbus_field_samples "
                    "(device_id,field_id,observed_at_utc,received_at_utc,"
                    "value_decimal,value_text,raw_registers_json,quality,ingest_source) "
                    "VALUES (%s,%s,%s,%s,%s,%s,NULL,%s,%s)",
                    (device_id, field_id, observed, observed, decimal, txt,
                     QUALITY, INGEST_SOURCE)
                )
            for address in reg_addresses:
                cur.execute(
                    "INSERT INTO rakurs_modbus_register_inventory "
                    "(address,function_code,profile_name,verified,created_at_utc) "
                    "VALUES (%s,3,%s,0,%s)",
                    (address, "deye_p3.yaml", observed)
                )
            for index, metric in enumerate(ui_metrics, 1):
                cur.execute(
                    "INSERT INTO rakurs_signals "
                    "(signal_key,label_uk,unit,source_register,function_code,"
                    "scale_expression,state_class,verified,verified_at_utc,verification_note) "
                    "VALUES (%s,%s,%s,NULL,NULL,NULL,NULL,0,NULL,%s)",
                    (metric["key"], metric["label"], metric.get("unit"),
                     "Synthetic sample; source Modbus register not validated")
                )
                signal_id = cur.lastrowid
                decimal, txt = _demo_value(
                    metric["key"], metric["label"], metric.get("unit"), index
                )
                # Existing dashboard samples are numeric. Text-capable profile
                # values are retained in rakurs_modbus_field_samples.
                cur.execute(
                    "INSERT INTO rakurs_samples "
                    "(device_id,signal_id,observed_at_utc,received_at_utc,"
                    "value_decimal,raw_value,quality,ingest_source) "
                    "VALUES (%s,%s,%s,%s,%s,NULL,%s,%s)",
                    (device_id, signal_id, observed, observed,
                     decimal, QUALITY, INGEST_SOURCE)
                )
            cur.execute(
                "INSERT INTO rakurs_ingest_batches "
                "(device_id,started_at_utc,finished_at_utc,received_count,"
                "accepted_count,rejected_count,status,error_code) "
                "VALUES (%s,%s,%s,%s,%s,0,%s,NULL)",
                (device_id, observed, observed, len(fields),
                 len(fields), "simulated")
            )
            batch_id = cur.lastrowid
        db.commit()
        return batch_id
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true",
                        help="insert exactly one synthetic read cycle into empty Rakurs tables")
    args = parser.parse_args()
    fields, ui_metrics, reg_addresses = prepare()
    print(f"P3_MODBUS_FIELDS={len(fields)}")
    print(f"P3_REGISTER_ADDRESSES={len(reg_addresses)}")
    print(f"RAKURS_UI_FIELDS={len(ui_metrics)}")
    print("SIMULATED_CYCLES=1; MODBUS_COMMANDS_SENT=0")
    if not args.execute:
        print("DRY_RUN=PASS; MYSQL_CHANGED=NO")
        return 0
    if not sys.stdin.isatty():
        print("STOP: interactive terminal required", file=sys.stderr)
        return 2
    if input("Type exactly SIMULATE-DEYE-ONCE: ").strip() != "SIMULATE-DEYE-ONCE":
        print("ABORTED; MYSQL_CHANGED=NO")
        return 2
    password = getpass.getpass("MySQL Ivital password (hidden): ")
    try:
        batch_id = write_once(password, fields, ui_metrics, reg_addresses)
    finally:
        del password
    print(f"SIMULATED_ONE_SHOT_BATCH_ID={batch_id}")
    print(f"MODBUS_FIELD_VALUES_WRITTEN={len(fields)}")
    print(f"UI_SAMPLE_VALUES_WRITTEN={len(ui_metrics)}")
    print(f"MODBUS_REGISTER_DEFINITIONS={len(reg_addresses)}")
    print("SIMULATED_ONE_SHOT=PASS")
    print("VIEW=https://rakurs.zakryttya.org.ua/?demo=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
