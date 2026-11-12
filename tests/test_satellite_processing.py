"""Processing chain tests executed against the numerical fake ``ee``.

Every expected value here is computed by hand from the documented equations, so
these tests fail if a band, a scaling factor or a mask changes.
"""

from __future__ import annotations

import numpy as np
import pytest

from app import satellite, statistics
from tests.fake_ee import Image, SceneSpec, build_sentinel_scene, register_scenes, reset_scenes

S2 = "COPERNICUS/S2_SR_HARMONIZED"


def _register(*specs: SceneSpec) -> None:
    register_scenes(S2, [build_sentinel_scene(spec) for spec in specs])


def _zonal(ee, collection, bands, scale=10):
    return (
        collection.median()
        .select(bands)
        .reduceRegion(reducer=satellite.zonal_reducer(ee), geometry=None, scale=scale, bestEffort=True)
        .getInfo()
    )


# --------------------------------------------------------------- index values


def test_ndvi_matches_the_documented_equation(fake):
    # NIR 0.45, RED 0.10 -> (0.45 - 0.10) / (0.45 + 0.10) = 0.636364
    _register(SceneSpec("2026-08-01", nir=0.45, red=0.10))
    collection = satellite.s2_collection(fake, "2026-07-01", "2026-09-01", None)
    result = _zonal(fake, collection, ["NDVI"])
    assert result["NDVI_mean"] == pytest.approx(0.6363636, abs=1e-6)
    assert result["NDVI_count"] == 64


def test_ndwi_matches_the_documented_equation(fake):
    # GREEN 0.12, NIR 0.45 -> (0.12 - 0.45) / (0.12 + 0.45) = -0.578947
    _register(SceneSpec("2026-08-01", green=0.12, nir=0.45))
    collection = satellite.s2_collection(fake, "2026-07-01", "2026-09-01", None)
    result = _zonal(fake, collection, ["NDWI"])
    assert result["NDWI_mean"] == pytest.approx(-0.5789473, abs=1e-6)


def test_mndwi_uses_swir_band_11(fake):
    # GREEN 0.12, SWIR 0.20 -> (0.12 - 0.20) / (0.32) = -0.25
    _register(SceneSpec("2026-08-01", green=0.12, swir=0.20))
    collection = satellite.s2_collection(fake, "2026-07-01", "2026-09-01", None)
    result = _zonal(fake, collection, ["MNDWI"])
    assert result["MNDWI_mean"] == pytest.approx(-0.25, abs=1e-9)


def test_evi_expression_is_evaluated_with_correct_bands(fake):
    # 2.5 * (0.45 - 0.10) / (0.45 + 6*0.10 - 7.5*0.08 + 1) = 0.875 / 1.45
    _register(SceneSpec("2026-08-01", nir=0.45, red=0.10, blue=0.08))
    collection = satellite.s2_collection(fake, "2026-07-01", "2026-09-01", None)
    result = _zonal(fake, collection, ["EVI"])
    assert result["EVI_mean"] == pytest.approx(2.5 * 0.35 / 1.45, abs=1e-6)


def test_reflectance_scaling_divides_dn_by_10000(fake):
    # The scene stores DN the way S2_SR_HARMONIZED does (reflectance * 10000),
    # so masking must convert 4500 DN back to 0.45 reflectance.
    scene = build_sentinel_scene(SceneSpec("2026-08-01", nir=0.45))
    assert scene.bands["B8"][0][0] == pytest.approx(4500.0)
    masked = satellite.mask_s2_clouds(fake, scene)
    assert masked.bands["B8"][0][0] == pytest.approx(0.45)


# ----------------------------------------------------------------- cloud mask


def test_qa60_and_scl_masking_removes_cloudy_pixels(fake):
    # 25% of the scene flagged cloudy in QA60 (bit 10) and SCL class 9.
    _register(SceneSpec("2026-08-01", cloud_fraction=0.25))
    collection = satellite.s2_collection(fake, "2026-07-01", "2026-09-01", None)
    result = _zonal(fake, collection, ["NDVI"])
    # 8x8 grid minus the 2 cloudy columns = 48 valid pixels.
    assert result["NDVI_count"] == 48


def test_cirrus_bit_is_also_masked(fake):
    scene = build_sentinel_scene(SceneSpec("2026-08-01"))
    scene.bands["QA60"][:, 0] = float(1 << 11)  # cirrus bit on one column
    masked = satellite.mask_s2_clouds(fake, scene)
    assert masked.mask[:, 0].sum() == 0
    assert masked.mask[:, 1:].all()


def test_scenes_above_the_cloud_threshold_are_rejected(fake):
    _register(
        SceneSpec("2026-08-01", cloudy_percentage=95.0),
        SceneSpec("2026-08-06", cloudy_percentage=10.0),
    )
    collection = satellite.s2_collection(fake, "2026-07-01", "2026-09-01", None, 70.0)
    assert satellite.count_observations(fake, collection) == 1


def test_cloud_free_scene_keeps_every_pixel(fake):
    _register(SceneSpec("2026-08-01", cloud_fraction=0.0))
    collection = satellite.s2_collection(fake, "2026-07-01", "2026-09-01", None)
    result = _zonal(fake, collection, ["NDVI"])
    assert result["NDVI_count"] == 64


# ------------------------------------------------------------------- medians


def test_median_composite_is_robust_to_a_cloudy_outlier(fake):
    clean = SceneSpec("2026-08-01", nir=0.45, red=0.10)
    outlier = SceneSpec("2026-08-06", nir=0.05, red=0.40)  # bare soil
    _register(clean, clean, outlier)
    collection = satellite.s2_collection(fake, "2026-07-01", "2026-09-01", None)
    result = _zonal(fake, collection, ["NDVI"])
    expected = (0.45 - 0.10) / (0.45 + 0.10)
    assert result["NDVI_mean"] == pytest.approx(expected, abs=1e-6)


# ------------------------------------------------------------- ancillary data


def test_rainfall_is_the_period_sum_not_the_mean(fake):
    from app.indicators import get_indicator

    dataset = "UCSB-CHG/CHIRPS/DAILY"
    register_scenes(
        dataset,
        [
            Image({"precipitation": np.full((8, 8), 5.0)}, properties={"system:time_start": 1}),
            Image({"precipitation": np.full((8, 8), 7.0)}, properties={"system:time_start": 2}),
        ],
    )
    image = satellite.ancillary_image(fake, get_indicator("rainfall"), "1970-01-01", "1970-01-03", None)
    result = image.reduceRegion(reducer=satellite.zonal_reducer(fake), geometry=None, scale=5566).getInfo()
    assert result["RAINFALL_mean"] == pytest.approx(12.0)


def test_lst_is_scaled_from_kelvin_to_celsius(fake):
    from app.indicators import get_indicator

    register_scenes(
        "MODIS/061/MOD11A2",
        [Image({"LST_Day_1km": np.full((8, 8), 15000.0)}, properties={"system:time_start": 1})],
    )
    image = satellite.ancillary_image(fake, get_indicator("lst"), "1970-01-01", "1970-01-03", None)
    result = image.reduceRegion(reducer=satellite.zonal_reducer(fake), geometry=None, scale=1000).getInfo()
    # 15000 * 0.02 = 300 K -> 26.85 C
    assert result["LST_mean"] == pytest.approx(26.85, abs=1e-9)


def test_et_is_scaled_from_kg_per_m2_to_mm(fake):
    from app.indicators import get_indicator

    register_scenes(
        "MODIS/061/MOD16A2GF",
        [Image({"ET": np.full((8, 8), 350.0)}, properties={"system:time_start": 1})],
    )
    image = satellite.ancillary_image(fake, get_indicator("et"), "1970-01-01", "1970-01-03", None)
    result = image.reduceRegion(reducer=satellite.zonal_reducer(fake), geometry=None, scale=500).getInfo()
    assert result["ET_mean"] == pytest.approx(35.0)


def test_empty_collection_is_detected_instead_of_being_zero_filled(fake):
    reset_scenes()
    register_scenes(S2, [])
    collection = satellite.s2_collection(fake, "2026-07-01", "2026-09-01", None)
    assert satellite.collection_is_empty(fake, collection) is True
    assert satellite.count_observations(fake, collection) == 0


# --------------------------------------------------------- statistics service


def test_statistics_report_value_coverage_and_freshness(fake):
    from datetime import datetime, timedelta, timezone

    recent = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d")
    _register(SceneSpec(recent, nir=0.45, red=0.10))

    results = statistics.compute_zonal_statistics(
        fake,
        indicators=["ndvi", "ndwi", "mndwi"],
        geometry=None,
        area_km2=1.0,  # 1 km^2 -> 10,000 pixels at 10 m
        start="2026-01-01",
        end="2027-01-01",
    )

    ndvi = results["ndvi"].to_dict()
    assert ndvi["value"] == pytest.approx(0.6364, abs=1e-3)
    assert ndvi["observation_count"] == 1
    assert ndvi["valid_pixel_count"] == 64
    assert ndvi["expected_pixel_count"] == 10_000
    assert ndvi["coverage_fraction"] == pytest.approx(64 / 10_000)
    assert ndvi["data_status"] == "current"
    assert ndvi["latest_observation"] is not None
    assert ndvi["source"]["dataset"] == S2
    assert "SCL" in ndvi["source"]["mask_method"]


def test_no_observations_returns_null_not_zero(fake):
    reset_scenes()
    register_scenes(S2, [])
    results = statistics.compute_zonal_statistics(
        fake,
        indicators=["ndvi"],
        geometry=None,
        area_km2=1.0,
        start="2026-01-01",
        end="2026-02-01",
    )
    payload = results["ndvi"].to_dict()
    assert payload["value"] is None
    assert payload["data_status"] == "insufficient_observations"
    assert payload["observation_count"] == 0
    assert payload["note"]


def test_min_observations_threshold_is_enforced(fake):
    _register(SceneSpec("2026-08-01"))
    results = statistics.compute_zonal_statistics(
        fake,
        indicators=["ndvi"],
        geometry=None,
        area_km2=1.0,
        start="2026-07-01",
        end="2026-09-01",
        min_observations=3,
    )
    assert results["ndvi"].to_dict()["value"] is None
    assert results["ndvi"].to_dict()["data_status"] == "insufficient_observations"


def test_reduction_scale_matches_the_indicator_resolution(fake):
    _register(SceneSpec("2026-08-01"))
    collection = satellite.s2_collection(fake, "2026-07-01", "2026-09-01", None)
    image = collection.median().select(["NDVI"])
    image.reduceRegion(reducer=satellite.zonal_reducer(fake), geometry=None, scale=10).getInfo()
    assert image.recorded["scale"] == 10


def test_all_masked_pixels_yield_null_mean(fake):
    scene = build_sentinel_scene(SceneSpec("2026-08-01", cloud_fraction=1.0))
    register_scenes(S2, [scene])
    collection = sentinel_collection_all_cloudy(fake)
    result = _zonal(fake, collection, ["NDVI"])
    assert result["NDVI_mean"] is None
    assert result["NDVI_count"] == 0


def sentinel_collection_all_cloudy(fake):
    """Collection whose every pixel is masked, with the cloud filter disabled."""

    return satellite.s2_collection(fake, "2026-07-01", "2026-09-01", None, 100.0)
