"""Browser checks for the Task 6 evidence and reuse surface."""

from __future__ import annotations

import functools
import http.server
import json
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
    pg = browser.new_page()
    pg.route(
        "**/_vercel/insights/script.js",
        lambda route: route.fulfill(status=200, content_type="application/javascript", body=""),
    )
    try:
        yield pg
    finally:
        pg.close()


def _goto(page, base_url, suffix=""):
    page.goto(base_url + suffix, wait_until="networkidle")
    page.wait_for_selector("#chart canvas", timeout=15000)


def test_trust_disclosure_shows_current_view_evidence_and_coverage(page, base_url):
    _goto(page, base_url, "#story=spend-vs-poverty&year=2024")
    trust = page.locator("#view-trust")
    assert trust.locator(":scope > summary").inner_text() == "Why trust this view?"
    trust.locator(":scope > summary").click()
    text = trust.inner_text()
    assert "Source" in text
    assert "Transform" in text
    assert "Awards are not disbursements" in text
    assert "2025 is unavailable" in text
    assert "invalid and 11 future award dates" in text
    page.locator("#view-coverage-details summary").click()
    rows = trust.locator("#view-coverage tbody tr")
    assert rows.count() >= 22
    assert any("full" in row for row in rows.all_inner_texts())


def test_trust_disclosure_covers_partial_rows(page, base_url):
    _goto(page, base_url, "#story=spend-vs-poverty&year=2018")
    page.locator("#view-trust > summary").click()
    page.locator("#view-coverage-details summary").click()
    coverage = page.locator("#view-coverage").inner_text()
    assert "partial" in coverage


def test_view_bundle_downloads_deterministic_metadata_and_citation(page, base_url):
    _goto(page, base_url, "#story=spend-vs-poverty&year=2024")
    with page.expect_download() as metadata_download:
        page.click("#metadata-json")
    metadata = json.loads(Path(metadata_download.value.path()).read_text())
    assert metadata["canonical_state"]["year"] == 2024
    assert metadata["csv_field_contract"]
    assert metadata["citation"]
    assert metadata["method_url"].endswith("/methodology")
    assert metadata["view_url"].startswith("http://")
    assert metadata["source_ids"]
    assert metadata["build_id"]
    assert metadata["coverage"]
    with page.expect_download() as citation_download:
        page.click("#citation-text")
    assert "dataviz.ph" in Path(citation_download.value.path()).read_text()


def test_repeated_metadata_downloads_are_identical(page, base_url):
    _goto(page, base_url, "#story=spend-vs-poverty&year=2024")
    with page.expect_download() as first:
        page.click("#metadata-json")
    with page.expect_download() as second:
        page.click("#metadata-json")
    assert Path(first.value.path()).read_bytes() == Path(second.value.path()).read_bytes()


def test_story_links_and_optional_failures_stay_honest(page, base_url):
    page.route("**/data/region_cpi_yoy_pct.json", lambda route: route.fulfill(status=500))
    _goto(page, base_url)
    story = page.get_by_role("button", name="Inflation vs poverty")
    assert story.is_disabled()
    assert "unavailable" in page.locator("#optional-load-notice").inner_text().lower()


def test_region_geography_failure_disables_the_active_regional_finding(page, base_url):
    page.route("**/data/regions.json", lambda route: route.fulfill(status=500))
    _goto(page, base_url, "#story=inflation-vs-poverty&year=2023")
    assert "unavailable" in page.locator("#story-finding").inner_text().lower()
    story = page.locator('#story-switcher button[data-story-id="inflation-vs-poverty"]')
    assert story.is_disabled()
    assert "unavailable" in story.inner_text().lower()
    page.locator("#view-trust > summary").click()
    assert "unavailable" in page.locator("#view-trust").inner_text().lower()


@pytest.mark.parametrize(
    ("path", "indicator"),
    [
        ("doh_spend_per_capita.json", "doh_spend_per_capita"),
        ("infra_spend_per_capita.json", "infra_spend_per_capita"),
        ("dpwh_share_pct.json", "dpwh_share_pct"),
        ("poverty_change_pp.json", "poverty_change_pp"),
        ("dpwh_spend_per_capita_cum.json", "dpwh_spend_per_capita_cum"),
    ],
)
def test_optional_indicator_failure_disables_native_choices(page, base_url, path, indicator):
    page.route(f"**/data/{path}", lambda route: route.fulfill(status=500))
    _goto(page, base_url)
    assert page.locator(f'#x-select option[value="{indicator}"]').is_disabled()


def test_curated_view_anchors_match_story_contract(page, base_url):
    _goto(page, base_url)
    stories = json.loads((PUBLIC / "data" / "stories.json").read_text())
    links = page.locator("#curated-view-links a[data-story-id]")
    assert links.count() == len(stories)
    for story in stories:
        link = page.locator(f'#curated-view-links a[data-story-id="{story["id"]}"]')
        assert f"story={story['id']}" in link.get_attribute("href")


def test_canonical_hash_and_embed_link_keep_full_state(page, base_url):
    _goto(
        page,
        base_url,
        "#story=spend-vs-poverty&year=2018&sel=174000000,012800000&grp=visayas,luzon&extrap=on&embed=1",
    )
    page.wait_for_function("() => location.hash.includes('extrap=on')")
    hash_text = page.evaluate("() => location.hash")
    assert "sel=012800000%2C174000000" in hash_text
    assert "grp=luzon%2Cvisayas" in hash_text
    href = page.locator("#embed-chip").get_attribute("href")
    assert "embed=1" not in href
    assert "extrap=on" in href
    assert "sel=012800000%2C174000000" in href


def test_history_restores_extrapolate_selection_and_groups(page, base_url):
    _goto(page, base_url, "#story=spend-vs-poverty&year=2018")
    page.evaluate(
        "() => location.hash = "
        "'#story=spend-vs-poverty&year=2018&extrap=on&sel=012800000&grp=luzon'"
    )
    page.wait_for_function("() => location.hash.includes('extrap=on')")
    page.go_back(wait_until="networkidle")
    page.wait_for_function("() => !location.hash.includes('extrap=on')")
    assert page.locator("#extrap-toggle").get_attribute("aria-pressed") == "false"
    page.go_forward(wait_until="networkidle")
    page.wait_for_function("() => location.hash.includes('extrap=on')")
    assert page.locator("#extrap-toggle").get_attribute("aria-pressed") == "true"
