# Repository Audit Report

**Project:** Ethiopian Transboundary River Monitoring Dashboard
**Audited revision:** `08003d2` ("Add files via upload") on `master`
**Audit date:** 2026-11-12
**Scope:** `main.py`, `gee_service.py`, `config.py`, `index.html`, `requirements.txt`,
`shapefiles/` (13 shapefile sets), `.hintrc`, committed `__pycache__`

Severity scale: **critical** (breaks the app or produces wrong/misleading science) ·
**high** (major functional or integrity risk) · **medium** (degraded quality, performance or
maintainability) · **low** (polish).

Every finding below was produced by reading the file or by running a command against the
repository data; the evidence column says what was observed.

---

## 1. Architecture as found

| Layer | File | What it did |
| --- | --- | --- |
| HTTP | `main.py` (250 lines) | FastAPI app, 8 routes, static mount, CORS `*` |
| Processing | `gee_service.py` (1020 lines) | Earth Engine init at import, basins/buffers, cloud mask, indices, 5 ancillary products, stats, time series, tiles, GeoJSON |
| Config | `config.py` | **empty file (0 bytes)** |
| Front-end | `index.html` (1091 lines) | Leaflet + Chart.js from CDN, hard-coded `http://127.0.0.1:8010/api`, sidebar controls, 2 charts |
| Data | `shapefiles/` | 6 basin polygons, 6 river buffers, Green Legacy (21,429 polygons), `shapefiles.rar` |
| Tests | — | none |
| Docs | — | none |

Data flow: browser → `127.0.0.1:8010` → `gee_service` → Earth Engine (project
`enduring-art-494812-u2`, uploaded assets) → GeoJSON/tiles back to Leaflet.

---

## 2. Findings

### Critical

**C1 · The application cannot start as shipped — missing dependency.**
`gee_service.py:3` does `from cachetools import TTLCache`, but `cachetools` is not listed in
`requirements.txt`. A clean install of the pinned requirements followed by `python main.py`
fails with `ModuleNotFoundError: No module named 'cachetools'`.
*Evidence:* `grep -c cachetools requirements.txt` → `0`.
*Action:* dependency list rewritten and pinned; the cache is now implemented in `app/cache.py`
with hit/miss accounting and explicit invalidation.

**C2 · Earth Engine is initialised at import time and can block forever.**
`gee_service.py:12-27` calls `ee.Initialize(project=...)` at module import and, on any
exception, calls `ee.Authenticate()`. `ee.Authenticate()` is interactive: inside a server
process it waits for a browser/terminal that will never appear, so an unauthorised deployment
hangs instead of starting. The project id is also hard-coded.
*Action:* `app/ee_client.py` initialises lazily on first use, never calls `ee.Authenticate()`,
runs every Earth Engine call in a worker thread with a timeout and bounded retries, and reports
availability through `/api/readiness`. Project id and credentials come from the environment.

**C3 · Zero is used as a data value in three places — fabricated observations.**
1. `gee_service.py:335` — `round(result.get('NDVI', 0), 4)`: when the reduction returns no
   value the code substitutes `0`, which is a legitimate NDVI value for bare soil/water.
2. `gee_service.py:296-303` (ET) and `:322-331` (soil moisture) —
   `ee.Image.constant(0)` is returned for an empty collection, i.e. "no data" is rendered as
   "0 mm of evapotranspiration".
3. `gee_service.py:340` — `round(result.get('NDVI', 0), 4)` again for the second/third index.
*Action:* every product now carries a `data_status`
(`current | delayed | stale | insufficient_observations | unavailable | demonstration`),
missing values are `null`, and an empty collection raises a 422 instead of producing a number.

**C4 · Type error on the main statistics endpoint when data is missing.**
`round(result.get('NDVI', 0), 4)` only protects against a *missing key*; Earth Engine returns
the key with value `None` when no pixel is valid, so `round(None, 4)` raises
`TypeError: type NoneType doesn't define __round__` → HTTP 500.
*Evidence:* code path read at `gee_service.py:333-352`.
*Action:* `app/statistics.py` distinguishes "no valid pixel" from "value 0.0" and returns
`value: null` with `data_status: insufficient_observations` (covered by
`tests/test_satellite_processing.py::test_all_masked_pixels_yield_null_mean`).

**C5 · CRS inconsistency across the project's own vector data.**
Read from the `.prj` files: `Abay_basin`, `Genale_Dawa_basin`, `Omo_basin`, `Tekeze_basin`,
`Wabe_Shabale_basin` and all buffers except one are **EPSG:32637** (WGS 84 / UTM 37N), but
`Baro_Akobo_basin_new.shp` is **EPSG:4326** (geographic) and `Baro_Akobo_river_buffer.shp` is
**EPSG:32636** (UTM 36N). Any code that assumes one CRS mixes degrees with metres — the classic
source of wrong areas and misplaced clips. `Baro_Akobo_basin_new` also carries no usable
attributes (`gid=0`, `Area=0`, no basin name).
*Action:* `app/geometry.py` reads each file with its own CRS, reprojects to EPSG:4326 for the
browser/Earth Engine, computes areas in an equal-area projection (ESRI:102022, Africa Albers
Equal Area Conic), and surfaces the mismatch as a per-basin warning in `/api/basins`
(verified by `tests/test_geometry.py::test_baro_akobo_crs_mismatch_is_reported_not_hidden`).

**C6 · Basin areas taken from attributes that contradict the geometry.**
`Abay_basin.shp` declares `SQKM = 15123` while the polygon measures **199,975.5 km²** in
ESRI:102022 — a factor of 13. The same disagreement appears in the other five basins. Using the
attribute would understate every area figure in the dashboard.
*Evidence:* `SQKM=15123` in the attribute table vs. geometric area computed with geopandas.
*Action:* areas are always computed geometrically; the disagreement is reported as a warning.

**C7 · `Green_legacy.Area_Ha` holds square metres, not hectares.**
`Area_Ha / Shape_Area == 1.000000` for all 21,429 features, so the field is square metres despite
its name. Summing it as hectares gives **6,515,407,122 ha** — 59× the land area of Ethiopia. The
correct figure, computed from the geometry, is **651,540 ha (6,515 km²)**.
*Evidence:* ratio quantiles `0.999985 … 1.000073` across the layer.
*Action:* `app/geometry.py` computes `area_ha_computed` from the geometry and publishes the
defect in the payload's `data_quality` block; the attribute is never summed as hectares.

### High

**H1 · Statistical scale of 5,000 m for 10 m data.**
`gee_service.py:322` and `:432` reduce with `scale=5000, bestEffort=True`. For Sentinel-2
indices this discards ~99.96 % of the resolution and, combined with `bestEffort`, lets Earth
Engine choose an arbitrary scale, so two calls can return different numbers for the same
request.
*Action:* each indicator declares its own `scale_m` (10 m for Sentinel-2 indices, native scale
for ancillary products) and it is published in every response and export.

**H2 · Time series mixes sensors between map and chart.**
`gee_service.py:381-390` computes the NDVI *time series* from MODIS MOD13Q1 (250 m, 16-day)
while the NDVI *map* comes from Sentinel-2 (10 m). The chart and the map therefore describe
different products while the UI presents them as one indicator.
*Action:* one indicator = one dataset, declared once in `app/indicators.py` and shown in the UI,
the API payload and the CSV exports.

**H3 · The current year is never shown, and pre-2017 years can never have data.**
`gee_service.py:369-372` builds `range(2015, current_year)`, so the most recent year is excluded
from every chart, while 2015-2016 are always empty because `COPERNICUS/S2_SR_HARMONIZED` begins
on 2017-03-28.
*Action:* the year range is an explicit, validated request parameter (default 2017 → current
year), and empty years are returned as gaps rather than silently dropped.

**H4 · Time series issues one blocking round trip per year.**
`gee_service.py:374-465` loops over years calling `stat.getInfo()` inside the loop: 9-11
sequential HTTP round trips for one chart, each inside an `async def` route, so the event loop
is blocked for the whole time (all other clients stall).
*Action:* the whole series is reduced server-side and returned by a **single** `getInfo()`, and
every blocking call now runs in a worker thread (`run_in_threadpool`).

**H5 · Unreachable code hides a second ET implementation.**
`gee_service.py:303-317` sits after a `return`, so the `MODIS/061/MOD16A2` (non-gap-filled)
branch can never execute. The same pattern repeats in `rainfall_collection`.
*Action:* dead code removed; one implementation per product.

**H6 · Cloud masking is QA60-only.**
`gee_service.py:150-176` masks only the QA60 cloud and cirrus bits. Sentinel-2 SR harmonised
carries the Scene Classification Layer (`SCL`), which is the documented way to remove cloud
*shadow* and defective pixels.
*Action:* `app/satellite.py` masks SCL classes {1, 3, 8, 9, 10, 11} **and** the QA60 bits, and
the mask method string is published with every product
(`tests/test_satellite_processing.py::test_qa60_and_scl_masking_removes_cloudy_pixels`).

**H7 · No valid-pixel accounting anywhere.**
No endpoint reported how many pixels or how many observations stood behind a number, so a
statistics value computed from 3 pixels was indistinguishable from one computed from 3 million.
*Action:* reductions combine `mean + count + stdDev`; responses include `observation_count`,
`valid_pixel_count`, `expected_pixel_count` and `coverage_fraction`.

**H8 · Front-end hard-codes `http://127.0.0.1:8010`.**
`index.html:419` — the dashboard only works when opened against a local server on that exact
port; any reverse proxy, container, HTTPS host or preview environment breaks every request.
*Action:* the front end uses relative URLs (`/api/...`) and is served by the same application.

**H9 · CORS wildcard combined with credentials.**
`main.py:34-40` sets `allow_origins=["*"]` **and** `allow_credentials=True`. Browsers reject
that combination, and it is not a safe production default.
*Action:* origins come from `CORS_ORIGINS`; credentials are enabled only when the origin list is
not a wildcard.

**H10 · Validation errors were reported as 500.**
`main.py:132-158` wraps the handler in `except Exception` after raising `HTTPException(400)` for
an invalid variable, so a bad indicator returned HTTP 500 instead of 400.
*Action:* typed exceptions map to stable status codes (400/404/422/501/502/503) with one error
envelope.

**H11 · Errors were converted into plausible-looking statistics in the front end.**
`index.html` charts and tables render whatever the API returns; with C3/H7 in place a failed or
empty reduction appeared as a zero line or an empty chart that reads as "measured zero".
*Action:* panels render an explicit error/notice; gaps stay gaps in Chart.js (`spanGaps:false`);
toasts report failures.

### Medium

**M1 · `config.py` was empty** — no configuration layer at all; project id, scales and asset
paths were hard-coded in the service. → `app/config.py` (env-driven, documented in
`.env.example`).

**M2 · Duplicated, contradictory dependency pins.** `requirements.txt` lists `fastapi` twice
(0.136.1 and 0.104.1), `earthengine-api` twice (1.7.25 and 0.1.380), `geopandas`, `pyproj`,
`pandas`, `numpy`, `uvicorn`, `python-multipart` all twice with different versions. pip resolves
this to the last pin, silently. → single, consistent, constrained list plus a dev list.

**M3 · No caching of derived geometry; heavy payloads.** `get_geojson()` returned a single
merged geometry with no attributes, and the Green Legacy layer (21,429 polygons ≈ 18 MB as
GeoJSON) would have been sent whole to the browser. → geometry is simplified with a documented
tolerance, cached on disk keyed by source mtime, and Green Legacy is served filtered/paginated
with a truncated flag, plus a wereda-level statistics table (the source data has no wereda
administrative boundaries, so no choropleth is offered).

**M4 · `ee.FeatureCollection.geometry().getInfo()` loses attributes.** The old GeoJSON helper
returned one feature with `properties: {}`, so popups could not show basin name, area or source.
→ per-feature properties with provenance.

**M5 · Unused work on every request.** `get_basin_statistics` and `variable_timeseries` both load
the river buffer (`buffer_zone`) even when the variable does not use it, and the buffer geometry
is fetched from Earth Engine on each call. → geometry is read from disk, cached, and only the
requested region is loaded.

**M6 · 78 invalid geometries in `Green_legacy.shp`.** Reading the layer without repair produces
incorrect area/intersection results. → `buffer(0)` / `make_valid` on load, counted and logged
(`tests/test_geometry.py::test_invalid_geometries_are_repaired_on_load`).

**M7 · Green Legacy attribute fields that cannot be used.** `Species` is 100 % null and
`Planted` is `0` for all 21,429 records; `Purpose`/`Program`/`Ownership` are ~34 % null and use
inconsistent casing (`Community` vs `community`). → not used; documented in the payload.

**M8 · River-buffer field-name typo.** `Baro_Akobo_river_buffer.shp` uses `River_Nmae` where the
other five use `River_Name`. → the loader reads either spelling; the defect is documented.

**M9 · Front-end dead code and unused preload.** `preloadTimeseries()` (11 sequential API calls
per basin) is defined but never called; `loadRiverBuffer()` is called with basin `NONE`. → dead
code removed; layer loading is explicit and cached.

**M10 · Committed build artefacts and a 10 MB archive.** `__pycache__/*.pyc` and
`shapefiles/shapefiles.rar` are tracked. → `.gitignore` added; `__pycache__` removed from the
index (the `.rar` is left in place, see "not changed").

**M11 · No tests, no README, no deployment documentation, no error contract.**

**M12 · Legend covered only 3 of 9 indicators** (`NDVI`, `NDWI`, `LST`) and used qualitative
labels ("Dry"/"Water") without units or thresholds. → legends are generated from the indicator
palette with numeric stops and units.

**M13 · No data-freshness concept.** No endpoint distinguished observation date, processing date
and refresh time. → `/api/freshness`, `latest_observation`, `observation_age_days`,
`processed_at`, `dashboard_refresh` and a freshness policy (`FRESH_DAYS`, `STALE_DAYS`).

### Low

**L1 · `from ee import collection` unused** (`gee_service.py:6`) and shadows the local variable
name used inside the collection builders.
**L2 · `.hintrc` disables `axe/forms` and inline-style hints** rather than fixing them; form
controls lacked labels. → controls are labelled and associated.
**L3 · Footer contains an empty author `<span>`.** → replaced with attribution text.
**L4 · Year slider hard-coded to `max=2025`** — would silently stop at 2025. → period is a
validated date range.
**L5 · Inline CSS/JS in one 1,091-line HTML file.** → `static/index.html`, `static/styles.css`,
`static/app.js`.
**L6 · Chart.js and Leaflet loaded from CDNs** — the dashboard breaks offline and the versions
are not pinned. → vendored under `static/vendor/` at pinned versions (Leaflet 1.9.4, Chart.js
4.4.7, html2canvas 1.4.1).

---

## 3. Data inventory (verified from the files)

| Layer | File | CRS | Features | Notes |
| --- | --- | --- | --- | --- |
| Abay basin | `Abay_basin.shp` | EPSG:32637 | 1 | 199,975 km² (geometric); `SQKM` attribute says 15,123 |
| Abay corridor | `Abay_river_buffer.shp` | EPSG:32637 | 1 | 19,075 km²; `BUFF_DIST = 2000` m; river "Abay" |
| Baro-Akobo basin | `Baro_Akobo_basin_new.shp` | **EPSG:4326** | 1 | 63,011.9 km²; attributes unusable (`gid=0`, `Area=0`) |
| Baro-Akobo corridor | `Baro_Akobo_river_buffer.shp` | **EPSG:32636** | 1 | 19,326.3 km²; field is `River_Nmae` |
| Genale-Dawa basin | `Genale_Dawa_basin.shp` | EPSG:32637 | 1 | 144,233.6 km²; `BASIN_NAME` spelled "Genalle" |
| Genale-Dawa corridor | `Genale_Dawa_river_buffer.shp` | EPSG:32637 | 1 | 5,363.5 km²; river "Genale" |
| Omo-Gibe basin | `Omo_basin.shp` | EPSG:32637 | 1 | 79,487.1 km² |
| Omo-Gibe corridor | `Omo_river_buffer.shp` | EPSG:32637 | 1 | 4,694.5 km²; river "Omo_Gibe" |
| Tekeze basin | `Tekeze_basin.shp` | EPSG:32637 | 1 | 61,703.5 km² |
| Tekeze corridor | `Tekeze_river_buffer.shp` | EPSG:32637 | 1 | 16,335.2 km² |
| Wabe-Shebelle basin | `Wabe_Shabale_basin.shp` | EPSG:32637 | 1 | 193,635.8 km²; `BASIN_NAME` "Shebelle" |
| Wabe-Shebelle corridor | `Wabe_Shabale_river_buffer.shp` | EPSG:32637 | 1 | 8,604.9 km² |
| Green Legacy sites | `Green_legacy.shp` | EPSG:32637 | 21,429 | Polygon **Z**; 78 invalid; `Area_Ha` is m²; `Species` null; `Planted`=0 |

Provenance from the `.shp.xml` lineage: basin data originates from an `Ethio_Watershed` layer in
**Adindan / UTM 37N** (Clarke 1880 RGS) projected to WGS 84 / UTM 37N in ArcGIS; Green Legacy
layers derive from 2017 Green Legacy Initiative "One Day" plantation files. Areas above are
computed in ESRI:102022 (Africa Albers Equal Area Conic).

Sites per basin (spatial join, UTM 37N): Abay 7,310 · Baro-Akobo 3,718 · Omo 2,488 ·
Wabe-Shebelle 1,706 · Genale-Dawa 1,057 · Tekeze 1,009 · total inside the six basins 17,288 of
21,429.

---

## 4. Things deliberately **not** changed

* **Datasets.** Sentinel-2 `COPERNICUS/S2_SR_HARMONIZED`, CHIRPS, MOD11A2, ERA5-Land, MOD16A2GF
  and SMAP are all retained; only masking, scale, bookkeeping and error handling changed.
* **Basin set and boundaries.** All six basins and their buffers are kept exactly as shipped; no
  geometry was edited. CRS handling is done at read time, not by rewriting the files.
* **Green Legacy layer.** Kept and now served from disk with real attributes.
* **Project identity.** Title, SSGI attribution, `uvicorn main:app` entry point and the
  `/api/basin/...`, `/api/tiles/...`, `/api/buffer/...` route shapes are preserved (legacy
  `/api/tiles/{basin}/{indicator}/{year}` still works).
* **`shapefiles/shapefiles.rar`** is left in the repository. It is redundant with the extracted
  shapefiles, but deleting data without an explicit instruction is out of scope; it is flagged
  here instead.
* **Original sources** are preserved verbatim in `legacy/` for rollback (see
  `docs/CHANGELOG.md`).

---

## 5. Residual risks and open items

| # | Item | Status |
| --- | --- | --- |
| R1 | Live Earth Engine credentials are not available in this environment, so satellite products could not be executed against the real service. | **Blocked** — code-level tests use a numerical fake; see `docs/TESTING.md` |
| R2 | The uploaded Earth Engine assets (`projects/enduring-art-494812-u2/assets/...`) could not be inspected. Statistics now use the shapefile geometry directly, so the assets are no longer required for zonal statistics. | Documented in `docs/DEPLOYMENT.md` |
| R3 | No gauge/telemetry feed exists for this deployment, so hydrological monitoring is an interface, not a module with data. | Implemented as a documented integration point (HTTP 501 + contract) |
| R4 | `Area_Ha`, `Species`, `Planted` and the basin `SQKM`/`AREA` attributes are defective at source. The application works around them and reports the defect, but the underlying files should be corrected by the data owner. | Reported, not silently fixed |
| R5 | Upstream/middle/downstream segmentation data does not exist in the repository, so reach-level comparison is not offered (only whole-basin and river-corridor views). | Not implemented — no supporting data |
| R6 | Water thresholds (`NDWI/MNDWI = 0`) are literature defaults, not locally validated. Water extent is labelled indicative. | Documented in `docs/SCIENTIFIC_METHODS.md` |
