#!/usr/bin/env python3
"""RAKURS V7 full-page Chromium regression against the real Jinja template.

All endpoints below are local, deterministic, read-only API doubles. The test
never connects to production, MySQL, TRB145 or Modbus; it proves frontend
integration and fallback behaviour, not a live database acceptance.
"""
from __future__ import annotations

import json
import mimetypes
import os
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATIC = ROOT / "deploy/rakurs/standalone/static"
TEMPLATES = ROOT / "deploy/rakurs/standalone/templates"
OUT = Path(os.environ.get("RAKURS_V7_SCREENSHOTS", "artifacts/rakurs-v7")).resolve()
OUT.mkdir(parents=True, exist_ok=True)
TEMPLATE = None  # assigned by run(); importable by other unittest suites
MEASURED = "2026-11-25T00:16:18Z"
D1 = "integration_deye_1"
D2 = "integration_deye_2"


def reading(value, unit="кВт", stamp=MEASURED):
    return {"value_decimal": value, "unit": unit, "observed_at_utc": stamp,
            "register_verified": False, "sign_verified": False}


def device_values(slot):
    factor = 1 if slot == 1 else 1.067
    return {
        "pv_power": reading(5.1 if slot == 1 else 5.432),
        "load_power": reading(round(6.4 * factor, 3)),
        "grid_power": reading(0),
        "battery_power": reading(-1.3 if slot == 1 else -1.385),
        "battery_soc": reading(72 if slot == 1 else 73.8, "%"),
    }


SSR = [
    {"value": "5,1", "unit": "кВт", "label": "PV", "quality": "valid", "icon": "☀"},
    {"value": "6,4", "unit": "кВт", "label": "Навантаження", "quality": "valid", "icon": "⌂"},
    {"value": "0", "unit": "кВт", "label": "Мережа", "quality": "valid", "icon": "⌁"},
    {"value": "72", "unit": "%", "label": "Заряд АКБ", "quality": "valid", "icon": "▣"},
]


class TestServer(ThreadingHTTPServer):
    daemon_threads = True
    scenario = "normal"

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.calls = []
        self.lock = threading.Lock()

    def record(self, verb, target):
        with self.lock:
            self.calls.append((verb, target))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def respond(self, status, content, media_type="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", media_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def reply(self, obj, status=200):
        self.respond(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_POST(self):
        self.server.record("POST", self.path)
        self.reply({"error": "READ_ONLY_FIXTURE"}, 405)

    def do_GET(self):
        parts = urlsplit(self.path)
        path, args = parts.path, parse_qs(parts.query)
        if path == "/":
            html = TEMPLATE.render(initial_cards=SSR).encode("utf-8")
            self.respond(200, html, "text/html; charset=utf-8")
            return
        if path.startswith("/static/"):
            name = path.removeprefix("/static/")
            asset = (STATIC / name).resolve()
            if not asset.is_relative_to(STATIC.resolve()) or not asset.is_file():
                self.reply({"error": "asset missing"}, 404)
                return
            kind, _encoding = mimetypes.guess_type(asset.name)
            self.respond(200, asset.read_bytes(), kind or "application/octet-stream")
            return
        if not path.startswith("/api/v1/"):
            self.reply({"error": "not found"}, 404)
            return
        self.server.record("GET", self.path)
        scenario = self.server.scenario
        if path == "/api/v1/catalog":
            self.reply({"groups": {}, "all_metric_count": 0})
        elif path == "/api/v1/status":
            self.reply({"mode": "real", "registered_devices": 2,
                        "displayed_signals": 10, "has_real_samples": True,
                        "collector_active": False,
                        "physical_topology_verified": False,
                        "register_semantics_verified": False})
        elif path == "/api/v1/signals":
            self.reply({"signals": []})
        elif path == "/api/v1/auth":
            self.reply({"authenticated": True, "role": "viewer",
                        "username": "fixture-viewer", "csrf": "fixture-only"})
        elif path == "/api/v1/parallel":
            if scenario == "twin-down":
                self.reply({"error": "API offline"}, 503)
            else:
                self.reply({"devices": [
                    {"device_key": D1, "slot_no": 1, "model": "Deye 20 kW"},
                    {"device_key": D2, "slot_no": 2, "model": "Deye 20 kW"}]})
        elif path == "/api/v1/parallel/latest":
            key = args.get("device", [""])[0]
            if key not in {D1, D2}:
                self.reply({"error": "unregistered device"}, 404)
            elif scenario == "both-down" or (scenario == "d2-down" and key == D2):
                self.reply({"error": "API offline"}, 503)
            else:
                self.reply({"device_key": key,
                            "values": {} if scenario == "empty" else device_values(1 if key == D1 else 2)})
        elif path == "/api/v1/parallel/history":
            key = args.get("key", [""])[0]
            device = args.get("device", [""])[0]
            hours = int(args.get("hours", ["24"])[0])
            if device not in {D1, D2} or key not in {
                "pv_power", "load_power", "grid_power", "battery_power", "battery_soc"
            } or hours not in {24, 72, 168}:
                self.reply({"error": "bad query"}, 400)
                return
            start = datetime(2026, 11, 24, tzinfo=timezone.utc)
            values = []
            for i in range(12):
                stamp = (start + timedelta(hours=2 * i)).isoformat().replace("+00:00", "Z")
                value = 0 if i == 0 else i * (0.44 if device == D1 else 0.37)
                if key == "battery_soc":
                    value = 70 + i
                values.append(reading(value, "%" if key == "battery_soc" else "кВт", stamp))
            self.reply({"points": [] if scenario == "empty" else values,
                        "device_key": device, "signal_key": key, "hours": hours})
        elif path == "/api/v1/latest":
            if scenario == "legacy-down":
                self.reply({"error": "API offline"}, 503)
            else:
                self.reply({"values": device_values(1)})
        elif path == "/api/v1/parallel/events":
            self.reply({"events": []})
        elif path == "/api/v1/events":
            self.reply({"events": []})
        else:
            self.reply({"error": "not implemented by test double"}, 404)


def wait_rendered(page):
    page.wait_for_selector(".rv7 [data-node='pv1']", state="attached", timeout=20000)
    page.wait_for_selector(".rv7 .rv7-chart-line", timeout=20000)


def has_real_ssr(page):
    return page.locator('[aria-label="Показники Deye з бази даних"]').count() == 1


def run():
    global TEMPLATE
    from jinja2 import Environment, FileSystemLoader
    from playwright.sync_api import sync_playwright
    TEMPLATE = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True).get_template("index.html")
    server = TestServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:" + str(server.server_port)

    report = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            try:
                for width, height, theme in [(360, 780, "light"), (390, 844, "light"),
                                             (412, 915, "dark"), (768, 1024, "light"),
                                             (1366, 768, "dark"), (1920, 1080, "light")]:
                    server.scenario = "normal"
                    server.calls = []
                    context = browser.new_context(
                        viewport={"width": width, "height": height},
                        color_scheme=theme, locale="uk-UA", reduced_motion="reduce")
                    page = context.new_page()
                    page_errors = []
                    page.on("pageerror", lambda error: page_errors.append(str(error)))
                    page.goto(base + "/?ux=v7#/overview", wait_until="load")
                    wait_rendered(page)
                    assert not page_errors, page_errors
                    assert "5,1 кВт" in page.locator(
                        ".rv7 [data-node='pv1'] .rv7-node-value").first.text_content()
                    assert "5,43 кВт" in page.locator(
                        ".rv7 [data-node='pv2'] .rv7-node-value").first.text_content()
                    assert not has_real_ssr(page), "Trusted SSR should be replaced only by verified twin response"
                    if width < 600:
                        assert page.locator("body.rakurs-v7-overview").count() == 1
                        assert not page.locator(".topbar .mobile-heading-row").is_visible()
                        first_screen = page.evaluate("""() => ({
                          navTop: document.querySelector(".mobile-tabs").getBoundingClientRect().top,
                          gridBottom: document.querySelector(
                            ".rv7-mobile [data-node='grid']").getBoundingClientRect().bottom,
                          batteryBottom: document.querySelector(
                            ".rv7-mobile [data-node='battery']").getBoundingClientRect().bottom
                        })""")
                        assert max(first_screen["gridBottom"], first_screen["batteryBottom"]) <                             first_screen["navTop"] - 6,                             "V7 critical energy nodes must be fully above the mobile bottom nav: " + str(first_screen)
                    assert not page.locator(".rv7 .rv7-pill.is-live").count()
                    assert page.locator(".rv7 .rv7-warnings").count() == 1
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= window.innerWidth + 2"
                    ), "Live template introduced horizontal page overflow"
                    assert all(method == "GET" for method, _target in server.calls)
                    assert not any(path.startswith("/api/v1/latest") for _verb, path in server.calls), \
                        "V7 must not depend on legacy single-device /latest"
                    png = OUT / f"integrated_{width}_{theme}.png"
                    page.screenshot(path=str(png), full_page=True, animations="disabled")
                    if width < 600:
                        page.locator(".rv7-mobile [data-node='pv2']").click()
                        assert "Deye №2" in page.locator(".rv7-panel").text_content()
                        page.keyboard.press("Escape")
                        assert page.locator(".rv7-panel").count() == 0
                        page.get_by_role("tab", name="Інженерний режим").click()
                        assert page.locator(".rv7-engineer tbody tr").count() == 5
                        page.get_by_role("tab", name="Огляд").click()
                        page.locator(".rv7-mobile [data-node='bus']").click()
                        page.locator(".rv7-panel a[href='#/parallel']").click()
                        page.wait_for_function("location.hash === '#/parallel'")
                        page.wait_for_function(
                            "!document.body.classList.contains('rakurs-v7-overview')")
                        page.wait_for_function("document.querySelector('#content')?.textContent.includes('Паралельна система Deye')")
                        page.go_back()
                        wait_rendered(page)
                    report.append({"width": width, "theme": theme, "result": "PASS"})
                    context.close()
                    print("V7_INTEGRATED_TEMPLATE_%s=PASS" % width)

                for scenario in ["twin-down", "both-down"]:
                    server.scenario = scenario
                    context = browser.new_context(viewport={"width": 390, "height": 844})
                    page = context.new_page()
                    page.goto(base + "/?ux=v7#/overview", wait_until="load")
                    page.wait_for_function(
                        "() => !!document.querySelector('.banner.warning')", timeout=20000)
                    assert has_real_ssr(page), scenario + ": trusted SSR was erased"
                    assert not page.locator(".v7-host").count()
                    assert "V7 тимчасово недоступний" in page.locator("#content").text_content()
                    page.screenshot(path=str(OUT / ("integrated_" + scenario + ".png")),
                                    full_page=True)
                    context.close()
                    print("V7_INTEGRATED_SSR_" + scenario.upper() + "=PASS")

                for scenario in ["d2-down", "empty", "legacy-down"]:
                    server.scenario = scenario
                    context = browser.new_context(viewport={"width": 390, "height": 844})
                    page = context.new_page()
                    page.goto(base + "/?ux=v7#/overview", wait_until="load")
                    page.wait_for_selector(".rv7-mobile [data-node='pv2']", timeout=20000)
                    if scenario == "d2-down":
                        assert "—" in page.locator(
                            ".rv7-mobile [data-node='pv2'] .rv7-node-value").text_content()
                        assert "5,1 кВт" in page.locator(
                            ".rv7-mobile [data-node='pv1'] .rv7-node-value").text_content()
                        assert page.locator(".rv7-warnings").count() == 1
                        page.get_by_role("tab", name="Інженерний режим").click()
                        assert "—" in page.locator(".rv7-engineer").text_content()
                    elif scenario == "empty":
                        assert "Немає даних" in page.locator(".rv7-top").text_content()
                        assert "—" in page.locator(
                            ".rv7-mobile [data-node='pv1'] .rv7-node-value").text_content()
                    else:
                        assert "5,1 кВт" in page.locator(
                            ".rv7-mobile [data-node='pv1'] .rv7-node-value").text_content()
                    context.close()
                    print("V7_INTEGRATED_" + scenario.upper() + "=PASS")
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()

    (OUT / "integrated-report.json").write_text(
        json.dumps({"case_count": len(report), "result": "PASS", "cases": report,
                    "scope": "full real template with mocked local read-only API; no MySQL or Modbus"},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print("RAKURS_V7_REAL_TEMPLATE_CHROMIUM=PASS")


if __name__ == "__main__":
    run()
