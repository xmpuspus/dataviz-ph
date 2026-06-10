"""Browser tests for the explorer-upgrades batch.

Covers, against the real page in headless Chromium:

  - SVG export: the button downloads a real SVG of the current view
  - color-by / size-by encodings: defaults hold, alternates recolor/resize the
    bubbles, the size legend hides on equal-size, and both round-trip the hash
  - inside dataZoom on the bubble view (and absent elsewhere)
  - the EN/Tagalog scaffold: toggle swaps html lang + headline + finding
    template with identical numbers, persists in localStorage, round-trips back

Self-skips when Playwright or its Chromium build is absent, matching
test_render_blocks.py, so CI (which installs only .[dev]) stays green.
"""

from __future__ import annotations

import functools
import http.server
import json
import re
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


def _stub_insights(pg):
    pg.route(
        "**/_vercel/insights/script.js",
        lambda route: route.fulfill(status=200, content_type="application/javascript", body=""),
    )


@pytest.fixture
def page(browser):
    ctx = browser.new_context(reduced_motion="reduce")
    pg = ctx.new_page()
    _stub_insights(pg)
    try:
        yield pg
    finally:
        pg.close()
        ctx.close()


def _goto(pg, base_url, hash_suffix):
    pg.goto(base_url + hash_suffix, wait_until="networkidle")
    pg.wait_for_selector("#chart canvas", timeout=15000)


# ---- SVG export -----------------------------------------------------------------


def test_svg_export_downloads_real_svg(page, base_url):
    _goto(page, base_url, "#story=spend-vs-poverty&year=2018&log=x&deflate=real")
    with page.expect_download(timeout=10000) as dl:
        page.click("#svg")
    download = dl.value
    assert download.suggested_filename == "dataviz-ph-spend-vs-poverty-2018.svg"
    head = Path(download.path()).read_bytes()[:2000].decode("utf-8", "ignore")
    assert "<svg" in head
    # The export is the chart, not an empty shell. zrender draws scatter symbols
    # as <path> fills, so count island-palette fills: one per bubble (82 areas,
    # minus any without data in 2018).
    body = Path(download.path()).read_text(encoding="utf-8", errors="ignore")
    palette_fills = sum(
        body.count(f'fill="{c}"') for c in ("#2b6cb0", "#38a169", "#d97706", "#6b46c1", "#c53030")
    )
    assert palette_fills > 60, f"only {palette_fills} bubble fills in the SVG"


# ---- color-by / size-by encodings -------------------------------------------------


def _bubble_field(page, expr):
    return page.evaluate(
        "() => (window.__datavizph_chartOption().series"
        ".find(s => s.id === 'bubbles').data || [])"
        f".map(d => {expr})"
    )


def test_default_encodings_are_island_color_and_population_size(page, base_url):
    _goto(page, base_url, "#story=spend-vs-poverty&year=2018")
    assert page.evaluate("() => document.getElementById('color-by').value") == "island"
    assert page.evaluate("() => document.getElementById('size-by').value") == "pop"
    sizes = set(_bubble_field(page, "d.symbolSize"))
    assert len(sizes) > 10, "population sizing should vary bubble diameters"
    # Defaults never pollute the hash.
    h = page.evaluate("() => location.hash")
    assert "col=" not in h and "size=" not in h
    assert page.locator("#size-legend").is_visible()


def test_color_by_y_quantile_recolors_and_round_trips(page, base_url):
    _goto(page, base_url, "#story=spend-vs-poverty&year=2018")
    page.select_option("#color-by", "yq")
    page.wait_for_function("() => location.hash.includes('col=yq')", timeout=8000)
    colors = set(_bubble_field(page, "d.itemStyle.color"))
    ramp = {"#dce8f5", "#9ec3e3", "#5a93c7", "#2b6cb0", "#08306b"}
    assert colors <= ramp, f"unexpected colors: {colors - ramp}"
    assert len(colors) >= 4, "quantile binning should use most of the ramp"
    assert not page.evaluate("() => document.getElementById('encode-yq-note').hidden")
    # Hash-driven load lands on the same encoding.
    _goto(page, base_url, "#story=spend-vs-poverty&year=2018&col=yq")
    assert page.evaluate("() => document.getElementById('color-by').value") == "yq"


def test_size_by_equal_fixes_diameter_and_hides_size_legend(page, base_url):
    _goto(page, base_url, "#story=spend-vs-poverty&year=2018&size=eq")
    assert page.evaluate("() => document.getElementById('size-by').value") == "eq"
    sizes = set(_bubble_field(page, "d.symbolSize"))
    assert len(sizes) == 1, f"equal sizing must collapse to one diameter, got {sizes}"
    assert page.locator("#size-legend").is_hidden()
    # Switching back restores population sizing and drops the param.
    page.select_option("#size-by", "pop")
    page.wait_for_function("() => !location.hash.includes('size=')", timeout=8000)
    assert len(set(_bubble_field(page, "d.symbolSize"))) > 10


# ---- inside dataZoom ---------------------------------------------------------------


def test_bubbles_carry_inside_datazoom_and_other_views_do_not(page, base_url):
    _goto(page, base_url, "#story=spend-vs-poverty&year=2018")
    dz = page.evaluate("() => (window.__datavizph_chartOption().dataZoom || []).length")
    assert dz == 2, "bubble view should zoom on both axes"
    types = page.evaluate(
        "() => (window.__datavizph_chartOption().dataZoom || []).map(d => d.type)"
    )
    assert set(types) == {"inside"}
    _goto(page, base_url, "#story=spend-vs-poverty&ct=bar&year=2018")
    dz_bar = page.evaluate("() => (window.__datavizph_chartOption().dataZoom || []).length")
    assert dz_bar == 0, "ranks view must not hijack scroll with a zoom"


# ---- EN/Tagalog scaffold -----------------------------------------------------------


def test_lang_toggle_swaps_lang_headline_and_finding_with_same_numbers(page, base_url):
    _goto(page, base_url, "#story=spend-vs-poverty&year=2018")
    page.wait_for_selector("#story-finding:not([hidden])", timeout=15000)
    assert page.evaluate("() => document.documentElement.lang") == "en"
    finding_en = page.text_content("#story-finding")
    m_en = re.search(r"rho = ([+-]\d\.\d{2}) \((p [=<] [\d.]+)\)", finding_en)
    assert m_en, finding_en

    page.click("#lang-toggle")
    page.wait_for_function("() => document.documentElement.lang === 'tl'", timeout=8000)
    headline = page.text_content("#story-headline")
    tl = json.loads((PUBLIC / "locales" / "tl.json").read_text())
    assert headline == tl["stories"]["spend-vs-poverty"]["headline"]
    # The TL finding interpolates the SAME computed rho and p as the EN one.
    finding_tl = page.text_content("#story-finding")
    m_tl = re.search(r"rho = ([+-]\d\.\d{2}) \((p [=<] [\d.]+)\)", finding_tl)
    assert m_tl, finding_tl
    assert m_tl.group(1) == m_en.group(1)
    assert m_tl.group(2) == m_en.group(2)
    assert tl["finding"]["prefix"] in finding_tl
    # Control labels swap too.
    assert (
        page.text_content('.chart-type-btn[data-type="bubbles"] .ct-label')
        == tl["controls"]["ct_bubbles"]
    )
    # Toggle back restores English losslessly.
    page.click("#lang-toggle")
    page.wait_for_function("() => document.documentElement.lang === 'en'", timeout=8000)
    assert page.text_content("#story-headline").startswith("Eleven years")
    assert page.text_content('.chart-type-btn[data-type="bubbles"] .ct-label') == "Bubbles"


def test_lang_persists_across_reload(page, base_url):
    _goto(page, base_url, "#story=spend-vs-poverty&year=2018")
    page.click("#lang-toggle")
    page.wait_for_function("() => document.documentElement.lang === 'tl'", timeout=8000)
    page.reload(wait_until="networkidle")
    page.wait_for_selector("#chart canvas", timeout=15000)
    page.wait_for_function("() => document.documentElement.lang === 'tl'", timeout=8000)
    assert page.text_content("#lang-toggle").strip() == "EN"
