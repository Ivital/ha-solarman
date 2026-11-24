#!/usr/bin/env python3
"""One synthetic Deye SG05LP3 snapshot, one MySQL transaction; NO device I/O.

This is a UI demonstration, not a Modbus TCP request or register validation.
The program cannot talk to Teltonika, ITM or Deye. The public Rakurs API stays
read-only and all demo data is distinctly labelled and opt-in (?demo=1).
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
CATALOG = ROOT / "signal_catalog.json"

# Physically plausible illustrative values only. NOT a verified register map,
# inverter configuration, serial number, measurement or energy accounting.
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


def prepared_snapshot() -> list[tuple[str, str, str | None, Decimal]]:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    all_items = {
        metric["key"]: metric
        for items in catalog["groups"].values()
        for metric in items
    }
    assert len(all_items) == catalog["all_metric_count"], "Catalog has duplicate keys"
    unknown = sorted(set(VALUES) - set(all_items))
    if unknown:
        raise ValueError("Unknown catalog keys: " + ", ".join(unknown))
    result = []
    for key, value in VALUES.items():
        metric = all_items[key]
        if metric.get("status") not in {"planned_unverified", "optional_unverified"}:
            raise ValueError(f"Unexpected verification status for {key}")
        result.append((key, metric["label"], metric.get("unit"), Decimal(value)))
    if not (8 <= len(result) <= 100):
        raise ValueError("Unexpected demo signal count")
    return result


def simulate_one_read(snapshot):
    """One logical in-memory inverter response, NOT an RTU/TCP exchange."""
    observed = datetime.now(timezone.utc).replace(tzinfo=None)
    return observed, tuple(snapshot)


def write_once(password: str, snapshot) -> tuple[int, int]:
    import pymysql

    db = pymysql.connect(
        host="192.168.5.30", port=3306, database="rakurs",
        user="Ivital", password=password, ssl_disabled=True,
        autocommit=False, connect_timeout=6, read_timeout=10,
        write_timeout=10, charset="utf8mb4",
    )
    observed, response = simulate_one_read(snapshot)
    try:
        db.begin()
        with db.cursor() as cur:
            cur.execute("SELECT DATABASE()")
            if cur.fetchone()[0] != "rakurs":
                raise RuntimeError("Wrong MySQL database")
            # Never insert synthetic data into a populated live measurement DB.
            for table in ("rakurs_devices", "rakurs_signals", "rakurs_samples",
                          "rakurs_ingest_batches", "rakurs_device_events"):
                cur.execute("SELECT COUNT(*) FROM " + table)
                if cur.fetchone()[0] != 0:
                    raise RuntimeError(
                        f"STOP: {table} is not empty; demo must not mix with existing data"
                    )
            cur.execute(
                "INSERT INTO rakurs_devices "
                "(device_key,kind,model,serial_number,commissioned_at_utc,created_at_utc) "
                "VALUES (%s,%s,%s,NULL,NULL,%s)",
                (DEVICE_KEY, "simulation", "Deye SG05LP3 (SIMULATED ONLY)", observed)
            )
            device_id = cur.lastrowid
            for key, label, unit, value in response:
                cur.execute(
                    "INSERT INTO rakurs_signals "
                    "(signal_key,label_uk,unit,source_register,function_code,"
                    "scale_expression,state_class,verified,verified_at_utc,verification_note) "
                    "VALUES (%s,%s,%s,NULL,NULL,NULL,NULL,0,NULL,%s)",
                    (key, label, unit,
                     "SIMULATED ONLY; not validated against a physical inverter")
                )
                signal_id = cur.lastrowid
                cur.execute(
                    "INSERT INTO rakurs_samples "
                    "(device_id,signal_id,observed_at_utc,received_at_utc,"
                    "value_decimal,raw_value,quality,ingest_source) "
                    "VALUES (%s,%s,%s,%s,%s,NULL,%s,%s)",
                    (device_id, signal_id, observed, observed, value,
                     QUALITY, INGEST_SOURCE)
                )
            cur.execute(
                "INSERT INTO rakurs_ingest_batches "
                "(device_id,started_at_utc,finished_at_utc,received_count,"
                "accepted_count,rejected_count,status,error_code) "
                "VALUES (%s,%s,%s,%s,%s,0,%s,NULL)",
                (device_id, observed, observed, len(response), len(response),
                 "simulated")
            )
            batch_id = cur.lastrowid
        db.commit()
        return batch_id, len(response)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true",
                        help="write one clearly labelled simulated snapshot to empty rakurs tables")
    args = parser.parse_args()
    snapshot = prepared_snapshot()
    print("SIMULATOR_DEVICE=" + DEVICE_KEY)
    print("SIMULATION_ONLY=YES; HARDWARE_REQUESTS=0; MODBUS_REQUESTS=0")
    print("SYNTHETIC_SIGNALS=" + str(len(snapshot)))
    if not args.execute:
        print("DRY_RUN=PASS; MYSQL_NOT_TOUCHED=YES")
        return 0
    if not sys.stdin.isatty():
        print("STOP: interactive terminal required", file=sys.stderr)
        return 2
    if input("Type exactly SIMULATE-DEYE-ONCE: ").strip() != "SIMULATE-DEYE-ONCE":
        print("ABORTED; MYSQL_NOT_TOUCHED=YES")
        return 2
    password = getpass.getpass("MySQL Ivital password (hidden): ")
    try:
        batch_id, count = write_once(password, snapshot)
    finally:
        del password
    print(f"SIMULATED_ONE_SHOT_BATCH_ID={batch_id}")
    print(f"SIMULATED_SAMPLES_WRITTEN={count}")
    print("MYSQL_TRANSACTION=COMMITTED; VERIFIED_SIGNALS=0")
    print("VIEW=https://rakurs.zakryttya.org.ua/?demo=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
