"""Time-series behaviour: values, gaps, single-pass retrieval."""

from __future__ import annotations

import pytest

from app import timeseries
from tests.fake_ee import Image, SceneSpec, build_sentinel_scene, register_scenes

S2 = "COPERNICUS/S2_SR_HARMONIZED"


def test_annual_series_returns_one_point_per_year_with_known_values(fake):
    register_scenes(
        S2,
        [
            build_sentinel_scene(SceneSpec("2024-08-01", nir=0.40, red=0.10)),  # NDVI 0.6
            build_sentinel_scene(SceneSpec("2025-08-01", nir=0.30, red=0.10)),  # NDVI 0.5
        ],
    )
    payload = timeseries.compute_time_series(
        fake, indicator_id="ndvi", geometry=None, start_year=2024, end_year=2025, aggregation="year"
    )
    values = {point["period"]: point["value"] for point in payload["series"]}
    assert values == {"2024": pytest.approx(0.6, abs=1e-3), "2025": pytest.approx(0.5, abs=1e-3)}
    assert payload["gaps"] == []
    assert payload["source"]["dataset"] == S2
    assert payload["interpolation"].startswith("none")


def test_missing_year_is_reported_as_a_gap_not_a_value(fake):
    register_scenes(S2, [build_sentinel_scene(SceneSpec("2024-08-01", nir=0.40, red=0.10))])
    payload = timeseries.compute_time_series(
        fake, indicator_id="ndvi", geometry=None, start_year=2024, end_year=2026, aggregation="year"
    )
    by_period = {point["period"]: point for point in payload["series"]}
    assert by_period["2024"]["value"] is not None
    assert by_period["2025"]["value"] is None
    assert by_period["2025"]["data_status"] == "insufficient_observations"
    assert by_period["2026"]["value"] is None
    assert set(payload["gaps"]) == {"2025", "2026"}


def test_monthly_aggregation_produces_twelve_periods_per_year(fake):
    register_scenes(S2, [build_sentinel_scene(SceneSpec("2025-07-15"))])
    payload = timeseries.compute_time_series(
        fake, indicator_id="ndvi", geometry=None, start_year=2025, end_year=2025, aggregation="month"
    )
    assert len(payload["series"]) == 12
    filled = [point for point in payload["series"] if point["value"] is not None]
    assert [point["period"] for point in filled] == ["2025-07"]


def test_ancillary_series_applies_documented_scaling(fake):
    register_scenes(
        "MODIS/061/MOD11A2",
        [
            Image({"LST_Day_1km": __import__("numpy").full((8, 8), 15000.0)}, properties={"system:time_start": 1}),
        ],
    )
    payload = timeseries.compute_time_series(
        fake, indicator_id="lst", geometry=None, start_year=1970, end_year=1970, aggregation="year"
    )
    assert payload["series"][0]["value"] == pytest.approx(26.85, abs=1e-6)


def test_series_carries_observation_counts_per_period(fake):
    register_scenes(
        S2,
        [
            build_sentinel_scene(SceneSpec("2024-07-01")),
            build_sentinel_scene(SceneSpec("2024-08-01")),
            build_sentinel_scene(SceneSpec("2024-09-01")),
        ],
    )
    payload = timeseries.compute_time_series(
        fake, indicator_id="ndvi", geometry=None, start_year=2024, end_year=2024, aggregation="year"
    )
    assert payload["series"][0]["observation_count"] == 3
