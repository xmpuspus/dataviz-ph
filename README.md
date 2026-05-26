# plot.ph

Animated bubble charts of Philippine public data. Pick a story, hit play.

## Stories

- **DPWH vs poverty** — DPWH spend per capita against poverty incidence, 82 provinces, 2014 to 2024. Toggle PHP nominal vs PHP 2018-real.
- **All government spend vs poverty** — every PhilGEPS contract attributable to a province per capita against poverty incidence, 82 provinces, 2014 to 2024. Same nominal vs real toggle.
- **GDP vs poverty** — PSA per-capita GDP (constant 2018 PHP) against poverty incidence, 81 provinces, 2022 to 2024.

All stories share the same UI: island-group color, log/linear X toggle, click-to-stick + trails, province search, sticky labels, outlier auto-labels, year prev/next stepper, compare-with-year overlay, CSV download.

## Stack

- Python ETL pulls PSA OpenStat (poverty, population, GDP per capita, CPI) + PhilGEPS awards; writes tidy long-form JSON to `public/data/`.
- Static HTML + vendored ECharts 5.5.1 (SHA-384 SRI) reads the JSON and animates.
- No backend. No build step beyond `python -m etl.build`. Hosting: any static CDN; target is Cloudflare Pages.

## Layout

```
etl/         Python pipeline
public/      Static site + pre-baked JSON + vendored ECharts + OG card
tests/       pytest
LICENSE      MIT
```

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m etl.build                       # pulls data, writes public/data/*.json
ruff check etl/ tests/
pytest -q

# Serve the static site locally:
python -m http.server -d public 8765
open http://localhost:8765/
```

The ETL caches all upstream responses under `.etl_cache/`. The first PhilGEPS run downloads ~620 MB of parquet chunks; subsequent runs are seconds.

## Data files in `public/data/`

- `provinces.json` — 82 units: 81 PSA provinces + NCR as a single virtual unit. Each carries name, island_group (luzon/visayas/mindanao/ncr/barmm), region_code, population_2020 (HUCs rolled into parent provinces).
- `poverty.json` — `{psgc, year, value, interp, extrap}`. PSA Full-Year anchors at 2018, 2021, 2023 with linear fill between, held-constant before/after.
- `dpwh_spend_per_capita.json` — `{psgc, year, value, value_real}`. `value` is PHP nominal; `value_real` is deflated to PHP 2018 using PSA national CPI.
- `all_spend_per_capita.json` — same shape, every PhilGEPS contract per province per year.
- `gdp_per_capita.json` — `{psgc, year, value}` at constant 2018 prices.
- `cpi.json` — `{year: index}` with 2018 = 100.
- `indicators.json` — per-indicator metadata (source, vintage, log_natural, can_deflate).
- `stories.json` — story definitions (id, headline, tagline, x/y/size indicator ids, panel years, default year).

## Sources

- **PSA OpenStat PXWeb API** — no key. Tables: 1A/PO (population), 1E/FY/Table 1a (poverty), 2A/PPA/2025/Table 9 (per capita GDP), 2M/PI/CPI/2018NEW (CPI all items).
- **PhilGEPS awards parquet** — DPWH subset (organization name contains "PUBLIC WORKS AND HIGHWAYS") and full PhilGEPS. Mirror: [csiiiv/philgeps-awards-dashboard](https://github.com/csiiiv/philgeps-awards-dashboard) (MIT). 15 chunks, ~620 MB total.
- **PSGC reference** — [psgc.gitlab.io](https://psgc.gitlab.io/api/) (community mirror of PSA's classifications, public domain).
- **ECharts** — Apache-2.0, vendored at `public/vendor/echarts.min.js` with SRI.

## Honesty notes

These ship with the page in the on-page Methodology section so a journalist who asks "is this right?" gets the answer immediately:

- About 20 percent of PhilGEPS award value cannot be attributed to a single province (null `area_of_delivery`, multi-province contracts, "Independent City" bucket) and is excluded from per-capita spend.
- Province-years with per-capita under PHP 100 are dropped as likely coverage gaps (validator floor in `etl/philgeps.py`).
- Maguindanao is shown as the pre-2022-split unit. PSA's poverty Table 1a still publishes the rolled-up Maguindanao; the del-Norte / del-Sur 2023 rows would silently overwrite the parent, so they're skipped via `psgc.SKIPPED_NAMES`.
- NCR is the regional aggregate (16 cities) treated as a single bubble.
- Poverty rates are PSA's "province-without-HUC" measure. Per-capita spend uses whole-province population (HUCs rolled into parent provinces via `psgc.HUC_TO_PARENT`). Per capita GDP uses PSA's "province-only" measure. Documented in the on-page Limits paragraph.
- Bubble size is the 2020 Census, held constant across the animation.
- Every province has all three PSA poverty anchors (2018, 2021, 2023) in `poverty.json`. Sulu fell from 75.3% in 2018 to 41.5% in 2021 to 13.0% in 2023; Mountain Province from 17.1% to 15.3% to 10.5%. Anchor years render as solid bubbles; years between are linearly interpolated; years before 2018 and after 2023 are held constant at the nearest anchor and tagged `extrap: true`.

## Accessibility

- The chart `<div>` is `tabindex="0"` and has a visible focus ring. When focused, Left/Right arrow scrub years; Home/End jump to first/last year.
- A `Year ◄ Prev / Next ►` button pair in the controls panel does the same with the mouse or with Tab + Enter.
- A `Compare with year` `<select>` overlays a second year's bubbles as ghost outlines.
- A hidden screen-reader `<table>` mirrors every visible bubble in the current frame, with proper `<caption>`, `<thead>`, `<tbody>`. The chart `aria-describedby` points to it and to a usage instructions paragraph.
- All buttons are at least 44 px tall on mobile.
- Color contrast: muted text uses `#595959` against white (WCAG AA at body text size).

## Honest gaps

- A screen-reader user can read the data table and use the prev/next year buttons, but cannot interact with individual bubbles (click-to-stick / trails). A fully accessible chart is on the wishlist.
- Auto-play kicks in on the first visit (no hash); shared links with a hash skip it.
- The OG card at `public/og.png` is hand-designed (1200x630, generated via Pillow); per-view OG images would need a Cloudflare Worker.
- Compare-with-year is a same-chart overlay; a side-by-side split view is not implemented.

## UI/UX iteration loop

Every change ends with an `/agent-browser` pass at three viewports (375, 768, 1440) before it counts as done. Failures get fixed and the loop reruns until clean. The bar is "feels like Gapminder", not "renders without errors".

Per-pass behavioural checks include: page loads with zero console errors, headline reads as a question, play button is at least 44 px on mobile, bubbles transition smoothly, hover or tap on a bubble shows a tooltip with all values, log toggle live-flips the X axis, island-group palette is not a rainbow, methodology link is visible, URL hash updates and round-trips on reload, interpolated/extrapolated frames render visibly different from anchors, no em-dashes anywhere in user-visible text, story switcher tabs across stories, deflate toggle hidden on stories without `can_deflate`, CSV download produces a valid file, screen-reader table mirrors the current view, prev/next year buttons step the timeline, Home/End jump to range ends, compare-with-year overlays ghost bubbles.

Iter screenshots live under `tmp/ui-iter-*/` (gitignored).

## License

MIT (this repo). ECharts is Apache-2.0. Data: PSA Open Data terms (PSA OpenStat + Census), MIT (PhilGEPS mirror), public domain (PSGC mirror).
