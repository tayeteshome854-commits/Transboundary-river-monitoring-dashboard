"""Configuration for the monitoring dashboard.

Everything tunable lives here and is driven by environment variables so that no
deployment depends on hidden local configuration (see ``.env.example``).

The basin registry is derived from the vector data that ships with the
repository (``shapefiles/``).  Each entry records the *observed* properties of
its source file (CRS, geometry count, buffer distance) so that the application
never has to guess about the data it is serving.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# ``.env`` is optional; when present it only *supplies defaults* for values that
# are not already set in the real environment.
load_dotenv(override=False)

BASE_DIR = Path(__file__).resolve().parent.parent
SHAPEFILE_DIR = BASE_DIR / "shapefiles"
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_list(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Basin:
    """One monitored transboundary basin and the data that describes it."""

    id: str
    display_name: str
    river_name: str
    basin_file: str
    buffer_file: str
    ee_basin_asset: str
    ee_buffer_asset: str
    aliases: tuple[str, ...] = ()
    #: Buffer half-width in metres, read from the ``BUFF_DIST`` attribute of the
    #: river-buffer shapefile (verified against the source data on 2026-11-12).
    buffer_distance_m: float = 2000.0
    notes: str = ""

    @property
    def basin_path(self) -> Path:
        return SHAPEFILE_DIR / self.basin_file

    @property
    def buffer_path(self) -> Path:
        return SHAPEFILE_DIR / self.buffer_file


#: Earth Engine project used by the original implementation.  Kept as the
#: default so the existing uploaded assets keep working, but overridable.
DEFAULT_EE_PROJECT = "enduring-art-494812-u2"
ASSET_ROOT = os.getenv("EE_ASSET_ROOT", f"projects/{DEFAULT_EE_PROJECT}/assets")

BASINS: tuple[Basin, ...] = (
    Basin(
        id="abay",
        display_name="Abay (Blue Nile)",
        river_name="Abay",
        basin_file="Abay_basin.shp",
        buffer_file="Abay_river_buffer.shp",
        ee_basin_asset=f"{ASSET_ROOT}/Abay_basin",
        ee_buffer_asset=f"{ASSET_ROOT}/Abay_river_buffer",
        aliases=("abay", "blue nile", "abai", "abay basin"),
        notes="Source CRS EPSG:32637 (WGS 84 / UTM zone 37N).",
    ),
    Basin(
        id="baro_akobo",
        display_name="Baro-Akobo",
        river_name="Baro-Akobo",
        basin_file="Baro_Akobo_basin_new.shp",
        buffer_file="Baro_Akobo_river_buffer.shp",
        ee_basin_asset=f"{ASSET_ROOT}/Baro_Akobo_basin_new",
        ee_buffer_asset=f"{ASSET_ROOT}/Baro_Akobo_river_buffer",
        aliases=("baro_akobo", "baro-akobo", "baro akobo", "baroakobo", "baro"),
        notes=(
            "Source CRS EPSG:4326 (geographic) while every other basin polygon is "
            "EPSG:32637; the river buffer is EPSG:32636 (UTM zone 36N). The "
            "attribute table carries no basin name (gid=0, Area=0)."
        ),
    ),
    Basin(
        id="genale_dawa",
        display_name="Genale-Dawa",
        river_name="Genale",
        basin_file="Genale_Dawa_basin.shp",
        buffer_file="Genale_Dawa_river_buffer.shp",
        ee_basin_asset=f"{ASSET_ROOT}/Genale_Dawa_basin",
        ee_buffer_asset=f"{ASSET_ROOT}/Genale_Dawa_river_buffer",
        aliases=("genale_dawa", "genale-dawa", "genale dawa", "genalle", "genale"),
        notes="Attribute BASIN_NAME is spelled 'Genalle' in the source data.",
    ),
    Basin(
        id="omo",
        display_name="Omo-Gibe",
        river_name="Omo-Gibe",
        basin_file="Omo_basin.shp",
        buffer_file="Omo_river_buffer.shp",
        ee_basin_asset=f"{ASSET_ROOT}/Omo_basin",
        ee_buffer_asset=f"{ASSET_ROOT}/Omo_river_buffer",
        aliases=("omo", "omo-gibe", "omo gibe", "omogibe"),
    ),
    Basin(
        id="tekeze",
        display_name="Tekeze",
        river_name="Tekeze",
        basin_file="Tekeze_basin.shp",
        buffer_file="Tekeze_river_buffer.shp",
        ee_basin_asset=f"{ASSET_ROOT}/Tekeze_basin",
        ee_buffer_asset=f"{ASSET_ROOT}/Tekeze_river_buffer",
        aliases=("tekeze", "tekezé", "tekeze basin"),
    ),
    Basin(
        id="wabe_shabale",
        display_name="Wabe-Shebelle",
        river_name="Wabe-Shebelle",
        basin_file="Wabe_Shabale_basin.shp",
        buffer_file="Wabe_Shabale_river_buffer.shp",
        ee_basin_asset=f"{ASSET_ROOT}/Wabe_Shabale_basin",
        ee_buffer_asset=f"{ASSET_ROOT}/Wabe_Shabale_river_buffer",
        aliases=("wabe_shabale", "wabe-shabale", "wabe shabale", "shebelle", "wabe"),
        notes="Attribute BASIN_NAME is 'Shebelle' in the source data.",
    ),
)

_ALIAS_INDEX: dict[str, str] = {}
for _basin in BASINS:
    _ALIAS_INDEX[_basin.id] = _basin.id
    _ALIAS_INDEX[_basin.display_name.lower()] = _basin.id
    for _alias in _basin.aliases:
        _ALIAS_INDEX[_alias.strip().lower()] = _basin.id


def resolve_basin_id(raw: str) -> Optional[str]:
    """Resolve a user supplied basin label to a canonical basin id.

    Accepts ids, display names and common spellings (``Abay``, ``abai``,
    ``Baro Akobo``, ``Wabe_Shabale`` ...).  Returns ``None`` when the label does
    not identify a monitored basin.
    """

    if raw is None:
        return None
    key = " ".join(str(raw).replace("_", " ").replace("-", " ").split()).lower()
    if key in _ALIAS_INDEX:
        return _ALIAS_INDEX[key]
    compact = key.replace(" ", "")
    for alias, basin_id in _ALIAS_INDEX.items():
        if alias.replace(" ", "") == compact:
            return basin_id
    return None


def get_basin(basin_id: str) -> Basin:
    for basin in BASINS:
        if basin.id == basin_id:
            return basin
    raise KeyError(basin_id)


GREEN_LEGACY_FILE = SHAPEFILE_DIR / "Green_legacy.shp"
GREEN_LEGACY_ASSET = f"{ASSET_ROOT}/Green_legacy"


@dataclass(frozen=True)
class Settings:
    """Runtime settings resolved once from the environment."""

    ee_project_id: str = field(
        default_factory=lambda: os.getenv("EE_PROJECT_ID", DEFAULT_EE_PROJECT)
    )
    ee_credentials_file: Optional[str] = field(
        default_factory=lambda: os.getenv("EE_SERVICE_ACCOUNT_KEY") or None
    )
    ee_timeout_seconds: float = field(default_factory=lambda: _env_float("EE_TIMEOUT_SECONDS", 90.0))
    ee_max_retries: int = field(default_factory=lambda: _env_int("EE_MAX_RETRIES", 2))
    ee_retry_backoff_seconds: float = field(
        default_factory=lambda: _env_float("EE_RETRY_BACKOFF_SECONDS", 1.5)
    )
    #: Maximum cloud cover (scene metadata) accepted for Sentinel-2 scenes.
    max_cloud_cover_percent: float = field(
        default_factory=lambda: _env_float("MAX_CLOUD_COVER_PERCENT", 70.0)
    )
    #: Minimum number of valid Sentinel-2 observations behind a composite.
    min_observations: int = field(default_factory=lambda: _env_int("MIN_OBSERVATIONS", 1))
    #: Freshness thresholds used to label the latest observation.
    fresh_days: int = field(default_factory=lambda: _env_int("FRESH_DAYS", 16))
    stale_days: int = field(default_factory=lambda: _env_int("STALE_DAYS", 45))
    #: Default look-back window (days) for "latest composite" products.
    default_window_days: int = field(default_factory=lambda: _env_int("DEFAULT_WINDOW_DAYS", 90))
    #: Simplification tolerance (metres) applied when vector data is served as GeoJSON.
    basin_simplify_m: float = field(default_factory=lambda: _env_float("BASIN_SIMPLIFY_M", 150.0))
    buffer_simplify_m: float = field(default_factory=lambda: _env_float("BUFFER_SIMPLIFY_M", 60.0))
    green_simplify_m: float = field(default_factory=lambda: _env_float("GREEN_SIMPLIFY_M", 25.0))
    #: Server-side cache lifetime for Earth Engine products.
    cache_ttl_seconds: int = field(default_factory=lambda: _env_int("CACHE_TTL_SECONDS", 3600))
    cache_max_items: int = field(default_factory=lambda: _env_int("CACHE_MAX_ITEMS", 256))
    #: Demonstration data is only ever produced when this is true *and* Earth
    #: Engine is unavailable, and every payload it produces is labelled.
    allow_demo_mode: bool = field(default_factory=lambda: _env_bool("ALLOW_DEMO_MODE", True))
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(_env_list("CORS_ORIGINS", "*"))
    )
    environment: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    host: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _env_int("PORT", 8000))

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
