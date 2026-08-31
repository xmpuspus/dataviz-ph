# Contributing to dataviz.ph

Thanks for your interest. This project visualizes Philippine public data (DPWH and
PhilGEPS contract awards, GDP, poverty) as a static site built from open records.

## Ways to help

- Report a data error. If a figure looks wrong, open an issue with the indicator, the
  province, the year, and the source you expected. Every number in the site is computed
  by the pipeline from a cited source, so a mismatch is worth tracking down.
- Improve the pipeline or the chart code.
- Suggest an indicator or a story pairing.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --requirement requirements.lock
python -m pip install --no-deps -e "."
python -m pip check
python -m etl.build        # regenerates public/data/*.json from the sources
```

The browser reads the JSON in `public/data/` and renders with ECharts. There is no
backend and no JS build step.

## Before you open a pull request

- Run the tests: `python -m pytest`.
- Run the linters: `ruff check . && ruff format --check .`.
- Keep data claims sourced. If you add or change a number that shows up in the UI or the
  README, cite where it comes from. Unsourced figures will not be merged.
- One focused change per pull request.

## Reporting a problem

Open an issue on GitHub. For anything sensitive, email xpuspus@gmail.com.
