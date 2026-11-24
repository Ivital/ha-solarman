"""Read-only Nginx diagnostics must reveal routing without dumping secrets."""
import importlib.util
from pathlib import Path
import unittest

REPO = Path(__file__).resolve().parents[1]
PATH = REPO / "deploy/rakurs/standalone/nginx_rakurs_route_audit.py"
spec = importlib.util.spec_from_file_location("nginx_rakurs_route_audit", PATH)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class NginxRouteAuditTests(unittest.TestCase):
    def test_two_different_listen_bindings(self):
        data = """# configuration file /etc/nginx/sites-enabled/site-a.conf:
server {
    listen 127.0.0.1:443 ssl;
    server_name other.example.test;
    proxy_pass http://127.0.0.1:8123;
    ssl_certificate_key SUPER_PRIVATE_KEY_PATH;
}
# configuration file /etc/nginx/sites-enabled/site-b.conf:
server {
    listen 443 ssl;
    server_name rakurs.zakryttya.org.ua;
    location / { return 503 "RAKURS_MAINTENANCE_UPGRADING"; }
}
"""
        blocks = audit.server_blocks(data)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["listen"], ["127.0.0.1:443 ssl"])
        self.assertEqual(blocks[0]["proxy_pass"], ["http://127.0.0.1:8123"])
        self.assertEqual(blocks[1]["server_name"], ["rakurs.zakryttya.org.ua"])
        self.assertTrue(blocks[1]["maintenance_marker"])
        self.assertNotIn("SUPER_PRIVATE_KEY_PATH", str(blocks))

    def test_config_not_modified_or_dumped(self):
        code = PATH.read_text(encoding="utf-8")
        self.assertIn("--noproxy", code)
        self.assertIn('"nginx", "-T"', code)
        self.assertIn('"ss", "-ltnp"', code)
        for action in ("nginx -s reload", "systemctl reload", "docker stop",
                       "rm -rf", "DROP TABLE", "sudo nginx -T | cat"):
            self.assertNotIn(action, code)


if __name__ == "__main__":
    unittest.main()
