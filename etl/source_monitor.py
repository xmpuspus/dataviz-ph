"""Read-only source health report for the scheduled upstream monitor."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

import httpx

from etl import geography, philgeps, psa_inflation, psa_openstat, psgc
from etl.source_catalog import PSA_TABLES, TableContract

PUBLIC_DATA = Path(__file__).resolve().parent.parent / "public" / "data"
PSGC_RELEASE_URL = "https://psa.gov.ph/classification/psgc"
PSGC_PROVINCES_URL = "https://psa.gov.ph/classification/psgc/provinces"


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def assess_source_state(
    *,
    contracts: dict[str, dict],
    shipped_years: dict[str, int],
    snapshot_inventory: dict | None,
    geography_version: dict | None,
    now: str,
) -> dict:
    """Create a serializable report from live probes and local, shipped evidence."""
    sources: dict[str, dict] = {}
    for name, evidence in contracts.items():
        official_year = evidence.get("latest_official_year")
        shipped_year = shipped_years.get(name)
        coverage = evidence.get("coverage", {})
        sources[name] = {
            "status": "reachable" if evidence.get("reachable", True) else "unreachable",
            "latest_official_year": official_year,
            "shipped_year": shipped_year,
            "shipped_lag_years": (
                official_year - shipped_year
                if isinstance(official_year, int) and isinstance(shipped_year, int)
                else None
            ),
            "coverage": coverage,
            "coverage_status": "complete"
            if coverage and all(item.get("status") == "complete" for item in coverage.values())
            else "short",
        }
    snapshot: dict = {"status": "missing"}
    if snapshot_inventory:
        fetched_at = snapshot_inventory.get("fetched_at")
        age_days = None
        if fetched_at:
            age_days = (_parse_datetime(now) - _parse_datetime(fetched_at)).days
        candidate_year = max(philgeps.REVIEWED_CANDIDATE_YEARS)
        attestation = snapshot_inventory.get("correction_attestation")
        try:
            attestation_status = philgeps.validate_correction_attestation(
                attestation, snapshot_inventory.get("snapshot_id")
            )
        except ValueError:
            attestation_status = "invalid"
        snapshot = {
            "status": "present",
            "age_days": age_days,
            "supported_date_range": snapshot_inventory.get("supported_date_range"),
            "latest_complete_philgeps_year": philgeps.LATEST_REVIEWED_COMPLETE_YEAR,
            "candidate_year": candidate_year,
            "correction_attestation": attestation,
            "correction_attestation_status": attestation_status,
            "candidate_year_status": philgeps.assess_year_gate(snapshot_inventory, candidate_year),
        }
    geography = {"status": "unknown"}
    if geography_version:
        geography = {**geography_version}
        geography.setdefault(
            "status",
            "current"
            if geography_version.get("shipped") == geography_version.get("official")
            else "drift",
        )
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


def _get_text(url: str) -> str:
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text


class _ProvinceTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_cell = False
        self.cell_parts: list[str] = []
        self.row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag == "td":
            self.in_cell = True
            self.cell_parts = []
        elif tag == "tr":
            self.row = []

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self.in_cell:
            self.row.append(" ".join("".join(self.cell_parts).split()))
            self.in_cell = False
        elif tag == "tr" and self.row:
            self.rows.append(self.row)


def _parse_official_provinces(page: str) -> list[dict]:
    parser = _ProvinceTableParser()
    parser.feed(page)
    records = []
    for row in parser.rows:
        if len(row) >= 3 and re.fullmatch(r"\d{10}", row[1]):
            records.append(
                {
                    "name": unescape(row[0]),
                    "source_psgc": row[1],
                    "correspondence_code": row[2] or None,
                }
            )
    if records:
        return records
    for line in page.splitlines():
        match = re.search(r"^\s*(.+?)\s*\|\s*(\d{10})\s*\|\s*(\d{9})?\s*\|", line)
        if match:
            records.append(
                {
                    "name": match.group(1).strip(),
                    "source_psgc": match.group(2),
                    "correspondence_code": match.group(3) or None,
                }
            )
    return records


def _live_geography_version() -> dict:
    """Compare committed PSGC identities with the current official PSA pages."""
    manifest = json.loads((PUBLIC_DATA / "manifest.json").read_text())
    shipped_meta = manifest.get("inputs", {}).get("psgc", {})
    release_page = _get_text(PSGC_RELEASE_URL)
    provinces_page = _get_text(PSGC_PROVINCES_URL)
    release_match = re.search(r"((?:First|Second|Third|Fourth) Quarter \d{4}) PSGC", release_page)
    as_of_match = re.search(
        r"Philippine Standard Geographic Code as of (\d{1,2} [A-Za-z]+ \d{4})",
        release_page,
    )
    if not release_match or not as_of_match:
        raise ValueError("Official PSGC page does not expose a parseable release identity")
    observed = _parse_official_provinces(provinces_page)
    if not observed:
        raise ValueError("Official PSGC provinces page has no parseable province rows")
    expected = {item["source_psgc"]: item for item in geography.current_province_records()}
    actual = {item["source_psgc"]: item for item in observed}
    added = sorted(set(actual) - set(expected))
    removed = sorted(set(expected) - set(actual))
    changed = sorted(
        code
        for code in set(actual) & set(expected)
        if actual[code]["name"] != expected[code]["name"]
        or actual[code]["correspondence_code"] != expected[code]["correspondence_code"]
    )
    observed_release = f"{release_match.group(1)} PSGC"
    observed_as_of = datetime.strptime(as_of_match.group(1), "%d %B %Y").date().isoformat()
    release_drift = observed_release != shipped_meta.get(
        "release"
    ) or observed_as_of != shipped_meta.get("as_of")
    fixture_hash = hashlib.sha256(geography.OFFICIAL_PSGC_2Q_2026_FIXTURE.read_bytes()).hexdigest()
    return {
        "status": "drift" if release_drift or added or removed or changed else "current",
        "shipped_release": shipped_meta.get("release"),
        "shipped_as_of": shipped_meta.get("as_of"),
        "official_release": observed_release,
        "official_as_of": observed_as_of,
        "observed_province_count": len(actual),
        "added_source_psgcs": added,
        "removed_source_psgcs": removed,
        "changed_source_psgcs": changed,
        "official_fixture_sha256": fixture_hash,
    }


def _location_labels(metadata: dict) -> list[str]:
    for variable in metadata.get("variables", []):
        if variable.get("code") in {"Geolocation", "Geographic Location"}:
            labels = variable.get("valueTexts") or variable.get("values") or []
            return [str(label) for label in labels]
    return []


def probe_metadata_coverage(contract: TableContract, metadata: dict) -> dict[str, dict]:
    """Measure each table against its declared grain-specific coverage target."""
    labels = _location_labels(metadata)
    raw_count = len(set(labels))
    results = {}
    for target, expected_count in contract.coverage_targets:
        if target == "analysis_areas":
            expected_ids = set(psgc.load_provinces())
            observed_ids = set()
            for label in labels:
                clean = psa_openstat._clean_geo_text(label)
                code = psgc.huc_parent(clean) or psgc.normalize_name(
                    clean, series=contract.series_policy
                )
                if code in expected_ids:
                    observed_ids.add(code)
        elif target == "regions":
            expected_ids = {item["id"] for item in psa_inflation.REGIONS}
            observed_ids = {
                region_id
                for label in labels
                if (region_id := psa_inflation.region_id_for(label)) is not None
            }
        elif target == "national":
            expected_ids = {"national"}
            observed_ids = {
                "national"
                for label in labels
                if psa_openstat._clean_geo_text(label).strip().lower()
                in {"philippines", "national", "total philippines"}
            }
        else:
            expected_ids = {f"published-{index}" for index in range(expected_count)}
            observed_ids = {f"published-{index}" for index in range(raw_count)}
        missing = sorted(expected_ids - observed_ids)
        results[target] = {
            "expected": expected_count,
            "observed": len(observed_ids),
            "missing": missing,
            "status": "complete" if observed_ids == expected_ids else "short",
            "raw_label_count": raw_count,
        }
    return results


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
            "coverage": probe_metadata_coverage(PSA_TABLES[name], metadata),
        }
    inventory = philgeps.load_reviewed_snapshot_inventory(required=False)
    try:
        geography_version = _live_geography_version()
    except Exception as exc:
        geography_version = {
            "status": "unreachable",
            "source_url": PSGC_RELEASE_URL,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return assess_source_state(
        contracts=evidence,
        shipped_years=_shipped_years(),
        snapshot_inventory=inventory,
        geography_version=geography_version,
        now=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )


def release_blockers(report: dict) -> list[str]:
    """Name every PSA source state that must fail a scheduled run.

    The official PSGC page answers an unattended request with a WAF 403, so an
    unreachable geography page is reported and never treated as a blocker. A PSA
    OpenStat table that goes away, or that stops covering its declared grain, is
    a real contract break and must turn the run red.
    """
    blockers = []
    for name, source in sorted(report.get("sources", {}).items()):
        if source.get("status") != "reachable":
            blockers.append(f"{name} is unreachable")
        elif source.get("coverage_status") != "complete":
            blockers.append(f"{name} coverage is short")
    return blockers


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only upstream source contract monitor")
    parser.add_argument("--live", action="store_true", help="Perform network metadata probes")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required; ordinary unit tests must stay offline")
    report = run_live_checks()
    print(json.dumps(report, indent=2, sort_keys=True))
    blockers = release_blockers(report)
    if blockers:
        print("source contract failures: " + "; ".join(blockers), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
