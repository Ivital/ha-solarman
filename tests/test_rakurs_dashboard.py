"""Validate the zero-fabrication RAKURS dashboard starter."""
import unittest
from pathlib import Path
import yaml

P = Path(__file__).resolve().parents[1] / "deploy" / "rakurs" / "dashboard-rakurs.yaml"

class RakursDashboardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = P.read_text(encoding="utf-8")
        cls.doc = yaml.safe_load(cls.raw)

    def test_four_sections_views(self):
        views = self.doc["views"]
        self.assertEqual([v["title"] for v in views],
                         ["Огляд", "Інвертор", "Історія", "Система"])
        self.assertEqual(len({v["path"] for v in views}), 4)
        self.assertTrue(all(v["type"] == "sections" for v in views))

    def test_builtin_read_only_cards(self):
        all_cards = [c for v in self.doc["views"] for sec in v["sections"]
                     for c in sec["cards"]]
        self.assertEqual({c["type"] for c in all_cards}, {"markdown", "heading"})
        for card in all_cards:
            self.assertNotIn("tap_action", card)
            self.assertNotIn("hold_action", card)
            self.assertNotIn("entity", card)
        self.assertNotIn("call-service", self.raw)
        self.assertNotIn("button", self.raw)
        self.assertNotIn("custom:", self.raw)

    def test_missing_data_explicitly_marked(self):
        self.assertIn("Очікування підтверджених показників Deye", self.raw)
        self.assertIn("sensor.rakurs_pv_power", self.raw)
        self.assertIn("не підтверджені", self.raw)
        self.assertNotIn("12.5 кВт", self.raw)
