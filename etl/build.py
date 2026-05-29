"""Orchestrator. Pulls every source, writes public/data/*.json."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from etl import interpolate, philgeps, psa_openstat, validate
from etl.psgc import huc_parent, load_provinces, normalize_name

PUBLIC_DATA = Path(__file__).resolve().parent.parent / "public" / "data"

PANEL_YEARS = list(range(2014, 2025))  # 2014-2024 inclusive
POVERTY_ANCHORS = [2018, 2021, 2023]
GDP_PANEL_YEARS = [2022, 2023, 2024]
GDP_ANCHORS = [2022, 2023, 2024]  # PSA publishes all three; no interpolation needed
CPI_YOY_YEARS = list(range(2019, 2026))  # need year-prior so series starts at 2019


def compute_dpwh_share(dpwh_spend: list[dict], all_spend: list[dict]) -> list[dict]:
    """Per (province, year), DPWH share of all PhilGEPS spend, in percent."""
    dpwh_by = {(r["psgc"], r["year"]): r["value"] for r in dpwh_spend}
    all_by = {(r["psgc"], r["year"]): r["value"] for r in all_spend}
    out = []
    for key, all_v in all_by.items():
        dpwh_v = dpwh_by.get(key)
        if dpwh_v is None or all_v <= 0:
            continue
        pct = (dpwh_v / all_v) * 100
        if pct > 100:  # numerical safety
            pct = 100.0
        out.append({"psgc": key[0], "year": key[1], "value": pct})
    return out


def compute_cpi_yoy(cpi: dict[int, float]) -> list[dict]:
    """National year-on-year inflation. Returns one row per year with psgc='000000000'."""
    years = sorted(cpi.keys())
    out = []
    for i in range(1, len(years)):
        y, prev = years[i], years[i - 1]
        if y - prev != 1:
            continue
        if cpi[prev] <= 0:
            continue
        pct = (cpi[y] - cpi[prev]) / cpi[prev] * 100
        out.append({"psgc": "000000000", "year": y, "value": pct})
    return out


def compute_poverty_change(poverty_anchors: list[dict]) -> list[dict]:
    """Per province, 2023 minus 2018 poverty incidence in percentage points.

    Emitted as one row per (province, year) for every panel year so the picker
    can plot it as a constant Y while X scrubs through years.
    """
    by = {}
    for r in poverty_anchors:
        by.setdefault(r["psgc"], {})[int(r["year"])] = r["value"]
    out = []
    for psgc, by_year in by.items():
        if 2018 not in by_year or 2023 not in by_year:
            continue
        change = by_year[2023] - by_year[2018]
        for year in PANEL_YEARS:
            out.append({"psgc": psgc, "year": year, "value": change})
    return out


def expand_population(provinces_with_pop: dict, panel_years: list[int]) -> list[dict]:
    """Emit one row per (province, year) with the 2020 Census population.

    Population is a snapshot, but for the picker we repeat the value across the
    panel so it can pair with year-varying X indicators on the bubble chart.
    """
    out = []
    for psgc, info in provinces_with_pop.items():
        pop = info.get("population_2020")
        if not pop:
            continue
        for year in panel_years:
            out.append({"psgc": psgc, "year": year, "value": pop})
    return out


def _rank(vals: list[float]) -> list[float]:
    """Fractional (average-of-ties) ranks, 1-based. Used for Spearman's rho."""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(vals):
        j = i
        while j + 1 < len(vals) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson correlation. None if fewer than 3 points or a degenerate axis."""
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=False))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / ((sxx * syy) ** 0.5)


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """Rank correlation: robust to the log/linear axis choice and to outliers."""
    if len(xs) < 3:
        return None
    return _pearson(_rank(xs), _rank(ys))


def _strength_word(rho: float) -> str:
    a = abs(rho)
    if a < 0.2:
        return "no clear"
    if a < 0.4:
        return "a weak"
    if a < 0.6:
        return "a moderate"
    if a < 0.8:
        return "a strong"
    return "a very strong"


def _median(vals: list[float]) -> float:
    s = sorted(vals)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def compute_story_finding(
    story: dict, value_index: dict[str, dict[tuple[str, int], float]]
) -> dict:
    """Compute a data-grounded answer to the story's question at its default year.

    Returns a finding dict with the Spearman rank correlation (robust to the
    log axis the chart uses), the Pearson r, the province count, the actual year
    used, and an off-diagonal quadrant count. The human sentence is assembled
    here from the computed numbers (never hand-typed) so it can never drift from
    the data. Every finding carries the correlation-not-causation caveat.
    """
    xid, yid, year = story["x"], story["y"], story["default_year"]
    xmap = value_index.get(xid, {})
    ymap = value_index.get(yid, {})
    pairs = [
        (xmap[(psgc, yr)], ymap[(psgc, yr)])
        for (psgc, yr) in xmap
        if yr == year and (psgc, yr) in ymap
    ]
    n = len(pairs)
    if n < 3:
        return {"available": False, "year": year, "n": n}
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    rho = _spearman(xs, ys)
    r = _pearson(xs, ys)
    # Off-diagonal count: provinces above the median on BOTH axes.
    mx, my = _median(xs), _median(ys)
    both_high = sum(1 for x, y in pairs if x > mx and y > my)

    yname = {
        "poverty": "poverty incidence",
        "gdp_per_capita": "per-capita GDP",
        "subsistence_incidence": "subsistence incidence",
    }.get(yid, yid.replace("_", " "))
    xname = {
        "dpwh_spend_per_capita": "DPWH spend per capita",
        "all_spend_per_capita": "all-government spend per capita",
        "gdp_per_capita": "per-capita GDP",
    }.get(xid, xid.replace("_", " "))

    direction = "negative" if (rho is not None and rho < 0) else "positive"
    strength = _strength_word(rho) if rho is not None else "no measurable"
    rho_txt = f"{rho:+.2f}" if rho is not None else "n/a"
    sentence = (
        f"In {year}, across {n} provinces, the rank correlation between {xname} and "
        f"{yname} is rho = {rho_txt}, showing {strength} {direction} link. "
        f"{both_high} of {n} provinces sat above the median on both axes."
    )
    award_ids = {
        "dpwh_spend_per_capita",
        "all_spend_per_capita",
        "doh_spend_per_capita",
        "infra_spend_per_capita",
    }
    caveat = "Correlation, not causation."
    if xid in award_ids or yid in award_ids:
        caveat += (
            " Spend here is PhilGEPS contract awards (money committed), not "
            "verified disbursement or built outcomes."
        )
    return {
        "available": True,
        "year": year,
        "n": n,
        "spearman": round(rho, 3) if rho is not None else None,
        "pearson": round(r, 3) if r is not None else None,
        "both_above_median": both_high,
        "sentence": sentence,
        "caveat": caveat,
    }


def write_json(name: str, payload: object) -> None:
    PUBLIC_DATA.mkdir(parents=True, exist_ok=True)
    out = PUBLIC_DATA / name
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {out.relative_to(PUBLIC_DATA.parent.parent)}  ({out.stat().st_size:,} bytes)")


def main(no_cache: bool = False) -> None:
    if no_cache:
        psa_openstat.clear_cache()
        from etl import psgc as _psgc

        _psgc.clear_cache()
        print(">> --no-cache: cleared PSA + PSGC caches")

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

    print(">> fetch poverty (PSA 1E/FY 1a, anchors 2018/2021/2023, with 95% CI)")
    poverty_anchors = psa_openstat.fetch_poverty(provinces, normalize_name)
    validate.validate_all(poverty_anchors, schema="rate_pct")
    validate.validate_precision(poverty_anchors)

    print(">> fetch subsistence incidence (PSA 1E/FY 3a, anchors 2018/2021/2023, with 95% CI)")
    subsistence_anchors = psa_openstat.fetch_subsistence(provinces, normalize_name)
    validate.validate_all(subsistence_anchors, schema="rate_pct")
    validate.validate_precision(subsistence_anchors)

    print(">> fetch DPWH spend (PhilGEPS, 15 chunks)")
    dpwh_spend = philgeps.fetch_dpwh_spend(provinces, normalize_name, pop_by_psgc)
    validate.validate_all(dpwh_spend, schema="peso_per_capita")

    print(">> fetch all PhilGEPS spend (no agency filter)")
    all_spend = philgeps.fetch_all_spend(provinces, normalize_name, pop_by_psgc)
    validate.validate_all(all_spend, schema="peso_per_capita")

    print(">> fetch DOH spend (PhilGEPS, org=DOH)")
    doh_spend = philgeps.fetch_doh_spend(provinces, normalize_name, pop_by_psgc)
    validate.validate_all(doh_spend, schema="peso_per_capita")

    print(">> fetch infra-only spend (PhilGEPS, construction/road/bridge/etc.)")
    infra_spend = philgeps.fetch_infra_spend(provinces, normalize_name, pop_by_psgc)
    validate.validate_all(infra_spend, schema="peso_per_capita")

    print(">> fetch CPI annual averages (PSA 2M/PI/CPI, PHILIPPINES national)")
    cpi = psa_openstat.fetch_cpi_annual()
    cpi_2018 = cpi.get(2018, 100.0)
    if cpi_2018 <= 0:
        raise RuntimeError("CPI 2018 base is zero, refusing to deflate")
    deflators = {y: cpi_2018 / cpi[y] for y in cpi if cpi[y] > 0}

    for series in (dpwh_spend, all_spend, doh_spend, infra_spend):
        for r in series:
            d = deflators.get(r["year"])
            r["value_real"] = (r["value"] * d) if d is not None else None

    print(">> fetch GDP per capita (PSA 2A/PPA, constant 2018 prices, 2022-2024)")
    gdp = psa_openstat.fetch_gdp_per_capita(provinces, normalize_name)
    validate.validate_all(gdp, schema="peso_per_capita_gdp")

    # Carry the 95% CI + CV through onto anchor years only (interpolated years are
    # model estimates, not survey estimates, so they carry no precision).
    precision_fields = ["cv", "se", "ci_lo", "ci_hi"]
    print(">> linear-fill poverty across 2014-2024 (CI carried on anchor years)")
    poverty = interpolate.linear_fill(
        poverty_anchors,
        anchor_years=POVERTY_ANCHORS,
        target_years=PANEL_YEARS,
        carry_fields=precision_fields,
    )
    validate.validate_all(poverty, schema="rate_pct")
    validate.validate_precision(poverty)

    print(">> linear-fill subsistence across 2014-2024 (CI carried on anchor years)")
    subsistence = interpolate.linear_fill(
        subsistence_anchors,
        anchor_years=POVERTY_ANCHORS,
        target_years=PANEL_YEARS,
        carry_fields=precision_fields,
    )
    validate.validate_all(subsistence, schema="rate_pct")
    validate.validate_precision(subsistence)

    # provinces.json enriched with population
    provinces_out = {
        code: {**info, "population_2020": pop_by_psgc.get(code, 0)}
        for code, info in provinces.items()
    }

    print(">> derive: DPWH share %, CPI YoY %, poverty change 2018-2023, population")
    dpwh_share = compute_dpwh_share(dpwh_spend, all_spend)
    validate.validate_all(dpwh_share, schema="share_pct")
    cpi_yoy = compute_cpi_yoy(cpi)
    validate.validate_all(cpi_yoy, schema="yoy_pct")
    poverty_change = compute_poverty_change(poverty_anchors)
    validate.validate_all(poverty_change, schema="delta_pp")
    population_series = expand_population(provinces_out, PANEL_YEARS)
    validate.validate_all(population_series, schema="population")

    write_json("provinces.json", provinces_out)
    write_json("poverty.json", poverty)
    write_json("subsistence.json", subsistence)
    write_json("dpwh_spend_per_capita.json", dpwh_spend)
    write_json("all_spend_per_capita.json", all_spend)
    write_json("doh_spend_per_capita.json", doh_spend)
    write_json("infra_spend_per_capita.json", infra_spend)
    write_json("gdp_per_capita.json", gdp)
    write_json("cpi.json", {str(y): v for y, v in cpi.items()})
    write_json("dpwh_share_pct.json", dpwh_share)
    write_json("cpi_yoy_pct.json", cpi_yoy)
    write_json("poverty_change_pp.json", poverty_change)
    write_json("population.json", population_series)

    indicators = [
        {
            "id": "poverty",
            "name": "Poverty incidence among families",
            "unit": "%",
            "source": "PSA OpenStat 1E/FY Table 1a",
            "source_url": (
                "https://openstat.psa.gov.ph/PXWeb/pxweb/en/DB/DB__1E__FY/"
            ),
            "definition": (
                "Share of families whose per-capita income falls below the official "
                "poverty threshold for their province, as published by the PSA in Table "
                "1a of the Full-Year Official Poverty Statistics."
            ),
            "vintage": (
                "PSA Full-Year anchors at 2018, 2021, 2023. Years between anchors are "
                "linearly interpolated; years before 2018 and after 2023 hold constant. "
                "Maguindanao published as the pre-2022-split unit. NCR is the regional "
                "aggregate (not a province). Each survey year carries PSA's published "
                "95% confidence interval and coefficient of variation; estimates with "
                "CV above 30% are flagged as imprecise."
            ),
            "log_natural": False,
            "panel_years": PANEL_YEARS,
            "anchor_years": POVERTY_ANCHORS,
            "has_ci": True,
            "cv_unreliable_above": 30,
        },
        {
            "id": "subsistence_incidence",
            "name": "Subsistence incidence among families",
            "unit": "%",
            "source": "PSA OpenStat 1E/FY Table 3a",
            "source_url": (
                "https://openstat.psa.gov.ph/PXWeb/pxweb/en/DB/DB__1E__FY/"
            ),
            "definition": (
                "Share of families whose per-capita income falls below the official "
                "food (subsistence) threshold for their province, as published by the "
                "PSA in Table 3a of the Full-Year Official Poverty Statistics. These "
                "are families who cannot afford even a basic nutritionally-adequate "
                "diet, so the rate is always lower than poverty incidence."
            ),
            "vintage": (
                "PSA Full-Year anchors at 2018, 2021, 2023. Years between anchors are "
                "linearly interpolated; years before 2018 and after 2023 hold constant. "
                "Maguindanao published as the pre-2022-split unit. NCR is the regional "
                "aggregate (not a province). Each survey year carries PSA's published "
                "95% confidence interval and coefficient of variation; estimates with "
                "CV above 30% are flagged as imprecise."
            ),
            "log_natural": False,
            "panel_years": PANEL_YEARS,
            "anchor_years": POVERTY_ANCHORS,
            "has_ci": True,
            "cv_unreliable_above": 30,
        },
        {
            "id": "dpwh_spend_per_capita",
            "name": "DPWH spend per capita",
            "unit": "PHP per person per year",
            "source": "PhilGEPS awards (DPWH subset) / PSA 2020 Census population",
            "source_url": "https://github.com/csiiiv/philgeps-awards-dashboard",
            "definition": (
                "Sum of every PhilGEPS contract awarded to the Department of Public "
                "Works and Highways that can be tied to a single province, divided by "
                "the 2020 Census whole-province population (HUCs rolled in)."
            ),
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
            "source_url": "https://github.com/csiiiv/philgeps-awards-dashboard",
            "definition": (
                "Sum of every PhilGEPS award (any agency) attributable to a single "
                "province via area_of_delivery, divided by the 2020 Census "
                "whole-province population. Excludes about 20 percent of total award "
                "value where no province tag is available."
            ),
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
            "source_url": (
                "https://openstat.psa.gov.ph/PXWeb/pxweb/en/DB/DB__2A__PPA/"
            ),
            "definition": (
                "Total provincial economic output divided by population, expressed in "
                "PHP at constant 2018 prices so values across years are directly "
                "comparable without an inflation adjustment."
            ),
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
            "coverage_label": "PSA publishes provincial GDP from 2022 only (3 years).",
        },
        {
            "id": "doh_spend_per_capita",
            "name": "DOH spend per capita",
            "unit": "PHP per person per year",
            "source": "PhilGEPS awards (Department of Health subset) / PSA 2020 Census",
            "source_url": "https://github.com/csiiiv/philgeps-awards-dashboard",
            "definition": (
                "Sum of every PhilGEPS contract awarded to the Department of Health "
                "that can be tied to a single province, divided by the 2020 Census "
                "whole-province population."
            ),
            "vintage": (
                "DOH-tagged awards 2014-2024, summed per province per year. Coverage "
                "varies sharply year-on-year because DOH centralizes many procurements "
                "and tags area_of_delivery inconsistently."
            ),
            "log_natural": True,
            "panel_years": PANEL_YEARS,
            "anchor_years": PANEL_YEARS,
            "can_deflate": True,
        },
        {
            "id": "infra_spend_per_capita",
            "name": "Infra spend per capita",
            "unit": "PHP per person per year",
            "source": "PhilGEPS awards (construction/road/bridge/flood keywords) / PSA Census",
            "source_url": "https://github.com/csiiiv/philgeps-awards-dashboard",
            "definition": (
                "Sum of every PhilGEPS contract whose title or category contains an "
                "infra keyword (construction, road, bridge, flood, drainage, highway, "
                "concreting, asphalt, rehabilitation), per capita."
            ),
            "vintage": (
                "Free-text keyword filter on award_title, notice_title, and "
                "business_category, 2014-2024. Picks up LGU-executed infra that DPWH "
                "alone misses. Some over-tagging on rehabilitation (e.g. IT systems) "
                "is unavoidable."
            ),
            "log_natural": True,
            "panel_years": PANEL_YEARS,
            "anchor_years": PANEL_YEARS,
            "can_deflate": True,
        },
        {
            "id": "dpwh_share_pct",
            "name": "DPWH share of all spend",
            "unit": "%",
            "source": "Derived from dpwh_spend_per_capita / all_spend_per_capita",
            "source_url": "",
            "definition": (
                "Percent of total province-attributable PhilGEPS spend that came from "
                "DPWH in a given year. High share means roads dominate the contracting "
                "mix in that province."
            ),
            "vintage": (
                "Computed as dpwh / all_spend * 100 per province per year. Capped at "
                "100 percent for numerical safety."
            ),
            "log_natural": False,
            "panel_years": PANEL_YEARS,
            "anchor_years": PANEL_YEARS,
        },
        {
            "id": "population",
            "name": "Population (2020 Census)",
            "unit": "people",
            "source": "PSA 2020 Census of Population and Housing",
            "source_url": "https://psa.gov.ph/population-and-housing",
            "definition": (
                "Whole-province population from the 2020 Census, including Highly "
                "Urbanized Cities rolled into their geographic parent province. NCR "
                "is reported as the regional aggregate (16 cities)."
            ),
            "vintage": (
                "Single 2020 snapshot, repeated across the panel so the picker can "
                "plot it against year-varying indicators."
            ),
            "log_natural": True,
            "panel_years": PANEL_YEARS,
            "anchor_years": [2020],
            "static_snapshot": True,
            "snapshot_label": "2020 Census only. Does not vary by year.",
        },
        {
            "id": "poverty_change_pp",
            "name": "Poverty change 2018 to 2023 (pp)",
            "unit": "percentage points",
            "source": "Derived from PSA 1E/FY Table 1a anchors",
            "source_url": (
                "https://openstat.psa.gov.ph/PXWeb/pxweb/en/DB/DB__1E__FY/"
            ),
            "definition": (
                "Province-level poverty incidence in 2023 minus the same measure in "
                "2018. Negative means poverty fell. Constant across panel years."
            ),
            "vintage": (
                "Computed once from the two PSA anchor years, then repeated across "
                "the panel so it pairs with year-varying X indicators."
            ),
            "log_natural": False,
            "panel_years": PANEL_YEARS,
            "anchor_years": [2018, 2023],
            "static_snapshot": True,
            "snapshot_label": "2018-to-2023 change. A single value, not a yearly series.",
        },
        {
            "id": "cpi_yoy_pct",
            "name": "National inflation (CPI year-on-year)",
            "unit": "%",
            "source": "Derived from PSA OpenStat 2M/PI/CPI/2018NEW",
            "source_url": (
                "https://openstat.psa.gov.ph/PXWeb/pxweb/en/DB/DB__2M__PI__CPI/"
            ),
            "definition": (
                "Year-on-year change in the national CPI all-items index, 2018=100. "
                "National series only; the bubble picker hides this indicator because "
                "it has no per-province variation."
            ),
            "vintage": (
                "(cpi_year - cpi_prev) / cpi_prev * 100. Series starts in 2019 since "
                "year-prior is required."
            ),
            "log_natural": False,
            "panel_years": CPI_YOY_YEARS,
            "anchor_years": CPI_YOY_YEARS,
            "national_only": True,
        },
    ]
    write_json("indicators.json", indicators)

    # Awards != disbursement. PhilGEPS publishes contract awards (money committed),
    # not cash actually paid. Province-grain disbursement is not published anywhere
    # public (COA's annual reports are PDF-only and tag the disbursing office, not
    # the project's province; DPWH's portal carries awards, not cash). So awards are
    # the closest available proxy, and every spend story says so up front.
    AWARDS_CAVEAT = (
        "Reads as contract awards, not cash spent. PhilGEPS publishes the value of "
        "contracts awarded, which can sit unexecuted for years; actual disbursement "
        "by province is not published anywhere public. Treat this as money committed, "
        "not money proven spent or built."
    )

    stories = [
        {
            "id": "spend-vs-poverty",
            "tab_label": "DPWH vs poverty",
            "headline": "Eleven years, five trillion in roads. Did poverty improve?",
            "tagline": (
                "DPWH spend per capita against poverty incidence. 82 provinces. "
                "2014 to 2024. Hit play, or use the arrow keys."
            ),
            "why": (
                "DPWH is the single largest PhilGEPS-tracked spend by agency. If "
                "infrastructure money tracks poverty reduction at all, this is where "
                "to look first. The chart shows you whether provinces with higher "
                "per-capita road spend also moved lower on the poverty axis."
            ),
            "source_url": "https://github.com/csiiiv/philgeps-awards-dashboard",
            "x": "dpwh_spend_per_capita",
            "y": "poverty",
            "size": "population_2020",
            "panel_years": PANEL_YEARS,
            "default_year": 2018,
            "default_log_x": True,
            "awards_caveat": AWARDS_CAVEAT,
        },
        {
            "id": "all-spend-vs-poverty",
            "tab_label": "All gov vs poverty",
            "headline": "All government spending. Does it reach the poor?",
            "tagline": (
                "Every province-attributable PhilGEPS contract per capita against "
                "poverty incidence. 82 provinces, 2014 to 2024. Scrub the years."
            ),
            "why": (
                "DPWH alone is too narrow. This widens the lens to every PhilGEPS "
                "contract any agency awarded that could be tied to a province via "
                "area_of_delivery. About 20 percent of total award value carries no "
                "usable province tag and is excluded; what remains is the broadest "
                "per-capita procurement picture available."
            ),
            "source_url": "https://github.com/csiiiv/philgeps-awards-dashboard",
            "x": "all_spend_per_capita",
            "y": "poverty",
            "size": "population_2020",
            "panel_years": PANEL_YEARS,
            "default_year": 2018,
            "default_log_x": True,
            "awards_caveat": AWARDS_CAVEAT,
        },
        {
            "id": "all-spend-vs-gdp",
            "tab_label": "Spend vs GDP",
            "headline": "Does spending follow wealth, or chase poverty?",
            "tagline": (
                "82 provinces. 2022 to 2024. All government contracts per capita "
                "against per-capita GDP."
            ),
            "why": (
                "DPWH-vs-poverty and all-spend-vs-poverty both ask whether money "
                "follows poverty. This one asks the opposite: does procurement "
                "spend follow wealth? If the cloud tilts upward, procurement money "
                "lands where GDP already is. If it tilts downward, it lands where "
                "GDP isn't. Both indicators are already shipped, so this preset "
                "needed zero new data."
            ),
            "source_url": (
                "https://openstat.psa.gov.ph/PXWeb/pxweb/en/DB/DB__2A__PPA/"
            ),
            "x": "all_spend_per_capita",
            "y": "gdp_per_capita",
            "size": "population_2020",
            "panel_years": GDP_PANEL_YEARS,
            "default_year": 2023,
            "default_log_x": True,
            "awards_caveat": AWARDS_CAVEAT,
        },
        {
            "id": "gdp-vs-poverty",
            "tab_label": "GDP vs poverty",
            "headline": "Wealthier provinces, lower poverty?",
            "tagline": (
                "Per capita GDP (constant 2018 PHP) against poverty incidence. "
                "Three years of PSA province data, 2022 to 2024."
            ),
            "why": (
                "GDP per capita is the headline wealth measure. Pairing it with "
                "poverty incidence shows whether the two are negatively correlated "
                "across provinces in the way most economic theory predicts, and where "
                "the outliers sit. PSA only publishes provincial per-capita GDP from "
                "2022 onward, so the panel is short."
            ),
            "source_url": "https://openstat.psa.gov.ph/PXWeb/pxweb/en/DB/DB__2A__PPA/",
            "x": "gdp_per_capita",
            "y": "poverty",
            "size": "population_2020",
            "panel_years": GDP_PANEL_YEARS,
            "default_year": 2023,
            "default_log_x": True,
        },
    ]
    # Compute a data-grounded finding per story so each question gets an answer
    # on the page, not just a chart. Built from the same series the chart plots.
    value_index = {
        "poverty": {(r["psgc"], r["year"]): r["value"] for r in poverty},
        "subsistence_incidence": {(r["psgc"], r["year"]): r["value"] for r in subsistence},
        "dpwh_spend_per_capita": {(r["psgc"], r["year"]): r["value"] for r in dpwh_spend},
        "all_spend_per_capita": {(r["psgc"], r["year"]): r["value"] for r in all_spend},
        "doh_spend_per_capita": {(r["psgc"], r["year"]): r["value"] for r in doh_spend},
        "infra_spend_per_capita": {(r["psgc"], r["year"]): r["value"] for r in infra_spend},
        "gdp_per_capita": {(r["psgc"], r["year"]): r["value"] for r in gdp},
    }
    for s in stories:
        s["finding"] = compute_story_finding(s, value_index)
    write_json("stories.json", stories)

    # Write manifest LAST so its sha256 covers every freshly-written file.
    dpwh_total = compute_dpwh_attributed_total(dpwh_spend, pop_by_psgc)
    manifest = build_manifest(
        derived={
            "dpwh_attributed_php_total": dpwh_total,
            "dpwh_attributed_php_note": (
                "Sum of per-capita DPWH spend x 2020 province population over the "
                "attributed subset the chart plots (2014-2024, 11 years). About 20% of "
                "DPWH award value is unattributable and excluded, so this is a floor. "
                "Rounds to ~5 trillion PHP nominal; cited by the DPWH story headline."
            ),
        },
        row_counts={
            "provinces": len(provinces_out),
            "poverty": len(poverty),
            "subsistence": len(subsistence),
            "dpwh_spend_per_capita": len(dpwh_spend),
            "all_spend_per_capita": len(all_spend),
            "doh_spend_per_capita": len(doh_spend),
            "infra_spend_per_capita": len(infra_spend),
            "gdp_per_capita": len(gdp),
            "cpi": len(cpi),
            "dpwh_share_pct": len(dpwh_share),
            "cpi_yoy_pct": len(cpi_yoy),
            "poverty_change_pp": len(poverty_change),
            "population": len(population_series),
            "stories": len(stories),
            "indicators": len(indicators),
        },
    )
    write_json("manifest.json", manifest)

    # quick coverage summary
    print()
    print("summary:")
    print(f"  provinces: {len(provinces_out)}")
    print(f"  poverty rows: {len(poverty)}  (interp={sum(1 for r in poverty if r['interp'])})")
    print(f"  dpwh spend rows: {len(dpwh_spend)}")
    print(f"  all spend rows: {len(all_spend)}")
    print(f"  doh spend rows: {len(doh_spend)}")
    print(f"  infra spend rows: {len(infra_spend)}")
    print(f"  gdp per capita rows: {len(gdp)}")
    print(f"  cpi years: {len(cpi)}")
    print(f"  dpwh share rows: {len(dpwh_share)}")
    print(f"  cpi yoy rows: {len(cpi_yoy)}")
    print(f"  poverty change rows: {len(poverty_change)}")
    print(f"  population rows: {len(population_series)}")
    print(f"  stories: {len(stories)}")
    print(f"  indicators: {len(indicators)}")
    print(f"  manifest built_at: {manifest['built_at']}")


def compute_dpwh_attributed_total(
    dpwh_spend: list[dict], pop_by_psgc: dict[str, int]
) -> int:
    """Reconstruct the attributed DPWH peso total the chart actually plots.

    Each dpwh row is per-capita PHP; multiply by the province population and sum.
    This is the figure the DPWH headline cites, so it is computed here (not
    hardcoded as prose) and stored in the manifest as the citable source.
    """
    total = 0.0
    for r in dpwh_spend:
        pc = r.get("value")
        pop = pop_by_psgc.get(r["psgc"])
        if pc is None or pop is None:
            continue
        total += pc * pop
    return round(total)


def build_manifest(row_counts: dict[str, int], derived: dict | None = None) -> dict:
    """Build a manifest of every JSON in public/data/ with sha256 + row count.

    Run AFTER all data files are written. Excludes manifest.json itself.
    """
    files = sorted(p for p in PUBLIC_DATA.glob("*.json") if p.name != "manifest.json")
    sha = {}
    sizes = {}
    for f in files:
        h = hashlib.sha256(f.read_bytes()).hexdigest()
        sha[f.name] = h
        sizes[f.name] = f.stat().st_size
    return {
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source_vintages": {
            "poverty": "PSA OpenStat 1E/FY Table 1a, anchors 2018/2021/2023",
            "cpi": "PSA OpenStat 2M/PI/CPI/2018NEW, annual averages 2018-2025",
            "philgeps": "csiiiv/philgeps-awards-dashboard mirror, 2014-2024",
            "gdp_per_capita": "PSA OpenStat 2A/PPA/2025 Table 9, constant 2018 prices, 2022-2024",
            "population": "PSA 2020 Census of Population and Housing",
            "psgc": "psgc.gitlab.io community mirror",
        },
        "row_counts": row_counts,
        "derived": derived or {},
        "file_bytes": sizes,
        "sha256_per_file": sha,
    }


def _count_rows(payload: object) -> int:
    """Row count for a data file: list length, or dict keys minus self-doc."""
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        return len([k for k in payload if k != "_description"])
    return 1


def refresh_manifest() -> None:
    """Rebuild manifest.json from the files currently on disk. No network.

    Use after editing any editorial data file by hand (stories.json,
    pair_headlines.json) so the recorded sha256 + row counts stop drifting from
    what actually ships. This is the cheap counterpart to a full `main()` build.
    """
    files = sorted(p for p in PUBLIC_DATA.glob("*.json") if p.name != "manifest.json")
    row_counts = {}
    for f in files:
        try:
            row_counts[f.stem] = _count_rows(json.loads(f.read_text()))
        except json.JSONDecodeError:
            row_counts[f.stem] = -1

    derived = {}
    dpwh_path = PUBLIC_DATA / "dpwh_spend_per_capita.json"
    prov_path = PUBLIC_DATA / "provinces.json"
    if dpwh_path.exists() and prov_path.exists():
        dpwh = json.loads(dpwh_path.read_text())
        provinces = json.loads(prov_path.read_text())
        pop_by_psgc = {code: info.get("population_2020", 0) for code, info in provinces.items()}
        total = compute_dpwh_attributed_total(dpwh, pop_by_psgc)
        derived["dpwh_attributed_php_total"] = total
        derived["dpwh_attributed_php_note"] = (
            "Sum of per-capita DPWH spend x 2020 province population over the "
            "attributed subset the chart plots (2014-2024, 11 years). About 20% of "
            "DPWH award value is unattributable and excluded, so this is a floor. "
            "Rounds to ~5 trillion PHP nominal; cited by the DPWH story headline."
        )

    manifest = build_manifest(row_counts=row_counts, derived=derived)
    write_json("manifest.json", manifest)
    print(f"refreshed manifest over {len(files)} files; built_at {manifest['built_at']}")
    if derived:
        print(f"  dpwh attributed total: PHP {derived['dpwh_attributed_php_total']:,}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="plot.ph ETL build")
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignore on-disk caches; refetch every upstream source.",
    )
    parser.add_argument(
        "--manifest-only",
        action="store_true",
        help="Rebuild manifest.json from files on disk (no network). Use after "
        "hand-editing editorial data files so the manifest stops drifting.",
    )
    args = parser.parse_args()
    if args.manifest_only:
        refresh_manifest()
    else:
        main(no_cache=args.no_cache)
