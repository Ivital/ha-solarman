#!/usr/bin/env python3
"""Real Chromium screenshot + interaction acceptance for Rakurs V7.
Never connects to production or to any inverter. Fixture data is explicitly labeled.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

if __name__ == '__main__':
    from playwright.sync_api import sync_playwright
    from PIL import Image, ImageStat

    HERE = Path(__file__).resolve().parent
    FIXTURE = (HERE / "rakurs_v7_preview.html").as_uri()
    OUT = Path(os.environ.get("RAKURS_V7_SCREENSHOTS", "artifacts/rakurs-v7")).resolve()
    OUT.mkdir(parents=True, exist_ok=True)

    CASES = [
        ("phone360", 360, 780),
        ("phone390", 390, 844),
        ("phone412", 412, 915),
        ("tablet768", 768, 1024),
        ("desktop1366", 1366, 768),
        ("desktop1920", 1920, 1080),
    ]
    report = []


    def verify_looks_nonblank(path: Path) -> None:
        im = Image.open(path).convert("RGB")
        stats = ImageStat.Stat(im)
        # Pixel-based smoke test, not a substitute for human visual review.
        if min(stats.stddev) < 8:
            raise AssertionError(f"Blank or monochrome browser screenshot: {path}")


    def ready(page) -> None:
        page.wait_for_selector(".rv7 [data-node='pv1']", state="attached", timeout=20000)
        page.wait_for_function(
            "() => !!document.querySelector('.rv7 .rv7-chart-line')",
            timeout=20000
        )
        page.evaluate("() => document.fonts.ready")


    def assert_no_horizontal_overflow(page, case: str) -> None:
        metrics = page.evaluate("""() => ({
          inner: window.innerWidth,
          scroll: document.documentElement.scrollWidth,
          content: document.querySelector('.rv7').getBoundingClientRect().width,
          right: document.querySelector('.rv7').getBoundingClientRect().right
        })""")
        if metrics["scroll"] > metrics["inner"] + 2:
            raise AssertionError(f"Horizontal overflow {case}: {metrics}")
        if metrics["right"] > metrics["inner"] + 2:
            raise AssertionError(f"Dashboard outside viewport {case}: {metrics}")


    def assert_graph_connections(page, mobile: bool) -> None:
        selector = ".rv7-mobile" if mobile else ".rv7-desktop"
        board = page.locator(selector)
        assert board.is_visible(), f"{selector} should be visible"
        assert board.locator("[data-node]").count() == 6
        assert board.locator("[data-flow]").count() == 6
        # All source/output connections are drawn within the same SVG viewport.
        result = page.evaluate("""(sel) => {
          const svg=document.querySelector(sel);
          const box=svg.viewBox.baseVal;
          const nodes=[...svg.querySelectorAll('[data-node]')];
          const wires=[...svg.querySelectorAll('[data-flow]')];
          return {boxW:box.width,boxH:box.height,
            nodeIds:nodes.map(x=>x.getAttribute('data-node')),
            paths:wires.map(x=>({key:x.getAttribute('data-flow'),d:x.getAttribute('d')}))}
        }""", selector)
        assert result["boxW"] > 300 and result["boxH"] <= 330
        assert set(result["nodeIds"]) == {"pv1", "pv2", "bus", "load", "battery", "grid"}
        assert all("M" in path["d"] for path in result["paths"])


    with sync_playwright() as p:
        executable = os.environ.get("CHROMIUM_PATH")
        browser = p.chromium.launch(
            headless=True,
            executable_path=executable or None,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            for name, width, height in CASES:
                for theme in ("light", "dark"):
                    context = browser.new_context(
                        viewport={"width": width, "height": height},
                        device_scale_factor=1,
                        locale="uk-UA",
                        color_scheme=theme,
                        reduced_motion="reduce",
                    )
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda exc: errors.append(str(exc)))
                    page.goto(FIXTURE + "?theme=" + theme, wait_until="load")
                    ready(page)
                    assert_no_horizontal_overflow(page, f"{name}/{theme}")
                    assert_graph_connections(page, width <= 600)
                    assert "Архів" in page.locator(".rv7-pill").first.inner_text()
                    assert "Онлайн" not in page.locator(".rv7-top").inner_text()
                    assert page.locator(".rv7 [data-node='pv1']").count() == 2
                    assert page.locator(".rv7 .rv7-engineer").count() == 0
                    image = OUT / f"{name}_{theme}_viewport.png"
                    page.screenshot(path=str(image), full_page=False, animations="disabled")
                    verify_looks_nonblank(image)
                    full = OUT / f"{name}_{theme}_full.png"
                    page.screenshot(path=str(full), full_page=True, animations="disabled")
                    verify_looks_nonblank(full)
                    if errors:
                        raise AssertionError(f"Page errors {name}/{theme}: {errors}")
                    report.append({"case": name, "theme": theme, "viewport": [width, height],
                                   "viewport_image": image.name, "full_image": full.name,
                                   "overflow": False, "page_errors": 0})
                    context.close()
                    print(f"V7_CHROMIUM_{name.upper()}_{theme.upper()}=PASS")

            mobile = browser.new_context(
                viewport={"width": 390, "height": 844},
                locale="uk-UA", reduced_motion="reduce"
            )
            page = mobile.new_page()
            page.goto(FIXTURE, wait_until="load")
            ready(page)

            # Interactivity is exercised by actual browser click and keyboard events.
            pv = page.locator(".rv7-mobile [data-node='pv1']")
            pv.click()
            sheet = page.locator(".rv7-panel")
            assert sheet.is_visible()
            assert "Deye №1" in sheet.inner_text()
            page.keyboard.press("Escape")
            assert page.locator(".rv7-panel").count() == 0
            print("V7_BROWSER_NODE_DETAILS_ESCAPE=PASS")

            # Inspect historical playback at a chosen timestamp: do not carry newest PV value.
            first_label = page.locator(".rv7-mobile [data-node='pv1'] .rv7-node-value").inner_text()
            slider = page.locator(".rv7-time-control input[type='range']")
            slider.fill("0")
            page.wait_for_timeout(100)
            after_label = page.locator(".rv7-mobile [data-node='pv1'] .rv7-node-value").inner_text()
            assert first_label != after_label, (first_label, after_label)
            assert "Архів" in page.locator(".rv7-card-meta").first.inner_text()
            page.screenshot(path=str(OUT / "mobile_archive_cursor.png"), full_page=True)
            print("V7_BROWSER_HISTORY_CURSOR_UPDATES_SVG=PASS")

            # Dedicated engineering workspace, both physical device reports and registers.
            page.get_by_role("tab", name="Інженерний режим").click()
            assert page.locator(".rv7-engineer").is_visible()
            assert page.locator(".rv7-engineer tbody tr").count() == 5
            assert "Не системний підсумок" in page.locator(".rv7-engineer").inner_text() or \
                   "Відмінні звіти" in page.locator(".rv7-engineer").inner_text()
            assert page.locator(".rv7-engineer a[href='#/registers']").count() == 1
            page.screenshot(path=str(OUT / "mobile_engineering.png"), full_page=True)
            page.get_by_role("tab", name="Огляд").click()
            assert page.locator(".rv7-diagram-card").is_visible()
            print("V7_BROWSER_ENGINEER_MODE_AND_RETURN=PASS")

            # Missing data and zero values must differ visually in the rendered browser.
            page.goto(FIXTURE + "?scenario=empty", wait_until="load")
            page.wait_for_selector(".rv7-mobile [data-node='pv1']")
            assert "—" in page.locator(".rv7-mobile [data-node='pv1']").inner_text()
            assert "Немає" in page.locator(".rv7-pill").first.inner_text()
            page.screenshot(path=str(OUT / "mobile_empty_db.png"), full_page=True)
            page.goto(FIXTURE + "?scenario=zero", wait_until="load")
            page.wait_for_selector(".rv7-mobile [data-node='pv1']")
            assert "0 кВт" in page.locator(".rv7-mobile [data-node='pv1']").inner_text()
            print("V7_BROWSER_ZERO_VS_MISSING=PASS")

            # Responsive rules respect OS motion preferences; no animated fake telemetry.
            state = page.evaluate("""() => ({
              animation: getComputedStyle(document.querySelector('.rv7-flow-foot')).animationName,
              ui: document.querySelector('.rv7')?.className || ''
            })""")
            assert "is-playing" not in state["ui"]
            assert_no_horizontal_overflow(page, "mobile_interactions")
            mobile.close()
        finally:
            browser.close()

    (OUT / "report.json").write_text(json.dumps(
        {"tested": len(report), "cases": report, "result": "PASS",
         "notes": "Actual Chromium rendering against clearly labeled offline archive fixture"},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print("RAKURS_V7_REAL_BROWSER_SCREENSHOTS=PASS")
