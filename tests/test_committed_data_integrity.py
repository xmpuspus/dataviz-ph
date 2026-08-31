"""Gate the COMMITTED public/data/*.json on every CI run.

Vercel deploys public/ straight from main, and CI is the only thing between a
commit and prod. The other ETL tests run validate.py against synthetic fixtures;
this one runs the same gates against the REAL shipped files, so a hand-edited
JSON, a half-empty source pull committed by a human, or a bad build can't reach
the public numbers unnoticed. Network-free: reads the committed files only.

manifest-sha and missing-file drift are already covered by
test_manifest_integrity.py; this file adds the data-correctness gates it doesn't:
coverage, (psgc, year) uniqueness, value ranges, and stories-finding sanity.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from etl import validate

PUBLIC_DATA = Path(__file__).resolve().parent.parent / "public" / "data"
POVERTY_ANCHORS = [2018, 2021, 2023]
PANEL_YEARS = list(range(2014, 2025))
N_PROVINCE_UNITS = 82  # 81 provinces + Metro Manila
N_REGIONS = 18


def _load(name: str) -> list | dict:
    return json.loads((PUBLIC_DATA / name).read_text())


# Every unit-keyed series file shipped to the browser.
UNIT_FILES = [
    "poverty.json",
    "subsistence.json",
    "population.json",
    "gdp_per_capita.json",
    "dpwh_spend_per_capita.json",
    "all_spend_per_capita.json",
    "doh_spend_per_capita.json",
    "infra_spend_per_capita.json",
    "dpwh_share_pct.json",
    "cpi_yoy_pct.json",
    "poverty_change_pp.json",
    "dpwh_spend_per_capita_cum.json",
    "region_poverty.json",
    "region_cpi_yoy_pct.json",
]


@pytest.mark.parametrize("name", UNIT_FILES)
def test_no_duplicate_psgc_year(name: str) -> None:
    """A dedup regression once doubled award rows; assert it can't recur."""
    rows = _load(name)
    seen: set[tuple[str, int]] = set()
    for r in rows:
        key = (r["psgc"], r["year"])
        assert key not in seen, f"{name}: duplicate (psgc, year) {key}"
        seen.add(key)


def test_full_coverage_files_are_complete() -> None:
    """poverty/population/region_poverty are full panels: assert exact unit counts
    so a half-empty pull (e.g. 40 of 82 provinces) can't ship a partial chart."""
    validate.validate_coverage(_load("poverty.json"), N_PROVINCE_UNITS, POVERTY_ANCHORS, "poverty")
    validate.validate_coverage(
        _load("population.json"), N_PROVINCE_UNITS, PANEL_YEARS, "population"
    )
    validate.validate_coverage(
        _load("region_poverty.json"), N_REGIONS, POVERTY_ANCHORS, "region_poverty"
    )


def test_partial_coverage_is_internally_consistent() -> None:
    """GDP and regional CPI legitimately cover fewer units, but every year they
    cover must carry the SAME count. A sudden drop in one year = a broken pull."""
    for name in ("gdp_per_capita.json", "region_cpi_yoy_pct.json"):
        rows = _load(name)
        per_year: dict[int, int] = {}
        for r in rows:
            per_year[r["year"]] = per_year.get(r["year"], 0) + 1
        counts = set(per_year.values())
        assert len(counts) == 1, f"{name}: inconsistent unit count per year {per_year}"


def test_refreshed_population_and_gdp_contracts_keep_reviewed_totals() -> None:
    provinces = _load("provinces.json")
    assert len(provinces) == N_PROVINCE_UNITS
    assert provinces["072200000"]["population_2024"] == 5_228_149
    assert provinces["126300000"]["population_2024"] == 1_732_068
    assert provinces["150700000"]["population_2024"] == 693_244

    gdp = _load("gdp_per_capita.json")
    assert len(gdp) == 656
    assert {(row["psgc"], row["year"]) for row in gdp} == {
        (psgc, year) for psgc in provinces for year in range(2018, 2026)
    }


def test_poverty_depth_rows_keep_published_precision_and_coverage_reasons() -> None:
    rows = _load("poverty_depth.json")
    assert all("se" in row and "ci_lo" in row and "ci_hi" in row for row in rows)
    assert all(row["ci_lo"] <= row["ci_hi"] for row in rows)
    coverage = _load("poverty_depth_coverage.json")
    assert all("missing_reasons" in row for row in coverage)


def test_rate_files_within_0_100() -> None:
    for name in ("poverty.json", "subsistence.json", "region_poverty.json"):
        for r in _load(name):
            assert 0.0 <= r["value"] <= 100.0, f"{name}: out-of-range rate {r}"


def test_spend_and_population_nonnegative() -> None:
    # poverty_change_pp is intentionally signed (negative = poverty fell), so it
    # is excluded here on purpose.
    for name in (
        "population.json",
        "dpwh_spend_per_capita.json",
        "all_spend_per_capita.json",
        "doh_spend_per_capita.json",
        "infra_spend_per_capita.json",
        "gdp_per_capita.json",
        "dpwh_spend_per_capita_cum.json",
    ):
        for r in _load(name):
            assert r["value"] >= 0, f"{name}: negative value {r}"


def test_stories_findings_are_sane() -> None:
    """Every shipped finding must be statistically well-formed and not over-claim
    its unit count. Also guards that the dropped pearson field stays dropped."""
    stories = _load("stories.json")
    for s in stories:
        f = s.get("finding")
        if not f or not f.get("available"):
            continue
        sid = s["id"]
        assert -1.0 <= f["spearman"] <= 1.0, f"{sid}: spearman {f['spearman']} out of range"
        assert 0.0 <= f["p_value"] <= 1.0, f"{sid}: p_value {f['p_value']} out of range"
        universe = N_REGIONS if s.get("unit_set") == "regions" else N_PROVINCE_UNITS
        assert 3 <= f["n"] <= universe, f"{sid}: n={f['n']} outside [3, {universe}]"
        assert "pearson" not in f, f"{sid}: pearson field should be dropped from findings"
        # The "n of expected" reconciliation: when n is short of the universe, the
        # sentence must say so rather than read as full coverage.
        if f["n"] < universe:
            assert f"of {universe} areas" in f["sentence"], (
                f"{sid}: n={f['n']} < {universe} but sentence omits the "
                f"'of {universe} areas' clause"
            )
