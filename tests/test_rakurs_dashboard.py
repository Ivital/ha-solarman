"""RAKURS v4: complete read-only dashboard, catalog, storage checks."""
import json
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
RAKURS = ROOT / "deploy" / "rakurs"


class RakursDashboardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = (RAKURS / "dashboard-rakurs.yaml").read_text(encoding="utf-8")
        cls.dashboard = yaml.safe_load(cls.raw)
        cls.catalog = json.loads(
            (RAKURS / "signal_catalog.json").read_text(encoding="utf-8")
        )

    def test_all_views_present_and_no_controls(self):
        views = self.dashboard["views"]
        self.assertEqual(
            [view["path"] for view in views],
            ["overview", "pv", "battery", "grid", "load", "inverter",
             "history", "events", "archive", "system"],
        )
        self.assertTrue(all(v["type"] == "sections" and
                            v["show_icon_and_title"] for v in views))
        cards = [
            card for view in views
            for section in view["sections"] for card in section["cards"]
        ]
        self.assertTrue(
            {c["type"] for c in cards} <=
            {"heading", "markdown", "picture", "conditional"}
        )
        for card in cards:
            if card["type"] == "conditional":
                self.assertEqual(card["card"]["type"], "history-graph")
                self.assertEqual(len(card["conditions"]), 2)
        for forbidden in ("call-service", "tap_action", "custom:", "hold_action"):
            self.assertNotIn(forbidden, self.raw)

    def test_catalog_unique_and_completely_visible(self):
        items = [
            item for group in self.catalog["groups"].values()
            for item in group
        ]
        self.assertEqual(len(items), self.catalog["all_metric_count"])
        self.assertGreaterEqual(len(items), 300)
        self.assertEqual(len(items), len({item["entity_id"] for item in items}))
        self.assertTrue(all(
            item["status"] in {"planned_unverified", "optional_unverified"}
            for item in items
        ))
        # Templates may compose an entity_id from an alias instead of
        # hard-coding the entire sensor name.
        not_rendered = [
            item["key"] for item in items
            if ("'" + item["key"] + "'" not in self.raw)
            and item["entity_id"] not in self.raw
        ]
        self.assertEqual(not_rendered, [])
        self.assertTrue(all(item["source_profile"] for item in items))
        self.assertTrue(self.catalog["read_only"])

    def test_reference_names_exist_in_profile(self):
        source = (
            ROOT / "custom_components" / "solarman" /
            "inverter_definitions" / "deye_p3.yaml"
        ).read_text(encoding="utf-8")
        declared = {
            line.split("name:", 1)[1].strip().strip("\"'")
            for line in source.splitlines()
            if line.strip().startswith("- name:")
        }
        referenced = {
            item["source_profile"]
            for group in self.catalog["groups"].values()
            for item in group
        }
        self.assertEqual(referenced - declared, set())

    def test_storage_audit_is_read_only_and_svg_preserved(self):
        script = (RAKURS / "audit_storage.sh").read_text(encoding="utf-8")
        for required in (
            "docker inspect", "mode=ro", "home-assistant_v2.db",
            "systemctl is-active"
        ):
            self.assertIn(required, script)
        for forbidden in (
            "rm -rf", "docker compose down", "recorder.purge",
            "chown -R", "sqlite3.connect('/config/"
        ):
            self.assertNotIn(forbidden, script)
        self.assertTrue((RAKURS / "assets" / "energy-flow.svg").exists())
        self.assertTrue((RAKURS / "dashboard-rakurs-v3.yaml").exists())

    def test_long_term_archive_schema_is_not_activated(self):
        sql = (RAKURS / "archive_schema.sql").read_text(encoding="utf-8")
        for table in ("devices", "signals", "samples", "device_events", "ingest_batches"):
            self.assertIn("CREATE TABLE IF NOT EXISTS " + table, sql)
        self.assertIn("PRIMARY KEY (device_id, signal_id, observed_at_utc)", sql)
        self.assertNotIn("DROP TABLE", sql)
        self.assertNotIn("DELETE FROM", sql)
