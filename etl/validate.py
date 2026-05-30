"""Sanity checks on tidy long-form rows. Fail loud on bounds + completeness."""

from __future__ import annotations

SCHEMAS = {
    "rate_pct": {"min": 0, "max": 95},
    "peso_per_capita": {"min": 0, "max": 200_000},
    "peso_per_capita_gdp": {"min": 1_000, "max": 1_500_000},
    "population": {"min": 1, "max": 30_000_000},
    "share_pct": {"min": 0, "max": 100},
    "yoy_pct": {"min": -20, "max": 30},
    "delta_pp": {"min": -100, "max": 100},
}


def validate_all(rows: list[dict], schema: str) -> None:
    """Bounds check on every row's value. Raises ValueError on first violation."""
    if schema not in SCHEMAS:
        raise ValueError(f"Unknown schema: {schema}")
    lo, hi = SCHEMAS[schema]["min"], SCHEMAS[schema]["max"]
    for r in rows:
        v = r.get("value")
        if v is None:
            raise ValueError(f"Null value in {schema} row: {r}")
        if not (lo <= v <= hi):
            raise ValueError(f"Out-of-bounds {schema} value {v} (expected [{lo},{hi}]) in row: {r}")


def validate_precision(rows: list[dict], tolerance: float = 0.25) -> None:
    """Sanity-check the PSA measures of precision on rows that carry them.

    For any row with a 95% CI, the published incidence must sit inside its own
    interval (allowing a small tolerance for PSA's independent rounding of each
    bound), the interval must be ordered lo <= hi, and the CV must be non-negative.
    Raises ValueError on the first violation so a malformed precision pull fails
    the build instead of silently shipping nonsense uncertainty bands.
    """
    for r in rows:
        cv = r.get("cv")
        if cv is not None and cv < 0:
            raise ValueError(f"Negative coefficient of variation {cv} in row: {r}")
        lo = r.get("ci_lo")
        hi = r.get("ci_hi")
        if lo is None and hi is None:
            continue
        if lo is None or hi is None:
            raise ValueError(f"Half-open confidence interval in row: {r}")
        if lo > hi:
            raise ValueError(f"Inverted confidence interval [{lo}, {hi}] in row: {r}")
        v = r["value"]
        if not (lo - tolerance <= v <= hi + tolerance):
            raise ValueError(f"Incidence {v} falls outside its 95% CI [{lo}, {hi}] in row: {r}")
