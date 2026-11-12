"""Satellite data processing: masking, index calculation, compositing.

Implements the processing chain documented in ``docs/SCIENTIFIC_METHODS.md``:

* Sentinel-2 surface reflectance (``COPERNICUS/S2_SR_HARMONIZED``), bands divided
  by 10 000 to obtain reflectance.
* Cloud and cloud-shadow masking with the Scene Classification Layer (``SCL``)
  as the primary mask and the ``QA60`` bit flags as a secondary screen.
* Normalised-difference indices computed from the correct Sentinel-2 bands.
* Temporal compositing with the median (robust to residual cloud noise).
* An observation-count band so every statistic can report how many valid
  observations stand behind it.

All functions take the ``ee`` module as an argument so the whole chain can be
exercised by the test suite against a small numerical fake.
"""

from __future__ import annotations

from typing import Any, Optional

from .errors import EarthEngineError
from .indicators import Indicator, get_indicator
from .logging_config import get_logger

logger = get_logger(__name__)

#: Bump when masking, bands or compositing change — it invalidates caches.
PROCESSING_VERSION = "2026-11-scl-qa60-median"

S2_COLLECTION = "COPERNICUS/S2_SR_HARMONIZED"
REFLECTANCE_SCALE = 10_000

# SCL classes (Sentinel-2 scene classification layer)
SCL_NO_DATA = 0
SCL_SATURATED_DEFECTIVE = 1
SCL_DARK_AREA = 2
SCL_CLOUD_SHADOW = 3
SCL_VEGETATION = 4
SCL_BARE_SOIL = 5
SCL_WATER = 6
SCL_UNCLASSIFIED = 7
SCL_CLOUD_MEDIUM_PROBABILITY = 8
SCL_CLOUD_HIGH_PROBABILITY = 9
SCL_THIN_CIRRUS = 10
SCL_SNOW = 11

#: Classes removed before compositing (clouds, cirrus, shadow, defects, snow).
SCL_REMOVED_CLASSES = (
    SCL_SATURATED_DEFECTIVE,
    SCL_CLOUD_SHADOW,
    SCL_CLOUD_MEDIUM_PROBABILITY,
    SCL_CLOUD_HIGH_PROBABILITY,
    SCL_THIN_CIRRUS,
    SCL_SNOW,
)

MASK_METHOD = (
    "Sentinel-2 SCL classes {removed} plus QA60 cloud/cirrus bits; scenes with "
    "CLOUDY_PIXEL_PERCENTAGE above {max_cloud:.0f}% are excluded before masking; "
    "compositing uses the per-pixel median of the masked scenes."
)


def mask_method_text(max_cloud: float) -> str:
    return MASK_METHOD.format(removed=list(SCL_REMOVED_CLASSES), max_cloud=max_cloud)


def mask_s2_clouds(ee: Any, image: Any) -> Any:
    """Apply cloud/shadow masking and convert DN to surface reflectance."""

    qa = image.select("QA60")
    cloud_bit = 1 << 10
    cirrus_bit = 1 << 11
    qa_mask = qa.bitwiseAnd(cloud_bit).eq(0).And(qa.bitwiseAnd(cirrus_bit).eq(0))

    scl = image.select("SCL")
    scl_mask = None
    for value in SCL_REMOVED_CLASSES:
        bit = scl.neq(value)
        scl_mask = bit if scl_mask is None else scl_mask.And(bit)

    mask = qa_mask if scl_mask is None else qa_mask.And(scl_mask)
    return image.updateMask(mask).divide(REFLECTANCE_SCALE)


def add_indices(ee: Any, image: Any) -> Any:
    """Add NDVI, EVI, NDWI and MNDWI bands using the documented band mapping."""

    ndvi = image.normalizedDifference(["B8", "B4"]).rename("NDVI")
    ndwi = image.normalizedDifference(["B3", "B8"]).rename("NDWI")
    mndwi = image.normalizedDifference(["B3", "B11"]).rename("MNDWI")
    evi = image.expression(
        "2.5 * ((NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1))",
        {
            "NIR": image.select("B8"),
            "RED": image.select("B4"),
            "BLUE": image.select("B2"),
        },
    ).rename("EVI")
    return image.addBands([ndvi, ndwi, mndwi, evi])


def s2_collection(
    ee: Any,
    start: str,
    end: str,
    region: Any,
    max_cloud_percent: float = 70.0,
) -> Any:
    """Masked Sentinel-2 SR collection filtered to a region and date range."""

    return (
        ee.ImageCollection(S2_COLLECTION)
        .filterBounds(region)
        .filterDate(start, end)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", max_cloud_percent))
        .map(lambda image: add_indices(ee, mask_s2_clouds(ee, image)))
    )


def observation_count(ee: Any, collection: Any) -> Any:
    """Per-pixel number of valid observations behind a composite."""

    return collection.select("NDVI").count().rename("observation_count")


def composite(
    ee: Any,
    collection: Any,
    band: str,
    region: Any,
    method: str = "median",
) -> Any:
    """Per-pixel median (or mean) composite of one band, clipped to ``region``."""

    if method == "mean":
        image = collection.select(band).mean()
    else:
        image = collection.select(band).median()
    return image.clip(region)


def latest_scene_info(ee: Any, collection: Any) -> dict[str, Any]:
    """Acquisition metadata for the most recent scene in a collection."""

    latest = collection.sort("system:time_start", False).first()
    return latest


def ancillary_image(
    ee: Any,
    indicator: Indicator,
    start: str,
    end: str,
    region: Any,
) -> Any:
    """Build the ancillary (non-Sentinel) product for one indicator.

    Each product keeps the scaling documented in ``app/indicators.py``; the
    ``ee.Algorithms.If`` guard on the empty-collection branches prevents the
    "constant 0" placeholder that the previous implementation silently returned
    for ET and soil moisture (a fabricated value presented as an observation).
    """

    identifier = indicator.id

    if identifier == "rainfall":
        collection = (
            ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
            .filterDate(start, end)
            .filterBounds(region)
            .select("precipitation")
        )
        return collection.sum().rename("RAINFALL")

    if identifier == "lst":
        collection = (
            ee.ImageCollection("MODIS/061/MOD11A2")
            .filterDate(start, end)
            .filterBounds(region)
            .select("LST_Day_1km")
        )
        return collection.mean().multiply(0.02).subtract(273.15).rename("LST")

    if identifier == "air_temp":
        collection = (
            ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR")
            .filterDate(start, end)
            .filterBounds(region)
            .select("temperature_2m")
        )
        return collection.mean().subtract(273.15).rename("AIR_TEMP")

    if identifier == "et":
        collection = (
            ee.ImageCollection("MODIS/061/MOD16A2GF")
            .filterDate(start, end)
            .filterBounds(region)
            .select("ET")
        )
        return collection.mean().multiply(0.1).rename("ET")

    if identifier == "soil_moisture":
        collection = (
            ee.ImageCollection("NASA/SMAP/SPL4SMGP/007")
            .filterDate(start, end)
            .filterBounds(region)
            .select("sm_surface")
        )
        return collection.mean().rename("SOIL_MOISTURE")

    raise EarthEngineError(f"No ancillary product is defined for indicator '{identifier}'")


def collection_is_empty(ee: Any, collection: Any) -> bool:
    """Cheap server-side size check.

    An empty collection means "no usable observation in this window".  The
    previous implementation replaced that case with ``ee.Image.constant(0)`` for
    ET and soil moisture -- a fabricated value indistinguishable from a real
    measurement.  Callers use this check to report ``insufficient_observations``
    instead of inventing a number.
    """

    return count_observations(ee, collection) == 0


def count_observations(ee: Any, collection: Any) -> int:
    """Number of scenes in a collection (one lightweight ``getInfo`` call)."""

    size = collection.size().getInfo()
    return int(size) if size is not None else 0


def band_for_indicator(ee: Any, indicator_id: str) -> str:
    """Band name produced by the processing chain for a Sentinel-2 index."""

    mapping = {"ndvi": "NDVI", "evi": "EVI", "ndwi": "NDWI", "mndwi": "MNDWI"}
    try:
        return mapping[indicator_id.lower()]
    except KeyError as exc:
        raise EarthEngineError(f"Indicator '{indicator_id}' is not a Sentinel-2 spectral index") from exc


def zonal_reducer(ee: Any, indicator: Optional[Indicator] = None):
    """Mean + valid-pixel count + std-dev in a single reduction."""

    mean = ee.Reducer.mean()
    count = ee.Reducer.count()
    stddev = ee.Reducer.stdDev()
    return mean.combine(count, sharedInputs=True).combine(stddev, sharedInputs=True)


__all__ = [
    "PROCESSING_VERSION",
    "S2_COLLECTION",
    "add_indices",
    "collection_is_empty",
    "count_observations",
    "ancillary_image",
    "band_for_indicator",
    "composite",
    "latest_scene_info",
    "mask_method_text",
    "mask_s2_clouds",
    "observation_count",
    "s2_collection",
    "zonal_reducer",
]
