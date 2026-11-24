#!/usr/bin/env python3
"""Append a deterministic 72-hour Deye time series to the EXISTING MySQL Rakurs archive.

All 316 UI signals: 15-minute resolution for 72h (288 older samples per signal).
All 624 P3 field definitions: hourly resolution for 72h.
The existing one-shot observation remains unchanged and is the final point.
Only the simulated Deye device may receive new rows. No real telemetry, device
connections, Modbus requests, schema changes, or removals.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import getpass
import hashlib
import math
import sys
from zoneinfo import ZoneInfo

from simulate_deye_once import DEVICE_KEY, INGEST_SOURCE, QUALITY, prepare

DEVICE_MINUTES = 72 * 4
MODBUS_HOURS = 72
BATCH_CODE = "DEYE_HISTORY_72H_V1"
KYIV = ZoneInfo("Europe/Kyiv")
UI_PER_TICK = 316
P3_PER_TICK = 624
UI_INSERTS = DEVICE_MINUTES * UI_PER_TICK
P3_INSERTS = MODBUS_HOURS * P3_PER_TICK


def _number(value: float) -> Decimal:
    return Decimal(str(round(value, 5)))


def _solar(hour: float) -> float:
    if not 7.5 < hour < 16.7:
        return 0.0
    return 9.3 * math.sin(math.pi * (hour - 7.5) / 9.2) ** 1.6


def _load(hour: float) -> float:
    busy = 0.8 if 8 <= hour < 19 else 0.0
    evening = 1.6 if 18 <= hour < 23 else 0.0
    return 3.9 + busy + evening + 0.35 * math.sin(hour * 1.4)


def _flows(hour: float) -> tuple[float, float, float, float, float]:
    pv = _solar(hour)
    load = _load(hour)
    if pv > load:
        battery = -min(2.7, 0.58 * (pv - load))  # negative: charging
    else:
        battery = min(2.1, 0.57 * (load - pv))    # positive: discharging
    grid = load - pv - battery  # positive: import; negative: export
    soc = 67.0 + 15.0 * math.cos(2 * math.pi * (hour - 16) / 24)
    return pv, load, battery, grid, soc


def _hour(ts: datetime) -> tuple[datetime, float]:
    local = ts.replace(tzinfo=timezone.utc).astimezone(KYIV)
    return local, local.hour + local.minute / 60 + local.second / 3600


def _daily_energy(hour: float, component: str) -> float:
    # Integrate using fixed quarter-hour steps, stable under repeated runs.
    accum = 0.0
    h = 0.0
    while h < hour:
        step = min(0.25, hour - h)
        pv, load, battery, grid, _soc = _flows(h + step / 2)
        power = {
            "pv": pv, "load": load,
            "battery_charge": max(0.0, -battery),
            "battery_discharge": max(0.0, battery),
            "grid_import": max(0.0, grid),
            "grid_export": max(0.0, -grid),
        }[component]
        accum += power * step
        h += step
    return accum


def snapshot(ts: datetime) -> dict[str, float]:
    _local, h = _hour(ts)
    pv, load, battery, grid, soc = _flows(h)
    frequency = 50 + 0.02 * math.sin(h * 0.9)
    voltage = 230 + 1.1 * math.sin(h * 0.55)
    battery_voltage = 51.5 + 0.012 * soc
    outputs = {
        "pv_power": pv, "pv1_power": pv * 0.49, "pv2_power": pv * 0.51,
        "pv1_voltage": 320.0 if pv > 0.01 else 0.0,
        "pv2_voltage": 324.0 if pv > 0.01 else 0.0,
        "pv1_current": (pv * 0.49 * 1000 / 320) if pv > 0.01 else 0.0,
        "pv2_current": (pv * 0.51 * 1000 / 324) if pv > 0.01 else 0.0,
        "load_power": load, "output_power": load,
        "battery_power": battery, "grid_power": grid,
        "battery_soc": soc, "bms_soc": soc,
        "battery_voltage": battery_voltage,
        "bms_voltage": battery_voltage,
        "battery_current": battery * 1000 / battery_voltage,
        "bms_current": battery * 1000 / battery_voltage,
        "battery_temperature": 25.0 + 2.2 * math.sin((h - 9) * math.pi / 12),
        "inverter_temperature": 28.4 + 0.8 * pv + 0.35 * load,
        "dc_temperature": 26.5 + 0.65 * pv,
        "grid_frequency": frequency, "load_frequency": frequency,
        "output_frequency": frequency,
        "grid_power_factor": 0.99,
        "pv_energy_today": _daily_energy(h, "pv"),
        "load_energy_today": _daily_energy(h, "load"),
        "battery_charge_today": _daily_energy(h, "battery_charge"),
        "battery_discharge_today": _daily_energy(h, "battery_discharge"),
        "grid_import_today": _daily_energy(h, "grid_import"),
        "grid_export_today": _daily_energy(h, "grid_export"),
    }
    for stem, total in (("load", load), ("output", load), ("grid", grid)):
        for phase, share in (("l1", 0.36), ("l2", 0.33), ("l3", 0.31)):
            outputs[f"{stem}_{phase}_power"] = total * share
            outputs[f"{stem}_{phase}_voltage"] = voltage + (0.4 if phase == "l2" else -0.2 if phase == "l3" else 0)
            if stem == "output":
                outputs[f"{stem}_{phase}_current"] = total * share * 1000 / max(voltage, 1.0)
    return outputs


def value_for(
    key: str, name: str, unit: str | None, original: Decimal | None,
    ts: datetime, anchor: datetime, profile: bool,
) -> Decimal | None:
    if original is None:
        return None
    derived = snapshot(ts)
    if key in derived:
        value = derived[key]
        if profile:
            u = (unit or "").lower()
            if u in ("w", "watt", "ват") and (
                "power" in key or key in ("grid_power", "load_power")
            ):
                value *= 1000
        return _number(value)
    base = float(original)
    low = (key + " " + name).lower()
    if any(word in low for word in (
        "mode", "state", "status", "fault", "alarm", "error", "rated",
        "firmware", "software", "serial", "phases", "mppt",
    )):
        return original
    delta_hours = (ts - anchor).total_seconds() / 3600
    if "total" in low or "cumulative" in low or "lifetime" in low:
        # Older cumulative readings cannot exceed the original final reading.
        return _number(max(0.0, base + delta_hours * max(0.01, abs(base) / 10000)))
    seed = int.from_bytes(hashlib.sha256(key.encode()).digest()[:4], "big")
    phase = (seed % 628) / 100
    _local, hour = _hour(ts)
    if ("pv" in low or "solar" in low) and any(
        x in low for x in ("power", "current")
    ):
        return _number(max(0.0, base) * _solar(hour) / 9.3)
    variation = (
        1 + 0.045 * math.sin(hour * math.pi / 12 + phase)
        + 0.02 * math.cos(delta_hours * 0.35 + phase)
    )
    return _number(base * variation) if base != 0 else original


def generate_ui_rows(signals, anchor: datetime, received: datetime):
    # Deterministic ordering and samples; batch insert while in one transaction.
    for offset in range(DEVICE_MINUTES, 0, -1):
        ts = anchor - timedelta(minutes=15 * offset)
        yield [
            (
                signal_id, ts, received,
                value_for(key, name, unit, baseline, ts, anchor, False),
                QUALITY, INGEST_SOURCE,
            )
            for signal_id, key, name, unit, baseline in signals
        ]


def generate_p3_rows(fields, anchor: datetime, received: datetime):
    for offset in range(MODBUS_HOURS, 0, -1):
        ts = anchor - timedelta(hours=offset)
        yield [
            (
                field_id, ts, received,
                value_for(key, name, unit, baseline, ts, anchor, True),
                text_value, QUALITY, INGEST_SOURCE,
            )
            for field_id, key, name, unit, baseline, text_value in fields
        ]


def _insert_chunks(cur, statement, iterator, device_id: int):
    total = 0
    batch = []
    for chunk in iterator:
        batch.extend((device_id, *row) for row in chunk)
        if len(batch) >= 1200:
            cur.executemany(statement, batch)
            total += len(batch)
            batch.clear()
    if batch:
        cur.executemany(statement, batch)
        total += len(batch)
    return total


def append_history(password: str) -> bool:
    import pymysql

    db = pymysql.connect(
        host="192.168.5.30", port=3306, database="rakurs",
        user="Ivital", password=password, ssl_disabled=True,
        autocommit=False, charset="utf8mb4",
        connect_timeout=6, read_timeout=120, write_timeout=120,
    )
    try:
        with db.cursor() as cur:
            cur.execute("SELECT DATABASE()")
            assert cur.fetchone()[0] == "rakurs"
            cur.execute("SELECT id FROM rakurs_devices WHERE device_key=%s FOR UPDATE",
                        (DEVICE_KEY,))
            row = cur.fetchone()
            if not row:
                raise RuntimeError("STOP: expected simulated Deye device is absent")
            device_id = row[0]
            cur.execute(
                "SELECT COUNT(*) FROM rakurs_samples WHERE quality<>'simulated'"
            )
            if cur.fetchone()[0]:
                raise RuntimeError("STOP: real telemetry present; leave this archive unchanged")
            cur.execute(
                "SELECT COUNT(*) FROM rakurs_modbus_field_samples WHERE quality<>'simulated'"
            )
            if cur.fetchone()[0]:
                raise RuntimeError("STOP: real Modbus samples present")
            cur.execute(
                "SELECT COUNT(*) FROM rakurs_ingest_batches "
                "WHERE device_id=%s AND status='simulated' AND error_code=%s",
                (device_id, BATCH_CODE),
            )
            marker_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM rakurs_samples WHERE device_id=%s",
                        (device_id,))
            current_ui = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(*) FROM rakurs_modbus_field_samples WHERE device_id=%s",
                (device_id,),
            )
            current_p3 = cur.fetchone()[0]
            if marker_count:
                if (
                    marker_count == 1
                    and current_ui == UI_PER_TICK + UI_INSERTS
                    and current_p3 == P3_PER_TICK + P3_INSERTS
                ):
                    print("DEYE_HISTORY_ALREADY_INSTALLED=PASS")
                    db.rollback()
                    return False
                raise RuntimeError("STOP: partial or inconsistent history detected")

            if (current_ui, current_p3) != (UI_PER_TICK, P3_PER_TICK):
                raise RuntimeError(
                    "STOP: existing Deye samples do not match the single original "
                    "snapshot; no overwriting is permitted"
                )
            cur.execute(
                "SELECT s.id, s.signal_key, s.label_uk, s.unit, m.value_decimal, "
                "m.observed_at_utc, m.quality, m.ingest_source "
                "FROM rakurs_signals AS s JOIN rakurs_samples AS m "
                "ON m.signal_id=s.id WHERE m.device_id=%s ORDER BY s.id",
                (device_id,),
            )
            originals_ui = cur.fetchall()
            cur.execute(
                "SELECT f.id, f.field_key, f.field_name, f.unit, "
                "m.value_decimal, m.value_text, m.observed_at_utc, "
                "m.quality, m.ingest_source "
                "FROM rakurs_modbus_fields AS f "
                "JOIN rakurs_modbus_field_samples AS m ON m.field_id=f.id "
                "WHERE m.device_id=%s ORDER BY f.id",
                (device_id,),
            )
            originals_p3 = cur.fetchall()
            if len(originals_ui) != 316 or len(originals_p3) != 624:
                raise RuntimeError("STOP: original field coverage mismatch")
            times = {x[5] for x in originals_ui} | {x[6] for x in originals_p3}
            if len(times) != 1:
                raise RuntimeError("STOP: original timestamps differ")
            anchor = times.pop()
            if not all(x[6] == QUALITY and x[7] == INGEST_SOURCE for x in originals_ui):
                raise RuntimeError("STOP: original UI provenance differs")
            if not all(x[7] == QUALITY and x[8] == INGEST_SOURCE for x in originals_p3):
                raise RuntimeError("STOP: original profile provenance differs")
            if anchor > datetime.now(timezone.utc).replace(tzinfo=None):
                raise RuntimeError("STOP: original observation is in the future")
            signals = [x[:5] for x in originals_ui]
            fields = [x[:6] for x in originals_p3]
            received = datetime.now(timezone.utc).replace(tzinfo=None)
            print("HISTORY_ANCHORED_AT_UTC=" + anchor.isoformat() + "Z")
            print("HISTORY_OLDEST_UTC=" +
                  (anchor - timedelta(hours=72)).isoformat() + "Z")
            print("=== INSERT IN ONE ATOMIC TRANSACTION ===")

            ui_sql = (
                "INSERT INTO rakurs_samples "
                "(device_id,signal_id,observed_at_utc,received_at_utc,"
                "value_decimal,raw_value,quality,ingest_source) "
                "VALUES (%s,%s,%s,%s,%s,NULL,%s,%s)"
            )
            ui_written = _insert_chunks(
                cur, ui_sql, generate_ui_rows(signals, anchor, received), device_id
            )
            modbus_sql = (
                "INSERT INTO rakurs_modbus_field_samples "
                "(device_id,field_id,observed_at_utc,received_at_utc,"
                "value_decimal,value_text,raw_registers_json,quality,ingest_source) "
                "VALUES (%s,%s,%s,%s,%s,%s,NULL,%s,%s)"
            )
            p3_written = _insert_chunks(
                cur, modbus_sql, generate_p3_rows(fields, anchor, received), device_id
            )
            if (ui_written, p3_written) != (UI_INSERTS, P3_INSERTS):
                raise RuntimeError("STOP: row counts mismatch; rolling back")
            cur.execute(
                "INSERT INTO rakurs_ingest_batches "
                "(device_id,started_at_utc,finished_at_utc,received_count,"
                "accepted_count,rejected_count,status,error_code) "
                "VALUES (%s,%s,%s,%s,%s,0,'simulated',%s)",
                (device_id, received, received, ui_written + p3_written,
                 ui_written + p3_written, BATCH_CODE),
            )
            cur.execute("SELECT COUNT(*) FROM rakurs_samples WHERE device_id=%s",
                        (device_id,))
            if cur.fetchone()[0] != UI_PER_TICK + UI_INSERTS:
                raise RuntimeError("STOP: UI samples not fully inserted")
            cur.execute(
                "SELECT COUNT(*) FROM rakurs_modbus_field_samples WHERE device_id=%s",
                (device_id,),
            )
            if cur.fetchone()[0] != P3_PER_TICK + P3_INSERTS:
                raise RuntimeError("STOP: profile samples not fully inserted")
            cur.execute(
                "SELECT COUNT(*), MIN(value_decimal), MAX(value_decimal) "
                "FROM rakurs_samples WHERE device_id=%s "
                "AND signal_id=(SELECT id FROM rakurs_signals WHERE signal_key='pv_power')",
                (device_id,),
            )
            count, lower, upper = cur.fetchone()
            if count != DEVICE_MINUTES + 1 or float(upper) - float(lower) < 2:
                raise RuntimeError("STOP: solar historical curve is flat or incomplete")
        db.commit()
        print(f"UI_HISTORY_ROWS_COMMITTED={UI_INSERTS}")
        print(f"P3_HISTORY_ROWS_COMMITTED={P3_INSERTS}")
        print(f"PV_HISTORY_POINTS={DEVICE_MINUTES + 1}")
        print("SIMULATED_PROVENANCE=RETAINED")
        print("MYSQL_HISTORY_TRANSACTION=COMMITTED")
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--execute", action="store_true")
    args = p.parse_args()

    fields, metrics, _addresses = prepare()
    assert len(fields) == 624 and len(metrics) == 316
    print("PROFILE_FIELDS=624 UI_FIELDS=316")
    print("HISTORY_HOURS=72")
    print("UI_SAMPLING_MINUTES=15")
    print("MODBUS_SAMPLING_MINUTES=60")
    print(f"PLANNED_UI_ROWS={UI_INSERTS}")
    print(f"PLANNED_MODBUS_ROWS={P3_INSERTS}")
    print("MODBUS_COMMANDS_SENT=0")
    if not args.execute:
        print("DRY_RUN=PASS MYSQL_CHANGED=NO")
        return 0

    if not sys.stdin.isatty():
        print("STOP: interactive terminal required", file=sys.stderr)
        return 2
    phrase = "APPEND-DEYE-HISTORY-72H"
    if input(f"Type exactly {phrase}: ").strip() != phrase:
        print("ABORTED MYSQL_CHANGED=NO")
        return 2
    pw = getpass.getpass("MySQL Ivital password (hidden): ")
    try:
        append_history(pw)
    finally:
        del pw
    print("DEYE_HISTORY_SEED=PASS")
    print("OPEN=https://rakurs.zakryttya.org.ua/#/history")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
