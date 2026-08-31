"""Contract tests for the stable dataviz.ph analytical geography."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from etl.geography import (
    ANALYSIS_AREA_COUNT,
    ANALYSIS_GEOGRAPHY_VERSION,
    CURRENT_PSGC_VINTAGE,
    HISTORICAL_GEOMETRY_VERSION,
    build_geography_crosswalk,
    current_province_records,
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
