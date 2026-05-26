"""Sanity test: every ETL module imports and exposes its declared callable."""

from etl import build, interpolate, philgeps, psa_openstat, psgc, validate


def test_build_main_exists():
    assert callable(build.main)


def test_psa_openstat_callables():
    assert callable(psa_openstat.fetch_poverty)
    assert callable(psa_openstat.fetch_population_2020)


def test_philgeps_callable():
    assert callable(philgeps.fetch_dpwh_spend)


def test_psgc_callables():
    assert callable(psgc.load_provinces)
    assert callable(psgc.normalize_name)


def test_interpolate_callable():
    assert callable(interpolate.linear_fill)


def test_validate_callable():
    assert callable(validate.validate_all)
