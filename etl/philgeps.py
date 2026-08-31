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


def _chunk_path(i: int) -> Path:
    return CACHE_DIR / f"facts_awards_chunk_{i:02d}.parquet"


def _inventory_path() -> Path:
    return CACHE_DIR / INVENTORY_FILENAME


def _chunk_url(i: int) -> str:
    return f"{CHUNK_BASE}/facts_awards_chunk_{i:02d}.parquet"


def write_snapshot_inventory(*, fetched_at: str | None = None) -> dict:
    """Inventory a complete local snapshot without contacting the upstream mirror."""
    chunks: dict[str, dict[str, int | str]] = {}
    for i in range(1, N_CHUNKS + 1):
        chunk = _chunk_path(i)
        if not chunk.exists():
            raise FileNotFoundError(
                f"Missing PhilGEPS chunk {i}. Acquire a complete snapshot first."
            )
        chunks[f"chunk_{i:02d}"] = {
            "sha256": hashlib.sha256(chunk.read_bytes()).hexdigest(),
            "row_count": pq.ParquetFile(chunk).metadata.num_rows,
        }
    inventory = {
        "schema_version": 1,
        "upstream_identity": {
            "name": "csiiiv/philgeps-awards-dashboard mirror",
            "url": CHUNK_BASE,
            "chunk_count": N_CHUNKS,
        },
        "fetched_at": fetched_at or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "correction_policy": SNAPSHOT_CORRECTION_POLICY,
        "revision_status": "not compared to a newer snapshot",
        "supported_date_range": {"start": PANEL_START, "end": PANEL_END},
        "chunks": chunks,
    }
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _inventory_path().write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n")
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
        "chunks",
    }
    missing = required_keys - inventory.keys()
    if missing:
        raise ValueError(
            f"PhilGEPS snapshot inventory missing required fields: {sorted(missing)!r}"
        )
    return inventory


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
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
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
        for i in range(1, N_CHUNKS + 1):
            os.replace(stage_dir / _chunk_path(i).name, _chunk_path(i))
    return write_snapshot_inventory()


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

    big["psgc"] = big["area_of_delivery"].apply(lambda s: normalize_name(s, provinces))
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
