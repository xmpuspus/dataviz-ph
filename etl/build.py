"""Orchestrator. Pulls every source, writes public/data/*.json."""

from __future__ import annotations

import json
from pathlib import Path

from etl import interpolate, philgeps, psa_openstat, validate
from etl.psgc import huc_parent, load_provinces, normalize_name

PUBLIC_DATA = Path(__file__).resolve().parent.parent / "public" / "data"

PANEL_YEARS = list(range(2014, 2025))  # 2014-2024 inclusive
POVERTY_ANCHORS = [2018, 2021, 2023]
GDP_PANEL_YEARS = [2022, 2023, 2024]
GDP_ANCHORS = [2022, 2023, 2024]  # PSA publishes all three; no interpolation needed


def write_json(name: str, payload: object) -> None:
    PUBLIC_DATA.mkdir(parents=True, exist_ok=True)
    out = PUBLIC_DATA / name
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {out.relative_to(PUBLIC_DATA.parent.parent)}  ({out.stat().st_size:,} bytes)")


def main() -> None:
    print(">> load provinces")
    provinces = load_provinces()

    print(">> fetch 2020 population (with HUC rollup into parent provinces)")
    population = psa_openstat.fetch_population_2020(
        provinces, normalize_name, huc_parent=huc_parent
    )
    validate.validate_all(population, schema="population")
    pop_by_psgc = {r["psgc"]: r["value"] for r in population}
    total_pop = sum(pop_by_psgc.values())
    print(f"   total covered pop: {total_pop:,} (PSA 2020 Census national = ~109,033,245)")

    print(">> fetch poverty (PSA 1E/FY 1a, anchors 2018/2021/2023)")
    poverty_anchors = psa_openstat.fetch_poverty(provinces, normalize_name)
    validate.validate_all(poverty_anchors, schema="rate_pct")

    print(">> fetch DPWH spend (PhilGEPS, 15 chunks)")
    dpwh_spend = philgeps.fetch_dpwh_spend(provinces, normalize_name, pop_by_psgc)
    validate.validate_all(dpwh_spend, schema="peso_per_capita")

    print(">> fetch all PhilGEPS spend (no agency filter)")
    all_spend = philgeps.fetch_all_spend(provinces, normalize_name, pop_by_psgc)
    validate.validate_all(all_spend, schema="peso_per_capita")

    print(">> fetch CPI annual averages (PSA 2M/PI/CPI, PHILIPPINES national)")
    cpi = psa_openstat.fetch_cpi_annual()
    cpi_2018 = cpi.get(2018, 100.0)
    if cpi_2018 <= 0:
        raise RuntimeError("CPI 2018 base is zero, refusing to deflate")
    deflators = {y: cpi_2018 / cpi[y] for y in cpi if cpi[y] > 0}

    for series in (dpwh_spend, all_spend):
        for r in series:
            d = deflators.get(r["year"])
            r["value_real"] = (r["value"] * d) if d is not None else None

    print(">> fetch GDP per capita (PSA 2A/PPA, constant 2018 prices, 2022-2024)")
    gdp = psa_openstat.fetch_gdp_per_capita(provinces, normalize_name)
    validate.validate_all(gdp, schema="peso_per_capita_gdp")

    print(">> linear-fill poverty across 2014-2024")
    poverty = interpolate.linear_fill(
        poverty_anchors, anchor_years=POVERTY_ANCHORS, target_years=PANEL_YEARS
    )
    validate.validate_all(poverty, schema="rate_pct")

    # provinces.json enriched with population
    provinces_out = {
        code: {**info, "population_2020": pop_by_psgc.get(code, 0)}
        for code, info in provinces.items()
    }
    write_json("provinces.json", provinces_out)
    write_json("poverty.json", poverty)
    write_json("dpwh_spend_per_capita.json", dpwh_spend)
    write_json("all_spend_per_capita.json", all_spend)
    write_json("gdp_per_capita.json", gdp)
    write_json("cpi.json", {str(y): v for y, v in cpi.items()})

    indicators = [
        {
            "id": "poverty",
            "name": "Poverty incidence among families",
            "unit": "%",
            "source": "PSA OpenStat 1E/FY Table 1a",
            "vintage": (
                "PSA Full-Year anchors at 2018, 2021, 2023. Years between anchors are "
                "linearly interpolated; years before 2018 and after 2023 hold constant. "
                "Maguindanao published as the pre-2022-split unit. NCR is the regional "
                "aggregate (not a province)."
            ),
            "log_natural": False,
            "panel_years": PANEL_YEARS,
            "anchor_years": POVERTY_ANCHORS,
        },
        {
            "id": "dpwh_spend_per_capita",
            "name": "DPWH spend per capita",
            "unit": "PHP per person per year",
            "source": "PhilGEPS awards (DPWH subset) / PSA 2020 Census population",
            "vintage": (
                "DPWH-tagged awards 2014-2024, summed per province per year, divided "
                "by 2020 Census whole-province population (HUCs rolled into parents). "
                "About 20% of DPWH award value (multi-province + null area_of_delivery + "
                "'Independent City' bucket) cannot be attributed to a single province and "
                "is excluded. Province-years with implausibly low per-capita "
                f"(< PHP {philgeps.MIN_PESO_PER_CAPITA:.0f}/cap) are dropped as coverage gaps. "
                "Each row carries both 'value' (nominal PHP) and 'value_real' "
                "(deflated to PHP 2018 using PSA national CPI annual averages)."
            ),
            "log_natural": True,
            "panel_years": PANEL_YEARS,
            "anchor_years": PANEL_YEARS,
            "can_deflate": True,
        },
        {
            "id": "all_spend_per_capita",
            "name": "All PhilGEPS spend per capita",
            "unit": "PHP per person per year",
            "source": "PhilGEPS awards (all agencies) / PSA 2020 Census population",
            "vintage": (
                "Every PhilGEPS award 2014-2024 attributable to a single province via "
                "area_of_delivery, summed and divided by 2020 Census whole-province "
                "population. About 20% of total award value carries no usable province "
                "tag and is excluded. Each row carries both 'value' (nominal PHP) and "
                "'value_real' (deflated to PHP 2018 using PSA national CPI)."
            ),
            "log_natural": True,
            "panel_years": PANEL_YEARS,
            "anchor_years": PANEL_YEARS,
            "can_deflate": True,
        },
        {
            "id": "gdp_per_capita",
            "name": "Per capita GDP",
            "unit": "PHP per person per year (constant 2018 prices)",
            "source": "PSA OpenStat 2A/PPA/2025 Table 9 (Per Capita GDP, constant 2018 prices)",
            "vintage": (
                "PSA province-level estimates, 2022, 2023, 2024. Already at constant 2018 "
                "prices. HUCs are published as separate rows (Cebu City, Davao City, etc.) "
                "and are NOT rolled into parent provinces here; the chart uses PSA's "
                "province-level value as published, so Cebu shows province-without-HUC "
                "GDP per capita. Maguindanao is split into del Norte / del Sur since 2022 "
                "and is omitted here for consistency with the rest of the panel."
            ),
            "log_natural": True,
            "panel_years": GDP_PANEL_YEARS,
            "anchor_years": GDP_ANCHORS,
            "can_deflate": False,
        },
    ]
    write_json("indicators.json", indicators)

    stories = [
        {
            "id": "spend-vs-poverty",
            "tab_label": "DPWH vs poverty",
            "headline": "Twelve years, two trillion in roads. Did poverty move?",
            "tagline": (
                "DPWH spend per capita against poverty incidence. 82 provinces. "
                "2014 to 2024. Hit play, or use the arrow keys."
            ),
            "x": "dpwh_spend_per_capita",
            "y": "poverty",
            "size": "population_2020",
            "panel_years": PANEL_YEARS,
            "default_year": 2018,
            "default_log_x": True,
        },
        {
            "id": "all-spend-vs-poverty",
            "tab_label": "All gov vs poverty",
            "headline": "All government spending. Does it reach the poor?",
            "tagline": (
                "Every province-attributable PhilGEPS contract per capita against "
                "poverty incidence. 82 provinces, 2014 to 2024. Scrub the years."
            ),
            "x": "all_spend_per_capita",
            "y": "poverty",
            "size": "population_2020",
            "panel_years": PANEL_YEARS,
            "default_year": 2018,
            "default_log_x": True,
        },
        {
            "id": "gdp-vs-poverty",
            "tab_label": "GDP vs poverty",
            "headline": "Wealthier provinces, lower poverty?",
            "tagline": (
                "Per capita GDP (constant 2018 PHP) against poverty incidence. "
                "Three years of PSA province data, 2022 to 2024."
            ),
            "x": "gdp_per_capita",
            "y": "poverty",
            "size": "population_2020",
            "panel_years": GDP_PANEL_YEARS,
            "default_year": 2023,
            "default_log_x": True,
        },
    ]
    write_json("stories.json", stories)

    # quick coverage summary
    print()
    print("summary:")
    print(f"  provinces: {len(provinces_out)}")
    print(f"  poverty rows: {len(poverty)}  (interp={sum(1 for r in poverty if r['interp'])})")
    print(f"  dpwh spend rows: {len(dpwh_spend)}")
    print(f"  all spend rows: {len(all_spend)}")
    print(f"  gdp per capita rows: {len(gdp)}")
    print(f"  cpi years: {len(cpi)}")
    print(f"  stories: {len(stories)}")


if __name__ == "__main__":
    main()
