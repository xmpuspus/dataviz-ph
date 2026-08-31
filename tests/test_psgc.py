"""Behavioural tests for etl.psgc.normalize_name and friends.

Covers:
- ALIASES table maps common Cotabato/Compostela Valley/etc. correctly
- NCR shortcut returns the regional code (130000000) for every Metro Manila variant
- HUC_TO_PARENT maps known HUCs to their parent province PSGC
- SKIPPED_NAMES (Maguindanao del Norte/del Sur) return None
- Parenthetical aliases are stripped: 'Davao de Oro (Compostela Valley)' resolves
- Unknown / regional aggregate names return None instead of false-positive
"""

from etl.geography import build_geography_crosswalk
from etl.psgc import (
    ALIASES,
    HUC_TO_PARENT,
    NCR_CODE,
    SKIPPED_NAMES,
    huc_parent,
    normalize_name,
)


def _fake_provinces():
    """Minimal provinces dict for tests; real load_provinces hits the network."""
    return {
        NCR_CODE: {"name": "Metro Manila", "island_group": "ncr", "region_code": NCR_CODE},
        "072200000": {"name": "Cebu", "island_group": "visayas", "region_code": "070000000"},
        "098300000": {"name": "Cotabato", "island_group": "mindanao", "region_code": "120000000"},
        "112300000": {
            "name": "Davao de Oro",
            "island_group": "mindanao",
            "region_code": "110000000",
        },
        "150900000": {"name": "Maguindanao", "island_group": "barmm", "region_code": "150000000"},
        "144400000": {
            "name": "Mountain Province",
            "island_group": "luzon",
            "region_code": "140000000",
        },
        "087800000": {"name": "Samar", "island_group": "visayas", "region_code": "080000000"},
        "168500000": {
            "name": "Dinagat Islands",
            "island_group": "mindanao",
            "region_code": "160000000",
        },
    }


def test_ncr_shortcut_for_every_variant():
    provs = _fake_provinces()
    variants = [
        "Metro Manila",
        "NCR",
        "National Capital Region",
        "..NATIONAL CAPITAL REGION (NCR)",
    ]
    for raw in variants:
        assert normalize_name(raw, provs) == NCR_CODE, f"NCR variant {raw!r} failed"


def test_aliases_map_to_canonical_names():
    provs = _fake_provinces()
    # Cotabato variants
    assert normalize_name("North Cotabato", provs) == "098300000"
    assert normalize_name("Cotabato (North Cotabato)", provs) == "098300000"
    # Compostela Valley / Davao de Oro
    assert normalize_name("Compostela Valley", provs) == "112300000"
    assert normalize_name("Davao del Oro", provs) == "112300000"
    # Western Samar
    assert normalize_name("Western Samar", provs) == "087800000"
    # Mountain Province abbreviations
    assert normalize_name("Mt. Province", provs) == "144400000"
    assert normalize_name("Mt Province", provs) == "144400000"
    # Dinagat singular
    assert normalize_name("Dinagat Island", provs) == "168500000"


def test_skipped_names_return_none():
    provs = _fake_provinces()
    # Both Maguindanao splits must not silently overwrite the parent.
    assert normalize_name("Maguindanao del Norte", provs) is None
    assert normalize_name("Maguindanao del Sur", provs) is None
    # And the canonical parent still resolves
    assert normalize_name("Maguindanao", provs) == "150900000"


def test_huc_parent_maps_known_hucs():
    """huc_parent() should return the parent province PSGC for every entry in HUC_TO_PARENT."""
    for huc_lower, parent in HUC_TO_PARENT.items():
        assert huc_parent(huc_lower) == parent
        # Case-insensitivity
        assert huc_parent(huc_lower.upper()) == parent
        assert huc_parent(huc_lower.title()) == parent
    # Unknown name returns None
    assert huc_parent("city of nowhere") is None
    assert huc_parent("") is None
    assert huc_parent(None) is None


def test_parenthetical_aliases_are_stripped():
    provs = _fake_provinces()
    # 'Davao de Oro (Compostela Valley)' should resolve via paren strip + alias.
    assert normalize_name("Davao de Oro (Compostela Valley)", provs) == "112300000"


def test_unknown_names_return_none():
    """Regional aggregates and typos must return None, not a false match."""
    provs = _fake_provinces()
    assert normalize_name("Ilocos Region", provs) is None
    assert normalize_name("Cagayan Valley", provs) is None
    assert normalize_name("Region IV-A (CALABARZON)", provs) is None
    assert normalize_name("", provs) is None
    assert normalize_name("   ", provs) is None
    assert normalize_name(None, provs) is None  # type: ignore[arg-type]


def test_skipped_names_set_is_lowercased():
    """SKIPPED_NAMES entries must be lower-case for the normalize_name check to fire."""
    for name in SKIPPED_NAMES:
        assert name == name.lower(), f"{name!r} must be lowercase in SKIPPED_NAMES"


def test_aliases_keys_are_lowercased():
    """ALIASES keys must be lower-case since normalize_name lower-cases input."""
    for key in ALIASES:
        assert key == key.lower(), f"{key!r} must be lowercase in ALIASES"


def test_current_split_maguindanao_names_map_to_declared_historical_analysis_unit():
    """Current PSA identities do not create new historical analysis rows."""
    provs = _fake_provinces()
    provs["153800000"] = {
        "name": "Maguindanao",
        "island_group": "barmm",
        "region_code": "1900000000",
    }

    assert normalize_name("Maguindanao del Norte", provs) == "153800000"
    assert normalize_name("Maguindanao del Sur", provs) == "153800000"
    maguindanao = [
        item
        for item in build_geography_crosswalk()["mappings"]
        if item["analysis_psgc"] == "153800000"
    ]
    assert {item["source_psgc"] for item in maguindanao} == {"1908700000", "1908800000"}


def test_load_provinces_uses_stable_crosswalk_instead_of_the_mutable_mirror(monkeypatch):
    monkeypatch.setattr(
        "etl.psgc._fetch_provinces_raw",
        lambda: (_ for _ in ()).throw(AssertionError("legacy mirror must not define identities")),
    )

    provinces = __import__("etl.psgc", fromlist=["load_provinces"]).load_provinces()

    assert len(provinces) == 82
    assert provinces["153800000"]["source_psgcs"] == ["1908700000", "1908800000"]
    assert provinces[NCR_CODE]["geography_role"] == "virtual_ncr"
