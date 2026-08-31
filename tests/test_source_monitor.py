"""Offline tests for the source-monitor report shape and drift gates."""

from __future__ import annotations

import json

from etl import philgeps, psa_openstat, source_monitor
from etl.source_monitor import assess_source_state


def test_monitor_reports_source_and_shipped_freshness_without_network():
    report = assess_source_state(
        contracts={"poverty": {"latest_official_year": 2025, "unit_coverage": 82}},
        shipped_years={"poverty": 2023},
        expected_unit_count=82,
        snapshot_inventory={
            "fetched_at": "2026-08-01T00:00:00Z",
            "revision_status": "review required",
            "supported_date_range": {"start": 2014, "end": 2024},
        },
        geography_version={"shipped": "2024-01", "official": "2025-01"},
        now="2026-08-31T00:00:00Z",
    )

    assert report["sources"]["poverty"]["status"] == "reachable"
    assert report["sources"]["poverty"]["shipped_lag_years"] == 2
    assert report["snapshot"]["age_days"] == 30
    assert report["snapshot"]["revision_status"] == "review required"
    assert report["geography"]["status"] == "drift"


def test_monitor_flags_short_unit_coverage():
    report = assess_source_state(
        contracts={"poverty": {"latest_official_year": 2023, "unit_coverage": 81}},
        shipped_years={"poverty": 2023},
        expected_unit_count=82,
        snapshot_inventory=None,
        geography_version=None,
        now="2026-08-31T00:00:00Z",
    )

    assert report["sources"]["poverty"]["coverage_status"] == "short"


def test_monitor_reports_philgeps_gate_and_snapshot_age():
    report = assess_source_state(
        contracts={},
        shipped_years={},
        expected_unit_count=82,
        snapshot_inventory={
            "fetched_at": "2026-05-26T13:17:16.561587Z",
            "revision_status": "not compared to a newer snapshot",
            "supported_date_range": {"start": "1920-01-08", "end": "2034-10-04"},
            "year_candidates": {
                "2025": {
                    "unique_award_id_count": 506831,
                    "date_range": {"start": "2025-01-01", "end": "2025-12-27"},
                    "month_counts": {str(month): 1 for month in range(1, 13)},
                    "invalid_award_date_count": 1,
                    "future_award_date_count": 11,
                    "candidate_series_coverage": {"all_spend": 82},
                }
            },
        },
        geography_version=None,
        now="2026-08-31T00:00:00Z",
    )

    snapshot = report["snapshot"]
    assert snapshot["age_days"] == 96
    assert snapshot["latest_complete_philgeps_year"] == 2024
    assert snapshot["candidate_year_status"]["status"] == "unavailable"
    assert snapshot["candidate_year_status"]["failed_gates"] == [
        "date_range_incomplete",
        "invalid_award_dates",
        "future_award_dates",
        "correction_comparison_pending",
    ]


def test_live_monitor_normalizes_shipped_cpi_object_shape(monkeypatch):
    metadata = {
        "variables": [
            {"code": "Geolocation", "values": ["A"] * 82},
            {"code": "Year", "values": ["0", "1", "2"], "valueTexts": ["2023", "2024", "2025"]},
        ]
    }
    monkeypatch.setattr(psa_openstat, "discover_table", lambda name: (f"path/{name}", metadata))
    monkeypatch.setattr(philgeps, "load_snapshot_inventory", lambda **_: None)
    monkeypatch.setattr(
        source_monitor, "_live_geography_version", lambda: {"shipped": "x", "official": "x"}
    )

    report = source_monitor.run_live_checks()

    assert report["sources"]["cpi"]["shipped_year"] == 2025
    assert report["sources"]["population"]["latest_official_year"] == 2020


def test_shipped_years_use_source_anchors_and_population_vintage(tmp_path, monkeypatch):
    monkeypatch.setattr(source_monitor, "PUBLIC_DATA", tmp_path)
    (tmp_path / "poverty.json").write_text(
        json.dumps(
            [
                {"year": 2023, "interp": False, "extrap": False},
                {"year": 2024, "interp": False, "extrap": True},
            ]
        )
    )
    (tmp_path / "subsistence.json").write_text(
        json.dumps(
            [
                {"year": 2023, "interp": False, "extrap": False},
                {"year": 2024, "interp": True, "extrap": False},
            ]
        )
    )
    (tmp_path / "population.json").write_text(json.dumps([{"year": 2024}]))
    (tmp_path / "gdp_per_capita.json").write_text(json.dumps([{"year": 2024}]))
    (tmp_path / "poverty_depth.json").write_text(
        json.dumps(
            [
                {"measure": "poverty_poor_families", "year": 2023},
                {"measure": "poverty_income_gap", "year": 2023},
                {"measure": "poverty_poverty_gap", "year": 2023},
                {"measure": "poverty_severity", "year": 2023},
            ]
        )
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps({"inputs": {"psa_openstat": {"ppa_industry_contract": {"years": [2018, 2025]}}}})
    )
    (tmp_path / "cpi.json").write_text(json.dumps({"2018": 100.0, "2025": 128.2}))

    years = source_monitor._shipped_years()

    assert years == {
        "poverty": 2023,
        "subsistence": 2023,
        "population": 2020,
        "population_2024": 2024,
        "gdp_total": 2024,
        "gdp_industry": 2025,
        "gdp_per_capita": 2024,
        "poverty_poor_families": 2023,
        "poverty_income_gap": 2023,
        "poverty_poverty_gap": 2023,
        "poverty_severity": 2023,
        "cpi": 2025,
    }
