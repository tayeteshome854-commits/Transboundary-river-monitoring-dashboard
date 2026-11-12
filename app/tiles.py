"""Map products: Earth Engine tile URLs, visualisation parameters, differences.

A tile request returns the tile URL *and* the provenance of the image behind it
(dataset, window, observation count, latest acquisition, mask method), plus a
``data_status``.  When no usable observation exists in the window the request
fails with an explicit error instead of rendering an empty or all-zero layer.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from . import satellite
from .config import settings
from .errors import DataStatus, InsufficientObservationsError, classify_freshness
from .indicators import Indicator, get_indicator, palette_to_vis
from .logging_config import get_logger
from .statistics import MAX_PIXELS, _age_days, _iso_from_millis

logger = get_logger(__name__)

#: Difference layers use a diverging palette centred on zero.
DIFFERENCE_PALETTE = ("#a50026", "#d73027", "#f46d43", "#fdae61", "#fee090", "#ffffbf", "#e0f3f8", "#abd9e9", "#74add1", "#4575b4", "#313695")


def _observation_summary(ee: Any, collection: Any) -> tuple[int, Optional[str], Optional[float]]:
    timestamps = collection.aggregate_array("system:time_start").getInfo() or []
    if not timestamps:
        return 0, None, None
    latest = _iso_from_millis(max(timestamps))
    return len(timestamps), latest, _age_days(latest)


def build_tile(
    ee: Any,
    *,
    indicator_id: str,
    geometry: Any,
    start: str,
    end: str,
    clip: Any,
    region_label: str = "basin",
) -> dict[str, Any]:
    """Tile layer for one indicator over one window, clipped to a region."""

    indicator = get_indicator(indicator_id)
    vis = palette_to_vis(indicator)

    if indicator.is_spectral_index:
        band = satellite.band_for_indicator(ee, indicator.id)
        collection = satellite.s2_collection(
            ee, start, end, geometry, settings.max_cloud_cover_percent
        )
        observation_count, latest, age = _observation_summary(ee, collection)
        if observation_count == 0:
            raise InsufficientObservationsError(
                f"No cloud-free {indicator.label} observation for the selected window",
                detail={"window": {"start": start, "end": end}, "region": region_label},
            )
        image = collection.median().select([band]).clip(clip)
        source = {
            "dataset": indicator.dataset,
            "bands": list(indicator.bands),
            "equation": indicator.equation,
            "scale_m": indicator.scale_m,
            "mask_method": satellite.mask_method_text(settings.max_cloud_cover_percent),
            "compositing": "per-pixel median of masked scenes",
        }
    else:
        collection = _ancillary_collection(ee, indicator, start, end, geometry)
        observation_count, latest, age = _observation_summary(ee, collection)
        if observation_count == 0:
            raise InsufficientObservationsError(
                f"No {indicator.label} observation for the selected window",
                detail={"window": {"start": start, "end": end}, "region": region_label},
            )
        image = satellite.ancillary_image(ee, indicator, start, end, geometry).clip(clip)
        source = {
            "dataset": indicator.dataset,
            "bands": list(indicator.bands),
            "equation": indicator.equation,
            "scale_m": indicator.scale_m,
            "compositing": "period aggregate",
        }

    map_id = image.visualize(**vis).getMapId() if _supports_visualize(image) else image.getMapId(vis)
    source.update(
        {
            "region": region_label,
            "processing_version": satellite.PROCESSING_VERSION,
            "output_crs": "Web Mercator tiles from Earth Engine",
        }
    )

    return {
        "tile_url": map_id["tile_fetcher"].url_format,
        "visualization": vis,
        "indicator": indicator.id,
        "label": indicator.label,
        "units": indicator.units,
        "window": {"start": start, "end": end},
        "observation_count": observation_count,
        "latest_observation": latest,
        "observation_age_days": age,
        "data_status": classify_freshness(age, settings.fresh_days, settings.stale_days).value,
        "caveat": indicator.caveat,
        "source": source,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _supports_visualize(image: Any) -> bool:
    return hasattr(image, "visualize")


def _ancillary_collection(ee: Any, indicator: Indicator, start: str, end: str, geometry: Any) -> Any:
    datasets = {
        "rainfall": ("UCSB-CHG/CHIRPS/DAILY", "precipitation"),
        "lst": ("MODIS/061/MOD11A2", "LST_Day_1km"),
        "air_temp": ("ECMWF/ERA5_LAND/DAILY_AGGR", "temperature_2m"),
        "et": ("MODIS/061/MOD16A2GF", "ET"),
        "soil_moisture": ("NASA/SMAP/SPL4SMGP/007", "sm_surface"),
    }
    dataset, band = datasets[indicator.id]
    return ee.ImageCollection(dataset).filterDate(start, end).filterBounds(geometry).select(band)


def build_difference_tile(
    ee: Any,
    *,
    indicator_id: str,
    geometry: Any,
    baseline: tuple[str, str],
    comparison: tuple[str, str],
    clip: Any,
    region_label: str = "basin",
) -> dict[str, Any]:
    """Difference map between two periods of the same indicator.

    Comparability rules enforced here:
      * same indicator, same region, same processing version and mask,
      * both periods must contain at least one usable observation,
      * percentage change is only reported where the baseline is far enough from
        zero for a ratio to be meaningful (|baseline| >= 0.05 for indices).
    """

    indicator = get_indicator(indicator_id)
    vis = {
        "min": -0.4 if indicator.valid_range and indicator.valid_range[0] >= -1 else -10.0,
        "max": 0.4 if indicator.valid_range and indicator.valid_range[1] <= 1 else 10.0,
        "palette": list(DIFFERENCE_PALETTE),
    }
    if indicator.is_spectral_index:
        band = satellite.band_for_indicator(ee, indicator.id)
        base_collection = satellite.s2_collection(ee, baseline[0], baseline[1], geometry, settings.max_cloud_cover_percent)
        comp_collection = satellite.s2_collection(ee, comparison[0], comparison[1], geometry, settings.max_cloud_cover_percent)
        base_count, base_latest, _ = _observation_summary(ee, base_collection)
        comp_count, comp_latest, _ = _observation_summary(ee, comp_collection)
        if base_count == 0 or comp_count == 0:
            raise InsufficientObservationsError(
                "Both periods must contain at least one cloud-free observation to compute a difference",
                detail={
                    "baseline_observations": base_count,
                    "comparison_observations": comp_count,
                    "window_baseline": {"start": baseline[0], "end": baseline[1]},
                    "window_comparison": {"start": comparison[0], "end": comparison[1]},
                },
            )
        base_image = base_collection.median().select([band])
        comp_image = comp_collection.median().select([band])
    else:
        base_collection = _ancillary_collection(ee, indicator, baseline[0], baseline[1], geometry)
        comp_collection = _ancillary_collection(ee, indicator, comparison[0], comparison[1], geometry)
        base_count, base_latest, _ = _observation_summary(ee, base_collection)
        comp_count, comp_latest, _ = _observation_summary(ee, comp_collection)
        if base_count == 0 or comp_count == 0:
            raise InsufficientObservationsError(
                "Both periods must contain at least one observation to compute a difference",
                detail={"baseline_observations": base_count, "comparison_observations": comp_count},
            )
        base_image = satellite.ancillary_image(ee, indicator, baseline[0], baseline[1], geometry)
        comp_image = satellite.ancillary_image(ee, indicator, comparison[0], comparison[1], geometry)

    difference = comp_image.subtract(base_image).rename("difference").clip(clip)
    map_id = difference.visualize(**vis).getMapId() if _supports_visualize(difference) else difference.getMapId(vis)

    return {
        "tile_url": map_id["tile_fetcher"].url_format,
        "visualization": vis,
        "indicator": indicator.id,
        "label": f"{indicator.label} difference",
        "units": f"{indicator.units} (comparison minus baseline)",
        "baseline": {"start": baseline[0], "end": baseline[1], "observation_count": base_count, "latest_observation": base_latest},
        "comparison": {"start": comparison[0], "end": comparison[1], "observation_count": comp_count, "latest_observation": comp_latest},
        "definition": "comparison composite minus baseline composite, per pixel",
        "percentage_change_note": (
            "Percentage change is only meaningful where the baseline is far from zero; it is "
            "reported separately in the statistics response, not baked into this layer."
        ),
        "data_status": DataStatus.CURRENT.value,
        "source": {
            "dataset": indicator.dataset,
            "bands": list(indicator.bands),
            "equation": indicator.equation,
            "scale_m": indicator.scale_m,
            "mask_method": satellite.mask_method_text(settings.max_cloud_cover_percent),
            "compositing": "per-pixel median of masked scenes in each period",
            "region": region_label,
            "processing_version": satellite.PROCESSING_VERSION,
        },
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def difference_statistics(
    ee: Any,
    *,
    indicator_id: str,
    geometry: Any,
    baseline: tuple[str, str],
    comparison: tuple[str, str],
    area_km2: Optional[float],
    region_label: str = "basin",
) -> dict[str, Any]:
    """Numeric comparison of two periods, with coverage and comparability flags."""

    indicator = get_indicator(indicator_id)
    scale_m = indicator.scale_m
    reducer = satellite.zonal_reducer(ee)

    if indicator.is_spectral_index:
        band = satellite.band_for_indicator(ee, indicator.id)
        base_collection = satellite.s2_collection(ee, baseline[0], baseline[1], geometry, settings.max_cloud_cover_percent)
        comp_collection = satellite.s2_collection(ee, comparison[0], comparison[1], geometry, settings.max_cloud_cover_percent)
        base_image = base_collection.median().select([band])
        comp_image = comp_collection.median().select([band])
    else:
        base_image = satellite.ancillary_image(ee, indicator, baseline[0], baseline[1], geometry)
        comp_image = satellite.ancillary_image(ee, indicator, comparison[0], comparison[1], geometry)
        band = base_image.bandNames().getInfo()[0]

    base_count, base_latest, _ = _observation_summary(ee, _collection_for(ee, indicator, baseline, geometry))
    comp_count, comp_latest, comp_age = _observation_summary(ee, _collection_for(ee, indicator, comparison, geometry))

    def reduce(image: Any) -> dict[str, Any]:
        return (
            image.reduceRegion(
                reducer=reducer,
                geometry=geometry,
                scale=scale_m,
                bestEffort=True,
                maxPixels=MAX_PIXELS,
            ).getInfo()
            or {}
        )

    base_stats = reduce(base_image)
    comp_stats = reduce(comp_image)
    diff_stats = reduce(comp_image.subtract(base_image).rename("difference"))

    base_value = base_stats.get(f"{band}_mean")
    comp_value = comp_stats.get(f"{band}_mean")
    diff_value = diff_stats.get("difference_mean")
    pixels = comp_stats.get(f"{band}_count")
    base_pixels = base_stats.get(f"{band}_count")

    comparable = base_count > 0 and comp_count > 0 and base_value is not None and comp_value is not None
    percentage_change = None
    if comparable and abs(base_value) >= 0.05:
        percentage_change = round((comp_value - base_value) / abs(base_value) * 100.0, 2)

    from .statistics import expected_pixels

    exp = expected_pixels(area_km2, scale_m)
    return {
        "indicator": indicator.id,
        "label": indicator.label,
        "units": indicator.units,
        "region": region_label,
        "baseline": {
            "window": {"start": baseline[0], "end": baseline[1]},
            "value": round(float(base_value), 4) if base_value is not None else None,
            "observation_count": base_count,
            "valid_pixel_count": int(base_pixels) if base_pixels is not None else None,
            "latest_observation": base_latest,
        },
        "comparison": {
            "window": {"start": comparison[0], "end": comparison[1]},
            "value": round(float(comp_value), 4) if comp_value is not None else None,
            "observation_count": comp_count,
            "valid_pixel_count": int(pixels) if pixels is not None else None,
            "latest_observation": comp_latest,
            "observation_age_days": comp_age,
        },
        "difference": round(float(diff_value), 4) if diff_value is not None else None,
        "difference_units": f"{indicator.units} (comparison minus baseline)",
        "percentage_change": percentage_change,
        "percentage_change_basis": (
            "not computed — baseline mean is too close to zero for a stable ratio"
            if comparable and percentage_change is None
            else "((comparison - baseline) / |baseline|) * 100"
        ),
        "comparable": bool(comparable),
        "comparability_checks": {
            "same_indicator": True,
            "same_region": True,
            "same_processing_version": True,
            "same_scale_m": scale_m,
            "baseline_has_observations": base_count > 0,
            "comparison_has_observations": comp_count > 0,
            "coverage_fraction": (
                round(min(int(pixels) / exp, 1.0), 4) if (pixels is not None and exp) else None
            ),
        },
        "data_status": (
            classify_freshness(comp_age, settings.fresh_days, settings.stale_days).value
            if comparable
            else DataStatus.INSUFFICIENT_OBSERVATIONS.value
        ),
        "source": {
            "dataset": indicator.dataset,
            "bands": list(indicator.bands),
            "equation": indicator.equation,
            "scale_m": scale_m,
            "mask_method": satellite.mask_method_text(settings.max_cloud_cover_percent),
            "processing_version": satellite.PROCESSING_VERSION,
        },
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _collection_for(ee: Any, indicator: Indicator, window: tuple[str, str], geometry: Any) -> Any:
    if indicator.is_spectral_index:
        return satellite.s2_collection(ee, window[0], window[1], geometry, settings.max_cloud_cover_percent)
    return _ancillary_collection(ee, indicator, window[0], window[1], geometry)


__all__ = ["build_difference_tile", "build_tile", "difference_statistics"]
