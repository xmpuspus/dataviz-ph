# dataviz.ph — 40 behavioural integration tests (/agent-browser)

Authored 2026-05-29. Driver: the `/agent-browser` skill (a real headless/headful browser — navigate, click, type, screenshot, read DOM/computed-style). **Not** Playwright.

**Run target:** `python3 -m http.server -d public 8765` → `http://localhost:8765`.
**Each test:** preconditions → actions (agent-browser verbs) → expected → **visual check** (screenshot read-back, not just "it loaded"). A test passes only when the screenshot is read back and matches intent (workflow-discipline: visual inspection is a hard gate).

**Acceptance-gate note:** several tests below will FAIL against current `HEAD dd0b60f` by design — they encode the verified ship-blockers from the 2026-05-29 audit (T07 headline, T31 manifest freshness, T11 national-only trap, T26 empty-state, T34–T40 a11y). Run after the ship-blocker fixes land; today they document the gap.

Viewports used: **mobile 375x812**, **small 600x900**, **tablet 880x1000**, **desktop 1440x900**.

---

## A. First load, shell, provenance (T01–T05)

- **T01 Cold load renders a bubble frame.** Navigate desktop. Wait for chart. Screenshot. Expect: title/headline, a populated bubble scatter (not blank), island-group legend, play button visible. Visual: bubbles actually drawn, axes labelled.
- **T02 Default story is DPWH-vs-poverty at 2018.** On load read the active tab + year readout. Expect tab "DPWH vs poverty" active, year 2018 (deflate-safe default). Visual: year label shows 2018.
- **T03 Freshness footer is present and truthful.** Scroll to footer, read text. Expect "Built {date}" + poverty-anchor vintage from `manifest.json`. Visual: footer line renders, date parses (not raw ISO/"Invalid Date").
- **T04 Public-data disclaimer block present.** Read footer region. Expect the civic-tech-ph "All data sourced from public records…" disclaimer. Visual: disclaimer visible without interaction.
- **T05 No console errors / no uncaught throw on load.** Capture console. Expect zero uncaught exceptions, no failed `data/*.json` (all 200). (Guards T31's stale-manifest + T32 fetch-resilience.)

## B. The four preset stories (T06–T10)

- **T06 Tab switch: All gov vs poverty.** Click tab. Expect axes relabel (X "All PhilGEPS spend per capita", Y poverty), cloud redraws, "why" + source link update. Visual: X-axis title changed, source link href = csiiiv dashboard.
- **T07 DPWH headline is numerically honest.** Read the DPWH-vs-poverty headline string. Expect it to NOT claim "two trillion" (the plotted subset = PHP 5.04T over 11 years). **Known FAIL on HEAD** — gate for ship-blocker #1.
- **T08 Spend-vs-GDP preset.** Click "Spend vs GDP". Expect 2022–2024 panel only, year clamps into range, log-X works, deflate control hidden (GDP not deflatable). Visual: year stepper bounded to 2022–2024.
- **T09 GDP-vs-poverty preset bubble count.** Click "GDP vs poverty" at 2023. Count rendered bubbles. Expect ~81 (Maguindanao omitted by design); tagline should not over-promise "82" without the caveat. Visual: count plausible, no orphan/NaN bubble.
- **T10 Preset → custom → preset round-trip keeps state coherent.** From a preset, change Y via picker, then re-click the preset tab. Expect the preset fully restores its X/Y/year/log. Visual: axes + headline match the original preset.

## C. Indicator picker & arbitrary pairs (T11–T16)

- **T11 National-only CPI trap.** Open Y picker, look for `cpi_yoy_pct`. Expect either it is hidden, OR if selectable it shows "· national only" AND selecting it does not silently collapse all bubbles to one vertical line without a notice. **Known FAIL on HEAD** (selectable, collapses) — gate for the Medium picker finding.
- **T12 Picker search filters.** Type "gdp" in the picker search. Expect list narrows to GDP-bearing indicators. Visual: filtered list, aria-label present on the search box.
- **T13 Valid custom pair renders.** Pick X=all_spend, Y=gdp_per_capita. Expect a coherent 2022–2024 cloud, CSV reflects the pair. Visual: both axis titles updated, bubbles drawn.
- **T14 Per-pair headline updates.** After T13, read the headline. Expect a pair-specific headline from `pair_headlines.json` (not a stale preset headline). Visual: headline text matches the chosen pair.
- **T15 Custom pair shows "why"/source or a graceful absence.** Pick a pair with no preset story. Expect the "why"/source area either populates from data or hides cleanly (no empty bordered box, no "undefined"). Visual: no dangling label.
- **T16 Population as an axis.** Pick X=population. Expect the static-2020 caveat is conveyed (it's frozen across years) and the chart doesn't imply year-varying population. Visual: a note or non-animating X.

## D. Chart types (T17–T20)

- **T17 Switch to Lines.** Click chart-type "line". Expect per-province trend lines over panel years, legend sane, no leftover bubble artifacts (`notMerge` clean). Visual: lines drawn, X = years.
- **T18 Switch to Bars/Ranks.** Click "bar". Expect a ranked bar layout for the current year, sorted, labels legible. Visual: bars sorted, no overlap clipping.
- **T19 Log toggle disabled/irrelevant in non-bubble modes.** In Lines mode, confirm the "X: log" control either hides or is clearly inert (it does nothing in lines). **Known partial FAIL on HEAD** (stays live) — gate for UX High.
- **T20 Chart-type persists across year scrub.** In Lines, scrub the year. Expect mode stays "line" (not silently reverting to bubbles). Visual: still lines after scrubbing.

## E. Animation, timeline, keyboard scrub (T21–T25)

- **T21 Play animates years.** Click the big yellow play button. Wait 2s. Expect year advances, bubbles move. Visual: two screenshots 1s apart differ (year changed).
- **T22 Pause stops.** Click pause mid-play. Expect year freezes. Visual: two screenshots 1s apart identical.
- **T23 Arrow-key scrubbing.** Focus chart, press ArrowRight/ArrowLeft. Expect year steps ±1 within panel bounds. Visual: year readout changes.
- **T24 Timeline endpoints clamp.** Scrub to first and last panel year. Expect no wrap-around past the ends, play stops at the last frame. Visual: year stays at bound.
- **T25 Reduced-motion suppresses autoplay.** Set browser `prefers-reduced-motion: reduce`, reload. Expect autoplay does NOT start; user can still press play. **Known FAIL on HEAD** — gate for a11y reduced-motion.

## F. Deflate & log toggles (T26–T29)

- **T26 Empty-pair empty-state.** Construct a pair with zero overlapping years (e.g. an indicator panel that doesn't intersect). Expect a visible "no data for this combination" message, not a blank canvas. **Known FAIL on HEAD** — gate for Reliability High.
- **T27 Deflate to real PHP (post-2018).** On a deflatable indicator at year 2020, toggle real. Expect values change to constant-2018 PHP, axis title notes "real". Visual: axis label shows real-PHP.
- **T28 Deflate pre-2018 shows the honesty banner.** Set deflate=real, scrub to 2015. Expect the inline "Spend bubbles hidden for this year. PSA CPI 2018-base does not cover years before 2018." banner with bubbles dropped. Visual: banner text present.
- **T29 Log↔linear X.** Toggle log off on a spend indicator. Expect axis ticks switch to linear, bubbles re-spread, no NaN. Visual: tick labels change from log spacing.

## G. Selection, trails, compare (T30–T32)

- **T30 Select a bubble pins it + shows a trail.** Click a bubble (e.g. a known province). Expect it highlights and a motion trail appears across years. Visual: trail path drawn, selection style applied.
- **T31 Compare-with-year overlay + connectors.** Enable compare with an earlier year. Expect ghost bubbles + 1px connectors between same-province points, "Compare: YYYY" pill top-right. Visual: connectors visible, pill present.
- **T32 Deselect / clear.** Click empty space or the selected bubble again. Expect selection + trail clear. Visual: trail gone.

## H. Export & share (T33–T35)

- **T33 CSV export matches current pair.** Click CSV. Expect a download whose header columns = current X/Y/year and rows = rendered provinces. Visual/inspect: file downloaded, first row has the chosen indicator ids.
- **T34 PNG snapshot is clean.** Click "Save PNG". Expect a 2x white-background PNG named `dataviz-ph-{story}-{year}.png`. Open it. Visual: image is the chart, white bg, no transparency artifacts.
- **T35 Copy-link round-trips full state.** Set a custom pair + chart type + year + log, click Copy link, open the copied URL in a fresh tab. Expect identical state restored from the hash. Visual: new tab matches the source tab exactly.

## I. Tooltips & popovers (T36–T37)

- **T36 Desktop hover tooltip.** Hover a bubble. Expect a tooltip with province, island, X value, Y value, population — all HTML-escaped, no markup leakage. Visual: tooltip readable, values formatted.
- **T37 Axis-label definition popover (keyboard).** Tab to the X-axis "i" badge, press Enter. Expect a popover with the indicator definition + source link, Escape closes it and focus returns to the badge. **Focus-restore is a known gap** — gate for a11y H4.

## J. Responsive (T38–T39)

- **T38 Mobile 375px: no overflow, controls reachable, tap tooltip.** Load at 375x812. Expect no horizontal scroll, the chart legible, the X pill and play button not overlapping, tapping a bubble fills the `#last-tap-panel` below the chart. Visual: screenshot read at 375 — confirm the picker pill ↔ play-button collision flagged as responsive M3 is absent (or document it).
- **T39 Breakpoint sweep 600 / 880 / 1440.** Load at each. Expect layout reflows cleanly (controls stack/inline appropriately, chart fills width). Visual: one screenshot per width, all balanced, no clipped panels.

## K. Accessibility & polish (T40)

- **T40 Keyboard-only + contrast + SR sweep (composite a11y gate).** With no mouse: Tab through every control (tabs, picker, chart-type, toggles, play, year, export, popovers) — expect a visible focus ring on each and a logical order. Run a contrast check on axis caption (`#8a8a8a`) and tooltip "N more" (`#888`) — expect ≥4.5:1 AA. Inspect the `#sr-data` aria-live region — expect it does NOT dump all 82 rows on every year scrub. **Known FAIL on HEAD** (contrast 3.45/3.54:1; aria-live floods) — gate for a11y H2/H3 + the regraded C2.

---

## Wow-factor / shareability rubric (apply while reading T01, T21, T34, T39 screenshots)

A public-ready, shareable, wow-inducing first impression means: the cold-load frame is immediately legible and inviting (T01); the play animation reads as real provincial movement, not jitter (T21); the PNG/share artifact looks publication-grade dropped into a CMS (T34); and it holds up on a phone (T39). If any of these four reads as "functional but flat," it is not yet wow-inducing — note the specific weakness, don't pass it.
