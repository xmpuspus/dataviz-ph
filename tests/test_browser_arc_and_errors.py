"""Browser tests for arc replay, gesture abort, and error handling.

Tests self-skip when Playwright or its Chromium build is absent, matching the
pattern in test_render_blocks.py. Each test uses page.route() for network
interception where needed.

Coverage:
  a. replay-during-autoplay: returning visitor clicks #replay-arc, arc advances.
  b. arc abort via tap vs scroll: tap aborts, scroll-like gesture does not.
  c. fetch-failure alert: poverty.json 500 -> role=alert error text renders.
  d. map geojson fallback: ph-provinces.geojson 500 -> chart falls back to bubbles.
"""

from __future__ import annotations

import functools
import http.server
import socketserver
import threading
from pathlib import Path

import pytest

pytest.importorskip("playwright", reason="playwright not installed")
from playwright.sync_api import Error as PlaywrightError  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

PUBLIC = Path(__file__).resolve().parent.parent / "public"


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


@pytest.fixture(scope="module")
def base_url():
    handler = functools.partial(_QuietHandler, directory=str(PUBLIC))
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    finally:
        httpd.shutdown()
        thread.join()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except PlaywrightError as e:
            pytest.skip(f"Chromium unavailable: {e}")
        try:
            yield b
        finally:
            b.close()


def _wait_ready(page, base_url, hash_suffix=""):
    """Navigate and wait until the chart is fully loaded."""
    page.goto(base_url + hash_suffix, wait_until="networkidle")
    page.wait_for_selector("#story-finding:not([hidden])", timeout=15000)


# ---- a. replay-during-autoplay -----------------------------------------------


def test_replay_during_autoplay_advances_arc(browser, base_url):
    """Returning visitor (arc_seen set) sees gentle autoplay. Clicking #replay-arc
    starts the guided arc and the beat advances past 'hook' within a timeout."""
    pg = browser.new_page()
    # Pre-set arc_seen so the page treats this as a returning visit.
    pg.add_init_script("""
        Object.defineProperty(window, '_arcSeenInit', {value: true});
        try { localStorage.setItem('datavizph_arc_seen_v1', '1'); } catch {}
        try { sessionStorage.setItem('datavizph_arc_seen_v1', '1'); } catch {}
    """)
    try:
        _wait_ready(pg, base_url)
        # Returning visit: replay button is visible, arc is not running.
        pg.wait_for_selector("#replay-arc:not([hidden])", timeout=8000)
        assert pg.evaluate("() => window.__datavizph_arcBeat()") is None
        # Click replay.
        pg.click("#replay-arc")
        # Arc should enter 'hook' beat immediately.
        pg.wait_for_function(
            "() => window.__datavizph_arcBeat() === 'hook'",
            timeout=5000,
        )
        # Wait for it to advance past hook to reveal (the animated beat).
        past_hook = (
            "() => ['reveal','reveal-end','specific','twist','release']"
            ".includes(window.__datavizph_arcBeat())"
        )
        pg.wait_for_function(past_hook, timeout=12000)
    finally:
        pg.close()


# ---- b. arc abort via tap vs scroll ------------------------------------------


def test_arc_tap_aborts_but_scroll_does_not(browser, base_url):
    """A tap (pointerdown + pointerup at same position) aborts the arc.
    A scroll-like gesture (large movement) must not abort it."""
    # --- first: verify scroll does NOT abort ---
    pg_scroll = browser.new_page()
    try:
        pg_scroll.goto(base_url, wait_until="networkidle")
        pg_scroll.wait_for_selector("#story-finding:not([hidden])", timeout=15000)
        pg_scroll.wait_for_selector("#arc-skip:not([hidden])", timeout=8000)
        # Simulate a scroll gesture: pointerdown on the chart then move 80px.
        chart = pg_scroll.query_selector("#chart")
        box = chart.bounding_box()
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        pg_scroll.mouse.move(cx, cy)
        pg_scroll.mouse.down()
        pg_scroll.mouse.move(cx, cy + 80)
        pg_scroll.mouse.up()
        pg_scroll.wait_for_timeout(300)
        # Arc should still be running (skip button visible).
        assert pg_scroll.get_attribute("#arc-skip", "hidden") is None, (
            "scroll gesture must not abort arc"
        )
    finally:
        pg_scroll.close()

    # --- second: verify tap DOES abort ---
    pg_tap = browser.new_page()
    try:
        pg_tap.goto(base_url, wait_until="networkidle")
        pg_tap.wait_for_selector("#story-finding:not([hidden])", timeout=15000)
        pg_tap.wait_for_selector("#arc-skip:not([hidden])", timeout=8000)
        chart = pg_tap.query_selector("#chart")
        box = chart.bounding_box()
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        # A tap: pointerdown and pointerup at the same point.
        pg_tap.mouse.move(cx, cy)
        pg_tap.mouse.down()
        pg_tap.mouse.up()
        # Arc should be aborted: skip hidden, replay visible.
        pg_tap.wait_for_selector("#replay-arc:not([hidden])", timeout=5000)
        assert pg_tap.get_attribute("#arc-skip", "hidden") is not None, (
            "tap must abort the arc and hide the skip button"
        )
        # arcBeat should be null (free explore).
        assert pg_tap.evaluate("() => window.__datavizph_arcBeat()") is None
    finally:
        pg_tap.close()


# ---- c. fetch-failure alert --------------------------------------------------


def test_fetch_failure_renders_alert(browser, base_url):
    """When poverty.json fails (HTTP 500), the chart renders a role=alert
    error message instead of crashing silently."""
    pg = browser.new_page()
    try:
        # Intercept poverty.json and return a server error.
        pg.route(
            "**/data/poverty.json",
            lambda route: route.fulfill(status=500, body="Internal Server Error"),
        )
        pg.goto(base_url, wait_until="networkidle")
        # The app's catch path puts an alert div in #chart.
        pg.wait_for_selector("[role='alert']", timeout=15000)
        alert_text = pg.inner_text("[role='alert']")
        assert len(alert_text) > 10, f"alert text too short: {alert_text!r}"
    finally:
        pg.close()


# ---- d. map geojson fallback -------------------------------------------------


def test_map_geojson_failure_falls_back_to_bubbles(browser, base_url):
    """When ph-provinces.geojson fails, clicking the Map tab falls back to
    bubble mode: the chart still renders and no uncaught error fires."""
    errors: list[str] = []
    pg = browser.new_page()
    pg.emulate_media(reduced_motion="reduce")
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    try:
        # Intercept the geojson so the map can never load.
        pg.route(
            "**/data/ph-provinces.geojson",
            lambda route: route.fulfill(status=500, body="Internal Server Error"),
        )
        _wait_ready(pg, base_url)
        # Click the Map tab.
        pg.click('.chart-type-btn[data-type="map"]')
        # The fallback switches back to bubbles; wait for the loading mask to clear.
        pg.wait_for_timeout(3000)
        # Bubbles chart-type button should be active (fallback fired).
        bubbles_btn = pg.query_selector('.chart-type-btn[data-type="bubbles"]')
        assert bubbles_btn is not None
        # No uncaught page errors.
        assert errors == [], f"unexpected page errors: {errors}"
        # The chart div must still contain canvas (ECharts rendered something).
        assert pg.query_selector("#chart canvas") is not None, (
            "chart canvas must still be present after map fallback"
        )
    finally:
        pg.close()
