"""Regression: RAKURS UI describes stored DB records without false source claims.

Internal simulator provenance in API and MySQL must remain intact; these
assertions cover only user-visible active pages and their bundled JS.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1] / "deploy" / "rakurs" / "standalone"

class PublicPresentationTests(unittest.TestCase):
    VISIBLE_FILES = (
        "templates/index.html",
        "templates/deye_server.html",
        "static/app-parallel-twin-20261125.js",
    )

    def test_no_simulator_presentation_copy_in_active_site(self):
        for relative in self.VISIBLE_FILES:
            with self.subTest(path=relative):
                content = (ROOT / relative).read_text(encoding="utf-8")
                # Internal JSON provenance keys and CSS selectors are not UI copy.
                self.assertIsNone(
                    re.search(r"симуляц|симуль|демонстрац|тестов|синтетич", content, re.I)
                )

    def test_archive_display_does_not_assert_physical_origin(self):
        template = (ROOT / "templates/index.html").read_text(encoding="utf-8")
        js = (ROOT / "static/app-parallel-twin-20261125.js").read_text(encoding="utf-8")
        self.assertIn("фізичне походження вимірювань не підтверджене", template)
        self.assertIn("Походження цих значень не підтверджене", js)
        self.assertIn('item.quality === "simulated"', js)
        self.assertIn('mode.textContent = DEMO_MODE ? "Архів MySQL"', js)

if __name__ == "__main__":
    unittest.main()
