"""Vector data tests against the real shapefiles in ``shapefiles/``.

These run without Earth Engine: basin geometry, areas and Green Legacy records
are served from the project's own data, so they must be verifiable on their own.
"""

from __future__ import annotations

import geopandas as gpd
import pytest

from app import geometry
from app.config import BASINS, GREEN_LEGACY_FILE, get_basin
from app.errors import UnknownBasinError


@pytest.fixture(scope="module", autouse=True)
def _clear_cache():
    geometry.clear_geometry_cache()
    yield


def test_every_registered_basin_has_both_vector_files():
    for basin in BASINS:
        assert basin.basin_path.exists(), basin.basin_file
        assert basin.buffer_path.exists(), basin.buffer_file


def test_catalog_reports_six_basins_with_positive_areas():
    catalog = geometry.basins_catalog()
    assert len(catalog) == 6
    for record in catalog:
        assert record["area_km2"] > 1000
        assert record["buffer_area_km2"] > 100
        assert len(record["bbox"]) == 4


def test_baro_akobo_crs_mismatch_is_reported_not_hidden():
    record = next(item for item in geometry.basins_catalog() if item["id"] == "baro_akobo")
    assert record["source"]["basin_crs"] == 4326
    assert any("EPSG:4326" in warning for warning in record["warnings"])


def test_abay_attribute_area_disagreement_is_flagged():
    record = next(item for item in geometry.basins_catalog() if item["id"] == "abay")
    assert any("SQKM" in warning for warning in record["warnings"])


def test_area_is_computed_in_an_equal_area_projection():
    frame = geometry.read_layer(get_basin("tekeze").basin_path)
    expected = frame.to_crs(geometry.AREA_CRS).geometry.area.sum() / 1e6
    assert geometry.area_km2(frame) == pytest.approx(expected, rel=1e-6)


def test_geojson_is_wgs84_two_dimensional_and_closed():
    payload = geometry.basin_geojson("omo", "basin")
    assert payload["type"] == "FeatureCollection"
    feature = payload["features"][0]
    rings = feature["geometry"]["coordinates"]
    if feature["geometry"]["type"] == "Polygon":
        rings = [rings]
    for polygon in rings:
        for ring in polygon:
            for coordinate in ring:
                assert len(coordinate) == 2, "GeoJSON must be 2D"
            assert ring[0] == ring[-1], "rings must be closed"
    assert payload["provenance"]["output_crs"] == "EPSG:4326"
    assert payload["provenance"]["simplify_tolerance_m"] == 150.0


def test_buffer_distance_is_read_from_the_source_attribute():
    payload = geometry.basin_geojson("abay", "buffer")
    props = payload["features"][0]["properties"]
    assert props["buffer_distance_m"] == 2000.0
    assert "2000 m" in props["buffer_note"]


def test_invalid_geometries_are_repaired_on_load():
    frame = geometry.read_layer(GREEN_LEGACY_FILE)
    assert frame.geometry.is_valid.all()


def test_green_legacy_area_attribute_is_square_metres_not_hectares():
    """The audit finding, re-verified from the data itself."""

    frame = gpd.read_file(GREEN_LEGACY_FILE)
    ratio = (frame["Area_Ha"] / frame["Shape_Area"]).dropna()
    assert ratio.median() == pytest.approx(1.0, abs=1e-4)


def test_green_legacy_summary_uses_geometric_area():
    summary = geometry.green_legacy_summary()
    assert summary["feature_count"] == 21429
    # 651,540 ha computed from geometry; summing Area_Ha as hectares would give
    # 6.5 billion ha, which is why the attribute is not used.
    assert 600_000 < summary["total_area_ha"] < 700_000
    assert "square metres (m^2)" in summary["data_quality"]["area_field"]


def test_green_legacy_can_be_subset_by_basin():
    whole = geometry.green_legacy_summary()
    subset = geometry.green_legacy_summary("tekeze")
    assert subset["feature_count"] < whole["feature_count"]
    assert subset["subset"] == "Tekeze"


def test_green_legacy_points_payload_is_capped_and_labelled():
    payload = geometry.green_legacy_geojson(basin_id=None, mode="points", limit=50)
    assert payload["provenance"]["feature_count_returned"] == 50
    assert payload["provenance"]["truncated"] is True
    assert payload["provenance"]["feature_count_total"] == 21429
    assert "does not measure tree counts or survival" in payload["provenance"]["note"]


def test_unknown_basin_raises_a_typed_error():
    with pytest.raises(UnknownBasinError):
        geometry.basin_geojson("does-not-exist")


def test_basin_geometry_for_earth_engine_is_lon_lat():
    shape = geometry.basin_geometry_4326("tekeze", "basin")
    minx, miny, maxx, maxy = shape.bounds
    assert 30 < minx < 50 and 0 < miny < 20, (minx, miny, maxx, maxy)


def test_layer_inventory_reports_crs_and_feature_counts():
    inventory = geometry.data_inventory()
    names = {entry["name"] for entry in inventory}
    assert "abay_basin" in names and "green_legacy" in names
    green = next(entry for entry in inventory if entry["name"] == "green_legacy")
    assert green["feature_count"] == 21429
    assert green["source_crs"] == 32637
