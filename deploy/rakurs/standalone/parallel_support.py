"""Read-only parallel Deye topology and data-quality rules.

Physical topology is not inferable from database rows. No Modbus writes or
assumptions about master/slave, battery BMS, CT location, or device addresses.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

MODEL = "SUN-20K-SG05LP3-EU-SM2"
EXPECTED_INVERTERS = 2
NOMINAL_W_PER_INVERTER = 20000
SIGNAL_KEYS = (
    "pv_power", "load_power", "grid_power", "battery_soc",
    "battery_power", "inverter_temperature", "pv_energy_today",
    "load_energy_today",
)


def describe_topology(registered: list[dict]) -> dict:
    """Return only database-grounded device identities.

    'expected' slots are intentionally NOT invented MySQL devices.
    """
    devices = []
    for item in registered:
        devices.append({
            "device_key": str(item["device_key"]),
            "model": str(item.get("model") or MODEL),
            "kind": str(item.get("kind") or "unknown"),
            "serial_number": item.get("serial_number"),
            "parallel_role": "unverified",
            "monitoring_transport": "unverified",
            "modbus_address": None,
            "connection_status": "unverified",
        })
    count = len(devices)
    return {
        "model": MODEL,
        "expected_inverters": EXPECTED_INVERTERS,
        "registered_inverters": count,
        "nominal_power_w_per_inverter": NOMINAL_W_PER_INVERTER,
        "nominal_power_w_total": NOMINAL_W_PER_INVERTER * EXPECTED_INVERTERS,
        "shared_battery": True,
        "battery_soc_source": "not_verified",
        "battery_soc_percent": None,
        "master_slave_verified": False,
        "rs485_topology_verified": False,
        "data_collection_active": False,
        "aggregate_verified": False,
        "aggregate_values": {},
        "aggregate_reason": (
            "Потрібно підтвердити два окремі джерела телеметрії, "
            "походження регістрів, розташування CT та загальний BMS. "
            "До того суми PV, навантаження, мережі й SOC не обчислюються."
        ),
        "completeness": (
            "registered" if count == EXPECTED_INVERTERS
            else "not_registered" if count == 0
            else "incomplete" if count < EXPECTED_INVERTERS
            else "ambiguous"
        ),
        "devices": devices,
    }


def verified_pv_sum(readings: list[dict], *, independently_measured: bool,
                    now_utc: datetime, max_age_seconds: int = 300,
                    max_skew_seconds: int = 120):
    """PV-only aggregation; explicit evidence must authorize both sources.

    Fail closed: duplicate/system-wide sources, stale, simulated, bad units,
    missing values, or asynchronous records never generate a total.
    Other electrical quantities MUST NOT reuse this function blindly.
    """
    if not independently_measured or len(readings) != EXPECTED_INVERTERS:
        return None
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must include a timezone")
    if (max_age_seconds <= 0 or max_skew_seconds < 0
            or max_skew_seconds > max_age_seconds):
        raise ValueError("Invalid freshness thresholds")
    timestamps = []
    values = []
    device_keys = set()
    for sample in readings:
        if sample.get("signal_key") != "pv_power":
            return None
        if sample.get("unit") not in {"кВт", "kW"}:
            return None
        if sample.get("quality") != "good":
            return None
        device_key = sample.get("device_key")
        if not isinstance(device_key, str) or not device_key.strip():
            return None
        if device_key in device_keys:
            return None
        device_keys.add(device_key)
        try:
            value = Decimal(str(sample["value_decimal"]))
        except (ValueError, TypeError, ArithmeticError):
            return None
        if not value.is_finite() or value < 0:
            return None
        stamp = sample.get("observed_at_utc")
        if isinstance(stamp, str):
            try:
                stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError:
                return None
        if not isinstance(stamp, datetime) or stamp.tzinfo is None:
            return None
        age = (now_utc - stamp).total_seconds()
        if age < -10 or age > max_age_seconds:
            return None
        values.append(value)
        timestamps.append(stamp)
    if (max(timestamps) - min(timestamps)).total_seconds() > max_skew_seconds:
        return None
    return {"value_decimal": str(sum(values)), "unit": "кВт",
            "quality": "aggregated_verified", "source_device_count": 2}
