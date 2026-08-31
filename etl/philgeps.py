"""PhilGEPS awards ETL.

Two fetchers:
- fetch_dpwh_spend: contracts where organization_name contains 'PUBLIC WORKS AND HIGHWAYS'.
- fetch_all_spend: every PhilGEPS contract attributable to a province via area_of_delivery.

Both reuse the 15 cached parquet chunks under .etl_cache/philgeps/ (~620 MB on disk),
attribute via area_of_delivery -> PSGC, group by (province, year), and divide by 2020
Census whole-province population for per-capita output.

Dedup note: PhilGEPS assigns a globally unique integer 'id' to each award notice. The
15 chunks are non-overlapping slices of the full dataset (verified: 0 id overlap across
chunks). We de-duplicate on 'id' before groupby to guard against any future re-crawl
overlap. contract_number is NOT a reliable dedup key - it is human-typed, nullable, and
legitimately repeated for multi-lot contracts (lot A, lot B of the same tender).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pandas as pd
import pyarrow.parquet as pq

CHUNK_BASE = (
    "https://raw.githubusercontent.com/csiiiv/philgeps-awards-dashboard/"
    "main/backend/django/static_data/chunks"
)
N_CHUNKS = 15
CACHE_DIR = Path(__file__).resolve().parent.parent / ".etl_cache" / "philgeps"
INVENTORY_FILENAME = "snapshot_inventory.json"
REVIEWED_INVENTORY_PATH = Path(__file__).with_name("philgeps_snapshot_inventory.json")
REQUIRED_COLUMNS = frozenset(
    {"id", "award_date", "contract_amount", "organization_name", "area_of_delivery"}
)
SNAPSHOT_CORRECTION_POLICY = (
    "Cached chunks are immutable inputs. Acquire a new snapshot explicitly, compare "
    "hashes and row counts, then review source corrections before rebuilding published data."
)

DPWH_PATTERN = "PUBLIC WORKS AND HIGHWAYS"
DOH_PATTERNS = ("DEPARTMENT OF HEALTH",)
INFRA_KEYWORDS = (
    "construction",
    "road",
    "bridge",
    "flood",
    "drainage",
    "highway",
    "concreting",
    "asphalt",
    "rehabilitation",
)
PANEL_START = 2014
PANEL_END = 2024
# Anything < this PHP/cap is almost certainly a coverage gap (typo in
# area_of_delivery, contract tagged with a city that we can't map, etc.).
# Treat it as missing data rather than publishing PHP 0.08 / cap.
MIN_PESO_PER_CAPITA = 100.0


def snapshot_identity(inventory: dict) -> str:
    """Return the reviewed snapshot identity without reference to chart years."""
    fields = {
        "snapshot_id": inventory.get("snapshot_id"),
        "supported_date_range": inventory.get("supported_date_range"),
        "anomalies": inventory.get("anomalies"),
        "chunks": inventory.get("chunks"),
    }
    return hashlib.sha256(
        json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def assess_year_gate(inventory: dict, year: int) -> dict:
    """Fail closed unless a reviewed candidate year meets every publication gate."""
    candidate = inventory.get("year_candidates", {}).get(str(year), {})
    date_range = candidate.get("date_range", {})
    failed_gates = []
    if date_range.get("start") != f"{year}-01-01" or date_range.get("end") != f"{year}-12-31":
        failed_gates.append("date_range_incomplete")
    invalid_dates = candidate.get(
        "invalid_award_date_count",
        inventory.get("anomalies", {}).get("invalid_award_date_count", 0),
    )
    if invalid_dates:
        failed_gates.append("invalid_award_dates")
    future_dates = candidate.get(
        "future_award_date_count", inventory.get("anomalies", {}).get("future_award_date_count", 0)
    )
    if future_dates:
        failed_gates.append("future_award_dates")
    if not candidate.get("unique_award_id_count"):
        failed_gates.append("usable_unique_award_ids_missing")
    if inventory.get("revision_status") != "compared with a newer snapshot and reviewed":
        failed_gates.append("correction_comparison_pending")
    return {
        "year": year,
        "status": "available" if not failed_gates else "unavailable",
        "failed_gates": failed_gates,
        "month_counts": candidate.get("month_counts", {}),
        "date_range": date_range,
        "unique_award_id_count": candidate.get("unique_award_id_count", 0),
        "invalid_award_date_count": invalid_dates,
        "future_award_date_count": future_dates,
        "candidate_series_coverage": candidate.get("candidate_series_coverage", {}),
        "award_status_null_count": candidate.get("award_status_null_count", 0),
        "source_system_null_count": candidate.get("source_system_null_count", 0),
        "revision_status": inventory.get("revision_status", "unknown"),
    }


def procurement_status(inventory: dict, *, candidate_year: int = PANEL_END + 1) -> dict:
    """Build the public status record without creating candidate chart rows."""
    gate = assess_year_gate(inventory, candidate_year)
    return {
        "status": gate["status"],
        "panel_start": PANEL_START,
        "panel_end": PANEL_END,
        "latest_complete_year": PANEL_END,
        "candidate_year": candidate_year,
        "failed_gates": gate["failed_gates"],
        "snapshot_id": inventory.get("snapshot_id"),
        "snapshot_identity": snapshot_identity(inventory),
        "fetched_at": inventory.get("fetched_at"),
        "revision_status": inventory.get("revision_status", "unknown"),
        "evidence": {
            key: gate[key]
            for key in (
                "date_range",
                "month_counts",
                "unique_award_id_count",
                "invalid_award_date_count",
                "future_award_date_count",
                "candidate_series_coverage",
                "award_status_null_count",
                "source_system_null_count",
            )
        },
    }


def _chunk_path(i: int) -> Path:
    return CACHE_DIR / f"facts_awards_chunk_{i:02d}.parquet"


def _inventory_path() -> Path:
    return CACHE_DIR / INVENTORY_FILENAME


def _chunk_url(i: int) -> str:
    return f"{CHUNK_BASE}/facts_awards_chunk_{i:02d}.parquet"


def _snapshot_inventory_path(cache_dir: Path) -> Path:
    return cache_dir / INVENTORY_FILENAME


def build_snapshot_inventory(
    cache_dir: Path | None = None, *, fetched_at: str | None = None
) -> dict:
    """Inspect a complete cache and return a validated, reproducible snapshot inventory."""
    root = cache_dir or CACHE_DIR
    chunks: dict[str, dict[str, int | str]] = {}
    snapshot_ids: set[int | str] = set()
    duplicate_id_count = 0
    schema_columns: list[str] | None = None
    min_date = None
    max_date = None
    invalid_date_count = 0
    future_award_date_count = 0
    for i in range(1, N_CHUNKS + 1):
        chunk = root / f"facts_awards_chunk_{i:02d}.parquet"
        if not chunk.exists():
            raise FileNotFoundError(
                f"Missing PhilGEPS chunk {i}. Acquire a complete snapshot first."
            )
        parquet = pq.ParquetFile(chunk)
        columns = sorted(parquet.schema.names)
        missing_columns = sorted(REQUIRED_COLUMNS - set(columns))
        if missing_columns:
            raise ValueError(f"PhilGEPS chunk {i} missing required columns: {missing_columns!r}")
        if schema_columns is None:
            schema_columns = columns
        elif columns != schema_columns:
            raise ValueError(f"PhilGEPS chunk {i} schema differs from the complete snapshot")
        if parquet.metadata.num_rows <= 0:
            raise ValueError(f"PhilGEPS chunk {i} is empty")
        values = parquet.read(columns=["id", "award_date"]).to_pandas()
        usable_ids = values["id"].notna() & values["id"].astype(str).str.strip().ne("")
        if not bool(usable_ids.all()):
            raise ValueError(f"PhilGEPS chunk {i} has unusable award ids")
        ids = values.loc[usable_ids, "id"].tolist()
        duplicate_id_count += sum(identifier in snapshot_ids for identifier in ids)
        snapshot_ids.update(ids)
        dates = pd.to_datetime(values["award_date"], errors="coerce", format="mixed", utc=True)
        invalid_date_count += int(dates.isna().sum())
        observed_dates = dates.dropna()
        if observed_dates.empty:
            raise ValueError(f"PhilGEPS chunk {i} has no usable award dates")
        chunk_min = observed_dates.min().date()
        chunk_max = observed_dates.max().date()
        min_date = chunk_min if min_date is None else min(min_date, chunk_min)
        max_date = chunk_max if max_date is None else max(max_date, chunk_max)
        fetched_date = pd.Timestamp(fetched_at or datetime.now(UTC).isoformat(), tz="UTC")
        future_award_date_count += int((observed_dates > fetched_date).sum())
        chunks[f"chunk_{i:02d}"] = {
            "sha256": hashlib.sha256(chunk.read_bytes()).hexdigest(),
            "row_count": parquet.metadata.num_rows,
            "usable_id_count": len(ids),
            "observed_date_range": {"start": chunk_min.isoformat(), "end": chunk_max.isoformat()},
        }
    chunk_digests = {name: item["sha256"] for name, item in chunks.items()}
    return {
        "schema_version": 2,
        "upstream_identity": {
            "name": "csiiiv/philgeps-awards-dashboard mirror",
            "url": CHUNK_BASE,
            "chunk_count": N_CHUNKS,
        },
        "fetched_at": fetched_at or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "correction_policy": SNAPSHOT_CORRECTION_POLICY,
        "revision_status": "not compared to a newer snapshot",
        "supported_date_range": {"start": min_date.isoformat(), "end": max_date.isoformat()},
        "anomalies": {
            "invalid_award_date_count": invalid_date_count,
            "future_award_date_count": future_award_date_count,
            "duplicate_id_count": duplicate_id_count,
        },
        "schema_columns": schema_columns,
        "snapshot_id": hashlib.sha256(
            json.dumps(chunk_digests, sort_keys=True).encode()
        ).hexdigest(),
        "usable_unique_award_id_count": len(snapshot_ids),
        "chunks": chunks,
    }


def _write_inventory(path: Path, inventory: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(path.suffix + ".tmp")
    staging.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n")
    os.replace(staging, path)


def write_snapshot_inventory(*, fetched_at: str | None = None) -> dict:
    """Write an inventory for the active local cache without contacting the mirror."""
    inventory = build_snapshot_inventory(fetched_at=fetched_at)
    _write_inventory(_inventory_path(), inventory)
    return inventory


def load_snapshot_inventory(*, required: bool = True) -> dict | None:
    """Read a locally recorded snapshot inventory, never fetching data as a side effect."""
    path = _inventory_path()
    if not path.exists():
        if required:
            raise FileNotFoundError(
                "PhilGEPS snapshot inventory is missing; inventory the cache first."
            )
        return None
    inventory = json.loads(path.read_text())
    required_keys = {
        "upstream_identity",
        "fetched_at",
        "correction_policy",
        "supported_date_range",
        "anomalies",
        "schema_columns",
        "snapshot_id",
        "chunks",
    }
    missing = required_keys - inventory.keys()
    if missing:
        raise ValueError(
            f"PhilGEPS snapshot inventory missing required fields: {sorted(missing)!r}"
        )
    return inventory


def load_reviewed_snapshot_inventory(*, required: bool = True) -> dict | None:
    """Read the tracked inventory that processing must match before aggregation."""
    if not REVIEWED_INVENTORY_PATH.exists():
        if required:
            raise FileNotFoundError("Reviewed PhilGEPS inventory is missing from the repository.")
        return None
    return json.loads(REVIEWED_INVENTORY_PATH.read_text())


def verify_reviewed_snapshot() -> dict:
    """Reject cache drift before processing can publish a mixed or changed snapshot."""
    reviewed = load_reviewed_snapshot_inventory()
    observed = build_snapshot_inventory(fetched_at=reviewed["fetched_at"])
    for key in ("snapshot_id", "schema_columns", "supported_date_range", "anomalies", "chunks"):
        if observed.get(key) != reviewed.get(key):
            raise ValueError(f"PhilGEPS cache does not match reviewed inventory: {key} differs")
    return reviewed


def acquire_snapshot(*, allow_network: bool = False) -> dict:
    """Download and inventory chunks only when an operator explicitly authorizes it.

    The build pipeline deliberately does not call this function.  A local cache is
    a reviewed snapshot, not a hidden downloader, so ``--no-cache`` cannot claim
    that it reacquired PhilGEPS data.
    """
    if not allow_network:
        raise RuntimeError(
            "PhilGEPS acquisition requires explicit network authority; "
            "use a reviewed offline snapshot."
        )
    CACHE_DIR.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="philgeps-acquire-", dir=CACHE_DIR.parent) as stage:
        stage_dir = Path(stage)
        with httpx.Client(timeout=180.0, follow_redirects=True) as client:
            for i in range(1, N_CHUNKS + 1):
                response = client.get(_chunk_url(i))
                response.raise_for_status()
                staged_chunk = stage_dir / _chunk_path(i).name
                staged_chunk.write_bytes(response.content)
                # Reject a malformed response before it can replace a reviewed chunk.
                _ = pq.ParquetFile(staged_chunk).metadata.num_rows
        inventory = build_snapshot_inventory(stage_dir)
        backup_dir = stage_dir / "backup"
        backup_dir.mkdir()
        moved_old: list[Path] = []
        promoted: list[Path] = []
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            for i in range(1, N_CHUNKS + 1):
                current = _chunk_path(i)
                if current.exists():
                    backup = backup_dir / current.name
                    os.replace(current, backup)
                    moved_old.append(backup)
            for i in range(1, N_CHUNKS + 1):
                current = _chunk_path(i)
                os.replace(stage_dir / current.name, current)
                promoted.append(current)
            _write_inventory(_inventory_path(), inventory)
        except Exception:
            for current in promoted:
                if current.exists():
                    current.unlink()
            for backup in moved_old:
                os.replace(backup, CACHE_DIR / backup.name)
            raise
    return inventory


def _read_chunk(i: int) -> pd.DataFrame:
    return pq.read_table(_chunk_path(i)).to_pandas()


def _attach_year(df: pd.DataFrame) -> pd.DataFrame:
    parsed = pd.to_datetime(df["award_date"], errors="coerce")
    df["year"] = parsed.dt.year
    # Drop garbage future dates (source data has stray 2034 entries).
    df = df[(df["year"] >= 1990) & (df["year"] <= PANEL_END + 1)]
    return df


def _aggregate(
    org_filter: Callable[[pd.DataFrame], pd.DataFrame] | None,
    provinces: dict,
    normalize_name,
    population_by_psgc: dict[str, int],
    label: str,
) -> tuple[list[dict], dict[str, float]]:
    """Aggregate awards per (province, year).

    Returns (rows, attribution_share) where attribution_share maps psgc ->
    fraction of the agency's raw peso total that was attributed to that province.
    Rows where area_of_delivery could not be mapped (null, multi-province, etc.)
    count toward the denominator but not the attributed numerator.
    """
    verify_reviewed_snapshot()
    frames: list[pd.DataFrame] = []
    for i in range(1, N_CHUNKS + 1):
        if not _chunk_path(i).exists():
            raise FileNotFoundError(f"Missing PhilGEPS chunk {i}. Pre-fetch the chunks first.")
        df = _read_chunk(i)
        if org_filter is not None:
            df = org_filter(df)
            if df.empty:
                continue
        df = _attach_year(df)
        df = df[df["year"].between(PANEL_START, PANEL_END)]
        if df.empty:
            continue
        frames.append(df)

    if not frames:
        return [], {}

    big = pd.concat(frames, ignore_index=True)

    # De-duplicate on the globally unique PhilGEPS award id. The 15 chunks are
    # non-overlapping by id (verified), but this guard protects against any future
    # re-crawl that re-slices the dataset differently.
    before_dedup = len(big)
    big = big.drop_duplicates(subset=["id"])
    after_dedup = len(big)
    if before_dedup != after_dedup:
        print(
            f"philgeps ({label}): dropped {before_dedup - after_dedup:,} duplicate ids "
            f"({before_dedup:,} -> {after_dedup:,} rows)"
        )

    # Total peso value before province attribution (denominator for coverage share).
    total_peso = big["contract_amount"].sum()

    big["psgc"] = big["area_of_delivery"].apply(
        lambda s: normalize_name(s, provinces, series="procurement")
    )
    big = big.dropna(subset=["psgc", "contract_amount"])

    # Per-province attributed total (numerator for coverage share).
    attributed_by_psgc = big.groupby("psgc")["contract_amount"].sum().to_dict()

    agg = (
        big.groupby(["psgc", "year"], as_index=False)["contract_amount"]
        .sum()
        .rename(columns={"contract_amount": "peso_total"})
    )

    rows: list[dict] = []
    dropped_low: list[tuple[str, int, float]] = []
    for _, r in agg.iterrows():
        psgc = r["psgc"]
        pop = population_by_psgc.get(psgc)
        if not pop:
            continue
        per_cap = float(r["peso_total"]) / pop
        if per_cap < MIN_PESO_PER_CAPITA:
            dropped_low.append((psgc, int(r["year"]), per_cap))
            continue
        rows.append({"psgc": psgc, "year": int(r["year"]), "value": per_cap})

    if dropped_low:
        msg = ", ".join(f"{p}/{y}=PHP{v:.2f}" for p, y, v in dropped_low[:6])
        print(
            f"philgeps ({label}): dropped {len(dropped_low)} province-years with "
            f"per-capita < PHP {MIN_PESO_PER_CAPITA:.0f} (likely coverage gaps): {msg}"
        )

    # Compute per-province attribution share (attributed psgc total / grand total).
    # total_peso is the filtered-agency total; if zero, no share can be computed.
    attribution_share: dict[str, float] = {}
    if total_peso > 0:
        for psgc, peso in attributed_by_psgc.items():
            attribution_share[psgc] = peso / total_peso

    return rows, attribution_share


def _dpwh_filter(df: pd.DataFrame) -> pd.DataFrame:
    org = df["organization_name"].fillna("").str.upper()
    return df[org.str.contains(DPWH_PATTERN)].copy()


def _doh_filter(df: pd.DataFrame) -> pd.DataFrame:
    org = df["organization_name"].fillna("").str.upper()
    mask = False
    for pat in DOH_PATTERNS:
        mask = (org.str.contains(pat)) | mask
    return df[mask].copy() if mask is not False else df.head(0)


def _infra_filter(df: pd.DataFrame) -> pd.DataFrame:
    # Combine the most descriptive free-text columns and match the infra keyword set.
    text = (
        df.get("award_title", "").fillna("").astype(str)
        + " "
        + df.get("notice_title", "").fillna("").astype(str)
        + " "
        + df.get("business_category", "").fillna("").astype(str)
    ).str.lower()
    pattern = "|".join(INFRA_KEYWORDS)
    return df[text.str.contains(pattern, regex=True, na=False)].copy()


def fetch_dpwh_spend(
    provinces: dict,
    normalize_name,
    population_by_psgc: dict[str, int],
) -> tuple[list[dict], dict[str, float]]:
    """DPWH contracts grouped by (province, year), divided by 2020 population.

    Returns (rows, attribution_share_by_psgc).
    """
    return _aggregate(_dpwh_filter, provinces, normalize_name, population_by_psgc, "dpwh")


def fetch_doh_spend(
    provinces: dict,
    normalize_name,
    population_by_psgc: dict[str, int],
) -> tuple[list[dict], dict[str, float]]:
    """DOH-attributable contracts per (province, year), per capita."""
    return _aggregate(_doh_filter, provinces, normalize_name, population_by_psgc, "doh")


def fetch_infra_spend(
    provinces: dict,
    normalize_name,
    population_by_psgc: dict[str, int],
) -> tuple[list[dict], dict[str, float]]:
    """Infra-tagged contracts (construction, road, bridge, flood, drainage, etc.) per capita."""
    return _aggregate(_infra_filter, provinces, normalize_name, population_by_psgc, "infra")


def fetch_all_spend(
    provinces: dict,
    normalize_name,
    population_by_psgc: dict[str, int],
) -> tuple[list[dict], dict[str, float]]:
    """All province-attributable PhilGEPS contracts per (province, year), per capita.

    No agency filter; covers every implementing agency whose awards can be tied to a
    PSGC via area_of_delivery. About 20 percent of all PhilGEPS award value carries
    no province tag (null / 'Independent City' / multi-province / 'Philippines') and
    is excluded.

    Returns (rows, attribution_share_by_psgc).
    """
    return _aggregate(None, provinces, normalize_name, population_by_psgc, "all")
