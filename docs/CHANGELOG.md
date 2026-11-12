# Change Log and Rollback Guidance

**From:** `08003d2` ("Add files via upload") on `master`
**To:** branch `arena/7c225e69-transboundary-river-monitoring`

---

## 1. Files changed

### Moved (verbatim, for rollback)

| Original | Now | Reason |
| --- | --- | --- |
| `main.py` | `legacy/main.py` | replaced by the packaged application |
| `gee_service.py` | `legacy/gee_service.py` | replaced by modular services |
| `index.html` | `legacy/index.html` | replaced by `static/` |
| `__pycache__/*.pyc` | *(removed from git)* | build artefacts |
| `config.py` (0 bytes) | *(deleted)* | empty placeholder, replaced by `app/config.py` |

### Added — application

| File | Purpose |
| --- | --- |
| `app/__init__.py` | package metadata |
| `app/config.py` | env-driven settings, basin registry, alias resolution |
| `app/indicators.py` | single source of truth for bands, equations, scales, units, palettes |
| `app/geometry.py` | shapefile → validated, reprojected GeoJSON; equal-area areas; caches |
| `app/ee_client.py` | lazy Earth Engine init, availability, timeouts, retries, threadpool |
| `app/satellite.py` | S2 masking (QA60 + SCL), indices, compositing, ancillary products |
| `app/statistics.py` | zonal statistics with valid-pixel accounting and data statuses |
| `app/timeseries.py` | annual/monthly series in one round trip, gaps preserved |
| `app/tiles.py` | tile URLs, visualisation, difference products, comparability checks |
| `app/service.py` | orchestration, caching, demo/unavailable decisions |
| `app/exports.py` | CSV builders with metadata and provenance |
| `app/report.py` | printable analytical summary (browser → PDF) |
| `app/hydrology.py` | gauge integration contract; 501 when not connected |
| `app/demo.py` | isolated, labelled demonstration data (off by default) |
| `app/cache.py` | TTL cache with explicit keys, stats and invalidation |
| `app/errors.py` | typed errors, HTTP mapping, `DataStatus` vocabulary |
| `app/logging_config.py` | JSON structured logging (no secrets) |
| `app/schemas.py` | request validation models, error envelope |
| `app/main.py` | FastAPI app and routes |
| `main.py` | thin compatibility entry point (`uvicorn main:app` still works) |
| `static/index.html`, `static/styles.css`, `static/app.js` | dashboard front end |
| `static/vendor/` | pinned Leaflet 1.9.4, Chart.js 4.4.7, html2canvas 1.4.1 |

### Added — tests, docs, tooling

| File | Purpose |
| --- | --- |
| `tests/fake_ee.py` | numerical Earth Engine stand-in (test infrastructure) |
| `tests/test_indicators.py`, `test_satellite_processing.py`, `test_timeseries.py`, `test_geometry.py`, `test_api.py`, `test_exports.py` | 99 tests |
| `tests/frontend_smoke.mjs` | front-end test in jsdom against the live API |
| `tests/conftest.py`, `tests/__init__.py` | fixtures |
| `docs/AUDIT.md`, `SCIENTIFIC_METHODS.md`, `API.md`, `DEPLOYMENT.md`, `TESTING.md`, `CHANGELOG.md` | deliverable documentation |
| `README.md` | project overview and quick start |
| `requirements.txt` (rewritten), `requirements-dev.txt` | constrained dependencies |
| `.env.example`, `.gitignore`, `scripts/build_geojson.py` | configuration, hygiene, cache warming |

### Unchanged

`shapefiles/**` (all data), `DASHBOARD.jpg`, `.hintrc`.

---

## 2. Why the major changes were made

| Change | Reason | Reference |
| --- | --- | --- |
| Package the backend under `app/` | a 1,020-line module mixing auth, assets, masking, statistics, tiles and GeoJSON could not be tested or maintained | Audit §1 |
| Lazy, non-interactive Earth Engine init | `ee.Authenticate()` at import can hang a server forever | C2 |
| Remove `0` as a placeholder for missing data | fabricated observations presented as measurements | C3, C4 |
| Read geometry from shapefiles with per-file CRS | mixed EPSG:32637/32636/4326 silently corrupts areas and clips | C5 |
| Compute areas geometrically in an equal-area CRS | basin area attributes disagree with geometry by up to 13× | C6 |
| Treat `Area_Ha` as square metres | summing it as hectares gives 6.5 billion ha | C7 |
| Per-indicator native scales | 5,000 m reductions of 10 m data discard the signal | H1 |
| One dataset per indicator | chart used MODIS while the map used Sentinel-2 for "NDVI" | H2 |
| Single round trip per time series | 9-11 blocking `getInfo()` calls per chart, blocking the event loop | H4 |
| SCL + QA60 masking | cloud shadow and defective pixels were not removed | H6 |
| Valid-pixel accounting | a 3-pixel statistic looked identical to a 3-million-pixel one | H7 |
| Relative API URLs in the front end | hard-coded `127.0.0.1:8010` breaks every non-local deployment | H8 |
| `CORS_ORIGINS` + credentials only for explicit origins | `*` with credentials is rejected by browsers and unsafe | H9 |
| Typed errors → stable status codes | validation errors returned 500 | H10 |
| Vendor the JS libraries | CDN dependence and unpinned versions | L6 |
| Demonstration data isolated and off by default | interface development must never masquerade as operations | §9 of the brief |

---

## 3. Compatibility impact

| Area | Impact |
| --- | --- |
| Launch command | unchanged — `uvicorn main:app` / `python main.py` |
| Port | default changed **8010 → 8000** (`PORT` env to restore) |
| Routes | all previous routes still resolve; `/api/tiles/{basin}/{var}/{year}` kept |
| Indicator ids | lower-case ids; upper-case still accepted |
| Response payloads | **additive**: new fields (`data_status`, provenance, pixel counts). Clients reading only `NDVI`/`tile_url` keep working, except that a missing value is now `null` instead of `0` — that is the intended fix |
| Front end | replaced; the old single-file UI is in `legacy/index.html` |
| Earth Engine assets | no longer required for statistics (geometry comes from shapefiles) |
| Configuration | now env-driven; `config.py` was empty before, so nothing is lost |

---

## 4. How to revert safely

The original code is untouched in `legacy/`. To roll back completely:

```bash
git revert <merge-commit>            # or: git checkout 08003d2 -- main.py gee_service.py index.html
cp legacy/main.py main.py
cp legacy/gee_service.py gee_service.py
cp legacy/index.html index.html
pip install cachetools               # the original requirement list omits it (finding C1)
uvicorn main:app --port 8010
```

To revert only part of the change:

| Want to revert | Command |
| --- | --- |
| Front end only | `cp legacy/index.html static/index.html` (the API stays) |
| Backend only | restore `legacy/*.py` and run on port 8010 (the old UI expects it) |
| Cloud masking | `app/satellite.py:SCL_REMOVED_CLASSES = ()` → QA60-only behaviour |
| Demonstration data | `ALLOW_DEMO_MODE=false` (already the default) |

Rollback notes:

* No dataset was modified, so reverting cannot corrupt data.
* `data/cache/` is derived; delete it any time (`rm -rf data/cache`).
* No secrets were added to the repository.
* If you revert the backend, also revert the front end — the old UI calls the old response
  shapes and the hard-coded `127.0.0.1:8010` origin.

---

## 5. Outstanding items

| # | Item | Owner / next step |
| --- | --- | --- |
| 1 | Verify satellite products against live Earth Engine | needs credentials — see `docs/TESTING.md` §6 |
| 2 | Correct `Area_Ha`, `Species`, `Planted` and the basin `SQKM`/`AREA` attributes at source | data owner |
| 3 | Re-project `Baro_Akobo_basin_new.shp` to EPSG:32637 and add basin attributes | data owner (the app currently compensates at read time) |
| 4 | Connect a gauge/telemetry feed | implement `app/hydrology.HydrologyProvider`, set `HYDROLOGY_PROVIDER` |
| 5 | Remove `shapefiles/shapefiles.rar` (10 MB, redundant) | awaiting explicit instruction |
| 6 | Reach-level (upstream/middle/downstream) monitoring | needs reach segmentation data that does not exist in the repository |
| 7 | Locally validated water thresholds | needs reference water data and accuracy assessment |
