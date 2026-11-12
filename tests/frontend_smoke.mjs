/**
 * Front-end smoke test (jsdom).
 *
 * Loads the real static/index.html, runs the real static/app.js against the
 * live API on http://127.0.0.1:8000, and fails if the application throws, if
 * required controls are not populated, or if a panel does not render.
 *
 * Browser-only libraries that need a real rendering context (Leaflet map
 * rendering, Chart.js canvas, html2canvas) are replaced with instrumented
 * stubs so the test can assert *how the app uses them* (which tile URL it
 * draws, which series it charts) without a browser.
 *
 * Run: node tests/frontend_smoke.mjs
 */

import { readFileSync } from "node:fs";
import { JSDOM, VirtualConsole } from "jsdom";

const API_ORIGIN = process.env.API_ORIGIN || "http://127.0.0.1:8000";
const html = readFileSync(new URL("../static/index.html", import.meta.url), "utf8");
const appJs = readFileSync(new URL("../static/app.js", import.meta.url), "utf8");

const failures = [];
const consoleErrors = [];
const drawn = { tiles: [], charts: [], popups: 0, fitBounds: 0 };

const virtualConsole = new VirtualConsole();
virtualConsole.on("jsdomError", (error) => consoleErrors.push(String(error)));
virtualConsole.on("error", (...args) => consoleErrors.push(args.join(" ")));

const dom = new JSDOM(html, {
  runScripts: "outside-only",
  pretendToBeVisual: true,
  url: `${API_ORIGIN}/`,
  virtualConsole,
});
const { window } = dom;

/* ------------------------------------------------------------ library stubs */

class FakeLayer {
  constructor(kind, options = {}) {
    this.kind = kind;
    this.options = options;
    this.added = false;
  }
  addTo() {
    this.added = true;
    return this;
  }
  setOpacity(value) {
    this.options.opacity = value;
  }
  bindPopup() {
    drawn.popups += 1;
    return this;
  }
  getBounds() {
    return { pad: () => ({}) };
  }
}

const L = {
  map: () => ({
    setView: () => ({
      on: () => {},
      getZoom: () => 6,
      removeLayer: () => {},
      addLayer: () => {},
      invalidateSize: () => {},
      fitBounds: () => {
        drawn.fitBounds += 1;
      },
    }),
  }),
  tileLayer: (url, options) => {
    drawn.tiles.push(url);
    return new FakeLayer("tile", options);
  },
  geoJSON: (data, handlers) => {
    if (handlers && handlers.onEachFeature && data.features) {
      data.features.slice(0, 3).forEach((feature) =>
        handlers.onEachFeature(feature, { bindPopup: () => {} })
      );
    }
    return new FakeLayer("geojson");
  },
  circleMarker: () => new FakeLayer("marker"),
  // Real Leaflet exposes L.control as a callable factory with .scale / .fullscreen
  control: Object.assign(
    () => ({ onAdd: () => ({}), addTo: () => {} }),
    { scale: () => ({ addTo: () => {} }), fullscreen: null }
  ),
  DomUtil: { create: () => ({ style: {} }) },
  DomEvent: { on: () => {} },
};

class FakeChart {
  constructor(canvas, config) {
    this.canvas = canvas;
    this.config = config;
    drawn.charts.push(config);
  }
  destroy() {}
}

/* --------------------------------------------------------------- globals */

window.L = L;
window.Chart = FakeChart;
window.html2canvas = async () => ({ toDataURL: () => "data:image/png;base64,AAAA" });
window.URL.createObjectURL = () => "blob:fake";
window.URL.revokeObjectURL = () => {};
window.open = () => null;

const realFetch = globalThis.fetch;
window.fetch = (input, init) => {
  const path = String(input);
  const url = path.startsWith("http") ? path : `${API_ORIGIN}${path.startsWith("/") ? path : `/${path}`}`;
  return realFetch(url, init);
};

/* ----------------------------------------------------------------- run it */

function check(condition, message) {
  if (!condition) failures.push(message);
}

window.eval(appJs);
// app.js starts itself; only fire the event when the document is still loading.
if (window.document.readyState === "loading") {
  window.document.dispatchEvent(new window.Event("DOMContentLoaded", { bubbles: true }));
}

// Give the asynchronous bootstrap time to settle.
await new Promise((resolve) => setTimeout(resolve, 25000));

const doc = window.document;

check(doc.querySelectorAll("#basinSelect option").length === 6, "basin select must list 6 basins");
check(doc.querySelectorAll("#indicatorChips .chip").length >= 9, "indicator chips must be rendered");
check(doc.querySelectorAll("#overviewTable tbody tr").length === 6, "overview table must list every basin");
check(doc.querySelectorAll("#kpiRow .kpi").length >= 4, "overview KPI cards must render");
check(doc.querySelectorAll("#methodGrid .method-card").length >= 9, "methods panel must list every indicator");
check(doc.getElementById("regionHint").textContent.length > 10, "region hint must describe the active region");
check(doc.getElementById("legend").textContent.length > 0, "legend must render for the active indicator");
check(doc.getElementById("eeText").textContent.length > 0, "data-source status must be reported");
check(doc.getElementById("footerMeta").textContent.includes("API v"), "footer must show the API version");

// Demonstration mode must be visible when the API says so.
const readiness = await (await realFetch(`${API_ORIGIN}/api/readiness`)).json();
const banner = doc.getElementById("demoBanner");
if (readiness.demonstration_mode && readiness.demonstration_mode.active) {
  check(!banner.hidden, "demonstration banner must be visible when demonstration data is served");
  check(
    doc.querySelector("#overviewTable tbody").textContent.length > 0,
    "overview must still render in demonstration mode"
  );
}

// Exercise the time-series panel through its own button handler.
doc.getElementById("seriesStartYear").value = "2024";
doc.getElementById("seriesEndYear").value = "2026";
doc.getElementById("loadSeries").click();
await new Promise((resolve) => setTimeout(resolve, 15000));
const seriesMeta = doc.getElementById("seriesMeta").textContent;
check(seriesMeta.length > 0, "time-series panel must report metadata or an explicit error");

// Exercise the Green Legacy panel.
doc.querySelector("[data-tab='green']").click();
await new Promise((resolve) => setTimeout(resolve, 15000));
check(
  doc.querySelectorAll("#greenKpis .kpi").length >= 3,
  "Green Legacy KPIs must render"
);
check(doc.getElementById("greenNotice").textContent.length > 20, "Green Legacy limitations must be stated");

// Exercise the hydrology panel.
doc.querySelector("[data-tab='hydro']").click();
await new Promise((resolve) => setTimeout(resolve, 8000));
check(
  doc.getElementById("hydroNotice").textContent.includes("not connected"),
  "hydrology panel must state that no gauge feed is connected"
);

/* ----------------------------------------------------------------- report */

const tileErrors = drawn.tiles.filter((url) => !url.startsWith("https://"));
check(tileErrors.length === 0, `only real tile URLs may be drawn, saw ${JSON.stringify(tileErrors)}`);

if (consoleErrors.length) {
  failures.push(`browser console errors: ${consoleErrors.join(" | ")}`);
}

console.log(
  JSON.stringify(
    {
      ok: failures.length === 0,
      tiles_drawn: drawn.tiles.length,
      charts_drawn: drawn.charts.length,
      series_points: drawn.charts[0] ? drawn.charts[0].data.labels.length : 0,
      demo_banner_visible: !doc.getElementById("demoBanner").hidden,
      ee_status: doc.getElementById("eeText").textContent,
      failures,
    },
    null,
    2
  )
);

process.exit(failures.length === 0 ? 0 : 1);
