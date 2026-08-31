"""Tests for the computed per-story findings and the defensibility metadata.

Guards that every story ships a data-grounded answer (Spearman rho), that the
spend stories carry the awards-not-disbursement caveat, and that single-snapshot
and short-panel indicators are flagged so the UI can stop the animation lying.
"""

from __future__ import annotations

import json
from pathlib import Path

from etl.build import _pearson, _spearman

PUBLIC_DATA = Path(__file__).resolve().parent.parent / "public" / "data"


def _stories():
    return json.loads((PUBLIC_DATA / "stories.json").read_text())


def _indicators():
    return {i["id"]: i for i in json.loads((PUBLIC_DATA / "indicators.json").read_text())}


def test_spearman_is_scale_invariant():
    """Rank correlation must be unaffected by a monotonic transform (e.g. log)."""
    import math

    xs = [1.0, 2.0, 4.0, 8.0, 16.0]
    ys = [10.0, 8.0, 6.0, 4.0, 2.0]
    rho_raw = _spearman(xs, ys)
    rho_log = _spearman([math.log(x) for x in xs], ys)
    assert rho_raw == rho_log == -1.0


def test_pearson_perfect_correlation():
    assert abs(_pearson([1, 2, 3], [2, 4, 6]) - 1.0) < 1e-9
    assert abs(_pearson([1, 2, 3], [6, 4, 2]) + 1.0) < 1e-9


def test_every_story_has_a_grounded_finding():
    for s in _stories():
        f = s.get("finding")
        assert f is not None, f"story {s['id']} has no finding"
        assert f.get("available") is True, f"story {s['id']} finding unavailable"
        assert f["n"] >= 3, f"story {s['id']} finding has too few provinces"
        rho = f["spearman"]
        assert rho is None or -1.0 <= rho <= 1.0
        assert f["sentence"] and str(f["year"]) in f["sentence"], (
            f"story {s['id']} finding sentence must name its reference year"
        )
        assert "causation" in (f.get("caveat") or "").lower(), (
            f"story {s['id']} finding must carry the correlation-not-causation caveat"
        )


def test_spend_stories_carry_awards_caveat():
    award_ids = {"dpwh_spend_per_capita", "all_spend_per_capita"}
    for s in _stories():
        if s["x"] in award_ids or s["y"] in award_ids:
            cav = (s.get("awards_caveat") or "").lower()
            assert "award" in cav and "disburs" in cav, (
                f"spend story {s['id']} must state awards != disbursement"
            )


def test_snapshot_and_coverage_flags_present():
    ind = _indicators()
    assert ind["population"].get("static_snapshot") is False
    assert "2024" in ind["population"].get("snapshot_label", "")
    assert ind["poverty_change_pp"].get("static_snapshot") is True
    assert ind["poverty_change_pp"].get("snapshot_label")
    assert ind["gdp_per_capita"].get("coverage_label")
    assert ind["poverty"].get("has_ci") is True
    assert ind["subsistence_incidence"].get("has_ci") is True
