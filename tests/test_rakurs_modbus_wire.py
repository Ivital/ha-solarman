"""Byte-accurate safety regression tests. No device, network or MySQL access."""
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] /
                       "deploy" / "rakurs" / "standalone"))
from modbus_wire import crc16, verify_read, InvalidModbusFrame
from modbus_archive import normalize, PayloadError, payload_fingerprint


def rtu(hex_value):
    p = bytes.fromhex(hex_value)
    return p + struct.pack("<H", crc16(p))


def tcp(pdu, transaction=0x1234, unit=1):
    return struct.pack(">HHHB", transaction, 0, 1 + len(pdu), unit) + pdu


class WireSafetyTests(unittest.TestCase):
    def read(self, *, transport="rs485", request=None, response=None,
             fc=3, address=1234, count=2, words=None, bits=None, status="ok",
             **other):
        return verify_read(
            transport=transport, slave=1, fc=fc, address=address, count=count,
            status=status, request=request or rtu("010304d20002"),
            response=response or rtu("0103040001ffff"),
            words=[1, 65535] if words is None else words,
            bits=[] if bits is None else bits, **other)

    def test_crc16_standard_vector(self):
        self.assertEqual(crc16(bytes.fromhex("010300000001")), 0x0A84)

    def test_rtu_holding_register_success(self):
        self.assertTrue(self.read())

    def test_reject_any_corruption_and_mismatched_values(self):
        valid = rtu("0103040001ffff")
        for reply in (valid[:-1] + bytes((valid[-1] ^ 1,)),
                      rtu("0203040001ffff"), rtu("01030400010000"),
                      rtu("0103020001"), rtu("0104040001ffff")):
            with self.subTest(reply=reply), self.assertRaises(InvalidModbusFrame):
                self.read(response=reply)
        with self.assertRaises(InvalidModbusFrame):
            self.read(words=[2, 65535])

    def test_discrete_inputs_lsb_first(self):
        request = rtu("010200640009")
        reply = rtu("0102025501")
        self.assertTrue(self.read(
            fc=2, address=100, count=9, request=request, response=reply,
            words=[], bits=[1,0,1,0,1,0,1,0,1]))

    def test_modbus_tcp_mbap_and_transaction(self):
        req = tcp(bytes.fromhex("0304d20002"))
        resp = tcp(bytes.fromhex("03040001ffff"))
        self.assertTrue(self.read(transport="modbus-tcp",request=req,response=resp))
        with self.assertRaises(InvalidModbusFrame):
            self.read(transport="modbus-tcp",request=req,
                      response=tcp(bytes.fromhex("03040001ffff"),transaction=123))

    def test_gateway_requires_explicit_wire_format(self):
        with self.assertRaises(InvalidModbusFrame):
            self.read(transport="gateway")
        self.assertTrue(self.read(transport="gateway",wire_format="rtu"))

    def test_exception_must_have_exact_provenance(self):
        req = rtu("010304d20002")
        exc = rtu("018302")
        self.assertTrue(self.read(status="exception",request=req,
                                  response=exc,words=[],exception_code=2))
        with self.assertRaises(InvalidModbusFrame):
            self.read(status="exception",request=req,response=exc,
                      words=[],exception_code=4)

    def test_normalize_rejects_unvalidated_derived_live_data(self):
        p = {
            "device_key":"deye_unit", "poll_uid":"valid_20261125_1",
            "quality":"good", "ingest_source":"trusted_collector",
            "transport":"rs485", "slave_id":1,
            "observed_at_utc":"2026-11-25T00:00:00Z",
            "frames":[{"function_code":3,"start_address":1234,"requested_count":2,
                       "words":[1,65535],"status":"ok",
                       "request_hex":rtu("010304d20002").hex(),
                       "response_hex":rtu("0103040001ffff").hex()}]
        }
        normalized = normalize(p)
        self.assertEqual(normalized["frames"][0]["words"], [1,65535])
        self.assertEqual(payload_fingerprint(normalized),
                         payload_fingerprint(normalize(p)))
        changed = {**p,"frames":[{**p["frames"][0],
                   "words":[1,65534],"response_hex":rtu("0103040001fffe").hex()}]}
        self.assertNotEqual(payload_fingerprint(normalized),
                            payload_fingerprint(normalize(changed)))
        with self.assertRaises(PayloadError):
            normalize({**p, "fields":[{"field_key":"p3_0001","value_decimal":1}]})
        with self.assertRaises(PayloadError):
            normalize({**p, "frames":[{**p["frames"][0],"words":[1,65534]}]})


class ReplayFingerprintTests(unittest.TestCase):
    def test_same_uid_requires_identical_payload_sha256(self):
        from modbus_archive import persist
        payload = {
            "device_key": "deye_physical_1",
            "poll_uid": "real_poll_20261125_01",
            "quality": "good",
            "ingest_source": "verified_gateway",
            "transport": "rs485", "slave_id": 1,
            "observed_at_utc": "2026-11-25T00:00:00Z",
            "frames": [{"function_code":3,"start_address":1234,
                        "requested_count":2,"status":"ok",
                        "words":[1,65535],
                        "request_hex":rtu("010304d20002").hex(),
                        "response_hex":rtu("0103040001ffff").hex()}],
        }
        canonical = payload_fingerprint(normalize(payload))

        class Fake:
            def __init__(self, saved_hash):
                self.saved_hash = saved_hash
                self.sql = ""
                self.rolled_back = 0
            def begin(self): pass
            def rollback(self): self.rolled_back += 1
            def cursor(self): return self
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def execute(self, sql, _params=()): self.sql = sql
            def fetchone(self):
                if "FROM rakurs_devices" in self.sql:
                    return (1, "inverter")
                if "FROM rakurs_parallel_members" in self.sql:
                    return (1,)
                if "FROM rakurs_modbus_poll_runs" in self.sql:
                    return (77, 1)
                if "FROM rakurs_modbus_poll_fingerprints" in self.sql:
                    return ((self.saved_hash,) if self.saved_hash else None)
                raise AssertionError(self.sql)
        equal = Fake(canonical)
        result = persist(equal, payload)
        self.assertEqual(result, {"status":"already_saved","poll_id":77})
        self.assertEqual(equal.rolled_back, 1)
        for digest in ("0"*64, None):
            mismatch = Fake(digest)
            with self.assertRaises(PayloadError):
                persist(mismatch, payload)
            self.assertEqual(mismatch.rolled_back, 1)


if __name__ == "__main__":
    unittest.main()
