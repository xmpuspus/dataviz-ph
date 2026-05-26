"""Behavioural tests for the cache TTL + clear_cache + manifest builder.

Covers:
- _cache_fresh returns False past the TTL window
- _cache_fresh returns True for a freshly-touched file
- clear_cache wipes every .json under the cache dir
- build_manifest emits the expected schema with sha256 + row_counts
"""

import time
from pathlib import Path

from etl import psa_openstat, psgc
from etl.build import build_manifest


def test_cache_fresh_returns_true_for_new_file(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{}")
    assert psa_openstat._cache_fresh(p, ttl_days=30) is True


def test_cache_fresh_returns_false_for_stale_file(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{}")
    # Set mtime to 31 days ago
    old = time.time() - 31 * 86400
    import os

    os.utime(p, (old, old))
    assert psa_openstat._cache_fresh(p, ttl_days=30) is False


def test_cache_fresh_returns_false_for_missing_file(tmp_path):
    assert psa_openstat._cache_fresh(tmp_path / "nope.json", ttl_days=30) is False


def test_clear_cache_removes_json_files(tmp_path, monkeypatch):
    monkeypatch.setattr(psa_openstat, "CACHE_DIR", tmp_path)
    (tmp_path / "a.json").write_text("{}")
    (tmp_path / "b.json").write_text("{}")
    (tmp_path / "keep.txt").write_text("not a cache file")
    psa_openstat.clear_cache()
    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert remaining == ["keep.txt"]


def test_psgc_clear_cache_removes_json_files(tmp_path, monkeypatch):
    monkeypatch.setattr(psgc, "CACHE_DIR", tmp_path)
    (tmp_path / "provinces.json").write_text("[]")
    psgc.clear_cache()
    assert not (tmp_path / "provinces.json").exists()


def test_build_manifest_schema():
    """Smoke test against the actual public/data/ files; covers happy path end-to-end."""
    m = build_manifest(row_counts={"provinces": 82, "poverty": 902})
    assert isinstance(m["built_at"], str) and "T" in m["built_at"]
    assert isinstance(m["source_vintages"], dict)
    assert "poverty" in m["source_vintages"]
    assert "cpi" in m["source_vintages"]
    assert m["row_counts"]["provinces"] == 82
    assert isinstance(m["sha256_per_file"], dict)
    # Every entry must be a 64-char hex sha256 digest
    for filename, sha in m["sha256_per_file"].items():
        assert filename.endswith(".json")
        assert len(sha) == 64
        int(sha, 16)  # raises ValueError if not hex


def test_build_manifest_excludes_itself():
    """manifest.json must not list itself (the file isn't written yet when sha is computed)."""
    public_data = Path("public/data")
    if not public_data.exists():
        return  # tests can run in a fresh checkout without data
    m = build_manifest(row_counts={})
    assert "manifest.json" not in m["sha256_per_file"]
