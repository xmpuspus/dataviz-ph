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
        pd.DataFrame(
            {
                "id": [1, 2],
                "award_date": ["2024-01-01", "2024-02-01"],
                "contract_amount": [1.0, 2.0],
                "organization_name": ["DPWH", "DPWH"],
                "area_of_delivery": ["Cebu", "Cebu"],
            }
        )
    )
    chunk = tmp_path / "facts_awards_chunk_01.parquet"
    pq.write_table(table, chunk)

    inventory = philgeps.write_snapshot_inventory(fetched_at="2026-08-31T00:00:00Z")

    assert inventory["upstream_identity"]["url"] == philgeps.CHUNK_BASE
    assert inventory["fetched_at"] == "2026-08-31T00:00:00Z"
    assert inventory["correction_policy"]
    assert inventory["supported_date_range"] == {"start": "2024-01-01", "end": "2024-02-01"}
    assert inventory["anomalies"]["invalid_award_date_count"] == 0
    assert inventory["usable_unique_award_id_count"] == 2
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


def test_snapshot_inventory_rejects_missing_production_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(philgeps, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(philgeps, "N_CHUNKS", 1)
    pq.write_table(
        pa.Table.from_pydict({"id": [1], "award_date": ["2024-01-01"]}),
        tmp_path / "facts_awards_chunk_01.parquet",
    )

    with pytest.raises(ValueError, match="required columns"):
        philgeps.write_snapshot_inventory()


def test_year_gate_fails_closed_when_reviewed_snapshot_is_incomplete_or_unreviewed():
    inventory = {
        "revision_status": "not compared to a newer snapshot",
        "year_candidates": {
            "2025": {
                "unique_award_id_count": 2,
                "date_range": {"start": "2025-01-01", "end": "2025-12-27"},
                "month_counts": {str(month): 1 for month in range(1, 13)},
                "invalid_award_date_count": 1,
                "future_award_date_count": 11,
                "candidate_series_coverage": {"all_spend": 82},
            }
        },
    }

    gate = philgeps.assess_year_gate(inventory, 2025)

    assert gate["status"] == "unavailable"
    assert gate["failed_gates"] == [
        "date_range_incomplete",
        "invalid_award_dates",
        "future_award_dates",
        "correction_comparison_pending",
    ]
    assert gate["month_counts"] == {str(month): 1 for month in range(1, 13)}


def test_snapshot_inventory_identity_does_not_depend_on_the_chart_panel(monkeypatch, tmp_path):
    inventory = {
        "snapshot_id": "reviewed",
        "supported_date_range": {"start": "2014-01-01", "end": "2034-01-01"},
        "anomalies": {"invalid_award_date_count": 1, "future_award_date_count": 11},
    }
    monkeypatch.setattr(philgeps, "PANEL_END", 2024)
    first = philgeps.snapshot_identity(inventory)
    monkeypatch.setattr(philgeps, "PANEL_END", 2030)

    assert philgeps.snapshot_identity(inventory) == first


def test_processing_rejects_a_cache_that_differs_from_its_reviewed_inventory(tmp_path, monkeypatch):
    monkeypatch.setattr(philgeps, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(philgeps, "N_CHUNKS", 1)
    reviewed = tmp_path / "reviewed.json"
    monkeypatch.setattr(philgeps, "REVIEWED_INVENTORY_PATH", reviewed)
    table = pa.Table.from_pydict(
        {
            "id": [1],
            "award_date": ["2024-01-01"],
            "contract_amount": [10.0],
            "organization_name": ["DPWH"],
            "area_of_delivery": ["Cebu"],
        }
    )
    chunk = tmp_path / "facts_awards_chunk_01.parquet"
    pq.write_table(table, chunk)
    philgeps._write_inventory(
        reviewed, philgeps.build_snapshot_inventory(fetched_at="2026-08-31T00:00:00Z")
    )
    pq.write_table(
        pa.Table.from_pydict(
            {
                "id": [2],
                "award_date": ["2024-01-01"],
                "contract_amount": [10.0],
                "organization_name": ["DPWH"],
                "area_of_delivery": ["Cebu"],
            }
        ),
        chunk,
    )

    with pytest.raises(ValueError, match="PhilGEPS cache does not match"):
        philgeps.verify_reviewed_snapshot()


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


def test_acquisition_rolls_back_if_promotion_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(philgeps, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(philgeps, "N_CHUNKS", 2)
    table = pa.Table.from_pydict(
        {
            "id": [1],
            "award_date": ["2024-01-01"],
            "contract_amount": [1.0],
            "organization_name": ["DPWH"],
            "area_of_delivery": ["Cebu"],
        }
    )
    old_bytes = []
    for index in (1, 2):
        path = tmp_path / f"facts_awards_chunk_{index:02d}.parquet"
        pq.write_table(table, path)
        old_bytes.append(path.read_bytes())
    buffer = io.BytesIO()
    pq.write_table(table, buffer)

    class Response:
        content = buffer.getvalue()

        def raise_for_status(self):
            return None

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def get(self, _url):
            return Response()

    real_replace = philgeps.os.replace

    def fail_second_promotion(source, destination):
        if (
            source.name == "facts_awards_chunk_02.parquet"
            and source.parent != tmp_path
            and source.parent.name != "backup"
        ):
            raise OSError("disk full")
        return real_replace(source, destination)

    monkeypatch.setattr(philgeps.httpx, "Client", lambda **_: Client())
    monkeypatch.setattr(philgeps.os, "replace", fail_second_promotion)

    with pytest.raises(OSError, match="disk full"):
        philgeps.acquire_snapshot(allow_network=True)

    assert (tmp_path / "facts_awards_chunk_01.parquet").read_bytes() == old_bytes[0]
    assert (tmp_path / "facts_awards_chunk_02.parquet").read_bytes() == old_bytes[1]
