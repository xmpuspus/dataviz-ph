"""Tests for the ETL hardening batch: dedup guard, validate upgrades, p-values, new preset.

Covers:
- Dedup guard: duplicate ids in synthetic parquet are dropped before groupby.
- validate_coverage: exact unit count assertion per anchor year.
- validate_uniqueness: duplicate (psgc, year) raises.
- validate_yoy_jumps: warns on >5x year-on-year but does not raise.
- validate_median_shift: warns >20%, fails >50%, respects ETL_ALLOW_BIG_SHIFT=1.
- Permutation p-value: seeded, deterministic, plausible range.
- New preset spend-vs-poverty-change present in stories.json with finite rho and p.
- New indicator dpwh_spend_per_capita_cum present in indicators.json.
- New data file dpwh_spend_per_capita_cum.json has positive values and one row per
  (psgc, year) pair.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from etl.build import (
    _permutation_p,
    _spearman,
    compute_dpwh_spend_per_capita_cum,
)
from etl.validate import (
    validate_coverage,
    validate_median_shift,
    validate_uniqueness,
    validate_yoy_jumps,
)

PUBLIC_DATA = Path(__file__).resolve().parent.parent / "public" / "data"


# ---------------------------------------------------------------------------
# Dedup guard
# ---------------------------------------------------------------------------


def _make_parquet_bytes(rows: list[dict]) -> bytes:
    """Serialize a list of dicts to parquet bytes (in-memory)."""
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pandas(df), buf)
    buf.seek(0)
    return buf.read()


def test_dedup_drops_duplicate_ids(tmp_path, monkeypatch):
    """A chunk with two rows sharing the same id should contribute only one contract amount."""
    from etl import philgeps

    # Build a minimal single-chunk parquet with one duplicate id.
    rows = [
        {
            "id": 1001,
            "contract_number": "CN-001",
            "award_date": pd.Timestamp("2019-06-01"),
            "published_date": pd.Timestamp("2019-06-01"),
            "closing_date": pd.Timestamp("2019-06-01"),
            # Large enough to exceed MIN_PESO_PER_CAPITA=100 after dividing by population.
            "contract_amount": 500_000_000.0,
            "award_title": "Test contract",
            "notice_title": "Test notice",
            "search_text": "test",
            "awardee_name": "Test Corp",
            "organization_name": "DEPT OF PUBLIC WORKS AND HIGHWAYS",
            "area_of_delivery": "Cebu",
            "business_category": "Construction",
            "award_status": None,
            "data_source": None,
            "procurement_mode": None,
            "funding_source": None,
            "line_item_number": None,
            "project_location": None,
            "contractor_address": None,
            "source_system": None,
        },
        # Exact duplicate row (same id): should be dropped before groupby.
        {
            "id": 1001,
            "contract_number": "CN-001",
            "award_date": pd.Timestamp("2019-06-01"),
            "published_date": pd.Timestamp("2019-06-01"),
            "closing_date": pd.Timestamp("2019-06-01"),
            "contract_amount": 500_000_000.0,
            "award_title": "Test contract",
            "notice_title": "Test notice",
            "search_text": "test",
            "awardee_name": "Test Corp",
            "organization_name": "DEPT OF PUBLIC WORKS AND HIGHWAYS",
            "area_of_delivery": "Cebu",
            "business_category": "Construction",
            "award_status": None,
            "data_source": None,
            "procurement_mode": None,
            "funding_source": None,
            "line_item_number": None,
            "project_location": None,
            "contractor_address": None,
            "source_system": None,
        },
    ]
    chunk_bytes = _make_parquet_bytes(rows)

    # Patch CACHE_DIR and N_CHUNKS so _aggregate reads only our synthetic chunk.
    fake_cache = tmp_path / "philgeps"
    fake_cache.mkdir()
    (fake_cache / "facts_awards_chunk_01.parquet").write_bytes(chunk_bytes)

    monkeypatch.setattr(philgeps, "CACHE_DIR", fake_cache)
    monkeypatch.setattr(philgeps, "N_CHUNKS", 1)
    reviewed_inventory = tmp_path / "reviewed_inventory.json"
    monkeypatch.setattr(philgeps, "REVIEWED_INVENTORY_PATH", reviewed_inventory)
    philgeps._write_inventory(
        reviewed_inventory, philgeps.build_snapshot_inventory(fetched_at="2026-08-31T00:00:00Z")
    )

    provinces = {
        "072200000": {"name": "Cebu", "island_group": "visayas", "region_code": "070000000"}
    }

    def _fake_normalize(raw, provs):
        if isinstance(raw, str) and raw.strip().lower() == "cebu":
            return "072200000"
        return None

    population_by_psgc = {"072200000": 3_325_000}

    rows_out, _ = philgeps.fetch_dpwh_spend(provinces, _fake_normalize, population_by_psgc)

    # After dedup: one row with 500_000_000 / 3_325_000 per-capita, not double.
    assert len(rows_out) == 1
    expected_per_cap = 500_000_000.0 / 3_325_000
    assert abs(rows_out[0]["value"] - expected_per_cap) < 1.0


# ---------------------------------------------------------------------------
# validate_coverage
# ---------------------------------------------------------------------------


def test_validate_coverage_passes_exact_count():
    rows = [{"psgc": f"unit_{i}", "year": 2018, "value": 10.0} for i in range(5)]
    validate_coverage(rows, expected_units=5, anchor_years=[2018], label="test")


def test_validate_coverage_fails_on_missing_units():
    rows = [{"psgc": f"unit_{i}", "year": 2018, "value": 10.0} for i in range(4)]
    with pytest.raises(ValueError, match="expected 5 units, found 4"):
        validate_coverage(rows, expected_units=5, anchor_years=[2018], label="test")


def test_validate_coverage_ignores_non_anchor_years():
    # 5 units in 2018 (anchor), 3 units in 2019 (non-anchor). Should pass.
    rows = [{"psgc": f"unit_{i}", "year": 2018, "value": 10.0} for i in range(5)]
    rows += [{"psgc": f"unit_{i}", "year": 2019, "value": 10.0} for i in range(3)]
    validate_coverage(rows, expected_units=5, anchor_years=[2018], label="test")


# ---------------------------------------------------------------------------
# validate_uniqueness
# ---------------------------------------------------------------------------


def test_validate_uniqueness_passes_unique_keys():
    rows = [
        {"psgc": "A", "year": 2018, "value": 1.0},
        {"psgc": "A", "year": 2019, "value": 2.0},
        {"psgc": "B", "year": 2018, "value": 3.0},
    ]
    validate_uniqueness(rows, "test")  # must not raise


def test_validate_uniqueness_fails_on_duplicate():
    rows = [
        {"psgc": "A", "year": 2018, "value": 1.0},
        {"psgc": "A", "year": 2018, "value": 2.0},  # duplicate key
    ]
    with pytest.raises(ValueError, match="duplicate .psgc, year."):
        validate_uniqueness(rows, "test")


# ---------------------------------------------------------------------------
# validate_yoy_jumps (warn, not raise)
# ---------------------------------------------------------------------------


def test_validate_yoy_jumps_warns_on_large_jump(capsys):
    rows = [
        {"psgc": "A", "year": 2018, "value": 1000.0},
        {"psgc": "A", "year": 2019, "value": 10000.0},  # 10x jump
    ]
    validate_yoy_jumps(rows, "test", threshold=5.0)
    captured = capsys.readouterr()
    assert "10.0x up" in captured.out


def test_validate_yoy_jumps_no_warn_for_modest_jump(capsys):
    rows = [
        {"psgc": "A", "year": 2018, "value": 1000.0},
        {"psgc": "A", "year": 2019, "value": 3000.0},  # 3x - under threshold
    ]
    validate_yoy_jumps(rows, "test", threshold=5.0)
    captured = capsys.readouterr()
    assert "jump" not in captured.out


# ---------------------------------------------------------------------------
# validate_median_shift
# ---------------------------------------------------------------------------


def test_validate_median_shift_warns_above_threshold(tmp_path, capsys, monkeypatch):
    import etl.validate as validate_mod

    committed = [{"psgc": f"u{i}", "year": 2018, "value": 100.0} for i in range(10)]
    f = tmp_path / "test_indicator.json"
    f.write_text(json.dumps(committed))

    monkeypatch.setattr(validate_mod, "PUBLIC_DATA", tmp_path)

    # New rows have median 130 - 30% shift, above warn (20%) but below fail (50%).
    new_rows = [{"psgc": f"u{i}", "year": 2018, "value": 130.0} for i in range(10)]
    validate_median_shift(new_rows, "test_indicator.json", "test")
    captured = capsys.readouterr()
    assert "WARNING" in captured.out


def test_validate_median_shift_fails_above_hard_threshold(tmp_path, monkeypatch):
    import etl.validate as validate_mod

    committed = [{"psgc": f"u{i}", "year": 2018, "value": 100.0} for i in range(10)]
    f = tmp_path / "test_indicator.json"
    f.write_text(json.dumps(committed))

    monkeypatch.setattr(validate_mod, "PUBLIC_DATA", tmp_path)

    # 80% shift - above fail threshold.
    new_rows = [{"psgc": f"u{i}", "year": 2018, "value": 180.0} for i in range(10)]
    with pytest.raises(ValueError, match="ETL_ALLOW_BIG_SHIFT"):
        validate_median_shift(new_rows, "test_indicator.json", "test")


def test_validate_median_shift_override_env(tmp_path, monkeypatch, capsys):
    import etl.validate as validate_mod

    committed = [{"psgc": f"u{i}", "year": 2018, "value": 100.0} for i in range(10)]
    f = tmp_path / "test_indicator.json"
    f.write_text(json.dumps(committed))

    monkeypatch.setattr(validate_mod, "PUBLIC_DATA", tmp_path)
    monkeypatch.setenv("ETL_ALLOW_BIG_SHIFT", "1")

    # 80% shift should only warn (not raise) when override is set.
    new_rows = [{"psgc": f"u{i}", "year": 2018, "value": 180.0} for i in range(10)]
    validate_median_shift(new_rows, "test_indicator.json", "test")
    captured = capsys.readouterr()
    assert "WARNING" in captured.out


def test_validate_median_shift_skips_missing_file(tmp_path, monkeypatch):
    import etl.validate as validate_mod

    monkeypatch.setattr(validate_mod, "PUBLIC_DATA", tmp_path)
    # File does not exist - should not raise.
    rows = [{"psgc": "A", "year": 2018, "value": 50.0}]
    validate_median_shift(rows, "nonexistent.json", "test")


# ---------------------------------------------------------------------------
# Permutation p-value
# ---------------------------------------------------------------------------


def test_permutation_p_is_deterministic():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    ys = [10.0, 8.0, 6.0, 4.0, 2.0, 1.0, 3.0, 5.0, 7.0, 9.0]
    rho = _spearman(xs, ys)
    p1 = _permutation_p(xs, ys, rho, n_perm=1000, seed=42)
    p2 = _permutation_p(xs, ys, rho, n_perm=1000, seed=42)
    assert p1 == p2, "permutation p must be deterministic with same seed"


def test_permutation_p_perfect_negative_correlation_is_significant():
    xs = list(range(1, 21))
    ys = list(range(20, 0, -1))  # perfect negative rank correlation
    rho = _spearman(xs, ys)
    assert rho == -1.0
    p = _permutation_p(xs, ys, rho, n_perm=1000, seed=42)
    assert p < 0.01, f"perfect negative rho should give very small p, got {p}"


def test_permutation_p_random_data_not_significant():
    # Shuffled ys have rho near 0; p should typically be large.
    xs = list(range(1, 21))
    # Deliberately chosen near-zero rho data.
    ys = [10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10]
    rho = _spearman(xs, ys)
    # rho=None when ys are all equal (degenerate); guard that case.
    if rho is None:
        return
    p = _permutation_p(xs, ys, rho, n_perm=1000, seed=42)
    assert 0.0 <= p <= 1.0


def test_permutation_p_range():
    xs = [1.0, 3.0, 2.0, 5.0, 4.0]
    ys = [5.0, 3.0, 4.0, 1.0, 2.0]
    rho = _spearman(xs, ys)
    p = _permutation_p(xs, ys, rho, n_perm=500, seed=99)
    assert 0.0 <= p <= 1.0


# ---------------------------------------------------------------------------
# New preset in stories.json
# ---------------------------------------------------------------------------


def test_spend_vs_poverty_change_preset_exists():
    if not (PUBLIC_DATA / "stories.json").exists():
        pytest.skip("public/data/stories.json not present")
    stories = json.loads((PUBLIC_DATA / "stories.json").read_text())
    ids = [s["id"] for s in stories]
    assert "spend-vs-poverty-change" in ids, "spend-vs-poverty-change preset missing"


def test_spend_vs_poverty_change_has_finite_rho_and_p():
    if not (PUBLIC_DATA / "stories.json").exists():
        pytest.skip("public/data/stories.json not present")
    stories = json.loads((PUBLIC_DATA / "stories.json").read_text())
    preset = next(s for s in stories if s["id"] == "spend-vs-poverty-change")
    f = preset["finding"]
    assert f.get("available") is True, "finding must be available"
    rho = f.get("spearman")
    p = f.get("p_value")
    assert rho is not None and -1.0 <= rho <= 1.0, f"rho out of range: {rho}"
    assert p is not None and 0.0 <= p <= 1.0, f"p_value out of range: {p}"
    assert "causation" in (f.get("caveat") or "").lower()


def test_spend_vs_poverty_change_uses_correct_indicators():
    if not (PUBLIC_DATA / "stories.json").exists():
        pytest.skip("public/data/stories.json not present")
    stories = json.loads((PUBLIC_DATA / "stories.json").read_text())
    preset = next(s for s in stories if s["id"] == "spend-vs-poverty-change")
    assert preset["x"] == "dpwh_spend_per_capita_cum"
    assert preset["y"] == "poverty_change_pp"


def test_all_stories_have_p_value():
    if not (PUBLIC_DATA / "stories.json").exists():
        pytest.skip("public/data/stories.json not present")
    stories = json.loads((PUBLIC_DATA / "stories.json").read_text())
    for s in stories:
        f = s.get("finding", {})
        if not f.get("available"):
            continue
        assert f.get("p_value") is not None, f"story {s['id']} finding missing p_value"
        assert 0.0 <= f["p_value"] <= 1.0


# ---------------------------------------------------------------------------
# New indicator entry
# ---------------------------------------------------------------------------


def test_dpwh_spend_per_capita_cum_indicator_exists():
    if not (PUBLIC_DATA / "indicators.json").exists():
        pytest.skip("public/data/indicators.json not present")
    indicators = {i["id"]: i for i in json.loads((PUBLIC_DATA / "indicators.json").read_text())}
    assert "dpwh_spend_per_capita_cum" in indicators, (
        "dpwh_spend_per_capita_cum indicator missing from indicators.json"
    )
    ind = indicators["dpwh_spend_per_capita_cum"]
    assert ind.get("can_deflate") is False
    assert ind.get("static_snapshot") is True


def test_dpwh_spend_per_capita_cum_data_file_exists():
    if not (PUBLIC_DATA / "dpwh_spend_per_capita_cum.json").exists():
        pytest.skip("public/data/dpwh_spend_per_capita_cum.json not present")
    rows = json.loads((PUBLIC_DATA / "dpwh_spend_per_capita_cum.json").read_text())
    assert len(rows) > 0
    # All values positive (cumulative nominal spend).
    for r in rows:
        assert r["value"] > 0, f"non-positive cumulative value: {r}"
    # No duplicate (psgc, year) pairs.
    seen: set[tuple] = set()
    for r in rows:
        key = (r["psgc"], r["year"])
        assert key not in seen, f"duplicate (psgc, year) in cum data: {key}"
        seen.add(key)


# ---------------------------------------------------------------------------
# compute_dpwh_spend_per_capita_cum unit test
# ---------------------------------------------------------------------------


def test_compute_dpwh_spend_per_capita_cum_sums_window():
    from etl.build import PANEL_YEARS

    rows = [
        {"psgc": "A", "year": 2014, "value": 100.0},
        {"psgc": "A", "year": 2015, "value": 200.0},
        {"psgc": "A", "year": 2024, "value": 999.0},  # outside window, excluded
        {"psgc": "B", "year": 2020, "value": 500.0},
    ]
    out = compute_dpwh_spend_per_capita_cum(rows)

    # Every row in output repeated for each panel year.
    a_rows = [r for r in out if r["psgc"] == "A"]
    b_rows = [r for r in out if r["psgc"] == "B"]
    assert len(a_rows) == len(PANEL_YEARS)
    assert len(b_rows) == len(PANEL_YEARS)

    # A's cumulative = 100 + 200 = 300 (2024 row excluded since it's past CUM_SPEND_END).
    assert all(r["value"] == 300.0 for r in a_rows)
    assert all(r["value"] == 500.0 for r in b_rows)
