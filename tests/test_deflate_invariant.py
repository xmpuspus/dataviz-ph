"""Invariant: every indicator marked can_deflate:true in indicators.json must be
in the DEFLATABLE_INDICATORS Set literal in app.js, and vice versa.

Catches the class of bug where a new indicator is added to the data pipeline with
deflation support but the front-end Set is not updated, causing nominal values to
render under a real-pesos label.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "public" / "app.js"
INDICATORS_JSON = ROOT / "public" / "data" / "indicators.json"


def _parse_deflatable_set(src: str) -> set[str]:
    """Extract ids from the DEFLATABLE_INDICATORS = new Set([...]) literal."""
    m = re.search(
        r"const DEFLATABLE_INDICATORS\s*=\s*new Set\(\[(.*?)\]\)",
        src,
        re.DOTALL,
    )
    assert m, "DEFLATABLE_INDICATORS Set literal not found in app.js"
    body = m.group(1)
    return set(re.findall(r'"([^"]+)"', body))


def test_deflatable_set_matches_indicators_json():
    src = APP_JS.read_text(encoding="utf-8")
    js_set = _parse_deflatable_set(src)

    indicators = json.loads(INDICATORS_JSON.read_text(encoding="utf-8"))
    json_deflatable = {ind["id"] for ind in indicators if ind.get("can_deflate")}

    missing_from_js = json_deflatable - js_set
    extra_in_js = js_set - json_deflatable

    assert not missing_from_js, (
        f"indicators.json has can_deflate:true but missing from DEFLATABLE_INDICATORS: "
        f"{sorted(missing_from_js)}"
    )
    assert not extra_in_js, (
        f"DEFLATABLE_INDICATORS contains ids not marked can_deflate in indicators.json: "
        f"{sorted(extra_in_js)}"
    )
