/* ==========================================================================
   Ethiopian Transboundary River Monitoring Dashboard — front-end application

   Design rules enforced here:
     * every value on screen comes from the API and is shown with its
       observation date, observation count and data status;
     * a failure is shown as a failure (toast + panel notice), never as a zero
       or an empty chart that looks like a measurement;
     * raster layers are only drawn when Earth Engine returned a real tile URL;
     * demonstration payloads draw a persistent banner and are labelled inline.
   ========================================================================== */

"use strict";

const API = "/api";

const state = {
  config: null,
  readiness: null,
  basin: "abay",
  region: "basin",
  indicator: "ndvi",
  window: { start: null, end: null },
  layers: {},
  charts: {},
  cache: {},
  demo: false,
  started: false,
  bootstrapping: false,
};

/* --------------------------------------------------------------- utilities */

class ApiError extends Error {
  constructor(message, code, status, detail) {
    super(message);
    this.code = code || "error";
    this.status = status;
    this.detail = detail || {};
  }
}

async function api(path, options) {
  const response = await fetch(API + path, options);
  const contentType = response.headers.get("content-type") || "";
  const isJson = contentType.includes("application/json");

  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    let code = "http_error";
    let detail = {};
    if (isJson) {
      const body = await response.json();
      if (body && body.error) {
        message = body.error.message || message;
        code = body.error.code || code;
        detail = body.error.detail || {};
      }
    } else {
      message = (await response.text()).slice(0, 200) || message;
    }
    throw new ApiError(message, code, response.status, detail);
  }
  return isJson ? response.json() : response;
}

async function apiBlob(path, filename) {
  const response = await fetch(API + path);
  if (!response.ok) {
    throw new ApiError(`Export failed (${response.status})`, "export_failed", response.status);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

function $(selector) {
  return document.querySelector(selector);
}

function $all(selector) {
  return Array.from(document.querySelectorAll(selector));
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function toast(message, kind, title) {
  const stack = $("#toastStack");
  const node = el("div", `toast ${kind || ""}`);
  if (title) node.appendChild(el("strong", null, title));
  node.appendChild(el("span", null, message));
  stack.appendChild(node);
  setTimeout(() => node.remove(), kind === "error" ? 12000 : 6000);
}

function fmt(value, digits) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const precision = digits === undefined ? 3 : digits;
  return Number(value).toLocaleString("en-GB", {
    minimumFractionDigits: 0,
    maximumFractionDigits: precision,
  });
}

function fmtInt(value) {
  if (value === null || value === undefined) return "—";
  return Number(value).toLocaleString("en-GB");
}

function fmtDate(iso) {
  if (!iso) return "—";
  return iso.replace("T", " ").replace("Z", " UTC");
}

function fmtPercent(value) {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function isoDate(daysAgo) {
  const date = new Date(Date.now() - daysAgo * 86400000);
  return date.toISOString().slice(0, 10);
}

function statusBadge(status) {
  const badge = el("span", `badge badge-${status || "unavailable"}`);
  badge.textContent = (status || "unknown").replace(/_/g, " ");
  return badge;
}

function setStatus(selector, dotClass, text) {
  const dot = $(`${selector} .dot`);
  const label = $(`${selector} span:last-child`);
  if (dot) dot.className = `dot ${dotClass}`;
  if (label) label.textContent = text;
}

async function withLoading(button, work) {
  const original = button ? button.textContent : "";
  if (button) {
    button.disabled = true;
    button.textContent = "Loading…";
  }
  try {
    return await work();
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = original;
    }
  }
}

/* ------------------------------------------------------------------- map */

function initMap() {
  const map = L.map("map", { zoomControl: true, attributionControl: true }).setView([8.6, 39.6], 6);

  const basemaps = {
    carto: L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
      attribution: "© OpenStreetMap contributors © CARTO",
      subdomains: "abcd",
      maxZoom: 19,
      crossOrigin: true,
    }),
    esri: L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      { attribution: "Imagery © Esri, Maxar, Earthstar Geographics", maxZoom: 19, crossOrigin: true }
    ),
    osm: L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "© OpenStreetMap contributors",
      maxZoom: 19,
      crossOrigin: true,
    }),
  };

  basemaps.carto.addTo(map);
  state.layers.basemaps = basemaps;
  state.layers.basemapActive = basemaps.carto;
  state.map = map;

  L.control.scale({ imperial: false, position: "bottomleft" }).addTo(map);
  L.control.fullscreen
    ? L.control.fullscreen().addTo(map)
    : addFullscreenFallback(map);

  map.on("mousemove", (event) => {
    $("#mapReadout").textContent =
      `${event.latlng.lat.toFixed(4)}, ${event.latlng.lng.toFixed(4)} · z${map.getZoom()}`;
  });

  return map;
}

function addFullscreenFallback(map) {
  const control = L.control({ position: "topleft" });
  control.onAdd = function () {
    const button = L.DomUtil.create("button", "leaflet-bar leaflet-control");
    button.type = "button";
    button.title = "Toggle full screen";
    button.textContent = "⤢";
    button.style.cssText = "width:30px;height:30px;background:#fff;border:2px solid rgba(0,0,0,.2);cursor:pointer";
    L.DomEvent.on(button, "click", () => {
      const wrap = document.querySelector(".map-wrap");
      if (document.fullscreenElement) document.exitFullscreen();
      else wrap.requestFullscreen();
    });
    return button;
  };
  control.addTo(map);
}

function setBasemap(key) {
  const maps = state.layers.basemaps;
  if (!maps || !maps[key]) return;
  if (state.layers.basemapActive) state.map.removeLayer(state.layers.basemapActive);
  maps[key].addTo(state.map);
  state.layers.basemapActive = maps[key];
}

/* -------------------------------------------------------------- legends */

function buildLegend(indicatorId, payload) {
  const legend = $("#legend");
  legend.innerHTML = "";
  const indicator = state.config.indicators.find((item) => item.id === indicatorId);
  if (!indicator) return;

  legend.appendChild(el("h4", null, indicator.label));
  const scale = el("div", "legend-scale");
  const colors = indicator.palette.map((stop) => stop.color);
  const swatch = el("div", "legend-swatch");
  swatch.style.background = `linear-gradient(to right, ${colors.join(", ")})`;
  scale.appendChild(swatch);
  const labels = el("div", "legend-labels");
  labels.appendChild(el("span", null, String(indicator.palette[0].value)));
  labels.appendChild(el("span", null, String(indicator.palette[indicator.palette.length - 1].value)));
  scale.appendChild(labels);
  legend.appendChild(scale);
  legend.appendChild(el("div", null, indicator.units));

  if (payload && payload.data_status) {
    const meta = el("div", "no-data");
    meta.appendChild(statusBadge(payload.data_status));
    meta.appendChild(el("div", null, `observations: ${payload.observation_count ?? "—"}`));
    meta.appendChild(el("div", null, `latest: ${fmtDate(payload.latest_observation)}`));
    legend.appendChild(meta);
  } else {
    legend.appendChild(el("div", "no-data", "No satellite layer loaded for this window."));
  }
}

function showMapNote(message, isError) {
  const note = $("#mapNote");
  if (!message) {
    note.hidden = true;
    note.textContent = "";
    return;
  }
  note.hidden = false;
  note.textContent = message;
  note.style.background = isError ? "var(--error-bg)" : "var(--warn-bg)";
  note.style.borderColor = isError ? "var(--error-border)" : "var(--warn-border)";
}

/* -------------------------------------------------------- boundary layers */

async function loadBoundary() {
  const key = `${state.basin}:${state.region}`;
  if (!state.cache.boundary) state.cache.boundary = {};
  let geojson = state.cache.boundary[key];
  if (!geojson) {
    geojson = await api(`/basin/${state.basin}/geojson?region=${state.region}`);
    state.cache.boundary[key] = geojson;
  }

  if (state.layers.boundary) state.map.removeLayer(state.layers.boundary);
  const props = (geojson.features[0] || {}).properties || {};

  state.layers.boundary = L.geoJSON(geojson, {
    style: { color: "#0b6e5f", weight: 2, fillColor: "#0b6e5f", fillOpacity: 0.05 },
    onEachFeature: (feature, layer) => {
      layer.bindPopup(() => boundaryPopup(props));
    },
  }).addTo(state.map);

  state.map.fitBounds(state.layers.boundary.getBounds(), { padding: [24, 24] });
  $("#regionHint").textContent = state.region === "buffer"
    ? `River corridor buffer (${fmtInt(props.buffer_distance_m)} m half-width), ${fmt(props.area_km2, 0)} km²`
    : `Whole basin, ${fmt(props.area_km2, 0)} km² · source ${props.source_file}`;
  return geojson;
}

function boundaryPopup(props) {
  const wrap = el("div");
  wrap.appendChild(el("div", "popup-title", props.basin_name || "Basin"));
  const rows = [
    ["Layer", props.layer === "buffer" ? "river corridor buffer" : "whole basin"],
    ["River", props.river_name],
    ["Area", `${fmt(props.area_km2, 0)} km² (equal-area projection)`],
    ["Source", `${props.source_file} · ${props.source_crs}`],
    ["Simplification", `${props.simplify_tolerance_m} m`],
    ["Buffer width", props.buffer_distance_m ? `${fmtInt(props.buffer_distance_m)} m` : null],
  ];
  rows.forEach(([label, value]) => {
    if (value === null) return;
    const row = el("div", "popup-row");
    row.appendChild(el("span", null, label));
    row.appendChild(el("span", null, String(value)));
    wrap.appendChild(row);
  });
  return wrap;
}

async function toggleOverlay(kind, enabled) {
  const layerKey = kind === "buffer" ? "buffer" : "green";
  if (!enabled) {
    if (state.layers[layerKey]) {
      state.map.removeLayer(state.layers[layerKey]);
      state.layers[layerKey] = null;
    }
    return;
  }
  const path =
    kind === "buffer"
      ? `/basin/${state.basin}/geojson?region=buffer`
      : `/green-legacy/geojson?basin=${state.basin}&mode=points&limit=6000`;
  const geojson = await api(path);
  const style =
    kind === "buffer"
      ? { color: "#1f6fb2", weight: 1.5, fillColor: "#1f6fb2", fillOpacity: 0.18 }
      : { color: "#16a34a", fillColor: "#16a34a", fillOpacity: 0.85, radius: 3 };

  state.layers[layerKey] = L.geoJSON(geojson, {
    pointToLayer: (feature, latlng) => L.circleMarker(latlng, style),
    style,
    onEachFeature: (feature, layer) => {
      const props = feature.properties || {};
      const rows = kind === "buffer"
        ? [["Buffer width", `${fmtInt(props.buffer_distance_m)} m`], ["Area", `${fmt(props.area_km2, 0)} km²`]]
        : [["Site", props.site], ["Region", props.region], ["Wereda", props.wereda],
           ["Area", `${fmt(props.area_ha, 2)} ha`], ["Seedlings recorded", fmtInt(props.seedlings)]];
      const wrap = el("div");
      wrap.appendChild(el("div", "popup-title", kind === "buffer" ? "River corridor buffer" : "Green Legacy planting site"));
      rows.forEach(([label, value]) => {
        const row = el("div", "popup-row");
        row.appendChild(el("span", null, label));
        row.appendChild(el("span", null, String(value ?? "—")));
        wrap.appendChild(row);
      });
      if (kind === "green") {
        wrap.appendChild(el("div", "no-data", "Planting record — not a survival measurement."));
      }
      layer.bindPopup(wrap);
    },
  }).addTo(state.map);

  if (kind === "green" && geojson.provenance) {
    toast(
      `${fmtInt(geojson.provenance.feature_count_returned)} of ${fmtInt(geojson.provenance.feature_count_total)} planting sites shown${geojson.provenance.truncated ? " (largest sites first)" : ""}.`,
      "info",
      "Green Legacy layer"
    );
  }
}

/* ------------------------------------------------------- satellite layer */

async function loadSatelliteLayer() {
  if (state.layers.satellite) {
    state.map.removeLayer(state.layers.satellite);
    state.layers.satellite = null;
  }
  if (!$("#layerSatellite").checked) {
    buildLegend(state.indicator, null);
    showMapNote(null);
    return;
  }

  const params = new URLSearchParams({
    start: state.window.start,
    end: state.window.end,
    region: state.region,
  });
  try {
    const payload = await api(`/tiles/${state.basin}/${state.indicator}?${params}`);
    state.layers.satellite = L.tileLayer(payload.tile_url, {
      attribution: "Google Earth Engine",
      opacity: Number($("#opacityRange").value) / 100,
      maxZoom: 19,
    }).addTo(state.map);
    buildLegend(state.indicator, payload);
    showMapNote(null);
    state.layers.lastTile = payload;
    return payload;
  } catch (error) {
    buildLegend(state.indicator, null);
    if (error.code === "earth_engine_unavailable") {
      showMapNote(
        "Satellite layer unavailable: Earth Engine is not connected, so no raster product can be generated. Basin boundaries and vector data are still live."
      );
    } else if (error.code === "insufficient_observations") {
      showMapNote(
        `No usable observation in this window (${state.window.start} → ${state.window.end}). Widen the period or lower the cloud threshold.`
      );
    } else {
      showMapNote(`Satellite layer could not be loaded: ${error.message}`);
      toast(error.message, "error", "Map layer");
    }
    return null;
  }
}

/* --------------------------------------------------------------- overview */

async function loadOverview() {
  const tbody = $("#overviewTable tbody");
  const params = new URLSearchParams({ start: state.window.start, end: state.window.end });

  let payload;
  try {
    payload = await api(`/overview?${params}`);
  } catch (error) {
    const row = el("tr");
    const cell = el("td", "empty", `Overview unavailable: ${error.message}`);
    cell.colSpan = 9;
    row.appendChild(cell);
    tbody.replaceChildren(row);
    renderKpis([], payload);
    $("#overviewFootnote").textContent = error.message;
    return;
  }

  // Rows are assembled off-DOM and swapped in once, so two overlapping
  // refreshes can never leave a doubled table behind.
  const rows = [];
  payload.basins.forEach((basin) => {
    const row = el("tr");
    row.appendChild(el("td", null, basin.basin_name));
    row.appendChild(el("td", "num", fmt(basin.area_km2, 0)));

    ["ndvi", "ndwi", "mndwi"].forEach((name) => {
      const entry = (basin.indicators || {})[name];
      const cell = el("td", "num", entry && entry.value !== null ? fmt(entry.value, 3) : "no data");
      if (entry && entry.value === null) cell.className = "empty";
      cell.title = entry
        ? `${entry.observation_count} observations · coverage ${fmtPercent(entry.coverage_fraction)} · ${entry.data_status}`
        : "No data";
      row.appendChild(cell);
    });

    const availability = basin.availability || {};
    const observations = Object.values(basin.indicators || {})
      .map((entry) => entry.observation_count || 0)
      .reduce((a, b) => Math.max(a, b), 0);
    row.appendChild(el("td", "num", fmtInt(observations || null)));
    row.appendChild(el("td", "num", fmtPercent(availability.coverage_fraction_mean)));
    row.appendChild(el("td", null, fmtDate(availability.latest_observation)));
    const statusCell = el("td");
    statusCell.appendChild(statusBadge(basin.data_status));
    row.appendChild(statusCell);
    rows.push(row);
  });
  tbody.replaceChildren(...rows);

  renderKpis(payload.basins, payload);
  $("#overviewFootnote").textContent = payload.scope_note;
  const latest = payload.latest_observation;
  setStatus("#freshnessStatus", latest ? "dot-ok" : "dot-warn", `observation: ${fmtDate(latest)}`);
}

function renderKpis(basins, payload) {
  const row = $("#kpiRow");
  const cards = [];
  const usable = basins.filter((basin) => basin.data_status !== "unavailable");
  const allIndicators = basins.flatMap((basin) => Object.values(basin.indicators || {}));
  const withValue = allIndicators.filter((entry) => entry.value !== null && entry.value !== undefined);
  const meanCoverage = withValue.length
    ? withValue.reduce((sum, entry) => sum + (entry.coverage_fraction || 0), 0) / withValue.length
    : null;

  const kpis = [
    {
      label: "Monitored basins",
      value: String(payload ? payload.basin_count : basins.length),
      sub: `${usable.length} with satellite data in this window`,
    },
    {
      label: "Latest valid observation",
      value: payload && payload.latest_observation ? fmtDate(payload.latest_observation).slice(0, 10) : "—",
      sub: payload ? `refresh ${fmtDate(payload.dashboard_refresh)}` : "",
    },
    {
      label: "Indicators with data",
      value: `${withValue.length}/${allIndicators.length || 0}`,
      sub: "NDVI, NDWI, MNDWI across basins",
    },
    {
      label: "Mean valid-pixel coverage",
      value: fmtPercent(meanCoverage),
      sub: "share of region with usable pixels",
    },
  ];

  kpis.forEach((kpi) => {
    const card = el("div", "kpi");
    card.appendChild(el("div", "kpi-label", kpi.label));
    card.appendChild(el("div", "kpi-value", kpi.value));
    card.appendChild(el("div", "kpi-sub", kpi.sub));
    cards.push(card);
  });

  if (payload && payload.data_status === "unavailable") {
    const notice = el("div", "kpi");
    notice.appendChild(el("div", "kpi-label", "Data source"));
    notice.appendChild(el("div", "kpi-value", "Offline"));
    notice.appendChild(el("div", "kpi-sub", "Earth Engine not connected — satellite indicators unavailable"));
    cards.push(notice);
  }
  row.replaceChildren(...cards);
}

/* -------------------------------------------------------------- statistics */

async function loadCorridor() {
  const tbody = $("#corridorTable tbody");
  const params = new URLSearchParams({
    indicators: "ndvi,ndwi,mndwi",
    start: state.window.start,
    end: state.window.end,
  });

  let bufferStats;
  let basinStats;
  try {
    bufferStats = await api(`/basin/${state.basin}/stats?${params}&region=buffer`);
    basinStats = await api(`/basin/${state.basin}/stats?${params}&region=basin`);
  } catch (error) {
    const row = el("tr");
    const cell = el("td", "empty", `Corridor statistics unavailable: ${error.message}`);
    cell.colSpan = 6;
    row.appendChild(cell);
    tbody.replaceChildren(row);
    $("#corridorNote").textContent =
      error.code === "earth_engine_unavailable"
        ? "Earth Engine is not connected; corridor statistics cannot be computed."
        : error.message;
    return;
  }

  const bufferProps = (bufferStats.indicators.ndvi || {}).source || {};
  $("#corridorNote").textContent =
    `Corridor = river network buffered by ${fmtInt(state.cache.bufferDistance || 2000)} m ` +
    `(${fmt(bufferStats.region_area_km2, 0)} km²). Whole basin = ${fmt(basinStats.region_area_km2, 0)} km². ` +
    `Scale ${bufferProps.scale_m || 10} m · mask: ${bufferProps.mask_method || "documented"}`;

  const corridorRows = [];
  ["ndvi", "ndwi", "mndwi"].forEach((name) => {
    const bufferEntry = bufferStats.indicators[name] || {};
    const basinEntry = basinStats.indicators[name] || {};
    const row = el("tr");
    row.appendChild(el("td", null, bufferEntry.label || name.toUpperCase()));
    row.appendChild(el("td", "num", bufferEntry.value === null || bufferEntry.value === undefined ? "no data" : fmt(bufferEntry.value, 3)));
    row.appendChild(el("td", "num", basinEntry.value === null || basinEntry.value === undefined ? "no data" : fmt(basinEntry.value, 3)));
    const diff =
      bufferEntry.value !== null && basinEntry.value !== null && bufferEntry.value !== undefined && basinEntry.value !== undefined
        ? bufferEntry.value - basinEntry.value
        : null;
    row.appendChild(el("td", "num", diff === null ? "—" : `${diff > 0 ? "+" : ""}${fmt(diff, 3)}`));
    row.appendChild(el("td", "num", fmtInt(bufferEntry.observation_count)));
    const statusCell = el("td");
    statusCell.appendChild(statusBadge(bufferEntry.data_status));
    row.appendChild(statusCell);
    corridorRows.push(row);
  });
  tbody.replaceChildren(...corridorRows);
}

/* ------------------------------------------------------------ time series */

async function loadSeries(button) {
  const indicator = $("#seriesIndicator").value;
  const startYear = Number($("#seriesStartYear").value);
  const endYear = Number($("#seriesEndYear").value);
  const aggregation = $("#seriesAggregation").value;

  const work = async () => {
    const params = new URLSearchParams({
      start_year: startYear,
      end_year: endYear,
      aggregation,
      region: state.region,
    });
    try {
      const payload = await api(`/basin/${state.basin}/timeseries/${indicator}?${params}`);
      renderSeriesChart(payload);
      renderSeriesMeta(payload);
      state.cache.series = payload;
    } catch (error) {
      renderSeriesError(error);
    }
  };

  if (button) await withLoading(button, work);
  else await work();
}

function renderSeriesChart(payload) {
  const points = payload.series || [];
  const labels = points.map((point) => point.period);
  const values = points.map((point) => (point.value === null || point.value === undefined ? null : point.value));

  if (state.charts.series) state.charts.series.destroy();
  const canvas = $("#seriesChart");
  state.charts.series = new Chart(canvas, {
    type: payload.indicator === "rainfall" || payload.indicator === "et" ? "bar" : "line",
    data: {
      labels,
      datasets: [
        {
          label: `${payload.label} (${payload.units})`,
          data: values,
          borderColor: "#0b6e5f",
          backgroundColor: "rgba(11,110,95,.18)",
          borderWidth: 2,
          spanGaps: false,
          tension: 0.25,
          pointRadius: 2.5,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: true, position: "bottom", labels: { boxWidth: 12, font: { size: 11 } } },
        tooltip: {
          callbacks: {
            afterBody: (items) => {
              const point = points[items[0].dataIndex];
              if (!point) return "";
              const lines = [`observations: ${point.observation_count ?? "—"}`];
              if (point.latest_observation) lines.push(`latest: ${fmtDate(point.latest_observation)}`);
              lines.push(`status: ${point.data_status}`);
              if (point.value === null) lines.push("missing observation — not interpolated");
              return lines;
            },
          },
        },
      },
      scales: {
        y: { beginAtZero: false, grid: { color: "#eceff3" }, title: { display: true, text: payload.units, font: { size: 10 } } },
        x: { grid: { display: false }, ticks: { maxTicksLimit: 14, font: { size: 10 } } },
      },
    },
  });
}

function renderSeriesMeta(payload) {
  const box = $("#seriesMeta");
  box.innerHTML = "";
  const source = payload.source || {};
  const gaps = (payload.gaps || []).length;
  const list = el("dl");
  const rows = [
    ["Dataset", source.dataset],
    ["Bands / field", (source.bands || []).join(", ")],
    ["Equation", source.equation],
    ["Scale", `${source.scale_m} m`],
    ["Cloud / quality mask", source.mask_method || "n/a (ancillary product)"],
    ["Compositing", source.compositing],
    ["Processing version", source.processing_version],
    ["Gaps (missing observations)", `${gaps} of ${(payload.series || []).length} periods`],
    ["Interpolation", payload.interpolation],
  ];
  if (payload.warning) rows.unshift(["Warning", payload.warning]);
  if (payload.caveat) rows.push(["Interpretation", payload.caveat]);

  rows.forEach(([label, value]) => {
    if (!value) return;
    list.appendChild(el("dt", null, label));
    list.appendChild(el("dd", null, String(value)));
  });
  box.appendChild(list);
}

function renderSeriesError(error) {
  const box = $("#seriesMeta");
  box.innerHTML = "";
  const notice = el("div", `notice ${error.status === 503 ? "error" : ""}`);
  notice.appendChild(el("strong", null, "Time series unavailable"));
  notice.appendChild(el("p", null, error.message));
  if (error.code === "earth_engine_unavailable") {
    notice.appendChild(
      el("p", null, "No series is drawn rather than showing a fabricated or empty-looking chart.")
    );
  }
  box.appendChild(notice);
  if (state.charts.series) {
    state.charts.series.destroy();
    state.charts.series = null;
  }
  toast(error.message, "error", "Time series");
}

/* ---------------------------------------------------------------- compare */

async function loadCompare(button) {
  const indicator = $("#compareIndicator").value;
  const selected = $all("#compareBasins .chip[aria-pressed='true']").map((chip) => chip.dataset.basin);
  const startYear = Number($("#seriesStartYear").value);
  const endYear = Number($("#seriesEndYear").value);

  const work = async () => {
    if (!selected.length) {
      toast("Select at least one basin to compare.", "warn", "Comparison");
      return;
    }
    const datasets = [];
    const palette = ["#0b6e5f", "#1f6fb2", "#b06d00", "#7c3aed", "#c62828", "#3f6212"];
    let labels = [];
    let failed = [];

    for (let index = 0; index < selected.length; index += 1) {
      const basin = selected[index];
      const params = new URLSearchParams({
        start_year: startYear,
        end_year: endYear,
        aggregation: $("#seriesAggregation").value,
      });
      try {
        const payload = await api(`/basin/${basin}/timeseries/${indicator}?${params}`);
        labels = payload.series.map((point) => point.period);
        datasets.push({
          label: payload.basin_name,
          data: payload.series.map((point) => point.value),
          borderColor: palette[index % palette.length],
          backgroundColor: palette[index % palette.length],
          borderWidth: 2,
          spanGaps: false,
          tension: 0.25,
          pointRadius: 2,
        });
      } catch (error) {
        failed.push(`${basin}: ${error.message}`);
      }
    }

    if (state.charts.compare) state.charts.compare.destroy();
    state.charts.compare = new Chart($("#compareChart"), {
      type: "line",
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { position: "bottom", labels: { boxWidth: 12, font: { size: 11 } } } },
        scales: {
          y: { grid: { color: "#eceff3" }, title: { display: true, text: indicator.toUpperCase(), font: { size: 10 } } },
          x: { grid: { display: false }, ticks: { maxTicksLimit: 14, font: { size: 10 } } },
        },
      },
    });

    if (failed.length) toast(failed.join(" · "), "error", "Comparison incomplete");
  };

  if (button) await withLoading(button, work);
  else await work();
}

async function loadChange(button) {
  const work = async () => {
    const body = {
      indicator: $("#compareIndicator").value,
      baseline_start: $("#baselineStart").value,
      baseline_end: $("#baselineEnd").value,
      comparison_start: $("#comparisonStart").value,
      comparison_end: $("#comparisonEnd").value,
      region: state.region,
      include_map: $("#changeMapToggle").checked,
    };

    const box = $("#changeSummary");
    box.innerHTML = "";

    try {
      const payload = await api(`/basin/${state.basin}/change`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      renderChange(payload);
      state.cache.change = payload;
    } catch (error) {
      const notice = el("div", "notice error");
      notice.appendChild(el("strong", null, "Change detection unavailable"));
      notice.appendChild(el("p", null, error.message));
      box.appendChild(notice);
      toast(error.message, "error", "Change detection");
      if (state.layers.difference) {
        state.map.removeLayer(state.layers.difference);
        state.layers.difference = null;
      }
    }
  };
  if (button) await withLoading(button, work);
  else await work();
}

function renderChange(payload) {
  const box = $("#changeSummary");
  box.innerHTML = "";
  const stats = payload.statistics || {};
  const checks = stats.comparability_checks || {};
  const list = el("dl");
  [
    ["Indicator", `${stats.label} (${stats.units})`],
    ["Baseline", `${fmt((stats.baseline || {}).value, 4)} · ${fmtInt((stats.baseline || {}).observation_count)} observations · ${(stats.baseline || {}).window ? `${(stats.baseline || {}).window.start} → ${(stats.baseline || {}).window.end}` : ""}`],
    ["Comparison", `${fmt((stats.comparison || {}).value, 4)} · ${fmtInt((stats.comparison || {}).observation_count)} observations · ${(stats.comparison || {}).window ? `${(stats.comparison || {}).window.start} → ${(stats.comparison || {}).window.end}` : ""}`],
    ["Difference", `${stats.difference === null || stats.difference === undefined ? "—" : fmt(stats.difference, 4)} (${stats.difference_units || ""})`],
    ["Percentage change", stats.percentage_change === null || stats.percentage_change === undefined ? `not computed — ${stats.percentage_change_basis}` : `${fmt(stats.percentage_change, 2)}%`],
    ["Valid-pixel coverage", fmtPercent(checks.coverage_fraction)],
    ["Comparable", checks.baseline_has_observations && checks.comparison_has_observations ? "yes — same indicator, region, scale and processing version" : "no — insufficient observations in one period"],
    ["Status", stats.data_status],
  ].forEach(([label, value]) => {
    list.appendChild(el("dt", null, label));
    list.appendChild(el("dd", null, String(value)));
  });
  box.appendChild(list);

  if (payload.map && payload.map.tile_url) {
    if (state.layers.difference) state.map.removeLayer(state.layers.difference);
    state.layers.difference = L.tileLayer(payload.map.tile_url, {
      attribution: "Google Earth Engine",
      opacity: 0.75,
    }).addTo(state.map);
    toast("Difference layer added to the map (comparison minus baseline).", "info", "Change detection");
  }
}

/* ----------------------------------------------------------- green legacy */

async function loadGreen() {
  const kpis = $("#greenKpis");
  const tbody = $("#greenTable tbody");

  try {
    const summary = await api(`/green-legacy/summary?basin=${state.basin}`);
    const cards = [];
    [
      ["Planting sites", fmtInt(summary.feature_count), `subset: ${summary.subset}`],
      ["Planted area", `${fmt(summary.total_area_ha, 0)} ha`, "computed from geometry (equal-area projection)"],
      ["Seedlings recorded", fmtInt(summary.total_seedlings_recorded), "attribute sum — survival not verified"],
      ["Source features", fmtInt(summary.provenance.feature_count_total), summary.provenance.source_file],
    ].forEach(([label, value, sub]) => {
      const card = el("div", "kpi");
      card.appendChild(el("div", "kpi-label", label));
      card.appendChild(el("div", "kpi-value", value));
      card.appendChild(el("div", "kpi-sub", sub));
      cards.push(card);
    });
    kpis.replaceChildren(...cards);

    const regionRows = [];
    (summary.by_region || []).forEach((row) => {
      const tr = el("tr");
      tr.appendChild(el("td", null, row.region));
      tr.appendChild(el("td", "num", fmtInt(row.sites)));
      tr.appendChild(el("td", "num", fmt(row.area_ha, 1)));
      tr.appendChild(el("td", "num", fmtInt(row.seedlings)));
      regionRows.push(tr);
    });
    tbody.replaceChildren(...regionRows);

    const quality = summary.data_quality || {};
    const notice = $("#greenNotice");
    notice.innerHTML = "";
    notice.appendChild(el("strong", null, "What this layer is — and is not"));
    const list = el("ul");
    [quality.area_field, quality.seedling_field, quality.species_field, quality.planted_field, quality.limitations]
      .filter(Boolean)
      .forEach((line) => list.appendChild(el("li", null, line)));
    notice.appendChild(list);
  } catch (error) {
    const row = el("tr");
    const cell = el("td", "empty", `Green Legacy summary unavailable: ${error.message}`);
    cell.colSpan = 4;
    row.appendChild(cell);
    tbody.replaceChildren(row);
  }
}

/* -------------------------------------------------------------- hydrology */

async function loadHydro() {
  const notice = $("#hydroNotice");
  const tbody = $("#hydroTable tbody");
  notice.innerHTML = "";

  let contract;
  try {
    contract = await api("/hydrology/integration");
  } catch (error) {
    notice.className = "notice error";
    notice.textContent = error.message;
    return;
  }

  let stationsError = null;
  try {
    await api("/hydrology/stations");
  } catch (error) {
    stationsError = error;
  }

  notice.className = contract.connected ? "notice info" : "notice";
  notice.appendChild(
    el(
      "strong",
      null,
      contract.connected ? "Hydrological feed connected" : "Operational gauge monitoring is not connected"
    )
  );
  notice.appendChild(el("p", null, contract.note));
  if (stationsError) {
    notice.appendChild(el("p", null, `API response: ${stationsError.message} (HTTP ${stationsError.status}).`));
    const detail = stationsError.detail || {};
    if (detail.explanation) notice.appendChild(el("p", null, detail.explanation));
    if (detail.how_to_connect) notice.appendChild(el("p", null, detail.how_to_connect));
  }
  notice.appendChild(
    el(
      "p",
      null,
      "Satellite indicators elsewhere in this dashboard are not river stage or discharge, and no simulated gauge values are produced."
    )
  );

  const contractRows = [];

  [
    ["Provider", contract.provider || "none configured"],
    ["Variables expected", (contract.variables_expected || []).join(", ")],
    ["Station fields", (contract.station_fields || []).join(", ")],
    ["Reading fields", (contract.reading_fields || []).join(", ")],
    ["Quality flags", (contract.quality_flags || []).join(", ")],
  ].forEach(([label, value]) => {
    const row = el("tr");
    row.appendChild(el("td", null, label));
    row.appendChild(el("td", null, String(value)));
    contractRows.push(row);
  });
  tbody.replaceChildren(...contractRows);
}

/* --------------------------------------------------------- data & methods */

function loadDataPanel() {
  const grid = $("#methodGrid");
  const cards = [];
  (state.config.indicators || []).forEach((indicator) => {
    const card = el("div", "method-card");
    card.appendChild(el("h4", null, `${indicator.label} (${indicator.id})`));
    const list = el("dl");
    [
      ["Dataset", indicator.dataset],
      ["Bands", indicator.bands.join(", ")],
      ["Equation", indicator.equation],
      ["Scale", `${indicator.scale_m} m`],
      ["Units", indicator.units],
      ["Valid range", indicator.valid_range ? indicator.valid_range.join(" to ") : "—"],
    ].forEach(([label, value]) => {
      list.appendChild(el("dt", null, label));
      list.appendChild(el("dd", null, String(value)));
    });
    card.appendChild(list);
    if (indicator.caveat) card.appendChild(el("div", "caveat", indicator.caveat));
    cards.push(card);
  });
  grid.replaceChildren(...cards);

  $("#scopeNotice").className = "notice info";
  $("#scopeNotice").textContent = state.config.satellite_scope;
}

async function loadInventory() {
  const tbody = $("#inventoryTable tbody");
  try {
    const payload = await api("/data/inventory");
    const rows = [];
    (payload.datasets || []).forEach((dataset) => {
      const row = el("tr");
      row.appendChild(el("td", null, dataset.name));
      row.appendChild(el("td", null, dataset.source_file));
      row.appendChild(el("td", null, String(dataset.source_crs || "—")));
      row.appendChild(el("td", "num", fmtInt(dataset.feature_count)));
      row.appendChild(el("td", null, (dataset.warnings || []).join(" ") || "—"));
      rows.push(row);
    });
    tbody.replaceChildren(...rows);
  } catch (error) {
    const row = el("tr");
    const cell = el("td", "empty", `Inventory unavailable: ${error.message}`);
    cell.colSpan = 5;
    row.appendChild(cell);
    tbody.replaceChildren(row);
  }
}

/* ----------------------------------------------------------------- exports */

function statsParams() {
  return new URLSearchParams({
    indicators: "ndvi,ndwi,mndwi",
    start: state.window.start,
    end: state.window.end,
    region: state.region,
  });
}

async function exportStats() {
  try {
    await apiBlob(`/basin/${state.basin}/stats?${statsParams()}&format=csv`, `statistics_${state.basin}.csv`);
    toast("Statistics CSV downloaded.", "info", "Export");
  } catch (error) {
    toast(error.message, "error", "Export failed");
  }
}

async function exportSeries() {
  const indicator = $("#seriesIndicator").value;
  const params = new URLSearchParams({
    start_year: $("#seriesStartYear").value,
    end_year: $("#seriesEndYear").value,
    aggregation: $("#seriesAggregation").value,
    region: state.region,
    format: "csv",
  });
  try {
    await apiBlob(`/basin/${state.basin}/timeseries/${indicator}?${params}`, `timeseries_${state.basin}_${indicator}.csv`);
    toast("Time series CSV downloaded.", "info", "Export");
  } catch (error) {
    toast(error.message, "error", "Export failed");
  }
}

async function exportMapPng() {
  if (typeof html2canvas !== "function") {
    toast("Map capture library not available.", "error", "Export failed");
    return;
  }
  try {
    const canvas = await html2canvas($("#map"), {
      useCORS: true,
      allowTaint: false,
      backgroundColor: "#e8ecef",
      logging: false,
    });
    const link = document.createElement("a");
    link.download = `map_${state.basin}_${state.indicator}_${state.window.start}_${state.window.end}.png`;
    link.href = canvas.toDataURL("image/png");
    link.click();
    toast("Map view PNG downloaded.", "info", "Export");
  } catch (error) {
    toast(
      `Map capture failed (${error.message}). Basemap tiles can block cross-origin capture; use the browser screenshot or the chart PNG instead.`,
      "error",
      "Export failed"
    );
  }
}

function exportReport() {
  const params = new URLSearchParams({
    basin: state.basin,
    indicator: state.indicator,
    start: state.window.start,
    end: state.window.end,
    start_year: $("#seriesStartYear").value,
    end_year: $("#seriesEndYear").value,
  });
  window.open(`/report?${params}`, "_blank", "noopener");
  toast("Summary opened in a new tab — use Print → Save as PDF.", "info", "Export");
}

/* ------------------------------------------------------------------ controls */

function buildIndicatorChips() {
  const wrap = $("#indicatorChips");
  wrap.innerHTML = "";
  state.config.indicators.forEach((indicator) => {
    const chip = el("button", "chip", indicator.label);
    chip.type = "button";
    chip.dataset.indicator = indicator.id;
    chip.setAttribute("aria-pressed", String(indicator.id === state.indicator));
    if (indicator.id === state.indicator) chip.classList.add("is-active");
    chip.addEventListener("click", async () => {
      state.indicator = indicator.id;
      $all("#indicatorChips .chip").forEach((node) => {
        const active = node.dataset.indicator === state.indicator;
        node.classList.toggle("is-active", active);
        node.setAttribute("aria-pressed", String(active));
      });
      $("#indicatorCaveat").textContent = indicator.caveat || "";
      await loadSatelliteLayer();
    });
    wrap.appendChild(chip);
  });
  const active = state.config.indicators.find((item) => item.id === state.indicator);
  $("#indicatorCaveat").textContent = active ? active.caveat || "" : "";
}

function buildCompareChips() {
  const wrap = $("#compareBasins");
  wrap.innerHTML = "";
  state.config.basins.forEach((basin) => {
    const chip = el("button", "chip", basin.name);
    chip.type = "button";
    chip.dataset.basin = basin.id;
    chip.setAttribute("aria-pressed", String(basin.id === state.basin));
    chip.addEventListener("click", () => {
      const pressed = chip.getAttribute("aria-pressed") === "true";
      chip.setAttribute("aria-pressed", String(!pressed));
    });
    wrap.appendChild(chip);
  });
}

function buildSelects() {
  const basinSelect = $("#basinSelect");
  basinSelect.innerHTML = "";
  state.config.basins.forEach((basin) => {
    const option = el("option", null, basin.name);
    option.value = basin.id;
    basinSelect.appendChild(option);
  });
  basinSelect.value = state.basin;

  [$("#seriesIndicator"), $("#compareIndicator")].forEach((select) => {
    select.innerHTML = "";
    state.config.indicators.forEach((indicator) => {
      const option = el("option", null, `${indicator.label} (${indicator.units})`);
      option.value = indicator.id;
      select.appendChild(option);
    });
    select.value = state.indicator;
  });
}

function bindControls() {
  $("#basinSelect").addEventListener("change", async (event) => {
    state.basin = event.target.value;
    $all("#compareBasins .chip").forEach((chip) => chip.setAttribute("aria-pressed", String(chip.dataset.basin === state.basin)));
    await refreshBasinViews();
  });

  $all(".segment").forEach((segment) => {
    segment.addEventListener("click", async () => {
      state.region = segment.dataset.region;
      $all(".segment").forEach((node) => node.classList.toggle("is-active", node === segment));
      await refreshBasinViews();
    });
  });

  $("#applyPeriod").addEventListener("click", async () => {
    state.window.start = $("#startDate").value;
    state.window.end = $("#endDate").value;
    if (!state.window.start || !state.window.end) {
      toast("Set both a start and an end date.", "warn", "Period");
      return;
    }
    await Promise.all([loadOverview(), loadSatelliteLayer(), loadCorridor()]);
  });

  $all("#periodPresets .btn").forEach((button) => {
    button.addEventListener("click", async () => {
      const days = Number(button.dataset.days);
      state.window.start = isoDate(days);
      state.window.end = isoDate(0);
      $("#startDate").value = state.window.start;
      $("#endDate").value = state.window.end;
      await Promise.all([loadOverview(), loadSatelliteLayer(), loadCorridor()]);
    });
  });

  $("#layerSatellite").addEventListener("change", loadSatelliteLayer);
  $("#layerBasin").addEventListener("change", (event) => {
    if (!state.layers.boundary) return;
    if (event.target.checked) state.layers.boundary.addTo(state.map);
    else state.map.removeLayer(state.layers.boundary);
  });
  $("#layerBuffer").addEventListener("change", (event) => {
    toggleOverlay("buffer", event.target.checked).catch((error) => toast(error.message, "error", "Overlay"));
  });
  $("#layerGreen").addEventListener("change", (event) => {
    toggleOverlay("green", event.target.checked).catch((error) => toast(error.message, "error", "Overlay"));
  });
  $("#basemapSelect").addEventListener("change", (event) => setBasemap(event.target.value));
  $("#opacityRange").addEventListener("input", (event) => {
    $("#opacityValue").textContent = `${event.target.value}%`;
    if (state.layers.satellite) state.layers.satellite.setOpacity(Number(event.target.value) / 100);
    if (state.layers.difference) state.layers.difference.setOpacity(Number(event.target.value) / 100);
  });

  $("#loadSeries").addEventListener("click", (event) => loadSeries(event.target));
  $("#loadCompare").addEventListener("click", (event) => loadCompare(event.target));
  $("#loadChange").addEventListener("click", (event) => loadChange(event.target));

  $("#exportStatsCsv").addEventListener("click", exportStats);
  $("#exportSeriesCsv").addEventListener("click", exportSeries);
  $("#exportMapPng").addEventListener("click", exportMapPng);
  $("#exportReport").addEventListener("click", exportReport);

  $("#refreshAll").addEventListener("click", () => bootstrap(true));

  $all(".tab").forEach((tab) => {
    tab.addEventListener("click", () => activateTab(tab.dataset.tab));
  });
}

function activateTab(name) {
  $all(".tab").forEach((tab) => {
    const active = tab.dataset.tab === name;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  $all(".tab-panel").forEach((panel) => panel.classList.toggle("is-active", panel.id === `panel-${name}`));
  if (name === "series" && !state.cache.series) loadSeries();
  if (name === "corridor") loadCorridor();
  if (name === "green") loadGreen();
  if (name === "hydro") loadHydro();
  if (name === "data") loadInventory();
  setTimeout(() => state.map.invalidateSize(), 60);
}

async function refreshBasinViews() {
  try {
    await loadBoundary();
  } catch (error) {
    toast(error.message, "error", "Basin geometry");
  }
  if ($("#layerBuffer").checked) {
    state.map.removeLayer(state.layers.buffer);
    state.layers.buffer = null;
    await toggleOverlay("buffer", true);
  }
  if ($("#layerGreen").checked) {
    state.map.removeLayer(state.layers.green);
    state.layers.green = null;
    await toggleOverlay("green", true);
  }
  await Promise.all([loadSatelliteLayer(), loadOverview(), loadCorridor()]);
}

/* --------------------------------------------------------------- bootstrap */

async function bootstrap(isRefresh) {
  if (state.bootstrapping) return;
  state.bootstrapping = true;
  try {
    await runBootstrap(isRefresh);
  } finally {
    state.bootstrapping = false;
  }
}

async function runBootstrap(isRefresh) {
  try {
    state.config = await api("/config");
  } catch (error) {
    toast(`Configuration could not be loaded: ${error.message}`, "error", "Startup");
    return;
  }

  try {
    state.readiness = await api("/readiness");
  } catch (error) {
    // Readiness returns 503 when Earth Engine is down; the body is still useful.
    state.readiness = error.detail && error.detail.body ? error.detail.body : null;
  }
  if (!state.readiness) {
    try {
      const response = await fetch(`${API}/readiness`);
      state.readiness = await response.json();
    } catch (error) {
      state.readiness = null;
    }
  }

  const eeState = (state.config.earth_engine || {});
  const available = Boolean(eeState.available);
  state.demo = Boolean(state.readiness && state.readiness.demonstration_mode && state.readiness.demonstration_mode.active);
  setStatus(
    "#eeStatus",
    available ? "dot-ok" : state.demo ? "dot-warn" : "dot-error",
    available ? `Earth Engine connected (${eeState.project || "project"})` : "Earth Engine unavailable"
  );
  $("#demoBanner").hidden = !state.demo;

  if (!state.window.start) {
    const days = state.config.freshness_policy.default_window_days;
    state.window.start = isoDate(days);
    state.window.end = isoDate(0);
    $("#startDate").value = state.window.start;
    $("#endDate").value = state.window.end;
  }
  const lastYear = new Date().getFullYear();
  $("#seriesStartYear").value = 2017;
  $("#seriesEndYear").value = lastYear;
  $("#baselineStart").value = `${lastYear - 1}-06-01`;
  $("#baselineEnd").value = `${lastYear - 1}-09-01`;
  $("#comparisonStart").value = `${lastYear}-06-01`;
  $("#comparisonEnd").value = `${lastYear}-09-01`;

  buildSelects();
  buildIndicatorChips();
  buildCompareChips();
  loadDataPanel();

  if (!state.map) initMap();
  if (isRefresh) {
    state.cache = {};
  }

  await refreshBasinViews();
  $("#footerMeta").textContent =
    `API v${state.config.version} · window ${state.window.start} → ${state.window.end} · ` +
    `data status: ${state.readiness ? state.readiness.status : "unknown"}`;

  if (!available) {
    toast(
      state.demo
        ? "Earth Engine is not connected. Demonstration data is being shown and is clearly labelled."
        : "Earth Engine is not connected, so satellite indicators are unavailable. Basin geometry and vector data are live.",
      state.demo ? "warn" : "error",
      "Data source"
    );
  }
}

function start() {
  if (state.started) return;
  state.started = true;
  bindControls();
  bootstrap(false);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", start);
} else {
  start();
}
