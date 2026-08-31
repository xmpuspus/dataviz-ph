"""Contract checks for the generated view evidence artifact."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from etl import build

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


def test_psa_refresh_regenerates_evidence_before_manifest(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "provinces.json").write_text('{"001":{"name":"Test"}}')
    (data_dir / "indicators.json").write_text('[{"id":"population"},{"id":"gdp_per_capita"}]')
    (data_dir / "manifest.json").write_text('{"row_counts":{},"derived":{},"inputs":{}}')
    monkeypatch.setattr(build, "PUBLIC_DATA", data_dir)
    monkeypatch.setattr(build, "PANEL_YEARS", [2024])
    monkeypatch.setattr(build, "GDP_PANEL_YEARS", [2024])
    monkeypatch.setattr(build, "GDP_ANCHORS", [2024])
    monkeypatch.setattr(build, "load_provinces", lambda: {"001": {}})
    monkeypatch.setattr(build, "huc_parent", {})
    monkeypatch.setattr(build, "normalize_name", lambda *args: "001")
    monkeypatch.setattr(
        build.psa_openstat,
        "fetch_population_2020",
        lambda *args, **kwargs: [{"psgc": "001", "value": 1}],
    )
    monkeypatch.setattr(
        build.psa_openstat,
        "fetch_population_2024",
        lambda *args, **kwargs: [{"psgc": "001", "value": 2}],
    )
    monkeypatch.setattr(
        build.psa_openstat,
        "interpolate_population_anchors",
        lambda *args: [{"psgc": "001", "year": 2024, "value": 2}],
    )
    monkeypatch.setattr(build.psa_openstat, "fetch_gdp_total", lambda *args: [])
    monkeypatch.setattr(build.psa_openstat, "fetch_gdp_per_capita_source", lambda *args: [])
    monkeypatch.setattr(build.psa_openstat, "validate_gdp_industry_contract", lambda: {})
    monkeypatch.setattr(build.psa_openstat, "recompute_gdp_per_capita", lambda *args: [])
    monkeypatch.setattr(build.psa_openstat, "fetch_poverty_depth", lambda *args: [])
    monkeypatch.setattr(build.psa_openstat, "require_source_years", lambda *args: None)
    monkeypatch.setattr(build.psa_openstat, "require_analysis_coverage", lambda *args: None)
    monkeypatch.setattr(build.validate, "validate_uniqueness", lambda *args, **kwargs: None)
    monkeypatch.setattr(build.validate, "validate_precision", lambda *args, **kwargs: None)
    monkeypatch.setattr(build.validate, "validate_coverage", lambda *args, **kwargs: None)
    monkeypatch.setattr(build.validate, "validate_all", lambda *args, **kwargs: None)
    monkeypatch.setattr(build, "poverty_depth_coverage", lambda *args, **kwargs: [])
    monkeypatch.setattr(build, "write_procurement_status", lambda: None)

    def evidence():
        indicators = json.loads((data_dir / "indicators.json").read_text())
        return {
            "population_name": next(
                item["name"] for item in indicators if item["id"] == "population"
            )
        }

    monkeypatch.setattr(build, "build_view_evidence", evidence)

    build.refresh_automated_psa_public_data()

    evidence_bytes = (data_dir / "view_evidence.json").read_bytes()
    manifest = json.loads((data_dir / "manifest.json").read_text())
    assert json.loads(evidence_bytes)["population_name"] == "Population (2020 and 2024 POPCEN)"
    assert (
        manifest["sha256_per_file"]["view_evidence.json"]
        == hashlib.sha256(evidence_bytes).hexdigest()
    )
