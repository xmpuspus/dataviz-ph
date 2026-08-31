"""Offline tests for the source-monitor report shape and drift gates."""

from __future__ import annotations

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
