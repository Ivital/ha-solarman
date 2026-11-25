"""Security, fresh deployment and live-device query regression tests.

Offline-only: no MySQL connection, no network, no serial or Modbus commands.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import os
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "deploy" / "rakurs" / "standalone"
sys.path.insert(0, str(ROOT / "deploy" / "rakurs"))
from standalone import app as app_module
from standalone.mysql_transport import mysql_tls_options


class SecurityUpgradeTests(unittest.TestCase):
    def test_tls_ca_verifies_server_identity_and_live_requires_tls(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(mysql_tls_options(), {"ssl_disabled": True})
            with self.assertRaises(RuntimeError):
                mysql_tls_options(require_verified=True)
        with tempfile.TemporaryDirectory() as work:
            ca = Path(work) / "ca.pem"
            ca.write_bytes(b"-----BEGIN CERTIFICATE-----\nFAKE-TEST-ONLY\n")
            ca.chmod(0o644)
            with patch.dict(os.environ, {"RAKURS_MYSQL_CA_FILE": str(ca)}, clear=True):
                options = mysql_tls_options(require_verified=True)
                self.assertTrue(options["ssl_verify_cert"])
                self.assertTrue(options["ssl_verify_identity"])
                self.assertNotIn("ssl_disabled", options)
            ca.chmod(0o666)
            with patch.dict(os.environ, {"RAKURS_MYSQL_CA_FILE": str(ca)}, clear=True):
                with self.assertRaises(RuntimeError):
                    mysql_tls_options()

    def test_stage_and_rolling_deploy_have_required_runtime_files(self):
        paths = [line.strip() for line in (BASE / "release_manifest.txt")
                 .read_text().splitlines() if line.strip() and not line.startswith("#")]
        for name in ("app.py","auth.py","mysql_transport.py",
                     "parallel_support.py","modbus_wire.py","modbus_archive.py",
                     "static/app-parallel-twin-20261125.js",
                     "static/parallel-twin-20261125.css","templates/index.html"):
            self.assertIn(name, paths)
        self.assertEqual(len(paths), len(set(paths)))
        self.assertTrue(all((BASE / p).is_file() for p in paths))
        stage = (BASE / "stage_standalone.sh").read_text()
        live = (BASE / "deploy_parallel_ui.sh").read_text()
        for item in ("mysql_transport.py", "modbus_wire.py"):
            self.assertIn(item, live)
        self.assertIn("RELEASE_FILES", stage)
        self.assertIn("service active; stage_standalone --install is unsafe", stage)

    def test_web_sql_account_cannot_update_all_user_columns(self):
        code = (BASE / "provision_mysql_web_user.py").read_text()
        self.assertIn("GRANT UPDATE (last_login_at_utc)", code)
        self.assertNotIn("GRANT UPDATE ON rakurs.rakurs_users", code)
        migrate = (BASE / "harden_mysql_grants.py").read_text()
        self.assertIn("REVOKE UPDATE ON rakurs.rakurs_users", migrate)
        self.assertIn("mysql_tls_options(require_verified=True)", migrate)


class LiveArchiveApiTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True, AUTH_READY=True,
                                     SECRET_KEY="offline-test", SESSION_COOKIE_SECURE=False)
        self.client = app_module.app.test_client()
        with self.client.session_transaction() as session:
            session.update(uid=123, ver=1, at=int(time.time()))
        self.recorded = []

        def one(sql, params=()):
            if "FROM rakurs_users WHERE id=%s" in sql:
                return {"id": 123, "username": "test", "role": "admin",
                        "is_active": 1, "session_version": 1}
            self.recorded.append((sql, params))
            if "COUNT(*)" in sql:
                return {"n": 1}
            if "FROM rakurs_modbus_poll_runs" in sql:
                return {"id": 77, "poll_uid": "poll_real_2026",
                        "observed_at_utc": datetime(2026, 11, 25, tzinfo=timezone.utc),
                        "quality": "good", "status": "complete",
                        "request_count": 1, "response_count": 1,
                        "exception_count": 0}
            return None

        def rows(sql, params=()):
            self.recorded.append((sql, params))
            if "FROM rakurs_devices AS d" in sql:
                return [{"id":21,"slot_no":1,"device_key":"real_1","kind":"inverter",
                         "model":app_module.PARALLEL_MODEL}]
            if "FROM rakurs_modbus_poll_runs" in sql:
                return [{"poll_uid":"poll_real_2026","quality":"good",
                         "status":"complete","request_count":1,"response_count":1,
                         "exception_count":0}]
            if "FROM rakurs_modbus_register_samples" in sql:
                return [{"frame_id":88,"function_code":3,
                         "register_address":124,"raw_uint16":500,
                         "quality":"good"}]
            if "FROM rakurs_modbus_bit_samples" in sql:
                return [{"frame_id":89,"function_code":2,
                         "bit_address":33,"bit_value":1,"quality":"good"}]
            if "FROM rakurs_modbus_frames" in sql:
                return [{"sequence_no":1,"function_code":3,
                         "request_hex":"0103", "response_hex":"010302"}]
            return []

        self.mock_one = patch.object(app_module, "one", side_effect=one)
        self.mock_rows = patch.object(app_module, "rows", side_effect=rows)
        self.mock_one.start()
        self.mock_rows.start()
        self.addCleanup(self.mock_one.stop)
        self.addCleanup(self.mock_rows.stop)

    def test_live_endpoints_scope_device_and_poll_with_paging(self):
        for suffix, field in (("raw","raw_registers"),("bits","bits"),
                              ("frames","frames"),("polls","polls")):
            res = self.client.get("/api/v1/parallel/"+suffix+"?device=real_1")
            self.assertEqual(res.status_code,200,suffix)
            payload = res.get_json()
            self.assertEqual(payload["device_key"],"real_1")
            self.assertFalse(payload["simulation"])
            self.assertEqual(payload["total"],1)
            self.assertEqual(len(payload[field]),1)
        for sql,params in self.recorded:
            if any(t in sql for t in ("rakurs_modbus_register_samples",
                                      "rakurs_modbus_bit_samples",
                                      "rakurs_modbus_frames",
                                      "rakurs_modbus_poll_runs")):
                self.assertIn("device_id=%s", sql)
                self.assertIn(21, params)

    def test_health_is_observation_only_and_never_claims_collector_active(self):
        res = self.client.get("/api/v1/parallel/health")
        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertFalse(payload["collector_process_confirmed"])
        self.assertFalse(payload["shared_battery_soc_verified"])
        self.assertEqual(len(payload["devices"]), 1)
        self.assertEqual(payload["devices"][0]["device_key"], "real_1")
        self.assertIn(payload["devices"][0]["freshness_state"],
                      ("recent", "delayed", "stale", "invalid_time"))
        self.assertFalse(payload["devices"][0]["collector_process_confirmed"])

    def test_reject_cross_device_and_bad_poll_ids(self):
        self.assertEqual(self.client.get(
            "/api/v1/parallel/raw?device=invalid").status_code,404)
        self.assertEqual(self.client.get(
            "/api/v1/parallel/frames?device=real_1&poll_uid=bad").status_code,400)
        self.assertEqual(self.client.get(
            "/api/v1/parallel/bits?device=real_1&limit=501").status_code,400)
        self.assertEqual(self.client.get(
            "/api/v1/parallel/polls?device=real_1&offset=-1").status_code,400)

    def test_homepage_query_is_bounded_and_parallel_is_device_scoped(self):
        # Both SQL latest paths must remain bounded regardless of archive size.
        code = (BASE / "app.py").read_text()
        self.assertIn("ORDER BY mx.observed_at_utc DESC,mx.id DESC LIMIT 1", code)
        self.assertIn("ORDER BY s.signal_key LIMIT 32", code)
        self.assertIn("AND pg.group_key=%s", code)


if __name__ == "__main__":
    unittest.main()
