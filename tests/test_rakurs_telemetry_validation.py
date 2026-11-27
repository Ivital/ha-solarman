"""Regression tests: malformed Modbus frames and unverified PV sums fail closed."""
from __future__ import annotations

import importlib.util
import struct
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "deploy" / "rakurs" / "standalone"


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wire = load("modbus_wire")
parallel = load("parallel_support")


def tcp_frame(pdu, transaction=42, slave=1):
    return struct.pack(">HHHB", transaction, 0, len(pdu) + 1, slave) + pdu


class ModbusValidationTests(unittest.TestCase):
    def params(self, **override):
        data = dict(
            transport="modbus-tcp", slave=1, fc=3, address=100,
            count=2, status="ok",
            request=tcp_frame(struct.pack(">BHH", 3, 100, 2)),
            response=tcp_frame(b"\x03\x04\x00\x05\x00\x06"),
            words=[5, 6], bits=[],
        )
        data.update(override)
        return data

    def test_valid_read(self):
        self.assertTrue(wire.verify_read(**self.params()))

    def test_rejects_invalid_fc(self):
        with self.assertRaises(wire.InvalidModbusFrame):
            wire.verify_read(**self.params(fc=6))

    def test_rejects_zero_or_excessive_count(self):
        for count in (0, 126, -1):
            with self.subTest(count=count), self.assertRaises(wire.InvalidModbusFrame):
                wire.verify_read(**self.params(count=count))

    def test_rejects_address_overflow(self):
        with self.assertRaises(wire.InvalidModbusFrame):
            wire.verify_read(**self.params(address=65535, count=2))

    def test_rejects_invalid_status(self):
        with self.assertRaises(wire.InvalidModbusFrame):
            wire.verify_read(**self.params(status="good"))

    def test_rejects_incomplete_or_invalid_words(self):
        for words in ([5], [5, 65536], [5, True]):
            with self.subTest(words=words), self.assertRaises(wire.InvalidModbusFrame):
                wire.verify_read(**self.params(words=words))

    def test_rejects_values_from_error(self):
        with self.assertRaises(wire.InvalidModbusFrame):
            wire.verify_read(**self.params(status="error", response=None))

    def test_rejects_response_from_timeout(self):
        with self.assertRaises(wire.InvalidModbusFrame):
            wire.verify_read(**self.params(status="timeout", words=[]))

    def test_rejects_wrong_transaction(self):
        with self.assertRaises(wire.InvalidModbusFrame):
            wire.verify_read(**self.params(response=tcp_frame(
                b"\x03\x04\x00\x05\x00\x06", transaction=43)))

    def test_valid_exception_with_no_values(self):
        args = self.params(status="exception", words=[],
                           exception_code=2, response=tcp_frame(b"\x83\x02"))
        self.assertTrue(wire.verify_read(**args))


class ParallelValidationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.readings = [
            dict(device_key="inverter_1", signal_key="pv_power", unit="кВт",
                 quality="good", value_decimal="5.1",
                 observed_at_utc=self.now - timedelta(seconds=10)),
            dict(device_key="inverter_2", signal_key="pv_power", unit="кВт",
                 quality="good", value_decimal="6.4",
                 observed_at_utc=self.now - timedelta(seconds=12)),
        ]

    def total(self, readings=None, **kw):
        return parallel.verified_pv_sum(
            self.readings if readings is None else readings,
            independently_measured=True, now_utc=self.now, **kw)

    def test_valid_unique_recent_sources(self):
        self.assertEqual(self.total()["value_decimal"], "11.5")

    def test_rejects_bad_quality(self):
        for quality in ("simulated", "bad", "stale", None):
            changed = [dict(x) for x in self.readings]
            changed[0]["quality"] = quality
            with self.subTest(quality=quality):
                self.assertIsNone(self.total(changed))

    def test_rejects_missing_and_duplicate_identity(self):
        for key in ("", None, "inverter_2"):
            changed = [dict(x) for x in self.readings]
            changed[0]["device_key"] = key
            with self.subTest(key=key):
                self.assertIsNone(self.total(changed))

    def test_rejects_untrusted_or_stale_samples(self):
        changed = [dict(x) for x in self.readings]
        changed[0]["observed_at_utc"] -= timedelta(minutes=10)
        self.assertIsNone(self.total(changed))
        changed = [dict(x) for x in self.readings]
        changed[0]["value_decimal"] = "NaN"
        self.assertIsNone(self.total(changed))

    def test_rejects_unverified_topology(self):
        self.assertIsNone(parallel.verified_pv_sum(
            self.readings, independently_measured=False, now_utc=self.now))


if __name__ == "__main__":
    unittest.main()
