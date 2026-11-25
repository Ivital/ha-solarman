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

if __name__ == "__main__":
    unittest.main()
