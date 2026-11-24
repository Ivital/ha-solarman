"""Static guardrails for two-host HTTPS Rakurs proxy templates."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "deploy" / "rakurs"

class RakursProxyTest(unittest.TestCase):
    def test_http_acme_and_redirect(self):
        s = (ROOT / "nginx-http.conf.template").read_text()
        self.assertIn("rakurs.zakryttya.org.ua www.rakurs.zakryttya.org.ua", s)
        self.assertIn("acme-challenge", s)
        self.assertIn("https://rakurs.zakryttya.org.ua$request_uri", s)
        self.assertNotIn("proxy_pass", s)

    def test_activation_retains_strict_tls_and_rollback(self):
        text = (ROOT / "activate_https.sh").read_text()
        self.assertIn("seq 1 20", text)
        self.assertIn("sudo rm -f -- \"$link\" \"$site\"", text)
        self.assertNotIn("curl -k", text)
        self.assertNotIn("--insecure", text)

    def test_https_local_upstream_and_san(self):
        s = (ROOT / "nginx-https.conf.template").read_text()
        self.assertIn("proxy_pass http://127.0.0.1:8123;", s)
        self.assertIn("server_name www.rakurs.zakryttya.org.ua;", s)
        self.assertIn("ssl_certificate_key", s)
        self.assertIn("proxy_set_header Upgrade $http_upgrade;", s)
        self.assertIn("proxy_set_header X-Forwarded-For $remote_addr;", s)
        self.assertNotIn("192.168.89.106", s)
        self.assertNotIn("0.0.0.0:8123", s)
