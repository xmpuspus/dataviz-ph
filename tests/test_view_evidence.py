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
