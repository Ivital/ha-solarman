"""Safety constraints for one-time two-Deye history population.

The tests do not connect to MySQL or poll Modbus. Generated history is
never promoted to verified physical measurements.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1] / "deploy" / "rakurs" / "standalone"

class HistoricalPreviewSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql=(ROOT/"populate_two_deye_history.sql").read_text(encoding="utf-8")
        cls.ps=(ROOT/"install_two_deye_history_windows.ps1").read_text(encoding="utf-8")
        cls.js=(ROOT/"static/app-parallel-twin-20261125.js").read_text(encoding="utf-8")
        cls.worker=(ROOT/"poll_worker.py").read_text(encoding="utf-8")

    def test_provenance_is_truthful_and_physical_polling_is_gated(self):
        sql=self.sql
        self.assertIn("'generated','rakurs_preview_history'",sql)
        self.assertIn("quality='generated'",sql)
        self.assertIn("member_kind='archive'",sql)
        self.assertIn("kind='inverter'",sql)
        self.assertNotIn("quality='good'",sql)
        self.assertNotIn("member_verified=1",sql)
        self.assertNotIn("transport_verified=1",sql)
        self.assertNotIn("verified_connection=1",sql)
        self.assertIn("pm.member_kind='physical'",self.worker)
        self.assertIn("pm.member_verified=1",self.worker)
        self.assertIn("pm.transport_verified=1",self.worker)

    def test_guards_and_full_transaction(self):
        sql=self.sql
        self.assertIn("START TRANSACTION;",sql)
        self.assertIn("HISTORY_PREINSERT_GUARD=PASS",sql)
        self.assertIn("HISTORY_POSTINSERT_GUARD=PASS",sql)
        self.assertEqual(sql.count("COMMIT;"),1)
        self.assertIn("@source_ui=632",sql)
        self.assertIn("@source_fields=1248",sql)
        self.assertIn("@all_devices=2",sql)
        self.assertIn("@settings=2",sql)
        self.assertIn("@poll_on=0",sql)
        self.assertIn("@insert_ui=212352",sql)
        self.assertIn("@insert_fields=209664",sql)
        self.assertIn("@new_ui=212984",sql)
        self.assertIn("@new_fields=210912",sql)
        self.assertNotIn("DROP TABLE",sql)
        self.assertNotIn("TRUNCATE TABLE",sql)
        self.assertNotIn("DELETE FROM",sql)
        self.assertNotIn("FOREIGN_KEY_CHECKS=0",sql)
        self.assertRegex(sql,r"INSERT INTO rakurs_history_assert\(ok\) VALUES \(\s*IF\(")

    def test_windows_installation_requires_backup_and_pinned_commit(self):
        ps=self.ps
        self.assertIn("param([switch]$Apply,[string]$GitRef='')",ps)
        self.assertIn("--single-transaction",ps)
        self.assertIn("--result-file=$Backup",ps)
        self.assertIn("[System.IO.File]::OpenText($Backup)",ps)
        self.assertIn("BACKUP_SHA256",ps)
        self.assertIn("TWO_DEYE_PREVIEW_BASELINE=PASS",ps)
        self.assertIn("HISTORY_PREVIEW_CHECK_ONLY=PASS",ps)
        self.assertIn("MYSQL_WRITES=0 MODBUS_COMMANDS=0",ps)
        self.assertIn("HARDWARE_MODBUS_READS=0 AUTOMATIC_POLLING=DISABLED",ps)
        self.assertIn("^[0-9a-f]{40}$",ps)
        self.assertNotIn("[System.IO.StreamReader]::new(",ps)
        self.assertLess(ps.index("=== FULL MYSQL BACKUP"),ps.index("=== APPLY TRANSACTIONAL"))

    def test_visible_js_does_not_expose_old_identifiers(self):
        for word in ("DEMO_MODE", "FORCE_REAL_MODE", "SIM-PROFILE",
                     "sim_deye20k", "simulated_deye", "status.simulated_signals"):
            self.assertNotIn(word,self.js)
        self.assertIn('key === "deye_inverter_1"',self.js)
        self.assertIn('key === "deye_inverter_2"',self.js)
        self.assertIn('const url = base + path + suffix;',self.js)
        for p in ("static/app.js","static/app-stable-20261124.js"):
            self.assertEqual((ROOT/p).read_text(encoding="utf-8"),self.js)

    def test_distinct_slots_not_overwritten(self):
        sql=self.sql
        self.assertIn("IF(m.device_id=2,0.98,1.07)",sql)
        self.assertIn("m.device_id IN (2,3)",sql)
        self.assertIn("deye_inverter_1",sql)
        self.assertIn("deye_inverter_2",sql)
        self.assertIn("HISTORY_UI_ROWS_ADDED=212352",sql)
        self.assertIn("HISTORY_MODBUS_FIELD_ROWS_ADDED=209664",sql)

if __name__=="__main__":
    unittest.main()
