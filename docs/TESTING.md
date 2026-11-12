# Testing and Verification Report

**Executed:** 2026-11-12 · **Environment:** Python 3.11 sandbox, no Earth Engine credentials,
no outbound access to `earthengine.googleapis.com`.

Every result below was produced by running the command shown. Nothing is marked PASS that was
not executed.

---

## 1. How to reproduce

```bash
source .venv/bin/activate
python -m pytest tests -q                    # unit + API tests
node tests/frontend_smoke.mjs                # front-end test against a running server
```

The front-end test needs the API running:

```bash
ALLOW_DEMO_MODE=true uvicorn main:app --host 0.0.0.0 --port 8000 &
node tests/frontend_smoke.mjs
```

---

## 2. Test strategy

Earth Engine credentials are not available in this environment, so the processing chain is
exercised against `tests/fake_ee.py` — a small numpy-backed stand-in for the `ee` API
(collections, filtering, masking, reductions, `Algorithms.If`, `Feature`/`FeatureCollection`,
`getMapId`). It is installed through `ee_client.set_module()`, so the **application code under
test is the real `app/satellite.py`, `app/statistics.py`, `app/timeseries.py` and `app/tiles.py`**
— not a re-implementation.

Scenes carry known reflectances, so expected index values are computed by hand:
NIR 0.45 / Red 0.10 / Green 0.12 / SWIR 0.20 → NDVI 0.636364, NDWI −0.578947, MNDWI −0.25,
EVI 0.603448. Band, scaling and mask changes therefore fail the suite.

Vector-data tests run against the real shapefiles in `shapefiles/`, and API tests use FastAPI's
`TestClient` against the real ASGI app.

---

## 3. Results

### 3.1 Python suite

Command: `python -m pytest tests -q`
Result: **`100 passed, 3 warnings in 15.03s`** (re-run after the follow-up fixes in §4)

| File | Tests | Result | Covers |
| --- | --- | --- | --- |
| `tests/test_indicators.py` | 23 | PASS | band mapping, equations, scales, units, palettes, alias resolution, interpretation caveats |
| `tests/test_satellite_processing.py` | 19 | PASS | NDVI/EVI/NDWI/MNDWI values, DN→reflectance scaling, QA60 + SCL masking, cloud-threshold screening, median compositing, ancillary scaling, empty-collection handling, statistics with coverage/freshness |
| `tests/test_geometry.py` | 15 | PASS | real shapefiles: CRS mismatch reporting, equal-area areas, GeoJSON validity (2-D, closed rings), buffer distance, invalid-geometry repair, `Area_Ha` = m², Green Legacy totals/subsetting/capping |
| `tests/test_timeseries.py` | 5 | PASS | annual values, gaps preserved, monthly aggregation, ancillary scaling, per-period observation counts |
| `tests/test_api.py` | 32 | PASS | health/readiness, config, vector routes, validation (400/404/422), statistics/tiles/change payloads, overview with and without Earth Engine, hydrology 501, demonstration labelling, cache admin, report HTML, static assets, Green Legacy summary/points/wereda-table shape |
| `tests/test_exports.py` | 6 | PASS | CSV metadata, provenance, empty cells for missing values, missing metadata marked `unavailable`, comparability reporting |
| **Total** | **100** | **PASS** | |

### 3.2 Front-end test

Command: `node tests/frontend_smoke.mjs` (loads the real `static/index.html` and `static/app.js`
in jsdom against the live API)
Result: **exit 0**

```json
{
  "ok": true,
  "tiles_drawn": 3,
  "charts_drawn": 1,
  "series_points": 3,
  "demo_banner_visible": true,
  "ee_status": "Earth Engine unavailable",
  "failures": []
}
```

Asserted: 6 basins in the selector and overview table, 9 indicator chips, KPI cards, method
cards, legend text, region hint, footer API version, demonstration banner visible only when the
API serves demonstration data, time-series metadata rendered, Green Legacy KPIs and limitations,
hydrology "not connected" notice, only `https://` tile URLs drawn, and **zero browser console
errors**.

### 3.3 Manual verification of the running server

Command: `uvicorn main:app --host 0.0.0.0 --port 8000` with `ALLOW_DEMO_MODE=true`

| Request | Result |
| --- | --- |
| `GET /` | 200, 11,808 bytes |
| `GET /api/health` | 200 |
| `GET /api/config` | 200, 8,321 bytes |
| `GET /api/overview` | 200, `basin_count: 6`, all basins `data_status: unavailable` (no Earth Engine) |
| `GET /api/basins` | 200, six basins with equal-area areas and CRS warnings |
| `GET /api/basin/abay/stats` | 200 with `data_status: demonstration` and the warning text |
| `GET /api/tiles/abay/ndvi` | **503 `earth_engine_unavailable`** — raster products are never synthesised |
| `GET /api/hydrology/stations` | **501 `hydrology_not_connected`** |
| `GET /api/green-legacy/by-wereda` | 200, **147,748 bytes**, 1,398 statistics records, no geometry |
| `GET /api/freshness`, `/api/cache` | 200 |
| `GET /report?basin=tekeze&indicator=ndvi` | 200, 5,427 bytes |
| `GET /api/basin/abay/stats?format=csv` | 200, metadata header populated (`basin_name`, `region`, `region_area_km2`) |
| `GET /api/basin/abay/timeseries/ndvi?...&format=csv` | 200, metadata header populated |
| `POST /api/basin/abay/change.csv` | **503 `earth_engine_unavailable`** — difference products are never synthesised |
| `GET /api/basin/nope/stats` | **404 `unknown_basin`** with the list of valid basin ids |
| `GET /static/app.js`, `/static/styles.css` | 200 |

---

## 4. Bugs found and fixed while testing

These were found by executing the code, not by reading it:

| # | Bug | Found by | Fix |
| --- | --- | --- | --- |
| 1 | `app/timeseries.py` parsed ancillary reductions using the source band name (`LST_Day_1km`) while the scaling step had renamed the band to `LST`, so every ancillary series returned `null` | `test_ancillary_series_applies_documented_scaling` | track the output band name separately |
| 2 | One empty year aborted the whole multi-year request: reducing an empty collection has no bands to reduce | `test_missing_year_is_reported_as_a_gap_not_a_value` | per-period reduction guarded by `ee.Algorithms.If(subset.size().gt(0), …)` |
| 3 | `_parse_series` derived the indicator from the band name (`get_indicator("lst_day_1km")` → `KeyError`) | same test | pass the indicator object through |
| 4 | EVI evaluated on unscaled DN instead of reflectance would return ~1e-4 instead of ~0.6 — the fake now stores DN as S2_SR_HARMONIZED does, which pins the requirement | `test_evi_expression_is_evaluated_with_correct_bands` | verified the chain divides by 10,000 before the indices |
| 5 | Image-minus-image arithmetic (difference maps) had no valid-pixel intersection rule | `test_change_detection_reports_comparability` | masks are intersected for image operands |
| 6 | Concurrent panel refreshes could double-append table rows | `frontend_smoke.mjs` | tables are built off-DOM and swapped in with `replaceChildren`; bootstrap is re-entrancy guarded |
| 7 | Demonstration CSV exports wrote empty metadata lines (`# region_area_km2,`, `# basin_name,`) because the demo payloads lacked the keys the exporter reads | HTTP sweep of `?format=csv` | demo payloads now carry `region_area_km2`, `basin_name`, `region`, `region_label` |
| 8 | `GET /api/green-legacy/by-wereda` returned 4.3 MB and dissolved scattered planting-site polygons into shapes that looked like wereda administrative boundaries | HTTP sweep of the endpoint | returns a statistics table (147,748 bytes) with an explicit note that wereda boundaries are absent from the source data |

---

## 5. Blocked and not-tested items

| Item | Status | Reason / what is needed |
| --- | --- | --- |
| Live Earth Engine initialisation and authentication | **BLOCKED** | no credentials and no network route to `earthengine.googleapis.com` in this environment. Needs `ee.Authenticate()` or a service-account key |
| Real Sentinel-2 statistics for the six basins | **BLOCKED** | as above; code path is covered by the fake, but actual values, latencies and quota behaviour are unverified |
| Real Earth Engine tile rendering | **BLOCKED** | `getMapId()` cannot be called; the URL contract is asserted against the fake |
| Uploaded Earth Engine assets (`projects/enduring-art-494812-u2/assets/...`) | **NOT TESTED** | not reachable. Statistics no longer depend on them |
| Production gauge/telemetry feed | **NOT TESTED** | no feed exists; only the integration contract is tested (501 + contract payload) |
| Multi-worker deployment, Redis-backed cache | **NOT TESTED** | single-process sandbox |
| TLS reverse proxy behaviour | **NOT TESTED** | no proxy in the sandbox |
| Browser rendering (real map tiles, canvas capture for PNG export) | **NOT TESTED** | jsdom has no rendering context; map PNG export depends on basemap CORS and must be verified in a real browser |

### Residual risk

* **Scale/precision of real reductions** — `bestEffort=True` lets Earth Engine choose the working
  scale under memory pressure. Values are reported with their `scale_m` and pixel counts, but
  exact reproducibility of a real reduction is not guaranteed by Earth Engine.
* **Latency** — a full-basin Abay composite at 10 m with a 90-day window may exceed the 90 s
  timeout on a cold cache. Raise `EE_TIMEOUT_SECONDS` or narrow the window; the timeout produces
  a 502 rather than a wrong value.
* **Map PNG export** — cross-origin basemap tiles can taint the canvas. The handler catches this
  and reports it instead of silently exporting a blank image.

---

## 6. Recommended verification once credentials are available

```bash
export EE_PROJECT_ID=<project>
python - <<'PY'
import ee
ee.Initialize(project=__import__("os").environ["EE_PROJECT_ID"])
print("assets reachable:", ee.data.numAssets({"type": "FOLDER"})["count"])
PY

uvicorn main:app --host 0.0.0.0 --port 8000
curl -s localhost:8000/api/readiness | python -m json.tool      # expect 200 "ready"
curl -s "localhost:8000/api/basin/abay/stats?start=2026-08-01&end=2026-11-01" | python -m json.tool
curl -s -o /dev/null -w '%{http_code}\n' "localhost:8000/api/tiles/abay/ndvi"   # expect 200
```

Check that `observation_count > 0`, `coverage_fraction` is plausible for the window, and
`latest_observation` is within the last few days. Then compare the dashboard's NDVI for a
cloud-free month against the same composite in the Earth Engine Code Editor.
