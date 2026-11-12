"""Indicator registry: the single source of truth for what each product is.

Every indicator declares its data source, band mapping, equation, native
resolution (the scale used for reductions), units, valid range and rendering
palette.  Nothing in the codebase hard-codes a band name or an equation twice;
the HTTP layer, the Earth Engine processing layer, the legend and the CSV
exports all read from this module.

Band mapping for Sentinel-2 Surface Reflectance (``COPERNICUS/S2_SR_HARMONIZED``)
is documented in ``docs/SCIENTIFIC_METHODS.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

Family = Literal["vegetation", "surface_water", "climate", "soil"]
Kind = Literal["satellite_optical", "satellite_ancillary"]


@dataclass(frozen=True)
class Indicator:
    """A derived geospatial product that the dashboard can compute and render."""

    id: str
    label: str
    family: Family
    kind: Kind
    dataset: str
    #: Human readable equation exactly as implemented in Earth Engine.
    equation: str
    bands: tuple[str, ...]
    scale_m: int
    units: str
    valid_range: Optional[tuple[float, float]]
    palette: tuple[tuple[float, str], ...]
    description: str
    #: Reducer used for zonal statistics (``mean`` unless stated otherwise).
    reducer: str = "mean"
    #: True when the product is a Sentinel-2 spectral index computed on the fly.
    is_spectral_index: bool = False
    #: Interpretation guard rail shown next to the value in the UI.
    caveat: str = ""
    #: Default temporal aggregation for time series ("year" or "month").
    default_aggregation: str = "year"
    extra: dict = field(default_factory=dict)

    @property
    def is_satellite_observation(self) -> bool:
        return True

    def to_public_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "family": self.family,
            "kind": self.kind,
            "dataset": self.dataset,
            "equation": self.equation,
            "bands": list(self.bands),
            "scale_m": self.scale_m,
            "units": self.units,
            "valid_range": list(self.valid_range) if self.valid_range else None,
            "palette": [{"value": value, "color": color} for value, color in self.palette],
            "description": self.description,
            "caveat": self.caveat,
            "default_aggregation": self.default_aggregation,
        }


S2_SR_HARMONIZED = "COPERNICUS/S2_SR_HARMONIZED"

INDICATORS: dict[str, Indicator] = {
    ind.id: ind
    for ind in (
        Indicator(
            id="ndvi",
            label="NDVI",
            family="vegetation",
            kind="satellite_optical",
            dataset=S2_SR_HARMONIZED,
            equation="(B8 - B4) / (B8 + B4)",
            bands=("B8", "B4"),
            scale_m=10,
            units="dimensionless (-1 to 1)",
            valid_range=(-1.0, 1.0),
            palette=(
                (-0.2, "#8c510a"),
                (0.0, "#bf812d"),
                (0.2, "#e0c97f"),
                (0.4, "#86b35b"),
                (0.6, "#2f8f3e"),
                (0.85, "#0b5123"),
            ),
            description=(
                "Normalised Difference Vegetation Index from Sentinel-2 surface "
                "reflectance; indicates green vegetation density and condition."
            ),
            is_spectral_index=True,
            caveat=(
                "A decline in NDVI is evidence of reduced greenness only; it is not "
                "by itself proof of drought. Read it together with rainfall, "
                "temperature and soil moisture."
            ),
        ),
        Indicator(
            id="evi",
            label="EVI",
            family="vegetation",
            kind="satellite_optical",
            dataset=S2_SR_HARMONIZED,
            equation="2.5 * (B8 - B4) / (B8 + 6*B4 - 7.5*B2 + 1)",
            bands=("B8", "B4", "B2"),
            scale_m=10,
            units="dimensionless (-1 to 1)",
            valid_range=(-1.0, 1.0),
            palette=(
                (-0.2, "#8c510a"),
                (0.0, "#c8a96b"),
                (0.2, "#9dc06b"),
                (0.4, "#4e9c46"),
                (0.6, "#1f7a34"),
                (0.9, "#08421c"),
            ),
            description=(
                "Enhanced Vegetation Index; less sensitive than NDVI to bright soil "
                "and residual atmospheric effects in dense canopies."
            ),
            is_spectral_index=True,
        ),
        Indicator(
            id="ndwi",
            label="NDWI",
            family="surface_water",
            kind="satellite_optical",
            dataset=S2_SR_HARMONIZED,
            equation="(B3 - B8) / (B3 + B8)",
            bands=("B3", "B8"),
            scale_m=10,
            units="dimensionless (-1 to 1)",
            valid_range=(-1.0, 1.0),
            palette=(
                (-0.5, "#7b4a12"),
                (-0.1, "#d9c9a8"),
                (0.1, "#a8d5f2"),
                (0.3, "#3f8fd2"),
                (0.6, "#12467f"),
            ),
            description=(
                "Normalised Difference Water Index (McFeeters). Highlights open "
                "water and wet surfaces."
            ),
            is_spectral_index=True,
            caveat=(
                "An index value is not a validated water-body classification. "
                "Thresholds used here are documented heuristics, not locally "
                "validated ones; treat water extent as indicative."
            ),
        ),
        Indicator(
            id="mndwi",
            label="MNDWI",
            family="surface_water",
            kind="satellite_optical",
            dataset=S2_SR_HARMONIZED,
            equation="(B3 - B11) / (B3 + B11)",
            bands=("B3", "B11"),
            scale_m=10,
            units="dimensionless (-1 to 1)",
            valid_range=(-1.0, 1.0),
            palette=(
                (-0.5, "#3f2d1a"),
                (-0.1, "#cfc4ae"),
                (0.1, "#8fd3de"),
                (0.3, "#17a2b8"),
                (0.6, "#0a4f63"),
            ),
            description=(
                "Modified NDWI (Xu 2006). Uses SWIR (B11) to suppress built-up and "
                "bare-soil noise; B11 is 20 m and is resampled to the 10 m grid."
            ),
            is_spectral_index=True,
            caveat=(
                "An index value is not a validated water-body classification. "
                "Thresholds used here are documented heuristics, not locally "
                "validated ones; treat water extent as indicative."
            ),
        ),
        Indicator(
            id="rainfall",
            label="Rainfall",
            family="climate",
            kind="satellite_ancillary",
            dataset="UCSB-CHG/CHIRPS/DAILY",
            equation="sum(daily precipitation) over the selected period",
            bands=("precipitation",),
            scale_m=5566,
            units="mm per period",
            valid_range=(0.0, 4000.0),
            palette=(
                (0.0, "#f7fbff"),
                (100.0, "#c6dbef"),
                (300.0, "#6baed6"),
                (600.0, "#2171b5"),
                (1200.0, "#08306b"),
            ),
            description=(
                "CHIRPS v2.0 daily precipitation accumulated over the selected "
                "period. Satellite/reanalysis estimate, not a rain-gauge record."
            ),
            reducer="sum",
            caveat=(
                "Gridded estimate; it is not a gauge measurement and does not "
                "replace station rainfall records."
            ),
        ),
        Indicator(
            id="lst",
            label="Land surface temperature",
            family="climate",
            kind="satellite_ancillary",
            dataset="MODIS/061/MOD11A2",
            equation="mean(LST_Day_1km) * 0.02 - 273.15",
            bands=("LST_Day_1km",),
            scale_m=1000,
            units="°C (daytime, 8-day composites averaged)",
            valid_range=(-10.0, 60.0),
            palette=(
                (10.0, "#2166ac"),
                (20.0, "#92c5de"),
                (30.0, "#fddbc7"),
                (40.0, "#ef8a62"),
                (50.0, "#b2182b"),
            ),
            description="MODIS Terra 8-day daytime land surface temperature, mean over the period.",
        ),
        Indicator(
            id="air_temp",
            label="Air temperature (2 m)",
            family="climate",
            kind="satellite_ancillary",
            dataset="ECMWF/ERA5_LAND/DAILY_AGGR",
            equation="mean(temperature_2m) - 273.15",
            bands=("temperature_2m",),
            scale_m=11132,
            units="°C",
            valid_range=(-10.0, 50.0),
            palette=(
                (5.0, "#4b2e83"),
                (15.0, "#4575b4"),
                (25.0, "#fdae61"),
                (35.0, "#d73027"),
                (45.0, "#7f0000"),
            ),
            description="ERA5-Land reanalysis daily mean 2 m air temperature.",
            caveat="Reanalysis product, not a station thermometer reading.",
        ),
        Indicator(
            id="et",
            label="Evapotranspiration",
            family="climate",
            kind="satellite_ancillary",
            dataset="MODIS/061/MOD16A2GF",
            equation="mean(ET) * 0.1",
            bands=("ET",),
            scale_m=500,
            units="mm per 8-day interval (gap-filled product, averaged)",
            valid_range=(0.0, 200.0),
            palette=(
                (0.0, "#f7fcf5"),
                (20.0, "#c7e9c0"),
                (50.0, "#74c476"),
                (90.0, "#238b45"),
                (150.0, "#00441b"),
            ),
            description="MODIS MOD16A2GF gap-filled evapotranspiration, scaled from kg/m^2 to mm.",
        ),
        Indicator(
            id="soil_moisture",
            label="Surface soil moisture",
            family="soil",
            kind="satellite_ancillary",
            dataset="NASA/SMAP/SPL4SMGP/007",
            equation="mean(sm_surface)",
            bands=("sm_surface",),
            scale_m=9000,
            units="m^3/m^3 (surface layer)",
            valid_range=(0.0, 0.6),
            palette=(
                (0.0, "#8c510a"),
                (0.1, "#d8b365"),
                (0.2, "#a1d99b"),
                (0.35, "#35978f"),
                (0.5, "#1b4f72"),
            ),
            description="SMAP L4 surface (0-5 cm) soil moisture analysis, mean over the period.",
        ),
    )
}

#: Sentinel-2 spectral indices that the dashboard computes itself.
SPECTRAL_INDICES = tuple(i for i, ind in INDICATORS.items() if ind.is_spectral_index)

#: Default indicator set promoted in the executive overview.
CORE_INDICATORS = ("ndvi", "ndwi", "mndwi")

#: Water thresholds used for indicative (not validated) water extent.
WATER_THRESHOLDS = {
    "ndwi": 0.0,
    "mndwi": 0.0,
}


def get_indicator(indicator_id: str) -> Indicator:
    try:
        return INDICATORS[indicator_id.lower()]
    except KeyError as exc:  # pragma: no cover - guarded by the API layer
        raise KeyError(indicator_id) from exc


def normalise_indicator(raw: str) -> Optional[str]:
    """Map a user supplied indicator label to a canonical id (or ``None``)."""

    if raw is None:
        return None
    key = str(raw).strip().lower().replace("-", "_").replace(" ", "_")
    if key in INDICATORS:
        return key
    aliases = {
        "ndvi": "ndvi",
        "evi": "evi",
        "ndwi": "ndwi",
        "mndwi": "mndwi",
        "rainfall": "rainfall",
        "precipitation": "rainfall",
        "lst": "lst",
        "land_surface_temperature": "lst",
        "air_temp": "air_temp",
        "air_temperature": "air_temp",
        "t2m": "air_temp",
        "et": "et",
        "evapotranspiration": "et",
        "soil_moisture": "soil_moisture",
        "soilmoisture": "soil_moisture",
    }
    return aliases.get(key)


def palette_to_vis(indicator: Indicator) -> dict:
    """Build an Earth Engine visualisation dictionary from the palette."""

    return {
        "min": indicator.palette[0][0],
        "max": indicator.palette[-1][0],
        "palette": [color for _, color in indicator.palette],
    }
