# dataviz.ph maintainer guide

## The repository serves a static explorer

- `public/` is the static site. Vercel serves it with `cleanUrls` and the CSP in `vercel.json`.
- `etl/` refreshes source data and writes committed public artifacts.
- `tests/` has pytest and Playwright checks. The browser tests use headless Chromium.
- `public/app.js` owns chart state, trust data, downloads, area choice, axis controls, and responsive behavior.
- `public/methodology/` explains sources and limits. `etl/build_share_pages.py` writes share pages and the embed kit.

## The product starts without autoplay

The first view stays static. It offers Play the guided story and Explore the data. Returning readers get replay.

Reduced-motion Play and replay do not advance the year. Do not restore first-visit autoplay in code, documentation, or the recorder.

Area choice must work through the chart, search combobox, and data table. Keep focus stable when a reader chooses an area or changes a story. The axis picker must keep its keyboard and Escape behavior.

## The data contract has 82 analysis areas

The contract has 81 provinces and virtual NCR. Read `public/data/geography-crosswalk.json` before a geography change.

Keep source-native identifiers. Record HUC roll-ups, Maguindanao history, the NIR change, and the Sulu transfer through the crosswalk.

GDP covers 2018 through 2025. Population uses 2020 and 2024 anchors and estimates 2021 through 2023. Poverty-depth data ends in 2023.

PhilGEPS figures are awards, but they are not disbursements. Do not claim causation from correlation.

The official 2025 poverty and FIES workbooks do not ship. Label them preliminary only after their source, precision, crosswalk, weight, and revision gates pass.

PhilGEPS 2025 is unavailable. Its snapshot must pass incomplete-date, invalid-date, future-date, and correction-comparison gates. DBM COMPASS and CBMS stay adoption-gated until access, grain, coverage, and suppression checks pass.

## The build has acquisition limits

```bash
python3 -m etl.build
python3 -m etl.build --no-cache
python3 -m etl.build_share_pages
node docs/build_og_cards.js
```

`--no-cache` refreshes PSA data and the build reads the committed geography contract. It does not get PhilGEPS snapshots or protected workbooks.

Do not hand-edit generated share pages, embed kit files, or cards. The share-page byte drift test checks HTML output. Card bytes can differ by browser rendering, so inspect each changed 1200 by 630 card. Classify `public/og.png` as the site-wide fallback card and `public/og/<story>.png` as story cards. Check both classes when their visible inputs change.

## The checks show current behavior

```bash
ruff check .
ruff format --check .
python3 -m pytest -q
node --check docs/record_demo.js
node --check docs/build_og_cards.js
git diff --check
```

Serve the site with `python3 -m http.server -d public 8099`. The recorder waits for `networkidle`, chooses Play, and records headless. Convert its WebM with the exact two-pass process below. Replace `DEMO_INPUT` with the recorded file path printed by the recorder.

```bash
DEMO_INPUT=/tmp/dataviz-demo-record/recording.webm
ffmpeg -i "$DEMO_INPUT" \
  -vf "setpts=0.67*PTS,fps=8,scale=720:-1:flags=lanczos,palettegen=max_colors=128:stats_mode=diff" \
  -y /tmp/dataviz-demo-palette.png
ffmpeg -i "$DEMO_INPUT" -i /tmp/dataviz-demo-palette.png \
  -lavfi "[0:v]setpts=0.67*PTS,fps=8,scale=720:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" \
  -y docs/demo.gif
ffprobe -v error -select_streams v:0 -count_frames \
  -show_entries stream=width,height,r_frame_rate,nb_read_frames \
  -show_entries format=duration,size -of json docs/demo.gif
python3 -m http.server 8124 --bind 127.0.0.1 --directory docs
```

Open `http://127.0.0.1:8124/demo.gif` in a real browser. Compare the opening frame with a frame at least five seconds later, and inspect the Sulu and GDP-switch frames. The GIF must have more than one frame and stay below 8 MB.
