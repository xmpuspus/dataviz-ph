"""Keep public and maintainer documentation aligned with the shipped explorer."""

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
