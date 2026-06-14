# dataviz.ph

Animated bubble charts of Philippine public spending vs poverty. 81 provinces + Metro Manila, 2014-2024. Gapminder-style explorer, now with a guided Rosling-style narrative arc on first load. Live at https://dataviz.ph.

This file is project-specific context. Global rules (data integrity, no-jargon, security, PR hygiene) live in the user's global CLAUDE.md and still apply.

## Stack and deploy

- Static `public/` served as-is. No build step. ECharts 5.6.0 vendored under `public/vendor/` and loaded with an SRI hash (check `public/index.html` for the current filename), vanilla ES-module JS, hand-written CSS.
- Python ETL under `etl/`, tested with `pytest` (`tests/`). `pyproject.toml` defines `.[dev]`. `etl/validate.py` gates every build: coverage (82 units per anchor year), (psgc, year) uniqueness, year-over-year jump warnings, median drift vs committed files.
- Host: Vercel, Git integration. **Push to `main` auto-deploys to production.** Feature branches get preview deploys.
- `vercel.json`: `cleanUrls: true`, strict CSP (`script-src 'self'`: no inline scripts, external JS only; `frame-ancestors *` for embeds), security headers, short cache on `app.js`/`style.css`.
- `main` is unprotected (no required reviews). CI (`.github/workflows/ci.yml`) runs `ruff check` + `ruff format --check` + `pytest` on PRs and pushes to main, installs Chromium so the Playwright browser tests run there too; it does NOT deploy (Vercel does). Actions are SHA-pinned. `.github/workflows/smoke.yml` cron-checks prod every 6 hours (homepage 200 + title, manifest parses and is fresh).
- Vercel Web Analytics: the same-origin `/_vercel/insights/script.js` tag is on both pages; it 404s harmlessly until Web Analytics is enabled on the Vercel project dashboard. The page works either way. `track()` in `app.js` reports client errors and arc events (complete/skip/abort).

## Key files

- `public/index.html`: the explorer page: topbar, title strip (headline/finding/caveat), chart, controls sidebar, slim footer (sources + freshness + disclaimer + Methodology link).
- `public/app.js`: the whole chart engine: data load, `makeView`, five chart types (bubbles/line/bar/map/panels), trails, compare-year, CI whiskers, live Spearman + permutation p for custom pairs, embed mode, island-group filter, the guided arc sequencer, autoplay, hash state, a11y mirror table.
- `public/methodology/index.html` + `public/methodology.js`: standalone `/methodology` page (served as a directory index so the clean URL resolves on Vercel AND a plain local `http.server`). `methodology.js` fills the live "Scale and trend" figures + freshness from the data; no chart engine.
- `public/style.css`: all styling. `public/data/*.json`: the data + `manifest.json` (now with an `inputs` provenance block and per-province attribution shares). `docs/`: `demo.gif` (hero), `og.html`/`og.png` (share card), `record_demo.js` (gif recorder).

## The guided narrative arc (v2, shipped)

First-visit (no URL hash, motion on) plays a 4-beat arc, then hands control to the explorer. It lives in `main()` in `app.js`:

- State: `state.arc = {beat, annotation, dim, quadrant}` (null in free explore). `render()` reads it to overlay on-chart narration via the existing `titleBlocks` ECharts `title` seam and to shade a median-split quadrant (`markArea`).
- Beats: HOOK (dimmed) -> REVEAL (autoplay 2018->2024, quadrant, "No link rho=+0.12") -> SPECIFIC (BARMM auto-trails plunge, names Sulu's PSA poverty drop) -> TWIST (CSS opacity cross-fade to gdp-vs-poverty, "Now it slides rho=-0.53") -> RELEASE.
- Gating: first-visit only (localStorage `datavizph_arc_seen_v1`); deep-links/hash skip it; `prefers-reduced-motion` shows the static annotated view + a "Play the guided story" button; returning visitors get gentle autoplay + replay.
- Abort: first user pointerdown/keydown (capture phase, `stopImmediatePropagation`) drops into the explorer. Skip button during, Replay after.
- `window.__datavizph_arcBeat()` is a read-only accessor (used by tests + the gif recorder to sync to a beat).
- Switching the arc onto a story MUST use `arcSetStory()` (resets `xIndicator`/`yIndicator`/`logX` like a real tab-click): setting `state.story` alone leaves the old axes because `makeView` prefers the indicator overrides.
- The arc runs in REAL mode (2018-real, the site default) starting at `CPI_BASE_YEAR` (real DPWH spend can't be computed pre-2018).

## Project conventions (in addition to global rules)

- Every on-screen number is computed from source at runtime or at build, never hardcoded (`computeDpwhSurge` in `methodology.js`; preset findings with 10k-shuffle permutation p-values are computed in `etl/build.py` and read from `stories.json`; custom picker pairs get a live Spearman rho + 1k-shuffle permutation p in `app.js` at the displayed year). Don't type a peso figure or rho into prose.
- Spend is PhilGEPS contract AWARDS, not disbursement; every spend surface says so. Correlation, never causation.
- Never single out a province as a signal (per-capita top spenders are small-population artifacts). The only safe named specific is Sulu's PSA poverty OUTCOME (75.3% 2018 -> 13.0% 2023), on the poverty axis, never tied to spend. BARMM attribution coverage is near zero, so per-province spend there is a stated lower bound, not a finding.
- The public-data disclaimer stays visible on the landing page (footer).
- Verified data findings (rho values, totals) live in memory `reference_data_findings_2026-05-30.md`, the single source of truth.

## Dev, test, ship

- Serve locally: `python3 -m http.server 8099 --directory public` (the topbar `/methodology` link works via the server's directory index).
- Test: `python3 -m pytest -q` (120 passing). Browser tests use Playwright + headless Chromium and self-skip when Chromium is absent; CI installs Chromium, so they run both locally AND on CI. Explorer browser tests emulate reduced motion so the arc doesn't swallow control clicks; the arc itself has dedicated tests.
- Lint: `ruff check . && ruff format --check .` (CI runs both).
- Ship: cut a branch off `main`, PR, let CI go green, merge to `main` (Vercel auto-deploys prod). Then VERIFY PROD:
  - Use a real headless browser (Playwright) against `https://dataviz.ph`. `WebFetch`/curl do NOT run JS, so the chart and the methodology scale numbers won't appear in a plain fetch.
  - Do NOT try to verify the Vercel branch PREVIEW: it is behind deployment-protection auth (HTTP 401). Verify the public prod domain after merge instead.
- Re-cut the hero gif: serve on :8099, `node docs/record_demo.js`, then the ffmpeg recipe in the commit that touched it (records the auto-arc; ~1.5x speed, fps 13, scale 900, two-pass palette).

## Current state (2026-06-14) — distribution (OG cards + embed kit)

Distribution pass (branch `feature/og-cards-embed-kit`), on top of the audit fixes. 145 tests.

- **Per-view OG share cards.** A pure static site can't carry per-view Open Graph through a URL hash (crawlers don't run JS or see the fragment), so each preset gets a real page at `/s/<id>` whose `og:*`/`twitter:*` meta is filled from the SAME computed finding the chart ships, with an instant redirect to `/#story=<id>`. Share `/s/<id>` → rich card; the human lands on the live chart. Card images at `/og/<id>.png` (1200×630, serif headline + the computed rho claim + island scatter), one per story.
- **Journalist embed kit** at `/embed-kit`: live preview + copy-paste responsive iframe snippet per preset (`#story=<id>&embed=1`), sizing/attribution/custom-view notes. Linked from the footer. Self-contained CSS (does NOT pull the site style.css, which would reflow it).
- **Generators, wired so nothing drifts.** `etl/build_share_pages.py` writes the `/s` pages + embed kit + `embed-kit.js` from stories.json and is called at the end of `etl/build.py` (OG descriptions are computed, never hand-typed). `docs/build_og_cards.js` (Node+Playwright, like record_demo.js) renders the PNGs — rerun when headlines/design change. `tests/test_share_pages.py` is a drift guard: regenerating must reproduce the committed pages byte-for-byte, plus every story has an OG card + correct meta. `vercel.json` caches `/og/` 1 day.

## Current state (2026-06-14) — audit fixes

Product-audit fix pass (branch `enhance/audit-fixes-20260614`) on top of PR #7. 140 tests. Front and back:

- **Data gate to prod.** `tests/test_committed_data_integrity.py` runs validate.py's coverage/uniqueness/range gates against the REAL committed `public/data/*.json` (the other ETL tests only used synthetic fixtures), plus stories-finding sanity. CI now actually blocks a wrong-numbers commit. `smoke.yml` gained a Playwright step that asserts the chart truly rendered a data series (a broken bundle/SRI used to pass the curl 200+title check); staleness warn tightened 180->90 days.
- **Reproducible build.** Runtime deps pinned exact in `pyproject.toml` (they reproduce the committed data byte-for-byte) + `requirements.lock`. `build.py`: CPI deflator base fails loud if 2018 is missing (was `cpi.get(2018, 100.0)`); population added to the exact-coverage gate.
- **Finding precision.** `compute_story_finding` takes `expected_units`; when a year's n is short of the 82/18 universe the sentence now says "across 81 of 82 areas with data" (reconciles with the "81 provinces and Metro Manila" tagline). Dropped the unused, sometimes sign-flipped `pearson` field from findings. Arc REVEAL-end text no longer asserts a specific province count. Custom picker pairs carry an "Exploratory: p isn't adjusted for many pairs" note; a custom pair with no data now shows a visible message instead of a blank chart.
- **Arc/autoplay pause on tab-hide.** `visibilitychange` handler freezes the autoplay `setInterval` and the arc's deadline-tracked `setTimeout` chain when `document.hidden`, resumes on return (was: a backgrounded tab ran the ~24s arc unseen). `window.__datavizph_playing` accessor + regression test.
- **Telemetry breadth.** New `track()` events: `story`, `chart_type`, `axis_pick`, `export` (png/svg/csv), `embed_copy`, `share_link` — so "which views people use" is measurable once Analytics is on. EN/Tagalog toggle now shows a "beta, partial" notice on switch.
- **Housekeeping.** Deleted the unreferenced 1 MB `echarts-5.6.0.min.js` (only the 666 KB custom build is loaded); vendor README updated.
- **Two manual residuals (only Xavier can do):** enable Vercel Web Analytics (makes all `track()` live; until then events queue in `window.vaq` unsent) and decide whether to protect `main` / make CI a required check.

## Current state (2026-06-10)

Two post-audit passes shipped on top of the audit sweep (PR #5, `faed40d`):

- **Explorer upgrades (PR #6).** Vendor bundle r2 (`echarts-custom-5.6.0-r2.min.js`: +SVGRenderer +DataZoomInside, +13.3 KB gzip, fresh SRI). SVG export button (throwaway SSR SVG-renderer instance). Color-by (island default / Y-quantile RAMP) + size-by (population default / equal) selectors, hash `col=yq` / `size=eq`. Inside dataZoom on bubble axes (pinch / Ctrl+scroll; plain scroll untouched). EN/Tagalog scaffold: `locales/tl.json` covers headlines/taglines/finding template/control labels; `t()` falls back to the English authored in HTML/JS; finding sentences re-interpolate the same computed numbers; toggle in topbar, persisted in localStorage.
- **Regional inflation story (PR #7).** `etl/psa_inflation.py` pulls the 18-region cut: CPI YoY per region (2M/PI/CPI/2018NEW regional rows, in-progress year excluded) and PSA's own regional poverty rows from Table 1a (never aggregated from provinces; shares the cached Table 1a payload). 6th preset `inflation-vs-poverty` (x `region_cpi_yoy_pct`, y `region_poverty`, panel 2021+2023, finding via the same 10k-permutation machinery). app.js gained a unit-set abstraction: `unit_set:"regions"` on an indicator routes every engine loop (`unitsForIndicator`/`unitsOf`) to `regions.json` (18 units, no population so equal-size bubbles, size legend hidden); map + panels are off at that grain; pickers only offer same-grain pairs; search/SR table/CSV run on the active set.

The earlier audit-sweep pass (PR #5) added:

- Critical fix: DOH and Infra spend now actually deflate (`DEFLATABLE_INDICATORS` in `app.js`); their labels claimed 2018-real over nominal values before.
- 5th preset `spend-vs-poverty-change`: cumulative nominal DPWH spend per capita 2014-2023 (`dpwh_spend_per_capita_cum`, `can_deflate: false`) vs poverty change in pp, single 2023 panel. 12 indicators total.
- All 5 preset findings carry permutation p-values (10,000 shuffles, seeded, `etl/build.py`); custom picker pairs get a live rho + 1,000-shuffle p in `app.js`. This closes A2 (Panels chart type) and the old "no p-value" residual.
- New views/controls: Panels (spend-vs-poverty next to GDP-vs-poverty, poverty-on-Y only), embed mode (`#embed=1` + Embed button, `frame-ancestors *`), island-group filter (`grp=` hash), all-years CSV export, 0.5/1/2x play speed, axes locked to global cross-year extents, "estimated, not surveyed" pill, COVID caveat on 2019-2020 poverty tooltips, load-failure notices with retry, search keyboard nav.
- ETL hardening: dedup on the PhilGEPS award id (0 dupes today, total unchanged), `etl/validate.py` gates, `manifest.json` `inputs` provenance block + per-province attribution shares (BARMM near zero: conservative lower bound).
- Ops: CI runs the browser tests (Chromium installed), `smoke.yml` 6-hourly prod check, SHA-pinned actions, Vercel Web Analytics + `track()` error/arc events.

Palette (island colors) + fonts (Georgia + system sans) are intentional; changing the default first-paint means re-cutting `docs/demo.gif`.
