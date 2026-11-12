"""Time series: annual and monthly aggregates for one indicator and region.

Performance note
----------------
The previous implementation issued one blocking ``getInfo()`` request per year
(9-11 sequential round trips for a single chart).  Here every year is reduced
server-side and the whole series is returned by **one** ``getInfo()`` call, so a
chart costs one request instead of ten.

Honesty note
------------
Years with no usable observation are returned with ``value: null`` and a
``data_status`` of ``insufficient_observations``.  Gaps stay gaps: nothing is
interpolated or carried forward.
"""

from __future__ import annotations

import time
from typing import Any, Iterable, Optional

from . import satellite
from .config import settings
from .errors import DataStatus
from .indicators import Indicator, get_indicator
from .logging_config import get_logger
from .statistics import MAX_PIXELS, _iso_from_millis

logger = get_logger(__name__)


def _periods(start_year: int, end_year: int, aggregation: str) -> list[dict[str, Any]]:
    if aggregation == "month":
        periods = []
        for year in range(start_year, end_year + 1):
            for month in range(1, 13):
                next_month = (month % 12) + 1
                next_year = year + 1 if month == 12 else year
                periods.append(
                    {
                        "label": f"{year}-{month:02d}",
                        "start": f"{year}-{month:02d}-01",
                        "end": f"{next_year}-{next_month:02d}-01",
                        "year": year,
                        "month": month,
                    }
                )
        return periods
    return [
        {
            "label": str(year),
            "start": f"{year}-01-01",
            "end": f"{year + 1}-01-01",
            "year": year,
            "month": None,
        }
        for year in range(start_year, end_year + 1)
    ]


def _empty_period(period: dict[str, Any], indicator: Indicator, note: str) -> dict[str, Any]:
    return {
        "period": period["label"],
        "year": period["year"],
        "month": period["month"],
        "window": {"start": period["start"], "end": period["end"]},
        "value": None,
        "std_dev": None,
        "observation_count": 0,
        "valid_pixel_count": 0,
        "data_status": DataStatus.INSUFFICIENT_OBSERVATIONS.value,
        "note": note,
    }


def compute_time_series(
    ee: Any,
    *,
    indicator_id: str,
    geometry: Any,
    start_year: int,
    end_year: int,
    aggregation: str = "year",
    min_observations: int = 1,
) -> dict[str, Any]:
    """One series for one indicator over one region (single round trip)."""

    indicator = get_indicator(indicator_id)
    periods = _periods(start_year, end_year, aggregation)
    started = time.time()

    if indicator.is_spectral_index:
        band = satellite.band_for_indicator(ee, indicator.id)
        output_band = band
        base = satellite.s2_collection(
            ee,
            periods[0]["start"],
            periods[-1]["end"],
            geometry,
            settings.max_cloud_cover_percent,
        )
        features = []
        for period in periods:
            subset = base.filterDate(period["start"], period["end"])
            stats = _guarded_reduction(
                ee,
                subset=subset,
                image=lambda: subset.median().select([band]),
                geometry=geometry,
                scale=indicator.scale_m,
            )
            features.append(
                ee.Feature(None, stats)
                .set("period", period["label"])
                .set("observation_count", subset.size())
                .set("latest_scene", subset.aggregate_array("system:time_start").reduce(ee.Reducer.max()))
            )
    else:
        dataset_band = {
            "rainfall": ("UCSB-CHG/CHIRPS/DAILY", "precipitation", "sum"),
            "lst": ("MODIS/061/MOD11A2", "LST_Day_1km", "mean"),
            "air_temp": ("ECMWF/ERA5_LAND/DAILY_AGGR", "temperature_2m", "mean"),
            "et": ("MODIS/061/MOD16A2GF", "ET", "mean"),
            "soil_moisture": ("NASA/SMAP/SPL4SMGP/007", "sm_surface", "mean"),
        }[indicator.id]
        dataset, band, method = dataset_band
        # _apply_scaling renames the band, so the reduction keys use this name.
        output_band = {
            "rainfall": "RAINFALL",
            "lst": "LST",
            "air_temp": "AIR_TEMP",
            "et": "ET",
            "soil_moisture": "SOIL_MOISTURE",
        }[indicator.id]
        base = (
            ee.ImageCollection(dataset)
            .filterDate(periods[0]["start"], periods[-1]["end"])
            .filterBounds(geometry)
            .select(band)
        )
        features = []
        for period in periods:
            subset = base.filterDate(period["start"], period["end"])
            stats = _guarded_reduction(
                ee,
                subset=subset,
                image=lambda subset=subset, method=method: _apply_scaling(
                    ee, subset.sum() if method == "sum" else subset.mean(), indicator.id
                ),
                geometry=geometry,
                scale=indicator.scale_m,
            )
            features.append(
                ee.Feature(None, stats)
                .set("period", period["label"])
                .set("observation_count", subset.size())
                .set("latest_scene", subset.aggregate_array("system:time_start").reduce(ee.Reducer.max()))
            )

    raw = ee.FeatureCollection(features).getInfo() or {}
    points = _parse_series(raw, periods, output_band, indicator, min_observations)

    return {
        "indicator": indicator.id,
        "label": indicator.label,
        "units": indicator.units,
        "aggregation": aggregation,
        "series": points,
        "window": {"start": periods[0]["start"], "end": periods[-1]["end"]},
        "source": {
            "dataset": indicator.dataset,
            "bands": list(indicator.bands),
            "equation": indicator.equation,
            "scale_m": indicator.scale_m,
            "mask_method": satellite.mask_method_text(settings.max_cloud_cover_percent),
            "compositing": "per-pixel median of masked scenes" if indicator.is_spectral_index else f"per-pixel {method}",
            "processing_version": satellite.PROCESSING_VERSION,
            "aggregation": f"{'monthly' if aggregation == 'month' else 'annual'} mean over the region",
        },
        "caveat": indicator.caveat,
        "gaps": [point["period"] for point in points if point["value"] is None],
        "interpolation": "none — gaps are reported as missing observations",
        "processing_seconds": round(time.time() - started, 2),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _guarded_reduction(
    ee: Any,
    *,
    subset: Any,
    image: Any,
    geometry: Any,
    scale: int,
) -> Any:
    """Reduce one period, tolerating periods with no usable observation.

    ``ee.Algorithms.If`` keeps the reduction inside a server-side branch, so an
    empty period yields an empty property dictionary instead of aborting the
    whole multi-year request (reducing an empty collection has no bands to
    reduce).  The gap is then reported as a missing observation.
    """

    reduction = image().reduceRegion(
        reducer=satellite.zonal_reducer(ee),
        geometry=geometry,
        scale=scale,
        bestEffort=True,
        maxPixels=MAX_PIXELS,
    )
    return ee.Dictionary(
        ee.Algorithms.If(
            subset.size().gt(0),
            reduction,
            ee.Dictionary({}),
        )
    )


def _apply_scaling(ee: Any, image: Any, indicator_id: str) -> Any:
    if indicator_id == "lst":
        return image.multiply(0.02).subtract(273.15).rename("LST")
    if indicator_id == "air_temp":
        return image.subtract(273.15).rename("AIR_TEMP")
    if indicator_id == "et":
        return image.multiply(0.1).rename("ET")
    if indicator_id == "rainfall":
        return image.rename("RAINFALL")
    return image.rename("SOIL_MOISTURE")


def _parse_series(
    raw: dict[str, Any],
    periods: list[dict[str, Any]],
    band: str,
    indicator: Indicator,
    min_observations: int,
) -> list[dict[str, Any]]:
    """Turn the server response into ordered points, preserving gaps."""

    by_period: dict[str, dict[str, Any]] = {}
    for feature in raw.get("features", []) or []:
        props = feature.get("properties", {}) or {}
        label = str(props.get("period"))
        by_period[label] = props

    points: list[dict[str, Any]] = []
    for period in periods:
        props = by_period.get(period["label"])
        if props is None:
            points.append(_empty_period(period, indicator, "period missing from server response"))
            continue
        observation_count = int(props.get("observation_count") or 0)
        value = props.get(f"{band}_mean", props.get(band))
        std_dev = props.get(f"{band}_stdDev")
        count = props.get(f"{band}_count")
        latest = _iso_from_millis(props.get("latest_scene"))
        if value is None or observation_count < min_observations:
            points.append(
                {
                    **_empty_period(period, indicator, "no usable observation"),
                    "observation_count": observation_count,
                    "valid_pixel_count": int(count) if count is not None else 0,
                    "latest_observation": latest,
                }
            )
            continue
        points.append(
            {
                "period": period["label"],
                "year": period["year"],
                "month": period["month"],
                "window": {"start": period["start"], "end": period["end"]},
                "value": round(float(value), 4),
                "std_dev": round(float(std_dev), 4) if std_dev is not None else None,
                "observation_count": observation_count,
                "valid_pixel_count": int(count) if count is not None else None,
                "latest_observation": latest,
                "data_status": DataStatus.CURRENT.value,
            }
        )
    return points


__all__ = ["compute_time_series"]
