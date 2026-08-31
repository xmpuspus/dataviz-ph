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
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from etl.source_catalog import PSA_TABLES, resolve_reviewed_table

API_BASE = "https://openstat.psa.gov.ph/PXWeb/api/v1/en/DB"
CACHE_DIR = Path(__file__).resolve().parent.parent / ".etl_cache" / "psa"


def _is_retryable(exc: BaseException) -> bool:
    """Retry only on transient network failures and 429/5xx, never on 4xx logic errors."""
    if isinstance(exc, (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or 500 <= code < 600
    return False


_RETRY = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=1, max=4, jitter=0.1),
    retry=retry_if_exception(_is_retryable),
    reraise=True,
)

# Compatibility aliases for etl.psa_inflation.  New callers must use
# discover_table(), which validates metadata rather than trusting a leaf name.
POVERTY_PATH = PSA_TABLES["poverty"].reviewed_fallbacks[0]
SUBSISTENCE_PATH = PSA_TABLES["subsistence"].reviewed_fallbacks[0]
POPULATION_PATH = PSA_TABLES["population"].reviewed_fallbacks[0]
GDP_PER_CAPITA_PATH = PSA_TABLES["gdp_per_capita"].reviewed_fallbacks[0]
CPI_PATH = PSA_TABLES["cpi"].reviewed_fallbacks[0]

MISSING_SENTINELS = {"..", "...", "-", "", None}


CACHE_TTL_DAYS = 30


def _cache_path(name: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / name


def clear_cache() -> None:
    """Delete every cached PSA payload. Used by `etl.build --no-cache`."""
    if not CACHE_DIR.exists():
        return
    for f in CACHE_DIR.glob("*.json"):
        f.unlink()


def _cache_fresh(path: Path, ttl_days: int = CACHE_TTL_DAYS) -> bool:
    """True if the cache file exists and was modified within ttl_days."""
    if not path.exists():
        return False
    import time

    age_seconds = time.time() - path.stat().st_mtime
    return age_seconds < ttl_days * 86400


@_RETRY
def _get_json(url: str) -> dict | list:
    with httpx.Client(timeout=30.0) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.json()


@_RETRY
def _post_json(url: str, query: dict) -> dict:
    # 180s tolerates the slowest PSA bulk pull (poverty Geolocation has 142 items).
    with httpx.Client(timeout=180.0) as client:
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
    """Fetch with a 30-day TTL. Stale entries are re-fetched (PSA republishes annually)."""
    cache = _cache_path(name)
    if _cache_fresh(cache):
        return json.loads(cache.read_text())
    payload = fetch()
    cache.write_text(json.dumps(payload))
    return payload


def _directory_paths(directory: str) -> list[str]:
    """Return PXWeb leaf paths from one reviewed directory listing."""
    listing = _get_json(f"{API_BASE}/{directory}")
    entries = listing if isinstance(listing, list) else listing.get("data", [])
    paths: list[str] = []
    for entry in entries:
        identifier = entry.get("id") if isinstance(entry, dict) else None
        if isinstance(identifier, str) and identifier.endswith(".px"):
            paths.append(identifier if "/" in identifier else f"{directory}/{identifier}")
    return paths


def discover_table(name: str) -> tuple[str, dict]:
    """Discover a compatible PSA leaf and validate its reviewed metadata contract."""
    contract = PSA_TABLES[name]
    metadata_by_path: dict[str, dict] = {}

    def fetch_metadata(path: str) -> dict:
        if path not in metadata_by_path:
            cache_name = f"source_catalog_{name}_{path.replace('/', '_')}.json"
            metadata_by_path[path] = _fetch_or_cache(
                cache_name, lambda: _get_json(f"{API_BASE}/{path}")
            )
        return metadata_by_path[path]

    path = resolve_reviewed_table(contract, _directory_paths(contract.directory), fetch_metadata)
    return path, fetch_metadata(path)


# Roles we pull from the "Threshold/Incidence/Parameters" dimension of PSA
# Tables 1a/3a. The incidence measure is matched per-table; the four precision
# measures share the same labels across both tables.
_PRECISION_LABELS = {
    "cv": "coefficient of variation",
    "se": "standard error",
    "ci_lo": "lower limit",
    "ci_hi": "upper limit",
}


def _fetch_incidence_with_precision(
    url: str,
    meta_name: str,
    data_name: str,
    incidence_pred,
    table_label: str,
    provinces: dict,
    normalize_name,
) -> list[dict]:
    """Pull an incidence rate plus its measures of precision from a PSA poverty table.

    PSA Tables 1a (poverty) and 3a (subsistence) both expose, per province-year:
    the incidence (%), its Coefficient of Variation (%), Standard Error, and the
    95% Confidence Interval lower/upper limits. Earlier we kept only the incidence
    and threw the precision away; this captures all of it so the UI can show how
    trustworthy each estimate is.

    Only the published survey years (2018/2021/2023) carry precision. Returns rows:
    [{psgc, year, value, cv, se, ci_lo, ci_hi}] where the precision keys are
    omitted when PSA reports them as missing.
    """
    meta = _fetch_or_cache(meta_name, lambda: _get_json(url))

    geo_var = next(v for v in meta["variables"] if v.get("code") == "Geolocation")
    measure_var = next(v for v in meta["variables"] if "Threshold" in (v.get("code") or ""))
    year_var = next(v for v in meta["variables"] if v.get("code") == "Year")

    # Map each measure value-code to a role by reading its valueText.
    measure_codes: dict[str, str] = {}
    for val, txt in zip(measure_var["values"], measure_var["valueTexts"], strict=False):
        t = txt.lower()
        if incidence_pred(t):
            measure_codes["value"] = val
            continue
        for role, needle in _PRECISION_LABELS.items():
            if needle in t:
                measure_codes[role] = val
                break
    if "value" not in measure_codes:
        raise RuntimeError(f"Could not locate the incidence measure in {table_label}")

    wanted_roles = [r for r in ("value", "cv", "se", "ci_lo", "ci_hi") if r in measure_codes]
    requested_codes = [measure_codes[r] for r in wanted_roles]
    code_to_role = {measure_codes[r]: r for r in wanted_roles}

    query = {
        "query": [
            {
                "code": "Geolocation",
                "selection": {"filter": "item", "values": geo_var["values"]},
            },
            {
                "code": measure_var["code"],
                "selection": {"filter": "item", "values": requested_codes},
            },
            {
                "code": "Year",
                "selection": {"filter": "item", "values": year_var["values"]},
            },
        ],
        "response": {"format": "json"},
    }
    payload = _fetch_or_cache(data_name, lambda: _post_json(url, query))

    geo_label = dict(zip(geo_var["values"], geo_var["valueTexts"], strict=False))
    year_label = dict(zip(year_var["values"], year_var["valueTexts"], strict=False))

    # Assemble per (psgc, year): one record gathering value + precision roles.
    rec: dict[tuple[str, int], dict[str, float]] = {}
    for entry in payload.get("data", []):
        key = entry.get("key", [])
        if len(key) < 3:
            continue
        geo_code, measure_code, year_code = key[0], key[1], key[2]
        role = code_to_role.get(measure_code)
        if role is None:
            continue
        clean = _clean_geo_text(geo_label.get(geo_code, ""))
        psgc = normalize_name(clean, provinces)
        if psgc is None or psgc not in provinces:
            continue
        try:
            year = int(year_label.get(year_code, year_code))
        except ValueError:
            continue
        rec.setdefault((psgc, year), {})[role] = _to_float(entry.get("values", [None])[0])

    rows: list[dict] = []
    for (psgc, year), m in rec.items():
        if m.get("value") is None:
            continue
        row = {"psgc": psgc, "year": year, "value": m["value"]}
        for role in ("cv", "se", "ci_lo", "ci_hi"):
            if m.get(role) is not None:
                row[role] = m[role]
        rows.append(row)
    return rows


def fetch_poverty(provinces: dict, normalize_name) -> list[dict]:
    """Poverty incidence among families (%) + precision, province-level, 2018/2021/2023.

    normalize_name is injected from etl.psgc to keep this module focused.
    Returns rows: [{psgc, year, value, cv, se, ci_lo, ci_hi}] (precision on the
    published survey years only).
    """
    path, _ = discover_table("poverty")
    return _fetch_incidence_with_precision(
        f"{API_BASE}/{path}",
        "poverty_full.json",  # meta is reused below; data cache is the precision pull
        "poverty_full_data.json",
        lambda t: "poverty incidence" in t and "famil" in t,
        "PSA table 1a",
        provinces,
        normalize_name,
    )


def fetch_subsistence(provinces: dict, normalize_name) -> list[dict]:
    """Subsistence incidence among families (%) + precision, 2018/2021/2023.

    PSA Table 3a (food-threshold). Structural mirror of fetch_poverty. Subsistence
    incidence is the share of families below the food threshold, so it is always
    lower than poverty incidence (food < full poverty threshold).
    Returns rows: [{psgc, year, value, cv, se, ci_lo, ci_hi}].
    """
    path, _ = discover_table("subsistence")
    return _fetch_incidence_with_precision(
        f"{API_BASE}/{path}",
        "subsistence_full.json",
        "subsistence_full_data.json",
        lambda t: "subsistence incidence" in t and "famil" in t,
        "PSA table 3a",
        provinces,
        normalize_name,
    )


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
    path, _ = discover_table("population")
    url = f"{API_BASE}/{path}"
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
    path, _ = discover_table("gdp_per_capita")
    url = f"{API_BASE}/{path}"
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
    path, _ = discover_table("cpi")
    url = f"{API_BASE}/{path}"
    meta = _fetch_or_cache("cpi_meta.json", lambda: _get_json(url))

    geo_var = next(v for v in meta["variables"] if v.get("code") == "Geolocation")
    commodity_var = next(v for v in meta["variables"] if v.get("code") == "Commodity Description")
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
