"""Offline regression suite for the one-shot read-only Modbus TCP probe."""
from __future__ import annotations

import runpy
import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "deye_modbus_tcp_probe.py"

class DeyeModbusProtocolProbeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ns = runpy.run_path(str(SCRIPT))
        cls.build = staticmethod(ns["build_fc03"])
        cls.parse = staticmethod(ns["parse_fc03_response"])
        cls.ProtocolError = ns["ProtocolError"]
        cls.ModbusException = ns["ModbusException"]
        cls.tid = ns["TRANSACTION_ID"]

    def test_fc03_request_mbap(self):
        expected = bytes.fromhex("4d 31 00 00 00 06 01 03 00 00 00 01")
        self.assertEqual(self.build(1, 0), expected)

    def test_invalid_unit_is_rejected(self):
        for unit in (0, 248):
            with self.assertRaises(ValueError):
                self.build(unit, 0)

    def test_parse_valid_fc03(self):
        raw = struct.pack(">HHHB", self.tid, 0, 5, 1) + bytes.fromhex("03 02 00 05")
        self.assertEqual(self.parse(raw, 1), 5)

    def test_modbus_exception_is_distinct(self):
        raw = struct.pack(">HHHB", self.tid, 0, 3, 1) + bytes.fromhex("83 02")
        with self.assertRaises(self.ModbusException) as ctx:
            self.parse(raw, 1)
        self.assertEqual(ctx.exception.code, 2)

    def test_reject_writer_reply_and_bad_mbap(self):
        good = struct.pack(">HHHB", self.tid, 0, 5, 1)
        for frame in (
            good + bytes.fromhex("06 02 00 05"),
            struct.pack(">HHHB", self.tid, 1, 5, 1) + bytes.fromhex("03 02 00 05"),
            struct.pack(">HHHB", self.tid, 0, 5, 2) + bytes.fromhex("03 02 00 05"),
            struct.pack(">HHHB", self.tid, 0, 6, 1) + bytes.fromhex("03 02 00 05"),
            b"bad",
        ):
            with self.subTest(frame=frame), self.assertRaises(self.ProtocolError):
                self.parse(frame, 1)
