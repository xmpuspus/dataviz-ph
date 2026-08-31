"""Orchestrator. Pulls every source, writes public/data/*.json."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from datetime import UTC, datetime
from pathlib import Path

from etl import build_share_pages, interpolate, philgeps, psa_inflation, psa_openstat, validate
from etl.psgc import huc_parent, load_provinces, normalize_name
from etl.source_catalog import PSA_TABLES

PUBLIC_DATA = Path(__file__).resolve().parent.parent / "public" / "data"

PANEL_YEARS = list(range(2014, 2025))  # 2014-2024 inclusive
POVERTY_ANCHORS = [2018, 2021, 2023]
# Regional story panel: poverty anchors that have a year-on-year inflation
# print (regional CPI is 2018-based, so YoY starts 2019; 2018 drops out).
REGION_STORY_YEARS = [2021, 2023]
GDP_PANEL_YEARS = list(range(2018, 2026))
GDP_ANCHORS = GDP_PANEL_YEARS
CPI_YOY_YEARS = list(range(2019, 2026))  # need year-prior so series starts at 2019

# Cumulative spend window for spend-vs-poverty-change preset.
# Uses nominal (not real) per-capita values to avoid CPI extrapolation before 2018.
# The caveat in the story makes the nominal-vs-real choice explicit.
CUM_SPEND_START = 2014
CUM_SPEND_END = 2023  # matches poverty_change 2018->2023 endpoint

# Permutation test parameters. Seeded for reproducibility across builds.
_PERM_N = 10_000
_PERM_SEED = 42


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


def poverty_depth_coverage(
    rows: list[dict],
    expected_units: int,
    units: set[str],
    *,
    missing_evidence: list[dict] | None = None,
    measures: tuple[str, ...] | None = None,
    years: tuple[int, ...] | None = None,
) -> list[dict]:
    """Describe published poverty-depth coverage without averaging unsafe values."""
    coverage = []
    evidence = missing_evidence or []
    for measure in measures or tuple(psa_openstat.POVERTY_DEPTH_MEASURES):
        for year in years or tuple(POVERTY_ANCHORS):
            observed = sum(row["measure"] == measure and row["year"] == year for row in rows)
            present = {
                row["psgc"] for row in rows if row["measure"] == measure and row["year"] == year
            }
            missing = sorted(units - present)
            evidence_by_psgc = {
                item["psgc"]: item
                for item in evidence
                if item["measure"] == measure and item["year"] == year and item["psgc"] in missing
            }
            revision_warnings = {
                row["psgc"]: row["source_revision_markers"]
                for row in rows
                if row["measure"] == measure
                and row["year"] == year
                and row.get("source_revision_markers")
            }
            source_warnings = {
                psgc: ["source_small_sample_warning"]
                for psgc, item in evidence_by_psgc.items()
                if item.get("source_small_sample_warning")
            }
            coverage.append(
                {
                    "measure": measure,
                    "year": year,
                    "expected_units": expected_units,
                    "observed_units": observed,
                    "status": "full" if observed == expected_units else "partial",
                    "missing_psgcs": missing,
                    "missing_status": "none"
                    if observed == expected_units
                    else "source_unavailable",
                    "missing_reasons": {
                        psgc: evidence_by_psgc.get(psgc, {}).get(
                            "reason", "source_unit_not_published"
                        )
                        for psgc in missing
                    },
                    "source_warnings": source_warnings,
                    "revision_warnings": revision_warnings,
                    "warning": (
                        "PSA publishes this measure at its natural grain. The build does not "
                        "average rates or custom-roll up HUC estimates."
                    ),
                }
            )
    return coverage


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


def compute_dpwh_spend_per_capita_cum(dpwh_spend: list[dict]) -> list[dict]:
    """Cumulative nominal DPWH spend per capita over CUM_SPEND_START..CUM_SPEND_END.

    Sums the nominal per-capita PHP values per province across the window. Using
    nominal (not real) values because CPI deflation before 2018 would require
    backward extrapolation, which is less defensible than a clearly-labelled nominal
    cumulative. The story caveat states this explicitly.

    Returns one row per province (psgc), year set to CUM_SPEND_END so it pairs
    with poverty_change_pp whose reference year is also 2023. Repeated across panel
    years so the indicator picker can pair it with any year-varying Y.
    """
    by_psgc: dict[str, float] = {}
    for r in dpwh_spend:
        if CUM_SPEND_START <= r["year"] <= CUM_SPEND_END:
            by_psgc[r["psgc"]] = by_psgc.get(r["psgc"], 0.0) + r["value"]

    # A province with gaps in some years still gets summed over the years present.
    # Years where spend was dropped as a coverage gap (< MIN_PESO_PER_CAPITA/cap)
    # simply contribute 0 to the cumulative, which is conservative.
    out = []
    for psgc, cum in by_psgc.items():
        for year in PANEL_YEARS:
            out.append({"psgc": psgc, "year": year, "value": cum})
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


def _permutation_p(
    xs: list[float],
    ys: list[float],
    observed_rho: float,
    n_perm: int = _PERM_N,
    seed: int = _PERM_SEED,
) -> float:
    """Two-tailed permutation p-value for Spearman's rho.

    Shuffles ys n_perm times, recomputes rho each time, and returns the fraction
    of permutations whose |rho| >= |observed_rho|. Seeded for reproducibility.
    """
    rng = random.Random(seed)
    ys_list = list(ys)
    count = 0
    abs_obs = abs(observed_rho)
    for _ in range(n_perm):
        rng.shuffle(ys_list)
        perm_rho = _spearman(xs, ys_list)
        if perm_rho is not None and abs(perm_rho) >= abs_obs:
            count += 1
    return count / n_perm


def _format_p(p: float) -> str:
    """Format a p-value for the finding sentence."""
    if p < 0.001:
        return "p < 0.001"
    return f"p = {p:.3f}"


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
    story: dict,
    value_index: dict[str, dict[tuple[str, int], float]],
    expected_units: int | None = None,
) -> dict:
    """Compute a data-grounded answer to the story's question at its default year.

    Returns a finding dict with the Spearman rank correlation (robust to the
    log axis the chart uses), a permutation p-value (10k shuffles, seeded),
    the unit count actually used, the actual year used, and an off-diagonal
    quadrant count. The human sentence is assembled here from the computed numbers
    (never hand-typed) so it can never drift from the data. Every finding carries
    the correlation-not-causation caveat.

    expected_units is the size of the story's unit universe (82 provincial units
    or 18 regions). When the year's usable n is short of it (e.g. a unit lacks
    spend or GDP data), the sentence says "n of expected" so the count never
    silently disagrees with the "81 provinces and Metro Manila" tagline.
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

    p_val = None
    if rho is not None:
        p_val = _permutation_p(xs, ys, rho)

    # Off-diagonal count: provinces above the median on BOTH axes.
    mx, my = _median(xs), _median(ys)
    both_high = sum(1 for x, y in pairs if x > mx and y > my)

    yname = {
        "poverty": "poverty incidence",
        "gdp_per_capita": "per-capita GDP",
        "subsistence_incidence": "subsistence incidence",
        "poverty_change_pp": "poverty change (pp)",
        "region_poverty": "regional poverty incidence",
    }.get(yid, yid.replace("_", " "))
    xname = {
        "dpwh_spend_per_capita": "DPWH spend per capita",
        "dpwh_spend_per_capita_cum": "cumulative DPWH spend per capita (2014-2023)",
        "all_spend_per_capita": "all-government spend per capita",
        "gdp_per_capita": "per-capita GDP",
        "region_cpi_yoy_pct": "regional CPI inflation (year-on-year)",
    }.get(xid, xid.replace("_", " "))

    direction = "negative" if (rho is not None and rho < 0) else "positive"
    strength = _strength_word(rho) if rho is not None else "no measurable"
    rho_txt = f"{rho:+.2f}" if rho is not None else "n/a"
    p_txt = _format_p(p_val) if p_val is not None else ""
    area_clause = f"across {n} areas"
    if expected_units is not None and n < expected_units:
        area_clause = f"across {n} of {expected_units} areas with data"
    sentence = (
        f"In {year}, {area_clause}, the rank correlation between {xname} and "
        f"{yname} is rho = {rho_txt} ({p_txt}), showing {strength} {direction} link. "
        f"{both_high} of {n} areas sat above the median on both axes."
    )
    award_ids = {
        "dpwh_spend_per_capita",
        "dpwh_spend_per_capita_cum",
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
        "p_value": round(p_val, 4) if p_val is not None else None,
        "both_above_median": both_high,
        "sentence": sentence,
        "caveat": caveat,
    }


def write_json(name: str, payload: object) -> None:
    PUBLIC_DATA.mkdir(parents=True, exist_ok=True)
    out = PUBLIC_DATA / name
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {out.relative_to(PUBLIC_DATA.parent.parent)}  ({out.stat().st_size:,} bytes)")


def write_procurement_status() -> dict:
    """Publish the reviewed candidate-year gate without candidate spend rows."""
    status = philgeps.procurement_status(philgeps.load_reviewed_snapshot_inventory())
    write_json("procurement_status.json", status)
    return status


def build_view_evidence() -> dict:
    """Build the small provenance contract used by the explorer."""
    indicators = json.loads((PUBLIC_DATA / "indicators.json").read_text())
    procurement = json.loads((PUBLIC_DATA / "procurement_status.json").read_text())
    file_by_indicator = {
        "poverty": "poverty.json",
        "subsistence_incidence": "subsistence.json",
        "dpwh_spend_per_capita": "dpwh_spend_per_capita.json",
        "all_spend_per_capita": "all_spend_per_capita.json",
        "doh_spend_per_capita": "doh_spend_per_capita.json",
        "infra_spend_per_capita": "infra_spend_per_capita.json",
        "gdp_per_capita": "gdp_per_capita.json",
        "dpwh_share_pct": "dpwh_share_pct.json",
        "population": "population.json",
        "poverty_change_pp": "poverty_change_pp.json",
        "cpi_yoy_pct": "cpi_yoy_pct.json",
        "dpwh_spend_per_capita_cum": "dpwh_spend_per_capita_cum.json",
        "region_cpi_yoy_pct": "region_cpi_yoy_pct.json",
        "region_poverty": "region_poverty.json",
    }
    expected = {"provinces": 82, "regions": 18}
    out = {}
    for indicator in indicators:
        indicator_id = indicator["id"]
        unit_set = indicator.get("unit_set", "provinces")
        rows = json.loads((PUBLIC_DATA / file_by_indicator[indicator_id]).read_text())
        coverage = []
        for year in indicator["panel_years"]:
            source_units = {
                row["psgc"]
                for row in rows
                if row["year"] == year and row.get("psgc") not in {None, "000000000"}
            }
            count = len(source_units)
            coverage.append(
                {
                    "year": year,
                    "target_units": expected[unit_set],
                    "source_units": count,
                    "status": "full"
                    if count == expected[unit_set]
                    else "partial"
                    if count
                    else "unavailable",
                }
            )
        source_url = indicator.get("source_url") or "https://openstat.psa.gov.ph/"
        if indicator_id in {
            "poverty",
            "subsistence_incidence",
            "poverty_change_pp",
            "region_poverty",
        }:
            source_url = source_url.replace("DB__1E__FY", "DB__1F__FY")
        out[indicator_id] = {
            "unit_set": unit_set,
            "natural_grain": "province" if unit_set == "provinces" else "region",
            "years": indicator["panel_years"],
            "source": indicator["source"],
            "source_url": source_url,
            "archive_url": source_url,
            "release": indicator.get("vintage", "Committed data release"),
            "transforms": indicator.get("definition", "No additional transform."),
            "warnings": [indicator["coverage_label"]] if indicator.get("coverage_label") else [],
            "coverage": coverage,
            "source_id": file_by_indicator[indicator_id],
        }
    depth = json.loads((PUBLIC_DATA / "poverty_depth_coverage.json").read_text())
    return {
        "schema_version": 1,
        "indicators": out,
        "supplemental_coverage": {"poverty_depth": depth},
        "procurement_status": procurement,
    }


def main(no_cache: bool = False) -> None:
    if no_cache:
        psa_openstat.clear_cache()
        from etl import psgc as _psgc

        _psgc.clear_cache()
        print(">> --no-cache: cleared PSA + PSGC caches")

    print(">> load provinces")
    provinces = load_provinces()
    n_units = len(provinces)  # 82: 81 provinces + Metro Manila

    print(">> fetch 2020 population (with HUC rollup into parent provinces)")
    population_2020 = psa_openstat.fetch_population_2020(
        provinces, normalize_name, huc_parent=huc_parent
    )
    validate.validate_all(population_2020, schema="population")
    pop_by_psgc = {r["psgc"]: r["value"] for r in population_2020}
    total_pop = sum(pop_by_psgc.values())
    print(f"   total covered pop: {total_pop:,} (PSA 2020 Census national = ~109,033,245)")

    print(">> fetch 2024 POPCEN anchor (with HUC rollup into parent provinces)")
    population_2024 = psa_openstat.fetch_population_2024(
        provinces, normalize_name, huc_parent=huc_parent
    )
    validate.validate_all(population_2024, schema="population")

    print(">> fetch poverty (PSA 1E/FY 1a, anchors 2018/2021/2023, with 95% CI)")
    poverty_anchors = psa_openstat.fetch_poverty(provinces, normalize_name)
    validate.validate_all(poverty_anchors, schema="rate_pct")
    validate.validate_precision(poverty_anchors)

    print(">> fetch subsistence incidence (PSA 1E/FY 3a, anchors 2018/2021/2023, with 95% CI)")
    subsistence_anchors = psa_openstat.fetch_subsistence(provinces, normalize_name)
    validate.validate_all(subsistence_anchors, schema="rate_pct")
    validate.validate_precision(subsistence_anchors)

    print(">> fetch DPWH spend (PhilGEPS, 15 chunks)")
    # Run median-shift check against committed file before overwriting.
    dpwh_spend_committed_path = "dpwh_spend_per_capita.json"
    dpwh_spend, dpwh_attribution = philgeps.fetch_dpwh_spend(provinces, normalize_name, pop_by_psgc)
    validate.validate_median_shift(dpwh_spend, dpwh_spend_committed_path, "dpwh_spend_per_capita")
    validate.validate_all(dpwh_spend, schema="peso_per_capita")
    validate.validate_yoy_jumps(dpwh_spend, "dpwh_spend_per_capita")

    print(">> fetch all PhilGEPS spend (no agency filter)")
    all_spend, all_attribution = philgeps.fetch_all_spend(provinces, normalize_name, pop_by_psgc)
    validate.validate_median_shift(all_spend, "all_spend_per_capita.json", "all_spend_per_capita")
    validate.validate_all(all_spend, schema="peso_per_capita")
    validate.validate_yoy_jumps(all_spend, "all_spend_per_capita")

    print(">> fetch DOH spend (PhilGEPS, org=DOH)")
    doh_spend, _ = philgeps.fetch_doh_spend(provinces, normalize_name, pop_by_psgc)
    validate.validate_all(doh_spend, schema="peso_per_capita")

    print(">> fetch infra-only spend (PhilGEPS, construction/road/bridge/etc.)")
    infra_spend, _ = philgeps.fetch_infra_spend(provinces, normalize_name, pop_by_psgc)
    validate.validate_all(infra_spend, schema="peso_per_capita")

    print(">> fetch CPI annual averages (PSA 2M/PI/CPI, PHILIPPINES national)")
    cpi = psa_openstat.fetch_cpi_annual()
    # Fail loud rather than fabricate a deflator base: an empty pull or a
    # missing 2018 row would otherwise default to 100.0 and silently ship
    # all-null "2018-real" values for every deflatable spend series.
    if not cpi or 2018 not in cpi:
        raise RuntimeError(
            "CPI series is empty or missing the 2018 base year; refusing to "
            "fabricate a deflator base"
        )
    cpi_2018 = cpi[2018]
    if cpi_2018 <= 0:
        raise RuntimeError("CPI 2018 base is non-positive, refusing to deflate")
    deflators = {y: cpi_2018 / cpi[y] for y in cpi if cpi[y] > 0}

    for series in (dpwh_spend, all_spend, doh_spend, infra_spend):
        for r in series:
            d = deflators.get(r["year"])
            r["value_real"] = (r["value"] * d) if d is not None else None

    print(">> fetch additive GDP (PSA 2A/PPA, constant 2018 prices, 2018-2025)")
    gdp_total = psa_openstat.fetch_gdp_total(provinces, normalize_name, huc_parent)
    gdp_published = psa_openstat.fetch_gdp_per_capita_source(provinces, normalize_name, huc_parent)
    psa_openstat.validate_gdp_industry_contract()
    psa_openstat.require_source_years(gdp_total, range(2018, 2026), "PPA GDP")
    psa_openstat.require_source_years(gdp_published, range(2018, 2026), "PPA per-capita GDP")
    gdp = psa_openstat.recompute_gdp_per_capita(gdp_total, gdp_published)
    validate.validate_all(gdp, schema="peso_per_capita_gdp")
    psa_openstat.require_analysis_coverage(gdp, range(2018, 2026), "PPA per-capita GDP")

    print(">> fetch poverty-depth measures at the published area grain")
    poverty_depth = []
    poverty_depth_missing = []
    for table in psa_openstat.POVERTY_DEPTH_MEASURES:
        poverty_depth.extend(
            psa_openstat.fetch_poverty_depth(
                table, provinces, normalize_name, poverty_depth_missing
            )
        )
    for row in poverty_depth:
        schema = "poor_families_thousands" if row["kind"] == "count" else "poverty_gap_pct"
        validate.validate_all([row], schema=schema)
    validate.validate_precision(poverty_depth)
    poverty_depth_status = poverty_depth_coverage(
        poverty_depth,
        n_units,
        set(provinces),
        missing_evidence=poverty_depth_missing,
    )

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
        code: {
            **info,
            "population_2020": pop_by_psgc.get(code, 0),
            "population_2024": next(
                (row["value"] for row in population_2024 if row["psgc"] == code), 0
            ),
        }
        for code, info in provinces.items()
    }

    print(">> derive: DPWH share %, CPI YoY %, poverty change 2018-2023, population")
    dpwh_share = compute_dpwh_share(dpwh_spend, all_spend)
    validate.validate_all(dpwh_share, schema="share_pct")
    cpi_yoy = compute_cpi_yoy(cpi)
    validate.validate_all(cpi_yoy, schema="yoy_pct")
    poverty_change = compute_poverty_change(poverty_anchors)
    validate.validate_all(poverty_change, schema="delta_pp")
    population_series = psa_openstat.interpolate_population_anchors(
        population_2020, population_2024, PANEL_YEARS
    )
    validate.validate_all(population_series, schema="population")

    print(">> fetch regional poverty + regional CPI (18 regions, PSA 1a + 2M/PI/CPI)")
    regions = psa_inflation.load_regions()
    region_poverty = psa_inflation.fetch_regional_poverty()
    validate.validate_all(region_poverty, schema="rate_pct")
    validate.validate_precision(region_poverty)
    validate.validate_coverage(region_poverty, len(regions), POVERTY_ANCHORS, "region_poverty")
    validate.validate_uniqueness(region_poverty, "region_poverty")
    region_cpi = psa_inflation.fetch_regional_cpi()
    region_cpi_yoy = psa_inflation.compute_regional_cpi_yoy(region_cpi)
    validate.validate_all(region_cpi_yoy, schema="yoy_pct")
    validate.validate_uniqueness(region_cpi_yoy, "region_cpi_yoy_pct")
    region_yoy_years = sorted({r["year"] for r in region_cpi_yoy})

    print(">> derive: cumulative DPWH spend per capita 2014-2023 (nominal)")
    dpwh_cum = compute_dpwh_spend_per_capita_cum(dpwh_spend)
    # Cumulative values can exceed the single-year peso_per_capita max; use a wide
    # schema check inline rather than a new schema entry.
    for r in dpwh_cum:
        if r["value"] < 0:
            raise ValueError(f"Negative cumulative spend in row: {r}")

    # Coverage validation: poverty and population cover all 82 units at every
    # year (population at every panel year; poverty at every survey anchor).
    # Spend/GDP indicators have legitimate gaps so we only assert uniqueness there.
    validate.validate_coverage(poverty_anchors, n_units, POVERTY_ANCHORS, "poverty_anchors")
    validate.validate_uniqueness(poverty_anchors, "poverty_anchors")
    validate.validate_coverage(population_series, n_units, PANEL_YEARS, "population")
    validate.validate_uniqueness(population_series, "population")
    validate.validate_uniqueness(gdp, "gdp_per_capita")
    for measure in psa_openstat.POVERTY_DEPTH_MEASURES:
        validate.validate_uniqueness(
            [row for row in poverty_depth if row["measure"] == measure], measure
        )
    validate.validate_precision(poverty_depth)
    validate.validate_uniqueness(dpwh_spend, "dpwh_spend_per_capita")
    validate.validate_uniqueness(all_spend, "all_spend_per_capita")

    # Report attribution coverage for DPWH (lowest 5 provinces)
    if dpwh_attribution:
        sorted_attr = sorted(dpwh_attribution.items(), key=lambda x: x[1])
        print("\n>> DPWH attribution coverage per province (lowest 5):")
        for psgc, share in sorted_attr[:5]:
            name = provinces_out.get(psgc, {}).get("name", psgc)
            print(f"   {name} ({psgc}): {share * 100:.1f}%")
        print()

    write_json("provinces.json", provinces_out)
    write_json("poverty.json", poverty)
    write_json("subsistence.json", subsistence)
    write_json("dpwh_spend_per_capita.json", dpwh_spend)
    write_json("all_spend_per_capita.json", all_spend)
    write_json("doh_spend_per_capita.json", doh_spend)
    write_json("infra_spend_per_capita.json", infra_spend)
    write_json("gdp_per_capita.json", gdp)
    write_json("poverty_depth.json", poverty_depth)
    write_json("poverty_depth_coverage.json", poverty_depth_status)
    write_json("cpi.json", {str(y): v for y, v in cpi.items()})
    write_json("dpwh_share_pct.json", dpwh_share)
    write_json("cpi_yoy_pct.json", cpi_yoy)
    write_json("poverty_change_pp.json", poverty_change)
    write_json("population.json", population_series)
    write_json("dpwh_spend_per_capita_cum.json", dpwh_cum)
    write_json("regions.json", regions)
    write_json("region_poverty.json", region_poverty)
    write_json("region_cpi_yoy_pct.json", region_cpi_yoy)

    indicators = [
        {
            "id": "poverty",
            "name": "Poverty incidence among families",
            "unit": "%",
            "source": "PSA OpenStat 1E/FY Table 1a",
            "source_url": ("https://openstat.psa.gov.ph/PXWeb/pxweb/en/DB/DB__1E__FY/"),
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
            "source_url": ("https://openstat.psa.gov.ph/PXWeb/pxweb/en/DB/DB__1E__FY/"),
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
            "source": "PSA OpenStat PPA Tables 1 and 9, constant 2018 prices",
            "source_url": ("https://openstat.psa.gov.ph/PXWeb/pxweb/en/DB/DB__2A__PPA/"),
            "definition": (
                "Total provincial economic output divided by population, expressed in "
                "PHP at constant 2018 prices so values across years are directly "
                "comparable without an inflation adjustment."
            ),
            "vintage": (
                "Custom historical-area recomputation for 2018-2025. The build sums "
                "constant-price GDP and source-implied PPA population before division. "
                "It does not average published per-capita GDP. HUCs roll into their parent "
                "provinces. Makati excludes EMBO barangays in 2022-2024. The published "
                "PPA per-capita series supplies the matching source-implied denominator."
            ),
            "log_natural": True,
            "panel_years": GDP_PANEL_YEARS,
            "anchor_years": GDP_ANCHORS,
            "can_deflate": False,
            "coverage_label": (
                "Custom stable-area values cover 2018-2025. The global common panel can "
                "end earlier when another indicator lacks a matching year."
            ),
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
            "name": "Population (2020 and 2024 POPCEN)",
            "unit": "people",
            "source": "PSA 2020 Census and 2024 POPCEN",
            "source_url": "https://psa.gov.ph/population-and-housing",
            "definition": (
                "Whole-province population from the 2020 Census and 2024 POPCEN, including "
                "Highly Urbanized Cities rolled into their geographic parent province. NCR "
                "uses the published regional total."
            ),
            "vintage": (
                "The 2020 and 2024 values are official anchors. The build interpolates "
                "2021-2023. Years through 2020 retain the 2020 Census value."
            ),
            "log_natural": True,
            "panel_years": PANEL_YEARS,
            "anchor_years": [2020, 2024],
            "static_snapshot": False,
            "snapshot_label": (
                "2020 Census through 2020. 2021-2023 are estimates. 2024 is official POPCEN."
            ),
        },
        {
            "id": "poverty_change_pp",
            "name": "Poverty change 2018 to 2023 (pp)",
            "unit": "percentage points",
            "source": "Derived from PSA 1E/FY Table 1a anchors",
            "source_url": ("https://openstat.psa.gov.ph/PXWeb/pxweb/en/DB/DB__1E__FY/"),
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
            "source_url": ("https://openstat.psa.gov.ph/PXWeb/pxweb/en/DB/DB__2M__PI__CPI/"),
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
        {
            "id": "dpwh_spend_per_capita_cum",
            "name": "Cumulative DPWH spend per capita 2014-2023",
            "unit": "PHP per person (cumulative, nominal)",
            "source": "PhilGEPS awards (DPWH subset) / PSA 2020 Census population",
            "source_url": "https://github.com/csiiiv/philgeps-awards-dashboard",
            "definition": (
                "Sum of annual nominal DPWH spend per capita over 2014-2023 (ten years). "
                "Represents the total PhilGEPS-tracked road and infrastructure commitment "
                "per person that could be attributed to a single province over the decade "
                "preceding the 2023 PSA poverty survey."
            ),
            "vintage": (
                "Summed from dpwh_spend_per_capita annual rows 2014-2023 (nominal PHP). "
                "Nominal, not real, because CPI deflation before 2018 requires backward "
                "extrapolation. Province-years dropped as coverage gaps (< PHP 100/cap) "
                "contribute 0 to the cumulative, so values are conservative lower bounds. "
                "Constant across panel years (single window, not rolling)."
            ),
            "log_natural": True,
            "panel_years": PANEL_YEARS,
            "anchor_years": [CUM_SPEND_END],
            "can_deflate": False,
            "static_snapshot": True,
            "snapshot_label": (
                f"Cumulative 2014-{CUM_SPEND_END}. A single value per province, "
                "not a yearly series."
            ),
        },
        {
            "id": "region_cpi_yoy_pct",
            "name": "Regional inflation (CPI year-on-year)",
            "unit": "%",
            "source": "Derived from PSA OpenStat 2M/PI/CPI/2018NEW, regional rows",
            "source_url": ("https://openstat.psa.gov.ph/PXWeb/pxweb/en/DB/DB__2M__PI__CPI/"),
            "definition": (
                "Year-on-year change in each region's all-items CPI annual average, "
                "2018=100. PSA publishes CPI by region, not province, so this "
                "indicator runs on the 18 official regions."
            ),
            "vintage": (
                "(cpi_year - cpi_prev) / cpi_prev * 100 per region, consecutive "
                "years only. The series starts in 2019 (the 2018-based index needs a "
                "year-prior) and excludes the in-progress calendar year, whose "
                "running annual average would read as a fake full-year print."
            ),
            "log_natural": False,
            "panel_years": region_yoy_years,
            "anchor_years": region_yoy_years,
            "unit_set": "regions",
            "coverage_label": (
                "Regional grain: PSA publishes CPI by region, so this view has 18 "
                "units instead of the 82 provincial units elsewhere on the site."
            ),
        },
        {
            "id": "region_poverty",
            "name": "Poverty incidence among families (regional)",
            "unit": "%",
            "source": "PSA OpenStat 1E/FY Table 1a, regional rows",
            "source_url": ("https://openstat.psa.gov.ph/PXWeb/pxweb/en/DB/DB__1E__FY/"),
            "definition": (
                "Share of families below the official poverty threshold, as PSA "
                "publishes it for each of the 18 regions in Table 1a. These are "
                "PSA's own regional estimates (full survey design), not an average "
                "of the provincial rows."
            ),
            "vintage": (
                "PSA Full-Year anchors at 2018, 2021, 2023; survey years only, no "
                "interpolation at the regional grain. Each year carries PSA's "
                "published 95% confidence interval and coefficient of variation."
            ),
            "log_natural": False,
            "panel_years": POVERTY_ANCHORS,
            "anchor_years": POVERTY_ANCHORS,
            "has_ci": True,
            "cv_unreliable_above": 30,
            "unit_set": "regions",
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
                "DPWH spend per capita against poverty incidence. 81 provinces "
                "and Metro Manila. 2014 to 2024. Hit play, or use the arrow keys."
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
                "poverty incidence. 81 provinces and Metro Manila, 2014 to 2024. "
                "Scrub the years."
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
                "81 provinces and Metro Manila. 2022 to 2024. All government "
                "contracts per capita against per-capita GDP."
            ),
            "why": (
                "DPWH-vs-poverty and all-spend-vs-poverty both ask whether money "
                "follows poverty. This one asks the opposite: does procurement "
                "spend follow wealth? If the cloud tilts upward, procurement money "
                "lands where GDP already is. If it tilts downward, it lands where "
                "GDP isn't. Both indicators are already shipped, so this preset "
                "needed zero new data."
            ),
            "source_url": ("https://openstat.psa.gov.ph/PXWeb/pxweb/en/DB/DB__2A__PPA/"),
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
        {
            "id": "spend-vs-poverty-change",
            "tab_label": "Spend vs poverty change",
            "headline": "More road spending, more poverty reduction?",
            "tagline": (
                "Cumulative DPWH spend per capita over 2014-2023 against the "
                "change in poverty incidence from 2018 to 2023. One snapshot, "
                "81 provinces and Metro Manila."
            ),
            "why": (
                "The year-by-year scatter shows no consistent pattern (rho near zero). "
                "This panel asks the same question differently: did the provinces that "
                "received the most road funding over a decade also see the largest "
                "reductions in poverty? Cumulative spend is matched to the 2018-to-2023 "
                "poverty change, the longest available window where both indicators overlap. "
                "A negative correlation would mean higher cumulative spend associates with "
                "larger poverty falls (which is what the headline question implies). "
                "A near-zero result means spending and poverty outcomes are effectively "
                "independent at the province level."
            ),
            "source_url": "https://github.com/csiiiv/philgeps-awards-dashboard",
            "x": "dpwh_spend_per_capita_cum",
            "y": "poverty_change_pp",
            "size": "population_2020",
            # Both axes are static window aggregates (2014-2023 spend, 2018-2023
            # poverty change), so every panel year renders the identical frame.
            # A single 2023 panel keeps the year label honest and the timeline quiet.
            "panel_years": [2023],
            "default_year": 2023,
            "default_log_x": True,
            "awards_caveat": AWARDS_CAVEAT,
            "caveat_cum": (
                "X axis is cumulative nominal PHP per capita (2014-2023), not real. "
                "CPI deflation before 2018 requires extrapolation so nominal is used "
                "for transparency. Negative Y means poverty fell; positive means it rose."
            ),
        },
        {
            "id": "inflation-vs-poverty",
            "tab_label": "Inflation vs poverty",
            "headline": "Where prices rise fastest, who already lives poor?",
            "tagline": (
                "Regional CPI inflation (year-on-year) against poverty incidence. "
                "PSA publishes CPI by region, not province, so this story drops "
                "from the 82 provincial units used elsewhere to 18 regions. "
                "PSA survey years 2021 and 2023."
            ),
            "why": (
                "Inflation is a tax that falls hardest where incomes are lowest, "
                "but headline inflation is a national number. PSA's regional CPI "
                "lets the question be asked one level down: do the regions with "
                "the fastest-rising prices also carry the highest poverty? The "
                "upper-right of the chart is the squeeze: high poverty and fast "
                "price growth at the same time."
            ),
            "source_url": ("https://openstat.psa.gov.ph/PXWeb/pxweb/en/DB/DB__2M__PI__CPI/"),
            "x": "region_cpi_yoy_pct",
            "y": "region_poverty",
            "unit_set": "regions",
            "panel_years": REGION_STORY_YEARS,
            "default_year": 2023,
            "default_log_x": False,
        },
    ]

    # Compute a data-grounded finding per story so each question gets an answer
    # on the page, not just a chart. Built from the same series the chart plots.
    value_index = {
        "poverty": {(r["psgc"], r["year"]): r["value"] for r in poverty},
        "subsistence_incidence": {(r["psgc"], r["year"]): r["value"] for r in subsistence},
        "dpwh_spend_per_capita": {(r["psgc"], r["year"]): r["value"] for r in dpwh_spend},
        "dpwh_spend_per_capita_cum": {(r["psgc"], r["year"]): r["value"] for r in dpwh_cum},
        "all_spend_per_capita": {(r["psgc"], r["year"]): r["value"] for r in all_spend},
        "doh_spend_per_capita": {(r["psgc"], r["year"]): r["value"] for r in doh_spend},
        "infra_spend_per_capita": {(r["psgc"], r["year"]): r["value"] for r in infra_spend},
        "gdp_per_capita": {(r["psgc"], r["year"]): r["value"] for r in gdp},
        "poverty_change_pp": {(r["psgc"], r["year"]): r["value"] for r in poverty_change},
        "region_cpi_yoy_pct": {(r["psgc"], r["year"]): r["value"] for r in region_cpi_yoy},
        "region_poverty": {(r["psgc"], r["year"]): r["value"] for r in region_poverty},
    }
    for s in stories:
        # Universe size for the "n of expected" clause: 18 regions for the
        # regional unit set, the 82 provincial units (81 provinces + Metro
        # Manila) otherwise.
        expected = len(regions) if s.get("unit_set") == "regions" else n_units
        s["finding"] = compute_story_finding(s, value_index, expected_units=expected)
    write_json("stories.json", stories)

    # Regenerate the per-story social share pages + the journalist embed kit from
    # the same stories (their OG descriptions are the computed finding, so they must
    # never be hand-synced). The OG card images are rebuilt separately by
    # docs/build_og_cards.js when headlines/design change.
    share_written = build_share_pages.write_share_pages(stories, PUBLIC_DATA.parent)
    print(f"  share/embed pages: {len(share_written)}")

    # Write manifest LAST so its sha256 covers every freshly-written file.
    procurement_status = write_procurement_status()
    dpwh_total = compute_dpwh_attributed_total(dpwh_spend, pop_by_psgc)

    # Per-province attribution coverage for manifest
    dpwh_attr_coverage = {psgc: round(share, 4) for psgc, share in dpwh_attribution.items()}

    # Compute sha256 of each fetched input for input pinning record.
    # Live API responses (PSA) are recorded by fetch date + row counts instead.
    psgc_cache = Path(__file__).resolve().parent.parent / ".etl_cache" / "psgc" / "provinces.json"
    psgc_sha = hashlib.sha256(psgc_cache.read_bytes()).hexdigest() if psgc_cache.exists() else None
    inputs = {
        "philgeps_chunks": {
            "source": philgeps.CHUNK_BASE,
            "note": (
                "mutable GitHub raw URL (no commit SHA available on this mirror). "
                "Pinning via local sha256 of each chunk at build time."
            ),
            "chunks": {},
        },
        "philgeps_snapshot": procurement_status,
        "psgc": {
            "source": "https://psgc.gitlab.io/api/provinces.json",
            "note": "mutable community mirror. Cached copy sha256 recorded.",
            "cached_sha256": psgc_sha,
        },
        "psa_openstat": {
            "source": "https://openstat.psa.gov.ph/PXWeb/api/v1/en/DB",
            "note": "live API; pinning impossible. Fetch date and row counts recorded.",
            "fetch_date": datetime.now(UTC).date().isoformat(),
            "row_counts": {
                "poverty_anchors": len(poverty_anchors),
                "subsistence_anchors": len(subsistence_anchors),
                "population_2020": len(population_2020),
                "population_2024": len(population_2024),
                "gdp": len(gdp),
                "poverty_depth": len(poverty_depth),
                "cpi_years": len(cpi),
                "region_poverty": len(region_poverty),
                "region_cpi_yoy": len(region_cpi_yoy),
            },
        },
    }

    # Record sha256 for each PhilGEPS chunk (immutable once cached).
    for i in range(1, philgeps.N_CHUNKS + 1):
        cp = philgeps._chunk_path(i)
        if cp.exists():
            sha = hashlib.sha256(cp.read_bytes()).hexdigest()
            inputs["philgeps_chunks"]["chunks"][f"chunk_{i:02d}"] = sha

    manifest = build_manifest(
        derived={
            "dpwh_attributed_php_total": dpwh_total,
            "dpwh_attributed_php_note": (
                "Sum of per-capita DPWH spend x 2020 province population over the "
                "attributed subset the chart plots (2014-2024, 11 years). About 20% of "
                "DPWH award value is unattributable and excluded, so this is a floor. "
                "Rounds to ~5 trillion PHP nominal; cited by the DPWH story headline."
            ),
            "dpwh_attribution_share_by_psgc": dpwh_attr_coverage,
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
            "poverty_depth": len(poverty_depth),
            "poverty_depth_coverage": len(poverty_depth_status),
            "cpi": len(cpi),
            "dpwh_share_pct": len(dpwh_share),
            "cpi_yoy_pct": len(cpi_yoy),
            "poverty_change_pp": len(poverty_change),
            "population": len(population_series),
            "dpwh_spend_per_capita_cum": len(dpwh_cum),
            "regions": len(regions),
            "region_poverty": len(region_poverty),
            "region_cpi_yoy_pct": len(region_cpi_yoy),
            "stories": len(stories),
            "indicators": len(indicators),
        },
        inputs=inputs,
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
    print(f"  dpwh cum rows: {len(dpwh_cum)}")
    print(f"  population rows: {len(population_series)}")
    print(f"  regions: {len(regions)}")
    print(f"  region poverty rows: {len(region_poverty)}")
    print(f"  region cpi yoy rows: {len(region_cpi_yoy)}")
    print(f"  stories: {len(stories)}")
    print(f"  indicators: {len(indicators)}")
    print(f"  manifest built_at: {manifest['built_at']}")
    print(f"  dpwh attributed total: PHP {dpwh_total:,}")


def compute_dpwh_attributed_total(dpwh_spend: list[dict], pop_by_psgc: dict[str, int]) -> int:
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


def build_manifest(
    row_counts: dict[str, int],
    derived: dict | None = None,
    inputs: dict | None = None,
) -> dict:
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
            "poverty": "PSA OpenStat 1F/FY official poverty tables, anchors 2018/2021/2023",
            "cpi": "PSA OpenStat 2M/PI/CPI/2018NEW, annual averages 2018-2025",
            "philgeps": "csiiiv/philgeps-awards-dashboard mirror, 2014-2024",
            "gdp_per_capita": "PSA OpenStat PPA Tables 1 and 9, constant 2018 prices, 2018-2025",
            "population": "PSA 2020 Census and 2024 POPCEN",
            "psgc": "PSA PSGC 2Q 2026 as of 2026-06-30",
        },
        "inputs": inputs or {},
        "row_counts": row_counts,
        "derived": derived or {},
        "file_bytes": sizes,
        "sha256_per_file": sha,
    }


def refresh_automated_psa_public_data() -> None:
    """Refresh PSA-only public artifacts without fetching the protected PhilGEPS snapshot."""
    provinces = load_provinces()
    population_2020 = psa_openstat.fetch_population_2020(
        provinces, normalize_name, huc_parent=huc_parent
    )
    population_2024 = psa_openstat.fetch_population_2024(
        provinces, normalize_name, huc_parent=huc_parent
    )
    population = psa_openstat.interpolate_population_anchors(
        population_2020, population_2024, PANEL_YEARS
    )
    gdp_total = psa_openstat.fetch_gdp_total(provinces, normalize_name, huc_parent)
    gdp_published = psa_openstat.fetch_gdp_per_capita_source(provinces, normalize_name, huc_parent)
    ppa_industry = psa_openstat.validate_gdp_industry_contract()
    psa_openstat.require_source_years(gdp_total, range(2018, 2026), "PPA GDP")
    psa_openstat.require_source_years(gdp_published, range(2018, 2026), "PPA per-capita GDP")
    gdp = psa_openstat.recompute_gdp_per_capita(gdp_total, gdp_published)
    poverty_depth = []
    poverty_depth_missing = []
    for table in psa_openstat.POVERTY_DEPTH_MEASURES:
        poverty_depth.extend(
            psa_openstat.fetch_poverty_depth(
                table, provinces, normalize_name, poverty_depth_missing
            )
        )
    for measure in psa_openstat.POVERTY_DEPTH_MEASURES:
        validate.validate_uniqueness(
            [row for row in poverty_depth if row["measure"] == measure], measure
        )
    validate.validate_precision(poverty_depth)
    poverty_depth_status = poverty_depth_coverage(
        poverty_depth,
        len(provinces),
        set(provinces),
        missing_evidence=poverty_depth_missing,
    )
    validate.validate_coverage(population, len(provinces), PANEL_YEARS, "population")
    validate.validate_uniqueness(population, "population")
    validate.validate_uniqueness(gdp, "gdp_per_capita")
    validate.validate_all(gdp, schema="peso_per_capita_gdp")
    psa_openstat.require_analysis_coverage(gdp, range(2018, 2026), "PPA per-capita GDP")

    provinces_out = json.loads((PUBLIC_DATA / "provinces.json").read_text())
    pop_2020 = {row["psgc"]: row["value"] for row in population_2020}
    pop_2024 = {row["psgc"]: row["value"] for row in population_2024}
    for code in provinces_out:
        provinces_out[code]["population_2020"] = pop_2020[code]
        provinces_out[code]["population_2024"] = pop_2024[code]
    write_json("provinces.json", provinces_out)
    write_json("population.json", population)
    write_json("gdp_per_capita.json", gdp)
    write_json("poverty_depth.json", poverty_depth)
    write_json("poverty_depth_coverage.json", poverty_depth_status)

    indicators = json.loads((PUBLIC_DATA / "indicators.json").read_text())
    by_id = {indicator["id"]: indicator for indicator in indicators}
    by_id["population"].update(
        {
            "name": "Population (2020 and 2024 POPCEN)",
            "source": "PSA 2020 Census and 2024 POPCEN",
            "vintage": "2020 and 2024 are official anchors. 2021-2023 are linear estimates.",
            "anchor_years": [2020, 2024],
            "static_snapshot": False,
            "snapshot_label": (
                "2020 Census through 2020. 2021-2023 are estimates. 2024 is official POPCEN."
            ),
        }
    )
    by_id["gdp_per_capita"].update(
        {
            "source": "PSA OpenStat PPA Tables 1 and 9, constant 2018 prices",
            "panel_years": GDP_PANEL_YEARS,
            "anchor_years": GDP_ANCHORS,
            "coverage_label": "Custom stable-area values cover 2018-2025.",
            "vintage": (
                "Custom historical-area recomputation sums GDP and source-implied population "
                "before division. Makati excludes EMBO barangays in 2022-2024."
            ),
        }
    )
    write_json("indicators.json", indicators)

    existing = json.loads((PUBLIC_DATA / "manifest.json").read_text())
    row_counts = existing.get("row_counts", {})
    row_counts.update(
        {
            "provinces": len(provinces_out),
            "population": len(population),
            "gdp_per_capita": len(gdp),
            "poverty_depth": len(poverty_depth),
            "poverty_depth_coverage": len(poverty_depth_status),
        }
    )
    inputs = existing.get("inputs", {})
    inputs["psa_openstat"] = {
        "source": psa_openstat.API_BASE,
        "release": "PPA updated 2026-08-28; poverty-depth tables updated 2024-08-15",
        "population_2024_path": "1A/PO_2024/0191A6DTHP8.px",
        "ppa_industry_contract": ppa_industry,
        "ppa_paths": [
            "2A/PPA/0012A5FPPA0.px",
            "2A/PPA/0022A5FPPA1.px",
            "2A/PPA/0092A5FPPA8.px",
        ],
        "poverty_depth_paths": [
            PSA_TABLES[table].reviewed_fallbacks[0] for table in psa_openstat.POVERTY_DEPTH_MEASURES
        ],
        "row_counts": {
            "population_2020": len(population_2020),
            "population_2024": len(population_2024),
            "gdp": len(gdp),
            "poverty_depth": len(poverty_depth),
        },
    }
    write_json(
        "manifest.json",
        build_manifest(row_counts=row_counts, derived=existing.get("derived", {}), inputs=inputs),
    )


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
    procurement_status = write_procurement_status()
    write_json("view_evidence.json", build_view_evidence())
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

    existing = json.loads((PUBLIC_DATA / "manifest.json").read_text())
    inputs = existing.get("inputs", {})
    inputs["philgeps_snapshot"] = procurement_status
    inputs.setdefault(
        "psgc",
        {
            "source": "https://psa.gov.ph/classification/psgc/provinces",
            "release": "Second Quarter 2026 PSGC",
            "as_of": "2026-06-30",
            "note": (
                "Official identity source; historical analysis IDs remain in "
                "geography-crosswalk.json."
            ),
        },
    )
    psa_inputs = inputs.get("psa_openstat")
    if isinstance(psa_inputs, dict):
        industry = psa_inputs.get("ppa_industry_contract", {})
        psa_inputs["ppa_industry_contract"] = {
            **industry,
            "years": list(range(2018, 2026)),
            "decimals": 12,
            "suppression_markers": ["-", "..", "...", "/s"],
            "valuations": ["At Current Prices", "At Constant 2018 Prices"],
        }
    manifest = build_manifest(row_counts=row_counts, derived=derived, inputs=inputs)
    write_json("manifest.json", manifest)
    print(f"refreshed manifest over {len(files)} files; built_at {manifest['built_at']}")
    if derived:
        print(f"  dpwh attributed total: PHP {derived['dpwh_attributed_php_total']:,}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="dataviz.ph ETL build")
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Clear PSA and PSGC caches. PhilGEPS uses a separately acquired reviewed snapshot.",
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
