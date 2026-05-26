# plot.ph roadmap

Generated 2026-05-27. Source docs in /tmp/plot-ph-roadmap/:

- `gapminder-gaps.md`: 34-feature gap table vs Gapminder Tools / Vizabi / OWID Grapher
- `psa-openstat-catalog.md`: 20-table PSA OpenStat inventory, top 5 story candidates ranked
- `non-psa-catalog.md`: 12 non-PSA sources (PhilGEPS, PHIVOLCS, PAGASA, NASA POWER, etc.) ranked
- `product-audit.md`: consolidated 8-dimension product audit (overall 65/100; 4 Criticals)

## Ship blockers (before plot.ph publishes under a public domain)

These are tagged Critical in `product-audit.md`. Land them before deploy.

1. Deflate toggle silently shows nominal PHP labeled as 2018-real for 2014-2017. CPI table starts at 2018; 321 of 876 DPWH rows fall back to nominal at [public/app.js:95](public/app.js#L95). Either backcast CPI via PSA `2M/PI/CPI/2006NEW` or block the toggle for pre-base years with an inline notice.
2. README and on-page methodology claim Mountain Province + Sulu have only the 2023 anchor; actual `poverty.json` shows all 3 anchors (Sulu 2018=75.30, 2021=41.50, 2023=13.00). Fix [README.md:73](README.md#L73) and [public/index.html:172](public/index.html#L172).
3. No `last_updated` or source manifest anywhere on the page. Add `public/data/manifest.json` from `etl/build.py` + a footer line in `app.js`.
4. Tooltip is hover-only on touch devices. Mobile users cannot read bubble values. Set `tooltip.triggerOn: 'click'` when `matchMedia('(hover: none)').matches` at [public/app.js:270](public/app.js#L270).

---

## Top 5 features to add (Gapminder parity)

Ordered by impact-to-effort ratio. All concrete; no new data required for #1, #2, #3.

| # | Feature | User value | Effort | File to edit | Acceptance check |
|---|---------|------------|--------|--------------|------------------|
| 1 | Tap tooltip on touch + persistent last-tap panel under chart on <600px | Half the audience is on mobile. Today tap toggles selection without showing the value. | S | [public/app.js:270](public/app.js#L270) tooltip config; [public/app.js:776](public/app.js#L776) click handler; [public/style.css](public/style.css) for the panel | Open on real iPhone Safari: first tap on bubble shows tooltip; second tap on same bubble toggles trail-pin. Panel below chart shows last-tapped value persistently. |
| 2 | PNG snapshot button next to CSV / Copy link | Journalists need an image they can drop into a CMS, not just a link. | S | [public/index.html:115](public/index.html#L115) add button; [public/app.js:801](public/app.js#L801) wire `chart.getDataURL({type:'png', pixelRatio:2, backgroundColor:'#fff'})` | Click "Save PNG", file `plot-ph-DPWH-2018.png` downloads at 2x density with white background; opens in macOS Preview without artifacts. |
| 3 | Indicator definitions on axis-label hover or click | "Poverty incidence among families" is jargon. `indicators.json` already carries 200-char vintage strings; today they are unused on axis. | S | [public/app.js:235](public/app.js#L235) wrap axis name as a `<button>` overlay; popover backed by `data.indicators[id].vintage` | Hover the Y axis label "Poverty incidence among families": a popover shows the PSA Table 1a definition and source link. Click to pin. Keyboard accessible. |
| 4 | Indicator picker (free choice of X / Y) | Three hard-coded stories under-use the data. A `<select>` per axis multiplies exploration surface from 3 to 12 valid pairs with no new ETL. | M | [public/index.html:84](public/index.html#L84) two `<select>`s; [public/app.js:580](public/app.js#L580) bind to `state.x`/`state.y` not `story.x`/`story.y`; [public/data/indicators.json](public/data/indicators.json) add `pickable: true` | Pick X=all_spend Y=gdp_per_capita: chart renders 2022-2024 panel, log toggle works, deflate hidden, CSV reflects the chosen pair. Story tabs become "presets" that pre-fill the selects. |
| 5 | Province choropleth view tab (bubble / map / rank / line) | Civic-tech readers think geographically first. The single biggest UX unlock per Gapminder gap doc. | L | New `public/data/ph-provinces.geojson` from psgc.gitlab.io polygons; new `public/map.js` or extend `app.js` with `viewMode` state; [public/index.html:45](public/index.html#L45) add tab nav above the chart | Click "Map" tab: PH outline renders with 82 provinces colored by current Y indicator; click a province pins it (round-trips with bubble view); year-stepper still works; mobile usable at 375px. |

Smaller wins also worth tucking in: per-frame URL hash already round-trips most state ([public/app.js:444](public/app.js#L444)); add an Embed-iframe copy button ([Effort S](public/index.html#L115)); add a Speed 0.5x/1x/2x toggle near play ([app.js:385](public/app.js#L385)).

---

## Top 3 new stories from PSA OpenStat

Ranked from `psa-openstat-catalog.md` after correcting for plot.ph's existing capability: plot.ph already uses provincial poverty (the catalog said regional-only because the MCP only wired the regional table; plot.ph's `etl/psa_openstat.py` wired provincial).

### Story 1: `all-spend-vs-gdp` (do this first; zero new data)

- Headline: "Where the money goes vs where the wealth lives"
- Tagline: "82 provinces, 2022 to 2024. All government contracts per capita against per-capita GDP."
- X: all_spend_per_capita (already in `public/data/all_spend_per_capita.json`)
- Y: gdp_per_capita (already in `public/data/gdp_per_capita.json`)
- Panel: 2022, 2023, 2024 (GDP coverage)
- Data caveat: Only 3 years (limited by GDP series start at 2022). Both indicators provincial-level. Population sizing as default. Zero new ETL.
- Why this matters: This pair answers a different question than the existing 3 stories. Today plot.ph shows spend-against-poverty and gdp-against-poverty, but never spend-against-wealth. Pairing the two already-shipped indicators reveals whether procurement money flows to higher-GDP provinces (already-rich-get-served) or to lower-GDP provinces (pro-poor allocation). A useful counterpoint to the DPWH headline. Add as a 4th preset in `etl/build.py:159` story dict; the picker (Top 5 feature #4) makes it native.

### Story 2: `inflation-vs-poverty` (regional)

- Headline: "Which regions combine high poverty with fastest-rising prices?"
- Tagline: "17 regions plus NCR. Year-on-year CPI inflation against poverty incidence."
- X: PSA `2M/PI/CPI/2018NEW` regional year-on-year inflation (%)
- Y: PSA `1E/FY/Table 1a` regional poverty incidence (%), 2018/2021/2023 anchors
- Panel: 2018 onward (CPI coverage starts at 2018; one-step regional aggregation needed)
- Data caveat: Drops from 82-province granularity to 18 units (17 regions + NCR). New `etl/psa_inflation.py` module fetching the regional CPI table; aggregate provincial poverty to regional. Document the granularity drop in the story tagline.
- Why this matters: Cost of living is the dominant PH economic conversation in 2026. A regional inflation map paired with poverty exposes "double-shock" regions where high baseline poverty meets fastest price growth. Policy-actionable. The catalog ranks this as the strongest next PSA story; the 18-bubble version still reads as a coherent civic-tech chart.

### Story 3: `gdp-growth-vs-poverty-change`

- Headline: "Are richer provinces pulling away, or are poorer ones catching up?"
- Tagline: "82 provinces. Annualized GDP growth 2014 to 2024 against poverty incidence change 2018 to 2023."
- X: PSA `2A/PPA/2025/0092A5GPPA8` GDP per capita CAGR over the panel (derived in ETL)
- Y: PSA `1E/FY/Table 1a` poverty incidence delta in percentage points
- Panel: a single bubble per province, color = island group; or 2-frame compare view
- Data caveat: This is a static scatter, not a time-animated panel. Plays differently from the existing stories; could be a different layout entirely. Anchor mismatch (GDP annual vs poverty 3-snapshot) means the X is a 10-year derived metric, Y is a 5-year derived metric.
- Why this matters: The convergence-divergence question is the central PH inequality debate. A clean scatter answers it visually. Doesn't fit the animated-bubble UX naturally; could ship as either a static bubble panel OR (better) be reframed under the "Rank view" feature (Top 5 #5 cousin), animated by year.

---

## Top 3 new stories from non-PSA sources

Ranked from `non-psa-catalog.md` after correcting for plot.ph's existing capability: the agent assumed PhilGEPS = the live MCP tool (~100 latest notices), but plot.ph uses the csiiiv parquet mirror with full multi-year coverage already in `etl/philgeps.py`. PhilGEPS variant filters are therefore feasible for plot.ph despite what that doc implies.

### Story 1: `infra-spend-vs-poverty` (PhilGEPS line-item filter, no upstream change)

- Headline: "Just the construction. Does building things move poverty?"
- Tagline: "Every PhilGEPS contract whose line item is construction, road, bridge, flood, drainage. 82 provinces, 2014 to 2024."
- X: PhilGEPS per-capita spend filtered by `line_item_description` regex on `construction|road|bridge|flood|drainage` (new function `_infra_filter` alongside `_dpwh_filter` at [etl/philgeps.py:113](etl/philgeps.py#L113))
- Y: poverty incidence (same as existing stories)
- Panel: 2014 to 2024 (same depth as existing PhilGEPS stories)
- Data caveat: Same per-capita floor concerns as the existing 2 PhilGEPS stories. Line-item descriptions are free text; regex misses on truncated or capitalized variants. Estimate coverage and document the regex in the methodology block.
- Why this matters: DPWH-only is too narrow (LGU-executed infra is invisible); all-spend is too broad (vaccines, salaries, software). The infrastructure-only lens is the natural civic-accountability story: did the brick-and-mortar money reach poorer provinces? Different answer from DPWH-vs-poverty because LGU-implemented projects show up. Same data pipeline, just a filter swap.

### Story 2: `doh-spend-vs-health-outcome` (PhilGEPS DOH filter + a paired health indicator)

- Headline: "Where DOH spent. Where the babies survived."
- Tagline: "Department of Health contracts per capita against infant mortality. 82 provinces, where data exists."
- X: PhilGEPS per-capita filtered by organization_name match on `DEPARTMENT OF HEALTH` or `DOH` (new `_doh_filter` at [etl/philgeps.py:113](etl/philgeps.py#L113))
- Y: PSA `1D` health indicator, ideally infant mortality at provincial granularity if it exists; otherwise paired with DOH bed-density or health-personnel ratio if those exist
- Panel: 2014 to 2024 for X; Y panel TBD by PSA browse
- Data caveat: Per the PSA catalog Honest Blockers section, `1D` health indicators are likely national-only; the story may degrade to regional or even national 1-bubble timeline. Browse `DB/1D/` to confirm before scoping. If granularity holds, this is the strongest civic-tech story in the roadmap.
- Why this matters: The 2020-2024 pandemic-era spending wave is the largest civic-data story PH still hasn't told visually. Pairing DOH procurement with a health outcome is the obvious frame. Even at regional granularity, this is journalism-grade.

### Story 3: `wb-gdp-vs-poverty-ph` (companion national long-arc, World Bank)

- Headline: "Forty years of Philippine GDP and the poverty line."
- Tagline: "One country, one bubble. 1985 to 2025. World Bank data."
- X: World Bank `NY.GDP.MKTP.CD` GDP current USD (via existing `get_world_bank_indicator` in ph-civic-data-mcp; mirror locally to `etl/world_bank.py` since plot.ph is static and shouldn't call live MCP)
- Y: World Bank `SI.POV.NAHC` poverty headcount ratio at national line
- Bubble size: World Bank `SP.POP.TOTL` population
- Panel: 1985 to most recent (40+ years annual)
- Data caveat: 1-unit national bubble; not a true 82-province plot. Lives as a companion story showing macro-scale context, not as a swap-in for the existing provincial stories. World Bank publishes with 1-2 year lag.
- Why this matters: Plot.ph's three stories cover a 2014-2024 window. A national companion that goes back to Aquino-Marcos-Ramos era contextualizes recent provincial movement against decade-scale national trajectory. Ranked #1 in the non-PSA catalog. Caveat is the 1-bubble shape which feels under-used; consider rendering this story as a Line view (Top 5 feature #5 cousin) rather than as a 1-bubble animated panel.

---

## Top 3 UX improvements from product audit

| # | Finding | Severity | File / line | Fix outline |
|---|---------|----------|-------------|-------------|
| 1 | Tooltip is hover-only on touch; mobile users cannot read bubble values | Critical | [public/app.js:270](public/app.js#L270) | `tooltip.triggerOn: 'click'` when `matchMedia('(hover: none)').matches`; render a persistent last-tapped panel below the chart on <600px. Tap once shows tooltip, tap again toggles pin. |
| 2 | Compare-with-year overlay has no per-bubble connectors; ghost vs live differ only by opacity, illegible on mobile | High | [public/app.js:304-328](public/app.js#L304) | Draw 1px connector line between same-province points across the two years (similar to trails). Add small "Compare: YYYY" pill in chart top-right. Optional: shift ghost hue to neutral gray with island-color accent dot. |
| 3 | No "why this matters" or per-story source link; indicator definitions never surfaced | High | [public/data/stories.json](public/data/stories.json) + [public/app.js:735](public/app.js#L735) + [public/data/indicators.json](public/data/indicators.json) | Add `why` and `source_url` to each story; render under tagline. Wire axis-label hover popover from `indicators.json[id].vintage` (already exists; just unused). |

---

## Top 3 reliability / performance improvements

| # | Finding | Severity | File / line | Fix outline |
|---|---------|----------|-------------|-------------|
| 1 | ETL cache never expires; PSA republishes annually, build silently ships stale anchors | Critical | [etl/psa_openstat.py:78](etl/psa_openstat.py#L78) `_fetch_or_cache` | Add a 30-day TTL (mtime check) plus a `--no-cache` CLI flag on `python -m etl.build`. Record fetch timestamp + sha256 of response in a sidecar JSON; surface in the build manifest. |
| 2 | No HTTP retry / backoff anywhere in ETL; single PSA WAF blip kills the build | Critical | [etl/psa_openstat.py:38-49](etl/psa_openstat.py#L38) `_get_json` / `_post_json`; [etl/psgc.py:106](etl/psgc.py#L106) | Wrap with tenacity (or hand-rolled): 3 attempts, exponential backoff 1s/2s/4s with jitter, retry only on 429/5xx/connection-error. Bump POST timeout to 180s for bulk pulls. |
| 3 | Page weight 1.32 MB; ECharts 1.03 MB alone; time-to-first-bubble 2.5-3.5s on 3G | High | [public/vendor/echarts.min.js](public/vendor/echarts.min.js) + [public/index.html:29](public/index.html#L29) | Switch to a custom ECharts build limited to Scatter + Timeline + Tooltip + Grid + Visual + Title components. Saves ~600 KB raw / ~180 KB gz. One-time vite/rollup config. Refresh the SHA-384 SRI on `<script integrity=>` in the same commit. Document the upgrade recipe in `public/vendor/README.md`. |

Honorable mentions that pair well: rebuild only changed timeline steps not all 11 ([app.js:335](public/app.js#L335)); queue user actions during 700ms tween instead of dropping ([app.js:730](public/app.js#L730)); add unit tests for `linear_fill`, `normalize_name`, `validate_all` (highest-value missing test: single-anchor extrapolation behavior with MP/Sulu fixture); honor `prefers-reduced-motion` before autoplay.

---

## Open questions for Xavier

1. **Ship blocker priority: fix Critical-Intel #1 (CPI backcast) by extending CPI to 2014 via PSA `2M/PI/CPI/2006NEW`, OR by blocking the deflate toggle for pre-2018 years with a notice. Which?** The first preserves the existing UX but adds an ETL fetch + chained-index math. The second is a 10-line app.js change but advertises the gap on the page. Both are honest; the second is faster to ship.

2. **For the 4th preset story: ship `all-spend-vs-gdp` as a fixed 4th tab, OR ship the indicator-picker first and let the user discover the pair themselves?** The first is S effort and one new line in stories.json. The second is M effort but ships 12 valid pairs at once. Both are good; only one is "first".

3. **Choropleth view vs indicator picker vs PNG snapshot, which one first?** Each is the headline lift for a different reader: choropleth for LGU staff who think geographically, indicator picker for researchers who want to compose, PNG snapshot for journalists who need an asset. Picking one signals plot.ph's primary persona.

4. **Domain `plot.ph` is "pending" per the user memory but README does not say what is blocking. Are we waiting on dotPH registrar paperwork, DNS provider choice, or Cloudflare zone setup?** Without that, the ship-blocker work has no destination to land on.

5. **Should plot.ph adopt the "All data sourced from public records" disclaimer block standard across the civic-tech PH cluster (per the scoped-rules `civic-tech-ph` doc)?** Today the methodology section is rich and honest but doesn't carry the standard disclaimer phrasing. Adopting it makes plot.ph defensible if a province ever pushes back on a published number. Yes / no.
