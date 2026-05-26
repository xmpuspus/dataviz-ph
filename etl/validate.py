"""Sanity checks on tidy long-form rows. Fail loud on bounds + completeness."""

from __future__ import annotations

SCHEMAS = {
    "rate_pct": {"min": 0, "max": 95},
    "peso_per_capita": {"min": 0, "max": 200_000},
    "peso_per_capita_gdp": {"min": 1_000, "max": 1_500_000},
    "population": {"min": 1, "max": 30_000_000},
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
