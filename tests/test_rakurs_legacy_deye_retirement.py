"""Safety regressions for retiring only the obsolete standalone Deye.

These tests never connect to MySQL or execute the Windows maintenance script.
"""
from pathlib import Path
import unittest

BASE=Path(__file__).resolve().parents[1]/"deploy"/"rakurs"/"standalone"

class LegacyDeyeRetirementTests(unittest.TestCase):
    def test_legacy_routes_removed_and_operational_routes_retained(self):
        s=(BASE/"app.py").read_text(encoding="utf-8")
        for path in (
            "/api/v1/parallel/simulation",
            "/api/v1/parallel/simulation/latest",
            "/api/v1/parallel/simulation/raw",
            "/api/v1/parallel/simulation/fields",
        ):
            self.assertNotIn('@app.get("'+path+'")',s)
        for path in (
            "/api/v1/parallel", "/api/v1/parallel/latest",
            "/api/v1/parallel/history", "/api/v1/parallel/raw",
            "/api/v1/parallel/modbus-fields",
        ):
            self.assertIn('@app.get("'+path+'")',s)
        self.assertNotIn("PARALLEL_DEMO_KEYS",s)
        self.assertIn("def selected_overview_device_id():",s)
        self.assertIn("pm.slot_no IN (1,2)",s)

    def test_windows_maintenance_is_exact_key_transactional_and_backed_up(self):
        script=(BASE/"retire_legacy_deye_windows.ps1").read_text(encoding="utf-8")
        self.assertIn("param([switch]$Apply)",script)
        self.assertIn("mysqldump.exe",script)
        self.assertIn("--single-transaction",script)
        self.assertIn("BACKUP_SHA256",script)
        self.assertIn("START TRANSACTION;",script)
        self.assertIn("COMMIT;",script)
        self.assertIn("RAKURS_GUARD_FAILED_STOP_AND_ROLLBACK",script)
        self.assertIn("RAKURS_POSTDELETE_GUARD_FAILED_ROLLBACK",script)
        self.assertIn("simulated_deye_sg05lp3",script)
        self.assertIn("@keep_count=2 AND @slot_count=2",script)
        self.assertIn("@all_count=3",script)
        self.assertIn("@unknown_fk=0",script)
        self.assertIn("DELETE FROM rakurs_devices",script)
        self.assertIn("WHERE id=@old_id AND device_key='simulated_deye_sg05lp3'",script)
        self.assertNotIn("DROP TABLE",script)
        self.assertNotIn("SET FOREIGN_KEY_CHECKS=0",script)
        self.assertNotIn("TRUNCATE TABLE",script)

    def test_composite_pk_and_dashboard_source_scoped_to_group(self):
        s=(BASE/"app.py").read_text()
        js=(BASE/"static"/"app-parallel-twin-20261125.js").read_text()
        self.assertIn("JOIN rakurs_parallel_members AS pm ON pm.device_id=d.id",s)
        self.assertIn("ORDER BY pm.slot_no",s)
        self.assertNotIn("FROM rakurs_samples ORDER BY device_id LIMIT 1",s)
        self.assertIn("selected_overview_device_id()",s)
        self.assertIn("state.selectedDeviceKey",js)
        self.assertIn("device="+chr(34)+" + encodeURIComponent",js)

if __name__ == "__main__":
    unittest.main()
