"""Keep public and maintainer documentation aligned with the shipped explorer."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_public_guides_describe_the_static_start_and_current_data_contract() -> None:
    readme = (ROOT / "README.md").read_text()
    methodology = (ROOT / "public" / "methodology" / "index.html").read_text()
    for text in (readme, methodology):
        assert "Play the guided story" in text
        assert "Explore the data" in text
        assert "82-area" in text
        assert "2020 and 2024" in text
        assert "PhilGEPS 2025" in text
        assert "DBM COMPASS" in text
        assert "CBMS" in text
    assert "auto-plays on the first visit" not in readme
    assert "clears PSA and PSGC caches" not in readme


def test_roadmap_is_a_dated_source_status_ledger() -> None:
    roadmap = (ROOT / "docs" / "roadmap.md").read_text()
    assert "# Source status ledger" in roadmap
    for state in ("Shipped", "Unavailable", "Adoption-gated"):
        assert state in roadmap
    for source in ("PhilGEPS 2025", "DBM COMPASS", "CBMS"):
        assert source in roadmap


def test_demo_recorder_uses_the_explicit_play_choice() -> None:
    recorder = (ROOT / "docs" / "record_demo.js").read_text()
    assert "#play-guided-story" in recorder
    assert ".click()" in recorder
    assert "auto-runs" not in recorder


def test_geography_media_and_generated_copy_state_the_current_contract() -> None:
    readme = (ROOT / "README.md").read_text()
    methodology = (ROOT / "public" / "methodology" / "index.html").read_text()
    roadmap = (ROOT / "docs" / "roadmap.md").read_text()
    maintainer = (ROOT / "CLAUDE.md").read_text()
    stories = json.loads((ROOT / "public" / "data" / "stories.json").read_text())

    assert "Highly Urbanized Cities roll up to their parent province" not in readme
    for phrase in (
        "additive values",
        "Cotabato City",
        "published NCR regional total",
        "2014 through 2019 use the 2020 Census value",
    ):
        assert phrase in methodology

    assert "PhilGEPS awards | Province and virtual NCR | 2014 to 2024" in roadmap
    assert roadmap.count("| None |") >= 4
    for command in ("palettegen", "paletteuse", "ffprobe"):
        assert command in maintainer
    assert "site-wide fallback card" in maintainer
    assert "story cards" in maintainer

    by_id = {story["id"]: story for story in stories}
    for story_id in ("all-spend-vs-gdp", "gdp-vs-poverty"):
        assert by_id[story_id]["panel_years"] == list(range(2018, 2025))
        assert "2018 to 2024" in by_id[story_id]["tagline"]
    assert "82 analysis areas" in by_id["inflation-vs-poverty"]["tagline"]
    assert "82 provincial units" not in by_id["inflation-vs-poverty"]["tagline"]
