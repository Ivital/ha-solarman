"""Standalone RAKURS MySQL-only authentication and telemetry behavior tests."""
import json
from datetime import datetime
from decimal import Decimal
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parents[1]
RAKURS = REPO / "deploy" / "rakurs"
sys.path.insert(0, str(RAKURS))
from standalone import app as module


AUTH_ACCOUNT = {"id": 123, "username": "admin", "role": "admin",
                "is_active": 1, "session_version": 1}


class StandaloneMySqlTests(unittest.TestCase):
    def setUp(self):
        module.app.config.update(
            TESTING=True, SECRET_KEY="testing-only-secret-key-not-used-in-real-app",
            AUTH_READY=True, SESSION_COOKIE_SECURE=False,
        )
        self.client = module.app.test_client()
        self.data_one = lambda _sql, _params=(): None
        self.data_rows = lambda _sql, _params=(): []
        self.data_calls = []
        self.one_patch = patch.object(module, "one", side_effect=self.fake_one)
        self.rows_patch = patch.object(module, "rows", side_effect=self.fake_rows)
        self.one_patch.start()
        self.rows_patch.start()
        self.addCleanup(self.one_patch.stop)
        self.addCleanup(self.rows_patch.stop)
        with self.client.session_transaction() as sess:
            sess["uid"] = AUTH_ACCOUNT["id"]
            sess["ver"] = AUTH_ACCOUNT["session_version"]
            sess["at"] = int(time.time())
            sess["csrf"] = "csrf-for-tests"

    def fake_one(self, sql, params=()):
        if "FROM rakurs_users WHERE id=%s" in sql:
            return AUTH_ACCOUNT
        self.data_calls.append(("one", sql, params))
        return self.data_one(sql, params)

    def fake_rows(self, sql, params=()):
        self.data_calls.append(("rows", sql, params))
        return self.data_rows(sql, params)

    def test_protected_index_and_assets_exist(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertIn("RAKURS", self.client.get("/").get_data(as_text=True))
        for path in ("/static/style.css", "/static/app.js"):
            self.assertEqual(self.client.get(path).status_code, 200)
        js = (RAKURS / "standalone/static/app.js").read_text()
        self.assertIn('"/api/v1"', js)
        self.assertNotIn("/api/states", js)

    def test_remote_clients_denied(self):
        self.assertEqual(
            self.client.get("/", environ_overrides={"REMOTE_ADDR": "192.168.5.21"}).status_code,
            403,
        )

    def test_catalog_planned_unverified(self):
        r = self.client.get("/api/v1/catalog")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data["all_metric_count"], 316)
        self.assertTrue(data["read_only"])

    def test_empty_status(self):
        self.data_one = lambda sql, _: {"n": 0} if "COUNT(*)" in sql else None
        r = self.client.get("/api/v1/status")
        self.assertEqual(r.status_code, 200)
        obj = r.get_json()
        self.assertEqual(obj["data_source"], "mysql")
        self.assertEqual(obj["registered_devices"], 0)
        self.assertFalse(obj["has_real_samples"])
        self.assertFalse(obj["home_assistant_dependency"])

    def test_missing_latest_is_empty(self):
        self.assertEqual(
            self.client.get("/api/v1/latest?keys=pv_power,battery_soc").get_json()["values"], {}
        )
        self.assertEqual(len(self.data_calls), 4)
        self.assertEqual(self.data_calls[-1][-1], ("battery_soc",))

    def test_latest_returns_only_verified_measurements(self):
        val = {
            "signal_key": "pv_power", "label_uk": "Генерація",
            "unit": "кВт", "value_decimal": Decimal("2.500000000"),
            "raw_value": 2500, "quality": "good",
            "observed_at_utc": datetime(2026, 11, 24, 14, 30),
        }
        self.data_one = lambda _sql, _params: val
        res = self.client.get("/api/v1/latest?keys=pv_power")
        self.assertEqual(res.status_code, 200)
        obj = res.get_json()["values"]["pv_power"]
        self.assertEqual(obj["value_decimal"], "2.500000000")
        self.assertEqual(obj["observed_at_utc"], "2026-11-24T14:30:00Z")

    def test_full_modbus_fields_api_is_authenticated_and_demo_scoped(self):
        self.data_rows = lambda sql, params: [{
            "field_key": "p3_0001", "group_name": "Info", "field_name": "Device",
            "platform": "sensor", "unit": None,
            "registers_json": "[0]", "derived": 0, "verified": 0,
            "value_decimal": Decimal("5"), "value_text": None,
            "quality": "simulated",
            "observed_at_utc": datetime(2026, 11, 24, 17, 0),
        }]
        self.data_one = lambda sql, params: {"n": 815}
        response = self.client.get("/api/v1/modbus-fields?demo=1")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["simulation"])
        self.assertEqual(payload["register_count"], 815)
        self.assertEqual(payload["fields"][0]["field_key"], "p3_0001")
        self.assertEqual(payload["fields"][0]["quality"], "simulated")
        self.assertIn("m.quality='simulated'", self.data_calls[0][1])
        self.data_calls.clear()
        normal = self.client.get("/api/v1/modbus-fields")
        self.assertEqual(normal.status_code, 200)
        self.assertFalse(normal.get_json()["simulation"])
        self.assertTrue(any("m.quality<>'simulated'" in call[1]
                            for call in self.data_calls))

    def test_malformed_keys_no_data_queries(self):
        for p in ("/api/v1/latest?keys=", "/api/v1/latest?keys=pv%27%3Bdrop",
                  "/api/v1/history?key=pv%3BDELETE"):
            self.assertEqual(self.client.get(p).status_code, 400)
        self.assertEqual(self.data_calls, [])

    def test_history_bounds_and_empty_verified_signal(self):
        r = self.client.get("/api/v1/history?key=pv_power&hours=24")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["points"], [])
        self.assertEqual(len(self.data_calls), 3)
        for value in ("0", "169", "abc"):
            self.assertEqual(
                self.client.get("/api/v1/history?key=pv_power&hours="+value).status_code, 400
            )

    def test_primary_homepage_and_default_api_show_seeded_mysql_without_query_flag(self):
        value = {
            "signal_key": "pv_power", "unit": "кВт",
            "value_decimal": Decimal("5.100000000"), "quality": "simulated",
            "label_uk": "Генерація PV", "raw_value": None,
            "observed_at_utc": datetime(2026, 11, 24, 17, 0),
        }
        def lookup(sql, params):
            if "WHERE quality<>'simulated' LIMIT 1" in sql:
                return None
            if "WHERE quality='simulated' AND ingest_source=%s" in sql:
                return {"n": 1}
            if "COUNT(DISTINCT m.signal_id)" in sql:
                return {"n": 316}
            if "COUNT(*)" in sql:
                return {"n": 1}
            if "SELECT 1 AS n" in sql:
                return {"n": 1}
            if "m.value_decimal" in sql:
                return value
            return None
        self.data_one = lookup
        self.data_rows = lambda sql, params: (
            [value] if "FROM rakurs_signals AS s" in sql else []
        )
        homepage = self.client.get("/")
        self.assertEqual(homepage.status_code, 200)
        html = homepage.get_data(as_text=True)
        self.assertIn("Генерація PV", html)
        self.assertIn("5.1", html)
        self.assertIn("simulated", html)
        self.assertIn("rakurs-history-72h-v1", html)

        status = self.client.get("/api/v1/status").get_json()
        self.assertEqual(status["mode"], "simulation")
        self.assertEqual(status["simulated_signals"], 316)
        self.assertEqual(status["verified_signals"], 0)
        latest = self.client.get("/api/v1/latest?keys=pv_power").get_json()
        self.assertTrue(latest["simulation"])
        self.assertEqual(latest["values"]["pv_power"]["value_decimal"], "5.100000000")

        real = self.client.get("/api/v1/status?real=1").get_json()
        self.assertEqual(real["mode"], "real")

    def test_password_file_mode_and_ownership(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(module.ConfigurationError):
                module._password()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secret"
            path.write_text("fake-password", encoding="utf-8")
            path.chmod(0o644)
            with patch.dict(os.environ, {"RAKURS_DB_PASSWORD_FILE": str(path)}, clear=True):
                with self.assertRaises(module.ConfigurationError):
                    module._password()
            path.chmod(0o600)
            with patch.dict(os.environ, {"RAKURS_DB_PASSWORD_FILE": str(path)}, clear=True):
                self.assertEqual(module._password(), "fake-password")

    def test_no_ha_data_access(self):
        code = (RAKURS / "standalone/app.py").read_text()
        self.assertIn('SCHEMA = "rakurs"', code)
        self.assertIn('HOST = "192.168.5.30"', code)
        self.assertNotIn("sqlite3.connect", code)
        self.assertNotIn("requests.get", code)
        for fragment in ('cur.execute("INSERT', 'cur.execute("DELETE',
                         'cur.execute("DROP'):
            self.assertNotIn(fragment, code)


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        module.app.config.update(
            TESTING=True, SECRET_KEY="test-secret-used-only-in-ci",
            AUTH_READY=True, SESSION_COOKIE_SECURE=False,
        )
        self.client = module.app.test_client()
        self.calls = []
        self.one_patch = patch.object(module, "one", side_effect=self.fake_one)
        self.one_patch.start()
        self.addCleanup(self.one_patch.stop)
        self.data_one = lambda _sql, _params: None

    def fake_one(self, sql, params=()):
        self.calls.append((sql, params))
        return self.data_one(sql, params)

    def test_anonymous_get_redirects_and_api_fails_closed(self):
        self.assertEqual(self.client.get("/").status_code, 303)
        api = self.client.get("/api/v1/status")
        self.assertEqual(api.status_code, 401)
        self.assertEqual(api.get_json()["error"], "Потрібна авторизація")
        self.assertEqual(self.client.get("/login").status_code, 200)
        self.assertIn("csrf", self.client.get("/login").get_data(as_text=True))

    def test_server_rendered_deye_routes_are_authenticated(self):
        self.assertEqual(
            self.client.get("/deye").headers["Location"], "/login?next=/deye"
        )
        self.assertEqual(
            self.client.get("/deye/modbus").headers["Location"],
            "/login?next=/deye/modbus"
        )
        login = self.client.get("/login?next=/deye/modbus")
        self.assertIn('name="next" value="/deye/modbus"',
                      login.get_data(as_text=True))
        unsafe = self.client.get("/login?next=https://example.org/")
        self.assertNotIn('name="next"', unsafe.get_data(as_text=True))
        with self.client.session_transaction() as sess:
            sess["uid"] = AUTH_ACCOUNT["id"]
            sess["ver"] = AUTH_ACCOUNT["session_version"]
            sess["at"] = int(time.time())
        self.data_one = lambda sql, params: (
            AUTH_ACCOUNT if "FROM rakurs_users WHERE id=%s" in sql else None
        )
        ui = {
            "signal_key": "pv_power", "label_uk": "Генерація PV",
            "unit": "кВт", "value_decimal": Decimal("5.100000000"),
            "quality": "simulated",
            "observed_at_utc": datetime(2026, 11, 24, 17, 0),
        }
        field = {
            "field_key": "p3_0001", "group_name": "PV", "field_name": "PV Power",
            "unit": "W", "registers_json": "[672,673]",
            "value_decimal": Decimal("5100"), "value_text": None,
            "quality": "simulated",
        }
        with patch.object(module, "rows", side_effect=[[ui], [field]]) as query:
            summary = self.client.get("/deye")
            self.assertEqual(summary.status_code, 200)
            body = summary.get_data(as_text=True)
            self.assertIn("Генерація PV", body)
            self.assertIn("5.1", body)
            self.assertIn("DEYE", body)
            self.assertNotIn("/static/app.js", body)
            modbus = self.client.get("/deye/modbus")
            self.assertEqual(modbus.status_code, 200)
            modbus_html = modbus.get_data(as_text=True)
            self.assertIn("PV Power", modbus_html)
            self.assertIn("0x02A0, 0x02A1", modbus_html)
            self.assertNotIn("/static/app.js", modbus_html)
            self.assertEqual(query.call_count, 2)
            for call in query.call_args_list:
                self.assertIn("m.quality='simulated'", call.args[0])
                self.assertIn("m.ingest_source=%s", call.args[0])

    def test_demo_login_redirect_preserves_explicit_selection(self):
        from werkzeug.security import generate_password_hash
        username = "demo_account"
        password = "unshared-test-password-for-simulation"
        account = {
            "id": 79, "username": username, "role": "admin",
            "password_hash": generate_password_hash(password, method="scrypt:32768:8:1"),
            "is_active": 1, "session_version": 1,
        }
        self.data_one = lambda sql, _params: (
            account if "FROM rakurs_users WHERE username=%s" in sql
            else None
        )
        anonymous = self.client.get("/?demo=1")
        self.assertEqual(anonymous.status_code, 303)
        self.assertTrue(anonymous.headers["Location"].endswith("/login?demo=1"))
        response = self.client.get("/login?demo=1")
        self.assertEqual(response.status_code, 200)
        self.assertIn('name="demo" value="1"', response.get_data(as_text=True))
        with self.client.session_transaction() as sess:
            csrf = sess["csrf"]

        class FakeCursor:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, *args):
                return 1

        class FakeConn:
            def cursor(self):
                return FakeCursor()

        with patch.object(module, "conn", return_value=FakeConn()):
            posted = self.client.post("/login", data={
                "username": username, "password": password,
                "csrf": csrf, "demo": "1"
            })
        self.assertEqual(posted.status_code, 303)
        self.assertTrue(posted.headers["Location"].endswith("/?demo=1"))

    def test_versioned_js_prevents_stale_dashboard_after_ha_migration(self):
        html = (RAKURS / "standalone/templates/index.html").read_text()
        js = (RAKURS / "standalone/static/app.js").read_text()
        script = (RAKURS / "standalone/repair_dashboard_assets.sh").read_text()
        self.assertIn('/static/app-stable-20261124.js', html)
        self.assertIn('data-initial-mode=', html)
        stable = (RAKURS / "standalone/static/app-stable-20261124.js").read_text()
        self.assertEqual(stable, js)
        self.assertIn('restoreServerReadings()', stable)
        self.assertIn('["modbus", "Modbus", "▤"]', js)
        self.assertIn("let DEMO_MODE = new URLSearchParams(", js)
        self.assertIn("sha256sum", script)
        self.assertIn('https://$DOMAIN$ASSET_PATH', script)
        self.assertIn("VERSIONED_SCRIPT_IN_RENDERED_HTML=PASS", script)
        self.assertIn("RAKURS_ASSET_FIX=PASS", script)
        self.assertNotIn("DELETE FROM", script)
        self.assertNotIn("TRUNCATE", script)
        self.assertNotIn("docker ", script)

    def test_primary_dashboard_displays_existing_samples_without_demo_url(self):
        js = (RAKURS / "standalone/static/app.js").read_text()
        self.assertIn('DEMO_MODE = state.status ? state.status.mode === "simulation"', js)
        self.assertIn('restoreServerReadings();', js)
        self.assertIn('critical.every(key => values[key]', js)
        self.assertIn('const FORCE_REAL_MODE =', js)
        self.assertIn('const selection = FORCE_REAL_MODE ? "real=1"', js)
        self.assertNotIn('window.location.replace(', js)
        self.assertNotIn('message("СИМУЛЯЦІЯ DEYE — НЕ РЕАЛЬНІ ВИМІРЮВАННЯ"', js)
        html = (RAKURS / "standalone/templates/index.html").read_text()
        self.assertIn('Показники Deye з бази даних', html)
        self.assertIn('{{ item.value }}', html)

    def test_browser_login_and_logout_forms_allowed_by_csp(self):
        login = self.client.get("/login")
        self.assertEqual(login.status_code, 200)
        self.assertIn('action="/login" method="post"', login.get_data(as_text=True))
        login_csp = login.headers["Content-Security-Policy"]
        self.assertIn("form-action 'self'", login_csp)
        self.assertNotIn("form-action 'none'", login_csp)
        self.assertIn("frame-ancestors 'none'", login_csp)

        with self.client.session_transaction() as sess:
            sess["uid"] = AUTH_ACCOUNT["id"]
            sess["ver"] = AUTH_ACCOUNT["session_version"]
            sess["at"] = int(time.time())
        self.data_one = lambda sql, params: (
            AUTH_ACCOUNT if "FROM rakurs_users WHERE id=%s" in sql else None
        )
        with patch.object(module, "rows", return_value=[]):
            dashboard = self.client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("form-action 'self'", dashboard.headers["Content-Security-Policy"])

    def test_login_requires_csrf_and_no_default_credentials(self):
        self.assertEqual(self.client.post("/login", data={
            "username": "admin", "password": "incorrect", "csrf": "bogus",
        }).status_code, 400)
        self.assertNotIn("CREATE USER", (RAKURS / "standalone/auth_schema.sql").read_text())

    def test_logout_must_be_post_and_csrf_protected(self):
        with self.client.session_transaction() as sess:
            sess["uid"] = 123
            sess["ver"] = 1
            sess["at"] = int(time.time())
            sess["csrf"] = "token"
        self.data_one = lambda sql, p: AUTH_ACCOUNT if "WHERE id=%s" in sql else None
        self.assertEqual(self.client.get("/logout").status_code, 405)
        self.assertEqual(self.client.post("/logout", data={"csrf": "invalid"}).status_code, 400)
        self.assertEqual(self.client.post("/logout", data={"csrf": "token"}).status_code, 303)
        self.assertEqual(self.client.get("/api/v1/auth").status_code, 401)

    def test_session_expiry_and_revocation(self):
        with self.client.session_transaction() as sess:
            sess["uid"] = 123
            sess["ver"] = 1
            sess["at"] = int(time.time()) - 9 * 3600
        self.assertEqual(self.client.get("/api/v1/status").status_code, 401)
        with self.client.session_transaction() as sess:
            sess["uid"] = 123
            sess["ver"] = 1
            sess["at"] = int(time.time())
        self.data_one = lambda sql, p: {
            **AUTH_ACCOUNT, "session_version": 2
        } if "WHERE id=%s" in sql else None
        self.assertEqual(self.client.get("/api/v1/status").status_code, 401)

    def test_no_secret_file_means_app_unavailable(self):
        before = module.app.config["AUTH_READY"]
        try:
            module.app.config["AUTH_READY"] = False
            self.assertEqual(self.client.get("/").status_code, 503)
            self.assertEqual(self.client.get("/login").status_code, 503)
        finally:
            module.app.config["AUTH_READY"] = before

    def test_successful_login_rotates_session(self):
        from werkzeug.security import generate_password_hash
        username = "administrator"
        password = "a-long-test-password-unique-123!"
        password_hash = generate_password_hash(password, method="scrypt:32768:8:1")
        account = {
            "id": 77, "username": username, "role": "admin",
            "password_hash": password_hash, "is_active": 1, "session_version": 1,
        }
        def lookup(sql, params):
            if "FROM rakurs_login_attempts" in sql:
                return None
            if "FROM rakurs_users WHERE username=%s" in sql:
                return account
            if "FROM rakurs_users WHERE id=%s" in sql:
                return account
            return None
        self.data_one = lookup
        class FakeCursor:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def execute(self, *args): return 1
        class FakeConn:
            def cursor(self): return FakeCursor()
        with patch.object(module, "conn", return_value=FakeConn()):
            self.client.get("/login")
            with self.client.session_transaction() as sess:
                csrf = sess["csrf"]
            response = self.client.post("/login", data={
                "username": username, "password": password, "csrf": csrf,
            })
            self.assertEqual(response.status_code, 303)
            me = self.client.get("/api/v1/auth")
            self.assertEqual(me.status_code, 200)
            self.assertEqual(me.get_json()["username"], username)

    def test_failed_login_is_rejected_without_disclosing_user(self):
        class FakeCursor:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def execute(self, *args): return 1
        class FakeConn:
            def cursor(self): return FakeCursor()
        with patch.object(module, "conn", return_value=FakeConn()):
            self.client.get("/login")
            with self.client.session_transaction() as sess:
                csrf = sess["csrf"]
            result = self.client.post("/login", data={
                "username": "missing_user", "password": "wrong", "csrf": csrf,
            })
        self.assertEqual(result.status_code, 401)
        self.assertIn("Неправильний логін або пароль", result.get_data(as_text=True))
        self.assertEqual(self.client.get("/api/v1/auth").status_code, 401)

    def test_auth_scripts_do_not_embed_credentials(self):
        check_script = (RAKURS / "standalone/apply_auth_schema.sh").read_text()
        web_user = (RAKURS / "standalone/provision_mysql_web_user.py").read_text()
        bootstrap = (RAKURS / "standalone/bootstrap_admin.py").read_text()
        for code in (check_script, web_user, bootstrap):
            self.assertNotIn("MYSQL_PWD", code)
            self.assertNotIn("--password=", code)
            self.assertNotIn("DROP DATABASE", code)
            self.assertNotIn("DROP TABLE", code)
        self.assertIn("getpass.getpass", web_user)
        self.assertIn("generate_password_hash", bootstrap)

    def test_immediate_legacy_removal_scoped_to_deye_homeassistant(self):
        script = (RAKURS / "standalone/remove_legacy_ha_now.sh").read_text()
        template = (RAKURS / "standalone/nginx-maintenance.conf").read_text()
        for marker in (
            "deye-homeassistant", "homeassistant", "deye-monitoring",
            "192.168.5.31", "RAKURS_MAINTENANCE_HTTPS=PASS",
            "tar -tzf", "rm -rf --", "--audit", "--remove-now",
            "proxy_pass http://127.0.0.1:8123;", "docker image rm",
            "RAKURS_MAINTENANCE_UPGRADING",
        ):
            if marker == "192.168.5.31":
                continue
            self.assertIn(marker, script)
        self.assertNotIn("docker system prune", script)
        self.assertNotIn("docker compose down", script)
        self.assertNotIn("docker image rm -f", script)
        self.assertIn("RAKURS_MAINTENANCE_UPGRADING", template)
        self.assertIn("return 503", template)
        self.assertIn("curl --noproxy '*'", script)
        self.assertIn("FAILED_STEP=$STEP", script)
        self.assertIn('X-Rakurs-Maintenance', template)

    def test_post_switch_retirement_backups_before_deleting(self):
        script = (RAKURS / "standalone/retire_homeassistant.sh").read_text()
        self.assertIn("trap rollback_on_error ERR", script)
        self.assertIn('sudo docker start "$CONTAINER" || true', script)
        self.assertIn("sudo tar --one-file-system", script)
        self.assertIn('sudo tar -tzf "$BACKUP"', script)
        self.assertIn('sudo sha256sum "$BACKUP"', script)
        self.assertIn("--noproxy '*'", script)
        self.assertLess(
            script.index('sudo tar -tzf "$BACKUP"'),
            script.index('sudo docker rm "$CONTAINER"'),
        )
        self.assertLess(
            script.index('sudo docker rm "$CONTAINER"'),
            script.index('sudo rm -rf -- "$DATA"'),
        )

    def test_publish_supports_prior_maintenance_vhost(self):
        script = (RAKURS / "standalone/switch_rakurs_nginx.sh").read_text()
        self.assertIn("RAKURS_MAINTENANCE_UPGRADING", script)
        self.assertIn("proxy_pass http://127.0.0.1:8123;", script)
        self.assertIn("rakurs-https.conf", script)
        self.assertIn("--noproxy '*'", script)
        self.assertIn("for attempt in $(seq 1 20)", script)
        self.assertIn("STANDALONE_PAGE=$MATCH", script)
        self.assertIn('RAKURS_HTTPS_UNAUTHORIZED_API=401', script)
        self.assertIn('/api/v1/status', script)
        self.assertIn("RAKURS_HTTPS_LOGIN=PASS", script)


class FullModbusSimulationTests(unittest.TestCase):
    def test_installed_web_files_checked_as_root(self):
        script = (RAKURS / "standalone/install_full_modbus_demo.sh").read_text()
        self.assertIn('sudo test -s "$TARGET/app.py"', script)
        self.assertIn('sudo test -s "$TARGET/static/app.js"', script)
        self.assertNotIn('\ntest -s "$TARGET/app.py"', script)
        self.assertNotIn('\ntest -s "$TARGET/static/app.js"', script)
        self.assertIn('STOP: installed Rakurs app.py missing or empty', script)
        self.assertIn('STOP: installed Rakurs app.js missing or empty', script)

    def test_full_catalog_matches_every_yaml_source_item(self):
        import yaml
        profile = yaml.safe_load((
            REPO / "custom_components/solarman/inverter_definitions/deye_p3.yaml"
        ).read_text(encoding="utf-8"))
        catalog = json.loads((
            RAKURS / "modbus_full_catalog.json"
        ).read_text(encoding="utf-8"))
        extracted = [
            (group["group"], item)
            for group in profile["parameters"]
            for item in group["items"]
        ]
        fields = catalog["field_definitions"]
        self.assertEqual(len(extracted), 624)
        self.assertEqual(len(fields), len(extracted))
        addresses = set()

        def flatten_registers(value):
            if isinstance(value, int) and not isinstance(value, bool):
                yield value
            elif isinstance(value, list):
                for item in value:
                    yield from flatten_registers(item)

        def all_register_addresses(value):
            if isinstance(value, dict):
                for k, v in value.items():
                    if k == "registers":
                        yield from flatten_registers(v)
                    else:
                        yield from all_register_addresses(v)
            elif isinstance(value, list):
                for item in value:
                    yield from all_register_addresses(item)

        for field, (group, entry) in zip(fields, extracted):
            self.assertEqual(field["group"], group)
            self.assertEqual(field["name"], entry.get("name") or "(unnamed switch)")
            from_yaml = set(all_register_addresses(entry))
            self.assertEqual(set(field["registers"]), from_yaml)
            self.assertFalse(field["verified"] if "verified" in field else False)
            addresses.update(field["registers"])
        self.assertEqual(len(addresses), 815)
        self.assertEqual(catalog["register_count"], 815)

    def test_one_shot_dry_run_covers_every_field_without_db(self):
        import runpy
        ns = runpy.run_path(str(RAKURS / "standalone/simulate_deye_once.py"))
        fields, ui_metrics, registers = ns["prepare"]()
        self.assertEqual(len(fields), 624)
        self.assertEqual(len(ui_metrics), 316)
        self.assertEqual(len(registers), 815)
        self.assertEqual(len(set(registers)), 815)
        self.assertEqual(ns["INGEST_SOURCE"], "rakurs_demo_one_shot")
        self.assertEqual(ns["QUALITY"], "simulated")
        for i, field in enumerate(fields, 1):
            number, text_value = ns["_demo_value"](
                field["key"], field["name"], field["unit"], i
            )
            self.assertTrue(number is not None or text_value is not None)
        for i, field in enumerate(ui_metrics, 1):
            number, text_value = ns["_demo_value"](
                field["key"], field["label"], field.get("unit"), i
            )
            self.assertTrue(number is not None or text_value is not None)
        src = (RAKURS / "standalone/simulate_deye_once.py").read_text()
        self.assertNotIn("socket.create_connection", src)
        self.assertNotIn("ModbusTcpClient", src)
        self.assertNotIn("requests.post", src)
        self.assertIn("db.rollback()", src)
        self.assertIn("db.commit()", src)

    def test_schema_adds_only_three_typed_tables(self):
        schema = (RAKURS / "standalone/modbus_full_schema.sql").read_text()
        self.assertEqual(schema.count("CREATE TABLE IF NOT EXISTS"), 3)
        self.assertIn("rakurs_modbus_fields", schema)
        self.assertIn("rakurs_modbus_field_samples", schema)
        self.assertIn("rakurs_modbus_register_inventory", schema)
        self.assertIn("value_text VARCHAR(512)", schema)
        self.assertIn("registers_json JSON", schema)
        for dangerous in ("DROP TABLE", "TRUNCATE TABLE", "DELETE FROM",
                          "ALTER TABLE rakurs_samples"):
            self.assertNotIn(dangerous, schema)


if __name__ == "__main__":
    unittest.main()
