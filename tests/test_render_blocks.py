"""Browser regression tests for the journalism-defensibility blocks.

The ETL/data layer is unit-tested (test_findings.py guards that every story ships
a grounded finding and that spend stories carry the awards caveat), but nothing
proved the page actually surfaces those facts. These tests load public/ in headless
Chromium and assert the render layer puts them on screen and keeps them in sync
when the reader switches stories:

  - #story-finding   the per-story Spearman answer ("What the data shows. ...")
  - #story-caveat    the awards-not-disbursement / coverage caveats
  - #data-freshness  the "Built ..." footer with the poverty-anchor vintage
  - .disclaimer      the public-data disclaimer

They self-skip when Playwright or its Chromium build is absent (the CI deploy
runner installs only ".[dev]", which omits both), so `pytest -q` stays green
everywhere and runs the real checks wherever a browser is present.
"""

from __future__ import annotations

import functools
import http.server
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
    def log_message(self, *_args):  # keep per-request lines out of the pytest report
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
        except PlaywrightError as e:  # browser binary not downloaded on this host
            pytest.skip(f"Chromium unavailable: {e}")
        try:
            yield b
        finally:
            b.close()


@pytest.fixture
def page(browser):
    errors: list[str] = []
    pg = browser.new_page()
    # These tests target the interactive explorer directly. Reduced motion disables
    # the first-visit guided arc (a real a11y path), so the chart is immediately
    # interactive and control/tab clicks are not swallowed by the arc's
    # take-control gesture guard. The arc itself is covered by test_guided_arc_*.
    pg.emulate_media(reduced_motion="reduce")
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    pg.console_errors = errors
    try:
        yield pg
    finally:
        pg.close()


def _load(page, base_url):
    page.goto(base_url, wait_until="networkidle")
    # The finding only un-hides once data has loaded and the first render ran,
    # so this doubles as the "page is ready" signal.
    page.wait_for_selector("#story-finding:not([hidden])", timeout=15000)


def test_cold_load_surfaces_finding_and_caveat(page, base_url):
    _load(page, base_url)
    finding = page.inner_text("#story-finding")
    assert finding.startswith("What the data shows.")
    assert re.search(r"\b(19|20)\d{2}\b", finding), "finding must name its reference year"
    # Default story is spend-vs-poverty (DPWH = an award-based indicator).
    caveat = page.inner_text("#story-caveat")
    assert "Awards, not disbursement" in caveat
    assert page.get_attribute("#story-caveat", "hidden") is None


def test_provenance_blocks_render(page, base_url):
    _load(page, base_url)
    fresh = page.inner_text("#data-freshness")
    assert fresh.startswith("Built ")
    assert "Poverty anchors:" in fresh
    assert "Invalid Date" not in fresh
    assert "public records" in page.inner_text(".disclaimer")


def test_no_console_or_page_errors_on_load(page, base_url):
    _load(page, base_url)
    assert page.console_errors == [], f"unexpected errors: {page.console_errors}"


def test_finding_and_caveat_react_to_story_switch(page, base_url):
    _load(page, base_url)
    first = page.inner_text("#story-finding")
    assert "Awards, not disbursement" in page.inner_text("#story-caveat")

    tabs = page.query_selector_all("#story-switcher button.story-btn")
    assert len(tabs) >= 4, "expected the four preset story tabs"
    # GDP-vs-poverty (last tab) plots no award-based indicator, so the awards
    # caveat must drop while the finding stays present and updates.
    tabs[-1].click()
    page.wait_for_function(
        "prev => document.querySelector('#story-finding').textContent !== prev", arg=first
    )
    switched = page.inner_text("#story-finding")
    assert switched.startswith("What the data shows.")
    assert switched != first
    assert "Awards, not disbursement" not in page.inner_text("#story-caveat")


def _switch_chart(page, kind):
    page.click(f'.chart-type-btn[data-type="{kind}"]')


def test_size_legend_renders_in_bubble_mode_only(page, base_url):
    # Bubbles is the default. The area channel (population) needs an on-page key.
    _load(page, base_url)
    assert page.get_attribute("#size-legend", "hidden") is None
    circles = page.query_selector_all("#size-legend-row .size-legend-circle")
    assert len(circles) == 3, "expected three reference circles (100k, 1M, 10M)"
    widths = [c.bounding_box()["width"] for c in circles]
    # Each circle is drawn at the exact sizeFor() px, so they must grow strictly.
    assert widths[0] < widths[1] < widths[2], f"circles must grow: {widths}"
    row_text = page.inner_text("#size-legend")
    for anchor in ("100k", "1M", "10M"):
        assert anchor in row_text, f"missing scale anchor {anchor}"
    assert "Population" in row_text

    # Map colours by value, not population, so the size key would be a lie there.
    _switch_chart(page, "map")
    page.wait_for_selector("#size-legend", state="hidden", timeout=10000)
    assert page.get_attribute("#size-legend", "hidden") is not None


def test_howto_scaffold_shows_year_span_and_dismisses(page, base_url):
    _load(page, base_url)
    assert page.get_attribute("#chart-howto", "hidden") is None
    text = page.inner_text("#chart-howto-text")
    assert text.startswith("Each bubble is a province or Metro Manila")
    assert "Size = population" in text
    # Year span is derived from the live panel, not hardcoded: first to last year.
    assert re.search(r"\b(19|20)\d{2}\b.*?\b(19|20)\d{2}\b", text), f"no year span: {text!r}"

    # Dismiss hides it and the dismissal sticks across a re-render (mode switch).
    page.click("#chart-howto-dismiss")
    page.wait_for_selector("#chart-howto", state="hidden", timeout=10000)
    _switch_chart(page, "map")
    _switch_chart(page, "bubbles")
    page.wait_for_timeout(300)
    assert page.get_attribute("#chart-howto", "hidden") is not None, (
        "dismissed scaffold must stay hidden after returning to bubble mode"
    )


def test_howto_scaffold_hidden_outside_bubble_mode(page, base_url):
    _load(page, base_url)
    _switch_chart(page, "line")
    page.wait_for_selector("#chart-howto", state="hidden", timeout=10000)
    assert page.get_attribute("#chart-howto", "hidden") is not None
    assert page.get_attribute("#size-legend", "hidden") is not None


# ---- Guided narrative arc (first-visit Rosling-style story) -------------------


def test_guided_arc_runs_then_yields_to_explorer(browser, base_url):
    """First visit (motion on, no hash) plays the guided arc; the first user
    gesture hands control back to the explorer, which then behaves normally."""
    errors: list[str] = []
    pg = browser.new_page()  # motion on, fresh context => fresh localStorage
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    try:
        pg.goto(base_url, wait_until="networkidle")
        pg.wait_for_selector("#story-finding:not([hidden])", timeout=15000)
        # The arc announces itself: the Skip control is visible while it runs.
        pg.wait_for_selector("#arc-skip:not([hidden])", timeout=5000)
        # The opening beats sit on spend-vs-poverty (an award-based indicator).
        assert "Awards, not disbursement" in pg.inner_text("#story-caveat")
        # Take control: Skip drops into the explorer and offers a replay.
        pg.click("#arc-skip")
        pg.wait_for_selector("#replay-arc:not([hidden])", timeout=5000)
        assert pg.get_attribute("#arc-skip", "hidden") is not None
        # Explorer is now live: a story-tab click switches stories (not swallowed).
        first = pg.inner_text("#story-finding")
        tabs = pg.query_selector_all("#story-switcher button.story-btn")
        assert len(tabs) >= 4
        tabs[-1].click()
        pg.wait_for_function(
            "prev => document.querySelector('#story-finding').textContent !== prev",
            arg=first,
        )
        assert errors == [], f"unexpected errors during arc: {errors}"
    finally:
        pg.close()


def test_guided_arc_skipped_for_deep_links(browser, base_url):
    """A shared/deep-linked view (URL hash present) never triggers the arc."""
    pg = browser.new_page()  # motion on
    try:
        pg.goto(base_url + "#story=gdp-vs-poverty&year=2023", wait_until="networkidle")
        pg.wait_for_selector("#story-finding:not([hidden])", timeout=15000)
        pg.wait_for_timeout(400)
        assert pg.get_attribute("#arc-skip", "hidden") is not None, (
            "the arc must not run when the URL carries a shared-view hash"
        )
    finally:
        pg.close()
