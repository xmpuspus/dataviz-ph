"""Contract tests for the stable dataviz.ph analytical geography."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from etl.geography import (
    ANALYSIS_AREA_COUNT,
    ANALYSIS_GEOGRAPHY_VERSION,
    CURRENT_PSGC_VINTAGE,
    HISTORICAL_GEOMETRY_VERSION,
    OFFICIAL_PSGC_2Q_2026_FIXTURE,
    build_geography_crosswalk,
    current_province_records,
    series_allows_split_mapping,
    validate_crosswalk,
)

PUBLIC_DATA = Path(__file__).resolve().parent.parent / "public" / "data"


def test_current_official_psgc_fixture_covers_all_82_current_provinces() -> None:
    records = current_province_records()

    assert len(records) == 82
    assert {"1908700000", "1908800000", "0906600000", "1804500000"} <= {
        record["source_psgc"] for record in records
    }
    assert all(len(record["source_psgc"]) == 10 for record in records)
    assert all("correspondence_code" in record for record in records)


def test_current_records_are_exactly_the_independent_reviewed_psa_fixture() -> None:
    fixture = json.loads(OFFICIAL_PSGC_2Q_2026_FIXTURE.read_text())

    assert len(fixture) == 82
    assert current_province_records() == fixture
    assert fixture[0] == {
        "source_psgc": "0102800000",
        "correspondence_code": "012800000",
        "name": "Ilocos Norte",
        "current_region_code": "0100000000",
        "analysis_psgc": "012800000",
    }


def test_crosswalk_maps_current_psgc_to_the_stable_82_area_universe() -> None:
    crosswalk = build_geography_crosswalk()
    validate_crosswalk(crosswalk)

    mappings = crosswalk["mappings"]
    assert crosswalk["analysis_geography_version"] == ANALYSIS_GEOGRAPHY_VERSION
    assert crosswalk["current_psgc_vintage"] == CURRENT_PSGC_VINTAGE
    assert crosswalk["historical_geometry_version"] == HISTORICAL_GEOMETRY_VERSION
    assert len(mappings) == 83  # 82 current provinces plus virtual NCR.
    assert len({item["analysis_psgc"] for item in mappings}) == ANALYSIS_AREA_COUNT
    assert {item["source_psgc"] for item in mappings if item["source_psgc"]} == {
        record["source_psgc"] for record in current_province_records()
    }


def test_boundary_events_are_explicit_and_never_silently_relabel_history() -> None:
    mappings = build_geography_crosswalk()["mappings"]
    by_source = {item["source_psgc"]: item for item in mappings}

    assert by_source["1908700000"]["analysis_psgc"] == "153800000"
    assert by_source["1908800000"]["analysis_psgc"] == "153800000"
    assert by_source["0906600000"]["analysis_psgc"] == "156600000"
    assert by_source["1804500000"]["analysis_psgc"] == "064500000"
    assert by_source["1804600000"]["analysis_psgc"] == "074600000"
    assert by_source["1806100000"]["analysis_psgc"] == "076100000"
    assert by_source[None]["analysis_psgc"] == "130000000"
    assert by_source[None]["is_virtual"] is True


def test_public_crosswalk_is_generated_and_matches_the_contract() -> None:
    committed = json.loads((PUBLIC_DATA / "geography-crosswalk.json").read_text())

    assert committed == build_geography_crosswalk()
    validate_crosswalk(committed)


def test_manifest_hashes_the_generated_crosswalk() -> None:
    manifest = json.loads((PUBLIC_DATA / "manifest.json").read_text())
    actual = hashlib.sha256((PUBLIC_DATA / "geography-crosswalk.json").read_bytes()).hexdigest()

    assert (
        manifest["file_bytes"]["geography-crosswalk.json"]
        == (PUBLIC_DATA / "geography-crosswalk.json").stat().st_size
    )
    assert manifest["sha256_per_file"]["geography-crosswalk.json"] == actual


def test_denominator_transforms_are_declared_for_hucs_and_virtual_ncr() -> None:
    crosswalk = build_geography_crosswalk()
    transforms = {item["id"]: item for item in crosswalk["denominator_transforms"]}

    assert transforms["whole_province_population_huc_rollup"]["operation"] == "sum"
    assert transforms["whole_province_population_huc_rollup"]["source_level"] == "province_and_huc"
    assert transforms["virtual_ncr_population"]["operation"] == "published_regional_total"
    assert "Cotabato City" in transforms["whole_province_population_huc_rollup"]["excluded_hucs"]


def test_crosswalk_rejects_an_invented_official_identity() -> None:
    crosswalk = build_geography_crosswalk()
    crosswalk["mappings"][0]["name"] = "Invented Province"

    with pytest.raises(ValueError, match="official PSGC fixture"):
        validate_crosswalk(crosswalk)


@pytest.mark.parametrize(
    "section", ["boundary_events", "denominator_transforms", "series_policies"]
)
def test_crosswalk_rejects_missing_required_contract_sections(section: str) -> None:
    crosswalk = deepcopy(build_geography_crosswalk())
    crosswalk[section] = []

    with pytest.raises(ValueError, match=section):
        validate_crosswalk(crosswalk)


def test_crosswalk_rejects_the_wrong_virtual_unit() -> None:
    crosswalk = deepcopy(build_geography_crosswalk())
    virtual = next(item for item in crosswalk["mappings"] if item["is_virtual"])
    virtual["analysis_psgc"] = "012800000"

    with pytest.raises(ValueError, match="virtual NCR"):
        validate_crosswalk(crosswalk)


def test_crosswalk_rejects_a_series_policy_without_cotabato_city_handling() -> None:
    crosswalk = deepcopy(build_geography_crosswalk())
    population = next(item for item in crosswalk["series_policies"] if item["id"] == "population")
    del population["cotabato_city"]

    with pytest.raises(ValueError, match="series_policies"):
        validate_crosswalk(crosswalk)


def test_series_policies_declare_supported_operations_and_prohibited_averages() -> None:
    policies = {item["id"]: item for item in build_geography_crosswalk()["series_policies"]}

    assert set(policies) == {"population", "poverty_fies", "procurement", "gdp_per_capita"}
    assert len(policies["population"]["huc_to_parent"]) == 18
    assert policies["population"]["cotabato_city"] == "exclude"
    assert policies["population"]["ncr"] == "published_regional_total"
    assert policies["procurement"]["split_maguindanao"] == "sum_additive_values"
    assert policies["poverty_fies"]["split_maguindanao"] == "omit_without_recomputation"
    assert policies["gdp_per_capita"]["split_maguindanao"] == "omit_without_recomputation"
    assert all(policy["prohibited_operation"] == "average" for policy in policies.values())


def test_gdp_loader_omits_split_maguindanao_nonadditive_rows(monkeypatch) -> None:
    from etl import psa_openstat
    from etl.psgc import normalize_name

    meta = {
        "variables": [
            {
                "code": "Geolocation",
                "values": ["north", "south"],
                "valueTexts": ["Maguindanao del Norte", "Maguindanao del Sur"],
            },
            {
                "code": "Type of Valuation",
                "values": ["constant"],
                "valueTexts": ["At Constant 2018 Prices"],
            },
            {"code": "Year", "values": ["2024"], "valueTexts": ["2024"]},
        ]
    }
    payload = {
        "data": [
            {"key": ["north", "constant", "2024"], "values": [100.0]},
            {"key": ["south", "constant", "2024"], "values": [200.0]},
        ]
    }
    monkeypatch.setattr(psa_openstat, "discover_table", lambda _name: ("gdp.px", {}))
    monkeypatch.setattr(
        psa_openstat,
        "_fetch_or_cache",
        lambda name, _fetch: meta if name.endswith("meta.json") else payload,
    )
    provinces = {"153800000": {"name": "Maguindanao"}}

    rows = psa_openstat.fetch_gdp_per_capita(provinces, normalize_name)

    assert rows == []


@pytest.mark.parametrize("series", [None, "unknown", "procuremnt"])
def test_split_maguindanao_rejects_missing_unknown_or_misspelled_series(series: str | None) -> None:
    from etl.psgc import normalize_name

    provinces = {"153800000": {"name": "Maguindanao"}}

    assert series_allows_split_mapping(series) is False
    assert normalize_name("Maguindanao del Norte", provinces, series=series) is None


@pytest.mark.parametrize("series", ["population", "procurement"])
def test_split_maguindanao_allows_only_declared_additive_series(series: str) -> None:
    from etl.psgc import normalize_name

    provinces = {"153800000": {"name": "Maguindanao"}}

    assert series_allows_split_mapping(series) is True
    assert normalize_name("Maguindanao del Norte", provinces, series=series) == "153800000"
