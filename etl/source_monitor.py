"""Read-only source health report for the scheduled upstream monitor."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from etl import philgeps, psa_openstat
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
        "gdp_per_capita": "gdp_per_capita.json",
        "cpi": "cpi.json",
    }
    years: dict[str, int] = {}
    for source, filename in files.items():
        path = PUBLIC_DATA / filename
        if not path.exists():
            continue
        rows = json.loads(path.read_text())
        if isinstance(rows, dict):
            values = [int(year) for year in rows if str(year).isdigit()]
        else:
            values = [row.get("year") for row in rows if isinstance(row.get("year"), int)]
        if values:
            years[source] = max(values)
    return years


def _live_geography_version() -> dict:
    """Compare the committed PSGC fingerprint with the current read-only API response."""
    manifest = json.loads((PUBLIC_DATA / "manifest.json").read_text())
    shipped = manifest.get("inputs", {}).get("psgc", {}).get("cached_sha256")
    response = httpx.get(PSGC_URL, timeout=30.0)
    response.raise_for_status()
    return {"shipped": shipped, "official": hashlib.sha256(response.content).hexdigest()}


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
            "latest_official_year": _latest_year(metadata),
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
