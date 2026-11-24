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
        self.assertEqual(len(self.data_calls), 2)
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

    def test_malformed_keys_no_data_queries(self):
        for p in ("/api/v1/latest?keys=", "/api/v1/latest?keys=pv%27%3Bdrop",
                  "/api/v1/history?key=pv%3BDELETE"):
            self.assertEqual(self.client.get(p).status_code, 400)
        self.assertEqual(self.data_calls, [])

    def test_history_bounds_and_empty_verified_signal(self):
        r = self.client.get("/api/v1/history?key=pv_power&hours=24")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["points"], [])
        self.assertEqual(len(self.data_calls), 1)
        for value in ("0", "169", "abc"):
            self.assertEqual(
                self.client.get("/api/v1/history?key=pv_power&hours="+value).status_code, 400
            )

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


if __name__ == "__main__":
    unittest.main()
