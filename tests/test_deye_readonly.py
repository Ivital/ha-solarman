"""Regression checks for Deye's read-only monitoring fork."""
from __future__ import annotations

import ast
import json
import runpy
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "custom_components" / "solarman"

class ReadOnlyPolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.require_read_function = staticmethod(
            runpy.run_path(str(PKG / "read_only.py"))["require_read_function"]
        )

    def test_allowed_read_only(self):
        for code in (3, 4):
            self.assertIsNone(self.require_read_function(code))

    def test_reject_writes_and_unknown_codes(self):
        for code in (0, 1, 2, 5, 6, 15, 16, 22, 23, 255, None, True, "03"):
            with self.subTest(code=code), self.assertRaises(PermissionError):
                self.require_read_function(code)

    def test_transport_and_execution_guards(self):
        tree = ast.parse((PKG / "device.py").read_text(encoding="utf-8"))
        dev = next(node for node in tree.body
                   if isinstance(node, ast.ClassDef) and node.name == "Device")
        setup = next(node for node in dev.body
                     if isinstance(node, ast.AsyncFunctionDef) and node.name == "setup")
        execute = next(node for node in dev.body
                       if isinstance(node, ast.AsyncFunctionDef) and node.name == "execute")
        self.assertIsInstance(setup.body[0], ast.If)
        self.assertEqual(ast.unparse(setup.body[0].test),
                         "self.config.transport != 'modbus_tcp'")
        self.assertEqual(ast.unparse(execute.body[0]), "require_read_function(code)")

    def test_entity_platforms_nonwritable(self):
        text = (PKG / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("_PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]", text)
        self.assertNotIn("async_track_time_interval", text)
        self.assertNotIn("discovery()", text)
        manifest = json.loads((PKG / "manifest.json").read_text(encoding="utf-8"))
        self.assertNotIn("dhcp", manifest)

    def test_services_read_only(self):
        text = (PKG / "services.py").read_text(encoding="utf-8")
        actions = yaml.safe_load((PKG / "services.yaml").read_text(encoding="utf-8"))
        for forbidden in ("SERVICE_WRITE_SINGLE_REGISTER", "SERVICE_WRITE_MULTIPLE_REGISTERS",
                          "DEPRECATION_SERVICE_WRITE", "_write_single_register",
                          "_write_multiple_registers"):
            self.assertNotIn(forbidden, text)
        self.assertEqual(set(actions), {"read_holding_registers", "read_input_registers"})
        self.assertIn("vol.Range(min = 1, max = 125)", text)

    def test_config_modbus_tcp_only(self):
        text = (PKG / "config_flow.py").read_text(encoding="utf-8")
        self.assertIn('options = ["modbus_tcp"]', text)
        self.assertNotIn("await discover(", text)
        constants = (PKG / "const.py").read_text(encoding="utf-8")
        self.assertIn('CONF_TRANSPORT: "modbus_tcp"', constants)
        self.assertIn('CONF_MPPT: 2', constants)

    def test_compose_security(self):
        cfg = yaml.safe_load((ROOT / "deploy" / "compose.yaml").read_text(encoding="utf-8"))
        ha = cfg["services"]["homeassistant"]
        self.assertTrue(ha["ports"][0].startswith("127.0.0.1:"))
        self.assertNotIn("privileged", ha)
        self.assertNotEqual("host", ha.get("network_mode"))
        self.assertIn("ALL", ha["cap_drop"])
        self.assertTrue(any(x.endswith(":/config/custom_components/solarman:ro")
                            for x in ha["volumes"]))

    def test_shared_host_resource_limits(self):
        cfg = yaml.safe_load((ROOT / "deploy" / "compose.yaml").read_text(encoding="utf-8"))
        ha = cfg["services"]["homeassistant"]
        self.assertEqual(ha["restart"], "no")
        self.assertEqual(ha["mem_limit"], "${HA_MEMORY_LIMIT:-1536m}")
        self.assertEqual(ha["memswap_limit"], "${HA_MEMSWAP_LIMIT:-1536m}")
        self.assertEqual(ha["cpus"], "${HA_CPU_LIMIT:-1.0}")
        self.assertEqual(ha["pids_limit"], 160)
        self.assertEqual(ha["logging"]["options"]["max-file"], "3")
        self.assertTrue((ROOT / "deploy" / "preflight_capacity.py").is_file())

    def test_capacity_threshold(self):
        script = runpy.run_path(str(ROOT / "deploy" / "preflight_capacity.py"))
        permitted = script["capacity_allows_probe"]
        self.assertFalse(permitted(1200 * 1024, 1536))
        self.assertTrue(permitted(2048 * 1024, 1536))

    def test_deye_profile_yaml(self):
        profile = yaml.safe_load((PKG / "inverter_definitions" / "deye_p3.yaml").read_text(encoding="utf-8"))
        self.assertIn("parameters", profile)
        self.assertIn("info", profile)
