"""Offline contract tests for PSA OpenStat source discovery."""

from __future__ import annotations

import pytest

from etl.source_catalog import (
    PSA_TABLES,
    SourceContractDriftError,
    resolve_reviewed_table,
    validate_table_metadata,
)


def _poverty_meta(*, title: str = "Table 1a: Poverty Incidence among Families") -> dict:
    return {
        "title": title,
        "variables": [
            {"code": "Geolocation", "values": ["01"], "valueTexts": ["Abra"]},
            {
                "code": "Threshold/Incidence/Parameters",
                "values": ["PI"],
                "valueTexts": ["Poverty Incidence among Families"],
            },
            {
                "code": "Year",
                "values": ["2018", "2021", "2023"],
                "valueTexts": ["2018", "2021", "2023"],
            },
        ],
    }


def test_catalog_selects_metadata_matching_discovered_table_over_reviewed_fallback():
    contract = PSA_TABLES["poverty"]
    metadata = {
        "1E/FY/obsolete.px": _poverty_meta(title="Table 1a: archived series"),
        "1E/FY/current.px": _poverty_meta(),
        contract.reviewed_fallbacks[0]: _poverty_meta(),
    }

    selected = resolve_reviewed_table(contract, metadata.keys(), metadata.__getitem__)

    assert selected == "1E/FY/current.px"


def test_catalog_uses_versioned_reviewed_fallback_when_discovery_has_no_compatible_table():
    contract = PSA_TABLES["poverty"]
    metadata = {contract.reviewed_fallbacks[0]: _poverty_meta()}

    selected = resolve_reviewed_table(contract, (), metadata.__getitem__)

    assert selected == contract.reviewed_fallbacks[0]


def test_catalog_fails_clearly_when_title_dimension_measure_or_years_drift():
    contract = PSA_TABLES["poverty"]
    metadata = _poverty_meta(title="Table 1b: Poverty gap")
    metadata["variables"][2]["values"] = ["2021", "2023"]

    with pytest.raises(SourceContractDriftError, match="title.*expected years"):
        validate_table_metadata(contract, metadata)
