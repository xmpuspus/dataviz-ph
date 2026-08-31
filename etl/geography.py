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
ANALYSIS_AREA_COUNT = 82
ANALYSIS_GEOGRAPHY_VERSION = "dataviz-ph-81-provinces-plus-virtual-ncr-v1"
CURRENT_PSGC_VINTAGE = "PSA PSGC 2Q 2026 as of 2026-06-30"
HISTORICAL_GEOMETRY_VERSION = "2019-adm2-pre-2022-maguindanao"
NCR_CODE = "130000000"
MAGUINDANAO_CODE = "153800000"

# The 2026 PSGC restored NIR and reassigned Sulu to Region IX.  Entries not in
# this table retain their historical analysis-region prefix after its leading
# zero is expanded to PSA's current ten-digit syntax.
CURRENT_REGION_PREFIX = {
    "15": "19",  # BARMM
    "17": "17",  # MIMAROPA
}
CURRENT_SOURCE_OVERRIDES = {
    "064500000": "1804500000",  # Negros Occidental, NIR
    "074600000": "1804600000",  # Negros Oriental, NIR
    "076100000": "1806100000",  # Siquijor, NIR
    "156600000": "0906600000",  # Sulu, Region IX
}


def _analysis_provinces() -> dict[str, dict]:
    """Read the committed stable analysis units without changing their IDs."""
    return json.loads((PUBLIC_DATA / "provinces.json").read_text())


def _source_psgc(analysis_psgc: str) -> str:
    """Convert a legacy analysis province code to its current PSA identity."""
    if analysis_psgc in CURRENT_SOURCE_OVERRIDES:
        return CURRENT_SOURCE_OVERRIDES[analysis_psgc]
    prefix = CURRENT_REGION_PREFIX.get(analysis_psgc[:2], analysis_psgc[:2])
    return f"{prefix}0{analysis_psgc[2:4]}00000"


def current_province_records() -> list[dict]:
    """Return the reviewed 82-current-province PSA identity fixture.

    ``correspondence_code`` remains source-native: it is the legacy PSGC code
    shown by PSA where one exists.  The two 2022 Maguindanao provinces have no
    one-to-one correspondence code and are mapped explicitly below.
    """
    records = []
    for analysis_psgc, info in _analysis_provinces().items():
        if analysis_psgc in {NCR_CODE, MAGUINDANAO_CODE}:
            continue
        records.append(
            {
                "source_psgc": _source_psgc(analysis_psgc),
                "correspondence_code": analysis_psgc,
                "name": info["name"],
                "current_region_code": _source_psgc(analysis_psgc)[:2] + "00000000",
                "analysis_psgc": analysis_psgc,
            }
        )
    records.extend(
        [
            {
                "source_psgc": "1908700000",
                "correspondence_code": None,
                "name": "Maguindanao del Norte",
                "current_region_code": "1900000000",
                "analysis_psgc": MAGUINDANAO_CODE,
            },
            {
                "source_psgc": "1908800000",
                "correspondence_code": None,
                "name": "Maguindanao del Sur",
                "current_region_code": "1900000000",
                "analysis_psgc": MAGUINDANAO_CODE,
            },
        ]
    )
    return sorted(records, key=lambda item: item["source_psgc"])


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
    }


def validate_crosswalk(crosswalk: dict) -> None:
    """Raise when an identity, mapping, or analytical-area coverage contract drifts."""
    mappings = crosswalk["mappings"]
    sources = [item["source_psgc"] for item in mappings if item["source_psgc"]]
    if len(sources) != 82 or len(set(sources)) != 82:
        raise ValueError("crosswalk must contain each of the 82 current PSGC provinces once")
    analysis_psgcs = {item["analysis_psgc"] for item in mappings}
    if len(analysis_psgcs) != ANALYSIS_AREA_COUNT:
        raise ValueError("crosswalk must map to exactly 82 stable analysis areas")
    maguindanao = [item for item in mappings if item["analysis_psgc"] == MAGUINDANAO_CODE]
    if {item["source_psgc"] for item in maguindanao} != {"1908700000", "1908800000"}:
        raise ValueError("Maguindanao source provinces must declare their historical rollup")
    if sum(item["is_virtual"] for item in mappings) != 1:
        raise ValueError("crosswalk must declare exactly one virtual analysis area")


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
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    write_public_artifacts()
