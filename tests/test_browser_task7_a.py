"""Browser checks for Task 7 first-use and keyboard behavior."""

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
            instance = pw.chromium.launch()
        except PlaywrightError as error:
            pytest.skip(f"Chromium unavailable: {error}")
        try:
            yield instance
        finally:
            instance.close()


def _page(browser, *, returning=False):
    context = browser.new_context()
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
