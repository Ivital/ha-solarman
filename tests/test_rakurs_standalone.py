"""Standalone Rakurs uses only LAN MySQL, with no HA and no fabricated data."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
RAKURS = REPO / "deploy" / "rakurs"
sys.path.insert(0, str(RAKURS))
from standalone import app as module


class StandaloneMySqlTests(unittest.TestCase):
    def setUp(self):
        module.app.config.update(TESTING=True)
        self.client = module.app.test_client()

    def test_index_and_assets_exist_without_database(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertIn("RAKURS", self.client.get("/").get_data(as_text=True))
        for path in ("/static/style.css", "/static/app.js"):
            self.assertEqual(self.client.get(path).status_code, 200)
        javascript = (RAKURS / "standalone/static/app.js").read_text()
        self.assertIn('"/api/v1"', javascript)
        self.assertNotIn("/api/states", javascript)
        self.assertNotIn("homeassistant.local", javascript)

    def test_deny_non_loopback_clients(self):
        response = self.client.get("/", environ_overrides={"REMOTE_ADDR": "192.168.5.21"})
        self.assertEqual(response.status_code, 403)

    def test_catalog_is_explicitly_planned_and_unverified(self):
        resp = self.client.get("/api/v1/catalog")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["all_metric_count"], 316)
        self.assertTrue(data["read_only"])
        self.assertTrue(all(
            s["status"] in ("planned_unverified", "optional_unverified")
            for entries in data["groups"].values() for s in entries
        ))

    def test_status_empty_database_is_not_false_zero_telemetry(self):
        def fake_one(sql, params=()):
            if "COUNT(*)" in sql:
                return {"n": 0}
            return None
        with patch.object(module, "one", side_effect=fake_one):
            result = self.client.get("/api/v1/status")
        self.assertEqual(result.status_code, 200)
        obj = result.get_json()
        self.assertEqual(obj["data_source"], "mysql")
        self.assertEqual(obj["database"], "rakurs")
        self.assertEqual(obj["registered_devices"], 0)
        self.assertEqual(obj["verified_signals"], 0)
        self.assertFalse(obj["has_real_samples"])
        self.assertFalse(obj["collector_active"])
        self.assertFalse(obj["home_assistant_dependency"])

    def test_latest_does_not_fabricate_missing_values(self):
        with patch.object(module, "one", return_value=None) as fake:
            res = self.client.get("/api/v1/latest?keys=pv_power,battery_soc")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["values"], {})
        self.assertEqual(fake.call_count, 2)
        self.assertEqual(fake.call_args.args[1], ("battery_soc",))

    def test_latest_reads_only_verified_signal_data(self):
        value = {
            "signal_key": "pv_power", "label_uk": "Генерація",
            "unit": "кВт", "value_decimal": Decimal("2.500000000"),
            "raw_value": 2500, "quality": "good",
            "observed_at_utc": datetime(2026, 11, 24, 14, 30),
        }
        with patch.object(module, "one", return_value=value):
            res = self.client.get("/api/v1/latest?keys=pv_power")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()["values"]["pv_power"]
        self.assertEqual(data["value_decimal"], "2.500000000")
        self.assertEqual(data["observed_at_utc"], "2026-11-24T14:30:00Z")

    def test_malformed_keys_rejected_before_database_access(self):
        with patch.object(module, "one") as fake:
            for path in (
                "/api/v1/latest?keys=",
                "/api/v1/latest?keys=pv_power%27%20OR%201%3D1",
                "/api/v1/history?key=pv_power%3BDELETE",
            ):
                self.assertEqual(self.client.get(path).status_code, 400)
            fake.assert_not_called()

    def test_history_has_bound_and_no_unverified_signal(self):
        with patch.object(module, "one", return_value=None), patch.object(module, "rows") as fake:
            result = self.client.get("/api/v1/history?key=pv_power&hours=24")
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.get_json()["points"], [])
            fake.assert_not_called()
        for value in ("0", "169", "abc"):
            self.assertEqual(self.client.get("/api/v1/history?key=pv_power&hours=" + value).status_code, 400)

    def test_password_file_is_required_and_private(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(module.ConfigurationError):
                module._password()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secret"
            path.write_text("not-a-real-password", encoding="utf-8")
            path.chmod(0o644)
            with patch.dict(os.environ, {"RAKURS_DB_PASSWORD_FILE": str(path)}, clear=True):
                with self.assertRaises(module.ConfigurationError):
                    module._password()
            path.chmod(0o600)
            with patch.dict(os.environ, {"RAKURS_DB_PASSWORD_FILE": str(path)}, clear=True):
                self.assertEqual(module._password(), "not-a-real-password")

    def test_backend_has_no_ha_recorder_or_sql_writes(self):
        code = (RAKURS / "standalone/app.py").read_text()
        self.assertIn('SCHEMA = "rakurs"', code)
        self.assertIn('HOST = "192.168.5.30"', code)
        self.assertIn("ssl_disabled=True", code)
        self.assertIn("AND s.verified=1", code)
        self.assertNotIn("sqlite3.connect", code)
        self.assertNotIn("requests.get", code)
        self.assertNotIn("sensor.rakurs_", code)
        for forbidden in ("cur.execute(\"INSERT", "cur.execute(\"UPDATE",
                          "cur.execute(\"DELETE", "cur.execute(\"DROP"):
            self.assertNotIn(forbidden, code)


if __name__ == "__main__":
    unittest.main()
