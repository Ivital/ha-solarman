"""Read-only parallel Deye contract, no fabricated master/slave or energy."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy" / "rakurs"))
from standalone import app as module
from standalone.parallel_support import MODEL, describe_topology, verified_pv_sum


class ParallelPureTests(unittest.TestCase):
    def test_empty_topology_does_not_invent_devices(self):
        p = describe_topology([])
        self.assertEqual(p["expected_inverters"], 2)
        self.assertEqual(p["registered_inverters"], 0)
        self.assertTrue(p["shared_battery"])
        self.assertIsNone(p["battery_soc_percent"])
        self.assertFalse(p["aggregate_verified"])
        self.assertEqual(p["aggregate_values"], {})
        self.assertEqual(p["devices"], [])

    def test_two_registered_devices_not_assumed_master_or_slave(self):
        p = describe_topology([
            {"device_key": "deye_one", "model": MODEL},
            {"device_key": "deye_two", "model": MODEL},
        ])
        self.assertEqual(p["completeness"], "registered")
        self.assertTrue(all(i["parallel_role"] == "unverified" for i in p["devices"]))
        self.assertTrue(all(i["modbus_address"] is None for i in p["devices"]))
        self.assertIsNone(p["battery_soc_percent"])

    def test_power_sum_needs_explicit_provenance_and_two_fresh_unique_sources(self):
        t = datetime(2026, 11, 24, 18, 0, tzinfo=timezone.utc)
        two = [
            dict(signal_key="pv_power", unit="кВт", quality="good",
                 device_key="unit_one", value_decimal=Decimal("5.1"),
                 observed_at_utc=t - timedelta(seconds=45)),
            dict(signal_key="pv_power", unit="кВт", quality="good",
                 device_key="unit_two", value_decimal=Decimal("6.2"),
                 observed_at_utc=t - timedelta(seconds=90)),
        ]
        self.assertIsNone(verified_pv_sum(two, independently_measured=False, now_utc=t))
        allowed = verified_pv_sum(two, independently_measured=True, now_utc=t)
        self.assertEqual(allowed["value_decimal"], "11.3")
        self.assertIsNone(verified_pv_sum(two[:1], independently_measured=True, now_utc=t))
        self.assertIsNone(verified_pv_sum([two[0], {**two[1], "device_key": "unit_one"}],
                                           independently_measured=True, now_utc=t))
        self.assertIsNone(verified_pv_sum([two[0], {**two[1], "quality": "simulated"}],
                                           independently_measured=True, now_utc=t))
        self.assertIsNone(verified_pv_sum([two[0], {**two[1],
                      "observed_at_utc": t - timedelta(minutes=20)}],
                      independently_measured=True, now_utc=t))
        self.assertIsNone(verified_pv_sum([two[0], {**two[1],
                      "observed_at_utc": t - timedelta(seconds=220)}],
                      independently_measured=True, now_utc=t))
        self.assertIsNone(verified_pv_sum([two[0], {**two[1], "unit": "W"}],
                                           independently_measured=True, now_utc=t))


class ParallelApiTests(unittest.TestCase):
    def setUp(self):
        module.app.config.update(
            TESTING=True, SECRET_KEY="parallel-testing-key",
            AUTH_READY=True, SESSION_COOKIE_SECURE=False,
        )
        self.client = module.app.test_client()
        with self.client.session_transaction() as sess:
            sess["uid"] = 123
            sess["ver"] = 1
            sess["at"] = int(time.time())
        self.db_calls = []
        self.fake_devices = []

        def fake_one(sql, params=()):
            if "FROM rakurs_users WHERE id=%s" in sql:
                return {"id": 123, "username": "tester", "role": "admin",
                        "is_active": 1, "session_version": 1}
            return None

        def fake_rows(sql, params=()):
            self.db_calls.append((sql, params))
            if "FROM rakurs_devices AS d" in sql:
                return [dict(item, slot_no=item.get("slot_no", i + 1))
                        for i, item in enumerate(self.fake_devices)]
            if "FROM rakurs_samples AS m" in sql:
                return [{
                    "signal_key": "pv_power", "label_uk": "Генерація PV",
                    "unit": "кВт", "value_decimal": Decimal("5.1"),
                    "quality": "good", "observed_at_utc":
                      datetime(2026, 11, 24, 20, 0),
                    "device_key": "inverter_1",
                }]
            if "FROM rakurs_device_events AS e" in sql:
                return []
            return []

        self.p_one = patch.object(module, "one", side_effect=fake_one)
        self.p_rows = patch.object(module, "rows", side_effect=fake_rows)
        self.p_one.start()
        self.p_rows.start()
        self.addCleanup(self.p_one.stop)
        self.addCleanup(self.p_rows.stop)

    def test_profile_unverified_when_database_has_only_simulation(self):
        response = self.client.get("/api/v1/parallel")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["registered_inverters"], 0)
        self.assertTrue(payload["shared_battery"])
        self.assertFalse(payload["rs485_topology_verified"])
        self.assertFalse(payload["aggregate_verified"])
        self.assertIsNone(payload["battery_soc_percent"])
        self.assertEqual(len(self.db_calls), 1)
        sql, params = self.db_calls[0]
        self.assertIn("pg.group_key=%s", sql)
        self.assertIn("deye_sun20k_parallel_shared_battery", params)

    def test_device_readings_are_scoped_and_never_aggregated(self):
        self.fake_devices = [
            {"id": 21, "device_key": "inverter_1", "model": MODEL,
             "kind": "inverter", "serial_number": None},
            {"id": 22, "device_key": "inverter_2", "model": MODEL,
             "kind": "inverter", "serial_number": None},
        ]
        response = self.client.get("/api/v1/parallel/latest?device=inverter_1")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["values"]["pv_power"]["value_decimal"], "5.1")
        self.assertFalse(payload["aggregation_verified"])
        self.assertIsNone(payload["shared_battery_soc"])
        self.assertIsNone(payload["total_values"])
        query = next((q for q, params in self.db_calls
                      if "FROM rakurs_samples AS m" in q), "")
        self.assertIn("m.device_id=%s", query)
        self.assertNotIn("m.quality=", query)
        self.assertIn("m.device_id=%s", query)
        self.assertFalse(any("INSERT " in sql or "UPDATE " in sql
                             for sql, params in self.db_calls))

    def test_rejects_unregistered_devices_and_unsafe_keys(self):
        self.assertEqual(self.client.get(
            "/api/v1/parallel/latest?device=simulated_deye_sg05lp3").status_code, 404)
        self.assertEqual(self.client.get(
            "/api/v1/parallel/latest?device=evil%27%3Bdrop").status_code, 400)
        self.assertEqual(self.client.get(
            "/api/v1/parallel/events?device=").status_code, 400)

    def test_events_scoped_to_physical_device(self):
        self.fake_devices = [{"id": 21, "device_key": "inverter_1",
                              "kind": "inverter", "model": MODEL}]
        result = self.client.get("/api/v1/parallel/events?device=inverter_1")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json()["events"], [])
        sql = next((q for q, _ in self.db_calls
                    if "FROM rakurs_device_events AS e" in q), "")
        self.assertIn("WHERE e.device_id=%s", sql)

    def test_individual_history_does_not_mix_devices_or_simulation(self):
        self.fake_devices = [
            {"id": 21, "device_key": "inverter_1", "model": MODEL}
        ]
        res = self.client.get(
            "/api/v1/parallel/history?device=inverter_1&key=pv_power&hours=24")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["device_key"], "inverter_1")
        sql = next(q for q, _ in self.db_calls
                   if "FROM rakurs_samples AS m" in q)
        self.assertIn("WHERE m.device_id=%s", sql)
        self.assertIn("s.signal_key=%s", sql)
        self.assertNotIn("m.quality=", sql)
        self.assertEqual(self.client.get(
            "/api/v1/parallel/history?device=inverter_1&key=unknown_metric").status_code, 400)
        self.assertEqual(self.client.get(
            "/api/v1/parallel/history?device=inverter_1&key=pv_power&hours=169").status_code, 400)

    def test_device_modbus_fields_are_scoped_to_single_inverter(self):
        self.fake_devices = [
            {"id": 22, "device_key": "inverter_2", "model": MODEL}
        ]
        res = self.client.get(
            "/api/v1/parallel/modbus-fields?device=inverter_2")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["count"], 0)
        self.assertFalse(res.get_json()["verified_only"])
        sql = next(q for q, _ in self.db_calls
                   if "FROM rakurs_modbus_fields AS f" in q)
        self.assertIn("m.device_id=%s", sql)
        self.assertIn("FROM rakurs_modbus_field_samples WHERE device_id=%s GROUP BY field_id", sql)
        self.assertIn("m.field_id=recent.field_id", sql)
        self.assertNotIn("f.verified=1", sql)
        self.assertNotIn("m.quality=", sql)

    def test_retired_parallel_simulation_api_is_not_available(self):
        for endpoint in (
            "/api/v1/parallel/simulation",
            "/api/v1/parallel/simulation/latest?device=sim_deye20k_parallel_1",
            "/api/v1/parallel/simulation/raw?device=sim_deye20k_parallel_1",
            "/api/v1/parallel/simulation/fields?device=sim_deye20k_parallel_1",
        ):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.client.get(endpoint).status_code, 404)

    def test_global_reads_are_scoped_to_one_stable_mysql_device(self):
        observed = []
        def one_real(sql, params=()):
            observed.append((sql, params))
            if "FROM rakurs_users WHERE id=%s" in sql:
                return {"id":123,"username":"tester","role":"admin",
                        "is_active":1,"session_version":1}
            if "SELECT d.id AS id" in sql:
                return {"id":21}
            if "m.value_decimal" in sql:
                return {"signal_key":"pv_power","unit":"кВт",
                        "quality":"simulated","value_decimal":Decimal("5.1")}
            if "SELECT COUNT(*)" in sql:
                return {"n":0}
            return None

        def mixed_rows(sql, params=()):
            return [{"signal_key":"pv_power","unit":"кВт","quality":"simulated",
                     "value_decimal":Decimal("5.1")}] if "m.value_decimal" in sql else []

        with patch.object(module, "one", side_effect=one_real), \
             patch.object(module, "rows", side_effect=mixed_rows):
            r = self.client.get("/api/v1/latest?keys=pv_power")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.get_json()["primary_device_id"], 21)
            self.assertEqual(r.get_json()["values"]["pv_power"]["value_decimal"], "5.1")
            h = self.client.get("/api/v1/history?key=pv_power&hours=24")
            self.assertEqual(h.status_code, 200)
            self.assertEqual(h.get_json()["primary_device_id"], 21)
            m = self.client.get("/api/v1/modbus-fields")
            self.assertEqual(m.status_code, 200)
            self.assertEqual(m.get_json()["primary_device_id"], 21)
            home = self.client.get("/")
            self.assertEqual(home.status_code, 200)
            self.assertIn(">5.1<", home.get_data(as_text=True))
            latest_sql = next(q for q, _ in observed if "m.value_decimal" in q)
            self.assertIn("m.device_id=%s", latest_sql)
            self.assertNotIn("quality='good'", latest_sql)

    def test_overview_uses_parallel_first_slot_not_old_single_device(self):
        observed = []
        original_devices = [
            {"id": 21, "device_key": "inverter_1", "slot_no": 1,
             "model": MODEL, "kind": "inverter"},
            {"id": 22, "device_key": "inverter_2", "slot_no": 2,
             "model": MODEL, "kind": "inverter"},
        ]
        self.fake_devices = original_devices
        def one_choice(sql, params=()):
            observed.append((sql, params))
            if "FROM rakurs_users WHERE id=%s" in sql:
                return {"id":123,"username":"tester","role":"admin",
                        "is_active":1,"session_version":1}
            if "SELECT d.id AS id" in sql:
                return {"id":21}
            if "FROM rakurs_samples AS m" in sql:
                return {"signal_key":"pv_power","unit":"кВт",
                        "value_decimal":Decimal("5.1"),"quality":"good"}
            if "COUNT(*)" in sql:
                return {"n":0}
            return None
        with patch.object(module, "one", side_effect=one_choice):
            home=self.client.get("/")
            self.assertEqual(home.status_code,200)
            d1=self.client.get("/api/v1/latest?keys=pv_power").get_json()
            self.assertEqual(d1["primary_device_id"],21)
            d2=self.client.get(
                "/api/v1/latest?keys=pv_power&device=inverter_2").get_json()
            self.assertEqual(d2["primary_device_id"],22)
            self.assertEqual(self.client.get(
                "/api/v1/history?key=pv_power&device=inverter_2"
                ).get_json()["primary_device_id"],22)
            self.assertEqual(self.client.get(
                "/api/v1/modbus-fields?device=inverter_2"
                ).get_json()["primary_device_id"],22)
            self.assertEqual(self.client.get(
                "/api/v1/latest?keys=pv_power&device=simulated_deye_sg05lp3"
                ).status_code,404)
            self.assertEqual(self.client.get(
                "/api/v1/latest?keys=pv_power&device=x%27%3BDROP"
                ).status_code,400)
        sql = next(sql for sql,_ in observed if "SELECT d.id AS id" in sql)
        self.assertIn("JOIN rakurs_parallel_members",sql)
        self.assertIn("pm.slot_no IN (1,2)",sql)
        self.assertNotIn("FROM rakurs_samples ORDER BY device_id",sql)

    def test_requires_authenticated_access(self):
        module.app.config.update(SECRET_KEY="parallel-testing-key")
        guest = module.app.test_client()
        self.assertEqual(guest.get("/api/v1/parallel").status_code, 401)
        self.assertEqual(guest.get("/api/v1/parallel/latest?device=inverter_1").status_code,
                         401)


if __name__ == "__main__":
    unittest.main()
