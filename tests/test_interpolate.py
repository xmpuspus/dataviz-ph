"""Behavioural tests for etl.interpolate.linear_fill.

Covers:
- Two-anchor linear interpolation (Sulu shape: 75.3 -> 41.5 -> 13.0)
- Single-anchor extrapolation (held constant across the panel)
- extrap=True before first anchor and after last anchor
- interp=True strictly between anchors, False at anchor years
- Multi-province independence (each psgc fills independently)
"""

from etl.interpolate import linear_fill


def _by_year(rows, psgc):
    return {r["year"]: r for r in rows if r["psgc"] == psgc}


def test_two_anchor_linear_fill_matches_sulu_shape():
    """Sulu: 75.3 (2018) -> 41.5 (2021) -> 13.0 (2023). Midpoints should land on the line."""
    rows = [
        {"psgc": "156600000", "year": 2018, "value": 75.3},
        {"psgc": "156600000", "year": 2021, "value": 41.5},
        {"psgc": "156600000", "year": 2023, "value": 13.0},
    ]
    out = linear_fill(rows, anchor_years=[2018, 2021, 2023], target_years=list(range(2014, 2025)))
    by_year = _by_year(out, "156600000")

    # Anchor years unchanged
    assert by_year[2018]["value"] == 75.3
    assert by_year[2021]["value"] == 41.5
    assert by_year[2023]["value"] == 13.0
    assert by_year[2018]["interp"] is False
    assert by_year[2021]["interp"] is False
    assert by_year[2023]["interp"] is False

    # 2019 sits 1/3 of the way from 2018 (75.3) to 2021 (41.5): 75.3 - (75.3-41.5)/3 ~ 64.03
    assert abs(by_year[2019]["value"] - (75.3 - (75.3 - 41.5) / 3)) < 1e-6
    assert by_year[2019]["interp"] is True
    assert by_year[2019]["extrap"] is False

    # 2022 sits halfway between 2021 (41.5) and 2023 (13.0): 27.25
    assert abs(by_year[2022]["value"] - ((41.5 + 13.0) / 2)) < 1e-6
    assert by_year[2022]["interp"] is True


def test_extrapolation_before_and_after_panel():
    """Years before the first anchor and after the last hold constant + extrap=True."""
    rows = [
        {"psgc": "144400000", "year": 2018, "value": 17.1},
        {"psgc": "144400000", "year": 2023, "value": 10.5},
    ]
    out = linear_fill(rows, anchor_years=[2018, 2023], target_years=[2014, 2015, 2018, 2023, 2024])
    by_year = _by_year(out, "144400000")

    # Before first anchor: held to 2018 value, extrap=True
    assert by_year[2014]["value"] == 17.1
    assert by_year[2014]["extrap"] is True
    assert by_year[2014]["interp"] is False
    assert by_year[2015]["value"] == 17.1
    assert by_year[2015]["extrap"] is True

    # After last anchor: held to 2023 value, extrap=True
    assert by_year[2024]["value"] == 10.5
    assert by_year[2024]["extrap"] is True

    # Anchor years
    assert by_year[2018]["extrap"] is False
    assert by_year[2023]["extrap"] is False


def test_single_anchor_holds_constant_across_panel():
    """A province with only one anchor year extrapolates that value across every target year."""
    rows = [{"psgc": "999999999", "year": 2021, "value": 50.0}]
    out = linear_fill(rows, anchor_years=[2018, 2021, 2023], target_years=list(range(2014, 2025)))
    by_year = _by_year(out, "999999999")
    # Every target year present, every value = 50.0
    assert len(by_year) == 11
    for year, row in by_year.items():
        assert row["value"] == 50.0
        if year == 2021:
            assert row["extrap"] is False
            assert row["interp"] is False
        else:
            assert row["extrap"] is True
            assert row["interp"] is False


def test_province_with_no_anchors_is_dropped():
    """A psgc that has zero anchor years contributes zero rows to output."""
    rows = [{"psgc": "888888888", "year": 2019, "value": 30.0}]  # 2019 is not an anchor
    out = linear_fill(rows, anchor_years=[2018, 2021, 2023], target_years=[2018, 2019, 2020])
    assert all(r["psgc"] != "888888888" for r in out)


def test_multi_province_independence():
    """Filling Sulu does not affect Mountain Province values or vice versa."""
    rows = [
        {"psgc": "156600000", "year": 2018, "value": 75.3},
        {"psgc": "156600000", "year": 2023, "value": 13.0},
        {"psgc": "144400000", "year": 2018, "value": 17.1},
        {"psgc": "144400000", "year": 2023, "value": 10.5},
    ]
    out = linear_fill(rows, anchor_years=[2018, 2023], target_years=[2020])
    sulu = next(r for r in out if r["psgc"] == "156600000" and r["year"] == 2020)
    mp = next(r for r in out if r["psgc"] == "144400000" and r["year"] == 2020)
    # Sulu 2020: 75.3 + 0.4 * (13.0 - 75.3) = 50.38
    assert abs(sulu["value"] - (75.3 + 0.4 * (13.0 - 75.3))) < 1e-6
    # MP 2020: 17.1 + 0.4 * (10.5 - 17.1) = 14.46
    assert abs(mp["value"] - (17.1 + 0.4 * (10.5 - 17.1))) < 1e-6
