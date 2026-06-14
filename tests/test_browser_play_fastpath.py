"""Browser test for the autoplay fast path.

Play ticks no longer rerun the full render() (which rebuilds the per-year step
options for every panel year plus the sidebar DOM); they dispatch a
timelineChange against the step options already on the chart and update only
the year-dependent DOM. This test proves the observable behavior survived the
split: across 3+ autoplay ticks the year label advances through the panel in
order, the bubbles series stays in lockstep with the prebuilt step option for
the displayed year, and the finding box keeps tracking the displayed year.

Self-skips when Playwright or its Chromium build is absent, matching
test_render_blocks.py, so CI (which installs only .[dev]) stays green.
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
    errors: list[str] = []
    pg = browser.new_page()
    pg.route(
        "**/_vercel/insights/script.js",
        lambda route: route.fulfill(status=200, content_type="application/javascript", body=""),
    )
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    pg.console_errors = errors
    try:
        yield pg
    finally:
        pg.close()


def _bubble_state(page):
    """(year label, timeline frame year, live bubble count) from the applied option."""
    return page.evaluate(
        """() => {
            const opt = window.__datavizph_chartOption();
            const live = (opt.series || []).find((s) => s.id === 'bubbles');
            const years = (opt.timeline && opt.timeline[0] && opt.timeline[0].data) || [];
            const idx = opt.timeline && opt.timeline[0] ? opt.timeline[0].currentIndex : -1;
            return {
              label: document.getElementById('year-display').textContent.trim(),
              timelineYear: years[idx],
              live: live ? (live.data || []).length : -1,
            };
        }"""
    )


def test_autoplay_ticks_advance_year_and_keep_bubbles(page, base_url):
    # Deep-link (hash present) skips the guided arc, so play is fully manual here.
    page.goto(base_url + "#story=spend-vs-poverty&year=2018", wait_until="networkidle")
    page.wait_for_selector("#story-finding:not([hidden])", timeout=15000)
    page.wait_for_selector("#chart canvas", timeout=15000)

    start = _bubble_state(page)
    assert start["label"] == "2018"
    assert start["live"] > 60, f"expected a full bubble cloud at 2018, got {start['live']}"

    page.evaluate("() => window.__datavizph_startPlay()")

    seen = [start]
    prev_label = start["label"]
    for _ in range(3):  # 3+ ticks through the fast path
        page.wait_for_function(
            "(prev) => document.getElementById('year-display').textContent.trim() !== prev",
            arg=prev_label,
            timeout=8000,
        )
        s = _bubble_state(page)
        seen.append(s)
        prev_label = s["label"]
    page.evaluate("() => window.__datavizph_stopPlay()")

    years = [int(s["label"]) for s in seen]
    assert years == sorted(years), f"year label must advance in order, got {years}"
    assert len(set(years)) >= 4, f"expected 3+ distinct ticks, got {years}"
    for s in seen:
        # The year label and the applied timeline frame must agree, and the
        # bubble cloud must stay full at every tick (trails/compare/CI ride the
        # same prebuilt step options the fast path replays).
        assert s["timelineYear"] == int(s["label"]), s
        assert s["live"] > 60, f"bubble cloud collapsed mid-play: {s}"

    # Equivalence with the slow path: a full render() at the same year must
    # produce the same bubble count the fast-path frame showed.
    fast = seen[-1]
    page.evaluate("() => window.__datavizph_render()")
    full = _bubble_state(page)
    assert full["label"] == fast["label"], (fast, full)
    assert full["live"] == fast["live"], (
        f"fast-path frame diverged from full render at {fast['label']}: {fast} vs {full}"
    )

    # The finding box is year-dependent (the preset correlation is measured at a
    # fixed year, so scrubbed-away years carry a "chart is showing" note) and
    # must keep updating on the fast path.
    finding = page.text_content("#story-finding")
    assert f"Chart is showing {years[-1]}" in finding, finding

    assert page.console_errors == [], page.console_errors


_HIDE = """() => {
    Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'hidden'});
    Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
    document.dispatchEvent(new Event('visibilitychange'));
}"""
_SHOW = """() => {
    Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'visible'});
    Object.defineProperty(document, 'hidden', {configurable: true, get: () => false});
    document.dispatchEvent(new Event('visibilitychange'));
}"""


def test_autoplay_pauses_while_tab_hidden_and_resumes(page, base_url):
    # Regression: before the visibilitychange handler, the autoplay setInterval
    # (and the guided arc's chained setTimeouts) kept firing in a backgrounded
    # tab, so a visitor who tab-switched mid-arc returned past the story. Deep-link
    # to skip the arc, run free-explore autoplay, and assert the timer is cleared
    # while hidden and re-armed when visible again.
    page.goto(base_url + "#story=spend-vs-poverty&year=2018", wait_until="networkidle")
    page.wait_for_selector("#chart canvas", timeout=15000)

    page.evaluate("() => window.__datavizph_startPlay()")
    assert page.evaluate("() => window.__datavizph_playing()") is True

    page.evaluate(_HIDE)
    assert page.evaluate("() => window.__datavizph_playing()") is False, (
        "autoplay interval must be cleared while the tab is hidden"
    )

    page.evaluate(_SHOW)
    assert page.evaluate("() => window.__datavizph_playing()") is True, (
        "autoplay must resume when the tab becomes visible again"
    )

    page.evaluate("() => window.__datavizph_stopPlay()")
    assert page.console_errors == [], page.console_errors
