"""Fail-closed scheduled Modbus read collector for two commissioned Deye units.

Disabled by default. No FC05/06/15/16 and no inverter configuration writes.
Only one bounded FC03/04 probe per verified device per interval; preserves
complete wire bytes and raw words using the existing archive pipeline.
Decode to dashboard signals is intentionally blocked until verified mapping.
"""
from __future__ import annotations

import argparse
import ipaddress
import os
import re
import secrets
import socket
import stat
import struct
import time
from datetime import datetime, timezone
from pathlib import Path

import pymysql

from .config_api import validate_ip
from .modbus_archive import persist
from .modbus_wire import crc16, verify_read
from .mysql_transport import mysql_tls_options

DB_HOST="192.168.5.30"
SCHEMA="rakurs"


def secret():
    path=os.environ.get("RAKURS_INGEST_PASSWORD_FILE")
    if not path:
        raise RuntimeError("Missing dedicated ingest password file")
    p=Path(path)
    st=p.stat()
    if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077 or st.st_uid!=os.geteuid():
        raise RuntimeError("Insecure ingest credential file")
    value=p.read_text(encoding="utf-8").rstrip("\r\n")
    if not value:
        raise RuntimeError("Empty ingest password")
    return value


def connect():
    user=os.environ.get("RAKURS_INGEST_DB_USER","")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,32}",user):
        raise RuntimeError("Dedicated ingest MySQL user required")
    tls=mysql_tls_options(require_verified=True)
    return pymysql.connect(
        host=DB_HOST,port=3306,database=SCHEMA,user=user,password=secret(),
        autocommit=True,charset="utf8mb4",cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=5,read_timeout=10,write_timeout=5,**tls)


def recv_exact(sock,size):
    if size<1 or size>260:
        raise RuntimeError("Invalid response length")
    data=b""
    while len(data)<size:
        packet=sock.recv(size-len(data))
        if not packet:
            raise ConnectionError("Unexpected end of Modbus reply")
        data+=packet
    return data


def make_read(transport,slave,fc,address,count,tx=1):
    if transport not in ("modbus_tcp","rtu_tcp") or type(slave) is not int or not 1<=slave<=247:
        raise ValueError("Unverified transport/slave")
    if fc not in (3,4) or type(address) is not int or type(count) is not int:
        raise ValueError("Only bounded FC03/FC04 reads")
    if not 0<=address<=65535 or not 1<=count<=8 or address+count>65536:
        raise ValueError("Out-of-range Modbus probe")
    pdu=struct.pack(">BHH",fc,address,count)
    if transport=="modbus_tcp":
        return struct.pack(">HHHB",tx,0,1+len(pdu),slave)+pdu
    raw=bytes([slave])+pdu
    return raw+crc16(raw).to_bytes(2,"little")


def poll_probe(config):
    host=validate_ip(config["gateway_ip"])
    if not host:
        raise RuntimeError("No gateway IP configured")
    allow=os.environ.get("RAKURS_ALLOWED_GATEWAY_IPS","")
    permitted={str(ipaddress.ip_address(x.strip())) for x in allow.split(",") if x.strip()}
    if host not in permitted:
        raise PermissionError("Gateway not in explicit operator allowlist")
    port=int(config["gateway_port"])
    if not 1<=port<=65535:
        raise ValueError("Invalid gateway port")
    transport=config["transport"]
    slave=int(config["slave_id"])
    fc=int(config["function_code"])
    addr=int(config["register_address"])
    count=int(config["register_count"])
    timeout=int(config["timeout_ms"])/1000
    request=make_read(transport,slave,fc,addr,count,
                      tx=secrets.randbelow(65535)+1)
    captured=datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
    raw_reply=None
    status="ok"
    exception_code=None
    try:
        with socket.create_connection((host,port),timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(request)
            if transport=="modbus_tcp":
                header=recv_exact(sock,7)
                size=int.from_bytes(header[4:6],"big")
                if size<3 or size>19:
                    raise ValueError("Unexpected MBAP response length")
                raw_reply=header+recv_exact(sock,size-1)
                pdu=raw_reply[7:]
            else:
                prefix=recv_exact(sock,3)
                if prefix[1]==(fc|0x80):
                    raw_reply=prefix+recv_exact(sock,2)
                    pdu=raw_reply[1:-2]
                else:
                    byte_count=prefix[2]
                    if byte_count != count*2:
                        raise ValueError("RTU byte count mismatch")
                    raw_reply=prefix+recv_exact(sock,byte_count+2)
                    pdu=raw_reply[1:-2]
            if pdu[0]==(fc|0x80):
                status="exception"
                exception_code=pdu[1] if len(pdu)==2 else None
                words=[]
            else:
                if len(pdu)!=2+count*2 or pdu[0]!=fc or pdu[1]!=count*2:
                    raise ValueError("Read reply function/count mismatch")
                words=list(struct.unpack(">"+("H"*count),pdu[2:]))
            verify_read(
                transport="modbus-tcp" if transport=="modbus_tcp" else "gateway",
                wire_format="rtu" if transport=="rtu_tcp" else None,
                slave=slave,fc=fc,address=addr,count=count,status=status,
                request=request,response=raw_reply,words=words,bits=[],
                exception_code=exception_code)
    except socket.timeout:
        status="timeout"
        raw_reply=None
        words=[]
    # Network errors other than timeout bubble up, and never produce a
    # fabricated successful frame. Invalid replies are never archived as good.
    return {
        "device_key":config["device_key"],
        "poll_uid":"poll_"+secrets.token_hex(16),
        "observed_at_utc":captured,
        "quality":"good" if status=="ok" else "partial",
        "transport":"modbus-tcp" if transport=="modbus_tcp" else "gateway",
        "wire_format":"rtu" if transport=="rtu_tcp" else None,
        "slave_id":slave, "ingest_source":"rakurs_poll_worker",
        "frames":[{
            "sequence_no":1,"captured_at_utc":captured,
            "function_code":fc,"start_address":addr,
            "requested_count":count,"status":status,
            "words":words,"bits":[], "request_hex":request.hex(),
            "response_hex":raw_reply.hex() if raw_reply is not None else None,
            "exception_code":exception_code
        }],
        "fields":[],"signals":[]
    }


def exclude_conflicting_buses(devices):
    """Fail closed when two physical units share ID or gateway framing."""
    grouped = {}
    for device in devices:
        key = (device["gateway_ip"], int(device["gateway_port"]))
        grouped.setdefault(key, []).append(device)
    blocked = set()
    for endpoint, members in grouped.items():
        modes = {d["transport"] for d in members}
        ids = [int(d["slave_id"]) for d in members]
        if len(modes) != 1 or len(ids) != len(set(ids)):
            blocked.add(endpoint)
    return [d for d in devices
            if (d["gateway_ip"], int(d["gateway_port"])) not in blocked]


def ready_devices(db):
    with db.cursor() as cur:
        cur.execute(
            "SELECT d.id,d.device_key,d.kind,c.transport,c.gateway_ip,"
            "c.gateway_port,c.slave_id,c.function_code,c.register_address,"
            "c.register_count,c.timeout_ms,pm.modbus_address,"
            "c.enabled,c.verified_connection,pm.member_kind,"
            "pm.member_verified,pm.transport_verified "
            "FROM rakurs_device_settings AS c "
            "JOIN rakurs_devices AS d ON d.id=c.device_id "
            "JOIN rakurs_parallel_members AS pm ON pm.device_id=d.id "
            "JOIN rakurs_parallel_groups AS pg ON pg.id=pm.group_id "
            "WHERE c.enabled=1 AND c.verified_connection=1 "
            "AND pm.member_kind='physical' AND pm.member_verified=1 "
            "AND pm.transport_verified=1 AND d.kind='inverter' "
            "AND pg.group_key='deye_sun20k_parallel_shared_battery' "
            "AND pm.slot_no IN (1,2) ORDER BY pm.slot_no")
        items=cur.fetchall()
    valid=[]
    for item in items:
        try:
            host=validate_ip(item["gateway_ip"])
            allowed={x.strip() for x in os.getenv("RAKURS_ALLOWED_GATEWAY_IPS","").split(",")}
            if (item.get("kind") == "inverter" and
                    item.get("enabled") == 1 and item.get("verified_connection") == 1 and
                    item.get("member_kind") == "physical" and
                    item.get("member_verified") == 1 and
                    item.get("transport_verified") == 1 and
                    host in allowed and item["transport"] in ("modbus_tcp","rtu_tcp")
                    and 1 <= int(item["gateway_port"]) <= 65535 and
                    1 <= int(item["slave_id"]) <= 247 and
                    item["slave_id"]==item["modbus_address"] and
                    1<=int(item["register_count"])<=8):
                valid.append(item)
        except (ValueError,TypeError):
            continue
    # Sequential poller: never issue ambiguous requests to one RS485 bus.
    return exclude_conflicting_buses(valid)


def runtime(db,state,*,device=None,error=None,attempt=False,success=False):
    with db.cursor() as cur:
        cur.execute(
            "UPDATE rakurs_poll_runtime SET worker_heartbeat_utc=UTC_TIMESTAMP(6),"
            "state=COALESCE(%s,state),last_device_id=COALESCE(%s,last_device_id),"
            "last_error=CASE WHEN %s THEN NULL "
            "WHEN %s IS NOT NULL THEN %s ELSE last_error END,"
            "last_attempt_utc=IF(%s,UTC_TIMESTAMP(6),last_attempt_utc),"
            "last_success_utc=IF(%s,UTC_TIMESTAMP(6),last_success_utc) "
            "WHERE id=1",
            (state,device,bool(success or state=="disabled"),error,error,
             bool(attempt),bool(success)))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run",action="store_true")
    args=parser.parse_args()
    if not args.run:
        print("POLLER_PREFLIGHT=PASS")
        print("READ_FUNCTIONS=FC03,FC04")
        print("HARDWARE_COMMANDS=0 MYSQL_WRITES=0")
        return 0
    if os.getenv("RAKURS_POLL_COMMISSIONED")!="YES":
        raise RuntimeError("Physical channel commissioning required")
    if not os.getenv("RAKURS_ALLOWED_GATEWAY_IPS"):
        raise RuntimeError("Explicit gateway allowlist is empty")
    db=connect()
    last_run=0.0
    try:
        while True:
            with db.cursor() as cur:
                cur.execute("SELECT enabled,interval_seconds "
                            "FROM rakurs_poll_settings WHERE id=1")
                cfg=cur.fetchone()
            if not cfg:
                raise RuntimeError("Polling configuration schema unavailable")
            if not bool(cfg["enabled"]):
                runtime(db,"disabled")
                last_run=0.0
            else:
                devices=ready_devices(db)
                if not devices:
                    runtime(db,"blocked",error="NO_VERIFIED_GATEWAY")
                elif time.monotonic()-last_run >= int(cfg["interval_seconds"]):
                    last_run=time.monotonic()
                    failures=0
                    completed=0
                    interrupted=False
                    for device in devices:
                        # Re-read enabled switch before EVERY hardware request.
                        with db.cursor() as cur:
                            cur.execute("SELECT enabled FROM rakurs_poll_settings WHERE id=1")
                            switch=cur.fetchone()
                        if not switch or not switch["enabled"]:
                            interrupted=True
                            break
                        # Device-level disable/config changes must also be
                        # enforced immediately before the network request.
                        fresh=next((d for d in ready_devices(db)
                                    if d["id"]==device["id"]),None)
                        if fresh is None:
                            failures+=1
                            continue
                        runtime(db,"probing",device=fresh["id"],attempt=True)
                        try:
                            payload=poll_probe(fresh)
                            result=persist(db,payload)
                            if result["status"]=="saved" and payload["quality"]=="good":
                                completed+=1
                                runtime(db,"probing",device=device["id"],success=True)
                            else:
                                failures+=1
                                runtime(db,"error",device=device["id"],error="READ_UNSUCCESSFUL")
                        except (OSError,ValueError,RuntimeError,PermissionError) as error:
                            failures+=1
                            runtime(db,"error",device=device["id"],
                                    error=type(error).__name__[:96])
                    if interrupted:
                        runtime(db,"disabled")
                    elif failures or completed!=len(devices):
                        runtime(db,"error",error="INCOMPLETE_POLL_CYCLE")
                    else:
                        runtime(db,"active")
                else:
                    # Heartbeat must not overwrite a failed cycle.
                    runtime(db,None)
            time.sleep(2)
    finally:
        db.close()


if __name__=="__main__":
    main()
