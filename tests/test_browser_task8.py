"""Browser checks for the Task 8 responsive control shell."""

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
AXE = Path(__file__).resolve().parent / "vendor" / "axe-core-4.12.1" / "axe.min.js"
VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1280, "height": 800},
    {"width": 768, "height": 1024},
    {"width": 390, "height": 844},
    {"width": 360, "height": 800},
    {"width": 320, "height": 568},
    {"width": 844, "height": 390},
]


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


def _page(browser, viewport):
    context = browser.new_context(viewport=viewport, reduced_motion="reduce")
    page = context.new_page()
    page.route(
        "**/_vercel/insights/script.js",
        lambda route: route.fulfill(status=200, content_type="application/javascript", body=""),
    )
    page.route(
        "**/__test__/axe-core-4.12.1.min.js",
        lambda route: route.fulfill(
            status=200,
            content_type="application/javascript",
            body=AXE.read_text(),
        ),
    )
    return context, page


def _load(page, base_url, suffix=""):
    page.goto(base_url + suffix, wait_until="networkidle")
    page.wait_for_selector("#chart canvas", timeout=15000)


def _document_overflows(page):
    return page.evaluate("() => document.documentElement.scrollWidth > window.innerWidth")


def _content_overflows(page):
    return page.evaluate(
        """() => {
          const width = window.innerWidth;
          const ids = ['body', 'topbar', 'title-strip', 'story-headline', 'chart-wrap'];
          return ids.map(id => {
            const node = id === 'body' ? document.body : document.getElementById(id);
            const rect = node.getBoundingClientRect();
            return { id, left: rect.left, right: rect.right, width: rect.width };
          }).filter(({ left, right }) => left < 0 || right > width);
        }"""
    )


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_viewports_keep_the_start_choice_and_document_width_stable(browser, base_url, viewport):
    context, page = _page(browser, viewport)
    try:
        _load(page, base_url)
        start = page.locator("#story-start")
        assert start.is_visible()
        assert start.get_by_role("button", name="Play the guided story").is_visible()
        assert start.get_by_role("button", name="Explore the data").is_visible()
        assert not _document_overflows(page)
        if viewport["width"] < 1100:
            assert page.locator("#mobile-controls-toggle").is_visible()
    finally:
        context.close()


@pytest.mark.parametrize("viewport", [{"width": 390, "height": 844}, {"width": 320, "height": 568}])
def test_narrow_layout_keeps_body_and_content_inside_the_viewport(browser, base_url, viewport):
    context, page = _page(browser, viewport)
    try:
        _load(page, base_url)
        assert not _document_overflows(page)
        assert _content_overflows(page) == []
    finally:
        context.close()


def test_mobile_shell_keeps_one_control_tree_and_restores_focus(browser, base_url):
    context, page = _page(browser, {"width": 390, "height": 844})
    try:
        _load(page, base_url, "#sel=141100000")
        toggle = page.locator("#mobile-controls-toggle")
        assert page.locator("#controls").count() == 1
        assert toggle.get_attribute("aria-controls") == "controls"
        assert toggle.get_attribute("aria-expanded") == "false"
        assert page.locator("#mobile-selected-summary").inner_text() == "Benguet"

        toggle.focus()
        toggle.press("Enter")
        assert toggle.get_attribute("aria-expanded") == "true"
        assert toggle.inner_text() == "Hide controls"
        assert page.locator("#search").is_visible()
        page.keyboard.press("Escape")
        assert toggle.get_attribute("aria-expanded") == "false"
        assert toggle.inner_text() == "Show controls"
        assert page.evaluate("() => document.activeElement.id") == "mobile-controls-toggle"
        assert page.locator("#mobile-selected-summary").inner_text() == "Benguet"
        assert not _document_overflows(page)
    finally:
        context.close()


def test_mobile_trust_link_opens_the_existing_trust_disclosure(browser, base_url):
    context, page = _page(browser, {"width": 390, "height": 844})
    try:
        _load(page, base_url)
        assert page.locator("#view-trust").count() == 1
        link = page.locator("#mobile-trust-link")
        link.click()
        assert page.locator("#view-trust").evaluate("node => node.open") is True
        assert page.evaluate("() => document.activeElement.parentElement.id") == "view-trust"
        assert not _document_overflows(page)
    finally:
        context.close()


def test_mobile_visible_targets_are_at_least_44_pixels(browser, base_url):
    context, page = _page(browser, {"width": 390, "height": 844})
    try:
        _load(page, base_url)
        page.locator("#mobile-controls-toggle").click()
        page.locator("#view-trust > summary").click()
        page.locator("#chart-data-table > summary").click()
        page.locator("#axis-pick-y").click()
        targets = page.evaluate(
            """() => [...document.querySelectorAll('button, a[href], input, select, summary')]
              .filter(
                node => !node.disabled && !node.closest('.sr-only')
                  && !!(node.offsetWidth || node.offsetHeight)
              )
              .map(node => {
                const rect = node.getBoundingClientRect();
                return { id: node.id || node.tagName, width: rect.width, height: rect.height };
              })
              .filter(({ width, height }) => width < 44 || height < 44)"""
        )
        assert targets == [], targets
    finally:
        context.close()


@pytest.mark.parametrize("viewport", [{"width": 320, "height": 568}, {"width": 844, "height": 390}])
def test_small_viewport_targets_are_at_least_44_pixels(browser, base_url, viewport):
    context, page = _page(browser, viewport)
    try:
        _load(page, base_url)
        page.locator("#mobile-controls-toggle").click()
        targets = page.evaluate(
            """() => [...document.querySelectorAll('button, a[href], input, select, summary')]
              .filter(node => !node.disabled && !node.closest('.sr-only'))
              .filter(node => !!(node.offsetWidth || node.offsetHeight))
              .map(node => node.getBoundingClientRect())
              .filter(({ width, height }) => width < 44 || height < 44)"""
        )
        assert targets == []
    finally:
        context.close()


@pytest.mark.parametrize(
    "viewport",
    [
        {"width": 390, "height": 844},
        {"width": 320, "height": 568},
        {"width": 844, "height": 390},
    ],
)
def test_mobile_first_fold_contains_both_start_choices(browser, base_url, viewport):
    context, page = _page(browser, viewport)
    try:
        _load(page, base_url)
        for label in ["Play the guided story", "Explore the data"]:
            box = page.get_by_role("button", name=label).bounding_box()
            assert box is not None
            assert box["y"] + box["height"] <= viewport["height"]
    finally:
        context.close()


def test_embed_mode_hides_the_mobile_shell(browser, base_url):
    context, page = _page(browser, {"width": 390, "height": 844})
    try:
        _load(page, base_url, "#embed=1")
        assert page.locator("#mobile-controls-toggle").is_hidden()
        assert page.locator("#mobile-selected-summary").is_hidden()
        assert page.locator("#mobile-trust-link").is_hidden()
    finally:
        context.close()


@pytest.mark.parametrize("viewport", [{"width": 390, "height": 844}, {"width": 844, "height": 390}])
def test_embed_attribution_keeps_the_view_state_on_small_screens(browser, base_url, viewport):
    context, page = _page(browser, viewport)
    suffix = "#story=gdp-vs-poverty&year=2023&sel=141100000&grp=luzon&ct=bubbles&embed=1"
    try:
        _load(page, base_url, suffix)
        href = page.locator("#embed-chip").get_attribute("href")
        assert href is not None
        assert "embed=1" not in href
        for value in ["story=gdp-vs-poverty", "year=2023", "sel=141100000", "grp=luzon"]:
            assert value in href
    finally:
        context.close()


def test_resizing_keeps_one_control_tree_and_connected_focus(browser, base_url):
    context, page = _page(browser, {"width": 1099, "height": 800})
    try:
        _load(page, base_url)
        page.locator("#mobile-controls-toggle").click()
        assert page.evaluate("() => document.activeElement.id") == "search"
        for width in [1100, 1099]:
            page.set_viewport_size({"width": width, "height": 800})
            page.wait_for_timeout(100)
            for element_id in [
                "controls",
                "search",
                "year-range",
                "view-trust",
                "metadata-json",
                "citation-text",
            ]:
                assert page.locator(f"#{element_id}").count() == 1
        assert page.evaluate("() => document.activeElement.id") == "search"
    finally:
        context.close()


def test_mobile_shell_actions_keep_the_canonical_hash_unchanged(browser, base_url):
    context, page = _page(browser, {"width": 390, "height": 844})
    suffix = (
        "#story=gdp-vs-poverty&year=2023&sel=141100000&grp=luzon&ct=bubbles&cmp=2022"
        "&log=x&deflate=real&extrap=on&col=yq&size=eq"
    )
    try:
        _load(page, base_url, suffix)
        before = page.evaluate("() => location.hash")
        page.locator("#mobile-controls-toggle").click()
        page.locator("#mobile-trust-link").click()
        page.set_viewport_size({"width": 844, "height": 390})
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("() => location.hash") == before
    finally:
        context.close()


def test_first_fold_choices_do_not_intersect_playback_controls(browser, base_url):
    context, page = _page(browser, {"width": 390, "height": 844})
    try:
        _load(page, base_url)
        overlap = page.evaluate(
            """() => ['play-guided-story', 'explore-data'].some(id => {
              const choice = document.getElementById(id).getBoundingClientRect();
              return ['big-play', 'play-speed'].some(controlId => {
                const control = document.getElementById(controlId);
                if (control.hidden) return false;
                const rect = control.getBoundingClientRect();
                return choice.left < rect.right && choice.right > rect.left
                  && choice.top < rect.bottom && choice.bottom > rect.top;
              });
            })"""
        )
        assert overlap is False
    finally:
        context.close()


def test_mobile_tagalog_and_other_grain_summary_stay_accurate(browser, base_url):
    context, page = _page(browser, {"width": 390, "height": 844})
    try:
        _load(page, base_url, "#story=inflation-vs-poverty&year=2023&sel=141100000")
        assert page.locator("#mobile-selected-summary").inner_text() == ""
        page.locator("#lang-toggle").click()
        page.wait_for_function("() => document.documentElement.lang === 'tl'")
        assert page.locator("#mobile-controls-toggle").inner_text() == "Ipakita ang mga kontrol"
        assert "Bakit mapagkakatiwalaan" in page.locator("#mobile-trust-link").inner_text()
        page.locator("#mobile-controls-toggle").click()
        assert page.locator("#mobile-controls-toggle").inner_text() == "Itago ang mga kontrol"
    finally:
        context.close()


def test_mobile_open_controls_and_trust_have_no_serious_axe_issues(browser, base_url):
    context, page = _page(browser, {"width": 390, "height": 844})
    try:
        _load(page, base_url)
        page.locator("#mobile-controls-toggle").click()
        page.locator("#mobile-trust-link").click()
        page.add_script_tag(url=f"{base_url}__test__/axe-core-4.12.1.min.js")
        violations = page.evaluate(
            """async () => (await axe.run(document, {
              runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa'] },
            })).violations.filter(({ impact }) => impact === 'serious' || impact === 'critical')"""
        )
        assert violations == []
    finally:
        context.close()
