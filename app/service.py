"""Service layer: turns HTTP intent into verified products.

Responsibilities
----------------
* resolve basin + region kind (whole basin / river buffer) to an Earth Engine
  geometry built from the project's own vector data,
* keep the result cache keyed on every input that affects a product,
* decide between live Earth Engine processing, an explicit "unavailable" state
  and (only when explicitly enabled) labelled demonstration data,
* assemble the payload envelopes that the API and the dashboard consume.

The satellite layers are computed from the geometry in ``shapefiles/`` rather
than from uploaded Earth Engine assets, so basin statistics do not silently
depend on assets that may be missing from a project.
"""

from __future__ import annotations

import time
from typing import Any, Iterable, Optional

from . import demo, geometry, satellite, statistics, tiles as tiles_service, timeseries
from .cache import TTLCache, cache_key
from .config import BASINS, get_basin, settings
from .ee_client import ee_client
from .errors import DataStatus, EarthEngineUnavailableError, InvalidRequestError
from .indicators import CORE_INDICATORS, INDICATORS, get_indicator, normalise_indicator
from .logging_config import get_logger

logger = get_logger(__name__)

_cache = TTLCache(
    maxsize=settings.cache_max_items,
    ttl_seconds=settings.cache_ttl_seconds,
    name="products",
)

REGION_KINDS = ("basin", "buffer")


# ------------------------------------------------------------------ geometries


def ee_geometry(ee: Any, basin_id: str, region_kind: str = "basin") -> Any:
    """Earth Engine geometry for a basin or its river corridor (from shapefiles)."""

    shapely_geometry = geometry.basin_geometry_4326(basin_id, region_kind)
    return ee.Geometry(shapely_geometry.__geo_interface__)


def region_area_km2(basin_id: str, region_kind: str = "basin") -> float:
    basin = get_basin(basin_id)
    frame = geometry.read_layer(geometry.basin_source(basin, region_kind))
    return round(geometry.area_km2(frame), 1)


def region_label(basin_id: str, region_kind: str) -> str:
    basin = get_basin(basin_id)
    return f"{basin.display_name} — {'river corridor buffer' if region_kind == 'buffer' else 'whole basin'}"


# --------------------------------------------------------------------- helpers


def normalise_window(start: Optional[str], end: Optional[str]) -> dict[str, str]:
    """Validate a start/end pair and fill defaults (rolling window ending today)."""

    today = time.strftime("%Y-%m-%d", time.gmtime())
    if not end:
        end = today
    if not start:
        start = time.strftime(
            "%Y-%m-%d",
            time.gmtime(time.time() - settings.default_window_days * 86_400),
        )
    for label, value in (("start", start), ("end", end)):
        try:
            time.strptime(value, "%Y-%m-%d")
        except ValueError as exc:
            raise InvalidRequestError(
                f"'{label}' must be an ISO date (YYYY-MM-DD), got '{value}'"
            ) from exc
    if start >= end:
        raise InvalidRequestError("'start' must be earlier than 'end'")
    return {"start": start, "end": end}


def resolve_indicators(raw: Iterable[str] | str | None) -> list[str]:
    if raw is None:
        return list(CORE_INDICATORS)
    items = [raw] if isinstance(raw, str) else list(raw)
    resolved: list[str] = []
    for item in items:
        indicator = normalise_indicator(item)
        if indicator is None:
            raise InvalidRequestError(
                f"Unknown indicator '{item}'",
                detail={"available": sorted(INDICATORS)},
            )
        if indicator not in resolved:
            resolved.append(indicator)
    return resolved


# ------------------------------------------------------------------- products


def basin_statistics(
    basin_id: str,
    indicator_ids: Iterable[str],
    window: dict[str, str],
    region_kind: str = "basin",
    use_cache: bool = True,
) -> dict[str, Any]:
    """Zonal statistics for one basin / region and window."""

    if region_kind not in REGION_KINDS:
        raise InvalidRequestError(f"region must be one of {list(REGION_KINDS)}")
    indicators = list(indicator_ids)
    key = cache_key(
        "stats",
        basin_id,
        region_kind,
        ",".join(sorted(indicators)),
        window["start"],
        window["end"],
        settings.max_cloud_cover_percent,
        satellite.PROCESSING_VERSION,
    )
    if use_cache:
        cached = _cache.get(key)
        if cached is not None:
            return cached

    started = time.time()
    ee = ee_client.module()  # raises EarthEngineUnavailableError when unusable
    area = region_area_km2(basin_id, region_kind)
    ee_region = ee_geometry(ee, basin_id, region_kind)
    results = statistics.compute_zonal_statistics(
        ee,
        indicators=indicators,
        geometry=ee_region,
        area_km2=area,
        start=window["start"],
        end=window["end"],
        region_label=region_label(basin_id, region_kind),
    )
    payload = {
        "basin_id": basin_id,
        "basin_name": get_basin(basin_id).display_name,
        "region": region_kind,
        "region_label": region_label(basin_id, region_kind),
        "region_area_km2": area,
        "window": window,
        "indicators": {name: result.to_dict() for name, result in results.items()},
        "availability": statistics.summarise(results),
        "earth_engine": ee_client.describe(),
        "data_status": _overall_status(results),
        "processing_seconds": round(time.time() - started, 2),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _cache.set(key, payload)
    return payload


def demo_statistics(basin_id: str, indicator_ids: Iterable[str], window: dict[str, str], region_kind: str) -> dict[str, Any]:
    payload = demo.demo_basin_statistics(
        basin_id, list(indicator_ids), window, region_area_km2(basin_id, region_kind), region_label(basin_id, region_kind)
    )
    payload.update(
        {
            "basin_id": basin_id,
            "basin_name": get_basin(basin_id).display_name,
            "region": region_kind,
            "region_label": region_label(basin_id, region_kind),
            "window": window,
            "earth_engine": ee_client.describe(),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    return payload


def time_series(
    basin_id: str,
    indicator_id: str,
    start_year: int,
    end_year: int,
    aggregation: str = "year",
    region_kind: str = "basin",
    use_cache: bool = True,
) -> dict[str, Any]:
    if aggregation not in {"year", "month"}:
        raise InvalidRequestError("aggregation must be 'year' or 'month'")
    if not 2000 <= start_year <= 2100 or not 2000 <= end_year <= 2100:
        raise InvalidRequestError("years must be between 2000 and 2100")
    if start_year > end_year:
        raise InvalidRequestError("start_year must not be later than end_year")
    if end_year - start_year > 30:
        raise InvalidRequestError("requested range is wider than 30 years")

    key = cache_key(
        "series",
        basin_id,
        region_kind,
        indicator_id,
        aggregation,
        start_year,
        end_year,
        settings.max_cloud_cover_percent,
        satellite.PROCESSING_VERSION,
    )
    if use_cache:
        cached = _cache.get(key)
        if cached is not None:
            return cached

    ee = ee_client.module()
    ee_region = ee_geometry(ee, basin_id, region_kind)
    payload = timeseries.compute_time_series(
        ee,
        indicator_id=indicator_id,
        geometry=ee_region,
        start_year=start_year,
        end_year=end_year,
        aggregation=aggregation,
    )
    payload.update(
        {
            "basin_id": basin_id,
            "basin_name": get_basin(basin_id).display_name,
            "region": region_kind,
            "region_label": region_label(basin_id, region_kind),
            "earth_engine": ee_client.describe(),
        }
    )
    _cache.set(key, payload)
    return payload


def tile_layer(
    basin_id: str,
    indicator_id: str,
    start: str,
    end: str,
    region_kind: str = "basin",
) -> dict[str, Any]:
    key = cache_key("tile", basin_id, region_kind, indicator_id, start, end, satellite.PROCESSING_VERSION)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    ee = ee_client.module()
    ee_region = ee_geometry(ee, basin_id, region_kind)
    payload = tiles_service.build_tile(
        ee,
        indicator_id=indicator_id,
        geometry=ee_region,
        start=start,
        end=end,
        clip=ee_region,
        region_label=region_label(basin_id, region_kind),
    )
    payload.update(
        {
            "basin_id": basin_id,
            "basin_name": get_basin(basin_id).display_name,
            "region": region_kind,
            "region_label": region_label(basin_id, region_kind),
        }
    )
    _cache.set(key, payload)
    return payload


def change_detection(
    basin_id: str,
    indicator_id: str,
    baseline: dict[str, str],
    comparison: dict[str, str],
    region_kind: str = "basin",
    include_map: bool = True,
) -> dict[str, Any]:
    key = cache_key(
        "change",
        basin_id,
        region_kind,
        indicator_id,
        baseline["start"],
        baseline["end"],
        comparison["start"],
        comparison["end"],
        include_map,
        satellite.PROCESSING_VERSION,
    )
    cached = _cache.get(key)
    if cached is not None:
        return cached
    ee = ee_client.module()
    ee_region = ee_geometry(ee, basin_id, region_kind)
    area = region_area_km2(basin_id, region_kind)
    stats = tiles_service.difference_statistics(
        ee,
        indicator_id=indicator_id,
        geometry=ee_region,
        baseline=(baseline["start"], baseline["end"]),
        comparison=(comparison["start"], comparison["end"]),
        area_km2=area,
        region_label=region_label(basin_id, region_kind),
    )
    payload: dict[str, Any] = {
        "basin_id": basin_id,
        "basin_name": get_basin(basin_id).display_name,
        "region": region_kind,
        "region_label": region_label(basin_id, region_kind),
        "statistics": stats,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if include_map:
        payload["map"] = tiles_service.build_difference_tile(
            ee,
            indicator_id=indicator_id,
            geometry=ee_region,
            baseline=(baseline["start"], baseline["end"]),
            comparison=(comparison["start"], comparison["end"]),
            clip=ee_region,
            region_label=region_label(basin_id, region_kind),
        )
    _cache.set(key, payload)
    return payload


def overview(window: dict[str, str]) -> dict[str, Any]:
    """Executive overview: every basin, core indicators, real availability.

    Basin geometry, areas and extents always come from the project's vector
    data.  Satellite values are attached only when Earth Engine is reachable;
    otherwise every indicator reports ``unavailable`` (never a placeholder).
    """

    catalog = geometry.basins_catalog()
    ee_available = ee_client.is_available()
    indicator_ids = list(CORE_INDICATORS)
    basins: list[dict[str, Any]] = []
    latest: list[str] = []

    for record in catalog:
        entry: dict[str, Any] = {
            "basin_id": record["id"],
            "basin_name": record["name"],
            "area_km2": record["area_km2"],
            "buffer_area_km2": record["buffer_area_km2"],
            "buffer_distance_m": record["buffer_distance_m"],
            "centroid": record["centroid"],
            "warnings": record["warnings"],
        }
        if ee_available:
            try:
                stats = basin_statistics(record["id"], indicator_ids, window, "basin")
                entry["indicators"] = stats["indicators"]
                entry["availability"] = stats["availability"]
                entry["data_status"] = stats["data_status"]
                if stats["availability"].get("latest_observation"):
                    latest.append(stats["availability"]["latest_observation"])
            except Exception as exc:  # noqa: BLE001 - one basin must not break the overview
                logger.exception("overview statistics failed for %s", record["id"])
                entry["data_status"] = DataStatus.UNAVAILABLE.value
                entry["error"] = str(exc)
                entry["indicators"] = {}
        else:
            entry["data_status"] = DataStatus.UNAVAILABLE.value
            entry["indicators"] = {}
            entry["note"] = "Earth Engine is not connected; satellite indicators are unavailable."
        basins.append(entry)

    return {
        "window": window,
        "basin_count": len(basins),
        "indicators": [get_indicator(name).to_public_dict() for name in indicator_ids],
        "basins": basins,
        "earth_engine_available": ee_available,
        "earth_engine": ee_client.describe(),
        "data_status": (
            DataStatus.UNAVAILABLE.value if not ee_available else DataStatus.CURRENT.value
        ),
        "latest_observation": max(latest) if latest else None,
        "dashboard_refresh": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "freshness_policy": {
            "fresh_days": settings.fresh_days,
            "stale_days": settings.stale_days,
            "default_window_days": settings.default_window_days,
        },
        "scope_note": (
            "Near-real-time satellite monitoring of vegetation and surface-water indicators. "
            "Sentinel-2 revisit time, cloud cover and processing delays mean this is not "
            "continuous river-level or discharge monitoring."
        ),
    }


def freshness(basin_id: Optional[str] = None, window: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """Data-freshness report: observation date vs processing date vs refresh time."""

    resolved_window = window or normalise_window(None, None)
    payload: dict[str, Any] = {
        "window": resolved_window,
        "earth_engine": ee_client.describe(),
        "dashboard_refresh": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "freshness_policy": {
            "fresh_days": settings.fresh_days,
            "stale_days": settings.stale_days,
        },
        "basins": {},
    }
    if not ee_client.is_available():
        payload["data_status"] = DataStatus.UNAVAILABLE.value
        return payload
    for basin in BASINS if basin_id is None else [get_basin(basin_id)]:
        try:
            stats = basin_statistics(basin.id, list(CORE_INDICATORS), resolved_window, "basin")
            payload["basins"][basin.id] = {
                "data_status": stats["data_status"],
                "latest_observation": stats["availability"].get("latest_observation"),
                "youngest_observation_age_days": stats["availability"].get("youngest_observation_age_days"),
                "processing_timestamp": stats["generated_at"],
            }
        except Exception as exc:  # noqa: BLE001
            payload["basins"][basin.id] = {"data_status": DataStatus.UNAVAILABLE.value, "error": str(exc)}
    return payload


def cache_report() -> dict[str, Any]:
    return {"products": _cache.stats(), "geometry": geometry._memory_cache.stats()}


def clear_caches() -> dict[str, Any]:
    removed = _cache.clear()
    geometry.clear_geometry_cache()
    return {"cleared_product_entries": removed, "cleared_geometry_cache": True}


def _overall_status(results: dict[str, statistics.ZonalResult]) -> str:
    statuses = {result.data_status for result in results.values()}
    for status in (
        DataStatus.CURRENT,
        DataStatus.DELAYED,
        DataStatus.STALE,
        DataStatus.INSUFFICIENT_OBSERVATIONS,
        DataStatus.UNAVAILABLE,
    ):
        if status.value in statuses:
            return status.value
    return DataStatus.UNAVAILABLE.value


__all__ = [
    "basin_statistics",
    "cache_report",
    "change_detection",
    "clear_caches",
    "demo_statistics",
    "ee_geometry",
    "freshness",
    "normalise_window",
    "overview",
    "region_area_km2",
    "resolve_indicators",
    "tile_layer",
    "time_series",
]
