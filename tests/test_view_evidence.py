"""Contract checks for the generated view evidence artifact."""

from __future__ import annotations

import json
from pathlib import Path

PUBLIC_DATA = Path(__file__).resolve().parent.parent / "public" / "data"


def test_view_evidence_covers_each_indicator_with_coverage_and_provenance():
    evidence = json.loads((PUBLIC_DATA / "view_evidence.json").read_text())
    indicators = json.loads((PUBLIC_DATA / "indicators.json").read_text())
    assert set(evidence["indicators"]) == {item["id"] for item in indicators}
    for item in evidence["indicators"].values():
        assert item["unit_set"]
        assert item["natural_grain"]
        assert item["years"]
        assert item["source_url"]
        assert item["archive_url"]
        assert item["release"]
        assert item["transforms"]
        assert item["coverage"]
        assert {row["status"] for row in item["coverage"]} <= {
            "full",
            "partial",
            "unavailable",
        }


def test_view_evidence_marks_poverty_depth_partial_and_procurement_unavailable():
    evidence = json.loads((PUBLIC_DATA / "view_evidence.json").read_text())
    poverty_depth = evidence["supplemental_coverage"]["poverty_depth"]
    assert any(row["status"] == "partial" for row in poverty_depth)
    procurement = evidence["procurement_status"]
    assert procurement["status"] == "unavailable"
    assert procurement["candidate_year"] == 2025
    assert procurement["snapshot_anomalies"] == {
        "scope": "snapshot_wide",
        "invalid_award_date_count": 1,
        "future_award_date_count": 11,
    }


def test_methodology_names_the_view_evidence_contract():
    page = (PUBLIC_DATA.parent / "methodology" / "index.html").read_text()
    assert "view evidence" in page.lower()
    assert "1F/FY" in page


def test_tagalog_locale_covers_view_evidence_actions():
    locale = json.loads((PUBLIC_DATA.parent / "locales" / "tl.json").read_text())
    assert locale["controls"]["metadata_json"]
    assert locale["controls"]["citation_text"]


def test_view_evidence_uses_reviewed_sources_and_geography_contract():
    evidence = json.loads((PUBLIC_DATA / "view_evidence.json").read_text())
    assert evidence["indicators"]["cpi_yoy_pct"]["natural_grain"] == "national"
    assert all(
        row["status"] == "full" and row["target_units"] == row["source_units"] == 1
        for row in evidence["indicators"]["cpi_yoy_pct"]["coverage"]
    )
    for indicator_id in ("poverty", "subsistence_incidence", "poverty_change_pp", "region_poverty"):
        item = evidence["indicators"][indicator_id]
        assert "1F/FY" in item["source"]
        assert "1F__FY" in item["archive_url"]
    assert "philgeps" in evidence["indicators"]["dpwh_share_pct"]["archive_url"]
    assert evidence["curated_views"]
