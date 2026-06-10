"""Unit tests for the regional inflation/poverty cut (etl/psa_inflation.py).

Pure-logic tests (no network): region label matching incl. the AONCR landmine,
the YoY arithmetic, and the shape of the committed region data files.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from etl.psa_inflation import (
    REGIONS,
    compute_regional_cpi_yoy,
    load_regions,
    region_id_for,
)

DATA = Path(__file__).resolve().parent.parent / "public" / "data"

VALID_ISLAND_GROUPS = {"luzon", "visayas", "mindanao", "ncr", "barmm"}


def test_regions_are_18_unique_with_valid_island_groups():
    assert len(REGIONS) == 18
    ids = [r["id"] for r in REGIONS]
    assert len(set(ids)) == 18
    for r in REGIONS:
        assert r["island_group"] in VALID_ISLAND_GROUPS, r
        assert r["name"]
    units = load_regions()
    assert set(units) == set(ids)
    # The unit set mirrors provinces.json's shape (name + island_group).
    assert all({"name", "island_group"} <= set(v) for v in units.values())


def test_region_id_for_matches_both_source_tables():
    # Labels exactly as PSA publishes them (poverty Table 1a carries footnotes).
    cases = {
        "..National Capital Region (NCR) 1/, 2/, a/": "ncr",
        "..Cordillera Administrative Region (CAR) 1/, 2/, 3/": "car",
        "..Region I (Ilocos Region) 1/, 2/": "r01",
        "..Region II (Cagayan Valley) 2/, 3/": "r02",
        "..Region III (Central Luzon)": "r03",
        "..Region IV-A (CALABARZON)": "r04a",
        "..MIMAROPA Region 1/, 3/": "mimaropa",
        "..Region V (Bicol Region)": "r05",
        "..Region VI (Western Visayas) r2, 2/": "r06",
        "..Negros Island Region (NIR) 2/, 3/": "nir",
        "..Region VII (Central Visayas) r2, 1/, 2/": "r07",
        "..Region VIII (Eastern Visayas) 3/": "r08",
        "..Region IX (Zamboanga Peninsula)": "r09",
        "..Region X (Northern Mindanao)": "r10",
        "..Region XI (Davao Region) 1/, 3/": "r11",
        "..Region XII (SOCCSKSARGEN) 2/, 3/, 4/": "r12",
        "..Region XIII (Caraga) 2/, 3/": "caraga",
        "..Bangsamoro Autonomous Region in Muslim Mindanao (BARMM) r1, 1/": "barmm",
    }
    for label, expected in cases.items():
        assert region_id_for(label) == expected, label


def test_region_id_for_rejects_lookalikes():
    # The CPI table's AONCR row contains "National Capital Region" but must
    # never match NCR; provinces and the national row must match nothing.
    for label in (
        "..Areas Outside National Capital Region (AONCR)",
        "PHILIPPINES r1, 1/, 2/, 3/",
        "....Negros Occidental",
        "....Negros Oriental",
        "....Ilocos Norte",
        "....City of Zamboanga",
        "......Quezon City 2/, a/, b/, c/",
    ):
        assert region_id_for(label) is None, label


def test_compute_regional_cpi_yoy_arithmetic_and_guards():
    series = {
        "r01": {2018: 100.0, 2019: 104.0, 2020: 106.08},
        # Gap year: 2021 missing, so 2022 has no consecutive prior.
        "r02": {2020: 100.0, 2022: 110.0},
        # Zero index must not divide.
        "r03": {2018: 0.0, 2019: 100.0},
    }
    rows = compute_regional_cpi_yoy(series)
    by = {(r["psgc"], r["year"]): r["value"] for r in rows}
    assert abs(by[("r01", 2019)] - 4.0) < 1e-9
    assert abs(by[("r01", 2020)] - 2.0) < 1e-9
    assert ("r02", 2022) not in by
    assert ("r03", 2019) not in by


def test_compute_regional_cpi_yoy_drops_in_progress_year():
    this_year = datetime.now(UTC).year
    series = {"ncr": {this_year - 2: 100.0, this_year - 1: 105.0, this_year: 110.0}}
    rows = compute_regional_cpi_yoy(series)
    years = {r["year"] for r in rows}
    assert this_year not in years, "partial-year annual average must not ship"
    assert (this_year - 1) in years


# ---- committed data files -----------------------------------------------------


def test_committed_region_files_cover_18_units():
    regions = json.loads((DATA / "regions.json").read_text())
    assert len(regions) == 18
    pov = json.loads((DATA / "region_poverty.json").read_text())
    for year in (2018, 2021, 2023):
        assert {r["psgc"] for r in pov if r["year"] == year} == set(regions), year
    yoy = json.loads((DATA / "region_cpi_yoy_pct.json").read_text())
    assert {r["psgc"] for r in yoy} == set(regions)
    # YoY never includes the in-progress calendar year.
    assert max(r["year"] for r in yoy) < datetime.now(UTC).year


def test_committed_region_story_finding_is_complete():
    stories = json.loads((DATA / "stories.json").read_text())
    story = next(s for s in stories if s["id"] == "inflation-vs-poverty")
    assert story["x"] == "region_cpi_yoy_pct"
    assert story["y"] == "region_poverty"
    assert story["unit_set"] == "regions"
    # The tagline documents the granularity drop in plain words.
    assert "18 regions" in story["tagline"]
    assert "82" in story["tagline"]
    f = story["finding"]
    assert f["available"] is True
    assert f["n"] == 18
    assert isinstance(f["spearman"], float)
    assert isinstance(f["p_value"], float)
    assert "rho = " in f["sentence"]
    assert "Correlation, not causation." in f["caveat"]
    indicators = {i["id"]: i for i in json.loads((DATA / "indicators.json").read_text())}
    assert indicators["region_cpi_yoy_pct"]["unit_set"] == "regions"
    assert indicators["region_poverty"]["unit_set"] == "regions"
    assert indicators["region_poverty"]["has_ci"] is True
