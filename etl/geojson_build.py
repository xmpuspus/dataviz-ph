"""Build public/data/ph-provinces.geojson dissolved to dataviz.ph's 82-unit model.

Source: faeldon/philippines-json-maps 2019 ADM2 (province-level, PSGC-coded,
lowres 0.001 simplification). That dataset is already province-grain with HUCs
folded into their parent province geometry (e.g. Cebu City is inside Cebu, not a
separate feature) and Maguindanao still unified (pre-2022 split) -- both of which
match dataviz.ph's data model. The only dissolve required is NCR: its 4 legislative
districts are merged into the single virtual NCR unit the rest of the app uses.

Output: one Feature per dataviz.ph unit, properties = {name, psgc, island_group}.
`name` is the canonical provinces.json name so ECharts can match chart data to
the polygon. Run once; the asset is committed (like the vendored ECharts build):

    python -m etl.geojson_build
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from etl.psgc import NCR_CODE, load_provinces, normalize_name

PUBLIC_DATA = Path(__file__).resolve().parent.parent / "public" / "data"
CACHE_DIR = Path(__file__).resolve().parent.parent / ".etl_cache" / "geojson"
RAW_BASE = (
    "https://raw.githubusercontent.com/faeldon/philippines-json-maps/master/"
    "2019/geojson/provinces/lowres"
)
REGION_CODES = [f"ph{n:02d}0000000" for n in range(1, 19)]  # ph010000000 .. ph180000000
COORD_PRECISION = 4  # ~11m; plenty for a national choropleth, keeps the file small

# Two independent cities the source carries as standalone ADM2 features. They have
# no province row of their own, so merge their GEOMETRY into the geographic parent
# to keep the map hole-free. This affects shape only, never the indicator value.
# - City of Isabela -> Basilan: matches the population rollup in psgc.HUC_TO_PARENT.
# - Cotabato City -> Maguindanao: geographically enclaved in Maguindanao. Its spend
#   stays unattributed (psgc excludes it); only the polygon is absorbed.
GEOMETRY_REPARENT = {
    "099700000": "150700000",  # City of Isabela -> Basilan
    "129800000": "153800000",  # Cotabato City -> Maguindanao
}


def _fetch_region(region_code: str) -> dict:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = CACHE_DIR / f"{region_code}.json"
    if cached.exists():
        return json.loads(cached.read_text())
    url = f"{RAW_BASE}/provinces-region-{region_code}.0.001.json"
    req = urllib.request.Request(url, headers={"User-Agent": "dataviz-ph-etl/1.0"})
    raw = urllib.request.urlopen(req, timeout=60).read()  # noqa: S310 (trusted host)
    cached.write_bytes(raw)
    return json.loads(raw)


def _round_coords(obj):
    if isinstance(obj, (int, float)):
        return round(obj, COORD_PRECISION)
    if isinstance(obj, list):
        return [_round_coords(x) for x in obj]
    return obj


def build_province_geojson() -> dict:
    provinces = load_provinces()  # {psgc: {name, island_group, region_code, population_2020}}

    # Collect source geometries grouped by the dataviz.ph unit they belong to.
    by_unit: dict[str, list] = {}
    unmatched: list[str] = []
    for region_code in REGION_CODES:
        fc = _fetch_region(region_code)
        for feat in fc.get("features", []):
            props = feat.get("properties", {})
            pcode = (props.get("ADM2_PCODE") or "").removeprefix("PH")
            name = props.get("ADM2_EN") or ""
            adm1 = props.get("ADM1_PCODE") or ""
            if adm1 == "PH130000000":  # any NCR district -> single NCR unit
                target = NCR_CODE
            elif pcode in GEOMETRY_REPARENT:  # independent cities -> geographic parent
                target = GEOMETRY_REPARENT[pcode]
            elif pcode in provinces:  # direct PSGC match (the common case)
                target = pcode
            else:  # fall back to name resolution (aliases, renamed provinces)
                target = normalize_name(name, provinces)
            if target is None or target not in provinces:
                unmatched.append(f"{name} ({pcode})")
                continue
            by_unit.setdefault(target, []).append(shape(feat["geometry"]))

    features = []
    for psgc, geoms in by_unit.items():
        geom = geoms[0] if len(geoms) == 1 else unary_union(geoms)
        info = provinces[psgc]
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "name": info["name"],
                    "psgc": psgc,
                    "island_group": info.get("island_group", ""),
                },
                "geometry": _round_coords(mapping(geom)),
            }
        )
    features.sort(key=lambda f: f["properties"]["name"])

    covered = {f["properties"]["psgc"] for f in features}
    missing = [(p, provinces[p]["name"]) for p in provinces if p not in covered]
    return {
        "type": "FeatureCollection",
        "features": features,
        "_meta": {
            "source": "faeldon/philippines-json-maps 2019 ADM2 lowres",
            "units": len(features),
            "dissolved": "NCR 4 districts -> 1; HUCs already in parent geometry",
            "missing_units": [f"{name} ({p})" for p, name in missing],
            "unmatched_source_features": unmatched,
        },
    }


def main() -> None:
    fc = build_province_geojson()
    out = PUBLIC_DATA / "ph-provinces.geojson"
    out.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")))
    meta = fc["_meta"]
    print(f"wrote {out.name} ({out.stat().st_size:,} bytes), {meta['units']} units")
    if meta["missing_units"]:
        print(f"  MISSING ({len(meta['missing_units'])}):", meta["missing_units"])
    if meta["unmatched_source_features"]:
        print(f"  UNMATCHED source ({len(meta['unmatched_source_features'])}):",
              meta["unmatched_source_features"])


if __name__ == "__main__":
    main()
