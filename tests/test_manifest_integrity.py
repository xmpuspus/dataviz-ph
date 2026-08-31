"""Guards against the manifest silently drifting from the shipped data files.

Regression for 2026-05-29: the gapminder restructure hand-edited stories.json
and added pair_headlines.json without re-running the build, so manifest.json
recorded a stale sha256 for stories.json and omitted pair_headlines.json
entirely. The manifest is dataviz.ph's only stale-deploy detector, so a stale
manifest is a data-integrity bug, not a cosmetic one.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from etl.source_catalog import PSA_TABLES

PUBLIC_DATA = Path(__file__).resolve().parent.parent / "public" / "data"


def _manifest() -> dict:
    return json.loads((PUBLIC_DATA / "manifest.json").read_text())


def test_every_data_file_is_in_manifest_with_matching_hash() -> None:
    man = _manifest()
    shas = man["sha256_per_file"]
    on_disk = sorted(p for p in PUBLIC_DATA.glob("*.json") if p.name != "manifest.json")
    for f in on_disk:
        assert f.name in shas, f"{f.name} ships but is absent from manifest.sha256_per_file"
        actual = hashlib.sha256(f.read_bytes()).hexdigest()
        assert actual == shas[f.name], (
            f"{f.name} sha256 drifted from manifest: run `python -m etl.build --manifest-only`"
        )


def test_manifest_references_no_missing_file() -> None:
    man = _manifest()
    for name in man["sha256_per_file"]:
        assert (PUBLIC_DATA / name).exists(), f"manifest references missing file {name}"


def test_every_data_file_has_a_manifest_row_count() -> None:
    man = _manifest()
    row_counts = man["row_counts"]
    on_disk = sorted(p for p in PUBLIC_DATA.glob("*.json") if p.name != "manifest.json")
    for path in on_disk:
        assert path.stem in row_counts, f"{path.name} has no manifest row count"


def test_manifest_records_each_observed_psa_table_identity() -> None:
    tables = _manifest()["inputs"]["psa_openstat"]["tables"]
    assert set(tables) == set(PSA_TABLES)
    for record in tables.values():
        assert record["resolved_path"]
        assert record["upstream_title"]
        assert record["fetched_at"]
        assert len(record["metadata_sha256"]) == 64
        assert record["expected_update"]


def test_dpwh_headline_matches_computed_total() -> None:
    """The DPWH story headline cites a peso figure; it must match the data.

    The chart plots the attributed DPWH subset; its reconstructed total is stored
    in manifest.derived. The headline word must match that magnitude so the
    flagship claim can never silently drift (it once read 'two trillion' against
    a 5.04-trillion total).
    """
    man = _manifest()
    total = man.get("derived", {}).get("dpwh_attributed_php_total")
    assert total is not None, "manifest.derived.dpwh_attributed_php_total missing"
    trillions = total / 1e12
    assert 4.5 <= trillions <= 5.5, f"DPWH total {trillions:.2f}T outside the 'five trillion' band"

    stories = json.loads((PUBLIC_DATA / "stories.json").read_text())
    dpwh = next(s for s in stories if s["x"] == "dpwh_spend_per_capita" and s["y"] == "poverty")
    headline = dpwh["headline"].lower()
    assert "five trillion" in headline, (
        f"DPWH headline {dpwh['headline']!r} must say 'five trillion' to match the "
        f"computed {trillions:.2f}T total"
    )
    assert "two trillion" not in headline, "DPWH headline still says the stale 'two trillion'"


def test_manifest_publishes_every_per_area_attribution_share() -> None:
    """The BARMM lower-bound claim needs its per-area evidence in the manifest.

    ``main()`` writes ``dpwh_attribution_share_by_psgc`` for all 82 areas.
    ``refresh_manifest()`` rebuilds ``derived`` from disk, so it used to drop the
    key and ship a manifest that states a lower bound with nothing behind it.
    """
    shares = _manifest().get("derived", {}).get("dpwh_attribution_share_by_psgc")
    assert isinstance(shares, dict), "manifest.derived.dpwh_attribution_share_by_psgc missing"
    provinces = json.loads((PUBLIC_DATA / "provinces.json").read_text())
    assert set(shares) == set(provinces), "attribution shares do not cover every shipped area"
    assert all(isinstance(v, int | float) and v >= 0 for v in shares.values())


def test_manifest_only_refresh_keeps_the_attribution_shares(tmp_path) -> None:
    """A manifest-only refresh must not delete what only a full build can compute."""
    from etl import build

    before = _manifest()
    kept = build.carry_forward_derived(before.get("derived", {}), {"dpwh_attributed_php_total": 1})
    assert (
        kept["dpwh_attribution_share_by_psgc"]
        == before["derived"]["dpwh_attribution_share_by_psgc"]
    )
    assert kept["dpwh_attributed_php_total"] == 1


def test_the_about_twenty_percent_unattributed_claim_matches_the_shipped_shares() -> None:
    """Six places in the prose say about 20 percent of award value is unattributed.

    The per-area shares are the evidence for that sentence. They sum to the
    attributed fraction, so the unattributed remainder must round to 20 percent.
    A future snapshot that shifts attribution has to move the prose with it.
    """
    shares = _manifest()["derived"]["dpwh_attribution_share_by_psgc"]
    attributed = sum(shares.values())
    unattributed_pct = (1 - attributed) * 100
    assert 15 <= unattributed_pct <= 25, (
        f"the prose says about 20 percent unattributed, the shares say "
        f"{unattributed_pct:.1f} percent. Update both together."
    )
