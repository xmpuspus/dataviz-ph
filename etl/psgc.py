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

# Names that should NOT be mapped — they belong to provinces that don't exist
# as separate units in our 82-unit panel. Returning None keeps them out of the
# data without silently overwriting the rolled-up parent's value.
# Maguindanao split into del Norte / del Sur in 2022 but gitlab.io still shows
# the unified "Maguindanao", so the split rows would overwrite the parent's value.
SKIPPED_NAMES = {
    "maguindanao del norte",
    "maguindanao del sur",
}

# Highly Urbanized Cities outside NCR. PSA publishes province poverty rates and
# populations "without" these HUCs (e.g. "Cebu (w/o the City of Cebu, ...)").
# For per-capita spend to reflect the whole geographic province (which is what
# PhilGEPS area_of_delivery covers), we sum HUC populations back into the parent.
# NCR HUCs are intentionally absent here: NCR is already aggregated as one bubble
# via the regional row, so its 16 cities must not be double-counted.
HUC_TO_PARENT: dict[str, str] = {
    # Visayas
    "city of cebu": "072200000",  # Cebu
    "city of lapu-lapu (opon)": "072200000",
    "city of mandaue": "072200000",
    "city of iloilo": "063000000",  # Iloilo
    "city of bacolod": "064500000",  # Negros Occidental
    "city of tacloban": "083700000",  # Leyte
    # Luzon
    "city of angeles": "035400000",  # Pampanga
    "city of olongapo": "037100000",  # Zambales
    "city of lucena": "045600000",  # Quezon
    "city of puerto princesa": "175300000",  # Palawan
    "city of baguio": "141100000",  # Benguet
    # Mindanao
    "city of davao": "112400000",  # Davao Del Sur
    "city of general santos (dadiangas)": "126300000",  # South Cotabato
    "city of zamboanga": "097300000",  # Zamboanga del Sur
    "city of cagayan de oro": "104300000",  # Misamis Oriental
    "city of iligan": "103500000",  # Lanao Del Norte
    "city of butuan": "160200000",  # Agusan Del Norte
    # BARMM / special
    "city of isabela": "150700000",  # Basilan
    # Note: Cotabato City sits inside Maguindanao geographically but is an
    # independent component city assigned to BARMM. Excluded here to avoid
    # mis-attributing its contracts; documented as one of the unattributable HUCs.
}


def huc_parent(raw_name: str) -> str | None:
    """If the raw cleaned name is a known HUC, return its parent province PSGC."""
    if not isinstance(raw_name, str):
        return None
    s = raw_name.strip().lower()
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
    """Return {psgc_code: {name, island_group, region_code}} for 82 units.

    island_group is one of: luzon, visayas, mindanao, ncr, barmm.
    Population is NOT included here; the build orchestrator enriches it from PSA.
    """
    raw = _fetch_provinces_raw()
    out: dict[str, dict] = {}
    for p in raw:
        out[p["code"]] = {
            "name": _normalize_capitalization(p["name"]),
            "island_group": _island_group(p),
            "region_code": p["regionCode"],
        }
    out[NCR_CODE] = {
        "name": "Metro Manila",
        "island_group": "ncr",
        "region_code": NCR_CODE,
    }
    return out


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
