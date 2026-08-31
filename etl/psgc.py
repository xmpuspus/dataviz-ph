"""Province name resolver. Pulls the canonical 82-entry province list (81 provinces + NCR).

Source: psgc.gitlab.io/api, the community mirror of PSA's PSGC dataset.

Handles BARMM (regionCode 190000000) by overriding island_group from 'mindanao' to 'barmm'
so the frontend can color it distinctly. NCR is added as a virtual 82nd unit since PhilGEPS
area_of_delivery uses 'Metro Manila' as a single label there.

normalize_name() folds common aliases (Cotabato/North Cotabato, Davao De Oro/Compostela Valley)
and the Metro Manila label so PhilGEPS area_of_delivery strings join cleanly.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from etl.geography import (
    HUC_LABEL_ALIASES,
    HUC_TO_PARENT,
    MAGUINDANAO_CODE,
    enrich_analysis_provinces,
    series_allows_split_mapping,
)
from etl.psa_openstat import _RETRY  # reuse the same retry policy

PSGC_BASE = "https://psgc.gitlab.io/api"
CACHE_DIR = Path(__file__).resolve().parent.parent / ".etl_cache" / "psgc"
NCR_CODE = "130000000"  # PSGC region code for NCR; reused as virtual province key
BARMM_REGION_CODE = "150000000"

# Aliases: lower-cased raw -> canonical name (the `name` returned by gitlab.io provinces.json)
ALIASES: dict[str, str] = {
    "metro manila": "Metro Manila",
    "ncr": "Metro Manila",
    "national capital region": "Metro Manila",
    "cotabato (north cotabato)": "Cotabato",
    "north cotabato": "Cotabato",
    "compostela valley": "Davao de Oro",
    "davao del oro": "Davao de Oro",
    "shariff kabunsuan": "Maguindanao",  # defunct province absorbed back into Maguindanao 2008
    "maguindanao del norte": "Maguindanao",  # 2022 split; gitlab.io still shows single Maguindanao
    "maguindanao del sur": "Maguindanao",
    "western samar": "Samar",
    "samar (western samar)": "Samar",
    "mt. province": "Mountain Province",
    "mt province": "Mountain Province",
    "dinagat island": "Dinagat Islands",  # PhilGEPS uses singular
}

# Names that should NOT be mapped. They belong to provinces that don't exist
# as separate units in our 82-unit panel. Returning None keeps them out of the
# data without silently overwriting the rolled-up parent's value.
# Maguindanao split into del Norte / del Sur in 2022 but gitlab.io still shows
# the unified "Maguindanao", so the split rows would overwrite the parent's value.
SKIPPED_NAMES = {
    "maguindanao del norte",
    "maguindanao del sur",
}


def huc_parent(raw_name: str) -> str | None:
    """If the raw cleaned name is a known HUC, return its parent province PSGC."""
    if not isinstance(raw_name, str):
        return None
    s = raw_name.strip().lower()
    s = HUC_LABEL_ALIASES.get(s, s)
    return HUC_TO_PARENT.get(s)


def _cache_path(name: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / name


def clear_cache() -> None:
    """Delete the cached PSGC payload. Used by `etl.build --no-cache`."""
    if not CACHE_DIR.exists():
        return
    for f in CACHE_DIR.glob("*.json"):
        f.unlink()


@_RETRY
def _http_get(url: str) -> object:
    with httpx.Client(timeout=30.0) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.json()


def _fetch_provinces_raw() -> list[dict]:
    cache = _cache_path("provinces.json")
    if cache.exists():
        return json.loads(cache.read_text())
    data = _http_get(f"{PSGC_BASE}/provinces.json")
    cache.write_text(json.dumps(data))
    return data


def _island_group(province: dict) -> str:
    if province["regionCode"] == BARMM_REGION_CODE:
        return "barmm"
    return province["islandGroupCode"]  # luzon / visayas / mindanao


_LOWERCASE_TOKENS = {"de", "del", "ng", "y", "de la", "de los", "of", "the"}


def _normalize_capitalization(name: str) -> str:
    """Apply PSA convention: 'Davao Del Sur' -> 'Davao del Sur', 'Davao De Oro' -> 'Davao de Oro'.

    Lowercases connector words ('del', 'de', 'ng') except when leading. Leaves
    everything else untouched.
    """
    if not name:
        return name
    parts = name.split(" ")
    out = [parts[0]]
    for tok in parts[1:]:
        out.append(tok.lower() if tok.lower() in _LOWERCASE_TOKENS else tok)
    return " ".join(out)


def load_provinces() -> dict[str, dict]:
    """Return the stable 82 analysis units backed by the PSGC crosswalk.

    island_group is one of: luzon, visayas, mindanao, ncr, barmm.
    Source-native current PSGC identifiers are retained alongside the legacy
    analysis IDs; the build orchestrator replaces any committed population
    value with the selected population-source denominator.
    """
    return enrich_analysis_provinces()


def _name_to_code_index(provinces: dict[str, dict]) -> dict[str, str]:
    """Lower-cased canonical name -> psgc code."""
    return {info["name"].lower(): code for code, info in provinces.items()}


def _strip_parens(s: str) -> str:
    """Drop parenthesized content so 'Davao de Oro (Compostela Valley)' -> 'Davao de Oro'."""
    import re

    return re.sub(r"\s*\([^)]*\)", "", s).strip()


def normalize_name(
    raw: str,
    provinces: dict[str, dict] | None = None,
    year: int | None = None,
    series: str | None = None,
) -> str | None:
    """Map a raw area string to a PSGC code, or None if unmappable.

    Strips parenthesized aliases (Davao de Oro (Compostela Valley)) and matches NCR
    via 'national capital region' / 'ncr' / 'metro manila'. Returns None for any
    other regional aggregate row (Ilocos Region, Cagayan Valley) since those don't
    map to a single province.

    year is accepted for future boundary-shift handling (BARMM 2019, NIR 2015-2017)
    but the current implementation does not branch on it.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    if provinces is None:
        provinces = load_provinces()
    s = _strip_parens(raw.strip()).lower()
    if not s:
        return None
    if s in {"maguindanao del norte", "maguindanao del sur"}:
        # Current PSA PSGC has two source provinces, but the historical chart
        # retains a single pre-2022 analytical Maguindanao unit. Nonadditive
        # series must recompute from additive inputs or omit these rows.
        if not series_allows_split_mapping(series):
            return None
        return MAGUINDANAO_CODE if MAGUINDANAO_CODE in provinces else None
    if s in SKIPPED_NAMES:
        return None
    # NCR shortcut: matches '..NATIONAL CAPITAL REGION (NCR)' regional row after cleaning
    if "national capital region" in s or s == "ncr" or s == "metro manila":
        return NCR_CODE
    if s in ALIASES:
        s = ALIASES[s].lower()
    name_idx = _name_to_code_index(provinces)
    if s in name_idx:
        return name_idx[s]
    return None
