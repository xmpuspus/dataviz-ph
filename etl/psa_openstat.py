"""PSA OpenStat PXWeb client. JSON-stat (format:'json') in, tidy long-form rows out.

Landmines (verified 2026-05-18 in ph-civic-data-mcp):
- POST with response={'format': 'json'} (NOT 'json-stat2').
- '..' / '...' / '-' are missing sentinels; guard every float cast.
- filter:'all' is WAF-403'd. Always use explicit item values.
- Hardcoding stable subject prefix is OK; .px leaf is the brittle part.

Tables used:
- Poverty: 1E/FY/0021E3DF01A.px  (Table 1a: province + HUC, 142 geolocations, 2018/2021/2023)
- Population: 1A/PO/0011A6DPHH0.px (2020 Census, 135 geolocations, single snapshot)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

API_BASE = "https://openstat.psa.gov.ph/PXWeb/api/v1/en/DB"
CACHE_DIR = Path(__file__).resolve().parent.parent / ".etl_cache" / "psa"

POVERTY_PATH = "1E/FY/0021E3DF01A.px"
POPULATION_PATH = "1A/PO/0011A6DPHH0.px"
GDP_PER_CAPITA_PATH = "2A/PPA/2025/0092A5GPPA8.px"
CPI_PATH = "2M/PI/CPI/2018NEW/0012M4ACP22.px"

MISSING_SENTINELS = {"..", "...", "-", "", None}


def _cache_path(name: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / name


def _get_json(url: str) -> dict | list:
    with httpx.Client(timeout=30.0) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.json()


def _post_json(url: str, query: dict) -> dict:
    with httpx.Client(timeout=60.0) as client:
        r = client.post(url, json=query)
        r.raise_for_status()
        return r.json()


def _to_float(raw: object) -> float | None:
    if raw in MISSING_SENTINELS:
        return None
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _clean_geo_text(text: str) -> str:
    """Strip leading dots, trailing footnote refs, and asterisks from PSA geo labels."""
    s = text.lstrip(".").strip()
    # Iteratively peel trailing footnotes: '/a', '1/', '2/', 'r1', etc.
    # Examples seen: 'Sulu r1, 1/, 2/, 3/, c/', 'Cebu /a', 'Tawi-tawi 1/, 3/, b/'.
    while True:
        prev = s
        s = re.sub(r"\s*[a-z]+\d+\s*[,]?\s*$", "", s, flags=re.IGNORECASE)  # r1, r2
        s = re.sub(r"\s*\d+/\s*[,]?\s*$", "", s)  # 1/, 2/
        s = re.sub(r"\s*/[a-z]\s*[,]?\s*$", "", s, flags=re.IGNORECASE)  # /a, /b
        s = re.sub(r"\s*[a-z]/\s*[,]?\s*$", "", s, flags=re.IGNORECASE)  # a/, b/
        s = re.sub(r"\s*\*+\s*$", "", s)  # trailing asterisks
        if s == prev:
            break
    return s.strip(", ").strip()


def _fetch_or_cache(name: str, fetch: callable) -> dict:
    cache = _cache_path(name)
    if cache.exists():
        return json.loads(cache.read_text())
    payload = fetch()
    cache.write_text(json.dumps(payload))
    return payload


def fetch_poverty(provinces: dict, normalize_name) -> list[dict]:
    """Poverty incidence among families (%), province-level, 2018/2021/2023.

    normalize_name is injected from etl.psgc to keep this module focused.
    Returns rows: [{psgc, year, value}].
    """
    url = f"{API_BASE}/{POVERTY_PATH}"
    meta = _fetch_or_cache("poverty_meta.json", lambda: _get_json(url))

    geo_var = next(v for v in meta["variables"] if v.get("code") == "Geolocation")
    measure_var = next(
        v for v in meta["variables"] if "Threshold" in (v.get("code") or "")
    )
    year_var = next(v for v in meta["variables"] if v.get("code") == "Year")

    # Pick the "Poverty Incidence among Families (%)" measure (index 1 in our probe).
    incidence_val = None
    for val, txt in zip(measure_var["values"], measure_var["valueTexts"], strict=False):
        if "poverty incidence" in txt.lower() and "famil" in txt.lower():
            incidence_val = val
            break
    if incidence_val is None:
        raise RuntimeError(
            "Could not locate 'Poverty Incidence among Families (%)' in PSA table 1a"
        )

    query = {
        "query": [
            {
                "code": "Geolocation",
                "selection": {"filter": "item", "values": geo_var["values"]},
            },
            {
                "code": measure_var["code"],
                "selection": {"filter": "item", "values": [incidence_val]},
            },
            {
                "code": "Year",
                "selection": {"filter": "item", "values": year_var["values"]},
            },
        ],
        "response": {"format": "json"},
    }
    payload = _fetch_or_cache("poverty_data.json", lambda: _post_json(url, query))

    # Build geo_code -> label map so we can resolve from row key.
    geo_label = dict(zip(geo_var["values"], geo_var["valueTexts"], strict=False))
    year_label = dict(zip(year_var["values"], year_var["valueTexts"], strict=False))

    rows: list[dict] = []
    for entry in payload.get("data", []):
        key = entry.get("key", [])
        if len(key) < 3:
            continue
        geo_code, _measure_code, year_code = key[0], key[1], key[2]
        raw_label = geo_label.get(geo_code, "")
        clean = _clean_geo_text(raw_label)
        psgc = normalize_name(clean, provinces)
        if psgc is None or psgc not in provinces:
            continue
        try:
            year = int(year_label.get(year_code, year_code))
        except ValueError:
            continue
        value = _to_float(entry.get("values", [None])[0])
        if value is None:
            continue
        rows.append({"psgc": psgc, "year": year, "value": value})
    return rows


def fetch_population_2020(
    provinces: dict,
    normalize_name,
    huc_parent=None,
) -> list[dict]:
    """2020 Census total population, province-level. Single year per row.

    Rolls non-NCR HUC populations into their parent province so per-capita
    spend (DPWH ÷ this) reflects the whole geographic province rather than
    PSA's "province-without-HUC" convention.

    huc_parent: optional callable name -> parent_psgc. If None, no rollup.
    """
    url = f"{API_BASE}/{POPULATION_PATH}"
    meta = _fetch_or_cache("population_meta.json", lambda: _get_json(url))

    geo_var = next(
        v
        for v in meta["variables"]
        if "Geographic Location" in (v.get("code") or v.get("text", ""))
    )
    param_var = next(v for v in meta["variables"] if v.get("code") == "Parameter")

    total_val = None
    for val, txt in zip(param_var["values"], param_var["valueTexts"], strict=False):
        if "total population" in txt.lower():
            total_val = val
            break
    if total_val is None:
        raise RuntimeError("Could not locate 'Total Population' parameter in PSA population table")

    query = {
        "query": [
            {
                "code": geo_var["code"],
                "selection": {"filter": "item", "values": geo_var["values"]},
            },
            {
                "code": "Parameter",
                "selection": {"filter": "item", "values": [total_val]},
            },
        ],
        "response": {"format": "json"},
    }
    payload = _fetch_or_cache("population_data.json", lambda: _post_json(url, query))

    geo_label = dict(zip(geo_var["values"], geo_var["valueTexts"], strict=False))

    # Aggregate into one row per province PSGC, summing HUC populations.
    agg: dict[str, int] = {}
    for entry in payload.get("data", []):
        key = entry.get("key", [])
        if not key:
            continue
        geo_code = key[0]
        raw_label = geo_label.get(geo_code, "")
        clean = _clean_geo_text(raw_label)
        value = _to_float(entry.get("values", [None])[0])
        if value is None:
            continue
        # Try direct province / NCR aggregate match first.
        psgc = normalize_name(clean, provinces)
        if psgc is not None and psgc in provinces:
            agg[psgc] = agg.get(psgc, 0) + int(value)
            continue
        # Else try HUC rollup (e.g. "City of Cebu" -> Cebu province).
        if huc_parent is not None:
            parent = huc_parent(clean)
            if parent is not None and parent in provinces:
                agg[parent] = agg.get(parent, 0) + int(value)

    return [{"psgc": psgc, "year": 2020, "value": v} for psgc, v in agg.items()]


def fetch_gdp_per_capita(provinces: dict, normalize_name) -> list[dict]:
    """Per Capita GDP at Constant 2018 Prices, province + HUC, 2022-2024.

    Source: 2A/PPA/2025/0092A5GPPA8.px
    Returns [{psgc, year, value}] where value is PHP per person at 2018 prices.
    HUCs are dropped (not rolled into parent) because GDP per capita is a per-person
    measure already; summing wouldn't make sense without re-deriving from totals.
    """
    url = f"{API_BASE}/{GDP_PER_CAPITA_PATH}"
    meta = _fetch_or_cache("gdp_per_capita_meta.json", lambda: _get_json(url))

    geo_var = next(v for v in meta["variables"] if v.get("code") == "Geolocation")
    val_var = next(v for v in meta["variables"] if v.get("code") == "Type of Valuation")
    year_var = next(v for v in meta["variables"] if v.get("code") == "Year")

    constant_val = None
    for val, txt in zip(val_var["values"], val_var["valueTexts"], strict=False):
        if "constant" in txt.lower() and "2018" in txt:
            constant_val = val
            break
    if constant_val is None:
        raise RuntimeError("Could not locate 'At Constant 2018 Prices' valuation")

    query = {
        "query": [
            {
                "code": "Geolocation",
                "selection": {"filter": "item", "values": geo_var["values"]},
            },
            {
                "code": "Type of Valuation",
                "selection": {"filter": "item", "values": [constant_val]},
            },
            {
                "code": "Year",
                "selection": {"filter": "item", "values": year_var["values"]},
            },
        ],
        "response": {"format": "json"},
    }
    payload = _fetch_or_cache("gdp_per_capita_data.json", lambda: _post_json(url, query))

    geo_label = dict(zip(geo_var["values"], geo_var["valueTexts"], strict=False))
    year_label = dict(zip(year_var["values"], year_var["valueTexts"], strict=False))

    rows: list[dict] = []
    for entry in payload.get("data", []):
        key = entry.get("key", [])
        if len(key) < 3:
            continue
        geo_code, _val_code, year_code = key[0], key[1], key[2]
        raw_label = geo_label.get(geo_code, "")
        clean = _clean_geo_text(raw_label)
        psgc = normalize_name(clean, provinces)
        if psgc is None or psgc not in provinces:
            continue
        try:
            year = int(year_label.get(year_code, year_code))
        except ValueError:
            continue
        value = _to_float(entry.get("values", [None])[0])
        if value is None:
            continue
        rows.append({"psgc": psgc, "year": year, "value": value})
    return rows


def fetch_cpi_annual() -> dict[int, float]:
    """Annual average CPI (2018=100) for PHILIPPINES, All Items.

    Returns {year: cpi_index}. Used to compute real-PHP deflators:
    real_value = nominal_value * (100 / cpi_year).
    """
    url = f"{API_BASE}/{CPI_PATH}"
    meta = _fetch_or_cache("cpi_meta.json", lambda: _get_json(url))

    geo_var = next(v for v in meta["variables"] if v.get("code") == "Geolocation")
    commodity_var = next(
        v for v in meta["variables"] if v.get("code") == "Commodity Description"
    )
    year_var = next(v for v in meta["variables"] if v.get("code") == "Year")
    period_var = next(v for v in meta["variables"] if v.get("code") == "Period")

    # PHILIPPINES national row
    ph_val = next(
        v
        for v, t in zip(geo_var["values"], geo_var["valueTexts"], strict=False)
        if t.strip().upper() == "PHILIPPINES"
    )
    # ALL ITEMS commodity
    all_val = next(
        v
        for v, t in zip(commodity_var["values"], commodity_var["valueTexts"], strict=False)
        if "all items" in t.lower() and not any(x in t.lower() for x in ("food", "core"))
    )
    # Annual average period
    ave_val = next(
        v
        for v, t in zip(period_var["values"], period_var["valueTexts"], strict=False)
        if t.strip().lower().startswith("ave")
    )

    query = {
        "query": [
            {"code": "Geolocation", "selection": {"filter": "item", "values": [ph_val]}},
            {
                "code": "Commodity Description",
                "selection": {"filter": "item", "values": [all_val]},
            },
            {"code": "Year", "selection": {"filter": "item", "values": year_var["values"]}},
            {"code": "Period", "selection": {"filter": "item", "values": [ave_val]}},
        ],
        "response": {"format": "json"},
    }
    payload = _fetch_or_cache("cpi_data.json", lambda: _post_json(url, query))

    year_label = dict(zip(year_var["values"], year_var["valueTexts"], strict=False))

    out: dict[int, float] = {}
    for entry in payload.get("data", []):
        key = entry.get("key", [])
        if len(key) < 4:
            continue
        year_code = key[2]
        try:
            year = int(year_label.get(year_code, year_code))
        except ValueError:
            continue
        value = _to_float(entry.get("values", [None])[0])
        if value is None:
            continue
        out[year] = value
    return out
