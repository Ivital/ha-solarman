"""Validate secure MySQL archive preflight and MySQL-specific DDL offline."""
from pathlib import Path
import unittest

R = Path(__file__).resolve().parents[1] / "deploy" / "rakurs"


class RakursMySqlArchiveTests(unittest.TestCase):
    def test_empty_mysql_schema(self):
        content = (R / "archive_mysql_schema.sql").read_text(encoding="utf-8")
        for name in ("rakurs_devices", "rakurs_signals", "rakurs_samples",
                     "rakurs_device_events", "rakurs_ingest_batches"):
            self.assertIn("CREATE TABLE IF NOT EXISTS " + name, content)
        self.assertEqual(content.count("ENGINE=InnoDB"), 5)
        self.assertIn("PRIMARY KEY (device_id, signal_id, observed_at_utc)", content)
        for forbidden in ("PRAGMA foreign_keys", "DROP TABLE", "INSERT INTO"):
            self.assertNotIn(forbidden, content)

    def test_no_stored_password_and_verified_tls(self):
        content = (R / "mysql_archive_preflight.sh").read_text(encoding="utf-8")
        self.assertIn("VERIFY_IDENTITY", content)
        self.assertIn("--ssl-verify-server-cert", content)
        self.assertIn("--password", content)
        self.assertIn("SELECT CONCAT", content)
        self.assertIn("--network", content)
        self.assertIn("--login", content)
        for forbidden in ("MYSQL_PWD", "RAKURS_MYSQL_PASSWORD=", "curl -k",
                          "uci set", "docker compose up"):
            self.assertNotIn(forbidden, content)
