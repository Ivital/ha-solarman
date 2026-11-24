"""Strict byte-level verification of read-only Modbus responses.

The caller MUST NOT treat a parsed JSON value as an instrument measurement
unless the bytes sent and received independently prove the same value.
This module sends no packets and has no network/device access.
"""
from __future__ import annotations

import struct


class InvalidModbusFrame(ValueError):
    pass


def crc16(payload: bytes) -> int:
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return crc


def _rtu_pdu(packet: bytes, slave: int) -> bytes:
    if len(packet) < 5 or packet[0] != slave:
        raise InvalidModbusFrame("RTU slave or frame length mismatch")
    if crc16(packet[:-2]) != int.from_bytes(packet[-2:], "little"):
        raise InvalidModbusFrame("RTU CRC16 mismatch")
    return packet[1:-2]


def _tcp_pdu(packet: bytes, slave: int):
    if len(packet) < 8:
        raise InvalidModbusFrame("Short Modbus/TCP ADU")
    transaction, protocol, length, unit = struct.unpack(">HHHB", packet[:7])
    if protocol != 0 or unit != slave or length != len(packet) - 6:
        raise InvalidModbusFrame("Invalid MBAP protocol/unit/length")
    return transaction, packet[7:]


def verify_read(*, transport: str, slave: int, fc: int, address: int,
                count: int, status: str, request: bytes, response: bytes | None,
                words: list[int], bits: list[int], wire_format: str | None = None):
    """Verify request and full successful reply against retained raw values.

    For errored/time-out requests, verify the request only. Exceptions with
    a response must have exact exception function and code; they never carry
    invented words. Uses strict RTU CRC or TCP MBAP framing, never guesses.
    """
    if transport == "modbus-tcp":
        fmt = "tcp"
    elif transport == "rs485":
        fmt = "rtu"
    elif transport == "gateway" and wire_format in ("tcp", "rtu"):
        fmt = wire_format
    else:
        raise InvalidModbusFrame("Unknown or ambiguous Modbus wire framing")
    if fmt == "rtu":
        request_pdu = _rtu_pdu(request, slave)
        response_pdu = _rtu_pdu(response, slave) if response is not None else None
    else:
        tid, request_pdu = _tcp_pdu(request, slave)
        if response is not None:
            reply_tid, response_pdu = _tcp_pdu(response, slave)
            if tid != reply_tid:
                raise InvalidModbusFrame("Modbus/TCP transaction mismatch")
        else:
            response_pdu = None
    if request_pdu != struct.pack(">BHH", fc, address, count):
        raise InvalidModbusFrame("Request function, address or count mismatch")
    if status == "ok":
        if response_pdu is None:
            raise InvalidModbusFrame("Missing successful Modbus response")
        expected_len = count * 2 if fc in (3, 4) else (count + 7) // 8
        if len(response_pdu) != expected_len + 2 or response_pdu[0] != fc or (
            response_pdu[1] != expected_len
        ):
            raise InvalidModbusFrame("Response function/byte count mismatch")
        data = response_pdu[2:]
        decoded = ([struct.unpack(">H", data[i:i + 2])[0]
                    for i in range(0, len(data), 2)]
                   if fc in (3, 4) else
                   [(data[i // 8] >> (i % 8)) & 1 for i in range(count)])
        if decoded != (words if fc in (3, 4) else bits):
            raise InvalidModbusFrame("Response bytes differ from recorded words/bits")
    elif status == "exception":
        if (response_pdu is None or len(response_pdu) != 2 or
                response_pdu[0] != (fc | 0x80)):
            raise InvalidModbusFrame("Malformed Modbus exception ADU")
    elif response_pdu is not None:
        # Never accept a success-looking response for an error/timeout row.
        if response_pdu and response_pdu[0] == fc:
            raise InvalidModbusFrame("Success response mislabeled as failure")
    return True
