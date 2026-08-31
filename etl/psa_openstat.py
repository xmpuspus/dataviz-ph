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
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from etl.source_catalog import PSA_TABLES, resolve_reviewed_table, source_record

API_BASE = "https://openstat.psa.gov.ph/PXWeb/api/v1/en/DB"
CACHE_DIR = Path(__file__).resolve().parent.parent / ".etl_cache" / "psa"
OBSERVED_SOURCE_RECORDS: dict[str, dict] = {}


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

MISSING_SENTINELS = {"..", "...", "-", "/s", "", None}

POVERTY_DEPTH_MEASURES = {
    "poverty_poor_families": {"kind": "count", "unit": "thousand families"},
    "poverty_income_gap": {"kind": "rate", "unit": "percent"},
    "poverty_poverty_gap": {"kind": "rate", "unit": "percent"},
    "poverty_severity": {"kind": "rate", "unit": "percent"},
}

INDUSTRY_REVIEWED_SEMANTICS = {
    "provenance": "PSA OpenSTAT reviewed metadata, 2026-08-31",
    "metadata_sha256": "306ae4217ea402feafa9e0bfacb1eb024a276283428fc57e98a44a0c660fa2fc",
    "unit": "thousand Philippine pesos",
    "decimals": 12,
    "suppression_markers": ["-", "..", "...", "/s"],
    "years": list(range(2018, 2026)),
    "valuations": ["At Current Prices", "At Constant 2018 Prices"],
}


CACHE_TTL_DAYS = 30


def _cache_path(name: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / name


def cache_identity(prefix: str, table: str, query: dict) -> str:
    """Return a stable cache name that changes with table and query selection."""
    encoded = json.dumps({"table": table, "query": query}, sort_keys=True, separators=(",", ":"))
    return f"{prefix}_{sha256(encoded.encode()).hexdigest()[:16]}.json"


def require_source_years(rows: list[dict], years: range, label: str) -> None:
    """Fail when an official source pull misses a reviewed release year."""
    missing = sorted(set(years) - {row["year"] for row in rows})
    if missing:
        raise ValueError(f"{label} source years missing: {missing}")


def require_analysis_coverage(rows: list[dict], years: range, label: str) -> None:
    """Fail when a stable analysis series lacks 82 unique units in any source year."""
    for year in years:
        units = [row["psgc"] for row in rows if row["year"] == year]
        if len(units) != 82 or len(set(units)) != 82:
            raise ValueError(f"{label} coverage for {year} needs 82 unique analysis units")


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
    if s.lower().startswith("palawan (w/o the city of puerto princesa"):
        return "Palawan"
    # PSA inserts one or two asterisks before numbered footnotes for estimates
    # that need a source warning. Remove them for identity matching; callers
    # inspect the raw label first when they need to preserve the warning.
    s = re.sub(r"\*+", "", s).strip()
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


def _query_payload(url: str, prefix: str, table: str, query: dict) -> dict:
    return _fetch_or_cache(cache_identity(prefix, table, query), lambda: _post_json(url, query))


def _directory_paths(directory: str, title_terms: tuple[str, ...]) -> list[str]:
    """Return matching PXWeb leaves below a reviewed root."""
    pending = [(directory, 0)]
    paths: list[str] = []
    while pending:
        current, depth = pending.pop()
        try:
            listing = _get_json(f"{API_BASE}/{current}")
        except httpx.HTTPError:
            # PSA has retired subject directories without retiring their leaf tables.
            # The reviewed fallback still undergoes full metadata contract validation.
            continue
        entries = listing if isinstance(listing, list) else listing.get("data", [])
        for entry in entries:
            identifier = entry.get("id") if isinstance(entry, dict) else None
            title = str(entry.get("text", "")).lower() if isinstance(entry, dict) else ""
            if not isinstance(identifier, str):
                continue
            path = identifier if "/" in identifier else f"{current}/{identifier}"
            if identifier.endswith(".px") and all(term in title for term in title_terms):
                paths.append(path)
            elif entry.get("type") == "l" and depth < 2:
                pending.append((path, depth + 1))
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

    path = resolve_reviewed_table(
        contract, _directory_paths(contract.directory, contract.title_terms), fetch_metadata
    )
    metadata = fetch_metadata(path)
    OBSERVED_SOURCE_RECORDS[name] = source_record(
        contract,
        path,
        metadata,
        fetched_at=datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
    )
    return path, metadata


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
        psgc = normalize_name(clean, provinces, series="poverty_fies")
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
        psgc = normalize_name(clean, provinces, series="population")
        if psgc is not None and psgc in provinces:
            agg[psgc] = agg.get(psgc, 0) + int(value)
            continue
        # Else try HUC rollup (e.g. "City of Cebu" -> Cebu province).
        if huc_parent is not None:
            parent = huc_parent(clean)
            if parent is not None and parent in provinces:
                agg[parent] = agg.get(parent, 0) + int(value)

    return [{"psgc": psgc, "year": 2020, "value": v} for psgc, v in agg.items()]


def fetch_population_2024(
    provinces: dict,
    normalize_name,
    huc_parent=None,
) -> list[dict]:
    """Fetch and roll up the official 2024 POPCEN population anchor."""
    path, _ = discover_table("population_2024")
    url = f"{API_BASE}/{path}"
    meta = _fetch_or_cache(cache_identity("population_meta", path, {}), lambda: _get_json(url))
    geo_var = next(
        variable
        for variable in meta["variables"]
        if "Geographic Location" in (variable.get("code") or variable.get("text", ""))
    )
    parameter = next(
        variable for variable in meta["variables"] if variable.get("code") == "Parameter"
    )
    total = next(
        value
        for value, text in zip(parameter["values"], parameter["valueTexts"], strict=False)
        if "total population" in text.lower()
    )
    query = {
        "query": [
            {"code": geo_var["code"], "selection": {"filter": "item", "values": geo_var["values"]}},
            {"code": "Parameter", "selection": {"filter": "item", "values": [total]}},
        ],
        "response": {"format": "json"},
    }
    payload = _query_payload(url, "population_data", path, query)
    labels = dict(zip(geo_var["values"], geo_var["valueTexts"], strict=False))
    aggregate: dict[str, int] = {}
    for entry in payload.get("data", []):
        key = entry.get("key", [])
        if not key:
            continue
        value = _to_float(entry.get("values", [None])[0])
        if value is None:
            continue
        name = _clean_geo_text(labels.get(key[0], ""))
        psgc = normalize_name(name, provinces, series="population")
        if psgc is None and huc_parent is not None:
            psgc = huc_parent(name)
        if psgc in provinces:
            aggregate[psgc] = aggregate.get(psgc, 0) + round(value)
    return [{"psgc": psgc, "year": 2024, "value": value} for psgc, value in aggregate.items()]


def interpolate_population_anchors(
    population_2020: list[dict], population_2024: list[dict], years: list[int]
) -> list[dict]:
    """Keep the 2020 census anchor and interpolate only until the 2024 anchor."""
    by_2020 = {row["psgc"]: row["value"] for row in population_2020}
    by_2024 = {row["psgc"]: row["value"] for row in population_2024}
    if set(by_2020) != set(by_2024):
        missing = sorted(set(by_2020) ^ set(by_2024))
        raise ValueError(f"population anchors have unmatched units: {missing}")
    rows = []
    for psgc in sorted(by_2020):
        start, end = by_2020[psgc], by_2024[psgc]
        for year in years:
            if year < 2020 or year > 2024:
                value = start if year <= 2020 else end
                estimate = False
                official = year == 2020
            elif year in {2020, 2024}:
                value = start if year == 2020 else end
                estimate = False
                official = True
            else:
                value = round(start + (end - start) * (year - 2020) / 4)
                estimate = True
                official = False
            rows.append(
                {
                    "psgc": psgc,
                    "year": year,
                    "value": value,
                    "estimate": estimate,
                    "official": official,
                }
            )
    return rows


def recompute_gdp_per_capita(gdp_rows: list[dict], published_rows: list[dict]) -> list[dict]:
    """Sum GDP and matching source-implied populations before division."""
    published: dict[tuple[str, int], float] = {}
    for row in published_rows:
        key = (row["source_id"], row["year"])
        if key in published:
            raise ValueError(f"duplicate published per-capita denominator: {key}")
        published[key] = row["value"]
    gdp_keys: set[tuple[str, int]] = set()
    for row in gdp_rows:
        key = (row["source_id"], row["year"])
        if key in gdp_keys:
            raise ValueError(f"duplicate GDP source leaf: {key}")
        gdp_keys.add(key)
    if gdp_keys != set(published):
        missing = sorted(gdp_keys - set(published))
        extra = sorted(set(published) - gdp_keys)
        raise ValueError(
            f"GDP/per-capita source pairing mismatch: missing={missing}, extra={extra}"
        )
    totals: dict[tuple[str, int], tuple[float, float]] = {}
    for row in gdp_rows:
        published_value = published[(row["source_id"], row["year"])]
        if published_value <= 0:
            raise ValueError(f"non-positive published per-capita denominator: {row['source_id']}")
        key = (row["psgc"], row["year"])
        gdp, population = totals.get(key, (0.0, 0.0))
        totals[key] = (
            gdp + row["value"],
            population + row["value"] / published_value,
        )
    return [
        {"psgc": psgc, "year": year, "value": gdp / population}
        for (psgc, year), (gdp, population) in sorted(totals.items())
        if population > 0
    ]


def fetch_gdp_total(provinces: dict, normalize_name, huc_parent) -> list[dict]:
    """Fetch additive constant-price GDP for defensible stable-area recomputation."""
    path, _ = discover_table("gdp_total")
    url = f"{API_BASE}/{path}"
    meta = _fetch_or_cache(cache_identity("gdp_total_meta", path, {}), lambda: _get_json(url))
    geo_var = next(variable for variable in meta["variables"] if variable["code"] == "Geolocation")
    valuation = next(
        variable for variable in meta["variables"] if variable["code"] == "Type of Valuation"
    )
    year_var = next(variable for variable in meta["variables"] if variable["code"] == "Year")
    constant = next(
        value
        for value, text in zip(valuation["values"], valuation["valueTexts"], strict=False)
        if "constant" in text.lower() and "2018" in text
    )
    query = {
        "query": [
            {"code": "Geolocation", "selection": {"filter": "item", "values": geo_var["values"]}},
            {"code": "Type of Valuation", "selection": {"filter": "item", "values": [constant]}},
            {"code": "Year", "selection": {"filter": "item", "values": year_var["values"]}},
        ],
        "response": {"format": "json"},
    }
    payload = _query_payload(url, "gdp_total_data", path, query)
    labels = dict(zip(geo_var["values"], geo_var["valueTexts"], strict=False))
    years = dict(zip(year_var["values"], year_var["valueTexts"], strict=False))
    rows = []
    for entry in payload.get("data", []):
        key = entry.get("key", [])
        if len(key) < 3:
            continue
        value = _to_float(entry.get("values", [None])[0])
        if value is None:
            continue
        name = _clean_geo_text(labels.get(key[0], ""))
        psgc = normalize_name(name, provinces, series="gdp_recomputation")
        if psgc is None:
            psgc = huc_parent(name)
        if psgc not in provinces:
            continue
        try:
            year = int(years.get(key[2], key[2]))
        except ValueError:
            continue
        rows.append({"source_id": key[0], "psgc": psgc, "year": year, "value": value * 1_000})
    return rows


def fetch_gdp_per_capita_source(provinces: dict, normalize_name, huc_parent) -> list[dict]:
    """Fetch the published PPA per-person series as a matching denominator source."""
    path, _ = discover_table("gdp_per_capita")
    url = f"{API_BASE}/{path}"
    meta = _fetch_or_cache(cache_identity("gdp_per_capita_meta", path, {}), lambda: _get_json(url))
    geo_var = next(variable for variable in meta["variables"] if variable["code"] == "Geolocation")
    valuation = next(
        variable for variable in meta["variables"] if variable["code"] == "Type of Valuation"
    )
    year_var = next(variable for variable in meta["variables"] if variable["code"] == "Year")
    constant = next(
        value
        for value, text in zip(valuation["values"], valuation["valueTexts"], strict=False)
        if "constant" in text.lower() and "2018" in text
    )
    query = {
        "query": [
            {"code": "Geolocation", "selection": {"filter": "item", "values": geo_var["values"]}},
            {"code": "Type of Valuation", "selection": {"filter": "item", "values": [constant]}},
            {"code": "Year", "selection": {"filter": "item", "values": year_var["values"]}},
        ],
        "response": {"format": "json"},
    }
    payload = _query_payload(url, "gdp_per_capita_data", path, query)
    labels = dict(zip(geo_var["values"], geo_var["valueTexts"], strict=False))
    years = dict(zip(year_var["values"], year_var["valueTexts"], strict=False))
    rows = []
    for entry in payload.get("data", []):
        key = entry.get("key", [])
        if len(key) < 3:
            continue
        value = _to_float(entry.get("values", [None])[0])
        if value is None:
            continue
        name = _clean_geo_text(labels.get(key[0], ""))
        psgc = normalize_name(name, provinces, series="gdp_recomputation")
        if psgc is None:
            psgc = huc_parent(name)
        if psgc not in provinces:
            continue
        try:
            year = int(years.get(key[2], key[2]))
        except ValueError:
            continue
        rows.append({"source_id": key[0], "psgc": psgc, "year": year, "value": value})
    return rows


def validate_gdp_industry_contract() -> dict:
    """Check the PPA industry table without treating its sectors as total GDP."""
    path, metadata = discover_table("gdp_industry")
    sector = next(variable for variable in metadata["variables"] if variable["code"] == "Sector")
    valuation = next(
        variable for variable in metadata["variables"] if variable["code"] == "Type of Valuation"
    )
    year = next(variable for variable in metadata["variables"] if variable["code"] == "Year")
    metadata_sha256 = sha256(
        json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if metadata_sha256 != INDUSTRY_REVIEWED_SEMANTICS["metadata_sha256"]:
        raise ValueError("PPA industry metadata differs from the reviewed source response")
    if len(sector["values"]) != 16:
        raise ValueError(f"PPA industry contract needs 16 sectors, got {len(sector['values'])}")
    valuation_text = " ".join(valuation["valueTexts"]).lower()
    if "current" not in valuation_text or "constant" not in valuation_text:
        raise ValueError("PPA industry contract needs current and constant valuations")
    require_source_years(
        [{"year": int(value)} for value in year["valueTexts"]], range(2018, 2026), "PPA industry"
    )
    if [*valuation["valueTexts"]] != INDUSTRY_REVIEWED_SEMANTICS["valuations"]:
        raise ValueError("PPA industry valuations differ from reviewed semantics")
    return {
        "path": path,
        "sectors": len(sector["values"]),
        **INDUSTRY_REVIEWED_SEMANTICS,
    }


def fetch_poverty_depth(
    table: str,
    provinces: dict,
    normalize_name,
    missing_evidence: list[dict] | None = None,
) -> list[dict]:
    """Fetch a poverty-depth measure at its published, non-averaged area grain."""
    contract = POVERTY_DEPTH_MEASURES.get(table)
    if contract is None:
        raise ValueError(f"unknown poverty-depth table: {table}")
    path, _ = discover_table(table)
    url = f"{API_BASE}/{path}"
    meta = _fetch_or_cache(cache_identity("poverty_depth_meta", path, {}), lambda: _get_json(url))
    geo_var = next(variable for variable in meta["variables"] if variable["code"] == "Geolocation")
    measure_var = next(
        variable
        for variable in meta["variables"]
        if variable["code"] == "Estimates/Measures of Precision"
    )
    year_var = next(variable for variable in meta["variables"] if variable["code"] == "Year")
    measure_codes = {}
    for value, text in zip(measure_var["values"], measure_var["valueTexts"], strict=False):
        normalized = text.lower()
        if normalized.startswith("estimate"):
            measure_codes["value"] = value
        elif "coefficient of variation" in normalized:
            measure_codes["cv"] = value
        elif "standard error" in normalized:
            measure_codes["se"] = value
        elif "lower limit" in normalized:
            measure_codes["ci_lo"] = value
        elif "upper limit" in normalized:
            measure_codes["ci_hi"] = value
    if "value" not in measure_codes:
        raise ValueError(f"{table} lacks an estimate measure")
    query = {
        "query": [
            {"code": "Geolocation", "selection": {"filter": "item", "values": geo_var["values"]}},
            {
                "code": measure_var["code"],
                "selection": {"filter": "item", "values": list(measure_codes.values())},
            },
            {"code": "Year", "selection": {"filter": "item", "values": year_var["values"]}},
        ],
        "response": {"format": "json"},
    }
    payload = _query_payload(url, "poverty_depth_data", path, query)
    labels = dict(zip(geo_var["values"], geo_var["valueTexts"], strict=False))
    years = dict(zip(year_var["values"], year_var["valueTexts"], strict=False))
    rows_by_key: dict[tuple[str, int], dict] = {}
    measure_by_code = {value: key for key, value in measure_codes.items()}
    for entry in payload.get("data", []):
        key = entry.get("key", [])
        if len(key) < 3:
            continue
        role = measure_by_code.get(key[1])
        if role is None:
            continue
        raw_label = labels.get(key[0], "")
        psgc = normalize_name(_clean_geo_text(raw_label), provinces, series="poverty_fies")
        if psgc not in provinces:
            continue
        try:
            year = int(years.get(key[2], key[2]))
        except ValueError:
            continue
        source_small_sample_warning = "*" in raw_label
        revision_markers = sorted(set(re.findall(r"\br\d+\b", raw_label, flags=re.IGNORECASE)))
        raw_value = entry.get("values", [None])[0]
        value = _to_float(raw_value)
        if value is None:
            if role == "value" and missing_evidence is not None:
                reason = (
                    "source_marker_dash"
                    if raw_value == "-"
                    else "source_marker_suppressed"
                    if raw_value in {"..", "...", "/s"}
                    else "source_value_unavailable"
                )
                missing_evidence.append(
                    {
                        "psgc": psgc,
                        "year": year,
                        "measure": table,
                        "reason": reason,
                        "source_marker": raw_value,
                        "source_small_sample_warning": source_small_sample_warning,
                        "source_revision_markers": revision_markers,
                    }
                )
            continue
        row = rows_by_key.setdefault(
            (psgc, year), {"psgc": psgc, "year": year, "measure": table, **contract}
        )
        row[role] = value
        if source_small_sample_warning:
            row["source_small_sample_warning"] = True
        if revision_markers:
            row["source_revision_markers"] = revision_markers
    rows = [row for row in rows_by_key.values() if "value" in row]
    for row in rows:
        if "ci_lo" in row and "ci_hi" in row and row["ci_lo"] > row["ci_hi"]:
            raise ValueError(f"{table} has reversed confidence interval for {row['psgc']}")
    return rows


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
        psgc = normalize_name(clean, provinces, series="gdp_per_capita")
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
