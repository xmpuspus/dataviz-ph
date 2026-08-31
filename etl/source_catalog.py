"""Reviewed, metadata-validated source contracts for PSA OpenStat tables.

Leaf filenames in PXWeb are mutable publication details.  The catalog therefore
describes the published table rather than treating a filename as the contract.
Reviewed fallback paths preserve a reproducible route when directory discovery
is unavailable, but are accepted only after the same metadata validation.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass


class SourceContractDriftError(RuntimeError):
    """Raised when an upstream table no longer meets a reviewed source contract."""


@dataclass(frozen=True)
class TableContract:
    """The stable attributes dataviz.ph requires from one PSA table."""

    name: str
    directory: str
    title_terms: tuple[str, ...]
    dimensions: tuple[str, ...]
    measure_terms: tuple[str, ...]
    expected_years: tuple[int, ...]
    vintage_terms: tuple[str, ...]
    fixed_source_year: int | None
    reviewed_fallbacks: tuple[str, ...]


PSA_TABLES: dict[str, TableContract] = {
    "poverty": TableContract(
        name="poverty",
        directory="1F/FY",
        title_terms=("table 1a", "poverty incidence", "famil"),
        dimensions=("Geolocation", "Threshold/Incidence/Parameters", "Year"),
        measure_terms=("poverty incidence",),
        expected_years=(2018, 2021, 2023),
        vintage_terms=(),
        fixed_source_year=None,
        reviewed_fallbacks=("1F/FY/0021F3DF01A.px",),
    ),
    "subsistence": TableContract(
        name="subsistence",
        directory="1F/FY",
        title_terms=("table 3a", "subsistence incidence", "famil"),
        dimensions=("Geolocation", "Threshold/Incidence/Measures of Precision", "Year"),
        measure_terms=("subsistence incidence",),
        expected_years=(2018, 2021, 2023),
        vintage_terms=(),
        fixed_source_year=None,
        reviewed_fallbacks=("1F/FY/0061F3DF03A.px",),
    ),
    "population": TableContract(
        name="population",
        directory="1A/PO_2020",
        title_terms=("population",),
        dimensions=("Geographic Location", "Parameter"),
        measure_terms=("total population",),
        expected_years=(),
        vintage_terms=("2020",),
        fixed_source_year=2020,
        reviewed_fallbacks=("1A/PO_2020/0011A6DPHH0.px",),
    ),
    "population_2024": TableContract(
        name="population_2024",
        directory="1A/PO_2024",
        title_terms=("total population", "philippines", "2024"),
        dimensions=("Geographic Location", "Parameter"),
        measure_terms=("total population",),
        expected_years=(),
        vintage_terms=("2024",),
        fixed_source_year=2024,
        reviewed_fallbacks=("1A/PO_2024/0191A6DTHP8.px",),
    ),
    "gdp_total": TableContract(
        name="gdp_total",
        directory="2A/PPA",
        title_terms=("gross domestic product", "province", "huc"),
        dimensions=("Geolocation", "Type of Valuation", "Year"),
        measure_terms=("constant", "2018"),
        expected_years=tuple(range(2018, 2026)),
        vintage_terms=(),
        fixed_source_year=None,
        reviewed_fallbacks=("2A/PPA/0012A5FPPA0.px",),
    ),
    "gdp_industry": TableContract(
        name="gdp_industry",
        directory="2A/PPA",
        title_terms=("gross domestic product", "industry", "province", "huc"),
        dimensions=("Sector", "Geolocation", "Type of Valuation", "Year"),
        measure_terms=("constant", "2018"),
        expected_years=tuple(range(2018, 2026)),
        vintage_terms=(),
        fixed_source_year=None,
        reviewed_fallbacks=("2A/PPA/0022A5FPPA1.px",),
    ),
    "gdp_per_capita": TableContract(
        name="gdp_per_capita",
        directory="2A/PPA",
        title_terms=("per capita", "gross domestic product"),
        dimensions=("Geolocation", "Type of Valuation", "Year"),
        measure_terms=("constant", "2018"),
        expected_years=tuple(range(2018, 2026)),
        vintage_terms=(),
        fixed_source_year=None,
        reviewed_fallbacks=("2A/PPA/0092A5FPPA8.px",),
    ),
    "cpi": TableContract(
        name="cpi",
        directory="2M/PI/CPI/2018NEW",
        title_terms=("consumer price index",),
        dimensions=("Geolocation", "Commodity Description", "Year", "Period"),
        measure_terms=("all items",),
        expected_years=(2018,),
        vintage_terms=(),
        fixed_source_year=None,
        reviewed_fallbacks=("2M/PI/CPI/2018NEW/0012M4ACP22.px",),
    ),
    "poverty_poor_families": TableContract(
        name="poverty_poor_families",
        directory="1F/FY",
        title_terms=("table 5a", "poor families"),
        dimensions=("Geolocation", "Estimates/Measures of Precision", "Year"),
        measure_terms=("estimate",),
        expected_years=(2018, 2021, 2023),
        vintage_terms=(),
        fixed_source_year=None,
        reviewed_fallbacks=("1F/FY/0101F3DF05A.px",),
    ),
    "poverty_income_gap": TableContract(
        name="poverty_income_gap",
        directory="1F/FY",
        title_terms=("table 10a", "income gap"),
        dimensions=("Geolocation", "Estimates/Measures of Precision", "Year"),
        measure_terms=("estimate",),
        expected_years=(2018, 2021, 2023),
        vintage_terms=(),
        fixed_source_year=None,
        reviewed_fallbacks=("1F/FY/0191F3DF10A.px",),
    ),
    "poverty_poverty_gap": TableContract(
        name="poverty_poverty_gap",
        directory="1F/FY",
        title_terms=("table 11a", "poverty gap"),
        dimensions=("Geolocation", "Estimates/Measures of Precision", "Year"),
        measure_terms=("estimate",),
        expected_years=(2018, 2021, 2023),
        vintage_terms=(),
        fixed_source_year=None,
        reviewed_fallbacks=("1F/FY/0211F3DF11A.px",),
    ),
    "poverty_severity": TableContract(
        name="poverty_severity",
        directory="1F/FY",
        title_terms=("table 12a", "severity of poverty"),
        dimensions=("Geolocation", "Estimates/Measures of Precision", "Year"),
        measure_terms=("estimate",),
        expected_years=(2018, 2021, 2023),
        vintage_terms=(),
        fixed_source_year=None,
        reviewed_fallbacks=("1F/FY/0231F3DF12A.px",),
    ),
}


def _variables_by_code(metadata: dict) -> dict[str, dict]:
    return {str(variable.get("code", "")): variable for variable in metadata.get("variables", [])}


def validate_table_metadata(contract: TableContract, metadata: dict) -> None:
    """Fail with all observable contract differences, rather than guessing a table."""
    title = str(metadata.get("title") or metadata.get("text") or "").lower()
    variables = _variables_by_code(metadata)
    problems: list[str] = []
    missing_title_terms = [term for term in contract.title_terms if term not in title]
    if missing_title_terms:
        problems.append(f"title missing {missing_title_terms!r}")
    missing_dimensions = [
        dimension for dimension in contract.dimensions if dimension not in variables
    ]
    if missing_dimensions:
        problems.append(f"dimensions missing {missing_dimensions!r}")
    measure_text = " ".join(
        text.lower()
        for variable in variables.values()
        for text in variable.get("valueTexts", [])
        if isinstance(text, str)
    )
    missing_measures = [term for term in contract.measure_terms if term not in measure_text]
    if missing_measures:
        problems.append(f"measures missing {missing_measures!r}")
    missing_vintage_terms = [
        term for term in contract.vintage_terms if term not in title + measure_text
    ]
    if missing_vintage_terms:
        problems.append(f"vintage missing {missing_vintage_terms!r}")
    if contract.expected_years:
        year_variable = variables.get("Year", {})
        year_values = [*year_variable.get("values", []), *year_variable.get("valueTexts", [])]
        years = {int(value) for value in year_values if str(value).isdigit()}
        missing_years = sorted(set(contract.expected_years) - years)
        if missing_years:
            problems.append(f"expected years missing {missing_years!r}")
    if problems:
        raise SourceContractDriftError(
            f"PSA {contract.name} contract drift: " + "; ".join(problems)
        )


def resolve_reviewed_table(
    contract: TableContract,
    discovered_paths: Iterable[str],
    fetch_metadata: Callable[[str], dict],
) -> str:
    """Choose the first valid discovered table, then a versioned reviewed fallback.

    Invalid discoveries are intentionally skipped.  If no candidate passes, the
    final error contains the per-path contract failures for an actionable review.
    """
    candidates = list(dict.fromkeys([*discovered_paths, *contract.reviewed_fallbacks]))
    failures: list[str] = []
    for path in candidates:
        try:
            validate_table_metadata(contract, fetch_metadata(path))
        except Exception as exc:
            failures.append(f"{path}: {exc}")
            continue
        return path
    detail = "; ".join(failures) if failures else "no paths discovered"
    raise SourceContractDriftError(f"No compatible PSA {contract.name} table: {detail}")
