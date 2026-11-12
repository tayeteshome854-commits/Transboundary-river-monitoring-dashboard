"""Indicator registry: band mapping, equations, scales and units."""

from __future__ import annotations

import pytest

from app.indicators import (
    CORE_INDICATORS,
    INDICATORS,
    get_indicator,
    normalise_indicator,
    palette_to_vis,
)


@pytest.mark.parametrize(
    ("indicator_id", "expected_bands", "expected_equation"),
    [
        ("ndvi", ("B8", "B4"), "(B8 - B4) / (B8 + B4)"),
        ("ndwi", ("B3", "B8"), "(B3 - B8) / (B3 + B8)"),
        ("mndwi", ("B3", "B11"), "(B3 - B11) / (B3 + B11)"),
    ],
)
def test_spectral_indices_use_documented_sentinel2_bands(indicator_id, expected_bands, expected_equation):
    indicator = get_indicator(indicator_id)
    assert indicator.bands == expected_bands
    assert indicator.equation == expected_equation
    assert indicator.dataset == "COPERNICUS/S2_SR_HARMONIZED"
    assert indicator.is_spectral_index is True


def test_spectral_indices_use_10m_scale_and_unit_range():
    for indicator_id in ("ndvi", "evi", "ndwi", "mndwi"):
        indicator = get_indicator(indicator_id)
        assert indicator.scale_m == 10
        assert indicator.valid_range == (-1.0, 1.0)
        assert indicator.units.startswith("dimensionless")


def test_evi_expression_matches_standard_formulation():
    assert get_indicator("evi").bands == ("B8", "B4", "B2")
    assert "2.5" in get_indicator("evi").equation
    assert "7.5" in get_indicator("evi").equation


@pytest.mark.parametrize(
    ("indicator_id", "dataset", "scale"),
    [
        ("rainfall", "UCSB-CHG/CHIRPS/DAILY", 5566),
        ("lst", "MODIS/061/MOD11A2", 1000),
        ("air_temp", "ECMWF/ERA5_LAND/DAILY_AGGR", 11132),
        ("et", "MODIS/061/MOD16A2GF", 500),
        ("soil_moisture", "NASA/SMAP/SPL4SMGP/007", 9000),
    ],
)
def test_ancillary_products_declare_their_source_and_native_scale(indicator_id, dataset, scale):
    indicator = get_indicator(indicator_id)
    assert indicator.dataset == dataset
    assert indicator.scale_m == scale
    assert indicator.is_spectral_index is False


def test_every_indicator_exposes_units_range_and_caveat_fields():
    for indicator in INDICATORS.values():
        assert indicator.units
        assert indicator.description
        assert isinstance(indicator.caveat, str)
        assert indicator.palette, f"{indicator.id} has no palette"


def test_palette_values_are_monotonically_increasing():
    for indicator in INDICATORS.values():
        values = [stop[0] for stop in indicator.palette]
        assert values == sorted(values), f"{indicator.id} palette is not ordered"


def test_palette_to_vis_produces_earth_engine_visualisation():
    vis = palette_to_vis(get_indicator("ndvi"))
    assert vis["min"] == -0.2
    assert vis["max"] == 0.85
    assert len(vis["palette"]) == len(get_indicator("ndvi").palette)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("NDVI", "ndvi"),
        ("mndwi", "mndwi"),
        ("Air Temp", "air_temp"),
        ("land-surface-temperature", "lst"),
        ("precipitation", "rainfall"),
        ("evapotranspiration", "et"),
        ("nonsense", None),
        (None, None),
    ],
)
def test_indicator_normalisation(raw, expected):
    assert normalise_indicator(raw) == expected


def test_core_indicator_set_is_vegetation_and_water():
    assert set(CORE_INDICATORS) == {"ndvi", "ndwi", "mndwi"}


def test_water_indices_carry_interpretation_guard_rails():
    for indicator_id in ("ndwi", "mndwi"):
        assert "not a validated water-body classification" in get_indicator(indicator_id).caveat
    assert "not by itself proof of drought" in get_indicator("ndvi").caveat
