"""Browser tests for the regional inflation-vs-poverty story (18-unit mode).

Covers, against the real page in headless Chromium:

  - the 6th preset tab renders and plots exactly 18 equal-size bubbles
  - the map chart type is hidden/disabled at the regional grain (hash attempts
    fall back to bubbles) while lines and ranks still work on the 18 units
  - search, selection, and the screen-reader table all run on the region set
  - the indicator pickers only offer same-grain pairs
  - switching back to a provincial preset restores the 82-unit explorer

Self-skips when Playwright or its Chromium build is absent.
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


@pytest.fixture
def page(browser):
    ctx = browser.new_context(reduced_motion="reduce")
    pg = ctx.new_page()
    pg.route(
        "**/_vercel/insights/script.js",
        lambda route: route.fulfill(status=200, content_type="application/javascript", body=""),
    )
    try:
        yield pg
    finally:
        pg.close()
        ctx.close()


def _goto(pg, base_url, hash_suffix):
    pg.goto(base_url + hash_suffix, wait_until="networkidle")
    pg.wait_for_selector("#chart canvas", timeout=15000)


def _bubble_data(page):
    return page.evaluate(
        "() => (window.__datavizph_chartOption().series"
        ".find(s => s.id === 'bubbles') || {data: []}).data || []"
    )


def test_region_story_renders_18_equal_bubbles(page, base_url):
    _goto(page, base_url, "#story=inflation-vs-poverty&year=2023")
    page.wait_for_selector("#story-finding:not([hidden])", timeout=15000)
    tabs = page.locator("#story-switcher .story-btn")
    assert tabs.count() == 6
    data = _bubble_data(page)
    assert len(data) == 18, f"expected 18 region bubbles, got {len(data)}"
    sizes = {d["symbolSize"] for d in data}
    assert len(sizes) == 1, "regions carry no population: bubbles must be equal-size"
    assert page.locator("#size-legend").is_hidden()
    # Map and Panels are off at this grain; bubbles/lines/ranks remain.
    assert page.locator('.chart-type-btn[data-type="map"]').is_hidden()
    assert page.locator('.chart-type-btn[data-type="panels"]').is_hidden()
    # The finding line carries the computed rho + permutation p for n=18.
    finding = page.text_content("#story-finding")
    assert "across 18 areas" in finding
    assert "rho = " in finding
    assert "p =" in finding or "p <" in finding
    # The tagline states the granularity drop.
    tagline = page.text_content("#story-tagline")
    assert "18 regions" in tagline


def test_region_map_hash_falls_back_to_bubbles(page, base_url):
    _goto(page, base_url, "#story=inflation-vs-poverty&ct=map&year=2023")
    page.wait_for_function(
        "() => (window.__datavizph_chartOption().series || [])"
        ".some(s => s.id === 'bubbles' && (s.data || []).length > 0)",
        timeout=15000,
    )
    ct = page.evaluate("() => new URLSearchParams(location.hash.slice(1)).get('ct')")
    assert ct is None, "map attempt must fall back to bubbles (the hash default)"


def test_region_lines_and_ranks_run_on_18_units(page, base_url):
    _goto(page, base_url, "#story=inflation-vs-poverty&ct=bar&year=2023")
    bars = page.evaluate(
        "() => (window.__datavizph_chartOption().series"
        ".find(s => s.id === 'ranks').data || []).length"
    )
    assert bars == 18
    _goto(page, base_url, "#story=inflation-vs-poverty&ct=line&year=2023")
    lines = page.evaluate(
        "() => window.__datavizph_chartOption().series"
        ".filter(s => s.id && s.id.startsWith('line_')).length"
    )
    assert lines == 18


def test_region_search_selection_and_sr_table(page, base_url):
    _goto(page, base_url, "#story=inflation-vs-poverty&year=2023")
    page.fill("#search", "bicol")
    page.wait_for_selector("#search-results li", timeout=8000)
    assert page.text_content("#search-results li").strip().startswith("Bicol Region")
    page.click("#search-results li")
    page.wait_for_function("() => location.hash.includes('sel=r05')", timeout=8000)
    # Chip renders with the region name.
    assert "Bicol Region" in page.text_content("#selected-chips")
    # SR mirror table: 18 rows, headed "Region", population marked not shown.
    rows = page.evaluate("() => document.querySelectorAll('#chart-sr-table tbody tr').length")
    assert rows == 18
    first_header = page.evaluate("() => document.querySelector('#chart-sr-table th').textContent")
    assert first_header == "Region"


def test_region_pickers_offer_only_region_indicators(page, base_url):
    _goto(page, base_url, "#story=inflation-vs-poverty&year=2023")
    page.click("#axis-pick-y")
    page.wait_for_selector("#indicator-panel .ipanel-item", timeout=8000)
    names = page.eval_on_selector_all(
        "#indicator-panel .ipanel-name", "els => els.map(e => e.textContent)"
    )
    assert len(names) == 2, f"region grain offers exactly its two indicators: {names}"
    assert any("Regional inflation" in n for n in names)
    # The sr-only fallback selects filter the same way.
    y_opts = page.eval_on_selector_all("#y-select option", "els => els.map(e => e.value)")
    assert set(y_opts) == {"region_cpi_yoy_pct", "region_poverty"}


def test_switching_back_to_provincial_story_restores_82_units(page, base_url):
    _goto(page, base_url, "#story=inflation-vs-poverty&year=2023")
    page.click("#story-switcher .story-btn >> nth=0")  # DPWH vs poverty
    page.wait_for_function(
        "() => ((window.__datavizph_chartOption().series || [])"
        ".find(s => s.id === 'bubbles') || {data: []}).data.length > 60",
        timeout=15000,
    )
    assert page.locator('.chart-type-btn[data-type="map"]').is_visible()
    y_opts = page.eval_on_selector_all("#y-select option", "els => els.map(e => e.value)")
    assert "poverty" in y_opts and "region_poverty" not in y_opts
