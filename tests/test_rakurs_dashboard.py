"""Comprehensive Rakurs v4: profile-source checks, read-only cards, data storage guards."""
import unittest
import json
from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parents[1]
R=ROOT/"deploy"/"rakurs"

class RakursDashboardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dashboard=(R/"dashboard-rakurs.yaml").read_text(encoding="utf-8")
        cls.data=yaml.safe_load(cls.dashboard)
        cls.catalog=json.loads((R/"signal_catalog.json").read_text(encoding="utf-8"))

    def test_all_views_present_and_no_controls(self):
        views=self.data["views"]
        self.assertEqual([v["path"] for v in views],
            ["overview","pv","battery","grid","load","inverter","history","events","archive","system"])
        self.assertTrue(all(v["type"]=="sections" and v["show_icon_and_title"] for v in views))
        cards=[c for v in views for s in v["sections"] for c in s["cards"]]
        self.assertTrue({c["type"] for c in cards} <= {"heading","markdown","picture","conditional"})
        for c in cards:
            if c["type"]=="conditional":
                self.assertEqual(c["card"]["type"],"history-graph")
                self.assertEqual(len(c["conditions"]),2)
        self.assertNotIn("call-service",self.dashboard)
        self.assertNotIn("tap_action",self.dashboard)
        self.assertNotIn("custom:",self.dashboard)

    def test_catalog_is_unique_and_referenced_in_dashboard(self):
        items=[item for g in self.catalog["groups"].values() for item in g]
        self.assertEqual(len(items),self.catalog["all_metric_count"])
        self.assertGreaterEqual(len(items),300)
        self.assertEqual(len(items),len({x["entity_id"] for x in items}))
        self.assertTrue(all(x["status"] in {"planned_unverified","optional_unverified"} for x in items))
        self.assertTrue(all(x["entity_id"] in self.dashboard for x in items if not x["key"].startswith("bms_")))
        self.assertTrue(all(x["source_profile"] for x in items))
        self.assertTrue(self.catalog["read_only"])

    def test_reference_profile_names_are_exact(self):
        profile=(ROOT/"custom_components"/"solarman"/"inverter_definitions"/"deye_p3.yaml").read_text(encoding="utf-8")
        sources={x["source_profile"] for g in self.catalog["groups"].values() for x in g}
        for name in sources:
            self.assertIn("name: "+name,profile)

    def test_storage_audit_is_read_only_and_svg_preserved(self):
        s=(R/"audit_storage.sh").read_text(encoding="utf-8")
        for term in ("docker inspect","mode=ro","home-assistant_v2.db","systemctl is-active"):
            self.assertIn(term,s)
        for forbidden in ("rm -rf","docker compose down","recorder.purge","chown -R","sqlite3.connect('/config/"):
            self.assertNotIn(forbidden,s)
        self.assertTrue((R/"assets"/"energy-flow.svg").exists())
        self.assertTrue((R/"dashboard-rakurs-v3.yaml").exists())
