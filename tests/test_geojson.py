"""Guards the committed province choropleth asset (public/data/ph-provinces.geojson).

No network: validates the shipped file against provinces.json so the map view can
never drift from the 82-unit data model (every polygon must match a chart unit by
name, or ECharts silently drops it).
"""

from __future__ import annotations

import json
from pathlib import Path

from etl import geojson_build

PUBLIC_DATA = Path(__file__).resolve().parent.parent / "public" / "data"


def _load():
    geo = json.loads((PUBLIC_DATA / "ph-provinces.geojson").read_text())
    prov = json.loads((PUBLIC_DATA / "provinces.json").read_text())
    return geo, prov


def test_one_polygon_per_unit() -> None:
    geo, prov = _load()
    feats = geo["features"]
    assert len(feats) == len(prov) == 82
    psgcs = {f["properties"]["psgc"] for f in feats}
    assert psgcs == set(prov), "geojson PSGC set must equal the 82-unit model exactly"


def test_every_polygon_name_matches_a_chart_unit() -> None:
    """ECharts matches chart data to a polygon by properties.name; a mismatch
    silently drops the province from the map."""
    geo, prov = _load()
    data_names = {v["name"] for v in prov.values()}
    geo_names = {f["properties"]["name"] for f in geo["features"]}
    assert geo_names == data_names


def test_ncr_dissolved_and_maguindanao_unified() -> None:
    geo, _ = _load()
    psgcs = {f["properties"]["psgc"] for f in geo["features"]}
    assert "130000000" in psgcs, "NCR must be present as one dissolved unit"
    assert "153800000" in psgcs, "Maguindanao must be present (pre-2022 unified unit)"
    # NCR appears exactly once (not 4 districts)
    ncr = [f for f in geo["features"] if f["properties"]["psgc"] == "130000000"]
    assert len(ncr) == 1
    assert geo["_meta"]["historical_geometry_version"] == "2019-adm2-pre-2022-maguindanao"


def test_geometry_is_valid_polygonal() -> None:
    geo, _ = _load()
    for f in geo["features"]:
        gtype = f["geometry"]["type"]
        assert gtype in ("Polygon", "MultiPolygon"), f"{f['properties']['name']}: {gtype}"
        assert f["geometry"]["coordinates"], f"{f['properties']['name']} has empty geometry"


def test_geojson_builder_declares_the_shared_historical_geometry_contract(monkeypatch) -> None:
    monkeypatch.setattr(
        geojson_build,
        "load_provinces",
        lambda: {
            "130000000": {"name": "Metro Manila", "island_group": "ncr"},
        },
    )
    monkeypatch.setattr(geojson_build, "_fetch_region", lambda _region: {"features": []})

    built = geojson_build.build_province_geojson()

    assert built["_meta"]["historical_geometry_version"] == "2019-adm2-pre-2022-maguindanao"
