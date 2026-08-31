"""Browser checks for Task 7 area choice and text table behavior."""

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


@pytest.fixture
def page(browser):
    context = browser.new_context(reduced_motion="reduce")
    page = context.new_page()
    page.route(
        "**/_vercel/insights/script.js",
        lambda route: route.fulfill(status=200, content_type="application/javascript", body=""),
    )
    try:
        yield page
    finally:
        context.close()


def _load(page, base_url, suffix=""):
    page.goto(base_url + suffix, wait_until="networkidle")
    page.wait_for_selector("#chart canvas", timeout=15000)


def _choice_state(page):
    return page.evaluate(
        """() => ({
          hash: location.hash,
          chips: [...document.querySelectorAll('#selected-chips button')]
            .map(button => button.getAttribute('aria-label')),
          label: document.querySelector('#selected-chips').textContent,
        })"""
    )


def test_area_combobox_keeps_focus_and_uses_one_active_option(page, base_url):
    _load(page, base_url)
    search = page.locator("#search")
    assert search.get_attribute("role") == "combobox"
    assert search.get_attribute("aria-controls") == "search-results"
    assert search.get_attribute("aria-expanded") == "false"
    assert search.get_attribute("aria-label") == "Find a province"

    search.fill("benguet")
    search.press("ArrowDown")
    assert search.get_attribute("aria-expanded") == "true"
    active = search.get_attribute("aria-activedescendant")
    assert active
    assert page.locator(f"#{active}").get_attribute("role") == "option"
    assert page.locator(f"#{active}").get_attribute("tabindex") == "-1"
    assert page.evaluate("() => document.activeElement.id") == "search"
    search.press("Enter")
    assert "sel=141100000" in page.evaluate("() => location.hash")
    assert search.get_attribute("aria-expanded") == "false"
    assert page.evaluate("() => document.activeElement.id") == "search"
    search.press("Escape")
    assert page.evaluate("() => document.activeElement.id") == "search"


def test_region_combobox_uses_the_region_label(page, base_url):
    _load(page, base_url, "#story=inflation-vs-poverty&year=2023")
    search = page.locator("#search")
    assert search.get_attribute("aria-label") == "Find a region"
    search.fill("bicol")
    search.press("ArrowDown")
    assert search.get_attribute("aria-activedescendant") == "search-opt-r05"


def test_search_chart_and_table_actions_choose_the_same_area(page, base_url):
    _load(page, base_url)
    page.locator("#search").fill("benguet")
    page.locator("#search").press("ArrowDown")
    page.locator("#search").press("Enter")
    search_state = _choice_state(page)

    page.goto(base_url, wait_until="networkidle")
    page.wait_for_selector("#chart canvas", timeout=15000)
    page.evaluate("() => window.__datavizph_chooseArea('141100000')")
    chart_state = _choice_state(page)

    page.goto(base_url, wait_until="networkidle")
    page.wait_for_selector("#chart canvas", timeout=15000)
    page.locator("#chart-data-table > summary").click()
    page.locator('#chart-data-table [data-area-id="141100000"]').click()
    table_state = _choice_state(page)

    assert chart_state == search_state == table_state


def test_chip_removal_keeps_a_stable_focus_target(page, base_url):
    _load(page, base_url, "#sel=141100000,012800000")
    second = page.locator("#chip-remove-141100000")
    second.focus()
    second.click()
    assert page.evaluate("() => document.activeElement.id") == "chip-remove-012800000"
    page.locator("#chip-remove-012800000").click()
    assert page.evaluate("() => document.activeElement.id") == "search"


def test_chart_description_and_text_table_use_native_semantics(page, base_url):
    _load(page, base_url)
    assert page.locator("#chart").get_attribute("aria-describedby") == (
        "chart-instructions sr-summary"
    )
    table_details = page.locator("#chart-data-table")
    assert table_details.locator(":scope > summary").inner_text() == "View data table"
    table_details.locator(":scope > summary").click()
    assert table_details.locator("table caption").inner_text()
    assert table_details.locator("thead th").count() == 6
    assert table_details.locator("thead th").nth(0).get_attribute("scope") == "col"
    assert table_details.locator("tbody th").nth(0).get_attribute("scope") == "row"
    assert table_details.locator("tbody button[data-area-id]").count() > 60


def test_tagalog_area_actions_use_localized_labels(page, base_url):
    _load(page, base_url)
    page.locator("#lang-toggle").click()
    page.wait_for_function("() => document.documentElement.lang === 'tl'")
    assert page.locator("#search").get_attribute("aria-label") == "Maghanap ng lalawigan"
    page.locator("#chart-data-table > summary").click()
    assert (
        page.locator("#chart-data-table > summary").inner_text()
        == "Tingnan ang talahanayan ng datos"
    )
    page.locator("#search").fill("benguet")
    page.locator("#search").press("ArrowDown")
    page.locator("#search").press("Enter")
    assert (
        page.locator("#chip-remove-141100000").get_attribute("aria-label") == "Alisin ang Benguet"
    )
    assert page.locator("#chart-instructions").inner_text().startswith("Tsart ng mga bula")
    assert (
        page.locator("#chart-data-table caption")
        .inner_text()
        .startswith("Labing-isang taon, limang trilyon sa kalsada.")
    )
    assert page.locator("#chart-data-table thead th").all_inner_texts() == [
        "Lalawigan",
        "Pangkat ng isla",
        "Gastos ng DPWH kada tao",
        "Antas ng kahirapan ng mga pamilya",
        "Populasyon (2020)",
        "Piliin ang lugar",
    ]


def test_tagalog_region_table_localizes_unavailable_population_text(page, base_url):
    _load(page, base_url, "#story=inflation-vs-poverty&year=2023")
    page.locator("#lang-toggle").click()
    page.wait_for_function("() => document.documentElement.lang === 'tl'")
    page.locator("#chart-data-table > summary").click()
    assert (
        page.locator("#chart-data-table caption")
        .inner_text()
        .startswith("Kung saan pinakamabilis tumaas ang presyo")
    )
    assert page.locator("#chart-data-table thead th").nth(0).inner_text() == "Rehiyon"
    assert page.locator("#chart-data-table tbody td").nth(3).inner_text() == (
        "hindi ipinapakita sa antas na ito"
    )
