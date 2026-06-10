"""Browser tests for the F4 feature batch.

Covers, against the real page in headless Chromium:

  - the 5th preset story (cumulative spend vs poverty change): five tabs render,
    the single-year panel disables the play button and the year slider
  - live Spearman finding for custom picker pairs, including parity with the
    ETL's _spearman on the same rows
  - embed mode (#...&embed=1): chrome hidden, chart visible, attribution chip,
    and the guided arc never starts
  - the Panels chart type: two grids, both panel titles, PNG button still usable
  - island-group filter + playback speed round-tripping through the hash

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

from etl.build import _spearman

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
    pg = browser.new_page()
    _stub_insights(pg)
    try:
        yield pg
    finally:
        pg.close()


def _goto(pg, base_url, hash_suffix):
    pg.goto(base_url + hash_suffix, wait_until="networkidle")
    pg.wait_for_selector("#chart canvas", timeout=15000)


# ---- 5th story: single-year panel + five tabs ---------------------------------


def test_new_story_tab_and_single_year_degrade(page, base_url):
    _goto(page, base_url, "#story=spend-vs-poverty-change")
    page.wait_for_selector("#story-finding:not([hidden])", timeout=15000)
    # All six preset tabs are rendered in the topbar.
    tabs = page.locator("#story-switcher .story-btn")
    assert tabs.count() == 6
    # Single-year panel: nothing to animate or scrub.
    assert page.locator("#big-play").is_hidden()
    assert page.locator("#play-speed").is_hidden()
    assert page.locator("#year-range").is_disabled()
    assert page.locator("#year-prev").is_disabled()
    assert page.locator("#year-next").is_disabled()
    # The preset finding (rho + p) renders for the new pair.
    finding = page.text_content("#story-finding")
    assert "rho" in finding
    assert "2023" in finding
    # The bubbles actually render (regression: the cum-spend rows were never
    # fetched by loadData, leaving an empty plot).
    n = page.evaluate(
        "() => { const b = (window.__datavizph_chartOption().series || [])"
        ".find(s => s.id === 'bubbles'); return b ? (b.data || []).length : -1; }"
    )
    assert n > 60, f"expected a full bubble cloud, got {n} points"


# ---- live rho for custom picker pairs ------------------------------------------


def test_custom_pair_live_finding_matches_etl_spearman(page, base_url):
    _goto(
        page,
        base_url,
        "#story=spend-vs-poverty&x=doh_spend_per_capita&y=poverty&year=2018&log=x&deflate=real",
    )
    page.wait_for_selector("#story-finding:not([hidden])", timeout=15000)
    finding = page.text_content("#story-finding")
    assert "rho" in finding
    assert "Correlation, not causation." in finding
    # DOH spend is award-based, so the awards caveat must ride along.
    assert "PhilGEPS" in finding
    m = re.search(r"rho = ([+-]\d\.\d{2}) \(p [=<]", finding)
    assert m, f"no rho in finding: {finding}"

    # Parity: the same rows through the ETL's _spearman give the same rho.
    data_dir = PUBLIC / "data"
    doh = {
        (r["psgc"], r["year"]): r.get("value_real")
        for r in json.loads((data_dir / "doh_spend_per_capita.json").read_text())
    }
    pov = {
        (r["psgc"], r["year"]): r.get("value")
        for r in json.loads((data_dir / "poverty.json").read_text())
    }
    xs, ys = [], []
    for (psgc, year), v in doh.items():
        if year != 2018 or v is None:
            continue
        y = pov.get((psgc, year))
        if y is None:
            continue
        xs.append(v)
        ys.append(y)
    expected = _spearman(xs, ys)
    assert expected is not None
    assert m.group(1) == f"{expected:+.2f}"


# ---- embed mode -----------------------------------------------------------------


def test_embed_mode_hides_chrome_and_never_runs_arc(page, base_url):
    # No arc_seen pre-set: a first visit, but embed must still suppress the arc.
    _goto(page, base_url, "#story=spend-vs-poverty&year=2018&embed=1")
    assert page.locator("#topbar").is_hidden()
    assert page.locator("#controls").is_hidden()
    assert page.locator("footer").is_hidden()
    assert page.locator("#chart canvas").is_visible()
    chip = page.locator("#embed-chip")
    assert chip.is_visible()
    assert chip.text_content() == "dataviz.ph"
    href = chip.get_attribute("href")
    assert "embed=1" not in href
    assert "story=spend-vs-poverty" in href
    # The guided arc never starts in embed mode.
    page.wait_for_timeout(1200)
    assert page.evaluate("() => window.__datavizph_arcBeat()") is None


# ---- Panels chart type ----------------------------------------------------------


def test_panels_view_two_grids_and_png(page, base_url):
    _goto(page, base_url, "#story=spend-vs-poverty&ct=panels")
    page.wait_for_function(
        "() => window.__datavizph_chartOption "
        "&& (window.__datavizph_chartOption().grid || []).length === 2",
        timeout=15000,
    )
    opt = page.evaluate("() => window.__datavizph_chartOption()")
    titles = " | ".join(t.get("text", "") for t in opt.get("title", []))
    assert "Road money: no pattern" in titles
    assert "Wealth: a clear slope" in titles
    subs = " | ".join(t.get("subtext", "") or "" for t in opt.get("title", []))
    assert "rho = " in subs
    # Both panels actually carry bubbles (regression: a built-but-not-returned
    # series array renders two empty grids).
    counts = page.evaluate(
        "() => window.__datavizph_chartOption().series"
        ".filter(s => s.id && s.id.startsWith('panel_'))"
        ".map(s => (s.data || []).length)"
    )
    assert len(counts) == 2
    assert all(n > 60 for n in counts), f"panels look empty: {counts}"
    # PNG export stays usable (getDataURL captures the whole canvas).
    assert page.locator("#png").is_enabled()
    # Hash round-trips the chart type.
    assert "ct=panels" in page.evaluate("() => window.location.hash")
    # Panels tab is visible on a poverty-Y story...
    assert page.locator('.chart-type-btn[data-type="panels"]').is_visible()
    # ...and hidden once the Y axis is not poverty.
    page.click("#story-switcher .story-btn >> nth=2")  # all-spend-vs-gdp
    page.wait_for_selector('.chart-type-btn[data-type="panels"]', state="hidden", timeout=8000)


# ---- island-group filter + playback speed ---------------------------------------


def test_group_filter_and_speed_round_trip(page, base_url):
    _goto(page, base_url, "#story=spend-vs-poverty&year=2018&grp=barmm&spd=2")
    barmm = page.locator('.legend-toggle[data-group="barmm"]')
    assert barmm.get_attribute("aria-pressed") == "true"
    assert (
        page.locator('.legend-toggle[data-group="luzon"]').get_attribute("aria-pressed") == "false"
    )
    assert page.text_content("#play-speed").strip() == "2x"
    # Toggling another group updates the hash.
    page.click('.legend-toggle[data-group="luzon"]')
    page.wait_for_function(
        "() => window.location.hash.includes('grp=') "
        "&& decodeURIComponent(window.location.hash).includes('luzon')",
        timeout=8000,
    )
    # Toggling both off drops the param.
    page.click('.legend-toggle[data-group="luzon"]')
    page.click('.legend-toggle[data-group="barmm"]')
    page.wait_for_function("() => !window.location.hash.includes('grp=')", timeout=8000)
    # Speed still round-trips.
    assert "spd=2" in page.evaluate("() => window.location.hash")
