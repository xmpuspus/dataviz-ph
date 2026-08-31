"""Versioned bridge from current PSA PSGC identities to historical chart areas.

The public chart deliberately keeps the historical 81 province units plus a
virtual Metro Manila.  PSA's 2Q 2026 PSGC has 82 current provinces because it
lists Maguindanao del Norte and Maguindanao del Sur separately.  This module is
the only place that translates between those two geography contracts.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

PUBLIC_DATA = Path(__file__).resolve().parent.parent / "public" / "data"
OFFICIAL_PSGC_2Q_2026_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "psgc_2q_2026_provinces.json"
)
ANALYSIS_AREA_COUNT = 82
ANALYSIS_GEOGRAPHY_VERSION = "dataviz-ph-81-provinces-plus-virtual-ncr-v1"
CURRENT_PSGC_VINTAGE = "PSA PSGC 2Q 2026 as of 2026-06-30"
HISTORICAL_GEOMETRY_VERSION = "2019-adm2-pre-2022-maguindanao"
NCR_CODE = "130000000"
MAGUINDANAO_CODE = "153800000"

# The 2026 PSGC restored NIR and reassigned Sulu to Region IX.  Entries not in
# this table retain their historical analysis-region prefix after its leading
# zero is expanded to PSA's current ten-digit syntax.
HUC_TO_PARENT: dict[str, str] = {
    "city of cebu": "072200000",
    "city of lapu-lapu (opon)": "072200000",
    "city of mandaue": "072200000",
    "city of iloilo": "063000000",
    "city of bacolod": "064500000",
    "city of tacloban": "083700000",
    "city of angeles": "035400000",
    "city of olongapo": "037100000",
    "city of lucena": "045600000",
    "city of puerto princesa": "175300000",
    "city of baguio": "141100000",
    "city of davao": "112400000",
    "city of general santos (dadiangas)": "126300000",
    "city of zamboanga": "097300000",
    "city of cagayan de oro": "104300000",
    "city of iligan": "103500000",
    "city of butuan": "160200000",
    "city of isabela": "150700000",
}
HUC_LABEL_ALIASES = {
    "city of lapu-lapu": "city of lapu-lapu (opon)",
    "city of general santos": "city of general santos (dadiangas)",
    "city of isabela (not a province)": "city of isabela",
}
ADDITIVE_SPLIT_SERIES = {"population", "procurement", "gdp_recomputation"}


def _analysis_provinces() -> dict[str, dict]:
    """Read the committed stable analysis units without changing their IDs."""
    return json.loads((PUBLIC_DATA / "provinces.json").read_text())


def current_province_records() -> list[dict]:
    """Read the reviewed official 82-row PSA identity fixture without derivation."""
    return json.loads(OFFICIAL_PSGC_2Q_2026_FIXTURE.read_text())


def build_geography_crosswalk() -> dict:
    """Build the machine-readable public contract deterministically."""
    analysis = _analysis_provinces()
    mappings = [
        {
            **record,
            "analysis_name": analysis[record["analysis_psgc"]]["name"],
            "mapping": "many_to_one"
            if record["analysis_psgc"] == MAGUINDANAO_CODE
            else "one_to_one",
            "is_virtual": False,
        }
        for record in current_province_records()
    ]
    mappings.append(
        {
            "source_psgc": None,
            "correspondence_code": "130000000",
            "name": "National Capital Region",
            "current_region_code": "1300000000",
            "analysis_psgc": NCR_CODE,
            "analysis_name": "Metro Manila",
            "mapping": "virtual_aggregate",
            "is_virtual": True,
        }
    )
    return {
        "analysis_geography_version": ANALYSIS_GEOGRAPHY_VERSION,
        "current_psgc_vintage": CURRENT_PSGC_VINTAGE,
        "historical_geometry_version": HISTORICAL_GEOMETRY_VERSION,
        "source": {
            "publisher": "Philippine Statistics Authority",
            "url": "https://psa.gov.ph/classification/psgc/provinces",
            "as_of": "2026-06-30",
            "release": "Second Quarter 2026 PSGC",
        },
        "mappings": sorted(
            mappings, key=lambda item: (item["is_virtual"], item["source_psgc"] or "")
        ),
        "boundary_events": [
            {
                "event": "Maguindanao split",
                "current_source_psgcs": ["1908700000", "1908800000"],
                "analysis_psgc": MAGUINDANAO_CODE,
                "treatment": "many_to_one_historical_rollup",
            },
            {
                "event": "Sulu transfer to Region IX",
                "current_source_psgc": "0906600000",
                "analysis_psgc": "156600000",
                "treatment": "preserve_historical_analysis_id",
            },
            {
                "event": "Negros Island Region restored",
                "current_source_psgcs": ["1804500000", "1804600000", "1806100000"],
                "analysis_psgcs": ["064500000", "074600000", "076100000"],
                "treatment": "preserve_historical_analysis_ids",
            },
            {
                "event": "Metro Manila virtual aggregate",
                "analysis_psgc": NCR_CODE,
                "treatment": "regional_total_not_a_current_province",
            },
        ],
        "denominator_transforms": [
            {
                "id": "whole_province_population_huc_rollup",
                "operation": "sum",
                "source_level": "province_and_huc",
                "target_level": "historical_analysis_province",
                "excluded_hucs": ["Cotabato City"],
                "reason": (
                    "whole-province per-capita denominator; Cotabato City remains unattributed"
                ),
            },
            {
                "id": "virtual_ncr_population",
                "operation": "published_regional_total",
                "source_level": "NCR regional aggregate",
                "target_level": "virtual Metro Manila analysis area",
            },
        ],
        "series_policies": [
            {
                "id": "population",
                "source_level": "province_and_huc",
                "huc_to_parent": HUC_TO_PARENT,
                "city_of_isabela": "include_in_basilan",
                "cotabato_city": "exclude",
                "ncr": "published_regional_total",
                "split_maguindanao": "sum_additive_values",
                "allowed_operation": "sum",
                "recomputation": "not_required_for_additive_values",
                "prohibited_operation": "average",
            },
            {
                "id": "poverty_fies",
                "source_level": "published_province_rate",
                "huc_to_parent": HUC_TO_PARENT,
                "city_of_isabela": "no_huc_rollup",
                "cotabato_city": "no_huc_rollup",
                "ncr": "published_regional_rate",
                "split_maguindanao": "omit_without_recomputation",
                "allowed_operation": "recompute_from_additive_numerators_and_denominators",
                "recomputation": "required_before_historical_rollup",
                "prohibited_operation": "average",
            },
            {
                "id": "procurement",
                "source_level": "award_value",
                "huc_to_parent": HUC_TO_PARENT,
                "city_of_isabela": "source_name_not_attributed_as_huc",
                "cotabato_city": "source_name_not_attributed_as_huc",
                "ncr": "metro_manila_awards_aggregate",
                "split_maguindanao": "sum_additive_values",
                "allowed_operation": "sum",
                "recomputation": "divide_additive_awards_by_selected_population",
                "prohibited_operation": "average",
            },
            {
                "id": "gdp_per_capita",
                "source_level": "published_per_capita_value",
                "huc_to_parent": HUC_TO_PARENT,
                "city_of_isabela": "no_huc_rollup",
                "cotabato_city": "no_huc_rollup",
                "ncr": "published_regional_per_capita_value",
                "split_maguindanao": "omit_without_recomputation",
                "allowed_operation": "recompute_from_additive_gdp_and_population",
                "component_series": "gdp_recomputation",
                "recomputation": "required_before_historical_rollup",
                "prohibited_operation": "average",
            },
        ],
    }


def validate_crosswalk(crosswalk: dict) -> None:
    """Raise when an identity, mapping, or analytical-area coverage contract drifts."""
    mappings = crosswalk["mappings"]
    expected_records = current_province_records()
    expected_by_source = {item["source_psgc"]: item for item in expected_records}
    sources = [item["source_psgc"] for item in mappings if item["source_psgc"]]
    if len(sources) != 82 or len(set(sources)) != 82:
        raise ValueError("crosswalk must contain each of the 82 current PSGC provinces once")
    virtual = [item for item in mappings if item["is_virtual"]]
    if len(virtual) != 1 or virtual[0]["analysis_psgc"] != NCR_CODE:
        raise ValueError("crosswalk must declare virtual NCR as its only virtual analysis area")
    analysis_psgcs = {item["analysis_psgc"] for item in mappings}
    if len(analysis_psgcs) != ANALYSIS_AREA_COUNT:
        raise ValueError("crosswalk must map to exactly 82 stable analysis areas")
    actual_by_source = {item["source_psgc"]: item for item in mappings if item["source_psgc"]}
    if set(actual_by_source) != set(expected_by_source):
        raise ValueError("crosswalk source IDs must equal the official PSGC fixture")
    for source_psgc, expected in expected_by_source.items():
        actual = actual_by_source[source_psgc]
        for field in ("correspondence_code", "name", "current_region_code", "analysis_psgc"):
            if actual.get(field) != expected[field]:
                raise ValueError("crosswalk identities must equal the official PSGC fixture")
    maguindanao = [item for item in mappings if item["analysis_psgc"] == MAGUINDANAO_CODE]
    if {item["source_psgc"] for item in maguindanao} != {"1908700000", "1908800000"}:
        raise ValueError("Maguindanao source provinces must declare their historical rollup")
    required_events = {
        "Maguindanao split",
        "Sulu transfer to Region IX",
        "Negros Island Region restored",
        "Metro Manila virtual aggregate",
    }
    if {event.get("event") for event in crosswalk.get("boundary_events", [])} != required_events:
        raise ValueError("boundary_events must declare every required boundary event")
    transforms = {item.get("id") for item in crosswalk.get("denominator_transforms", [])}
    if transforms != {"whole_province_population_huc_rollup", "virtual_ncr_population"}:
        raise ValueError("denominator_transforms must declare every required transform")
    policies = {item.get("id"): item for item in crosswalk.get("series_policies", [])}
    if set(policies) != {"population", "poverty_fies", "procurement", "gdp_per_capita"}:
        raise ValueError("series_policies must declare every required series policy")
    for policy in policies.values():
        required_policy_fields = {
            "huc_to_parent",
            "city_of_isabela",
            "cotabato_city",
            "ncr",
            "split_maguindanao",
            "allowed_operation",
            "recomputation",
            "prohibited_operation",
        }
        if (
            not required_policy_fields <= set(policy)
            or policy.get("cotabato_city")
            not in {"exclude", "no_huc_rollup", "source_name_not_attributed_as_huc"}
            or policy.get("huc_to_parent") != HUC_TO_PARENT
            or policy.get("prohibited_operation") != "average"
            or (
                policy["id"] == "gdp_per_capita"
                and policy.get("component_series") != "gdp_recomputation"
            )
        ):
            raise ValueError("series_policies must retain the declared HUC and averaging rules")


def series_allows_split_mapping(series: str | None) -> bool:
    """Return whether the series can sum current Maguindanao source components."""
    return series in ADDITIVE_SPLIT_SERIES


def enrich_analysis_provinces(provinces: dict | None = None) -> dict:
    """Add public source-native identifiers while preserving app-compatible keys."""
    out = deepcopy(provinces if provinces is not None else _analysis_provinces())
    by_analysis: dict[str, list[dict]] = {}
    for record in current_province_records():
        by_analysis.setdefault(record["analysis_psgc"], []).append(record)
    for analysis_psgc, info in out.items():
        if analysis_psgc == NCR_CODE:
            info["source_psgcs"] = []
            info["correspondence_codes"] = [NCR_CODE]
            info["geography_role"] = "virtual_ncr"
            continue
        records = by_analysis[analysis_psgc]
        info["source_psgcs"] = [record["source_psgc"] for record in records]
        info["correspondence_codes"] = [
            record["correspondence_code"]
            for record in records
            if record["correspondence_code"] is not None
        ]
        info["geography_role"] = "historical_analysis_unit"
    return out


def write_public_artifacts() -> None:
    """Write only public geography artifacts from this contract."""
    crosswalk = build_geography_crosswalk()
    validate_crosswalk(crosswalk)
    (PUBLIC_DATA / "geography-crosswalk.json").write_text(
        json.dumps(crosswalk, ensure_ascii=False, indent=2) + "\n"
    )
    provinces = enrich_analysis_provinces()
    (PUBLIC_DATA / "provinces.json").write_text(json.dumps(provinces, ensure_ascii=False) + "\n")
    geojson_path = PUBLIC_DATA / "ph-provinces.geojson"
    geojson = json.loads(geojson_path.read_text())
    geojson.setdefault("_meta", {})["historical_geometry_version"] = HISTORICAL_GEOMETRY_VERSION
    geojson["_meta"]["analysis_geography_version"] = ANALYSIS_GEOGRAPHY_VERSION
    geojson_path.write_text(json.dumps(geojson, ensure_ascii=False, separators=(",", ":")))

    manifest_path = PUBLIC_DATA / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["source_vintages"]["psgc"] = CURRENT_PSGC_VINTAGE
    manifest["inputs"]["psgc"] = {
        "source": "https://psa.gov.ph/classification/psgc/provinces",
        "release": "Second Quarter 2026 PSGC",
        "as_of": "2026-06-30",
        "note": (
            "Official identity source; historical analysis IDs remain in geography-crosswalk.json."
        ),
    }
    for path in [PUBLIC_DATA / "geography-crosswalk.json", PUBLIC_DATA / "provinces.json"]:
        manifest["file_bytes"][path.name] = path.stat().st_size
        manifest["sha256_per_file"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    write_public_artifacts()
