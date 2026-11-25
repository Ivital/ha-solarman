"""Configuration-plane and read-only poller regressions: no external I/O."""
from __future__ import annotations
import os
import sys
import struct
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from flask import Flask, g

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT / "deploy" / "rakurs"))
from standalone.config_api import validate_ip, valid_device, register
from standalone.poll_worker import make_read, poll_probe, ready_devices
from standalone.modbus_wire import crc16
from standalone.modbus_archive import normalize


def config_payload(**updates):
    row={"display_name":"Deye №1","model":"SUN-20K-SG05LP3-EU-SM2",
         "serial_number":None,"transport":"modbus_tcp",
         "gateway_ip":"192.168.5.21","gateway_port":502,"slave_id":1,
         "function_code":3,"register_address":12,"register_count":2,
         "timeout_ms":1500,"enabled":False}
    row.update(updates)
    return row


class ValidationTests(unittest.TestCase):
    def test_transport_and_ranges_are_bounded(self):
        self.assertEqual(valid_device(config_payload())["gateway_ip"],"192.168.5.21")
        for ip in ("8.8.8.8","127.0.0.1","169.254.10.2","224.0.0.1","::1"):
            with self.subTest(ip=ip),self.assertRaises(ValueError):
                validate_ip(ip)
        for changes in (
            {"transport":"ssh"},{"function_code":6},{"function_code":16},
            {"register_count":125},{"register_address":65535,"register_count":2},
            {"slave_id":248},{"gateway_port":70000},{"timeout_ms":100},
            {"enabled":True,"gateway_ip":None},
            {"enabled":"true"},{"display_name":"Deye\ninvalid"},
            {"model":"bad\x00model"},
        ):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                valid_device(config_payload(**changes))

    def test_read_only_wire_protocol(self):
        self.assertEqual(make_read("modbus_tcp",1,3,12,2,tx=4),
                         bytes.fromhex("0004000000060103000c0002"))
        self.assertEqual(make_read("rtu_tcp",1,4,12,1)[:6],
                         bytes.fromhex("0104000c0001"))
        for params in (
            ("modbus_tcp",1,6,12,2),("modbus_tcp",1,16,12,2),
            ("rtu_tcp",0,3,12,2),("modbus_tcp",1,3,65535,2),
            ("modbus_tcp",1,3,12,126)):
            with self.assertRaises(ValueError): make_read(*params)


class FakeSocket:
    def __init__(self,response):
        self.response=response
        self.out=[]
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def settimeout(self,value):pass
    def sendall(self,data):self.out.append(data)
    def recv(self,count):
        part=self.response[:count]
        self.response=self.response[count:]
        return part


class PollTransportTests(unittest.TestCase):
    def test_tcp_read_keeps_real_request_response_bytes(self):
        words=struct.pack(">HH",0x1234,0x5678)
        reply=struct.pack(">HHHB",1,0,7,1)+bytes([3,4])+words
        fake=FakeSocket(reply)
        original_make=make_read
        def fixed_request(*args,**kw):
            kw["tx"]=1
            return original_make(*args,**kw)
        with patch("standalone.poll_worker.make_read",side_effect=fixed_request),\
             patch("standalone.poll_worker.socket.create_connection",return_value=fake),\
             patch.dict(os.environ,{"RAKURS_ALLOWED_GATEWAY_IPS":"192.168.5.21"}):
            payload=poll_probe(dict(config_payload(),device_key="inverter_1"))
        self.assertEqual(payload["frames"][0]["words"],[0x1234,0x5678])
        self.assertEqual(payload["fields"],[])
        self.assertEqual(payload["signals"],[])
        self.assertEqual(payload["quality"],"good")
        self.assertEqual(normalize(payload)["transport"],"modbus-tcp")
        self.assertEqual(fake.out[0],bytes.fromhex("0001000000060103000c0002"))

    def test_rtu_passthrough_preserves_crc_and_verified_frame(self):
        raw=bytes.fromhex("01030412345678")
        reply=raw+crc16(raw).to_bytes(2,"little")
        fake=FakeSocket(reply)
        with patch("standalone.poll_worker.socket.create_connection",return_value=fake),\
             patch.dict(os.environ,{"RAKURS_ALLOWED_GATEWAY_IPS":"192.168.5.21"}):
            data=poll_probe(dict(config_payload(transport="rtu_tcp"),
                                 device_key="inverter_2"))
        self.assertEqual(data["wire_format"],"rtu")
        self.assertEqual(normalize(data)["frames"][0]["words"],[0x1234,0x5678])

    def test_no_allowlist_disables_all_outbound_requests(self):
        with patch("standalone.poll_worker.socket.create_connection") as sock,\
             patch.dict(os.environ,{"RAKURS_ALLOWED_GATEWAY_IPS":""}):
            with self.assertRaises(PermissionError):
                poll_probe(dict(config_payload(),device_key="inverter_1"))
            sock.assert_not_called()

    def test_uncommissioned_device_not_selected(self):
        class DB:
            def cursor(self):return self
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def execute(self,*args):pass
            def fetchall(self):
                return [dict(config_payload(),id=2,device_key="inverter_1",
                             kind="simulation",modbus_address=1)]
        with patch.dict(os.environ,{"RAKURS_ALLOWED_GATEWAY_IPS":"192.168.5.21"}):
            self.assertEqual(ready_devices(DB()),[])


class FakeDB:
    def __init__(self):
        self.calls=[]
        self.committed=False
        self.rolled_back=False
    def cursor(self):return self
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def begin(self):pass
    def execute(self,sql,params=()):self.calls.append((sql,params))
    def fetchone(self):
        sql=self.calls[-1][0]
        if "SELECT enabled FROM rakurs_poll_settings" in sql:return {"enabled":0}
        if "SELECT COUNT(*)" in sql:return {"n":1}
        if "SELECT worker_heartbeat_utc" in sql:return {"worker_heartbeat_utc":None}
        return None
    def commit(self):self.committed=True
    def rollback(self):self.rolled_back=True


class AdminApiTests(unittest.TestCase):
    def setUp(self):
        self.role="admin"
        self.db=FakeDB()
        self.app=Flask("rakurs-config-test")
        self.app.config.update(TESTING=True,SECRET_KEY="offline")
        @self.app.before_request
        def account():
            g.rakurs_user={"id":55,"role":self.role}
        register(self.app,rows=lambda sql,params=():[],
                 one=lambda sql,params=():{"enabled":0,"interval_seconds":60}
                   if "rakurs_poll_settings" in sql else {},
                 conn=lambda:self.db)
        self.client=self.app.test_client()
        with self.client.session_transaction() as session:
            session["csrf"]="secrettoken"

    def post(self,path,json,token="secrettoken"):
        return self.client.post(path,json=json,headers={"X-CSRF-Token":token})

    def test_only_admin_can_write_with_csrf(self):
        obj={"enabled":False,"interval_seconds":60}
        self.role="viewer"
        self.assertEqual(self.post("/api/v1/config/polling",obj).status_code,403)
        self.role="admin"
        self.assertEqual(self.post("/api/v1/config/polling",obj,"wrong").status_code,403)
        self.assertEqual(self.db.calls,[])

    def test_interval_disable_and_enable_requires_live_worker(self):
        response=self.post("/api/v1/config/polling",
                           {"enabled":False,"interval_seconds":90})
        self.assertEqual(response.status_code,200)
        self.assertTrue(self.db.committed)
        self.assertTrue(any("UPDATE rakurs_poll_settings" in sql
                            for sql,_ in self.db.calls))
        self.db=FakeDB()
        blocked=self.post("/api/v1/config/polling",
                          {"enabled":True,"interval_seconds":60})
        self.assertEqual(blocked.status_code,409)
        self.assertTrue(self.db.rolled_back)
        self.assertFalse(self.db.committed)

    def test_device_creation_rejects_invalid_payload_without_sql(self):
        bad=config_payload(enabled=True,transport="unconfigured")
        bad["device_key"]="test_inv"
        self.assertEqual(self.post("/api/v1/config/inverters",bad).status_code,400)
        self.assertEqual(self.db.calls,[])
        bad=config_payload()
        bad["device_key"]="foo;DROP"
        self.assertEqual(self.post("/api/v1/config/inverters",bad).status_code,400)
        self.assertEqual(self.db.calls,[])


if __name__=="__main__":
    unittest.main()
