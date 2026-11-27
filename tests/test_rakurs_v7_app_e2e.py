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
import socket
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
MEASURED = "2026-11-25T00:16:18.000786Z"
D1 = "integration_deye_1"
D2 = "integration_deye_2"
PAGE_GROUPS = {"pv": [("pv_power","Сонячна генерація")], "battery": [("battery_soc","Заряд"),("battery_power","Потужність АКБ"),("battery_soh","SOH")], "grid": [("grid_power","Мережа"),("grid_l1_voltage","Напруга L1"),("grid_l2_voltage","Напруга L2"),("grid_l3_voltage","Напруга L3")], "load": [("load_power","Споживання")], "inverter": [("inverter_temperature","Температура")], "bms_module_1":[("bms_1_min_cell_voltage","Мін. комірка"),("bms_1_max_cell_voltage","Макс. комірка"),("bms_1_voltage","Модуль 1")]}


def reading(value, unit="кВт", stamp=MEASURED):
    return {"value_decimal": value, "unit": unit, "observed_at_utc": stamp,
            "register_verified": False, "sign_verified": False, "quality": "generated"}


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
        self.startup_gate = threading.Event()
        self.startup_gate.set()

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
        if urlsplit(self.path).path=="/static/rakurs-sw.js":
            self.send_header("Service-Worker-Allowed","/")
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
        if path == "/" and self.server.scenario == "offline-nav":
            self.close_connection=True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            return
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
            self.server.startup_gate.wait(timeout=15)
            self.reply({"groups": {group: [{"key": key, "label": label} for key,label in items] for group,items in PAGE_GROUPS.items()}, "all_metric_count": 8})
        elif path == "/api/v1/status":
            self.reply({"mode": "real", "registered_devices": 2,
                        "displayed_signals": 10, "has_real_samples": True,
                        "collector_active": False,
                        "physical_topology_verified": False,
                        "register_semantics_verified": False})
        elif path == "/api/v1/signals":
            self.reply({"signals": [{"signal_key": key, "label_uk": label, "unit": "%" if key == "battery_soc" else "кВт"} for items in PAGE_GROUPS.values() for key,label in items]})
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
        elif path == "/api/v1/solar/calendar":
            self.reply({"days":{"2026-11-24":{"1":{"kwh":"8","hours":24},"2":{"kwh":"10","hours":20}}},"kwp":{}})
        elif path == "/api/v1/parallel/frame" and args.get("group")==["mppt"]:
            at=args["at"][0]
            self.reply({"devices":[{"device_key":d,"values":{"pv1_voltage":reading(322,"В",at)}} for d in (D1,D2)]})
        elif path == "/api/v1/parallel/frame":
            at=args.get("at",[""])[0]
            self.reply({"at":at,"devices":[{"device_key":d,"slot_no":i,"values":{k:{**v,"observed_at_utc":at} for k,v in device_values(i).items()}} for i,d in enumerate([D1,D2],1)]})
        elif path == "/api/v1/parallel/comparison":
            self.reply({"start":"2026-11-24T00:16:18Z","end":MEASURED,
                        "peak":"2026-11-24T12:16:18.000115Z",
                        "frames":[{"t":t,"v":v} for t,v in [
                            ("2026-11-24T00:16:18Z",[35,None,2,None,3,None]),
                            ("2026-11-24T12:16:18.000115Z",[34,38,3,4,5,6]),
                            (MEASURED,[37,38,None,None,4,5])]]})
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
                self.reply({"values": {**device_values(1),"battery_soh":reading(98,"%"),"grid_l1_voltage":reading(230,"В"),"grid_l2_voltage":reading(231,"В"),"grid_l3_voltage":reading(229,"В"),"bms_1_min_cell_voltage":reading(3.31,"В"),"bms_1_max_cell_voltage":reading(3.35,"В"),"bms_1_voltage":reading(53.2,"В")}})
        elif path == "/api/v1/load/profile":
            day=datetime.fromisoformat(args["day"][0])
            start=day-timedelta(hours=2)
            stamps=[(start+timedelta(hours=h)).isoformat()+"Z" for h in (4,5,6)]
            self.reply({"start":start.isoformat()+"Z","end":(start+timedelta(days=1)).isoformat()+"Z",
                        "days":[(day-timedelta(days=n)).date().isoformat() for n in range(6,-1,-1)],
                        "hours":[{"day":args["day"][0],"hour":5,"peak":9}],"band":[],
                        "frames":[{"t":t,"v":[v,*ph]} for t,v,ph in zip(stamps,[4,9,2],[[1,2,1],[6,None,3],[1,1,0]])]})
        elif path == "/api/v1/grid/day":
            date=args["day"][0]
            frames=[{"t":date+"T"+hour+":00:00Z","v":[231,v,230,50,6.4,1.3],"breaks":([0,1,2] if i==3 else [])}
                    for i,(hour,v) in enumerate([("01",230),("02",209),("03",230),("08",228)])]
            self.reply({"start":date+"T00:00:00Z","end":(datetime.fromisoformat(date)+timedelta(days=1)).isoformat()+"Z",
                        "frames":frames,"plot":[0,1,2,3],"gaps":[[2,3]],"peak":1})
        elif path == "/api/v1/bms/history":
            if "at" in args:
                self.reply({"modules":{"1":{"cell_1_voltage":3310,"cell_2_voltage":3350},"2":{"min_cell_voltage":3300,"max_cell_voltage":3360}}})
            else:
                self.reply({"peak":{"module":"2","stamp":MEASURED,"spread":60}})
        elif path == "/api/v1/bms/cells":
            self.reply({"values":{"bms_1_cell_1_voltage":reading(3.31,"В"),"bms_1_cell_2_voltage":reading(3.35,"В")}})
        elif path == "/api/v1/alerts/timeline-rules":
            self.reply({"rules":[{"slot":1,"signal":"battery_soc","unit":"%","direction":"above","threshold":"75","hysteresis":"2","delay":60,"gap":8000}]})
        elif path == "/api/v1/alerts":
            self.reply({"state":"running","checked_at_utc":MEASURED,"rules":[{"key":"mysql","label":"Доступність MySQL","phase":"normal"}],"events":[]})
        elif path == "/api/v1/solar-forecast":
            self.reply({"state":"available","arrays":[{"slot":i,"hours":[{"start_utc":f"2026-11-27T{hour:02d}:00:00Z","kw":float(hour-7)*i} for hour in range(8,11)]} for i in (1,2)],"common_hours":3,"estimated_kwh":18,"updated_at_utc":MEASURED})
        elif path == "/api/v1/energy/hourly":
            day=datetime.fromisoformat(args["day"][0])
            keys=["pv1","pv2","grid_import_total","battery_discharge_total","load_energy_total","battery_charge_total","grid_export_total"]
            hours=[]
            for h in range(24):
                t=day+timedelta(hours=h-2)
                a=t.isoformat()+"Z";b=(t+timedelta(hours=1)).isoformat()+"Z"
                channels={key:{"value":i+1,"status":"full","first_at":a,"last_at":b,"opening":100,"closing":101+i} for i,key in enumerate(keys)}
                if h==5:
                    channels["pv1"].update(value=None,status="reset",closing=0)
                matches={key:{"current":2,"previous":1,"seconds":3600,"first_at":a,"last_at":b,"prior_first":a,"prior_last":b} for key in ["pv"]+keys[2:]}
                hours.append({"start":a,"end":b,"label":f"{h:02}:00","offset":"+0200","channels":channels,"comparison":matches if args.get("compare")==["1"] else {}})
            self.reply({"hours":hours,"prior_day":(day-timedelta(days=1)).date().isoformat(),"sources":{key:{"key":"pv_energy_total" if key.startswith("pv") else key,"device":D2 if key=="pv2" else D1} for key in keys}})
        elif path == "/api/v1/energy":
            self.reply({"period":args.get("period",["day"])[0],"start":"2026-11-24T22:00:00Z","anchor":MEASURED,"daily":[{"day":"2026-11-24","value":"12.5"}],"counters":[{"key":"pv_energy_total","unit":"кВт·год","value":"12.5","samples":8,"first_at":"2026-11-24T22:00:00Z","last_at":MEASURED,"decreases":"0"}],"aggregate_verified":False})
        elif path == "/api/v1/history/range" and args.get("device") and "start" in args:
            key=args.get("key",[""])[0]
            device=args["device"][0]
            start=datetime.fromisoformat(args["start"][0].replace("Z","+00:00"))
            end=datetime.fromisoformat(args["end"][0].replace("Z","+00:00"))
            if device not in {D1,D2} or len(args["device"]) != 1:
                self.reply({"error":"bad device"},400)
                return
            points=[]
            for i in range(12):
                stamp=datetime(2026,11,24,tzinfo=timezone.utc)+timedelta(hours=i*2)
                if start<=stamp<=end:
                    value=70+i if key=="battery_soc" else i*(.44 if device==D1 else .37)
                    point=reading(value,"%" if key=="battery_soc" else "кВт",stamp.isoformat().replace("+00:00","Z"))
                    points.append(point)
            # Include a real latest-row fixture with sub-millisecond MySQL precision.
            latest_stamp=datetime.fromisoformat(MEASURED.replace("Z","+00:00"))
            latest_row=device_values(1 if device==D1 else 2).get(key)
            if latest_row and start<=latest_stamp<=end:
                points.append(dict(latest_row))
            # Internal archive also uses this route; keep its fixture deterministic.
            if not points and scenario=="normal" and key=="pv_power" and end>datetime(2026,11,25,12,tzinfo=timezone.utc):
                points=[reading(1,"кВт","2026-11-24T10:00:00Z"),reading(2,"кВт","2026-11-24T11:00:00Z")]
            self.reply({"points":[] if scenario=="empty" else points,"truncated":False,"resolution":"extrema" if scenario=="dense" else "raw"})
        elif path in {"/api/v1/history", "/api/v1/history/range"}:
            self.reply({"signal":{"unit":"кВт"},"points":[reading(1,"кВт","2026-11-24T10:00:00Z"),reading(4,"кВт","2026-11-24T11:00:00Z"),reading(2,"кВт","2026-11-24T12:00:00Z")],"truncated":False})
        elif path in {"/api/v1/modbus-fields","/api/v1/parallel/modbus-fields"}:
            self.reply({"fields":[{"field_key":"pv_power","field_name":"PV power","group_name":"Power","registers_json":"[672]","value_decimal":"5.1","value_text":None,"unit":"кВт","observed_at_utc":MEASURED}],"register_count":1})
        elif path == "/api/v1/parallel/raw":
            self.reply({"total":1,"raw_registers":[{"register_address":672,"function_code":3,"raw_uint16":510}]})
        elif path == "/api/v1/config":
            self.reply({"available":True,"devices":[],"shared_rs485_buses":[],"enabled":False,"interval_seconds":60,"worker_online":False})
        elif path == "/api/v1/devices":
            self.reply({"devices":[{"device_key":D1,"kind":"inverter","model":"Deye 20 kW"}]})
        elif path == "/api/v1/batches":
            self.reply({"batches":[]})
        elif path == "/api/v1/parallel/events":
            self.reply({"events": []})
        elif path == "/api/v1/events":
            self.reply({"events": []})
        else:
            self.reply({"error": "not implemented by test double"}, 404)


def wait_rendered(page):
    page.wait_for_selector(".rv7 [data-node='pv1']", state="attached", timeout=20000)
    page.wait_for_selector(".rv7 .rv7-chart-line", timeout=20000)
    assert any(" L" in (d or "") for d in page.locator(".rv7 .rv7-chart-line").evaluate_all("(els) => els.map(e => e.getAttribute('d'))")), "Regular sparse history must retain visible line segments"


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
                # Verify actual visibility before JavaScript and while the API is delayed.
                for width,height in [(390,844),(1440,900)]:
                    server.scenario = "normal"
                    nojs = browser.new_context(viewport={"width":width,"height":height},java_script_enabled=False)
                    page = nojs.new_page()
                    page.goto(base+"/",wait_until="load")
                    assert has_real_ssr(page)
                    assert not page.locator(".rakurs-server-fallback").is_visible()
                    assert page.locator(".rakurs-startup").is_visible()
                    assert page.locator('.rakurs-startup a[href="/deye"]').is_visible()
                    nojs.close()
                    ctx=browser.new_context(viewport={"width":width,"height":height})
                    page=ctx.new_page()
                    for attempt in range(2):
                        server.startup_gate.clear()
                        try:
                            if attempt==0: page.goto(base+"/",wait_until="domcontentloaded")
                            else: page.reload(wait_until="domcontentloaded")
                            assert page.locator(".rakurs-startup").is_visible()
                            assert not page.locator(".rakurs-server-fallback").is_visible()
                            assert page.locator(".rakurs-server-fallback .card:visible").count()==0
                            page.screenshot(path=str(OUT/f"startup_{width}_{attempt}.png"))
                        finally:
                            server.startup_gate.set()
                        wait_rendered(page)
                        assert page.locator(".rakurs-startup").count()==0
                        assert not has_real_ssr(page)
                    ctx.close()
                print("RAKURS_STARTUP_NO_LEGACY_CARD_FLASH=PASS")
                pwa=browser.new_context(viewport={"width":390,"height":844})
                page=pwa.new_page();page.goto(base+"/",wait_until="load");wait_rendered(page)
                page.wait_for_function("navigator.serviceWorker.controller !== null",timeout=15000)
                assert page.locator('link[rel="manifest"]').get_attribute("href")=="/static/rakurs.webmanifest"
                assert page.evaluate("async()=>await caches.keys()")==[]
                server.scenario="offline-nav"
                try:
                    page.goto(base+"/",wait_until="load")
                    assert "Показники не завантажено" in page.locator("body").inner_text()
                    assert "5,1 кВт" not in page.locator("body").inner_text()
                finally:
                    server.scenario="normal"
                    pwa.close()
                print("RAKURS_PWA_NETWORK_ONLY_OFFLINE_NO_TELEMETRY_CACHE=PASS")
                for width, height, theme in [(360, 780, "light"), (390, 844, "light"),
                                             (412, 915, "dark"), (768, 1024, "light"),
                                             (1366, 768, "dark"), (1920, 1080, "light")]:
                    server.scenario = "normal"
                    server.calls = []
                    context = browser.new_context(
                        viewport={"width": width, "height": height},
                        color_scheme=theme, locale="uk-UA", reduced_motion="reduce")
                    # The real app deliberately persists its own theme preference.
                    # Color-scheme emulation alone does not activate the dark UI.
                    context.add_init_script(
                        script=f"localStorage.setItem('rakurs_theme','{theme}')")
                    page = context.new_page()
                    page_errors = []
                    page.on("pageerror", lambda error: page_errors.append(str(error)))
                    page.goto(base + "/", wait_until="load")
                    wait_rendered(page)
                    assert page.locator("body").get_attribute("data-theme") == theme
                    assert not page_errors, page_errors
                    assert "5,1 кВт" in page.locator(
                        ".rv7 [data-node='pv1'] .rv7-node-value").first.text_content()
                    assert "5,43 кВт" in page.locator(
                        ".rv7 [data-node='pv2'] .rv7-node-value").first.text_content()
                    assert not has_real_ssr(page), "Trusted SSR should be replaced only by verified twin response"
                    assert page.locator(".rv7-chart-stat").count() == 6
                    assert page.get_by_role("button", name="Останній запис").count() == 1
                    assert page.get_by_label("Кінцева дата архіву").input_value()=="2026-11-25"
                    ranges=[parse_qs(urlsplit(path).query) for verb,path in server.calls if urlsplit(path).path=="/api/v1/history/range"]
                    assert len(ranges)>=6 and all(q.get("end")==["2026-11-25T00:16:18.000999Z"] for q in ranges)
                    assert {q["device"][0] for q in ranges}=={D1,D2}
                    assert all(any(q.get("device")==[device] and q.get("key")==[key] for q in ranges) for device in [D1,D2] for key in ["pv_power","load_power","battery_power","battery_soc","grid_power"])
                    assert page.locator(".rv7-chart-date").text_content()==page.locator(".rv7-status-date").text_content()
                    assert "5,1 кВт" in page.locator(".rv7-chart-stat").first.text_content()
                    picker = page.get_by_label("Обраний інвертор")
                    overview_state = page.locator(".rv7-chart-stat, .rv7-chart-date").all_text_contents()
                    picker.select_option(D2)
                    assert overview_state == page.locator(".rv7-chart-stat, .rv7-chart-date").all_text_contents()
                    picker.select_option(D1)
                    assert not any(word in page.locator(".rv7").inner_text().lower() for word in ["тестові дані", "демонстраційний стан"])
                    if width < 600:
                        assert page.locator("body.rakurs-v7-overview").count() == 1
                        assert not page.locator(".topbar .mobile-heading-row").is_visible()
                        first_screen = page.evaluate("""() => ({
                          navTop: document.querySelector(".mobile-tabs").getBoundingClientRect().top,
                          chartTop: document.querySelector(".rv7-chart-card").getBoundingClientRect().top,
                          gridBottom: document.querySelector(
                            ".rv7-mobile [data-node='grid']").getBoundingClientRect().bottom,
                          batteryBottom: document.querySelector(
                            ".rv7-mobile [data-node='battery']").getBoundingClientRect().bottom
                        })""")
                        assert max(first_screen["gridBottom"], first_screen["batteryBottom"]) <                             first_screen["navTop"] - 6,                             "V7 critical energy nodes must be fully above the mobile bottom nav: " + str(first_screen)
                        assert first_screen["chartTop"] < first_screen["navTop"], "Chart header must be visible on first mobile viewport"
                    assert not page.locator(".rv7 .rv7-pill.is-live").count()
                    assert "Архів" in page.locator(".rv7-pill").first.inner_text()
                    assert page.locator(".rv7 .rv7-warnings").count() == 0
                    assert "Покази вузла" not in page.locator(".rv7").inner_text()
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
                        page.locator(".rv7-mobile [data-node='inv1']").click()
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

                # Regression: animated page ancestors used to capture fixed modal coordinates.
                server.scenario = "normal"
                motion_context=browser.new_context(viewport={"width":360,"height":680},locale="uk-UA",reduced_motion="no-preference",is_mobile=True,has_touch=True)
                motion_page=motion_context.new_page()
                motion_page.goto(base+"/",wait_until="load")
                wait_rendered(motion_page)
                motion_page.wait_for_timeout(400)
                grid=motion_page.locator(".rv7-mobile [data-node=grid]")
                grid.scroll_into_view_if_needed()
                scroll_before=motion_page.evaluate("window.scrollY")
                grid.tap()
                sheet=motion_page.locator(".rv7-panel")
                sheet.wait_for(state="visible")
                motion_page.wait_for_timeout(250)
                bounds=sheet.bounding_box()
                assert bounds and bounds["y"]>=-1 and bounds["y"]+bounds["height"]<=681,bounds
                assert motion_page.locator("body > .rv7-dialog-root").count()==1
                assert motion_page.evaluate("window.scrollY")==scroll_before
                close=sheet.get_by_role("button",name="Закрити деталі")
                assert close.is_visible()
                assert close.bounding_box()["y"]>=0
                close.tap()
                assert motion_page.locator(".rv7-dialog-root").count()==0
                assert motion_page.evaluate("window.scrollY")==scroll_before
                motion_context.close()
                print("RAKURS_MOBILE_INSPECTOR_VIEWPORT_NO_PAGE_SCROLL=PASS")

                # Every existing internal route, both representative viewports and themes.
                route_targets = {
                    "pv": ".product-metric-catalog .metric",
                    "battery": ".product-metric-catalog .metric",
                    "grid": ".product-metric-catalog .metric",
                    "load": ".product-metric-catalog .metric",
                    "inverter": ".product-metric-catalog .metric",
                    "parallel": ".pair-comparison .product-timeseries",
                    "trends": ".trend-card .product-timeseries",
                    "history": ".history-workspace .product-timeseries",
                    "archive": ".history-workspace .product-timeseries",
                    "events": ".events-list",
                    "modbus": ".modbus-table tbody tr",
                    "registers": ".register-row",
                    "system": ".kv",
                    "settings": ".settings-panel"
                }
                for width,height,theme in [(390,844,"light"),(1440,900,"dark")]:
                    server.scenario = "normal"
                    context = browser.new_context(viewport={"width":width,"height":height},locale="uk-UA",timezone_id="America/New_York",reduced_motion="reduce")
                    context.add_init_script(script=f"localStorage.setItem('rakurs_theme','{theme}')")
                    page = context.new_page()
                    errors = []
                    page.on("pageerror",lambda error:errors.append(str(error)))
                    for route,selector in route_targets.items():
                        page.goto(base + "/#/" + route, wait_until="load")
                        page.reload(wait_until="load")
                        page.wait_for_function("(route) => document.body.dataset.route === route", arg=route)
                        page.wait_for_selector("#content " + selector,timeout=20000)
                        assert page.locator("body").get_attribute("data-route") == route
                        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 2"), f"Overflow: {route}/{width}"
                        assert not errors,(route,errors)
                        if width < 761:
                            assert page.locator(".topbar").bounding_box()["height"] <= 110, f"Oversized header: {route}"
                        nav_icon = page.locator(".mobile-tabs a[data-route=overview] .mobile-tab-symbol" if width < 761 else '.nav a[href="#/overview"] .symbol').first
                        assert nav_icon.evaluate("(el)=>getComputedStyle(el, '::before').maskImage") != "none"
                        if route=="trends":
                            hourly=page.locator(".hourly-balance")
                            hourly.get_by_label("Порівняти з попередньою добою").check()
                            hourly.get_by_role("button",name="Енергобаланс 05:00–06:00").click()
                            hourly.locator("summary").click()
                            assert "Лічильник зменшився" in hourly.locator("details").inner_text()
                            assert hourly.locator(".balance-compare tr").count()==7
                            hourly.get_by_role("button",name="Енергобаланс 13:00–14:00").click()
                            assert "3 кВт·год" in hourly.locator(".balance-values").inner_text()
                            assert hourly.locator(".balance-frame").bounding_box()["width"] <= hourly.bounding_box()["width"]
                            hourly.screenshot(path=str(OUT/f"hourly-balance-{width}.png"))

                            page.wait_for_selector(".product-comparison-card .product-timeseries")
                            assert page.locator(".product-overlay-line").count()==2
                            page.wait_for_selector(".product-energy-row")
                            assert page.locator(".product-energy-row").count()==12
                            assert page.locator(".period-column").count()==2
                            assert "Зменшення лічильника" not in page.locator(".product-energy-row").first.inner_text()
                            page.get_by_label("Показник порівняння").select_option("load_power")
                            page.wait_for_function("document.querySelectorAll('.product-overlay-line').length===1")
                            series=page.locator(".trend-card svg").first
                            series.focus()
                            page.keyboard.press("Home")
                            assert "Навантаження" in page.locator(".period-tooltip").inner_text()
                            chosen=series.get_attribute("data-stamp")
                            page.locator(".trend-card").first.get_by_text("Відкрити в архіві").click()
                            page.wait_for_selector(".history-workspace .period-reading")
                            assert page.locator(".history-workspace select").input_value()=="pv_power"
                            assert len(page.locator(".history-workspace optgroup").all())>0
                            assert datetime.fromisoformat(chosen.replace("Z","+00:00")).minute==int(page.locator("input[type=datetime-local]").last.input_value()[-2:])
                            page.go_back()
                            page.wait_for_selector(".period-column")
                            page.get_by_label("Період енергетичного звіту").select_option("month")
                            page.wait_for_selector(".product-energy-row")
                            assert any("/energy?" in path and "period=month" in path for _,path in server.calls)
                            chart=page.locator(".trend-card .product-chart-interactive").first
                            chart.get_by_role("button",name="Збільшити масштаб").click()
                            chart.get_by_role("button",name="Скинути масштаб").click()
                        if route in {"grid","load","inverter"}:
                            assert page.locator(".product-phase-grid .product-phase-card").count()==3
                        if route=="battery":
                            await_logo=page.locator(".brand img")
                            assert await_logo.evaluate("(img)=>img.complete && img.naturalWidth===200")
                            page.get_by_role("button",name="Найбільший розкид за добу").click()
                            page.wait_for_function("document.querySelector('.battery-module strong')?.textContent==='Модуль 2'")
                            assert "60 мВ" in page.locator(".battery-module").first.inner_text()
                            assert page.locator(".battery-plots svg").count()==2
                            assert page.locator(".battery-cell").count()==2
                            page.locator(".battery-history").screenshot(path=str(OUT/f"battery-history-{width}.png"))
                            assert page.locator(".bms-summary").count()==1
                            assert page.locator(".product-bms-gauge").count()==2
                            page.locator(".product-bms-records > summary").click()
                            module=page.locator(".product-bms-module").first
                            module.locator("summary").click()
                            assert "40 мВ" in module.locator(".product-bms-delta").text_content()
                            assert module.locator(".product-technical-bars").count()==1
                            page.locator(".product-battery-estimate summary").click()
                            inputs=page.locator(".product-battery-estimate input")
                            inputs.nth(0).fill("20");inputs.nth(1).fill("20")
                            assert "10,4 кВт·год" in page.locator(".product-battery-estimate").inner_text()
                        if route=="load":
                            panel=page.locator(".load-profile")
                            panel.get_by_role("button",name="Пік",exact=False).click()
                            assert panel.locator(".load-phase b").all_text_contents()==["6 кВт","—","3 кВт"]
                            assert panel.locator(".load-hour").count()==168
                            panel.locator(".load-hour:enabled").click()
                            panel.get_by_role("button",name="Уся доба",exact=False).click()
                            panel.locator(".load-phase").first.wait_for()
                            panel.screenshot(path=str(OUT/f"load-profile-{width}.png"))
                        if route=="grid":
                            grid=page.locator(".grid-day")
                            grid.get_by_role("button",name="Найбільша різниця між фазами").click()
                            assert "209 В" in grid.locator(".grid-voltage").nth(1).inner_text()
                            assert "22 В" in " ".join(grid.locator(".grid-selected").all_text_contents())
                            assert grid.locator(".grid-inspect tr").count()==(9 if width<=760 else 4)
                            grid.locator(".grid-gap").click()
                            assert "Перший після" in grid.locator(".grid-day-coverage").inner_text()
                            grid.get_by_role("button",name="Найбільша різниця між фазами").click()
                            grid.screenshot(path=str(OUT/f"grid-day-{width}.png"))
                            assert "230 В" in page.locator(".product-phase-grid").inner_text()
                            assert page.locator(".product-technical-bars").count()>=1
                        if route=="pv":
                            panel=page.locator(".solar-analysis")
                            if width<761:
                                panel.locator("summary").click()
                            chosen=panel.get_by_role("button",name="2026-11-24 · 18 кВт·год · неповне погодинне покриття")
                            chosen.click()
                            panel.locator(".solar-tracker-row").first.wait_for()
                            assert panel.locator(".solar-tracker-row").count()==4
                            assert "322 В" in panel.locator(".solar-trackers").inner_text()
                            assert panel.locator(".product-overlay-line").count()==2
                            panel.locator("svg").focus()
                            page.keyboard.press("Home")
                            page.wait_for_function("document.querySelector('.solar-frame-time')?.textContent.includes('02:00')")
                            panel.screenshot(path=str(OUT/f"solar-analysis-{width}.png"))
                            assert page.locator(".product-mppt").count()==1
                            assert page.locator(".product-mppt .product-phase-card").count()==2
                            page.locator(".product-forecast summary").click()
                            page.wait_for_selector(".product-forecast .product-overlay-line")
                            assert page.locator(".product-forecast .product-overlay-line").count()==2
                        if route=="events":
                            tl=page.locator(".state-timeline")
                            tl.locator(".state-active").first.click()
                            assert "81 %" in tl.locator(".state-detail > strong").inner_text()
                            assert "Межа відновлення 73 %" in tl.locator(".state-detail").inner_text()
                            assert tl.locator(".state-row").count()==3
                            assert tl.locator(".state-unknown").count()>0
                            tl.screenshot(path=str(OUT/f"events-states-{width}.png"))
                            page.wait_for_selector(".product-alert-center .product-energy-row")
                            assert "Монітор працює" in page.locator(".product-alert-center").inner_text()
                            page.get_by_label("Важливість подій").select_option("critical")
                            page.wait_for_function("document.querySelector(\".event-count-card strong\").textContent !== \"…\"")
                        if route=="system":
                            assert page.locator(".product-health").count()==1
                        if route=="archive":
                            assert page.locator("input[type=datetime-local]").last.input_value()=="2026-11-25T02:16"
                        if route in {"history","archive","trends","parallel"}:
                            chart=page.locator("#content .product-timeseries").first
                            label=chart.locator(".product-chart-label")
                            before=label.text_content()
                            chart.focus()
                            page.keyboard.press("Home")
                            assert label.text_content()!=before,route
                        if route in {"pv","battery","grid","load","inverter"}:
                            search=page.locator(".catalog-controls input")
                            search.fill("неможливий_ключ")
                            assert page.locator(".product-metric-catalog .metric:visible").count() == 0
                            search.fill("")
                            assert page.locator(".product-metric-catalog .metric:visible").count() > 0
                        if route=="parallel":
                            assert page.locator(".parallel-device").count()==2
                            assert page.locator(".pair-shared").count()==4
                            panel=page.locator(".pair-comparison")
                            panel.get_by_role("button",name="Найбільша різниця температур").click()
                            assert "4 °C" in panel.locator(".pair-detail > strong").inner_text()
                            assert len(set(panel.locator(".product-chart-label").all_text_contents()))==1
                            panel.get_by_label("Показник потужності").select_option("4")
                            assert "5 кВт" in panel.locator(".pair-detail-row").first.inner_text()
                            panel.get_by_role("button",name="Година навколо точки").click()
                            panel.screenshot(path=str(OUT/f"pair-{width}.png"))
                            assert not page.locator("details.parallel-raw-viewer").first.get_attribute("open")
                            page.locator("details.parallel-raw-viewer summary").first.click()
                            assert page.locator("details.parallel-raw-viewer").first.get_attribute("open") is not None
                        page.screenshot(path=str(OUT / f"route_{route}_{width}_{theme}.png"),full_page=True,animations="disabled")
                        report.append({"route":route,"width":width,"theme":theme,"result":"PASS"})
                        print(f"PRODUCT_ROUTE_{route}_{width}=PASS")
                    context.close()
                for scenario in ["twin-down", "both-down"]:
                    server.scenario = scenario
                    context = browser.new_context(viewport={"width": 390, "height": 844})
                    page = context.new_page()
                    page.goto(base + "/", wait_until="load")
                    page.wait_for_function(
                        "() => !!document.querySelector('.banner.warning')", timeout=20000)
                    assert has_real_ssr(page), scenario + ": trusted SSR was erased"
                    assert page.locator(".rakurs-server-fallback").is_visible()
                    assert page.locator(".rakurs-startup").count()==0
                    assert not page.locator(".v7-host").count()
                    assert "Огляд тимчасово недоступний" in page.locator("#content").text_content()
                    page.screenshot(path=str(OUT / ("integrated_" + scenario + ".png")),
                                    full_page=True)
                    context.close()
                    print("V7_INTEGRATED_SSR_" + scenario.upper() + "=PASS")

                server.scenario="dense"
                dense=browser.new_context(viewport={"width":390,"height":844})
                page=dense.new_page();page.goto(base+"/",wait_until="load");wait_rendered(page)
                slider=page.locator(".rv7-time-control input[type=range]");slider.wait_for();slider.fill("0")
                page.wait_for_function("document.querySelector(\".rv7-mobile [data-node=pv1] .rv7-node-value\").textContent.includes(\"5,1 кВт\")")
                assert any("/parallel/frame?at=" in path for _,path in server.calls)
                dense.close();print("RAKURS_ADAPTIVE_CHART_EXACT_FRAME=PASS")
                for scenario in ["d2-down", "empty", "legacy-down"]:
                    server.scenario = scenario
                    context = browser.new_context(viewport={"width": 390, "height": 844})
                    page = context.new_page()
                    page.goto(base + "/", wait_until="load")
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
