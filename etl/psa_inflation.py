"""Regional cut of PSA OpenStat: CPI inflation + poverty at the 18-region grain.

PSA publishes CPI only down to regions (the 2M/PI/CPI/2018NEW table carries
province rows for a subset only, with gaps), so the inflation-vs-poverty story
runs on the 18 official regions instead of the 82 provincial units the rest of
the site uses. Both pulls reuse the psa_openstat plumbing: tenacity retry on
transient failures, 30-day TTL cache, missing-value sentinels, footnote
stripping. The regional poverty cut shares the cached Table 1a payload with the
provincial pull (identical query), so it costs no extra PSA request.

Region identity: PSA spells region names slightly differently across tables
("..Region I (Ilocos Region)" vs "..REGION I (ILOCOS REGION)") and Table 1a
adds footnote refs. Matching is on the cleaned, lowercased label via
startswith patterns chosen so no other row can collide: "Areas Outside
National Capital Region (AONCR)" starts with "areas outside" (never matches
the NCR pattern), provinces sit at a deeper indent and none of their names
start with a region pattern.
"""

from __future__ import annotations

from datetime import UTC, datetime

from etl.psa_openstat import (
    API_BASE,
    CPI_PATH,
    POVERTY_PATH,
    _clean_geo_text,
    _fetch_incidence_with_precision,
    _fetch_or_cache,
    _get_json,
    _post_json,
    _to_float,
)

# The 18 official PSA regions, in PSGC order. island_group keys into the same
# palette the provincial views use, so the island legend keeps working at the
# regional grain. "pattern" is a startswith match on the cleaned lowercase
# label; chosen to be collision-free across both source tables (see module
# docstring).
REGIONS: list[dict] = [
    {"id": "ncr", "name": "NCR", "island_group": "ncr", "pattern": "national capital region"},
    {"id": "car", "name": "CAR", "island_group": "luzon", "pattern": "cordillera"},
    {"id": "r01", "name": "Ilocos Region", "island_group": "luzon", "pattern": "region i ("},
    {"id": "r02", "name": "Cagayan Valley", "island_group": "luzon", "pattern": "region ii ("},
    {"id": "r03", "name": "Central Luzon", "island_group": "luzon", "pattern": "region iii ("},
    {"id": "r04a", "name": "CALABARZON", "island_group": "luzon", "pattern": "region iv-a"},
    {"id": "mimaropa", "name": "MIMAROPA", "island_group": "luzon", "pattern": "mimaropa"},
    {"id": "r05", "name": "Bicol Region", "island_group": "luzon", "pattern": "region v ("},
    {"id": "r06", "name": "Western Visayas", "island_group": "visayas", "pattern": "region vi ("},
    {
        "id": "nir",
        "name": "Negros Island Region",
        "island_group": "visayas",
        "pattern": "negros island",
    },
    {"id": "r07", "name": "Central Visayas", "island_group": "visayas", "pattern": "region vii ("},
    {"id": "r08", "name": "Eastern Visayas", "island_group": "visayas", "pattern": "region viii ("},
    {
        "id": "r09",
        "name": "Zamboanga Peninsula",
        "island_group": "mindanao",
        "pattern": "region ix (",
    },
    {
        "id": "r10",
        "name": "Northern Mindanao",
        "island_group": "mindanao",
        "pattern": "region x (",
    },
    {"id": "r11", "name": "Davao Region", "island_group": "mindanao", "pattern": "region xi ("},
    {"id": "r12", "name": "SOCCSKSARGEN", "island_group": "mindanao", "pattern": "region xii ("},
    {"id": "caraga", "name": "Caraga", "island_group": "mindanao", "pattern": "region xiii"},
    {"id": "barmm", "name": "BARMM", "island_group": "barmm", "pattern": "bangsamoro"},
]


def load_regions() -> dict[str, dict]:
    """Region unit set keyed by id, in the shape provinces.json uses
    ({name, island_group}; no population: PSA region population is not pulled
    and the regional story renders equal-size bubbles)."""
    return {r["id"]: {"name": r["name"], "island_group": r["island_group"]} for r in REGIONS}


def region_id_for(label: str) -> str | None:
    """Map a PSA geo label (cleaned or raw) to a region id, or None.

    startswith matching on the cleaned lowercase text. "Areas Outside National
    Capital Region (AONCR)" must never match NCR; province rows never start
    with a region pattern.
    """
    clean = _clean_geo_text(label).lower()
    for r in REGIONS:
        if clean.startswith(r["pattern"]):
            return r["id"]
    return None


def fetch_regional_poverty() -> list[dict]:
    """Poverty incidence among families (%) + precision for the 18 regions.

    PSA's own regional estimates from Table 1a, read directly off the region
    rows. NOT an aggregate of provincial rows: PSA's regional estimate uses the
    full survey design (weights, strata), which a naive provincial average
    would get wrong. Shares the cached Table 1a payload with the provincial
    pull (identical all-geolocations query). Returns
    [{psgc: region_id, year, value, cv, se, ci_lo, ci_hi}] for 2018/2021/2023.
    """
    regions = load_regions()
    return _fetch_incidence_with_precision(
        f"{API_BASE}/{POVERTY_PATH}",
        "poverty_full.json",  # same cache files as the provincial pull: one query, two cuts
        "poverty_full_data.json",
        lambda t: "poverty incidence" in t and "famil" in t,
        "PSA table 1a (regional rows)",
        regions,
        lambda clean, _units: region_id_for(clean),
    )


def fetch_regional_cpi() -> dict[str, dict[int, float]]:
    """Annual-average CPI (2018=100, All Items) per region.

    Returns {region_id: {year: index}}. Years on the table run 2018 onward; the
    in-progress calendar year is dropped by compute_regional_cpi_yoy (a partial
    -year "annual average" is not comparable to full-year averages).
    """
    url = f"{API_BASE}/{CPI_PATH}"
    meta = _fetch_or_cache("cpi_meta.json", lambda: _get_json(url))

    geo_var = next(v for v in meta["variables"] if v.get("code") == "Geolocation")
    commodity_var = next(v for v in meta["variables"] if v.get("code") == "Commodity Description")
    year_var = next(v for v in meta["variables"] if v.get("code") == "Year")
    period_var = next(v for v in meta["variables"] if v.get("code") == "Period")

    # Region rows sit at the two-dot indent; map their table codes to region ids.
    code_to_region: dict[str, str] = {}
    for val, txt in zip(geo_var["values"], geo_var["valueTexts"], strict=False):
        if not txt.startswith("..") or txt.startswith("...."):
            continue
        rid = region_id_for(txt)
        if rid is not None:
            code_to_region[val] = rid
    if len(code_to_region) != len(REGIONS):
        raise RuntimeError(
            f"Expected {len(REGIONS)} region rows in the CPI table, matched {len(code_to_region)}"
        )

    all_val = next(
        v
        for v, t in zip(commodity_var["values"], commodity_var["valueTexts"], strict=False)
        if "all items" in t.lower() and not any(x in t.lower() for x in ("food", "core"))
    )
    ave_val = next(
        v
        for v, t in zip(period_var["values"], period_var["valueTexts"], strict=False)
        if t.strip().lower().startswith("ave")
    )

    query = {
        "query": [
            {
                "code": "Geolocation",
                "selection": {"filter": "item", "values": sorted(code_to_region)},
            },
            {
                "code": "Commodity Description",
                "selection": {"filter": "item", "values": [all_val]},
            },
            {"code": "Year", "selection": {"filter": "item", "values": year_var["values"]}},
            {"code": "Period", "selection": {"filter": "item", "values": [ave_val]}},
        ],
        "response": {"format": "json"},
    }
    payload = _fetch_or_cache("cpi_regional_data.json", lambda: _post_json(url, query))

    year_label = dict(zip(year_var["values"], year_var["valueTexts"], strict=False))

    out: dict[str, dict[int, float]] = {}
    for entry in payload.get("data", []):
        key = entry.get("key", [])
        if len(key) < 4:
            continue
        rid = code_to_region.get(key[0])
        if rid is None:
            continue
        try:
            year = int(year_label.get(key[2], key[2]))
        except ValueError:
            continue
        value = _to_float(entry.get("values", [None])[0])
        if value is None:
            continue
        out.setdefault(rid, {})[year] = value
    return out


def compute_regional_cpi_yoy(cpi_by_region: dict[str, dict[int, float]]) -> list[dict]:
    """Year-on-year inflation (%) per region from annual-average CPI indices.

    Same arithmetic as the national compute_cpi_yoy: (cpi_y - cpi_prev) /
    cpi_prev * 100, consecutive years only. The in-progress calendar year is
    dropped: PSA's running "Ave" for it is a partial-year average and would
    read as a fake full-year inflation print.
    """
    current_year = datetime.now(UTC).year
    out: list[dict] = []
    for rid, series in cpi_by_region.items():
        years = sorted(y for y in series if y < current_year)
        for i in range(1, len(years)):
            y, prev = years[i], years[i - 1]
            if y - prev != 1 or series[prev] <= 0:
                continue
            pct = (series[y] - series[prev]) / series[prev] * 100
            out.append({"psgc": rid, "year": y, "value": pct})
    return out
