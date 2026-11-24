"""Lossless, append-only MySQL archive for every *received* Modbus response.

No sockets, no serial access, no poller, no Modbus writes. Payloads are supplied
by a separately verified read-only collector. Frames include request/response
bytes and decoded raw FC01/02 bits / FC03/04 register words, even if no
profile-field decoder exists. Only the caller controls the DB transaction.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re

try:
    from .modbus_wire import InvalidModbusFrame, verify_read
except ImportError:  # offline standalone importer used by archive CLI
    from modbus_wire import InvalidModbusFrame, verify_read

KEY = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
UID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
SOURCE = re.compile(r"^[a-zA-Z0-9_:-]{1,80}$")
READ_FUNCTIONS = frozenset({1, 2, 3, 4})


class PayloadError(ValueError):
    pass


def utc_time(value):
    if not isinstance(value, str):
        raise PayloadError("Expected ISO-8601 UTC timestamp")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PayloadError("Invalid ISO-8601 timestamp") from exc
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise PayloadError("Timestamp must be timezone-aware")
    return stamp.astimezone(timezone.utc).replace(tzinfo=None)


def hexbytes(value, required=False):
    if value is None and not required:
        return None
    if not isinstance(value, str) or len(value) % 2 or len(value) > 131072:
        raise PayloadError("Raw ADU must be even-length hexadecimal (max 64 KiB)")
    try:
        return bytes.fromhex(value)
    except ValueError as exc:
        raise PayloadError("Invalid raw ADU hexadecimal") from exc


def decimal_or_none(value):
    if value is None:
        return None
    try:
        n = Decimal(str(value))
    except (ValueError, TypeError, ArithmeticError) as exc:
        raise PayloadError("Invalid numeric value") from exc
    if not n.is_finite() or len(n.as_tuple().digits) > 24:
        raise PayloadError("Non-finite or oversized measurement")
    return n


def normalize(payload):
    if not isinstance(payload, dict):
        raise PayloadError("Modbus payload must be an object")
    device_key = payload.get("device_key")
    poll_uid = payload.get("poll_uid")
    quality = payload.get("quality")
    source = payload.get("ingest_source")
    transport = payload.get("transport")
    if not isinstance(device_key, str) or not KEY.fullmatch(device_key):
        raise PayloadError("Invalid device_key")
    if not isinstance(poll_uid, str) or not UID.fullmatch(poll_uid):
        raise PayloadError("Invalid poll_uid")
    if not isinstance(source, str) or not SOURCE.fullmatch(source):
        raise PayloadError("Invalid ingest_source")
    if quality not in {"simulated", "good", "unverified", "partial"}:
        raise PayloadError("Provenance quality required")
    if transport not in {"simulation", "rs485", "modbus-tcp", "gateway"}:
        raise PayloadError("Transport must be explicit")
    if (quality == "simulated") != (transport == "simulation"):
        raise PayloadError("Simulation must use simulation transport and quality")
    slave = payload.get("slave_id")
    if slave is not None and (type(slave) is not int or not 1 <= slave <= 247):
        raise PayloadError("Unverified or out-of-range slave_id")
    if quality != "simulated" and slave is None:
        raise PayloadError("Live Modbus read requires identified slave")
    observed = utc_time(payload.get("observed_at_utc"))
    if quality != "simulated" and (payload.get("fields") or payload.get("signals")):
        raise PayloadError(
            "Decoded live values are blocked until SG05LP3 firmware profile validation. "
            "Archive independently verified raw frames only."
        )
    frames = payload.get("frames")
    if not isinstance(frames, list) or not frames or len(frames) > 4096:
        raise PayloadError("At least one frame required, max 4096")
    normalized = []
    seen_sequences = set()
    for i, frame in enumerate(frames, 1):
        if not isinstance(frame, dict):
            raise PayloadError("Frame must be an object")
        seq = frame.get("sequence_no", i)
        fc = frame.get("function_code")
        addr = frame.get("start_address")
        status = frame.get("status", "simulated" if quality == "simulated" else "ok")
        count = frame.get("requested_count")
        if type(seq) is not int or seq < 1 or seq in seen_sequences:
            raise PayloadError("Frame sequence number must be unique and positive")
        seen_sequences.add(seq)
        if type(fc) is not int or fc not in READ_FUNCTIONS:
            raise PayloadError("Only Modbus read functions FC01-FC04 accepted")
        limit = 2000 if fc in (1, 2) else 125
        if type(count) is not int or not 1 <= count <= limit:
            raise PayloadError("Invalid Modbus read length")
        if type(addr) is not int or addr < 0 or addr + count > 65536:
            raise PayloadError("Address range must fit Modbus")
        if status not in {"ok", "simulated", "timeout", "exception", "error"}:
            raise PayloadError("Invalid frame status")
        if (status == "simulated") != (quality == "simulated"):
            raise PayloadError("Frame provenance mismatch")
        words = frame.get("words", [])
        bits = frame.get("bits", [])
        if not isinstance(words, list) or not isinstance(bits, list):
            raise PayloadError("Words and bits must be arrays")
        if status in {"ok", "simulated"}:
            values = words if fc in (3, 4) else bits
            unused = bits if fc in (3, 4) else words
            if len(values) != count or unused:
                raise PayloadError("Every returned bit/word must be retained")
            for number in values:
                upper = 65535 if fc in (3, 4) else 1
                if type(number) is not int or not 0 <= number <= upper:
                    raise PayloadError("Invalid bit/register value")
        elif words or bits:
            raise PayloadError("Errored frame must not invent returned register values")
        request = hexbytes(frame.get("request_hex"), required=quality != "simulated")
        response = hexbytes(frame.get("response_hex"),
                            required=quality != "simulated" and status in ("ok", "exception"))
        if quality != "simulated" and (not request or (status == "ok" and not response)):
            raise PayloadError("Real poll must retain complete request and successful response ADU")
        exception_code = frame.get("exception_code")
        if exception_code is not None and (
            type(exception_code) is not int or not 0 <= exception_code <= 255
        ):
            raise PayloadError("Invalid exception code")
        if quality != "simulated":
            try:
                verify_read(
                    transport=transport, slave=slave, fc=fc, address=addr,
                    count=count, status=status, request=request, response=response,
                    words=words, bits=bits, wire_format=payload.get("wire_format"),
                    exception_code=exception_code,
                )
            except InvalidModbusFrame as exc:
                raise PayloadError("Unverified Modbus wire bytes: " + str(exc)) from exc
        error_code = frame.get("error_code")
        if error_code is not None and (
            not isinstance(error_code, str) or len(error_code) > 80
        ):
            raise PayloadError("Invalid error code")
        captured = (utc_time(frame["captured_at_utc"])
                    if frame.get("captured_at_utc") else observed)
        if abs((captured - observed).total_seconds()) > 3600:
            raise PayloadError("Frame timestamp is more than 1 hour from poll timestamp")
        normalized.append({
            "captured": captured,
            "sequence_no": seq, "function_code": fc, "start_address": addr,
            "requested_count": count, "status": status, "words": words, "bits": bits,
            "request_bytes": request, "response_bytes": response,
            "exception_code": exception_code, "error_code": error_code,
        })
    fields = payload.get("fields", [])
    signals = payload.get("signals", [])
    if not isinstance(fields, list) or not isinstance(signals, list):
        raise PayloadError("Optional decoded fields/signals must be lists")
    for field in fields:
        if not isinstance(field, dict) or not KEY.fullmatch(str(field.get("field_key", ""))):
            raise PayloadError("Invalid decoded field key")
        decimal_or_none(field.get("value_decimal"))
        if field.get("value_text") is not None and (
            not isinstance(field["value_text"], str) or len(field["value_text"]) > 512
        ):
            raise PayloadError("Invalid text field")
    for sig in signals:
        if not isinstance(sig, dict) or not KEY.fullmatch(str(sig.get("signal_key", ""))):
            raise PayloadError("Invalid signal key")
        decimal_or_none(sig.get("value_decimal"))
    return dict(device_key=device_key, poll_uid=poll_uid, quality=quality,
                source=source, transport=transport, slave_id=slave, observed=observed,
                frames=normalized, fields=fields, signals=signals)


def payload_fingerprint(item: dict) -> str:
    """Canonical hash protects retries from silently swallowing different polls."""
    def stable(obj):
        if isinstance(obj, bytes):
            return obj.hex()
        if isinstance(obj, datetime):
            return obj.isoformat(timespec="microseconds")
        if isinstance(obj, Decimal):
            return str(obj)
        raise TypeError(type(obj).__name__)
    return sha256(json.dumps(
        item, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        allow_nan=False, default=stable,
    ).encode("utf-8")).hexdigest()


def persist(db, payload, *, manage_transaction=True):
    """Atomic archive. Real devices must be verified before accepting live data.

    Database connection must use autocommit=False and a deliberately authorized
    ingest account. Returns an idempotent receipt, never alters catalog validity.
    """
    item = normalize(payload)
    fingerprint = payload_fingerprint(item) if item["quality"] != "simulated" else None
    if manage_transaction:
        db.begin()
    try:
        with db.cursor() as cur:
            cur.execute(
                "SELECT id, kind FROM rakurs_devices WHERE device_key=%s FOR UPDATE",
                (item["device_key"],),
            )
            device = cur.fetchone()
            if not device:
                raise PayloadError("Device is not registered")
            if isinstance(device, dict):
                device_id, kind = device["id"], device["kind"]
            else:
                device_id, kind = device
            if item["quality"] == "simulated":
                if kind != "simulation":
                    raise PayloadError("Synthetic data requires a simulation device")
            else:
                if kind != "inverter":
                    raise PayloadError("Live data cannot use simulation/planned device")
                cur.execute(
                    "SELECT modbus_address FROM rakurs_parallel_members "
                    "WHERE device_id=%s AND member_kind='physical' "
                    "AND member_verified=1 AND transport_verified=1 LIMIT 1",
                    (device_id,),
                )
                member = cur.fetchone()
                verified_slave = (member["modbus_address"] if isinstance(member, dict)
                                  else member[0] if member else None)
                if verified_slave is None or verified_slave != item["slave_id"]:
                    raise PayloadError(
                        "Physical telemetry blocked: source slave ID is not "
                        "identical to the verified member Modbus address"
                    )
            cur.execute(
                "SELECT id, device_id FROM rakurs_modbus_poll_runs WHERE poll_uid=%s",
                (item["poll_uid"],),
            )
            previous = cur.fetchone()
            if previous:
                prev_id, prev_device = ((previous["id"], previous["device_id"])
                                        if isinstance(previous, dict) else previous)
                if prev_device != device_id:
                    raise PayloadError("Poll UID already belongs to another device")
                if fingerprint is not None:
                    cur.execute(
                        "SELECT payload_sha256 FROM rakurs_modbus_poll_fingerprints "
                        "WHERE poll_id=%s FOR UPDATE", (prev_id,),
                    )
                    saved = cur.fetchone()
                    stored_hash = (saved["payload_sha256"] if isinstance(saved, dict)
                                   else saved[0] if saved else None)
                    if stored_hash != fingerprint:
                        raise PayloadError(
                            "Poll UID collision or legacy record without fingerprint"
                        )
                if manage_transaction:
                    db.rollback()
                return {"status": "already_saved", "poll_id": prev_id}
            frames = item["frames"]
            responses = sum(f["status"] in {"ok", "simulated"} for f in frames)
            exceptions = len(frames) - responses
            cur.execute(
                "INSERT INTO rakurs_modbus_poll_runs "
                "(device_id,poll_uid,observed_at_utc,finished_at_utc,transport,"
                "slave_id,status,quality,ingest_source,request_count,response_count,"
                "exception_count,created_at_utc) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (device_id, item["poll_uid"], item["observed"],
                 max(x["captured"] for x in frames),
                 item["transport"], item["slave_id"],
                 ("simulated" if item["quality"] == "simulated"
                  else "complete" if exceptions == 0 else "partial"),
                 item["quality"], item["source"], len(frames), responses,
                 exceptions, item["observed"]),
            )
            poll_id = cur.lastrowid
            raw_registers = 0
            raw_bits = 0
            for frame in frames:
                cur.execute(
                    "INSERT INTO rakurs_modbus_frames "
                    "(poll_id,device_id,sequence_no,captured_at_utc,function_code,"
                    "start_address,requested_count,status,exception_code,error_code,"
                    "request_bytes,response_bytes) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (poll_id, device_id, frame["sequence_no"], frame["captured"],
                     frame["function_code"], frame["start_address"],
                     frame["requested_count"], frame["status"],
                     frame["exception_code"], frame["error_code"],
                     frame["request_bytes"], frame["response_bytes"]),
                )
                frame_id = cur.lastrowid
                common = (poll_id, frame_id, device_id, frame["captured"],
                          frame["function_code"])
                if frame["words"]:
                    cur.executemany(
                        "INSERT INTO rakurs_modbus_register_samples "
                        "(poll_id,frame_id,device_id,observed_at_utc,function_code,"
                        "register_address,raw_uint16,quality,ingest_source) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        [common + (frame["start_address"] + k, word,
                                   item["quality"], item["source"])
                         for k, word in enumerate(frame["words"])],
                    )
                    raw_registers += len(frame["words"])
                if frame["bits"]:
                    cur.executemany(
                        "INSERT INTO rakurs_modbus_bit_samples "
                        "(poll_id,frame_id,device_id,observed_at_utc,function_code,"
                        "bit_address,bit_value,quality,ingest_source) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        [common + (frame["start_address"] + k, value,
                                   item["quality"], item["source"])
                         for k, value in enumerate(frame["bits"])],
                    )
                    raw_bits += len(frame["bits"])
            for record in item["fields"]:
                cur.execute("SELECT id FROM rakurs_modbus_fields WHERE field_key=%s",
                            (record["field_key"],))
                match = cur.fetchone()
                if not match:
                    raise PayloadError("Unknown decoded field: " + record["field_key"])
                field_id = match["id"] if isinstance(match, dict) else match[0]
                cur.execute(
                    "INSERT INTO rakurs_modbus_field_samples "
                    "(device_id,field_id,observed_at_utc,received_at_utc,"
                    "value_decimal,value_text,raw_registers_json,quality,ingest_source) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (device_id, field_id, item["observed"], item["observed"],
                     decimal_or_none(record.get("value_decimal")),
                     record.get("value_text"),
                     json.dumps(record.get("raw_registers")) if
                     record.get("raw_registers") is not None else None,
                     item["quality"], item["source"]),
                )
            for record in item["signals"]:
                cur.execute("SELECT id FROM rakurs_signals WHERE signal_key=%s",
                            (record["signal_key"],))
                match = cur.fetchone()
                if not match:
                    raise PayloadError("Unknown dashboard signal: " + record["signal_key"])
                signal_id = match["id"] if isinstance(match, dict) else match[0]
                cur.execute(
                    "INSERT INTO rakurs_samples "
                    "(device_id,signal_id,observed_at_utc,received_at_utc,"
                    "value_decimal,raw_value,quality,ingest_source) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (device_id, signal_id, item["observed"], item["observed"],
                     decimal_or_none(record.get("value_decimal")),
                     record.get("raw_value"), item["quality"], item["source"]),
                )
            if fingerprint is not None:
                cur.execute(
                    "INSERT INTO rakurs_modbus_poll_fingerprints "
                    "(poll_id,payload_sha256,created_at_utc) "
                    "VALUES (%s,%s,UTC_TIMESTAMP(6))",
                    (poll_id, fingerprint),
                )
        if manage_transaction:
            db.commit()
        return {"status": "saved", "poll_id": poll_id, "frames": len(frames),
                "raw_registers": raw_registers, "raw_bits": raw_bits,
                "decoded_fields": len(item["fields"]),
                "dashboard_signals": len(item["signals"])}
    except Exception:
        if manage_transaction:
            db.rollback()
        raise
