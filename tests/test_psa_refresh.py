"""Offline contracts for the 2024 population and 2025 PSA source refresh."""

from __future__ import annotations

import json
import math
from hashlib import sha256
from pathlib import Path

import pytest

from etl import psa_openstat
from etl.build import poverty_depth_coverage
from etl.geography import HUC_LABEL_ALIASES, HUC_TO_PARENT
from etl.psgc import huc_parent, load_provinces, normalize_name

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "psa"
CONTRACT = FIXTURE_DIR / "automated-refresh-contracts.json"
CONTENT = FIXTURE_DIR / "official-refresh-representative.json"
FULL_CONTENT = FIXTURE_DIR / "official-refresh-full-responses.json"


def _fixtures() -> tuple[dict, dict]:
    return json.loads(CONTRACT.read_text()), json.loads(CONTENT.read_text())


def _canonical_sha256(payload: object) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256(serialized.encode()).hexdigest()


def _load_table(monkeypatch, table: str, content: dict) -> list[dict]:
    source = content["tables"][table]
    queries: list[dict] = []

    def query_payload(_url: str, _prefix: str, _path: str, query: dict) -> dict:
        queries.append(query)
        return source["payload"]

    monkeypatch.setattr(psa_openstat, "discover_table", lambda _name: (source["path"], {}))
    monkeypatch.setattr(psa_openstat, "_fetch_or_cache", lambda *_args: source["metadata"])
    monkeypatch.setattr(psa_openstat, "_query_payload", query_payload)
    return queries


def _normalizer(name: str, _provinces: dict, series: str | None = None) -> str | None:
    direct = {
        "National Capital Region (NCR)": "130000000",
        "Isabela": "020310000",
        "Palawan": "170530000",
        "Maguindanao": "153800000",
    }
    if name in {"Maguindanao del Norte", "Maguindanao del Sur"}:
        return "153800000" if series in {"population", "gdp_recomputation"} else None
    return direct.get(name)


def _huc_parent(name: str) -> str | None:
    return {
        "City of Lapu-Lapu": "072200000",
        "City of General Santos": "126300000",
        "City of Isabela (Not a Province)": "150700000",
        "City of Cebu": "072200000",
    }.get(name)


def test_reviewed_source_fixtures_pin_all_eight_upstream_responses() -> None:
    contract, content = _fixtures()
    full = json.loads(FULL_CONTENT.read_text())
    expected = {
        "population_2024",
        "gdp_total",
        "gdp_industry",
        "gdp_per_capita",
        "poverty_poor_families",
        "poverty_income_gap",
        "poverty_poverty_gap",
        "poverty_severity",
    }

    assert expected <= set(contract)
    assert content["fetched_at"] == "2026-08-31T10:43:00+08:00"
    assert full["canonicalization"] == "JSON sorted keys, compact separators, UTF-8, SHA-256"
    for name in expected:
        source = full["tables"][name]
        assert _canonical_sha256(source["metadata"]) == contract[name]["metadata_sha256"]
        if "payload" in source:
            assert _canonical_sha256(source["payload"]) == contract[name]["payload_sha256"]
    for name in ("population_2024", "gdp_total", "gdp_per_capita", "gdp_industry"):
        source = content["tables"][name]
        assert (
            _canonical_sha256(source["metadata"])
            == contract[name]["representative_metadata_sha256"]
        )
        if "payload" in source:
            assert (
                _canonical_sha256(source["payload"])
                == contract[name]["representative_payload_sha256"]
            )
            selected_cells = math.prod(
                len(item["selection"]["values"]) for item in source["query"]["query"]
            )
            assert len(source["payload"]["data"]) == selected_cells
    depth = content["poverty_depth"]
    for name, source in depth["tables"].items():
        assert (
            _canonical_sha256(source["metadata"])
            == contract[name]["representative_metadata_sha256"]
        )
        assert (
            _canonical_sha256(source["payload"]) == contract[name]["representative_payload_sha256"]
        )
        selected_cells = math.prod(
            len(item["selection"]["values"]) for item in source["query"]["query"]
        )
        assert len(source["payload"]["data"]) == selected_cells


def test_population_fixture_uses_real_loader_and_prevents_regional_double_count(
    monkeypatch,
) -> None:
    _, content = _fixtures()
    queries = _load_table(monkeypatch, "population_2024", content)
    provinces = load_provinces()

    rows = psa_openstat.fetch_population_2024(provinces, normalize_name, huc_parent)

    values = {row["psgc"]: row["value"] for row in rows}
    assert values["023100000"] == 1_733_048
    assert values["072200000"] == 5_228_149
    assert values["126300000"] == 1_732_068
    assert values["130000000"] == 14_001_751
    assert values["150700000"] == 693_244
    assert values["153800000"] == 1_938_054
    assert sum(values.values()) < 112_729_484
    assert queries == [content["tables"]["population_2024"]["query"]]


def test_population_geography_contract_has_all_reviewed_huc_identities_once() -> None:
    expected = {
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
    assert expected == HUC_TO_PARENT
    assert all(huc_parent(label.title()) == parent for label, parent in expected.items())
    assert HUC_LABEL_ALIASES == {
        "city of lapu-lapu": "city of lapu-lapu (opon)",
        "city of general santos": "city of general santos (dadiangas)",
        "city of isabela (not a province)": "city of isabela",
    }
    assert huc_parent("City of Cotabato") is None


def test_population_fixture_rolls_every_reviewed_huc_once(monkeypatch) -> None:
    _, content = _fixtures()
    source = content["tables"]["population_2024"]
    queries = _load_table(monkeypatch, "population_2024", content)
    provinces = load_provinces()

    rows = psa_openstat.fetch_population_2024(provinces, normalize_name, huc_parent)
    by_psgc = {row["psgc"]: row["value"] for row in rows}
    labels = dict(
        zip(
            source["metadata"]["variables"][0]["values"],
            source["metadata"]["variables"][0]["valueTexts"],
            strict=True,
        )
    )
    source_values = {
        entry["key"][0]: int(entry["values"][0]) for entry in source["payload"]["data"]
    }
    reviewed_hucs = {
        code: huc_parent(psa_openstat._clean_geo_text(label))
        for code, label in labels.items()
        if huc_parent(psa_openstat._clean_geo_text(label))
    }

    assert len(reviewed_hucs) == 18
    for code, parent in reviewed_hucs.items():
        parent_source = sum(
            value
            for source_code, value in source_values.items()
            if normalize_name(
                psa_openstat._clean_geo_text(labels[source_code]), provinces, series="population"
            )
            == parent
        )
        huc_source = sum(
            value
            for source_code, value in source_values.items()
            if huc_parent(psa_openstat._clean_geo_text(labels[source_code])) == parent
        )
        assert by_psgc[parent] == parent_source + huc_source
        assert source_values[code] > 0
    assert queries == [source["query"]]


@pytest.mark.parametrize("table", ["gdp_total", "gdp_per_capita"])
def test_ppa_fixture_selects_constant_price_rows_and_complete_year_contract(
    monkeypatch, table: str
) -> None:
    _, content = _fixtures()
    queries = _load_table(monkeypatch, table, content)
    provinces = load_provinces()
    loader = (
        psa_openstat.fetch_gdp_total
        if table == "gdp_total"
        else psa_openstat.fetch_gdp_per_capita_source
    )

    rows = loader(provinces, normalize_name, huc_parent)

    assert {row["year"] for row in rows} == set(range(2018, 2026))
    assert {row["source_id"] for row in rows} == {"0", "83", "84", "132", "133"}
    if table == "gdp_total":
        assert math.isclose(rows[0]["value"], 5_814_440_130_224.75)
    else:
        assert math.isclose(rows[0]["value"], 432_181.459230047)
    years = content["tables"][table]["metadata"]["variables"][2]["valueTexts"]
    assert years == [str(year) for year in range(2018, 2026)]
    assert queries == [content["tables"][table]["query"]]


def test_ppa_recomputation_uses_source_implied_population_and_additive_components(
    monkeypatch,
) -> None:
    _, content = _fixtures()
    provinces = load_provinces()
    total_queries = _load_table(monkeypatch, "gdp_total", content)
    total = psa_openstat.fetch_gdp_total(provinces, normalize_name, huc_parent)
    per_capita_queries = _load_table(monkeypatch, "gdp_per_capita", content)
    per_capita = psa_openstat.fetch_gdp_per_capita_source(provinces, normalize_name, huc_parent)

    rows = psa_openstat.recompute_gdp_per_capita(total, per_capita)

    maguindanao = next(row for row in rows if row["psgc"] == "153800000" and row["year"] == 2025)
    assert maguindanao["value"] == pytest.approx(64_871.17504091287)
    assert total_queries == [content["tables"]["gdp_total"]["query"]]
    assert per_capita_queries == [content["tables"]["gdp_per_capita"]["query"]]


def test_ppa_recomputation_rejects_missing_extra_and_duplicate_source_pairs() -> None:
    gdp = [{"source_id": "a", "psgc": "012800000", "year": 2025, "value": 100.0}]
    with pytest.raises(ValueError, match="pairing mismatch"):
        psa_openstat.recompute_gdp_per_capita(gdp, [])
    with pytest.raises(ValueError, match="duplicate GDP source leaf"):
        psa_openstat.recompute_gdp_per_capita(gdp * 2, [{**gdp[0], "value": 10.0}])


def test_industry_fixture_checks_contract_without_summing_sectors(monkeypatch) -> None:
    _, content = _fixtures()
    full = json.loads(FULL_CONTENT.read_text())
    source = full["tables"]["gdp_industry"]
    monkeypatch.setattr(
        psa_openstat, "discover_table", lambda _name: (source["path"], source["metadata"])
    )

    contract = psa_openstat.validate_gdp_industry_contract()

    assert contract["path"] == "2A/PPA/0022A5FPPA1.px"
    assert contract["sectors"] == 16
    assert contract["unit"] == "thousand Philippine pesos"
    assert contract["decimals"] == 12
    assert contract["years"] == list(range(2018, 2026))
    assert content["tables"]["gdp_industry"]["suppression_markers"] == ["..", "...", "-", "/s"]


@pytest.mark.parametrize(
    ("table", "expected_kind", "expect_ncr"),
    [
        ("poverty_poor_families", "count", True),
        ("poverty_income_gap", "rate", True),
        ("poverty_poverty_gap", "rate", True),
        ("poverty_severity", "rate", False),
    ],
)
def test_poverty_depth_fixtures_use_real_loader_and_keep_safe_source_rows(
    monkeypatch, table: str, expected_kind: str, expect_ncr: bool
) -> None:
    _, content = _fixtures()
    depth = content["poverty_depth"]
    source = depth["tables"][table]
    monkeypatch.setattr(psa_openstat, "discover_table", lambda _name: (source["path"], {}))
    monkeypatch.setattr(psa_openstat, "_fetch_or_cache", lambda *_args: source["metadata"])
    queries: list[dict] = []

    def query_payload(_url: str, _prefix: str, _path: str, query: dict) -> dict:
        queries.append(query)
        return source["payload"]

    monkeypatch.setattr(psa_openstat, "_query_payload", query_payload)
    provinces = load_provinces()

    missing: list[dict] = []
    rows = psa_openstat.fetch_poverty_depth(table, provinces, normalize_name, missing)

    assert {row["kind"] for row in rows} == {expected_kind}
    assert {row["unit"] for row in rows} == {psa_openstat.POVERTY_DEPTH_MEASURES[table]["unit"]}
    keys = {(row["psgc"], row["year"]) for row in rows}
    assert {("153800000", 2023), ("175300000", 2023)} <= keys
    assert (("130000000", 2023) in keys) is expect_ncr
    assert all(row["psgc"] != "190870000" for row in rows)
    assert all("se" in row and "ci_lo" in row and "ci_hi" in row for row in rows)
    assert queries == [source["query"]]
    if table == "poverty_severity":
        assert any(row.get("source_small_sample_warning") for row in rows)
        assert any(item["reason"] == "source_marker_dash" for item in missing)


def test_poverty_coverage_keeps_specific_source_markers_and_revision_warnings() -> None:
    rows = [
        {
            "psgc": "012800000",
            "year": 2023,
            "value": 0.3,
            "measure": "poverty_severity",
            "source_revision_markers": ["r1"],
        }
    ]
    missing = [
        {
            "psgc": "130000000",
            "year": 2023,
            "measure": "poverty_severity",
            "reason": "source_marker_dash",
            "source_marker": "-",
            "source_small_sample_warning": True,
        }
    ]

    coverage = poverty_depth_coverage(
        rows,
        2,
        {"012800000", "130000000"},
        missing_evidence=missing,
        measures=("poverty_severity",),
        years=(2023,),
    )

    assert coverage[0]["missing_reasons"] == {"130000000": "source_marker_dash"}
    assert coverage[0]["source_warnings"]["130000000"] == ["source_small_sample_warning"]
    assert coverage[0]["revision_warnings"]["012800000"] == ["r1"]


def test_cache_identity_changes_with_query_and_source_years_reject_gaps() -> None:
    first = psa_openstat.cache_identity("ppa_gdp", "0012A5FPPA0.px", {"Year": ["2024"]})
    second = psa_openstat.cache_identity("ppa_gdp", "0012A5FPPA0.px", {"Year": ["2025"]})

    assert first != second
    with pytest.raises(ValueError, match="2025"):
        psa_openstat.require_source_years([{"year": 2024}], range(2018, 2026), "PPA")


def test_clean_geo_text_recovers_reviewed_palawan_label() -> None:
    assert psa_openstat._clean_geo_text("Palawan (w/o the City of Puerto Princesa") == "Palawan"
    assert psa_openstat._clean_geo_text("....Ilocos Norte **, 2/, 3/") == "Ilocos Norte"
