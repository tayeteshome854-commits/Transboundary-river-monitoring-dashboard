"""Vector data service: shapefiles on disk -> validated, reprojected GeoJSON.

The basin, river-buffer and Green Legacy geometries that ship with this
repository are the project's real spatial data, so they are served directly from
disk.  That keeps basin boundaries, extents and areas working even when Earth
Engine is unreachable, and it makes every area figure reproducible.

Responsibilities
----------------
* read the shapefiles with their own CRS (``pyogrio``),
* repair invalid geometries (``make_valid``) and drop Z coordinates,
* simplify with a documented tolerance *in metres, in the source projection*,
* reproject to WGS 84 (EPSG:4326) for the browser / Earth Engine,
* compute areas in an equal-area projection (Africa Albers Equal Area Conic,
  ESRI:102022) so basin areas are not distorted by UTM zone edges,
* cache the derived GeoJSON on disk (``data/cache``) keyed by source mtime.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import CRS
from shapely import force_2d, make_valid
from shapely.geometry import mapping
from shapely.ops import unary_union

from .cache import TTLCache
from .config import BASINS, CACHE_DIR, GREEN_LEGACY_FILE, Basin, SHAPEFILE_DIR
from .errors import UnknownBasinError
from .logging_config import get_logger

logger = get_logger(__name__)

WEB_CRS = CRS.from_epsg(4326)
AREA_CRS = CRS.from_user_input("ESRI:102022")  # Africa Albers Equal Area Conic
AREA_CRS_NAME = "ESRI:102022 (Africa Albers Equal Area Conic)"

#: Bump when the derivation logic changes so cached artefacts are rebuilt.
GEOMETRY_SCHEMA_VERSION = "2"

_memory_cache: TTLCache = TTLCache(maxsize=32, ttl_seconds=3600, name="geometry")


@dataclass
class LayerProvenance:
    """What the source file actually contains (read, never assumed)."""

    name: str
    source_file: str
    source_crs: Optional[str]
    source_crs_name: Optional[str]
    feature_count: int
    geometry_type: Optional[str]
    invalid_geometry_count: int
    attributes: dict[str, Any]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source_file": self.source_file,
            "source_crs": self.source_crs,
            "source_crs_name": self.source_crs_name,
            "feature_count": self.feature_count,
            "geometry_type": self.geometry_type,
            "invalid_geometry_count": self.invalid_geometry_count,
            "attributes": self.attributes,
            "warnings": self.warnings,
        }


# --------------------------------------------------------------------------- IO


def read_layer(path: Path) -> gpd.GeoDataFrame:
    """Read a shapefile, repairing invalid geometries and dropping Z values."""

    if not path.exists():
        raise FileNotFoundError(f"Vector data not found: {path}")
    frame = gpd.read_file(path)
    if frame.crs is None:
        raise ValueError(f"{path.name} has no coordinate reference system defined")
    invalid = int((~frame.geometry.is_valid).sum())
    if invalid:
        logger.warning("repaired %s invalid geometries in %s", invalid, path.name)
        frame["geometry"] = frame.geometry.buffer(0)
        still_invalid = int((~frame.geometry.is_valid).sum())
        if still_invalid:
            frame["geometry"] = frame.geometry.apply(
                lambda geom: make_valid(geom) if geom is not None and not geom.is_valid else geom
            )
    if frame.geometry.has_z.any():
        frame["geometry"] = frame.geometry.apply(force_2d)
    return frame


def _source_mtime(path: Path) -> int:
    try:
        return int(path.stat().st_mtime)
    except OSError:
        return 0


def _cache_path(key: str) -> Path:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
    return CACHE_DIR / f"{digest}.geojson"


def _cached_json(key: str, builder) -> Any:
    """Serve derived GeoJSON from memory, then disk, then rebuild it."""

    hit = _memory_cache.get(key)
    if hit is not None:
        return hit

    path = _cache_path(key)
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            _memory_cache.set(key, payload)
            return payload
        except (json.JSONDecodeError, OSError):
            logger.warning("discarding unreadable geometry cache entry %s", path)

    started = time.time()
    payload = builder()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError as exc:  # pragma: no cover - disk problems must not break serving
        logger.warning("could not write geometry cache %s: %s", path, exc)
    _memory_cache.set(key, payload)
    logger.info(
        "built geometry cache %s in %.2fs (%.1f kB)",
        key,
        time.time() - started,
        len(json.dumps(payload)) / 1024,
    )
    return payload


def clear_geometry_cache() -> None:
    _memory_cache.clear()
    if CACHE_DIR.exists():
        for entry in CACHE_DIR.glob("*.geojson"):
            entry.unlink(missing_ok=True)


# --------------------------------------------------------------------- helpers


def to_web(frame: gpd.GeoDataFrame, simplify_m: float = 0.0) -> gpd.GeoDataFrame:
    """Simplify in the source projection (metres) then reproject to EPSG:4326."""

    out = frame.copy()
    if simplify_m and out.crs is not None and out.crs.is_projected:
        out["geometry"] = out.geometry.simplify(simplify_m, preserve_topology=True)
    out = out.to_crs(WEB_CRS)
    if simplify_m and (out.crs is None or not frame.crs.is_projected):
        # Geographic source: convert the metre tolerance to approximate degrees.
        out["geometry"] = out.geometry.simplify(simplify_m / 111_320.0, preserve_topology=True)
    out["geometry"] = out.geometry.apply(force_2d)
    return out


def area_km2(frame: gpd.GeoDataFrame) -> float:
    """Total area in km^2 using an equal-area projection for Africa."""

    projected = frame.to_crs(AREA_CRS)
    return float(projected.geometry.area.sum() / 1e6)


def _feature(geometry, properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Feature", "geometry": mapping(geometry), "properties": properties}


def _feature_collection(features: Iterable[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": list(features)}


def _jsonable(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if value is pd.NaT:
        return None
    if isinstance(value, float) and np.isnan(value):
        return None
    if pd.isna(value):  # covers pandas NA / NaT for scalars
        return None
    return value


def _attributes_dict(frame: gpd.GeoDataFrame) -> dict[str, Any]:
    """First-row attribute table, JSON safe (used as source metadata)."""

    if frame.empty:
        return {}
    row = frame.drop(columns="geometry").iloc[0]
    return {key: _jsonable(value) for key, value in row.items()}


def _bbox(frame: gpd.GeoDataFrame) -> list[float]:
    frame_4326 = frame if frame.crs == WEB_CRS else frame.to_crs(WEB_CRS)
    return [round(float(value), 5) for value in frame_4326.total_bounds]


# --------------------------------------------------------------------- layers


def basin_source(basin: Basin, kind: str = "basin") -> Path:
    return basin.basin_path if kind == "basin" else basin.buffer_path


def layer_provenance(name: str, path: Path) -> LayerProvenance:
    frame = gpd.read_file(path, rows=5)
    info = gpd.read_file(path, rows=0)
    crs = info.crs
    warnings: list[str] = []
    if crs is not None and crs.to_epsg() == 4326:
        warnings.append("Source CRS is geographic (EPSG:4326) while the other project layers are projected.")
    if crs is not None and crs.to_epsg() in {32636, 32637, 32638} and crs.to_epsg() != 32637:
        warnings.append(f"Source CRS is {crs.name}; the majority of project layers use EPSG:32637.")
    full = gpd.read_file(path, columns=["geometry"])
    invalid = int((~full.geometry.is_valid).sum())
    if invalid:
        warnings.append(f"{invalid} invalid geometries repaired at load time (buffer(0)/make_valid).")
    return LayerProvenance(
        name=name,
        source_file=path.name,
        source_crs=crs.to_epsg() if crs is not None and crs.to_epsg() else str(crs),
        source_crs_name=crs.name if crs is not None else None,
        feature_count=int(len(full)),
        geometry_type=str(info.geometry_type) if hasattr(info, "geometry_type") else None,
        invalid_geometry_count=invalid,
        attributes=_attributes_dict(frame),
        warnings=warnings,
    )


def _basin_or_none(basin_id: str) -> Basin:
    for basin in BASINS:
        if basin.id == basin_id:
            return basin
    raise UnknownBasinError(
        f"Unknown basin '{basin_id}'",
        detail={"available": [basin.id for basin in BASINS]},
    )


def basin_geojson(basin_id: str, kind: str = "basin", simplify_m: Optional[float] = None) -> dict[str, Any]:
    """GeoJSON (EPSG:4326) for a basin or its river buffer, with provenance."""

    basin = _basin_or_none(basin_id)
    from .config import settings

    tolerance = (
        simplify_m
        if simplify_m is not None
        else (settings.basin_simplify_m if kind == "basin" else settings.buffer_simplify_m)
    )
    path = basin_source(basin, kind)
    key = f"{GEOMETRY_SCHEMA_VERSION}:{kind}:{basin_id}:{tolerance}:{_source_mtime(path)}"

    def build() -> dict[str, Any]:
        frame = read_layer(path)
        web = to_web(frame, tolerance)
        union = unary_union(web.geometry.values)
        properties: dict[str, Any] = {
            "basin_id": basin.id,
            "basin_name": basin.display_name,
            "layer": kind,
            "river_name": basin.river_name,
            "source_file": path.name,
            "source_crs": str(frame.crs),
            "simplify_tolerance_m": tolerance,
            "area_km2": round(area_km2(frame), 1),
            "output_crs": "EPSG:4326",
        }
        if kind == "buffer":
            buffer_distance = _buffer_distance(path)
            properties.update(
                {
                    "buffer_distance_m": buffer_distance,
                    "buffer_note": (
                        "Corridor geometry is the project's digitised river network buffered by "
                        f"{buffer_distance:.0f} m (attribute BUFF_DIST)."
                    ),
                }
            )
        feature = _feature(union, properties)
        payload = _feature_collection([feature])
        payload["provenance"] = {
            "source_file": path.name,
            "source_crs": str(frame.crs),
            "source_feature_count": int(len(frame)),
            "output_crs": "EPSG:4326",
            "simplify_tolerance_m": tolerance,
            "area_crs": AREA_CRS_NAME,
            "area_km2": round(area_km2(frame), 1),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        return payload

    return _cached_json(key, build)


def _buffer_distance(path: Path) -> float:
    try:
        frame = gpd.read_file(path)
        for column in ("BUFF_DIST", "BUFFDIST", "BufferDist"):
            if column in frame.columns:
                value = pd.to_numeric(frame[column], errors="coerce").dropna()
                if not value.empty:
                    return float(value.iloc[0])
    except Exception:  # pragma: no cover - metadata must never break the map
        logger.exception("could not read buffer distance from %s", path.name)
    return 2000.0


def basins_catalog() -> list[dict[str, Any]]:
    """One record per monitored basin with areas, extents and data caveats."""

    key = f"{GEOMETRY_SCHEMA_VERSION}:catalog:{':'.join(str(_source_mtime(b.basin_path)) for b in BASINS)}"

    def build() -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for basin in BASINS:
            basin_frame = read_layer(basin.basin_path)
            buffer_frame = read_layer(basin.buffer_path)
            source_crs = basin_frame.crs
            record = {
                "id": basin.id,
                "name": basin.display_name,
                "river_name": basin.river_name,
                "area_km2": round(area_km2(basin_frame), 1),
                "buffer_area_km2": round(area_km2(buffer_frame), 1),
                "buffer_distance_m": _buffer_distance(basin.buffer_path),
                "bbox": _bbox(basin_frame),
                "centroid": [
                    round(float(basin_frame.to_crs(WEB_CRS).geometry.iloc[0].centroid.y), 4),
                    round(float(basin_frame.to_crs(WEB_CRS).geometry.iloc[0].centroid.x), 4),
                ],
                "source": {
                    "basin_file": basin.basin_file,
                    "buffer_file": basin.buffer_file,
                    "basin_crs": source_crs.to_epsg() or str(source_crs),
                    "buffer_crs": buffer_frame.crs.to_epsg() or str(buffer_frame.crs),
                    "attributes": _attributes_dict(basin_frame),
                },
                "notes": basin.notes,
            }
            warnings: list[str] = []
            if source_crs.to_epsg() != 32637:
                warnings.append(
                    f"Basin polygon is in {source_crs.name}; other basins are in EPSG:32637. "
                    "It is reprojected to EPSG:4326 before use."
                )
            attrs = record["source"]["attributes"]
            declared = attrs.get("SQKM")
            if declared:
                ratio = record["area_km2"] / float(declared)
                if ratio > 1.5 or ratio < 0.67:
                    warnings.append(
                        f"Attribute SQKM={declared:.0f} km^2 disagrees with the geometry, which "
                        f"measures {record['area_km2']:,.0f} km^2 in {AREA_CRS_NAME}. The geometric "
                        "area is used; the attribute is not trusted."
                    )
            record["warnings"] = warnings
            records.append(record)
        return records

    return _cached_json(key, build)


def basin_geometry_4326(basin_id: str, kind: str = "basin"):
    """Shapely geometry in EPSG:4326 — the shape handed to Earth Engine filters."""

    basin = _basin_or_none(basin_id)
    frame = read_layer(basin_source(basin, kind))
    return unary_union(to_web(frame).geometry.values)


# ----------------------------------------------------------------- Green Legacy

GREEN_LEGACY_FIELDS = ("Region", "Zone", "Wereda", "Site", "Program", "Purpose", "Ownership")


def green_legacy_frame() -> gpd.GeoDataFrame:
    """Green Legacy sites with a corrected area field.

    Data audit (2026-11-12): the ``Area_Ha`` attribute equals ``Shape_Area``
    exactly (ratio 1.000000 across 21,429 features), i.e. it holds **square
    metres** despite its name.  Summing it as hectares overstates the programme
    by a factor of 10,000, so this loader exposes ``area_ha_computed`` from the
    geometry and flags the attribute as untrusted.
    """

    frame = read_layer(GREEN_LEGACY_FILE)
    frame["area_ha_computed"] = frame.geometry.area / 10_000.0
    return frame


def green_legacy_summary(basin_id: Optional[str] = None) -> dict[str, Any]:
    """Aggregate Green Legacy planting records; attribute defects are reported."""

    key = f"{GREEN_LEGACY_SCHEMA_KEY()}:summary:{basin_id or 'all'}"

    def build() -> dict[str, Any]:
        frame = green_legacy_frame()
        selected = frame
        subset_note = "all regions"
        if basin_id:
            basin = _basin_or_none(basin_id)
            basin_geom = basin_geometry_4326(basin_id, "basin")
            web = to_web(frame)
            mask = web.geometry.intersects(basin_geom)
            selected = frame.loc[mask]
            subset_note = basin.display_name

        total_ha = float(selected["area_ha_computed"].sum())
        by_region = (
            selected.groupby("Region", dropna=False)
            .agg(sites=("OBJECTID", "count"), area_ha=("area_ha_computed", "sum"), seedlings=("Seedling", "sum"))
            .sort_values("sites", ascending=False)
        )
        by_program = (
            selected.groupby("Program", dropna=False)
            .agg(sites=("OBJECTID", "count"), area_ha=("area_ha_computed", "sum"))
            .sort_values("sites", ascending=False)
        )
        return {
            "subset": subset_note,
            "feature_count": int(len(selected)),
            "total_area_ha": round(total_ha, 1),
            "total_seedlings_recorded": int(selected["Seedling"].sum()) if not selected.empty else 0,
            "by_region": [
                {
                    "region": _jsonable(index) or "unrecorded",
                    "sites": int(row["sites"]),
                    "area_ha": round(float(row["area_ha"]), 1),
                    "seedlings": int(row["seedlings"]),
                }
                for index, row in by_region.iterrows()
            ],
            "by_program": [
                {
                    "program": _jsonable(index) or "unrecorded",
                    "sites": int(row["sites"]),
                    "area_ha": round(float(row["area_ha"]), 1),
                }
                for index, row in by_program.iterrows()
            ],
            "data_quality": {
                "area_field": (
                    "The source field Area_Ha equals Shape_Area, i.e. it holds square metres (m^2) "
                    "despite its name. Areas reported here are computed from the geometry in an "
                    "equal-area projection and converted to hectares."
                ),
                "seedling_field": "Sum of the source Seedling attribute; no survival verification is available.",
                "species_field": "100% empty in the source data; not used.",
                "planted_field": "0 for every record in the source data; not used.",
                "limitations": (
                    "These are planting records (sites, recorded seedling counts). They are not a "
                    "measure of tree survival, and satellite NDVI is not a count of trees planted."
                ),
            },
            "provenance": {
                "source_file": GREEN_LEGACY_FILE.name,
                "feature_count_total": int(len(frame)),
                "area_crs": AREA_CRS_NAME,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        }

    return _cached_json(key, build)


def green_legacy_geojson(
    basin_id: Optional[str] = None,
    mode: str = "points",
    limit: int = 5000,
) -> dict[str, Any]:
    """Green Legacy sites as GeoJSON (centroids by default — 21k polygons is ~18 MB)."""

    key = f"{GREEN_LEGACY_SCHEMA_KEY()}:features:{mode}:{basin_id or 'all'}:{limit}"

    def build() -> dict[str, Any]:
        frame = green_legacy_frame()
        subset_note = "all regions"
        if basin_id:
            _basin_or_none(basin_id)
            basin_geom = basin_geometry_4326(basin_id, "basin")
            mask = to_web(frame).geometry.intersects(basin_geom)
            frame = frame.loc[mask]
            subset_note = _basin_or_none(basin_id).display_name

        web = to_web(frame)
        truncated = False
        if limit and len(web) > limit:
            web = web.sort_values("Seedling", ascending=False).head(limit)
            truncated = True

        features = []
        for _, row in web.iterrows():
            geometry = row.geometry.representative_point() if mode == "points" else row.geometry
            features.append(
                _feature(
                    geometry,
                    {
                        "site": _jsonable(row.get("Site")) or "unnamed site",
                        "region": _jsonable(row.get("Region")),
                        "zone": _jsonable(row.get("Zone")),
                        "wereda": _jsonable(row.get("Wereda")),
                        "program": _jsonable(row.get("Program")),
                        "ownership": _jsonable(row.get("Ownership")),
                        "area_ha": round(float(row["area_ha_computed"]), 3),
                        "seedlings": _jsonable(row.get("Seedling")),
                    },
                )
            )

        payload = _feature_collection(features)
        payload["provenance"] = {
            "source_file": GREEN_LEGACY_FILE.name,
            "subset": subset_note,
            "mode": mode,
            "feature_count_total": int(len(frame)),
            "feature_count_returned": len(features),
            "truncated": truncated,
            "limit": limit,
            "output_crs": "EPSG:4326",
            "note": (
                "Planting-site records from the Green Legacy Initiative. NDVI is an independent "
                "satellite observation of greenness and does not measure tree counts or survival."
            ),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        return payload

    return _cached_json(key, build)


def green_legacy_by_wereda() -> dict[str, Any]:
    """Wereda-level aggregation — a small, fast choropleth of planting effort."""

    key = f"{GREEN_LEGACY_SCHEMA_KEY()}:by_wereda"

    def build() -> dict[str, Any]:
        frame = green_legacy_frame()
        web = to_web(frame, 200.0)
        web = web.assign(_key=web["Region"].astype(str) + " / " + web["Zone"].astype(str) + " / " + web["Wereda"].astype(str))
        grouped = web.dissolve(by="_key", as_index=False, aggfunc={"OBJECTID": "count", "area_ha_computed": "sum", "Seedling": "sum"})
        features = [
            _feature(
                row.geometry,
                {
                    "admin": row["_key"],
                    "sites": int(row["OBJECTID"]),
                    "area_ha": round(float(row["area_ha_computed"]), 1),
                    "seedlings": int(row["Seedling"]),
                },
            )
            for _, row in grouped.iterrows()
        ]
        payload = _feature_collection(features)
        payload["provenance"] = {
            "source_file": GREEN_LEGACY_FILE.name,
            "aggregation": "region / zone / wereda",
            "feature_count_returned": len(features),
            "output_crs": "EPSG:4326",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        return payload

    return _cached_json(key, build)


def GREEN_LEGACY_SCHEMA_KEY() -> str:
    return f"{GEOMETRY_SCHEMA_VERSION}:green:{_source_mtime(GREEN_LEGACY_FILE)}"


def data_inventory() -> list[dict[str, Any]]:
    """Inventory of every vector dataset the application serves."""

    entries: list[dict[str, Any]] = []
    for basin in BASINS:
        entries.append(layer_provenance(f"{basin.id}_basin", basin.basin_path).to_dict())
        entries.append(layer_provenance(f"{basin.id}_river_buffer", basin.buffer_path).to_dict())
    entries.append(layer_provenance("green_legacy", GREEN_LEGACY_FILE).to_dict())
    return entries


__all__ = [
    "AREA_CRS_NAME",
    "SHAPEFILE_DIR",
    "area_km2",
    "basin_geometry_4326",
    "basin_geojson",
    "basins_catalog",
    "clear_geometry_cache",
    "data_inventory",
    "green_legacy_by_wereda",
    "green_legacy_geojson",
    "green_legacy_summary",
    "layer_provenance",
    "read_layer",
    "to_web",
]
