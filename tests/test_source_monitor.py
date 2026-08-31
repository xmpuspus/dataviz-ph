"""Offline tests for the source-monitor report shape and drift gates."""

from __future__ import annotations

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
