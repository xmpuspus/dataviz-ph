"""Browser checks for Task 7 axis picker and accessibility paths."""

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
AXE = Path(__file__).resolve().parent / "vendor" / "axe-core-4.12.1" / "axe.min.js"


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


@pytest.fixture
def page(browser):
    context = browser.new_context(reduced_motion="reduce")
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
    try:
        yield page
    finally:
        context.close()


def _load(page, base_url, suffix=""):
    page.goto(base_url + suffix, wait_until="networkidle")
    page.wait_for_selector("#chart canvas", timeout=15000)


def _open_picker(page, axis="y"):
    trigger = page.locator(f"#axis-pick-{axis}")
    trigger.click()
    panel = page.locator("#indicator-panel")
    panel.wait_for()
    return trigger, panel


def test_axis_picker_uses_one_dialog_listbox_and_restores_trigger_focus(page, base_url):
    _load(page, base_url)
    trigger, panel = _open_picker(page)
    assert trigger.get_attribute("aria-haspopup") == "dialog"
    assert trigger.get_attribute("aria-expanded") == "true"
    assert trigger.get_attribute("aria-controls") == "indicator-panel"
    assert panel.get_attribute("role") == "dialog"
    assert panel.get_attribute("aria-labelledby")
    listbox = panel.get_by_role("listbox")
    assert listbox.get_attribute("aria-activedescendant")
    assert listbox.locator('[role="option"][tabindex="0"]').count() == 0
    assert listbox.locator('[role="option"][tabindex="-1"]').count() > 0

    listbox.press("End")
    last = listbox.get_attribute("aria-activedescendant")
    listbox.press("Home")
    assert listbox.get_attribute("aria-activedescendant") != last
    listbox.press("Escape")
    assert page.locator("#indicator-panel").count() == 0
    assert page.evaluate("() => document.activeElement.id") == "axis-pick-y"


def test_axis_picker_chooses_with_keyboard_and_skips_incompatible_options(page, base_url):
    _load(page, base_url)
    trigger, panel = _open_picker(page, "x")
    listbox = panel.get_by_role("listbox")
    current = listbox.get_attribute("aria-activedescendant")
    listbox.press("ArrowDown")
    chosen = listbox.get_attribute("aria-activedescendant")
    assert chosen != current
    assert page.locator(f"#{chosen}").get_attribute("aria-disabled") is None
    listbox.press("Enter")
    assert page.locator("#indicator-panel").count() == 0
    assert page.evaluate("() => document.activeElement.id") == "axis-pick-x"
    assert trigger.get_attribute("aria-expanded") == "false"


def test_axis_picker_marks_incompatible_and_failed_options_disabled(page, base_url):
    page.route("**/data/population.json", lambda route: route.fulfill(status=404, body=""))
    _load(page, base_url)
    _, panel = _open_picker(page, "y")
    assert (
        page.locator("#axis-option-y-dpwh_spend_per_capita").get_attribute("aria-disabled")
        == "true"
    )
    assert page.locator("#axis-option-y-population").get_attribute("aria-disabled") == "true"


def _axe_violations(page, base_url):
    page.add_script_tag(url=f"{base_url}__test__/axe-core-4.12.1.min.js")
    return page.evaluate(
        """async () => (await axe.run(document, {
          runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa'] },
        })).violations.filter(({ impact }) => impact === 'serious' || impact === 'critical')"""
    )


@pytest.mark.parametrize(
    "suffix, action",
    [
        ("", None),
        ("#story=inflation-vs-poverty&year=2023", None),
        ("", "picker"),
        ("", "table"),
        ("", "tagalog"),
    ],
)
def test_axe_has_no_serious_or_critical_violations(page, base_url, suffix, action):
    _load(page, base_url, suffix)
    if action == "picker":
        _open_picker(page)
    elif action == "table":
        page.locator("#chart-data-table > summary").click()
    elif action == "tagalog":
        page.locator("#lang-toggle").click()
        page.wait_for_function("() => document.documentElement.lang === 'tl'")
    assert _axe_violations(page, base_url) == []


def test_axis_definition_popover_carries_an_accessible_name(page, base_url):
    """A role=dialog with no name is announced as an unnamed dialog.

    The axe run in this file filters to wcag2a and wcag2aa, and
    ``aria-dialog-name`` carries the best-practice tag, so it slipped through.
    """
    _load(page, base_url)
    for trigger in ("#axis-info-x", "#axis-info-y"):
        page.locator(trigger).click()
        popover = page.locator("#axis-popover")
        labelled_by = popover.get_attribute("aria-labelledby")
        assert labelled_by, f"{trigger} opens a dialog with no accessible name"
        heading = page.locator(f"#{labelled_by}")
        assert heading.count() == 1
        assert heading.inner_text().strip()
        page.keyboard.press("Escape")


def test_axe_reports_no_dialog_naming_violation_with_a_popover_open(page, base_url):
    _load(page, base_url)
    page.locator("#axis-info-x").click()
    page.add_script_tag(url=f"{base_url}__test__/axe-core-4.12.1.min.js")
    violations = page.evaluate(
        """async () => (await axe.run(document, {
          runOnly: { type: 'rule', values: ['aria-dialog-name'] },
        })).violations"""
    )
    assert violations == []
