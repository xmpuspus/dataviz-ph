"""Offline tests for PhilGEPS snapshot acquisition and inventory."""

from __future__ import annotations

import hashlib
import io

import httpx
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from etl import philgeps


def test_snapshot_inventory_records_identity_policy_range_hashes_and_row_counts(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(philgeps, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(philgeps, "N_CHUNKS", 1)
    table = pa.Table.from_pandas(
        pd.DataFrame({"id": [1, 2], "award_date": ["2024-01-01", "2024-02-01"]})
    )
    chunk = tmp_path / "facts_awards_chunk_01.parquet"
    pq.write_table(table, chunk)

    inventory = philgeps.write_snapshot_inventory(fetched_at="2026-08-31T00:00:00Z")

    assert inventory["upstream_identity"]["url"] == philgeps.CHUNK_BASE
    assert inventory["fetched_at"] == "2026-08-31T00:00:00Z"
    assert inventory["correction_policy"]
    assert inventory["supported_date_range"] == {"start": 2014, "end": 2024}
    assert (
        inventory["chunks"]["chunk_01"]["sha256"] == hashlib.sha256(chunk.read_bytes()).hexdigest()
    )
    assert inventory["chunks"]["chunk_01"]["row_count"] == 2
    assert philgeps.load_snapshot_inventory() == inventory


def test_snapshot_inventory_requires_every_chunk(tmp_path, monkeypatch):
    monkeypatch.setattr(philgeps, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(philgeps, "N_CHUNKS", 2)

    with pytest.raises(FileNotFoundError, match="Missing PhilGEPS chunk 1"):
        philgeps.write_snapshot_inventory()


def test_acquisition_requires_explicit_network_authority():
    with pytest.raises(RuntimeError, match="offline snapshot"):
        philgeps.acquire_snapshot()


def test_acquisition_keeps_existing_snapshot_when_a_download_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(philgeps, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(philgeps, "N_CHUNKS", 2)
    existing = tmp_path / "facts_awards_chunk_01.parquet"
    existing.write_bytes(b"reviewed-snapshot")
    buffer = io.BytesIO()
    pq.write_table(pa.Table.from_pydict({"id": [1]}), buffer)

    class Response:
        content = buffer.getvalue()

        def raise_for_status(self):
            return None

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def get(self, url):
            if url.endswith("02.parquet"):
                raise httpx.ConnectError("source unavailable")
            return Response()

    monkeypatch.setattr(philgeps.httpx, "Client", lambda **_: Client())

    with pytest.raises(httpx.ConnectError, match="source unavailable"):
        philgeps.acquire_snapshot(allow_network=True)

    assert existing.read_bytes() == b"reviewed-snapshot"
