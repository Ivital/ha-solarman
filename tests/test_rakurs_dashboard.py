"""Validate Ukrainian Rakurs dashboard with a safe SVG illustration."""
import unittest
from pathlib import Path
import yaml
from xml.etree import ElementTree

R=Path(__file__).resolve().parents[1]/"deploy"/"rakurs"
class RakursDashboardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw=(R/"dashboard-rakurs.yaml").read_text(encoding="utf-8")
        cls.d=yaml.safe_load(cls.raw)

    def test_views(self):
        v=self.d["views"]
        self.assertEqual([x["title"] for x in v],["Огляд","Інвертор","Історія","Система"])
        self.assertTrue(all(x["type"]=="sections" and x["max_columns"]==2 and x["show_icon_and_title"] for x in v))

    def test_builtin_cards_and_picture_link(self):
        cs=[c for v in self.d["views"] for s in v["sections"] for c in s["cards"]]
        self.assertEqual({c["type"] for c in cs},{"heading","markdown","picture"})
        pics=[c for c in cs if c["type"]=="picture"]
        self.assertEqual(len(pics),1)
        self.assertEqual(pics[0]["image"],"/local/rakurs/energy-flow.svg?v=3")
        self.assertEqual(pics[0]["grid_options"]["columns"],24)
        self.assertNotIn("custom:",self.raw)
        self.assertNotIn("call-service",self.raw)
        self.assertIn("states('sensor.rakurs_pv_power')",self.raw)

    def test_svg_is_static_and_valid(self):
        svg=(R/"assets"/"energy-flow.svg").read_text(encoding="utf-8")
        self.assertEqual(ElementTree.fromstring(svg).tag,"{http://www.w3.org/2000/svg}svg")
        for term in ["Пунктирні стрілки","PV1 / PV2","DEYE","АКУМУЛЯТОР","НАВАНТАЖЕННЯ","ЕЛЕКТРОМЕРЕЖА"]:
            self.assertIn(term,svg)
        self.assertNotIn("<script",svg.lower())
        self.assertNotIn("https://",svg)

    def test_guarded_deploy_and_rollback(self):
        s=(R/"install_diagram.sh").read_text(encoding="utf-8")
        self.assertIn('docker inspect',s)
        self.assertIn('"/config"',s)
        self.assertNotIn("chown -R",s)
        self.assertNotIn("chmod -R",s)
        self.assertNotIn("docker compose restart",s)
        self.assertTrue((R/"dashboard-rakurs-v2.yaml").is_file())
