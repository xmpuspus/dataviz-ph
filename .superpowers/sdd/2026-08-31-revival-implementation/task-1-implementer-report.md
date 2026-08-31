# Task 1 implementation report

## Changed files

- `etl/source_catalog.py`: reviewed PSA metadata contracts, discovery choice, and validated fallback paths.
- `etl/psa_openstat.py`: source discovery for all five PSA loaders. Old path names stay as compatibility aliases only for the regional loader.
- `etl/philgeps.py`: explicit acquisition boundary and local snapshot inventory with source identity, fetched timestamp, correction policy, coverage range, SHA-256, and row counts.
- `etl/source_monitor.py` and `.github/workflows/source-monitor.yml`: explicit read-only live monitor and its weekly workflow.
- `tests/test_source_catalog.py`, `tests/test_philgeps.py`, and `tests/test_source_monitor.py`: offline source-contract tests.

## TDD evidence

RED, at once after adding the tests and before production modules existed:

```text
pytest tests/test_source_catalog.py
ModuleNotFoundError: No module named 'etl.source_catalog'

pytest tests/test_philgeps.py
AttributeError: module 'etl.philgeps' has no attribute 'write_snapshot_inventory'

pytest tests/test_source_monitor.py
ModuleNotFoundError: No module named 'etl.source_monitor'
```

GREEN:

```text
.venv/bin/python -m pytest -q tests/test_source_catalog.py tests/test_philgeps.py tests/test_source_monitor.py tests/test_cache.py tests/test_etl_hardening.py tests/test_imports.py tests/test_psa_inflation.py
52 passed in 2.03s

.venv/bin/ruff check etl/source_catalog.py etl/psa_openstat.py etl/philgeps.py etl/source_monitor.py tests/test_source_catalog.py tests/test_philgeps.py tests/test_source_monitor.py
All checks passed!

.venv/bin/ruff format --check etl/source_catalog.py etl/psa_openstat.py etl/philgeps.py etl/source_monitor.py tests/test_source_catalog.py tests/test_philgeps.py tests/test_source_monitor.py
7 files already formatted
```

An all-suite run was started. It did not reach a terminal result within the 30-second tool window. This report does not claim it passed.

## Source-contract decisions

- The catalog selects a PSA table by title terms, dimension codes, measure labels, and needed years. Discovery checks the reviewed parent directory first. It uses a versioned fallback only after the same validation passes.
- PhilGEPS processing reads only local parquet chunks. `acquire_snapshot()` needs `allow_network=True`. The build never calls it as an implicit cache miss. `write_snapshot_inventory()` produces one reviewable local record per complete snapshot.
- Normal tests are offline. The only live probe is `python -m etl.source_monitor --live`, which the scheduled workflow invokes. It reports metadata reachability, official versus shipped years, source unit coverage, local snapshot age/revision status, and a live PSGC hash comparison.

## Residuals and integration handoff

- Run `python -m etl.source_monitor --live` to do the explicit live probe. No live probe ran in this implementation session.
- Do not move `etl/psa_inflation.py` in Task 1. It still imports legacy PSA path aliases. They now derive from catalog fallbacks.
- Task 3 must update the stale `etl/build.py` `--no-cache` help, currently `Ignore on-disk caches; refetch every upstream source.`, and README claims that say it forces a PhilGEPS refetch. Intended wording: `Clear PSA and PSGC caches and refetch those sources; PhilGEPS uses an existing reviewed local snapshot and must be acquired separately.`

## Commit

7af5586 Add upstream source contracts and monitoring

## Round 1 review repairs

### RED evidence

New tests failed before their repairs:

    tests/test_source_catalog.py
    httpx.HTTPStatusError: gone

    tests/test_philgeps.py
    ValueError: PhilGEPS chunk 1 missing required columns
    FileNotFoundError after a forced second-chunk promotion failure

    tests/test_source_monitor.py
    AttributeError: 'str' object has no attribute 'get'

The first live monitor also failed on the retired 1E/FY path. A live metadata probe then exposed year codes such as 0, 1, and 2. The published years are in valueTexts.

### GREEN evidence

    .venv/bin/python -m pytest -q tests/test_source_catalog.py tests/test_philgeps.py tests/test_source_monitor.py tests/test_etl_hardening.py tests/test_psa_inflation.py tests/test_cache.py tests/test_imports.py
    63 passed in 0.73s

    .venv/bin/ruff check .
    All checks passed!

    .venv/bin/ruff format --check .
    36 files already formatted

    python -m etl.source_monitor --live
    All five PSA contracts reachable and validated. The report found CPI one official year ahead of shipped data, a 96-day PhilGEPS snapshot, and PSGC geography hash drift.

### Repairs and evidence artifact

- The catalog now uses current PSA paths for poverty, subsistence, and the 2020 census. It skips unavailable directories and stale leaf metadata before trying a checked fallback.
- Regional loaders use discover_table() for poverty and CPI. The population contract requires a 2020 vintage.
- etl/philgeps_snapshot_inventory.json is a tracked inventory built from the protected cache. It records every chunk hash and count, the shared production schema, usable ID counts, observed date coverage, and anomalies.
- The reviewed cache contains dates from 1920-01-08 through 2034-10-04. It has one invalid award date, 1,044,471 dates outside the 2014-2024 panel, and zero cross-chunk duplicate IDs.
- Processing now verifies the cache against that reviewed inventory before aggregation. Acquisition validates a staged complete snapshot and rolls back every promoted chunk if promotion fails.
- The --no-cache help and README now state that it clears PSA and PSGC caches only. PhilGEPS acquisition is separate.

### Round 1 commit

Pending commit.
