"""Zonal statistics with valid-pixel accounting and honest data statuses.

Every statistic returned here carries:

* the value and its dispersion (mean / standard deviation),
* how many *valid* pixels and how many *observations* stand behind it,
* the exact window, the latest acquisition timestamp and its age,
* the source dataset, scale, units, processing version and mask method,
* a ``data_status`` from :class:`app.errors.DataStatus`.

When there is no usable observation the value is ``None`` and the status says
so.  A missing value is never replaced by zero.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from . import satellite
from .config import settings
from .errors import DataStatus, InsufficientObservationsError, classify_freshness
from .indicators import Indicator, get_indicator
from .logging_config import get_logger

logger = get_logger(__name__)

MAX_PIXELS = 1e10


@dataclass
class ZonalResult:
    """One indicator's zonal statistic for one region and window."""

    indicator: str
    label: str
    units: str
    value: Optional[float]
    std_dev: Optional[float]
    data_status: str
    observation_count: int
    valid_pixel_count: Optional[int]
    expected_pixel_count: Optional[int]
    coverage_fraction: Optional[float]
    window: dict[str, str]
    latest_observation: Optional[str]
    observation_age_days: Optional[float]
    processed_at: str
    source: dict[str, Any]
    caveat: str = ""
    note: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "indicator": self.indicator,
            "label": self.label,
            "units": self.units,
            "value": self.value,
            "std_dev": self.std_dev,
            "data_status": self.data_status,
            "observation_count": self.observation_count,
            "valid_pixel_count": self.valid_pixel_count,
            "expected_pixel_count": self.expected_pixel_count,
            "coverage_fraction": self.coverage_fraction,
            "window": self.window,
            "latest_observation": self.latest_observation,
            "observation_age_days": self.observation_age_days,
            "processed_at": self.processed_at,
            "source": self.source,
        }
        if self.caveat:
            payload["caveat"] = self.caveat
        if self.note:
            payload["note"] = self.note
        if self.extra:
            payload.update(self.extra)
        return payload


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _iso_from_millis(millis: Any) -> Optional[str]:
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(millis) / 1000.0))
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _age_days(iso: Optional[str]) -> Optional[float]:
    if not iso:
        return None
    stamp = time.mktime(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
    return round((time.time() - stamp) / 86_400.0, 2)


def expected_pixels(area_km2: Optional[float], scale_m: int) -> Optional[int]:
    """How many ``scale_m`` pixels fit inside a region of ``area_km2``.

    Used only to express valid-pixel coverage as a fraction; the count itself
    comes from the reduction.
    """

    if not area_km2:
        return None
    return int(round(area_km2 * 1e6 / float(scale_m ** 2)))


def _parse_reducer_output(raw: dict[str, Any], band: str) -> tuple[Optional[float], Optional[int], Optional[float]]:
    """Extract mean / count / stdDev for one band from a combined reduction."""

    if not raw:
        return None, None, None

    def pick(*suffixes: str) -> Any:
        for suffix in suffixes:
            for key in (f"{band}_{suffix}", band if suffix == "mean" else None):
                if key and key in raw and raw[key] is not None:
                    return raw[key]
        return None

    mean = pick("mean")
    count = pick("count")
    std_dev = pick("stdDev", "stddev")
    return (
        float(mean) if mean is not None else None,
        int(count) if count is not None else None,
        float(std_dev) if std_dev is not None else None,
    )


def _source_metadata(indicator: Indicator, scale_m: int, region: str) -> dict[str, Any]:
    return {
        "dataset": indicator.dataset,
        "bands": list(indicator.bands),
        "equation": indicator.equation,
        "scale_m": scale_m,
        "region": region,
        "mask_method": satellite.mask_method_text(settings.max_cloud_cover_percent),
        "compositing": "per-pixel median of masked scenes",
        "processing_version": satellite.PROCESSING_VERSION,
        "output_crs": "EPSG:4326",
    }


def compute_zonal_statistics(
    ee: Any,
    *,
    indicators: Iterable[str],
    geometry: Any,
    area_km2: Optional[float],
    start: str,
    end: str,
    region_label: str = "basin",
    min_observations: Optional[int] = None,
) -> dict[str, ZonalResult]:
    """Compute zonal statistics for several indicators in one pass where possible.

    Sentinel-2 indices share a single collection, composite and reduction; each
    ancillary product is reduced separately because it comes from another
    dataset (and therefore has another scale and mask).
    """

    indicator_ids = [get_indicator(name).id for name in indicators]
    spectral = [name for name in indicator_ids if get_indicator(name).is_spectral_index]
    ancillary = [name for name in indicator_ids if not get_indicator(name).is_spectral_index]
    threshold = settings.min_observations if min_observations is None else min_observations

    results: dict[str, ZonalResult] = {}
    window = {"start": start, "end": end}

    # ------------------------------------------------- Sentinel-2 indices
    if spectral:
        collection = satellite.s2_collection(
            ee, start, end, geometry, settings.max_cloud_cover_percent
        )
        timestamps = collection.aggregate_array("system:time_start").getInfo() or []
        observation_count = len(timestamps)
        latest = _iso_from_millis(max(timestamps)) if timestamps else None
        age = _age_days(latest)

        if observation_count == 0:
            for name in spectral:
                indicator = get_indicator(name)
                results[name] = ZonalResult(
                    indicator=name,
                    label=indicator.label,
                    units=indicator.units,
                    value=None,
                    std_dev=None,
                    data_status=DataStatus.INSUFFICIENT_OBSERVATIONS.value,
                    observation_count=0,
                    valid_pixel_count=0,
                    expected_pixel_count=expected_pixels(area_km2, indicator.scale_m),
                    coverage_fraction=0.0,
                    window=window,
                    latest_observation=None,
                    observation_age_days=None,
                    processed_at=_now_iso(),
                    source=_source_metadata(indicator, indicator.scale_m, region_label),
                    caveat=indicator.caveat,
                    note="No Sentinel-2 scene passed the cloud mask in this window.",
                )
        else:
            bands = [satellite.band_for_indicator(ee, name) for name in spectral]
            scale_m = max(get_indicator(name).scale_m for name in spectral)
            reduction = (
                collection.median()
                .select(bands)
                .reduceRegion(
                    reducer=satellite.zonal_reducer(ee),
                    geometry=geometry,
                    scale=scale_m,
                    bestEffort=True,
                    maxPixels=MAX_PIXELS,
                )
                .getInfo()
                or {}
            )
            for name, band in zip(spectral, bands):
                indicator = get_indicator(name)
                value, count, std_dev = _parse_reducer_output(reduction, band)
                exp = expected_pixels(area_km2, scale_m)
                coverage = (
                    round(min(count / exp, 1.0), 4) if (count is not None and exp) else None
                )
                if value is None or observation_count < threshold:
                    status = DataStatus.INSUFFICIENT_OBSERVATIONS.value
                    note = (
                        "The reduction returned no valid pixel inside the region."
                        if value is None
                        else f"Only {observation_count} observation(s) passed the cloud mask."
                    )
                    results[name] = ZonalResult(
                        indicator=name,
                        label=indicator.label,
                        units=indicator.units,
                        value=None,
                        std_dev=None,
                        data_status=status,
                        observation_count=observation_count,
                        valid_pixel_count=count or 0,
                        expected_pixel_count=exp,
                        coverage_fraction=coverage,
                        window=window,
                        latest_observation=latest,
                        observation_age_days=age,
                        processed_at=_now_iso(),
                        source=_source_metadata(indicator, scale_m, region_label),
                        caveat=indicator.caveat,
                        note=note,
                    )
                    continue
                results[name] = ZonalResult(
                    indicator=name,
                    label=indicator.label,
                    units=indicator.units,
                    value=round(value, 4),
                    std_dev=round(std_dev, 4) if std_dev is not None else None,
                    data_status=classify_freshness(age, settings.fresh_days, settings.stale_days).value,
                    observation_count=observation_count,
                    valid_pixel_count=count,
                    expected_pixel_count=exp,
                    coverage_fraction=coverage,
                    window=window,
                    latest_observation=latest,
                    observation_age_days=age,
                    processed_at=_now_iso(),
                    source=_source_metadata(indicator, scale_m, region_label),
                    caveat=indicator.caveat,
                )

    # ------------------------------------------------------ ancillary data
    for name in ancillary:
        indicator = get_indicator(name)
        collection = _ancillary_collection(ee, indicator, start, end, geometry)
        observation_count = satellite.count_observations(ee, collection)
        if observation_count == 0:
            results[name] = ZonalResult(
                indicator=name,
                label=indicator.label,
                units=indicator.units,
                value=None,
                std_dev=None,
                data_status=DataStatus.INSUFFICIENT_OBSERVATIONS.value,
                observation_count=0,
                valid_pixel_count=0,
                expected_pixel_count=expected_pixels(area_km2, indicator.scale_m),
                coverage_fraction=0.0,
                window=window,
                latest_observation=None,
                observation_age_days=None,
                processed_at=_now_iso(),
                source=_source_metadata(indicator, indicator.scale_m, region_label),
                caveat=indicator.caveat,
                note=f"No {indicator.dataset} scene intersects the region in this window.",
            )
            continue

        latest = _iso_from_millis(_last_timestamp(collection))
        age = _age_days(latest)
        image = satellite.ancillary_image(ee, indicator, start, end, geometry)
        band = image.bandNames().getInfo()[0]
        reduction = (
            image.reduceRegion(
                reducer=satellite.zonal_reducer(ee),
                geometry=geometry,
                scale=indicator.scale_m,
                bestEffort=True,
                maxPixels=MAX_PIXELS,
            ).getInfo()
            or {}
        )
        value, count, std_dev = _parse_reducer_output(reduction, band)
        exp = expected_pixels(area_km2, indicator.scale_m)
        coverage = round(min(count / exp, 1.0), 4) if (count is not None and exp) else None
        results[name] = ZonalResult(
            indicator=name,
            label=indicator.label,
            units=indicator.units,
            value=round(value, 4) if value is not None else None,
            std_dev=round(std_dev, 4) if std_dev is not None else None,
            data_status=(
                classify_freshness(age, settings.fresh_days, settings.stale_days).value
                if value is not None
                else DataStatus.INSUFFICIENT_OBSERVATIONS.value
            ),
            observation_count=observation_count,
            valid_pixel_count=count,
            expected_pixel_count=exp,
            coverage_fraction=coverage,
            window=window,
            latest_observation=latest,
            observation_age_days=age,
            processed_at=_now_iso(),
            source=_source_metadata(indicator, indicator.scale_m, region_label),
            caveat=indicator.caveat,
        )

    return results


def _ancillary_collection(ee: Any, indicator: Indicator, start: str, end: str, geometry: Any) -> Any:
    """Raw (unscaled) collection for an ancillary product, used for size/metadata."""

    datasets = {
        "rainfall": ("UCSB-CHG/CHIRPS/DAILY", "precipitation"),
        "lst": ("MODIS/061/MOD11A2", "LST_Day_1km"),
        "air_temp": ("ECMWF/ERA5_LAND/DAILY_AGGR", "temperature_2m"),
        "et": ("MODIS/061/MOD16A2GF", "ET"),
        "soil_moisture": ("NASA/SMAP/SPL4SMGP/007", "sm_surface"),
    }
    dataset, band = datasets[indicator.id]
    return ee.ImageCollection(dataset).filterDate(start, end).filterBounds(geometry).select(band)


def _last_timestamp(collection: Any) -> Any:
    values = collection.aggregate_array("system:time_start").getInfo() or []
    return max(values) if values else None


def summarise(results: dict[str, ZonalResult]) -> dict[str, Any]:
    """Overall availability summary for a set of zonal results."""

    values = [item.to_dict() for item in results.values()]
    usable = [item for item in values if item["value"] is not None]
    ages = [item["observation_age_days"] for item in usable if item["observation_age_days"] is not None]
    statuses = sorted({item["data_status"] for item in values})
    return {
        "indicator_count": len(values),
        "usable_indicator_count": len(usable),
        "data_statuses": statuses,
        "latest_observation": max((item["latest_observation"] for item in usable if item["latest_observation"]), default=None),
        "youngest_observation_age_days": min(ages) if ages else None,
        "coverage_fraction_mean": (
            round(sum(item["coverage_fraction"] for item in usable if item["coverage_fraction"]) / len(usable), 4)
            if usable
            else None
        ),
    }


__all__ = ["MAX_PIXELS", "ZonalResult", "compute_zonal_statistics", "expected_pixels", "summarise"]
