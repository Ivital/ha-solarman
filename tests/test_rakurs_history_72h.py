"""Deterministic, no-MySQL tests for a safe 72-hour Rakurs history append."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sys
import unittest

REPO = Path(__file__).resolve().parents[1]
STANDALONE = REPO / "deploy" / "rakurs" / "standalone"
sys.path.insert(0, str(STANDALONE))
import append_deye_history_72h as h


class SimulatedHistoryTests(unittest.TestCase):
    def setUp(self):
        self.anchor = datetime(2026, 11, 24, 18, 0, 0)
        self.received = self.anchor + timedelta(minutes=12)

    def test_covers_all_original_fields_and_append_only(self):
        definitions, metrics, _registers = h.prepare()
        self.assertEqual(len(definitions), 624)
        self.assertEqual(len(metrics), 316)
        self.assertEqual(h.DEVICE_MINUTES, 288)
        self.assertEqual(h.MODBUS_HOURS, 72)
        self.assertEqual(h.UI_INSERTS, 91008)
        self.assertEqual(h.P3_INSERTS, 44928)
        self.assertEqual(h.QUALITY, "simulated")
        script = (STANDALONE / "append_deye_history_72h.py").read_text()
        self.assertIn("autocommit=False", script)
        self.assertIn("FOR UPDATE", script)
        self.assertIn("db.commit()", script)
        self.assertIn("db.rollback()", script)
        self.assertIn("DEYE_HISTORY_72H_V1", script)
        self.assertIn("APPEND-DEYE-HISTORY-72H", script)
        self.assertNotIn("DELETE FROM ", script)
        self.assertNotIn("DROP TABLE ", script)
        self.assertNotIn("UPDATE rakurs_", script)
        self.assertNotIn("TRUNCATE TABLE ", script)
        self.assertNotIn("socket.", script)

    def test_pv_day_night_balance_and_battery_soc(self):
        # 00:00 Kyiv is night; 12:00 Kyiv is sunlight.
        night = datetime(2026, 11, 23, 22, 0)
        day = datetime(2026, 11, 24, 10, 0)
        np = h.snapshot(night)
        dp = h.snapshot(day)
        self.assertEqual(np["pv_power"], 0.0)
        self.assertGreater(dp["pv_power"], 5)
        for p in (np, dp):
            self.assertAlmostEqual(
                p["pv_power"] + p["battery_power"] + p["grid_power"],
                p["load_power"],
            )
            self.assertTrue(45 < p["battery_soc"] < 86)
            self.assertTrue(p["load_power"] > 3)

    def test_counters_progress_and_power_varies(self):
        morning = datetime(2026, 11, 24, 6)
        afternoon = datetime(2026, 11, 24, 12)
        self.assertGreater(
            h.snapshot(afternoon)["pv_energy_today"],
            h.snapshot(morning)["pv_energy_today"],
        )
        self.assertGreater(
            h.value_for("pv_energy_total", "Total", "kWh",
                        Decimal("4000"), afternoon, self.anchor, False),
            h.value_for("pv_energy_total", "Total", "kWh",
                        Decimal("4000"), morning, self.anchor, False),
        )
        self.assertEqual(h.snapshot(afternoon), h.snapshot(afternoon))

    def test_generators_preserve_time_spacing_and_synthetic_quality(self):
        sig = [(7, "pv_power", "Solar power", "кВт", Decimal("5.10"))]
        series = list(h.generate_ui_rows(sig, self.anchor, self.received))
        self.assertEqual(len(series), 288)
        self.assertEqual(series[0][0][1], self.anchor - timedelta(hours=72))
        self.assertEqual(series[-1][0][1], self.anchor - timedelta(minutes=15))
        self.assertEqual(series[0][0][-2:], ("simulated", h.INGEST_SOURCE))
        self.assertGreaterEqual(min(float(v[0][3]) for v in series), 0)
        self.assertGreater(max(float(v[0][3]) for v in series), 5)

        fields = [(5, "pv_power", "PV power", "W", Decimal("5100"), None)]
        p3 = list(h.generate_p3_rows(fields, self.anchor, self.received))
        self.assertEqual(len(p3), 72)
        self.assertEqual(p3[0][0][1], self.anchor - timedelta(hours=72))
        self.assertEqual(p3[-1][0][1], self.anchor - timedelta(hours=1))
        self.assertEqual(p3[0][0][-2:], ("simulated", h.INGEST_SOURCE))
        self.assertGreater(max(float(v[0][3]) for v in p3), 5000)

    def test_main_dashboard_has_dynamic_charts_and_initial_state(self):
        js = (STANDALONE / "static" / "app.js").read_text()
        self.assertIn("Динаміка показників", js)
        self.assertIn('["pv_power", "Сонячна генерація", "кВт", "solar"]', js)
        self.assertIn('["load_power", "Навантаження", "кВт", "load"]', js)
        self.assertIn('["battery_soc", "Заряд акумулятора", "%", "battery"]', js)
        self.assertIn('if (sig.signal_key === "pv_power") option.selected = true', js)
        self.assertIn("Promise.allSettled(series.map", js)


if __name__ == "__main__":
    unittest.main()
