"""PhilGEPS awards ETL.

Two fetchers:
- fetch_dpwh_spend: contracts where organization_name contains 'PUBLIC WORKS AND HIGHWAYS'.
- fetch_all_spend: every PhilGEPS contract attributable to a province via area_of_delivery.

Both reuse the 15 cached parquet chunks under .etl_cache/philgeps/ (~620 MB on disk),
attribute via area_of_delivery -> PSGC, group by (province, year), and divide by 2020
Census whole-province population for per-capita output.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

CHUNK_BASE = (
    "https://raw.githubusercontent.com/csiiiv/philgeps-awards-dashboard/"
    "main/backend/django/static_data/chunks"
)
N_CHUNKS = 15
CACHE_DIR = Path(__file__).resolve().parent.parent / ".etl_cache" / "philgeps"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

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
) -> list[dict]:
    frames: list[pd.DataFrame] = []
    for i in range(1, N_CHUNKS + 1):
        if not _chunk_path(i).exists():
            raise FileNotFoundError(
                f"Missing PhilGEPS chunk {i}. Pre-fetch the chunks first."
            )
        df = _read_chunk(i)
        if org_filter is not None:
            df = org_filter(df)
            if df.empty:
                continue
        df = _attach_year(df)
        df = df[df["year"].between(PANEL_START, PANEL_END)]
        if df.empty:
            continue
        df = df[["area_of_delivery", "year", "contract_amount"]]
        frames.append(df)

    if not frames:
        return []

    big = pd.concat(frames, ignore_index=True)
    big["psgc"] = big["area_of_delivery"].apply(lambda s: normalize_name(s, provinces))
    big = big.dropna(subset=["psgc", "contract_amount"])

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
    return rows


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
) -> list[dict]:
    """DPWH contracts grouped by (province, year), divided by 2020 population."""
    return _aggregate(_dpwh_filter, provinces, normalize_name, population_by_psgc, "dpwh")


def fetch_doh_spend(
    provinces: dict,
    normalize_name,
    population_by_psgc: dict[str, int],
) -> list[dict]:
    """DOH-attributable contracts per (province, year), per capita."""
    return _aggregate(_doh_filter, provinces, normalize_name, population_by_psgc, "doh")


def fetch_infra_spend(
    provinces: dict,
    normalize_name,
    population_by_psgc: dict[str, int],
) -> list[dict]:
    """Infra-tagged contracts (construction, road, bridge, flood, drainage, etc.) per capita."""
    return _aggregate(_infra_filter, provinces, normalize_name, population_by_psgc, "infra")


def fetch_all_spend(
    provinces: dict,
    normalize_name,
    population_by_psgc: dict[str, int],
) -> list[dict]:
    """All province-attributable PhilGEPS contracts per (province, year), per capita.

    No agency filter; covers every implementing agency whose awards can be tied to a
    PSGC via area_of_delivery. About 20 percent of all PhilGEPS award value carries
    no province tag (null / 'Independent City' / multi-province / 'Philippines') and
    is excluded.
    """
    return _aggregate(None, provinces, normalize_name, population_by_psgc, "all")
