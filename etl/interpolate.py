"""Linear interpolation for sparse anchors.

Used for poverty: anchors at 2018, 2021, 2023; fill 2014-2024.
Each filled row carries `interp: true` so the frontend can render it differently.
"""

from __future__ import annotations


def linear_fill(
    rows: list[dict],
    anchor_years: list[int],
    target_years: list[int],
    carry_fields: list[str] | None = None,
) -> list[dict]:
    """Per-psgc linear interpolation between anchor years.

    rows are [{psgc, year, value}] containing at least the anchor years.
    Returns [{psgc, year, value, interp, extrap}] for every (psgc, target_year):
    - anchor years: interp=False, extrap=False
    - between two anchors: interp=True, extrap=False
    - before first or after last anchor: interp=False, extrap=True (held constant)

    carry_fields names extra source keys (e.g. confidence-interval bounds) to copy
    through ONTO ANCHOR YEARS ONLY. Interpolated and extrapolated years are model
    estimates, not survey estimates, so they deliberately carry no precision -- it
    would be dishonest to attach a 95% CI to a year PSA never measured.
    """
    anchor_years = sorted(set(anchor_years))
    target_years = sorted(set(target_years))
    carry_fields = carry_fields or []

    by_psgc: dict[str, dict[int, float]] = {}
    extra_by_psgc: dict[str, dict[int, dict]] = {}
    for r in rows:
        yr = int(r["year"])
        by_psgc.setdefault(r["psgc"], {})[yr] = float(r["value"])
        if carry_fields:
            extra_by_psgc.setdefault(r["psgc"], {})[yr] = {
                f: r[f] for f in carry_fields if r.get(f) is not None
            }

    out: list[dict] = []
    for psgc, by_year in by_psgc.items():
        present_anchors = [y for y in anchor_years if y in by_year]
        if not present_anchors:
            continue
        for ty in target_years:
            if ty in by_year and ty in anchor_years:
                row = {
                    "psgc": psgc,
                    "year": ty,
                    "value": by_year[ty],
                    "interp": False,
                    "extrap": False,
                }
                if carry_fields:
                    row.update(extra_by_psgc.get(psgc, {}).get(ty, {}))
                out.append(row)
                continue
            lower = max((y for y in present_anchors if y <= ty), default=None)
            upper = min((y for y in present_anchors if y >= ty), default=None)
            if lower is None or upper is None or lower == upper:
                anchor = lower if upper is None else upper if lower is None else lower
                value, interp, extrap = by_year[anchor], False, True
            else:
                span = upper - lower
                frac = (ty - lower) / span
                value = by_year[lower] + frac * (by_year[upper] - by_year[lower])
                interp, extrap = True, False
            out.append(
                {"psgc": psgc, "year": ty, "value": value, "interp": interp, "extrap": extrap}
            )
    return out
