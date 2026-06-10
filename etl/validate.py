"""Sanity checks on tidy long-form rows.

Covers:
- validate_all: bounds check on every row's value (fails on first violation).
- validate_precision: sanity-check PSA CI/CV measures on rows that carry them.
- validate_coverage: exactly N area units present per (anchor year x indicator).
- validate_uniqueness: no duplicate (psgc, year) pairs per indicator file.
- validate_yoy_jumps: warn when a province's spend jumps more than 5x in one year.
- validate_median_shift: compare new build medians against committed files; warn
  above 20%, hard-fail above 50% (override with ETL_ALLOW_BIG_SHIFT=1).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

SCHEMAS = {
    "rate_pct": {"min": 0, "max": 95},
    "peso_per_capita": {"min": 0, "max": 200_000},
    "peso_per_capita_gdp": {"min": 1_000, "max": 1_500_000},
    "population": {"min": 1, "max": 30_000_000},
    "share_pct": {"min": 0, "max": 100},
    "yoy_pct": {"min": -20, "max": 30},
    "delta_pp": {"min": -100, "max": 100},
}

PUBLIC_DATA = Path(__file__).resolve().parent.parent / "public" / "data"


def validate_all(rows: list[dict], schema: str) -> None:
    """Bounds check on every row's value. Raises ValueError on first violation."""
    if schema not in SCHEMAS:
        raise ValueError(f"Unknown schema: {schema}")
    lo, hi = SCHEMAS[schema]["min"], SCHEMAS[schema]["max"]
    for r in rows:
        v = r.get("value")
        if v is None:
            raise ValueError(f"Null value in {schema} row: {r}")
        if not (lo <= v <= hi):
            raise ValueError(f"Out-of-bounds {schema} value {v} (expected [{lo},{hi}]) in row: {r}")


def validate_precision(rows: list[dict], tolerance: float = 0.25) -> None:
    """Sanity-check the PSA measures of precision on rows that carry them.

    For any row with a 95% CI, the published incidence must sit inside its own
    interval (allowing a small tolerance for PSA's independent rounding of each
    bound), the interval must be ordered lo <= hi, and the CV must be non-negative.
    Raises ValueError on the first violation so a malformed precision pull fails
    the build instead of silently shipping nonsense uncertainty bands.
    """
    for r in rows:
        cv = r.get("cv")
        if cv is not None and cv < 0:
            raise ValueError(f"Negative coefficient of variation {cv} in row: {r}")
        lo = r.get("ci_lo")
        hi = r.get("ci_hi")
        if lo is None and hi is None:
            continue
        if lo is None or hi is None:
            raise ValueError(f"Half-open confidence interval in row: {r}")
        if lo > hi:
            raise ValueError(f"Inverted confidence interval [{lo}, {hi}] in row: {r}")
        v = r["value"]
        if not (lo - tolerance <= v <= hi + tolerance):
            raise ValueError(f"Incidence {v} falls outside its 95% CI [{lo}, {hi}] in row: {r}")


def validate_coverage(
    rows: list[dict],
    expected_units: int,
    anchor_years: list[int],
    label: str,
) -> None:
    """Assert that exactly expected_units areas are present per anchor year.

    anchor_years is the list of years where full coverage is expected. For
    spend indicators this is every panel year; for poverty it is [2018, 2021, 2023].
    Raises ValueError if any anchor year is missing areas or has extras.
    """
    by_year: dict[int, set[str]] = {}
    for r in rows:
        yr = int(r["year"])
        if yr in anchor_years:
            by_year.setdefault(yr, set()).add(r["psgc"])

    for yr in anchor_years:
        found = len(by_year.get(yr, set()))
        if found != expected_units:
            raise ValueError(
                f"validate_coverage ({label}, year={yr}): "
                f"expected {expected_units} units, found {found}"
            )


def validate_uniqueness(rows: list[dict], label: str) -> None:
    """Assert no duplicate (psgc, year) pairs in the row set.

    Duplicate keys mean two values would compete for the same chart coordinate.
    Raises ValueError naming the first duplicate found.
    """
    seen: set[tuple[str, int]] = set()
    for r in rows:
        key = (r["psgc"], int(r["year"]))
        if key in seen:
            raise ValueError(f"validate_uniqueness ({label}): duplicate (psgc, year) = {key}")
        seen.add(key)


def validate_yoy_jumps(
    rows: list[dict],
    label: str,
    threshold: float = 5.0,
) -> None:
    """Warn when a province's value jumps more than threshold-x in one year.

    Prints offenders to stdout (does not raise). Intended for spend indicators
    where a 5x single-year jump is almost certainly a data artifact.
    """
    by_psgc: dict[str, list[tuple[int, float]]] = {}
    for r in rows:
        by_psgc.setdefault(r["psgc"], []).append((int(r["year"]), float(r["value"])))

    offenders: list[str] = []
    for psgc, pairs in by_psgc.items():
        pairs.sort(key=lambda x: x[0])
        for i in range(1, len(pairs)):
            prev_yr, prev_val = pairs[i - 1]
            yr, val = pairs[i]
            if yr - prev_yr != 1:
                continue
            if prev_val <= 0:
                continue
            ratio = val / prev_val
            if ratio >= threshold or ratio <= 1.0 / threshold:
                direction = "up" if ratio >= threshold else "down"
                offenders.append(
                    f"{psgc} {prev_yr}->{yr}: {prev_val:.0f}->{val:.0f} ({ratio:.1f}x {direction})"
                )

    if offenders:
        print(
            f"validate_yoy_jumps ({label}): {len(offenders)} province-year jumps >= {threshold}x:"
        )
        for o in offenders[:10]:
            print(f"  {o}")
        if len(offenders) > 10:
            print(f"  ... and {len(offenders) - 10} more")


def _median(vals: list[float]) -> float:
    s = sorted(vals)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def validate_median_shift(
    rows: list[dict],
    filename: str,
    label: str,
    warn_threshold: float = 0.20,
    fail_threshold: float = 0.50,
) -> None:
    """Compare new build medians against the committed file on disk.

    Reads public/data/{filename} (the currently committed version) before it is
    overwritten, computes per-year medians, and compares them to the new rows.

    Warns (prints) when shift > warn_threshold. Hard-fails (raises) when shift
    > fail_threshold, unless ETL_ALLOW_BIG_SHIFT=1 is set in the environment.
    Call this BEFORE write_json so the committed file is still on disk.
    """
    committed_path = PUBLIC_DATA / filename
    if not committed_path.exists():
        return  # first build; nothing to compare against

    try:
        committed = json.loads(committed_path.read_text())
    except (json.JSONDecodeError, OSError):
        return

    if not isinstance(committed, list):
        return

    # Build per-year median maps.
    def _year_medians(data: list[dict]) -> dict[int, float]:
        by_year: dict[int, list[float]] = {}
        for r in data:
            yr = int(r.get("year", 0))
            v = r.get("value")
            if v is not None:
                by_year.setdefault(yr, []).append(float(v))
        return {yr: _median(vals) for yr, vals in by_year.items()}

    old_med = _year_medians(committed)
    new_med = _year_medians(rows)

    big_shifts: list[str] = []
    warn_shifts: list[str] = []
    for yr in sorted(set(old_med) & set(new_med)):
        old_v = old_med[yr]
        if old_v == 0:
            continue
        shift = abs(new_med[yr] - old_v) / abs(old_v)
        if shift > fail_threshold:
            big_shifts.append(f"  year={yr}: {old_v:.1f} -> {new_med[yr]:.1f} ({shift * 100:.0f}%)")
        elif shift > warn_threshold:
            warn_shifts.append(
                f"  year={yr}: {old_v:.1f} -> {new_med[yr]:.1f} ({shift * 100:.0f}%)"
            )

    if warn_shifts:
        print(
            f"validate_median_shift ({label}): WARNING - median shifted > "
            f"{warn_threshold * 100:.0f}% in {len(warn_shifts)} year(s):"
        )
        for s in warn_shifts:
            print(s)

    if big_shifts:
        msg = (
            f"validate_median_shift ({label}): FAIL - median shifted > "
            f"{fail_threshold * 100:.0f}% (possible data regression) in "
            f"{len(big_shifts)} year(s):\n"
            + "\n".join(big_shifts)
            + "\nSet ETL_ALLOW_BIG_SHIFT=1 to override."
        )
        if os.environ.get("ETL_ALLOW_BIG_SHIFT") == "1":
            print(f"WARNING (overridden): {msg}")
        else:
            raise ValueError(msg)
