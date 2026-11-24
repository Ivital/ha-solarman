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
                return self.fake_devices
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
        self.assertIn("d.device_key<>%s", sql)
        self.assertEqual(params[0], module.DEMO_DEVICE_KEY)

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
        self.assertIn("m.quality<>'simulated'", query)
        self.assertIn("s.verified=1", query)
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
        self.assertIn("m.quality<>'simulated'", sql)
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
        self.assertTrue(res.get_json()["verified_only"])
        sql = next(q for q, _ in self.db_calls
                   if "FROM rakurs_modbus_fields AS f" in q)
        self.assertIn("m.device_id=%s", sql)
        self.assertIn("ms.device_id=%s", sql)
        self.assertIn("f.verified=1", sql)
        self.assertIn("m.quality<>'simulated'", sql)

    def test_simulated_two_device_archive_only_exposes_simulated_data(self):
        queries = []
        def fake_one(sql, params=()):
            queries.append((sql, params))
            if "FROM rakurs_users WHERE id=%s" in sql:
                return {"id":123,"username":"tester","role":"admin",
                        "is_active":1,"session_version":1}
            if "FROM information_schema.tables" in sql:
                return {"n":3}
            if "FROM rakurs_devices AS d" in sql and "pm.member_kind='simulation'" in sql:
                return {"id":55,"device_key":"sim_deye20k_parallel_1","slot_no":1}
            if "FROM rakurs_modbus_poll_runs" in sql:
                return {"id":77,"observed_at_utc":datetime(2026,11,24,20,0)}
            if "COUNT(*) AS n FROM rakurs_modbus_register_samples" in sql:
                return {"n":815}
            if "COUNT(*) AS n FROM rakurs_modbus_field_samples AS m" in sql:
                return {"n":624}
            return None

        def fake_rows(sql, params=()):
            queries.append((sql, params))
            if "FROM rakurs_parallel_members AS pm" in sql:
                return [
                    {"slot_no":1,"device_key":"sim_deye20k_parallel_1",
                     "model":MODEL,"raw_registers":815,"decoded_fields":624,
                     "ui_fields":316},
                    {"slot_no":2,"device_key":"sim_deye20k_parallel_2",
                     "model":MODEL,"raw_registers":815,"decoded_fields":624,
                     "ui_fields":316},
                ]
            if "FROM rakurs_samples AS m" in sql:
                return [{
                    "signal_key":"pv_power","label_uk":"Сонце","unit":"кВт",
                    "value_decimal":Decimal("5.1"),"quality":"simulated",
                    "observed_at_utc":datetime(2026,11,24,20,0),
                }]
            if "FROM rakurs_modbus_register_samples" in sql:
                return [{"function_code":3,"register_address":1234,
                         "raw_uint16":54321,"quality":"simulated",
                         "observed_at_utc":datetime(2026,11,24,20,0)}]
            if "FROM rakurs_modbus_field_samples AS m" in sql:
                return [{"field_key":"p3_0001","field_name":"PV output",
                         "unit":"кВт","group_name":"PV",
                         "value_decimal":Decimal("5.1"),"value_text":None,
                         "quality":"simulated",
                         "observed_at_utc":datetime(2026,11,24,20,0)}]
            return []

        with patch.object(module, "one", side_effect=fake_one), \
             patch.object(module, "rows", side_effect=fake_rows):
            overview = self.client.get("/api/v1/parallel/simulation")
            self.assertEqual(overview.status_code, 200)
            self.assertTrue(overview.get_json()["simulation"])
            self.assertEqual(len(overview.get_json()["devices"]), 2)
            value = self.client.get(
                "/api/v1/parallel/simulation/latest?device=sim_deye20k_parallel_1")
            self.assertEqual(value.status_code, 200)
            self.assertTrue(value.get_json()["simulation"])
            self.assertEqual(value.get_json()["values"]["pv_power"]["quality"],
                             "simulated")
            raw = self.client.get(
                "/api/v1/parallel/simulation/raw?device=sim_deye20k_parallel_1"
                "&offset=0&limit=200")
            self.assertEqual(raw.status_code, 200)
            self.assertEqual(raw.get_json()["total"], 815)
            self.assertEqual(raw.get_json()["raw_registers"][0]["raw_uint16"], 54321)
            decoded = self.client.get(
                "/api/v1/parallel/simulation/fields?device=sim_deye20k_parallel_1"
                "&offset=0&limit=80")
            self.assertEqual(decoded.status_code, 200)
            self.assertTrue(decoded.get_json()["simulation"])
            self.assertEqual(decoded.get_json()["total"], 624)
            self.assertEqual(decoded.get_json()["fields"][0]["field_key"], "p3_0001")
            self.assertEqual(decoded.get_json()["fields"][0]["value_decimal"], "5.1")
            self.assertEqual(self.client.get(
                "/api/v1/parallel/simulation/fields?device=sim_deye20k_parallel_1"
                "&offset=0&limit=101").status_code, 400)
            self.assertEqual(self.client.get(
                "/api/v1/parallel/simulation/raw?device=sim_deye20k_parallel_1"
                "&offset=0&limit=501").status_code, 400)
            self.assertEqual(self.client.get(
                "/api/v1/parallel/simulation/latest?device=deye_real_one").status_code,
                             404)
        self.assertTrue(all("INSERT " not in q and "UPDATE " not in q for q, _ in queries))

    def test_two_real_inverters_never_mix_global_readings(self):
        def one_real(sql, params=()):
            if "FROM rakurs_users WHERE id=%s" in sql:
                return {"id":123,"username":"tester","role":"admin",
                        "is_active":1,"session_version":1}
            if "WHERE quality<>'simulated' LIMIT 1" in sql:
                return {"n":1}
            if "WHERE kind='inverter' AND model LIKE" in sql:
                return {"n":2}
            return {"n":0} if "COUNT(*)" in sql else None

        def mixed_rows(sql, params=()):
            return [{"signal_key":"pv_power","unit":"кВт","quality":"good",
                     "value_decimal":Decimal("5.1")}] if "FROM rakurs_signals" in sql else []

        with patch.object(module, "one", side_effect=one_real), \
             patch.object(module, "rows", side_effect=mixed_rows):
            r = self.client.get("/api/v1/latest?keys=pv_power")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.get_json()["values"], {})
            self.assertTrue(r.get_json()["device_selection_required"])
            h = self.client.get("/api/v1/history?key=pv_power&hours=24")
            self.assertEqual(h.status_code, 200)
            self.assertEqual(h.get_json()["points"], [])
            self.assertTrue(h.get_json()["device_selection_required"])
            m = self.client.get("/api/v1/modbus-fields")
            self.assertEqual(m.status_code, 200)
            self.assertEqual(m.get_json()["fields"], [])
            self.assertTrue(m.get_json()["device_selection_required"])
            home = self.client.get("/")
            self.assertEqual(home.status_code, 200)
            self.assertNotIn('>5.1<', home.get_data(as_text=True))

    def test_requires_authenticated_access(self):
        module.app.config.update(SECRET_KEY="parallel-testing-key")
        guest = module.app.test_client()
        self.assertEqual(guest.get("/api/v1/parallel").status_code, 401)
        self.assertEqual(guest.get("/api/v1/parallel/latest?device=inverter_1").status_code,
                         401)


if __name__ == "__main__":
    unittest.main()
