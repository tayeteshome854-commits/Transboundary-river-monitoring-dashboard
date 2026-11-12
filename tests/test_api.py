"""API contract tests: statuses, validation, error envelope, honest failures."""

from __future__ import annotations

import pytest

from app import service
from app.config import settings
from app.ee_client import ee_client
from tests.fake_ee import SceneSpec, build_sentinel_scene, register_scenes

S2 = "COPERNICUS/S2_SR_HARMONIZED"


def _scenes(count=2):
    return [
        build_sentinel_scene(SceneSpec(f"2026-08-0{index + 1}", nir=0.45, red=0.10, green=0.12, swir=0.20))
        for index in range(count)
    ]


# ------------------------------------------------------------------- health


def test_health_reports_application_state(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_status_endpoint_is_backwards_compatible(client):
    body = client.get("/api/status").json()
    assert body["status"] == "running"
    assert "version" in body


def test_readiness_is_degraded_without_earth_engine(client, no_earth_engine):
    response = client.get("/api/readiness")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["earth_engine"]["available"] is False
    assert body["vector_data"]["available"] is True
    assert body["hydrology"]["connected"] is False


def test_readiness_is_ready_with_a_working_data_source(client, fake):
    register_scenes(S2, _scenes())
    response = client.get("/api/readiness")
    assert response.status_code == 200
    assert response.json()["earth_engine"]["available"] is True


# -------------------------------------------------------------------- config


def test_config_exposes_basins_indicators_and_freshness_policy(client):
    body = client.get("/api/config").json()
    assert {basin["id"] for basin in body["basins"]} == {
        "abay", "baro_akobo", "genale_dawa", "omo", "tekeze", "wabe_shabale"
    }
    assert {indicator["id"] for indicator in body["indicators"]} >= {"ndvi", "ndwi", "mndwi"}
    assert body["freshness_policy"]["fresh_days"] == settings.fresh_days
    assert "Not continuous river-level" in body["satellite_scope"]


# -------------------------------------------------------------------- vector


def test_basins_endpoint_returns_real_geometry(client):
    body = client.get("/api/basins").json()
    assert len(body["basins"]) == 6
    abay = next(basin for basin in body["basins"] if basin["id"] == "abay")
    assert abay["area_km2"] > 150_000
    assert abay["buffer_distance_m"] == 2000.0


def test_basin_geojson_accepts_aliases(client):
    for alias in ("Abay", "abay", "Abay Basin", "blue nile"):
        response = client.get(f"/api/basin/{alias.replace(' ', '_')}/geojson")
        assert response.status_code == 200, alias
        assert response.json()["features"][0]["properties"]["basin_id"] == "abay"


def test_buffer_geojson_legacy_route_still_works(client):
    body = client.get("/api/buffer/tekeze/geojson").json()
    assert body["features"][0]["properties"]["layer"] == "buffer"


def test_unknown_basin_returns_404_with_the_error_envelope(client):
    response = client.get("/api/basin/nile/geojson")
    assert response.status_code == 404
    body = response.json()["error"]
    assert body["code"] == "unknown_basin"
    assert "available" in body["detail"]
    assert "timestamp" in body


def test_invalid_region_parameter_is_rejected(client):
    response = client.get("/api/basin/abay/geojson?region=upstream")
    assert response.status_code in (400, 422)


# ---------------------------------------------------------------- validation


def test_invalid_dates_are_rejected(client, fake):
    response = client.get("/api/basin/abay/stats?start=not-a-date&end=2026-01-01")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_inverted_window_is_rejected(client, fake):
    response = client.get("/api/basin/abay/stats?start=2026-05-01&end=2026-01-01")
    assert response.status_code == 400


def test_unknown_indicator_is_rejected(client, fake):
    response = client.get("/api/basin/abay/timeseries/vegetation_index")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_indicator"


def test_invalid_timeseries_aggregation_is_rejected(client, fake):
    response = client.get("/api/basin/abay/timeseries/ndvi?aggregation=weekly")
    assert response.status_code in (400, 422)


def test_change_request_with_inverted_baseline_is_rejected(client, fake):
    response = client.post(
        "/api/basin/abay/change",
        json={
            "indicator": "ndvi",
            "baseline_start": "2025-06-01",
            "baseline_end": "2025-01-01",
            "comparison_start": "2026-06-01",
            "comparison_end": "2026-09-01",
        },
    )
    assert response.status_code == 400


# ------------------------------------------------------------------ products


def test_basin_statistics_payload_is_complete(client, fake):
    register_scenes(S2, _scenes())
    response = client.get("/api/basin/tekeze/stats?start=2026-07-01&end=2026-09-01")
    assert response.status_code == 200
    body = response.json()
    assert set(body["indicators"]) == {"ndvi", "ndwi", "mndwi"}
    ndvi = body["indicators"]["ndvi"]
    assert ndvi["value"] is not None
    assert ndvi["observation_count"] == 2
    assert ndvi["units"]
    assert ndvi["source"]["dataset"] == S2
    assert ndvi["source"]["scale_m"] == 10
    assert body["region_area_km2"] > 1000
    assert body["data_status"] in {"current", "delayed", "stale"}


def test_tile_layer_returns_a_tile_url_and_provenance(client, fake):
    register_scenes(S2, _scenes())
    response = client.get("/api/tiles/tekeze/ndvi?start=2026-07-01&end=2026-09-01")
    assert response.status_code == 200
    body = response.json()
    assert body["tile_url"].startswith("https://")
    assert body["observation_count"] == 2
    assert body["visualization"]["palette"]
    assert body["source"]["mask_method"]


def test_legacy_year_tile_route_is_supported(client, fake):
    register_scenes(S2, _scenes())
    response = client.get("/api/tiles/tekeze/ndvi/2026")
    assert response.status_code == 200
    assert "tile_url" in response.json()


def test_tile_layer_reports_insufficient_observations(client, fake):
    register_scenes(S2, [])
    response = client.get("/api/tiles/tekeze/ndvi?start=2026-07-01&end=2026-09-01")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "insufficient_observations"


def test_change_detection_reports_comparability(client, fake):
    register_scenes(
        S2,
        [
            build_sentinel_scene(SceneSpec("2025-08-01", nir=0.40, red=0.10, green=0.12, swir=0.20)),
            build_sentinel_scene(SceneSpec("2026-08-01", nir=0.50, red=0.10, green=0.12, swir=0.20)),
        ],
    )
    response = client.post(
        "/api/basin/tekeze/change",
        json={
            "indicator": "ndvi",
            "baseline_start": "2025-07-01",
            "baseline_end": "2025-09-01",
            "comparison_start": "2026-07-01",
            "comparison_end": "2026-09-01",
        },
    )
    assert response.status_code == 200
    body = response.json()
    stats = body["statistics"]
    assert stats["comparable"] is True
    assert stats["baseline"]["value"] < stats["comparison"]["value"]
    assert stats["difference"] > 0
    assert stats["percentage_change"] is not None
    assert body["map"]["tile_url"].startswith("https://")


def test_overview_lists_all_basins_with_data_status(client, fake):
    register_scenes(S2, _scenes())
    body = client.get("/api/overview?start=2026-07-01&end=2026-09-01").json()
    assert body["basin_count"] == 6
    assert body["earth_engine_available"] is True
    for basin in body["basins"]:
        assert "data_status" in basin
        assert "area_km2" in basin
    assert "not continuous river-level" in body["scope_note"]


def test_overview_without_earth_engine_reports_unavailable_not_zeros(client, no_earth_engine):
    body = client.get("/api/overview").json()
    assert body["earth_engine_available"] is False
    assert body["data_status"] == "unavailable"
    for basin in body["basins"]:
        assert basin["indicators"] == {}
        assert basin["data_status"] == "unavailable"
        assert basin["area_km2"] > 0  # geometry is still real


# ---------------------------------------------------------------- hydrology


def test_hydrology_states_it_is_not_connected(client):
    integration = client.get("/api/hydrology/integration").json()
    assert integration["connected"] is False

    response = client.get("/api/hydrology/stations")
    assert response.status_code == 501
    body = response.json()["error"]
    assert body["code"] == "hydrology_not_connected"
    assert "not river stage or discharge" in body["detail"]["explanation"]
    assert body["detail"]["simulated_data"] == "never produced by this application"


def test_hydrology_observations_require_a_provider(client):
    response = client.get("/api/hydrology/stations/ST-1/observations?start=2026-01-01&end=2026-02-01")
    assert response.status_code == 501


# --------------------------------------------------------- failure behaviour


def test_stats_without_earth_engine_returns_503_and_no_values(client, no_earth_engine, monkeypatch):
    monkeypatch.setattr(type(settings), "allow_demo_mode", property(lambda self: False), raising=False)
    response = client.get("/api/basin/abay/stats")
    assert response.status_code == 503
    body = response.json()["error"]
    assert body["code"] == "earth_engine_unavailable"
    assert "value" not in response.text


def test_demonstration_mode_is_labelled_when_enabled(client, no_earth_engine, monkeypatch):
    monkeypatch.setattr(type(settings), "allow_demo_mode", property(lambda self: True), raising=False)
    response = client.get("/api/basin/abay/stats")
    assert response.status_code == 200
    body = response.json()
    assert body["data_status"] == "demonstration"
    assert "DEMONSTRATION DATA" in body["warning"]
    assert body["indicators"]["ndvi"]["data_status"] == "demonstration"
    assert body["indicators"]["ndvi"]["observation_count"] == 0


def test_tiles_are_never_synthesised_in_demonstration_mode(client, no_earth_engine, monkeypatch):
    monkeypatch.setattr(type(settings), "allow_demo_mode", property(lambda self: True), raising=False)
    response = client.get("/api/tiles/abay/ndvi")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "earth_engine_unavailable"


# -------------------------------------------------------------- cache admin


def test_cache_stats_and_clear(client, fake):
    register_scenes(S2, _scenes())
    client.get("/api/basin/tekeze/stats?start=2026-07-01&end=2026-09-01")
    stats = client.get("/api/cache").json()
    assert stats["products"]["size"] >= 1
    cleared = client.post("/api/cache/clear").json()
    assert cleared["cleared_geometry_cache"] is True


def test_data_inventory_lists_every_dataset(client):
    body = client.get("/api/data/inventory").json()
    names = {entry["name"] for entry in body["datasets"]}
    assert "green_legacy" in names
    assert len(names) == 13


def test_green_legacy_endpoints(client):
    summary = client.get("/api/green-legacy/summary").json()
    assert summary["feature_count"] == 21429
    features = client.get("/api/green-legacy/geojson?limit=20").json()
    assert len(features["features"]) == 20
    wereda = client.get("/api/green-legacy/by-wereda").json()
    assert wereda["provenance"]["aggregation"] == "region / zone / wereda"


def test_report_renders_html(client, fake):
    register_scenes(S2, _scenes())
    response = client.get("/report?basin=tekeze&indicator=ndvi&start=2026-07-01&end=2026-09-01")
    assert response.status_code == 200
    assert "Tekeze" in response.text
    assert "Interpretation limits" in response.text


def test_frontend_assets_are_served(client):
    index = client.get("/")
    assert index.status_code == 200
    assert "Ethiopian Transboundary River Monitoring" in index.text
    for asset in ("/static/app.js", "/static/styles.css", "/static/vendor/leaflet.js", "/static/vendor/chart.umd.js"):
        assert client.get(asset).status_code == 200, asset
