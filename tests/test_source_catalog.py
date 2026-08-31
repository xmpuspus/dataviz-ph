"""Offline contract tests for PSA OpenStat source discovery."""

from __future__ import annotations

import pytest

from etl import psa_openstat
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
    metadata["variables"][2]["valueTexts"] = ["2021", "2023"]

    with pytest.raises(SourceContractDriftError, match="title.*expected years"):
        validate_table_metadata(contract, metadata)


@pytest.mark.parametrize("name", sorted(PSA_TABLES))
def test_every_catalog_entry_validates_its_reviewed_fallback(name):
    contract = PSA_TABLES[name]
    variables = [
        {"code": dimension, "values": [], "valueTexts": []} for dimension in contract.dimensions
    ]
    for variable in variables:
        if variable["code"] == "Year":
            variable["values"] = [str(year) for year in contract.expected_years]
        if variable["code"] == contract.dimensions[-1]:
            variable["valueTexts"] = list(contract.measure_terms)
    metadata = {
        "title": " ".join([*contract.title_terms, *contract.vintage_terms]),
        "variables": variables,
    }

    validate_table_metadata(contract, metadata)


def test_discovery_uses_validated_fallback_when_retired_directory_returns_404(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(psa_openstat, "CACHE_DIR", tmp_path)
    contract = PSA_TABLES["poverty"]

    def fake_get(url):
        if url.endswith(contract.directory):
            request = pytest.importorskip("httpx").Request("GET", url)
            response = pytest.importorskip("httpx").Response(404, request=request)
            raise pytest.importorskip("httpx").HTTPStatusError(
                "gone", request=request, response=response
            )
        return _poverty_meta()

    monkeypatch.setattr(psa_openstat, "_get_json", fake_get)

    path, metadata = psa_openstat.discover_table("poverty")

    assert path == contract.reviewed_fallbacks[0]
    assert metadata["title"].startswith("Table 1a")


def test_catalog_skips_a_stale_discovered_leaf_before_validated_fallback():
    contract = PSA_TABLES["poverty"]

    def metadata(path):
        if path == "stale.px":
            request = pytest.importorskip("httpx").Request("GET", "https://example.test/stale.px")
            response = pytest.importorskip("httpx").Response(404, request=request)
            raise pytest.importorskip("httpx").HTTPStatusError(
                "gone", request=request, response=response
            )
        return _poverty_meta()

    assert (
        resolve_reviewed_table(contract, ["stale.px"], metadata) == contract.reviewed_fallbacks[0]
    )


def test_gdp_contract_discovers_from_published_root_and_requires_2025():
    contract = PSA_TABLES["gdp_per_capita"]

    assert contract.directory == "2A/PPA"
    assert contract.expected_years[-1] == 2025
    assert contract.reviewed_fallbacks == ("2A/PPA/2025/0092A5FPPA8.px",)
