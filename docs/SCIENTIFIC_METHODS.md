# Scientific Processing Documentation

How every number and layer in this dashboard is produced, with the assumptions and
limitations that go with it.

---

## 1. What this dashboard can and cannot claim

| Capability | Data used | What it supports |
| --- | --- | --- |
| **Near-real-time satellite monitoring** | latest usable Sentinel-2 observations | vegetation and surface-water *indicators*, limited by revisit time, cloud cover and processing delay |
| **Historical environmental monitoring** | archived satellite time series (2017 → present) + gridded climate data | seasonal patterns, long-term trends, period-to-period change |
| **Real-time hydrological monitoring** | gauge / telemetry / institutional feeds | measured river stage, discharge, rainfall — **not connected in this deployment** |

Sentinel-2's 5-day revisit (per satellite), cloud persistence over the Ethiopian highlands in
the main rainy season, and the S2_SR_HARMONIZED processing delay mean this is **not** continuous
river-level or discharge monitoring. Satellite indices are never presented as stage or flow, and
no gauge value is simulated anywhere in this codebase.

---

## 2. Data sources

| Product | Dataset (Earth Engine id) | Native resolution | Notes |
| --- | --- | --- | --- |
| NDVI, EVI, NDWI, MNDWI | `COPERNICUS/S2_SR_HARMONIZED` | 10 m (B2/B3/B4/B8), 20 m (B11) | surface reflectance, harmonised with Landsat |
| Rainfall | `UCSB-CHG/CHIRPS/DAILY` | ~5.5 km (0.05°) | satellite-gauge merged estimate, **not** a gauge record |
| Land surface temperature | `MODIS/061/MOD11A2` | 1 km, 8-day composite | daytime LST |
| Air temperature | `ECMWF/ERA5_LAND/DAILY_AGGR` | ~9 km (0.1°) | reanalysis, not a station reading |
| Evapotranspiration | `MODIS/061/MOD16A2GF` | 500 m, 8-day | gap-filled product |
| Surface soil moisture | `NASA/SMAP/SPL4SMGP/007` | ~9 km | 0-5 cm layer |
| Basin / river corridor geometry | repository `shapefiles/` | vector | see `docs/AUDIT.md` §3 |
| Green Legacy planting sites | repository `shapefiles/Green_legacy.shp` | vector | 21,429 planting records |

All of these are declared once, in `app/indicators.py`. Band names, equations, scales, units and
palettes are never repeated elsewhere in the code, so the API, the map legend and the CSV
exports cannot disagree with each other.

---

## 3. Sentinel-2 processing chain

```
COPERNICUS/S2_SR_HARMONIZED
  └─ filterBounds(region)                  region = basin or river-corridor geometry (EPSG:4326)
  └─ filterDate(start, end)                requested window
  └─ filter(CLOUDY_PIXEL_PERCENTAGE < 70)  scene-level screen (MAX_CLOUD_COVER_PERCENT)
  └─ map(mask_s2_clouds)                   QA60 + SCL masking
  └─ map(divide by 10 000)                 DN → surface reflectance
  └─ map(add_indices)                      NDVI, EVI, NDWI, MNDWI
  └─ median()                              per-pixel temporal composite
  └─ clip(region) → reduceRegion / getMapId
```

### 3.1 Band mapping and equations

| Indicator | Bands | Equation as implemented | Scale | Units |
| --- | --- | --- | --- | --- |
| NDVI | B8 (NIR), B4 (Red) | `(B8 − B4) / (B8 + B4)` | 10 m | dimensionless −1…1 |
| EVI | B8, B4, B2 | `2.5 * ((NIR − RED) / (NIR + 6·RED − 7.5·BLUE + 1))` | 10 m | dimensionless −1…1 |
| NDWI (McFeeters) | B3 (Green), B8 (NIR) | `(B3 − B8) / (B3 + B8)` | 10 m | dimensionless −1…1 |
| MNDWI (Xu 2006) | B3 (Green), B11 (SWIR1) | `(B3 − B11) / (B3 + B11)` | 10 m | dimensionless −1…1 |

B11 is a 20 m band; it is resampled to the 10 m grid when the MNDWI is formed. That resampling is
the only place where resolution is changed, and it is stated in the indicator description shown
in the UI.

These equations are verified numerically in `tests/test_satellite_processing.py`: a scene with
NIR 0.45 / Red 0.10 / Green 0.12 / SWIR 0.20 must return NDVI 0.636364, NDWI −0.578947,
MNDWI −0.25 and EVI 0.603448.

### 3.2 Cloud, shadow and quality masking

Two independent screens are applied per scene, and both must pass:

1. **QA60 bit flags** — bit 10 (opaque cloud) and bit 11 (cirrus) must be unset.
2. **Scene Classification Layer (`SCL`)** — classes removed:
   `1` saturated/defective, `3` cloud shadow, `8` cloud medium probability,
   `9` cloud high probability, `10` thin cirrus, `11` snow.
   Classes retained: `2` dark area, `4` vegetation, `5` bare soil, `6` water, `7` unclassified.

Before masking, scenes whose `CLOUDY_PIXEL_PERCENTAGE` exceeds `MAX_CLOUD_COVER_PERCENT`
(default 70 %) are dropped, which avoids compositing scenes that are mostly cloud.

The exact method is published with every product in `source.mask_method`, so a reader of the API
or a CSV export can see which mask produced the number.

*Limitation:* `SCL` is itself a classification and is imperfect over bright bare soil, sand and
urban surfaces; residual cloud can survive, and cloud shadow can be over-masked in dark valleys.
The per-pixel observation count reported with each statistic is the practical control on this:
a value supported by 2 observations should not be read like one supported by 30.

### 3.3 Temporal compositing

Per-pixel **median** over the masked scenes in the window. The median is used rather than the
mean because it is robust to residual cloud and haze outliers; this is verified in
`tests/test_satellite_processing.py::test_median_composite_is_robust_to_a_cloudy_outlier`.

### 3.4 Valid data and nulls

* A pixel is counted only if it survives the mask **and** the index denominator is non-zero.
* Reductions combine `mean + count + stdDev`, so each statistic carries:
  * `value` — `null` when no pixel is valid (never `0`),
  * `observation_count` — scenes contributing,
  * `valid_pixel_count` — pixels used,
  * `expected_pixel_count` — pixels the region contains at that scale,
  * `coverage_fraction` — `valid_pixel_count / expected_pixel_count`, capped at 1.
* A window with no usable scene returns HTTP 422 `insufficient_observations` for map products and
  `value: null` for statistics and series.
* `MIN_OBSERVATIONS` (default 1) sets how many observations a statistic needs before it is
  published.

### 3.5 Scales

Each product is reduced at its own native scale: 10 m for the Sentinel-2 indices, 5,566 m for
CHIRPS, 1 km for MOD11A2, 11,132 m for ERA5-Land, 500 m for MOD16A2GF, 9 km for SMAP. Mixing
scales between the map and the chart is impossible because both read the same registry entry.

---

## 4. Ancillary products and their scaling

| Indicator | Band | Transform | Resulting unit |
| --- | --- | --- | --- |
| Rainfall | `precipitation` | `sum` over the window | mm per period |
| LST | `LST_Day_1km` | `mean × 0.02 − 273.15` | °C |
| Air temperature | `temperature_2m` | `mean − 273.15` | °C |
| ET | `ET` | `mean × 0.1` | mm per 8-day interval |
| Soil moisture | `sm_surface` | `mean` | m³/m³ |

These transforms are tested (`test_rainfall_is_the_period_sum_not_the_mean`,
`test_lst_is_scaled_from_kelvin_to_celsius`, `test_et_is_scaled_from_kg_per_m2_to_mm`).
An empty ancillary collection returns `null`, not `0` — the previous implementation returned
`ee.Image.constant(0)` for ET and soil moisture, which read as a real measurement.

---

## 5. Time series and change detection

* **Aggregation:** annual or monthly means over the selected region, one value per period.
* **Single round trip:** all periods are reduced server-side and returned together, so a chart
  costs one Earth Engine request instead of one per year.
* **Gaps stay gaps:** a period with no usable observation is returned as `value: null` with
  `data_status: insufficient_observations`. Nothing is interpolated, carried forward or
  smoothed; the UI draws a break in the line (`spanGaps: false`).
* **Sentinel-2 coverage begins 2017-03-28**, so 2015 and 2016 can never contain Sentinel-2 data.
* **Change detection** compares two windows of the *same* indicator, region, mask, scale and
  processing version. The response reports:
  * the difference (comparison minus baseline),
  * percentage change, computed **only** where `|baseline| ≥ 0.05`; otherwise the response says
    why the ratio is not stable instead of printing a meaningless percentage,
  * `comparability_checks` — same indicator/region/scale/version and observation counts for both
    periods,
  * coverage fraction for the comparison period.

**Seasonal bias:** comparing a wet-season window with a dry-season window measures the season,
not a trend. The UI defaults the change detector to matching months
(1 Jun – 1 Sep of consecutive years) and the interpretation notes repeat the caveat.

---

## 6. Surface-water interpretation

NDWI/MNDWI thresholds are used only for *indicative* water extent:

* default threshold `0.0` for both indices (`app/indicators.py: WATER_THRESHOLDS`),
* labelled everywhere as an index threshold, **not** a validated water-body classification,
* no locally validated thresholds, reference water layer or accuracy assessment exists in this
  repository, so no accuracy figure is claimed and none is displayed.

Known confusions: built-up and bare-soil surfaces can raise NDWI; dense vegetation and shadows
affect both indices; a narrow highland river is often narrower than a 10 m pixel, so "water
extent" in a river corridor is better read as a relative index of wetness over time than as an
area in hectares.

---

## 7. Vegetation interpretation

NDVI is used for condition, seasonality and change — not for drought declaration. The dashboard
states this next to the value:

> A decline in NDVI is evidence of reduced greenness only; it is not by itself proof of drought.
> Read it together with rainfall, temperature and soil moisture.

Equivalent seasonal windows should be compared where possible, which the change-detection panel
encourages by defaulting to the same months in consecutive years.

---

## 8. River-corridor monitoring

* Geometry: the project's own digitised river network buffered by **2,000 m**
  (`BUFF_DIST` attribute, identical in all six buffer files).
* Projection of record: EPSG:32637 for five buffers, **EPSG:32636** for Baro-Akobo (documented as
  a per-basin warning).
* Area: computed geometrically in ESRI:102022, never taken from attributes.
* Corridor areas (ESRI:102022): Abay 19,075 km² · Baro-Akobo 19,326 km² · Genale-Dawa 5,364 km² ·
  Omo-Gibe 4,695 km² · Tekeze 16,335 km² · Wabe-Shebelle 8,605 km².
* The corridor and whole-basin views use identical masks, scales and processing versions, so the
  "corridor vs basin" table compares like with like.
* **Not offered:** upstream/middle/downstream reach comparison — the repository contains no reach
  segmentation or stream-order data beyond a single `sorder` value per file, so any such split
  would be invented.

---

## 9. Green Legacy monitoring

The layer is a record of **planting sites**, and it is presented as such.

What the data supports:
* site counts and geometry (21,429 records, 17,288 of them inside the six monitored basins),
* planted area computed **from the geometry**: 651,540 ha (6,515 km²) in total,
* recorded seedling counts (`Seedling` attribute),
* breakdown by region, zone, wereda and programme.

What the data does **not** support:
* tree survival — no survival or monitoring attribute exists,
* species analysis — `Species` is null for all 21,429 records,
* "planted" verification — `Planted` is `0` for every record,
* hectare totals from the `Area_Ha` field — it contains **square metres**
  (`Area_Ha / Shape_Area = 1.000000`); summing it as hectares would give 6.5 billion ha.

Satellite NDVI over these polygons is an independent observation of greenness. It is **not** a
count of trees planted or surviving, and the UI says so wherever the two are shown together.

---

## 10. Hydrological monitoring

No gauge, telemetry or institutional feed is connected to this deployment. The application:

* returns HTTP 501 with an explicit explanation rather than an empty table,
* publishes the exact contract a provider must implement
  (`app/hydrology.py: HydrologyProvider` — `stations()` and
  `observations(station_id, start, end)`),
* lists the required reading fields (`station_id`, `variable`, `timestamp`, `value`, `unit`,
  `quality_flag`, `source`) and quality flags (`good`, `suspect`, `missing`, `delayed`,
  `sensor_failure`),
* never generates simulated stage, discharge or rainfall values.

---

## 11. Geometry, CRS and areas

* Vector data is read with its own CRS, repaired (`buffer(0)` then `make_valid`) and Z values are
  dropped.
* Geometry served to the browser and to Earth Engine is EPSG:4326.
* Areas are computed in **ESRI:102022 (Africa Albers Equal Area Conic)**, an equal-area
  projection, so basin areas are not distorted by UTM zone edges.
* Simplification is applied in metres in the source projection before reprojection:
  150 m for basins, 60 m for corridors, 25 m for Green Legacy sites (all configurable). Every
  GeoJSON response states the tolerance it used.
* Basin area attributes (`SQKM`, `AREA`, `Shape_Area`) are **not** used; the Abay attribute
  disagrees with its geometry by a factor of 13, and the disagreement is published as a warning.

---

## 12. Freshness vocabulary

| Status | Meaning |
| --- | --- |
| `current` | newest valid observation is ≤ `FRESH_DAYS` old (default 16) |
| `delayed` | newest valid observation is older than `FRESH_DAYS` but ≤ `STALE_DAYS` (default 45) |
| `stale` | newest valid observation is older than `STALE_DAYS` |
| `insufficient_observations` | the window contained no usable scene, or fewer than `MIN_OBSERVATIONS` |
| `unavailable` | the data source could not be reached — no value is returned |
| `demonstration` | synthetic data, produced only in demonstration mode and labelled as such |

Three timestamps are always kept apart: `latest_observation` (when the satellite acquired the
data), `processed_at` / `generated_at` (when this application computed the product) and
`dashboard_refresh` (when the panel was last reloaded).

---

## 13. Known limitations

1. Sentinel-2 coverage starts 2017-03-28; earlier years are empty by definition.
2. Cloud cover in the June–September rainy season can leave long gaps, especially in the
   south-west (Baro-Akobo, Genale-Dawa).
3. `S2_SR_HARMONIZED` is available with a processing delay of roughly 1-3 days; the dashboard is
   near-real-time, not real-time.
4. Ancillary products are gridded estimates at 0.05°-0.25° resolution; they cannot resolve
   individual valleys.
5. 2,000 m corridors are wider than most highland rivers, so corridor statistics mix river,
   riparian and adjacent land.
6. Water and vegetation thresholds are literature defaults, not locally calibrated.
7. Basin boundary data is a single polygon per basin; sub-basin analysis is not possible.
8. Green Legacy attributes are partially defective at source (see §9).
