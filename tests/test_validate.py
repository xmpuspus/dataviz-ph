"""Behavioural tests for etl.validate.

Covers:
- Schema bounds enforcement (raises on out-of-range)
- Null values raise rather than silently ship
- Unknown schema raises
- Boundary values (min, max) pass cleanly
"""

import pytest

from etl.validate import SCHEMAS, validate_all


def test_rate_pct_accepts_in_bounds():
    rows = [
        {"psgc": "A", "year": 2020, "value": 0.0},
        {"psgc": "B", "year": 2020, "value": 50.0},
        {"psgc": "C", "year": 2020, "value": 95.0},
    ]
    validate_all(rows, schema="rate_pct")  # must not raise


def test_rate_pct_rejects_negative():
    with pytest.raises(ValueError, match="Out-of-bounds"):
        validate_all([{"psgc": "X", "year": 2020, "value": -1.0}], schema="rate_pct")


def test_rate_pct_rejects_above_max():
    with pytest.raises(ValueError, match="Out-of-bounds"):
        validate_all([{"psgc": "X", "year": 2020, "value": 100.0}], schema="rate_pct")


def test_null_value_raises():
    with pytest.raises(ValueError, match="Null value"):
        validate_all([{"psgc": "X", "year": 2020, "value": None}], schema="rate_pct")


def test_unknown_schema_raises():
    with pytest.raises(ValueError, match="Unknown schema"):
        validate_all([{"psgc": "X", "year": 2020, "value": 1.0}], schema="not_a_schema")


def test_peso_per_capita_accepts_zero():
    """A real coverage gap legitimately produces 0; validator must accept zero."""
    validate_all(
        [{"psgc": "X", "year": 2020, "value": 0}],
        schema="peso_per_capita",
    )


def test_peso_per_capita_rejects_implausible_high():
    """Anything over PHP 200_000/cap is almost certainly a join bug."""
    with pytest.raises(ValueError, match="Out-of-bounds"):
        validate_all(
            [{"psgc": "X", "year": 2020, "value": 250_000}], schema="peso_per_capita"
        )


def test_population_accepts_full_range():
    """1 -> 30M covers every PSGC unit (NCR is the largest at ~13M)."""
    validate_all([{"psgc": "X", "year": 2020, "value": 13_484_462}], schema="population")
    validate_all([{"psgc": "Y", "year": 2020, "value": 1}], schema="population")


def test_schemas_constants_are_well_formed():
    """Defensive: every schema in SCHEMAS has both min and max as numbers."""
    for name, spec in SCHEMAS.items():
        assert "min" in spec and "max" in spec, f"{name} missing bounds"
        assert isinstance(spec["min"], (int, float))
        assert isinstance(spec["max"], (int, float))
        assert spec["min"] < spec["max"], f"{name} has inverted bounds"
