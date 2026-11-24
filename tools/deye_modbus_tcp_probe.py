#!/usr/bin/env python3
"""One explicitly authorized FC03 Modbus/TCP read; no writes, scans or retries.

A successful TCP handshake ALONE does not show whether the endpoint is
Modbus TCP-over-serial or raw serial Over IP. This probe checks MBAP framing.
"""
from __future__ import annotations

import argparse
import ipaddress
import socket
import struct
import sys

TRANSACTION_ID = 0x4D31
READ_HOLDING_REGISTERS = 3


class ProtocolError(Exception):
    """Malformed or unexpected Modbus/TCP response."""


class ModbusException(Exception):
    """A valid Modbus/TCP exception response (not register data)."""

    def __init__(self, code: int):
        self.code = code
        super().__init__(f"Modbus exception 0x{code:02X}")


def build_fc03(unit: int, register: int) -> bytes:
    if not (1 <= unit <= 247 and 0 <= register <= 65535):
        raise ValueError("Unit/register out of range")
    # MBAP transaction, protocol=0, length=6, unit, function=3, start, count=1.
    return struct.pack(">HHHBBHH", TRANSACTION_ID, 0, 6, unit,
                       READ_HOLDING_REGISTERS, register, 1)


def parse_fc03_response(frame: bytes, unit: int) -> int:
    if len(frame) < 9:
        raise ProtocolError("Response shorter than Modbus/TCP minimum")
    transaction, protocol, length, response_unit = struct.unpack(">HHHB", frame[:7])
    if transaction != TRANSACTION_ID or protocol != 0 or response_unit != unit:
        raise ProtocolError("Unexpected transaction/protocol/unit in MBAP header")
    if length != len(frame) - 6:
        raise ProtocolError("Incorrect Modbus/TCP length")
    pdu = frame[7:]
    if len(pdu) == 2 and pdu[0] == (READ_HOLDING_REGISTERS | 0x80):
        raise ModbusException(pdu[1])
    if len(pdu) != 4 or pdu[:2] != b"\x03\x02":
        raise ProtocolError("Not a valid single-register FC03 response")
    return struct.unpack(">H", pdu[2:4])[0]


def read_exact(sock: socket.socket, amount: int) -> bytes:
    received = bytearray()
    while len(received) < amount:
        part = sock.recv(amount - len(received))
        if not part:
            raise ProtocolError("Connection closed before complete Modbus response")
        received.extend(part)
    return bytes(received)


def probe(host: str, port: int, unit: int, register: int, timeout: float) -> int:
    query = build_fc03(unit, register)
    with socket.create_connection((host, port), timeout=timeout) as conn:
        conn.settimeout(timeout)
        conn.sendall(query)
        header = read_exact(conn, 7)
        transaction, protocol, length, response_unit = struct.unpack(">HHHB", header)
        if transaction != TRANSACTION_ID or protocol != 0 or response_unit != unit:
            raise ProtocolError("Non-Modbus-TCP, unrelated or corrupt MBAP header")
        if not 3 <= length <= 253:
            raise ProtocolError(f"Invalid Modbus TCP length: {length}")
        frame = header + read_exact(conn, length - 1)
        return parse_fc03_response(frame, unit)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="Private IPv4 address of Teltonika")
    parser.add_argument("--port", type=int, default=50006)
    parser.add_argument("--unit", type=int, default=1)
    parser.add_argument("--register", type=lambda n: int(n, 0), default=0)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--execute", action="store_true",
                        help="Authorize ONE FC03 read with no retries")
    args = parser.parse_args()

    try:
        address = ipaddress.IPv4Address(args.host)
        if (not address.is_private or address.is_loopback or address.is_link_local
                or address.is_unspecified or address.is_multicast):
            raise ValueError("Private IPv4 endpoint required")
        if not 1 <= args.port <= 65535:
            raise ValueError("Port must be 1..65535")
        if not 1 <= args.unit <= 247:
            raise ValueError("Unit must be 1..247")
        if not 0 <= args.register <= 65535:
            raise ValueError("Register must be 0..65535")
        if not 1 <= args.timeout <= 20:
            raise ValueError("Timeout must be 1..20 seconds")
        query = build_fc03(args.unit, args.register)
    except ValueError as exc:
        parser.error(str(exc))

    print(f"Target={address}:{args.port}, unit={args.unit}, FC03, "
          f"register=0x{args.register:04X}, quantity=1")
    print("MBAP request (hex):", query.hex(" "))
    if not args.execute:
        print("DRY RUN: no network packets sent. Add --execute only after "
              "confirming Teltonika uses Modbus TCP over Serial Gateway.")
        return 0

    print("Sending ONE read-only request; no retries, no configuration changes.")
    try:
        result = probe(str(address), args.port, args.unit, args.register, args.timeout)
    except ModbusException as exc:
        print(f"MODBUS_TCP_EXCEPTION: {exc}; valid MBAP, but register/RS485 "
              "route or slave configuration is not yet confirmed.")
        return 3
    except (OSError, ProtocolError) as exc:
        print(f"NO_CONFIRMED_MODBUS_RESPONSE: {type(exc).__name__}: {exc}")
        print("Check Over IP versus Modbus TCP over Serial Gateway, Slave ID, "
              "RS485 wiring/baud and whether the inverter accepts this register.")
        return 2

    print(f"MODBUS_TCP_FC03_PASS: register 0x{args.register:04X} = "
          f"{result} (0x{result:04X}).")
    print("This verifies one Modbus/TCP read; device model and other registers "
          "still require independent validation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
