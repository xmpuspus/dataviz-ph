# dataviz.ph

dataviz.ph is a Philippine public-data explorer. It compares public indicators and does not claim causation from correlation.

![Explorer demo](docs/demo.gif)

## The first screen gives a clear choice

The first screen stays static. Select **Play the guided story** for the short explanation, or select **Explore the data** for direct control.

Returning readers see the same static screen and can replay the guided story. Reduced-motion Play and replay keep the selected year unchanged.

Readers can select an area by chart, search box, or data table. The axis picker supports keyboard control. The chart summary and labelled table support screen readers. On small screens, the page keeps the story first and puts extra controls in a disclosure.

## The explorer shows evidence with each view

The trust panel shows the source, update, grain, transforms, status, coverage, warnings, and archive information for the active view. A coverage table shows full, partial, and unavailable years.

Each view can download CSV data, metadata JSON, and citation text. The metadata JSON includes the view URL, method URL, source and build identifiers, warnings, and coverage. Named views, stateful links, and embed attribution keep the same chosen state.

The embed kit at `/embed-kit` gives a responsive iframe for each named view. Embed mode keeps an attribution link back to the full explorer.

## The data uses one 82-area contract

The analysis layer has 81 provinces plus virtual NCR. The versioned geography contract preserves source-native identifiers and documents all roll-ups and changes.

Population and award totals sum additive Highly Urbanized City values into the matching historical analysis area. Cotabato City remains unattributed. The build never averages poverty, FIES, or GDP rates across areas. It recomputes a rate from additive components or leaves it unavailable. The contract records the historical Maguindanao treatment, the Negros Island Region change, the Sulu transfer, and virtual NCR.

GDP covers 2018 through 2025. Population uses 2020 and 2024 anchors, then estimates 2021 through 2023 between them. Poverty-depth data ends in 2023.

The official 2025 poverty and FIES workbooks do not ship. They need source, precision, crosswalk, weight, and revision gates before publication. If they pass, the explorer labels them preliminary and states the revision policy.

PhilGEPS data shows contract awards, but it does not show disbursements. PhilGEPS 2025 is unavailable because the reviewed snapshot fails incomplete-date, invalid-date, future-date, and correction-comparison gates. DBM COMPASS and CBMS are adoption-gated until their access, grain, coverage, and suppression contracts pass.

## The build reads committed contracts

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

python3 -m etl.build
python3 -m etl.build --no-cache

ruff check .
ruff format --check .
python3 -m pytest -q
python3 -m http.server -d public 8765
```

`--no-cache` refreshes PSA data. The build reads the committed geography contract. It does not get PhilGEPS snapshots, 2025 poverty workbooks, or 2025 FIES workbooks.

Run `python3 -m etl.build_share_pages` after a story change. Run `node docs/build_og_cards.js` after a visible card change. The share-page test compares generated pages with committed pages. Card PNG bytes can differ by browser rendering, so inspect every changed card at 1200 by 630 pixels before commit. Treat `public/og.png` as the site-wide fallback card and each `public/og/<story>.png` as a story card. Classify and inspect both kinds when their visible inputs change.

To record the demo, serve `public/` on port 8099, then run `node docs/record_demo.js`. The recorder chooses Play after `networkidle`. Follow the exact two-pass ffmpeg and browser-check commands in `CLAUDE.md`.

## The repository keeps source and generated files separate

| Path | Purpose |
| --- | --- |
| `etl/` | Data refresh, geography, validation, and generated share pages. |
| `public/` | Static explorer, data, methodology, share pages, embed kit, and cards. |
| `tests/` | Data, browser, share-page, and documentation checks. |
| `docs/` | Source ledger, card source, demo recorder, and demo GIF. |

## The public method page states limits

Read `/methodology` for the geography contract, indicator sources, transforms, coverage limits, and revision status. The page explains why awards differ from disbursements and why correlation does not show cause.
