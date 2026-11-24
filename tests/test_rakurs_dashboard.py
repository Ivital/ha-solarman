"""Validate dashboard v2 structure and read-only/no-fake-data placeholders."""
import unittest
from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parents[1]/"deploy"/"rakurs"
P=ROOT/"dashboard-rakurs.yaml"

class RakursDashboardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw=P.read_text(encoding="utf-8")
        cls.doc=yaml.safe_load(cls.raw)

    def test_four_views_and_labeled_tabs(self):
        v=self.doc["views"]
        self.assertEqual([x["title"] for x in v],
            ["Огляд","Інвертор","Історія","Система"])
        self.assertEqual(len({x["path"] for x in v}),4)
        self.assertTrue(all(x["type"]=="sections" and
                 x["max_columns"]==2 and
                 x["dense_section_placement"] and
                 x["show_icon_and_title"] for x in v))

    def test_full_width_sections_and_builtin_readonly_cards(self):
        s=[sec for v in self.doc["views"] for sec in v["sections"]]
        self.assertTrue(any(x.get("column_span")==2 for x in s))
        cards=[c for sec in s for c in sec["cards"]]
        self.assertEqual({c["type"] for c in cards},{"heading","markdown"})
        self.assertTrue(any(c.get("grid_options",{}).get("columns")==24 for c in cards))
        self.assertNotIn("call-service",self.raw)
        self.assertNotIn("custom:",self.raw)
        for card in cards:
            self.assertNotIn("tap_action",card)
            self.assertNotIn("hold_action",card)
            self.assertNotIn("entity",card)

    def test_no_fabricated_telemetry(self):
        self.assertIn("states('sensor.rakurs_pv_power')",self.raw)
        self.assertIn("Очікування телеметрії Deye",self.raw)
        self.assertIn("не підтверджені",self.raw)
        self.assertNotIn("12.5 кВт",self.raw)

    def test_rollback_copy_preserved(self):
        self.assertTrue((ROOT/"dashboard-rakurs-v1.yaml").exists())
