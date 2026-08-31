"""Browser checks for Task 7 first-use and keyboard behavior."""

from __future__ import annotations

import functools
import http.server
import socketserver
import threading
from pathlib import Path

import pytest
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
            instance = pw.chromium.launch()
        except PlaywrightError as error:
            pytest.fail(f"Chromium unavailable: {error}", pytrace=False)
        try:
            yield instance
        finally:
            instance.close()


def _page(browser, *, returning=False, reduced_motion=None):
    context = browser.new_context(reduced_motion=reduced_motion)
    if returning:
        context.add_init_script(
            "localStorage.setItem('datavizph_arc_seen_v1', '1');",
        )
    page = context.new_page()
    page.route(
        "**/_vercel/insights/script.js",
        lambda route: route.fulfill(status=200, content_type="application/javascript", body=""),
    )
    return context, page


def _load(page, base_url):
    page.goto(base_url, wait_until="domcontentloaded")
    page.wait_for_selector("#story-finding:not([hidden])", timeout=15000)


def _active_story(page):
    return page.evaluate(
        "() => document.activeElement && document.activeElement.dataset.storyId",
    )


def test_story_button_keeps_focus_across_story_render_and_year_updates(browser, base_url):
    context, page = _page(browser)
    try:
        _load(page, base_url)
        story = page.locator('#story-switcher button[data-story-id="gdp-vs-poverty"]')
        story.focus()
        story.press("Enter", timeout=3000)
        page.wait_for_timeout(100)
        assert _active_story(page) == "gdp-vs-poverty"

        page.evaluate("() => window.__datavizph_render()")
        assert _active_story(page) == "gdp-vs-poverty"

        page.locator("#year-next").click()
        page.locator('#story-switcher button[data-story-id="gdp-vs-poverty"]').focus()
        page.evaluate("() => window.__datavizph_render()")
        assert _active_story(page) == "gdp-vs-poverty"
    finally:
        context.close()


@pytest.mark.parametrize("key", ["ArrowRight", "ArrowLeft", "Home", "End"])
def test_year_shortcuts_ignore_story_buttons(browser, base_url, key):
    context, page = _page(browser)
    try:
        _load(page, base_url)
        story = page.locator('#story-switcher button[data-story-id="spend-vs-poverty"]')
        story.focus()
        year = page.locator("#year-display").inner_text()
        page.keyboard.press(key)
        page.wait_for_timeout(100)
        assert page.locator("#year-display").inner_text() == year
        assert _active_story(page) == "spend-vs-poverty"
    finally:
        context.close()


def test_first_visit_stays_static_until_the_reader_chooses_play_or_explore(browser, base_url):
    context, page = _page(browser)
    try:
        _load(page, base_url)
        choices = page.locator("#story-start")
        assert choices.is_visible()
        assert choices.get_by_role("button", name="Play the guided story").is_visible()
        assert choices.get_by_role("button", name="Explore the data").is_visible()
        year = page.locator("#year-display").inner_text()
        page.wait_for_timeout(800)
        assert page.locator("#year-display").inner_text() == year
        assert page.evaluate("() => window.__datavizph_playing()") is False

        choices.get_by_role("button", name="Explore the data").click()
        assert page.locator("#replay-arc").is_visible()
        assert page.evaluate("() => window.__datavizph_playing()") is False
    finally:
        context.close()


def test_returning_visit_stays_static_and_offers_replay(browser, base_url):
    context, page = _page(browser, returning=True)
    try:
        _load(page, base_url)
        year = page.locator("#year-display").inner_text()
        page.wait_for_timeout(800)
        assert page.locator("#year-display").inner_text() == year
        assert page.evaluate("() => window.__datavizph_playing()") is False
        assert page.locator("#replay-arc").is_visible()
    finally:
        context.close()


def test_reduced_motion_play_and_replay_stay_static(browser, base_url):
    context, page = _page(browser, reduced_motion="reduce")
    try:
        _load(page, base_url)
        year = page.locator("#year-display").inner_text()
        page.get_by_role("button", name="Play the guided story").click()
        page.wait_for_timeout(5000)
        assert page.locator("#year-display").inner_text() == year
        assert page.evaluate("() => window.__datavizph_playing()") is False

        page.locator("#replay-arc").click()
        page.wait_for_timeout(200)
        assert page.locator("#year-display").inner_text() == year
        assert page.evaluate("() => window.__datavizph_playing()") is False
    finally:
        context.close()


def _shown_year(page):
    """The year the chart currently draws, read from its own timeline."""
    return page.evaluate(
        "() => { const t = window.__datavizph_chartOption().timeline[0];"
        "  return t.data[t.currentIndex]; }"
    )


def test_home_and_end_do_not_scrub_the_year_from_the_document_body(browser, base_url):
    """Home and End belong to the page until the chart holds focus.

    With focus on ``body`` the keys did both jobs at once: the page scrolled and
    the year jumped. Home landed on 2014, where the default real-peso view has
    no DPWH value, because the deflator starts at the 2018 CPI base year.
    """
    context, page = _page(browser, returning=True, reduced_motion="reduce")
    try:
        _load(page, base_url)
        page.evaluate("() => document.activeElement.blur()")
        start = _shown_year(page)
        page.keyboard.press("End")
        assert _shown_year(page) == start
        page.keyboard.press("Home")
        assert _shown_year(page) == start

        page.locator("#chart").focus()
        page.keyboard.press("End")
        assert _shown_year(page) != start
    finally:
        context.close()


def test_play_and_explore_move_focus_into_the_chart(browser, base_url):
    """A keyboard reader who presses the first-use choice must not land on body."""
    for button in ("#play-guided-story", "#explore-data"):
        context, page = _page(browser, reduced_motion="reduce")
        try:
            _load(page, base_url)
            page.locator(button).click()
            assert page.evaluate("() => document.activeElement.id") == "chart", button
        finally:
            context.close()


def test_reduced_motion_play_shows_the_annotated_static_view(browser, base_url):
    """Reduced motion replaces the animation, it does not remove the story.

    The button offered a guided story and then set no annotation, no quadrant,
    and no year change. A reader who asks for less motion still gets the finding.
    """
    context, page = _page(browser, reduced_motion="reduce")
    try:
        _load(page, base_url)
        page.locator("#play-guided-story").click()
        beat = page.evaluate("() => window.__datavizph_arcBeat()")
        assert beat == "reveal-end", f"reduced motion produced beat {beat!r}"
        assert page.evaluate("() => window.__datavizph_playing()") is False
        # The annotation is drawn by ECharts, so read the option, not the DOM.
        titles = page.evaluate(
            "() => (window.__datavizph_chartOption().title || []).map(t => t.text).join(' ')"
        )
        assert "No link" in titles, titles
        option = page.evaluate("() => window.__datavizph_chartOption()")
        assert any((series.get("markArea") or {}).get("data") for series in option["series"]), (
            "the median-split quadrant is missing from the static view"
        )
    finally:
        context.close()
