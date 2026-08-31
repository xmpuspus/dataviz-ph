"""Read-only source health report for the scheduled upstream monitor."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from etl import geography, philgeps, psa_openstat
from etl.source_catalog import PSA_TABLES

PUBLIC_DATA = Path(__file__).resolve().parent.parent / "public" / "data"
PSGC_URL = "https://psgc.gitlab.io/api/provinces.json"


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def assess_source_state(
    *,
    contracts: dict[str, dict],
    shipped_years: dict[str, int],
    expected_unit_count: int,
    snapshot_inventory: dict | None,
    geography_version: dict | None,
    now: str,
) -> dict:
    """Create a serializable report from live probes and local, shipped evidence."""
    sources: dict[str, dict] = {}
    for name, evidence in contracts.items():
        official_year = evidence.get("latest_official_year")
        shipped_year = shipped_years.get(name)
        coverage = evidence.get("unit_coverage")
        sources[name] = {
            "status": "reachable" if evidence.get("reachable", True) else "unreachable",
            "latest_official_year": official_year,
            "shipped_year": shipped_year,
            "shipped_lag_years": (
                official_year - shipped_year
                if isinstance(official_year, int) and isinstance(shipped_year, int)
                else None
            ),
            "unit_coverage": coverage,
            "coverage_status": "complete"
            if isinstance(coverage, int) and coverage >= expected_unit_count
            else "short",
        }
    snapshot: dict = {"status": "missing"}
    if snapshot_inventory:
        fetched_at = snapshot_inventory.get("fetched_at")
        age_days = None
        if fetched_at:
            age_days = (_parse_datetime(now) - _parse_datetime(fetched_at)).days
        snapshot = {
            "status": "present",
            "age_days": age_days,
            "revision_status": snapshot_inventory.get("revision_status", "unknown"),
            "supported_date_range": snapshot_inventory.get("supported_date_range"),
            "latest_complete_philgeps_year": philgeps.PANEL_END,
            "candidate_year_status": philgeps.assess_year_gate(
                snapshot_inventory, philgeps.PANEL_END + 1
            ),
        }
    geography = {"status": "unknown"}
    if geography_version:
        geography = {
            **geography_version,
            "status": (
                "current"
                if geography_version.get("shipped") == geography_version.get("official")
                else "drift"
            ),
        }
    return {"checked_at": now, "sources": sources, "snapshot": snapshot, "geography": geography}


def _latest_year(metadata: dict) -> int | None:
    for variable in metadata.get("variables", []):
        if variable.get("code") == "Year":
            years = [
                int(value)
                for value in [*variable.get("values", []), *variable.get("valueTexts", [])]
                if str(value).isdigit()
            ]
            return max(years, default=None)
    return None


def _shipped_years() -> dict[str, int]:
    """Read the latest years in committed source-derived series, without rebuilding data."""
    files = {
        "poverty": "poverty.json",
        "subsistence": "subsistence.json",
        "population": "population.json",
        "population_2024": "population.json",
        "gdp_total": "gdp_per_capita.json",
        "gdp_per_capita": "gdp_per_capita.json",
        "poverty_poor_families": "poverty_depth.json",
        "poverty_income_gap": "poverty_depth.json",
        "poverty_poverty_gap": "poverty_depth.json",
        "poverty_severity": "poverty_depth.json",
        "cpi": "cpi.json",
    }
    years: dict[str, int] = {}
    manifest_path = PUBLIC_DATA / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        industry = (
            manifest.get("inputs", {}).get("psa_openstat", {}).get("ppa_industry_contract", {})
        )
        industry_years = industry.get("years", [])
        if industry_years:
            years["gdp_industry"] = max(industry_years)
    for source, filename in files.items():
        fixed_source_year = PSA_TABLES[source].fixed_source_year
        if fixed_source_year is not None:
            years[source] = fixed_source_year
            continue
        path = PUBLIC_DATA / filename
        if not path.exists():
            continue
        rows = json.loads(path.read_text())
        if isinstance(rows, dict):
            values = [int(year) for year in rows if str(year).isdigit()]
        else:
            source_rows = rows
            if source in {"poverty", "subsistence"}:
                source_rows = [
                    row
                    for row in rows
                    if not row.get("interp", False) and not row.get("extrap", False)
                ]
            if source == "population_2024":
                source_rows = [
                    row for row in rows if row.get("official") and row.get("year") == 2024
                ]
            if source.startswith("poverty_"):
                source_rows = [row for row in rows if row.get("measure") == source]
            values = [row.get("year") for row in source_rows if isinstance(row.get("year"), int)]
        if values:
            years[source] = max(values)
    return years


def _live_geography_version() -> dict:
    """Compare the committed official PSGC release identity with the pinned source fixture."""
    manifest = json.loads((PUBLIC_DATA / "manifest.json").read_text())
    psgc = manifest.get("inputs", {}).get("psgc", {})
    shipped = f"{psgc.get('release')}|{psgc.get('as_of')}"
    official = "Second Quarter 2026 PSGC|2026-06-30"
    fixture_hash = hashlib.sha256(geography.OFFICIAL_PSGC_2Q_2026_FIXTURE.read_bytes()).hexdigest()
    return {"shipped": shipped, "official": official, "official_fixture_sha256": fixture_hash}


def run_live_checks() -> dict:
    """Probe PSA metadata and local PhilGEPS inventory without changing either source."""
    evidence: dict[str, dict] = {}
    for name in PSA_TABLES:
        try:
            path, metadata = psa_openstat.discover_table(name)
        except Exception as exc:
            evidence[name] = {
                "reachable": False,
                "error": f"{type(exc).__name__}: {exc}",
                "latest_official_year": None,
                "unit_coverage": None,
            }
            continue
        evidence[name] = {
            "reachable": True,
            "path": path,
            "latest_official_year": PSA_TABLES[name].fixed_source_year or _latest_year(metadata),
            "unit_coverage": len(
                next(
                    (
                        v.get("values", [])
                        for v in metadata.get("variables", [])
                        if v.get("code") in {"Geolocation", "Geographic Location"}
                    ),
                    [],
                )
            ),
        }
    inventory = philgeps.load_reviewed_snapshot_inventory(required=False)
    return assess_source_state(
        contracts=evidence,
        shipped_years=_shipped_years(),
        expected_unit_count=82,
        snapshot_inventory=inventory,
        geography_version=_live_geography_version(),
        now=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only upstream source contract monitor")
    parser.add_argument("--live", action="store_true", help="Perform network metadata probes")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required; ordinary unit tests must stay offline")
    print(json.dumps(run_live_checks(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
