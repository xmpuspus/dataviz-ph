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
                "award_status": [None, None],
                "source_system": [None, None],
                "award_title": ["road", "road"],
                "notice_title": ["road", "road"],
                "business_category": ["construction", "construction"],
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


def test_snapshot_inventory_requires_candidate_evidence_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(philgeps, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(philgeps, "N_CHUNKS", 1)
    pq.write_table(
        pa.Table.from_pydict(
            {
                "id": [1],
                "award_date": ["2025-01-01"],
                "contract_amount": [10.0],
                "organization_name": ["DPWH"],
                "area_of_delivery": ["Cebu"],
                "award_status": [None],
                "award_title": ["road repair"],
                "notice_title": ["road repair"],
                "business_category": ["construction"],
            }
        ),
        tmp_path / "facts_awards_chunk_01.parquet",
    )

    with pytest.raises(ValueError, match="source_system"):
        philgeps.write_snapshot_inventory(fetched_at="2026-01-01T00:00:00Z")


def test_reviewed_candidate_years_do_not_follow_chart_panel(tmp_path, monkeypatch):
    monkeypatch.setattr(philgeps, "N_CHUNKS", 1)
    table = pa.Table.from_pydict(
        {
            "id": [1],
            "award_date": ["2025-01-01"],
            "contract_amount": [10.0],
            "organization_name": ["DPWH"],
            "area_of_delivery": ["Cebu"],
            "award_status": [None],
            "source_system": [None],
            "award_title": ["road repair"],
            "notice_title": ["road repair"],
            "business_category": ["construction"],
        }
    )
    pq.write_table(table, tmp_path / "facts_awards_chunk_01.parquet")

    first = philgeps.build_snapshot_inventory(tmp_path, fetched_at="2026-08-31T00:00:00Z")
    monkeypatch.setattr(philgeps, "PANEL_END", 2030)
    second = philgeps.build_snapshot_inventory(tmp_path, fetched_at="2026-08-31T00:00:00Z")

    assert philgeps.REVIEWED_CANDIDATE_YEARS == (2025,)
    assert first["anomalies"] == second["anomalies"]
    assert first["year_candidates"] == second["year_candidates"]
    assert philgeps.snapshot_identity(first) == philgeps.snapshot_identity(second)


def test_correction_attestation_requires_bound_review_evidence():
    attestation = {
        "prior_snapshot_id": "prior",
        "current_snapshot_id": "current",
        "reviewed_at": "2026-08-31T00:00:00Z",
        "status": "reviewed",
        "result": "no_material_corrections",
    }

    assert philgeps.validate_correction_attestation(attestation, "current") == "reviewed"
    attestation["current_snapshot_id"] = "other"
    with pytest.raises(ValueError, match="current snapshot"):
        philgeps.validate_correction_attestation(attestation, "current")


def test_year_gate_labels_snapshot_wide_anomalies_separately_from_candidate_rows():
    inventory = {
        "snapshot_id": "current",
        "anomalies": {"invalid_award_date_count": 1, "future_award_date_count": 11},
        "correction_attestation": {
            "prior_snapshot_id": None,
            "current_snapshot_id": "current",
            "reviewed_at": None,
            "status": "pending",
            "result": "not_compared",
        },
        "year_candidates": {
            "2025": {
                "unique_award_id_count": 12,
                "date_range": {"start": "2025-01-01", "end": "2025-12-27"},
                "month_counts": {str(month): 1 for month in range(1, 13)},
            }
        },
    }

    gate = philgeps.assess_year_gate(inventory, 2025)

    assert "invalid_award_dates" in gate["failed_gates"]
    assert "future_award_dates" in gate["failed_gates"]
    assert gate["snapshot_anomalies"] == {
        "scope": "snapshot_wide",
        "invalid_award_date_count": 1,
        "future_award_date_count": 11,
    }
    assert "invalid_award_date_count" not in gate["candidate_evidence"]


def test_year_gate_fails_closed_when_reviewed_snapshot_is_incomplete_or_unreviewed():
    inventory = {
        "snapshot_id": "current",
        "revision_status": "compared with a newer snapshot and reviewed",
        "anomalies": {"invalid_award_date_count": 1, "future_award_date_count": 11},
        "correction_attestation": {
            "prior_snapshot_id": None,
            "current_snapshot_id": "current",
            "reviewed_at": None,
            "status": "pending",
            "result": "not_compared",
        },
        "year_candidates": {
            "2025": {
                "unique_award_id_count": 2,
                "date_range": {"start": "2025-01-01", "end": "2025-12-27"},
                "month_counts": {str(month): 1 for month in range(1, 13)},
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


def test_year_gate_rejects_missing_required_candidate_evidence():
    gate = philgeps.assess_year_gate(
        {
            "snapshot_id": "current",
            "anomalies": {"invalid_award_date_count": 0, "future_award_date_count": 0},
            "correction_attestation": {
                "prior_snapshot_id": "prior",
                "current_snapshot_id": "current",
                "reviewed_at": "2026-08-31T00:00:00Z",
                "status": "reviewed",
                "result": "no_material_corrections",
            },
            "year_candidates": {"2025": {"date_range": {}}},
        },
        2025,
    )

    assert gate["status"] == "unavailable"
    assert gate["failed_gates"] == [
        "date_range_incomplete",
        "month_counts_incomplete",
        "usable_unique_award_ids_missing",
    ]


def test_snapshot_inventory_derives_candidate_evidence_with_one_utc_timestamp(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(philgeps, "N_CHUNKS", 1)
    table = pa.Table.from_pydict(
        {
            "id": [1, 2],
            "award_date": ["2025-01-01", "2026-01-02"],
            "contract_amount": [10.0, 10.0],
            "organization_name": ["PUBLIC WORKS AND HIGHWAYS", "OTHER"],
            "area_of_delivery": ["Cebu", "Cebu"],
            "award_status": [None, "posted"],
            "source_system": [None, "source"],
            "award_title": ["road repair", "office supplies"],
            "notice_title": ["road repair", "office supplies"],
            "business_category": ["construction", "goods"],
        }
    )
    pq.write_table(table, tmp_path / "facts_awards_chunk_01.parquet")

    inventory = philgeps.build_snapshot_inventory(tmp_path, fetched_at="2026-01-01T08:00:00+08:00")

    candidate = inventory["year_candidates"]["2025"]
    assert inventory["fetched_at"] == "2026-01-01T00:00:00Z"
    assert inventory["anomalies"]["future_award_date_count"] == 1
    assert candidate["month_counts"] == {
        str(month): 1 if month == 1 else 0 for month in range(1, 13)
    }
    assert candidate["unique_award_id_count"] == 1
    assert "invalid_award_date_count" not in candidate
    assert "future_award_date_count" not in candidate
    assert inventory["correction_attestation"]["status"] == "pending"


def test_processing_rejects_hand_edited_candidate_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(philgeps, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(philgeps, "N_CHUNKS", 1)
    reviewed = tmp_path / "reviewed.json"
    monkeypatch.setattr(philgeps, "REVIEWED_INVENTORY_PATH", reviewed)
    table = pa.Table.from_pydict(
        {
            "id": [1],
            "award_date": ["2025-01-01"],
            "contract_amount": [10.0],
            "organization_name": ["DPWH"],
            "area_of_delivery": ["Cebu"],
            "award_status": [None],
            "source_system": [None],
            "award_title": ["road repair"],
            "notice_title": ["road repair"],
            "business_category": ["construction"],
        }
    )
    pq.write_table(table, tmp_path / "facts_awards_chunk_01.parquet")
    inventory = philgeps.build_snapshot_inventory(tmp_path, fetched_at="2026-08-31T00:00:00Z")
    inventory["year_candidates"]["2025"]["unique_award_id_count"] = 999
    philgeps._write_inventory(reviewed, inventory)

    with pytest.raises(ValueError, match="year_candidates differs"):
        philgeps.verify_reviewed_snapshot()


def test_processing_accepts_a_bound_reviewed_correction_attestation(tmp_path, monkeypatch):
    monkeypatch.setattr(philgeps, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(philgeps, "N_CHUNKS", 1)
    reviewed = tmp_path / "reviewed.json"
    monkeypatch.setattr(philgeps, "REVIEWED_INVENTORY_PATH", reviewed)
    pq.write_table(
        pa.Table.from_pydict(
            {
                "id": [1],
                "award_date": ["2025-01-01"],
                "contract_amount": [10.0],
                "organization_name": ["DPWH"],
                "area_of_delivery": ["Cebu"],
                "award_status": [None],
                "source_system": [None],
                "award_title": ["road repair"],
                "notice_title": ["road repair"],
                "business_category": ["construction"],
            }
        ),
        tmp_path / "facts_awards_chunk_01.parquet",
    )
    inventory = philgeps.build_snapshot_inventory(tmp_path, fetched_at="2026-08-31T00:00:00Z")
    inventory["correction_attestation"] = {
        "prior_snapshot_id": "prior-reviewed-snapshot",
        "current_snapshot_id": inventory["snapshot_id"],
        "reviewed_at": "2026-08-31T00:00:00Z",
        "status": "reviewed",
        "result": "no_material_corrections",
    }
    philgeps._write_inventory(reviewed, inventory)

    assert philgeps.verify_reviewed_snapshot() == inventory


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


def test_snapshot_identity_changes_when_candidate_or_attestation_changes():
    inventory = {
        "snapshot_id": "reviewed",
        "supported_date_range": {"start": "2014-01-01", "end": "2034-01-01"},
        "anomalies": {"invalid_award_date_count": 1, "future_award_date_count": 11},
        "year_candidates": {"2025": {"unique_award_id_count": 1}},
        "correction_attestation": {
            "prior_snapshot_id": None,
            "current_snapshot_id": "reviewed",
            "reviewed_at": None,
            "status": "pending",
            "result": "not_compared",
        },
    }
    first = philgeps.snapshot_identity(inventory)
    inventory["year_candidates"]["2025"]["unique_award_id_count"] = 2
    assert philgeps.snapshot_identity(inventory) != first
    inventory["correction_attestation"]["status"] = "reviewed"
    assert philgeps.snapshot_identity(inventory) != first


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
            "award_status": [None],
            "source_system": [None],
            "award_title": ["road repair"],
            "notice_title": ["road repair"],
            "business_category": ["construction"],
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
                "award_status": [None],
                "source_system": [None],
                "award_title": ["road repair"],
                "notice_title": ["road repair"],
                "business_category": ["construction"],
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
            "award_status": [None],
            "source_system": [None],
            "award_title": ["road"],
            "notice_title": ["road"],
            "business_category": ["construction"],
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
