"""Tests for the PSA measures-of-precision pipeline (95% CI + CV).

Covers:
- validate_precision rejects inverted intervals, out-of-CI values, negative CV
- linear_fill carries precision onto anchor years ONLY (model-estimate years
  deliberately carry no survey precision)
- the shipped poverty/subsistence files honour those invariants on disk
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from etl.interpolate import linear_fill
from etl.validate import validate_precision

PUBLIC_DATA = Path(__file__).resolve().parent.parent / "public" / "data"


def test_validate_precision_accepts_well_formed_ci():
    rows = [{"psgc": "x", "year": 2018, "value": 12.1, "cv": 1.4, "ci_lo": 11.8, "ci_hi": 12.5}]
    validate_precision(rows)  # must not raise


def test_validate_precision_rejects_inverted_interval():
    rows = [{"psgc": "x", "year": 2018, "value": 12.1, "ci_lo": 12.5, "ci_hi": 11.8}]
    with pytest.raises(ValueError, match="Inverted"):
        validate_precision(rows)


def test_validate_precision_rejects_value_outside_ci():
    rows = [{"psgc": "x", "year": 2018, "value": 40.0, "ci_lo": 11.8, "ci_hi": 12.5}]
    with pytest.raises(ValueError, match="outside its 95% CI"):
        validate_precision(rows)


def test_validate_precision_rejects_negative_cv():
    rows = [{"psgc": "x", "year": 2018, "value": 12.1, "cv": -3.0}]
    with pytest.raises(ValueError, match="Negative coefficient"):
        validate_precision(rows)


def test_validate_precision_rejects_half_open_interval():
    rows = [{"psgc": "x", "year": 2018, "value": 12.1, "ci_lo": 11.8}]
    with pytest.raises(ValueError, match="Half-open"):
        validate_precision(rows)


def test_linear_fill_carries_precision_on_anchors_only():
    """Precision rides anchor years; interpolated/extrapolated years carry none."""
    rows = [
        {"psgc": "p", "year": 2018, "value": 20.0, "cv": 5.0, "ci_lo": 18.0, "ci_hi": 22.0},
        {"psgc": "p", "year": 2023, "value": 10.0, "cv": 6.0, "ci_lo": 8.0, "ci_hi": 12.0},
    ]
    out = linear_fill(
        rows,
        anchor_years=[2018, 2023],
        target_years=[2014, 2018, 2020, 2023, 2024],
        carry_fields=["cv", "se", "ci_lo", "ci_hi"],
    )
    by_year = {r["year"]: r for r in out}

    # Anchor years keep the published CI.
    assert by_year[2018]["ci_lo"] == 18.0 and by_year[2018]["ci_hi"] == 22.0
    assert by_year[2023]["cv"] == 6.0

    # Interpolated (2020) and extrapolated (2014, 2024) years carry no precision.
    for yr in (2014, 2020, 2024):
        assert "ci_lo" not in by_year[yr]
        assert "ci_hi" not in by_year[yr]
        assert "cv" not in by_year[yr]


@pytest.mark.parametrize("name", ["poverty.json", "subsistence.json"])
def test_shipped_incidence_files_have_valid_anchor_ci(name):
    """On disk: anchor rows carry an ordered CI containing the value; non-anchor
    rows carry none (we never fabricate a CI for a year PSA did not measure)."""
    rows = json.loads((PUBLIC_DATA / name).read_text())
    anchors = [r for r in rows if not r["interp"] and not r["extrap"]]
    assert anchors, f"{name} has no anchor rows"
    # At least some anchor rows ship a CI (PSA reports it for most province-years).
    with_ci = [r for r in anchors if "ci_lo" in r]
    assert len(with_ci) > 0.5 * len(anchors), f"{name}: too few anchor rows carry a CI"
    validate_precision(rows)  # ordering + containment hold across the file

    for r in rows:
        if r["interp"] or r["extrap"]:
            assert "ci_lo" not in r and "ci_hi" not in r, (
                f"{name}: a non-anchor row carries a CI it should not have: {r}"
            )
