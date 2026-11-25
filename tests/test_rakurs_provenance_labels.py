"""RAKURS must show one operational MySQL view, without legacy test copy."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1] / "deploy" / "rakurs" / "standalone"

class PublicPresentationTests(unittest.TestCase):
    VISIBLE_FILES = (
        "templates/index.html",
        "templates/deye_server.html",
        "static/app-parallel-twin-20261125.js",
        "static/app.js",
        "static/app-stable-20261124.js",
    )

    def test_no_test_or_simulation_presentation_in_active_site(self):
        for relative in self.VISIBLE_FILES:
            with self.subTest(path=relative):
                content = (ROOT / relative).read_text(encoding="utf-8")
                # Technical DB fields and legacy device identifiers are not
                # displayed directly; UI copy must never expose test labels.
                self.assertNotRegex(content,
                    r"Тестові дані|Тестовий інвертор|СИМУЛЯЦІЯ|ДЕМО РЕЖИМ")

    def test_all_js_entrypoints_share_single_mode(self):
        active = (ROOT / "static/app-parallel-twin-20261125.js").read_text()
        self.assertIn('const DEMO_MODE = false;', active)
        self.assertIn('const url = base + path;', active)
        self.assertIn('mode.textContent = empty ? "Очікування показників" : "Дані MySQL";', active)
        self.assertIn('api("/parallel/latest?device="', active)
        self.assertIn('api("/parallel/raw"', active)
        self.assertNotIn('api("/parallel/simulation', active)
        for relative in ("static/app.js", "static/app-stable-20261124.js"):
            self.assertEqual((ROOT / relative).read_text(), active)

    def test_server_uses_one_operational_data_mode(self):
        app = (ROOT / "app.py").read_text()
        self.assertIn('"mode": "real"', app)
        self.assertIn('SELECT COUNT(*) AS n FROM rakurs_devices', app)
        self.assertIn('SELECT COUNT(DISTINCT signal_id) AS n FROM rakurs_samples', app)
        self.assertIn('WHERE m.device_id=%s', app)
        self.assertIn('def primary_device_id():', app)

    def test_queries_follow_composite_mysql_primary_keys(self):
        code = (ROOT / "app.py").read_text()
        mysql_schema = (ROOT.parent / "archive_mysql_schema.sql").read_text()
        modbus_schema = (ROOT / "modbus_full_schema.sql").read_text()

        sample_definition = mysql_schema.split(
            "CREATE TABLE IF NOT EXISTS rakurs_samples (", 1
        )[1].split(") ENGINE=InnoDB", 1)[0]
        field_definition = modbus_schema.split(
            "CREATE TABLE IF NOT EXISTS rakurs_modbus_field_samples (", 1
        )[1].split(") ENGINE=InnoDB", 1)[0]
        self.assertIn("PRIMARY KEY (device_id, signal_id, observed_at_utc)",
                      sample_definition)
        self.assertIn("PRIMARY KEY (device_id, field_id, observed_at_utc)",
                      field_definition)
        self.assertNotRegex(sample_definition, r"(?m)^\s*id\s+BIGINT")
        self.assertNotRegex(field_definition, r"(?m)^\s*id\s+BIGINT")
        for invalid in (
            "SELECT mx.id FROM rakurs_samples",
            "SELECT ms.id FROM rakurs_modbus_field_samples",
            "SELECT mx.id FROM rakurs_modbus_field_samples",
            "JOIN rakurs_samples AS m ON m.id=",
            "LEFT JOIN rakurs_modbus_field_samples AS m ON m.id=",
            "ORDER BY m.observed_at_utc DESC,m.id DESC",
            "ORDER BY mx.observed_at_utc DESC,mx.id DESC",
            "ORDER BY ms.observed_at_utc DESC,ms.id DESC",
            "FROM rakurs_samples ORDER BY id",
        ):
            with self.subTest(invalid=invalid):
                self.assertNotIn(invalid, code)
        self.assertIn("SELECT MAX(mx.observed_at_utc)", code)
        self.assertIn("SELECT MAX(ms.observed_at_utc)", code)

if __name__ == "__main__":
    unittest.main()
