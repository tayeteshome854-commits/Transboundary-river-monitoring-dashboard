"""FastAPI application and HTTP routes.

Conventions
-----------
* every route is ``async`` and delegates blocking Earth Engine work to a worker
  thread via :func:`starlette.concurrency.run_in_threadpool`,
* every product response carries ``data_status`` and provenance,
* errors use one envelope: ``{"error": {code, message, detail, timestamp}}``,
* no route ever fabricates a value: when Earth Engine is unreachable the
  response says so (HTTP 503) unless demonstration mode is explicitly enabled,
  in which case the payload is labelled ``demonstration``.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import __version__, exports, geometry, service
from .config import BASINS, STATIC_DIR, resolve_basin_id, settings
from .ee_client import ee_client
from .errors import (
    STATUS_LABELS,
    DashboardError,
    EarthEngineUnavailableError,
    InvalidRequestError,
    UnknownBasinError,
    UnknownIndicatorError,
)
from . import demo
from .hydrology import integration_contract, require_provider
from .indicators import INDICATORS, normalise_indicator
from .logging_config import configure_logging, get_logger
from .schemas import ChangeRequest, ErrorBody, ErrorResponse

configure_logging(settings.log_level)
logger = get_logger(__name__)

app = FastAPI(
    title="Ethiopian Transboundary River Monitoring Dashboard API",
    version=__version__,
    description=(
        "Near-real-time satellite monitoring of Ethiopian transboundary river basins "
        "(vegetation and surface-water indicators), with clearly separated hydrological "
        "monitoring integration. See docs/SCIENTIFIC_METHODS.md for methods and limitations."
    ),
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

# --------------------------------------------------------------------- CORS
_origins = list(settings.cors_origins)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    # Credentials cannot be combined with a wildcard origin; that combination is
    # rejected by browsers and is a security smell.
    allow_credentials="*" not in _origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ------------------------------------------------------------- error handling
@app.exception_handler(DashboardError)
async def dashboard_error_handler(request: Request, exc: DashboardError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error=ErrorBody(code=exc.code, message=exc.message, detail=exc.detail)
        ).model_dump(),
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=ErrorResponse(
            error=ErrorBody(
                code="invalid_request",
                message="Request validation failed",
                detail={"errors": exc.errors()},
            )
        ).model_dump(),
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error=ErrorBody(
                code="internal_error",
                message="Unexpected server error",
                detail={"path": request.url.path},
            )
        ).model_dump(),
    )


# ------------------------------------------------------------------- helpers
async def resolve_basin(basin_id: str) -> str:
    """Resolve and validate a basin identifier from the path."""

    resolved = resolve_basin_id(basin_id)
    if resolved is None:
        raise UnknownBasinError(
            f"Unknown basin '{basin_id}'",
            detail={"available": [basin.id for basin in BASINS]},
        )
    return resolved


def _csv_response(content: str, filename: str) -> PlainTextResponse:
    return PlainTextResponse(
        content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _stats_or_demo(basin_id: str, indicators: list[str], window: dict[str, str], region: str) -> dict[str, Any]:
    """Basin statistics, falling back to labelled demonstration data only when enabled."""

    try:
        return await run_in_threadpool(service.basin_statistics, basin_id, indicators, window, region)
    except EarthEngineUnavailableError:
        if demo.demo_active():
            logger.warning("serving labelled demonstration data for %s", basin_id)
            return service.demo_statistics(basin_id, indicators, window, region)
        raise


# ------------------------------------------------------------------- frontend
@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
async def status() -> dict[str, Any]:
    """Backwards-compatible status endpoint (kept for existing integrations)."""

    return {
        "status": "running",
        "message": "Ethiopian Transboundary River Monitoring Dashboard API",
        "version": __version__,
        "earth_engine_available": ee_client.is_available(),
    }


@app.get("/api/health")
async def health() -> dict[str, Any]:
    """Application health: does the service itself work?"""

    return {
        "status": "ok",
        "version": __version__,
        "environment": settings.environment,
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


@app.get("/api/readiness")
async def readiness() -> JSONResponse:
    """Readiness: is the Earth-observation data source reachable?

    Reported separately from application health because the dashboard can be
    healthy while its satellite backend is not.
    """

    available = await run_in_threadpool(ee_client.is_available)
    vector_ok = all(basin.basin_path.exists() and basin.buffer_path.exists() for basin in BASINS)
    body = {
        "status": "ready" if (available and vector_ok) else "degraded",
        "application": {"healthy": True, "version": __version__},
        "earth_engine": await run_in_threadpool(ee_client.describe),
        "vector_data": {
            "available": vector_ok,
            "basin_count": len(BASINS),
            "shapefile_directory": str(geometry.SHAPEFILE_DIR),
        },
        "hydrology": integration_contract(),
        "demonstration_mode": {
            "enabled": bool(settings.allow_demo_mode),
            "active": bool(settings.allow_demo_mode) and not available,
            "warning": "Demonstration payloads are synthetic and clearly labelled.",
        },
        "cache": service.cache_report(),
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    return JSONResponse(status_code=200 if body["status"] == "ready" else 503, content=body)


@app.get("/api/config")
async def public_config() -> dict[str, Any]:
    """Everything the frontend needs to render controls and legends."""

    return {
        "version": __version__,
        "basins": [
            {
                "id": basin.id,
                "name": basin.display_name,
                "river_name": basin.river_name,
                "regions": [
                    {"id": "basin", "label": "Whole basin"},
                    {"id": "buffer", "label": f"River corridor buffer ({int(basin.buffer_distance_m)} m)"},
                ],
                "notes": basin.notes,
            }
            for basin in BASINS
        ],
        "indicators": [indicator.to_public_dict() for indicator in INDICATORS.values()],
        "freshness_policy": {
            "fresh_days": settings.fresh_days,
            "stale_days": settings.stale_days,
            "default_window_days": settings.default_window_days,
            "status_labels": {key.value: label for key, label in STATUS_LABELS.items()},
        },
        "satellite_scope": (
            "Near-real-time satellite monitoring (Sentinel-2 revisit plus cloud and "
            "processing delays). Not continuous river-level or discharge monitoring."
        ),
        "demonstration_mode": bool(settings.allow_demo_mode),
        "earth_engine": await run_in_threadpool(ee_client.describe),
    }


# ------------------------------------------------------------- vector layers
@app.get("/api/basins")
async def basins() -> dict[str, Any]:
    catalog = await run_in_threadpool(geometry.basins_catalog)
    return {
        "basins": catalog,
        "area_crs": geometry.AREA_CRS_NAME,
        "output_crs": "EPSG:4326",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


@app.get("/api/basin/{basin_id}/geojson")
async def basin_geojson(
    basin_id: str,
    region: str = Query("basin", pattern="^(basin|buffer)$"),
) -> dict[str, Any]:
    resolved = await resolve_basin(basin_id)
    return await run_in_threadpool(geometry.basin_geojson, resolved, region)


@app.get("/api/buffer/{basin_id}/geojson")
async def buffer_geojson(basin_id: str) -> dict[str, Any]:
    """Legacy route kept for compatibility — the river corridor layer."""

    resolved = await resolve_basin(basin_id)
    return await run_in_threadpool(geometry.basin_geojson, resolved, "buffer")


@app.get("/api/green-legacy/geojson")
async def green_legacy_geojson(
    basin: Optional[str] = Query(None),
    mode: str = Query("points", pattern="^(points|polygons)$"),
    limit: int = Query(5000, ge=1, le=25000),
) -> dict[str, Any]:
    resolved = await resolve_basin(basin) if basin else None
    return await run_in_threadpool(geometry.green_legacy_geojson, resolved, mode, limit)


@app.get("/api/green-legacy/summary")
async def green_legacy_summary(basin: Optional[str] = Query(None)) -> dict[str, Any]:
    resolved = await resolve_basin(basin) if basin else None
    return await run_in_threadpool(geometry.green_legacy_summary, resolved)


@app.get("/api/green-legacy/by-wereda")
async def green_legacy_by_wereda() -> dict[str, Any]:
    return await run_in_threadpool(geometry.green_legacy_by_wereda)


@app.get("/api/data/inventory")
async def data_inventory() -> dict[str, Any]:
    inventory = await run_in_threadpool(geometry.data_inventory)
    return {"datasets": inventory, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


# ------------------------------------------------------------------ products
@app.get("/api/overview")
async def overview(start: Optional[str] = None, end: Optional[str] = None) -> dict[str, Any]:
    window = service.normalise_window(start, end)
    return await run_in_threadpool(service.overview, window)


@app.get("/api/freshness")
async def freshness(basin: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None) -> dict[str, Any]:
    resolved = await resolve_basin(basin) if basin else None
    window = service.normalise_window(start, end)
    return await run_in_threadpool(service.freshness, resolved, window)


@app.get("/api/basin/{basin_id}/stats")
async def basin_stats(
    basin_id: str,
    indicators: str = Query("ndvi,ndwi,mndwi", description="Comma separated indicator ids"),
    start: Optional[str] = None,
    end: Optional[str] = None,
    region: str = Query("basin", pattern="^(basin|buffer)$"),
    format: Optional[str] = Query(None, pattern="^(csv)$"),
) -> Any:
    resolved = await resolve_basin(basin_id)
    indicator_ids = service.resolve_indicators(indicators.split(","))
    window = service.normalise_window(start, end)
    payload = await _stats_or_demo(resolved, indicator_ids, window, region)
    if format == "csv":
        return _csv_response(
            exports.statistics_csv(payload),
            exports.csv_filename("statistics", resolved),
        )
    return payload


@app.get("/api/basin/{basin_id}/timeseries/{indicator}")
async def basin_timeseries(
    basin_id: str,
    indicator: str,
    start_year: int = Query(2017, ge=2000, le=2100),
    end_year: int = Query(2026, ge=2000, le=2100),
    aggregation: str = Query("year", pattern="^(year|month)$"),
    region: str = Query("basin", pattern="^(basin|buffer)$"),
    format: Optional[str] = Query(None, pattern="^(csv)$"),
) -> Any:
    resolved = await resolve_basin(basin_id)
    indicator_id = normalise_indicator(indicator)
    if indicator_id is None:
        raise UnknownIndicatorError(
            f"Unknown indicator '{indicator}'", detail={"available": sorted(INDICATORS)}
        )

    try:
        payload = await run_in_threadpool(
            service.time_series, resolved, indicator_id, start_year, end_year, aggregation, region
        )
    except EarthEngineUnavailableError:
        if demo.demo_active():
            payload = demo.demo_time_series(resolved, indicator_id, start_year, end_year, aggregation)
            payload.update(
                {
                    "basin_id": resolved,
                    "window": {"start": f"{start_year}-01-01", "end": f"{end_year}-12-31"},
                }
            )
        else:
            raise

    if format == "csv":
        return _csv_response(
            exports.timeseries_csv(payload),
            exports.csv_filename("timeseries", resolved, indicator_id),
        )
    return payload


@app.get("/api/tiles/{basin_id}/{indicator}")
async def tile_layer(
    basin_id: str,
    indicator: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    region: str = Query("basin", pattern="^(basin|buffer)$"),
) -> dict[str, Any]:
    """Map layer for one indicator and window.

    Raster products are never synthesised: if Earth Engine is unavailable this
    route returns HTTP 503 with the reason.
    """

    resolved = await resolve_basin(basin_id)
    indicator_id = normalise_indicator(indicator)
    if indicator_id is None:
        raise UnknownIndicatorError(
            f"Unknown indicator '{indicator}'", detail={"available": sorted(INDICATORS)}
        )
    window = service.normalise_window(start, end)
    return await run_in_threadpool(
        service.tile_layer, resolved, indicator_id, window["start"], window["end"], region
    )


@app.get("/api/tiles/{basin_id}/{indicator}/{year}")
async def tile_layer_legacy(basin_id: str, indicator: str, year: int) -> dict[str, Any]:
    """Legacy ``/year`` route kept for compatibility (calendar-year window)."""

    return await tile_layer(basin_id, indicator, f"{year}-01-01", f"{year}-12-31", "basin")


@app.post("/api/basin/{basin_id}/change")
async def change_detection(basin_id: str, request: ChangeRequest) -> dict[str, Any]:
    resolved = await resolve_basin(basin_id)
    baseline = {"start": request.baseline_start, "end": request.baseline_end}
    comparison = {"start": request.comparison_start, "end": request.comparison_end}
    for label, window in (("baseline", baseline), ("comparison", comparison)):
        if window["start"] >= window["end"]:
            raise InvalidRequestError(f"{label}_start must be earlier than {label}_end")
    return await run_in_threadpool(
        service.change_detection,
        resolved,
        request.indicator,
        baseline,
        comparison,
        request.region,
        request.include_map,
    )


# ------------------------------------------------------------------- exports
@app.post("/api/basin/{basin_id}/change.csv")
async def change_csv(basin_id: str, request: ChangeRequest) -> PlainTextResponse:
    payload = await change_detection(basin_id, request)
    return _csv_response(
        exports.change_csv(payload),
        exports.csv_filename("change", payload["basin_id"], request.indicator),
    )


@app.get("/report", include_in_schema=False)
async def report(
    basin: str = Query("abay"),
    indicator: str = Query("ndvi"),
    start: Optional[str] = None,
    end: Optional[str] = None,
    start_year: int = Query(2017, ge=2000, le=2100),
    end_year: int = Query(2026, ge=2000, le=2100),
) -> HTMLResponse:
    """Printable analytical summary (use the browser's "Save as PDF")."""

    from .report import build_report

    resolved = await resolve_basin(basin)
    indicator_id = normalise_indicator(indicator) or "ndvi"
    window = service.normalise_window(start, end)
    html = await run_in_threadpool(build_report, resolved, indicator_id, window, start_year, end_year)
    return HTMLResponse(html)


# ----------------------------------------------------------------- hydrology
@app.get("/api/hydrology/integration")
async def hydrology_integration() -> dict[str, Any]:
    return integration_contract()


@app.get("/api/hydrology/stations")
async def hydrology_stations() -> dict[str, Any]:
    provider = require_provider()
    stations = await run_in_threadpool(provider.stations)
    return {
        "provider": provider.name,
        "stations": [station.__dict__ for station in stations],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


@app.get("/api/hydrology/stations/{station_id}/observations")
async def hydrology_observations(station_id: str, start: str, end: str) -> dict[str, Any]:
    provider = require_provider()
    readings = await run_in_threadpool(provider.observations, station_id, start, end)
    return {
        "provider": provider.name,
        "station_id": station_id,
        "window": {"start": start, "end": end},
        "readings": [reading.__dict__ for reading in readings],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


# -------------------------------------------------------------- cache admin
@app.get("/api/cache")
async def cache_stats() -> dict[str, Any]:
    return service.cache_report()


@app.post("/api/cache/clear")
async def cache_clear() -> dict[str, Any]:
    return service.clear_caches()


# --------------------------------------------------------------- static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


__all__ = ["app"]
