# API Reference

Base URL: the application origin (the front end uses relative paths, e.g. `/api/overview`).
Interactive documentation is served at **`/api/docs`** (OpenAPI schema at `/api/openapi.json`).

Version: 5.0.0 · Every product response includes `data_status` and provenance.

---

## 1. Conventions

**Basin ids** — `abay`, `baro_akobo`, `genale_dawa`, `omo`, `tekeze`, `wabe_shabale`.
Aliases are accepted (`Abay`, `Abay Basin`, `blue nile`, `Wabe Shabale`, …) and resolved
server-side by `app/config.py:resolve_basin_id`.

**Indicator ids** — `ndvi`, `evi`, `ndwi`, `mndwi`, `rainfall`, `lst`, `air_temp`, `et`,
`soil_moisture`. Aliases (`precipitation`, `air temp`, `evapotranspiration`, …) are accepted.

**Regions** — `basin` (whole basin) or `buffer` (2,000 m river corridor).

**Dates** — ISO `YYYY-MM-DD`. Defaults: rolling window of `DEFAULT_WINDOW_DAYS` (90) ending today.

**Error envelope** — every non-2xx response:

```json
{
  "error": {
    "code": "insufficient_observations",
    "message": "No cloud-free NDVI observation for the selected window",
    "detail": { "window": { "start": "2026-07-01", "end": "2026-08-01" }, "region": "basin" },
    "timestamp": "2026-11-12T09:00:00Z"
  }
}
```

| HTTP | `code` | Meaning |
| --- | --- | --- |
| 400 | `invalid_request` | bad date, inverted window, unknown parameter value |
| 404 | `unknown_basin` / `unknown_indicator` | id does not exist (the list is returned in `detail`) |
| 422 | `insufficient_observations` | the window contained no usable observation |
| 501 | `hydrology_not_connected` | no gauge/telemetry feed is configured |
| 502 | `earth_engine_error` | Earth Engine rejected or failed the computation |
| 503 | `earth_engine_unavailable` | Earth Engine is not reachable or not authenticated |

---

## 2. Operational endpoints

### `GET /api/health`
Application health only. `200` while the process can serve requests, regardless of data sources.

```json
{ "status": "ok", "version": "5.0.0", "environment": "development", "time": "2026-11-12T09:00:00Z" }
```

### `GET /api/readiness`
Data-source readiness, reported separately from application health.
`200` when Earth Engine and the vector data are both usable, otherwise **`503`** with
`status: "degraded"`.

```json
{
  "status": "degraded",
  "application": { "healthy": true, "version": "5.0.0" },
  "earth_engine": { "available": false, "project": "…", "error": "…", "calls": 0 },
  "vector_data": { "available": true, "basin_count": 6 },
  "hydrology": { "connected": false, "…": "…" },
  "demonstration_mode": { "enabled": false, "active": false },
  "cache": { "products": { "size": 0, "hit_rate": null }, "geometry": { "…": "…" } }
}
```

### `GET /api/status`
Backwards-compatible status line kept for existing integrations.

### `GET /api/config`
Everything the front end needs: basins with their available regions, all indicators (bands,
equation, scale, units, palette, caveat), the freshness policy and status labels, and the current
Earth Engine state.

---

## 3. Vector data (works without Earth Engine)

### `GET /api/basins`
Per basin: `area_km2`, `buffer_area_km2`, `buffer_distance_m`, `bbox`, `centroid`, source
filenames, source CRS, attribute table, and `warnings` (CRS mismatch, attribute/geometry
disagreement).

### `GET /api/basin/{basin}/geojson?region=basin|buffer`
FeatureCollection in EPSG:4326 with per-feature properties (`basin_name`, `area_km2`,
`source_file`, `source_crs`, `simplify_tolerance_m`, and `buffer_distance_m` for corridors) plus
a `provenance` block.

### `GET /api/buffer/{basin}/geojson`
Legacy alias for `region=buffer`.

### `GET /api/green-legacy/geojson?basin=&mode=points|polygons&limit=1..25000`
Planting sites as GeoJSON. Defaults to centroids (`points`) because the full polygon layer is
~18 MB; `provenance.truncated` reports whether the `limit` cut the result, and
`provenance.note` states that NDVI does not measure tree counts.

### `GET /api/green-legacy/summary?basin=`
Counts, area (computed from geometry) and recorded seedlings overall and by region/programme,
plus a `data_quality` block describing the `Area_Ha` (m²) defect and the unusable `Species` /
`Planted` fields.

### `GET /api/green-legacy/by-wereda`
Planting effort aggregated to region / zone / wereda: sites, hectares (computed from geometry)
and recorded seedlings — 1,398 records, ~165 kB.

**No geometry is returned.** The repository holds planting-site polygons, not wereda
administrative boundaries; dissolving scattered site polygons would look like a boundary while
being nothing of the sort. The response says so in `provenance.geometry` and points to
`/api/green-legacy/geojson` for mapping the sites themselves.

### `GET /api/data/inventory`
Every vector dataset with its CRS, feature count, invalid-geometry count and warnings.

---

## 4. Satellite products (require Earth Engine)

### `GET /api/overview?start=&end=`
Executive overview: all six basins with core indicators (NDVI, NDWI, MNDWI), areas, observation
counts, coverage, latest observation, per-basin `data_status`, the Earth Engine state, the
freshness policy and the scope note. Basin geometry and areas are always returned; indicator
values are omitted (with `data_status: unavailable`) when Earth Engine is unreachable.

### `GET /api/basin/{basin}/stats`
| Parameter | Default | Notes |
| --- | --- | --- |
| `indicators` | `ndvi,ndwi,mndwi` | comma separated |
| `start`, `end` | rolling 90 days | ISO dates |
| `region` | `basin` | `basin` or `buffer` |
| `format` | — | `csv` returns a downloadable CSV |

```json
{
  "basin_id": "tekeze",
  "region": "basin",
  "region_area_km2": 61703.5,
  "window": { "start": "2026-08-14", "end": "2026-11-12" },
  "indicators": {
    "ndvi": {
      "value": 0.61, "std_dev": 0.07, "units": "dimensionless (-1 to 1)",
      "data_status": "current", "observation_count": 14,
      "valid_pixel_count": 580000, "expected_pixel_count": 617035, "coverage_fraction": 0.94,
      "latest_observation": "2026-11-05T10:30:00Z", "observation_age_days": 7.1,
      "processed_at": "2026-11-12T09:00:00Z",
      "source": { "dataset": "COPERNICUS/S2_SR_HARMONIZED", "bands": ["B8","B4"],
                  "equation": "(B8 - B4) / (B8 + B4)", "scale_m": 10,
                  "mask_method": "…SCL classes…plus QA60…", "compositing": "per-pixel median…",
                  "processing_version": "2026-11-scl-qa60-median" },
      "caveat": "A decline in NDVI is evidence of reduced greenness only…"
    }
  },
  "availability": { "usable_indicator_count": 3, "latest_observation": "…", "coverage_fraction_mean": 0.94 },
  "data_status": "current"
}
```

### `GET /api/basin/{basin}/timeseries/{indicator}`
| Parameter | Default | Notes |
| --- | --- | --- |
| `start_year`, `end_year` | 2017 / current year | max 30 years |
| `aggregation` | `year` | `year` or `month` |
| `region` | `basin` | |
| `format` | — | `csv` |

Returns one point per period with `value` (`null` for gaps), `observation_count`,
`valid_pixel_count`, `latest_observation`, `data_status`, plus `gaps`, `source`, `interpolation`
(always `"none — gaps are reported as missing observations"`) and `processing_seconds`.

### `GET /api/tiles/{basin}/{indicator}?start=&end=&region=`
Map layer. Returns `tile_url` (Earth Engine tile template), `visualization` (min/max/palette used
for the legend), `observation_count`, `latest_observation`, `data_status`, `caveat` and `source`.

**`422`** when the window has no usable observation. **`503`** when Earth Engine is unavailable —
raster products are never synthesised, including in demonstration mode.

### `GET /api/tiles/{basin}/{indicator}/{year}`
Legacy calendar-year form of the above.

### `POST /api/basin/{basin}/change`
```json
{
  "indicator": "ndvi",
  "baseline_start": "2025-06-01", "baseline_end": "2025-09-01",
  "comparison_start": "2026-06-01", "comparison_end": "2026-09-01",
  "region": "basin", "include_map": true
}
```
Returns baseline/comparison statistics, the difference, `percentage_change` (only where
`|baseline| ≥ 0.05`, otherwise an explanation), `comparability_checks` (same indicator, region,
scale, processing version; observation counts for both periods; coverage) and, when
`include_map` is true, a difference `tile_url` with a diverging palette.

### `GET /api/freshness?basin=&start=&end=`
Per basin: `data_status`, `latest_observation`, `youngest_observation_age_days`,
`processing_timestamp`, alongside `dashboard_refresh` and the freshness policy.

---

## 5. Hydrology (integration point)

### `GET /api/hydrology/integration`
`200` — describes the contract: expected variables, station fields, reading fields, quality flags
and whether a provider is connected.

### `GET /api/hydrology/stations` and `GET /api/hydrology/stations/{id}/observations`
**`501 hydrology_not_connected`** until a provider is registered
(`HYDROLOGY_PROVIDER=<name>`, implementing `app/hydrology.py:HydrologyProvider`). The response
explains how to connect a feed and states that simulated data is never produced.

---

## 6. Exports and reporting

| Endpoint | Result |
| --- | --- |
| `GET /api/basin/{basin}/stats?format=csv` | statistics CSV |
| `GET /api/basin/{basin}/timeseries/{indicator}?format=csv` | time-series CSV |
| `POST /api/basin/{basin}/change.csv` | change-detection CSV |
| `GET /report?basin=&indicator=&start=&end=&start_year=&end_year=` | printable HTML summary → browser "Save as PDF" |

Every CSV starts with `#` metadata lines carrying the selection (basin, region, indicator,
window, aggregation) and the provenance (dataset, bands, equation, scale, mask method,
compositing, processing version, interpolation policy, generation time). Missing values are empty
cells — never zeros.

---

## 7. Cache administration

* `GET /api/cache` — product and geometry cache statistics (size, hits, misses, hit rate, TTL).
* `POST /api/cache/clear` — clears both caches. Useful after re-uploading vector data.

Cache keys encode every input that affects a product (basin, region, indicators, window, cloud
threshold, `PROCESSING_VERSION`), so changing processing invalidates results automatically.
Geometry cache entries are keyed on the source file mtime.

---

## 8. Compatibility with the previous API

| Old route | Status |
| --- | --- |
| `GET /api/status` | kept |
| `GET /api/basin/{name}/stats` | kept (richer payload) |
| `GET /api/basin/{name}/geojson` | kept |
| `GET /api/buffer/{name}/geojson` | kept |
| `GET /api/green-legacy/geojson` | kept (now filtered/paginated) |
| `GET /api/basin/{name}/timeseries/{VARIABLE}` | kept; variables are lower-case ids, upper-case still accepted |
| `GET /api/tiles/{name}/{VARIABLE}/{year}` | kept; the query-parameter form is preferred |

Older `main.py` / `gee_service.py` / `index.html` are preserved verbatim under `legacy/`.
