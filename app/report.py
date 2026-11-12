"""Printable analytical summary (browser "Save as PDF").

Rendering a summary as HTML avoids adding a PDF library: the browser's print
engine produces the document.  Everything shown here is produced by the same
service layer the API uses, so the report cannot disagree with the dashboard.
"""

from __future__ import annotations

import html
import time
from typing import Any, Optional

from . import demo, geometry, service
from .config import get_basin
from .errors import DataStatus, EarthEngineUnavailableError
from .indicators import get_indicator


def _esc(value: Any) -> str:
    return html.escape(str(value)) if value is not None else "—"


def _status_row(entry: dict[str, Any]) -> str:
    value = entry.get("value")
    status = entry.get("data_status", "")
    return (
        "<tr>"
        f"<td>{_esc(entry.get('label'))}</td>"
        f"<td class='num'>{_esc(round(value, 4) if isinstance(value, (int, float)) else 'no valid observation')}</td>"
        f"<td>{_esc(entry.get('units'))}</td>"
        f"<td>{_esc(entry.get('observation_count'))}</td>"
        f"<td>{_esc(entry.get('valid_pixel_count'))}</td>"
        f"<td>{_esc(entry.get('coverage_fraction'))}</td>"
        f"<td>{_esc(entry.get('latest_observation'))}</td>"
        f"<td class='status status-{_esc(status)}'>{_esc(status)}</td>"
        "</tr>"
    )


def build_report(
    basin_id: str,
    indicator_id: str,
    window: dict[str, str],
    start_year: int,
    end_year: int,
) -> str:
    """Build the printable summary document for one basin and indicator."""

    basin = get_basin(basin_id)
    indicator = get_indicator(indicator_id)
    generated = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())

    warning = ""
    stats_payload: Optional[dict[str, Any]] = None
    series_payload: Optional[dict[str, Any]] = None
    error_note = ""

    try:
        stats_payload = service.basin_statistics(basin_id, ["ndvi", "ndwi", "mndwi"], window, "basin")
        series_payload = service.time_series(basin_id, indicator_id, start_year, end_year, "year", "basin")
    except EarthEngineUnavailableError as exc:
        if demo.demo_active():
            warning = demo.WARNING
            stats_payload = service.demo_statistics(basin_id, ["ndvi", "ndwi", "mndwi"], window, "basin")
            series_payload = demo.demo_time_series(basin_id, indicator_id, start_year, end_year, "year")
        else:
            error_note = (
                "Earth Engine is not connected, so no satellite statistics could be computed for this "
                f"report. Reason: {_esc(exc.message)}"
            )

    catalog = {record["id"]: record for record in geometry.basins_catalog()}
    record = catalog.get(basin_id, {})

    indicator_rows = ""
    if stats_payload:
        for entry in (stats_payload.get("indicators") or {}).values():
            indicator_rows += _status_row(entry)

    series_rows = ""
    if series_payload:
        for point in series_payload.get("series", []) or []:
            series_rows += (
                "<tr>"
                f"<td>{_esc(point.get('period'))}</td>"
                f"<td class='num'>{_esc(point.get('value'))}</td>"
                f"<td>{_esc(point.get('observation_count'))}</td>"
                f"<td>{_esc(point.get('latest_observation'))}</td>"
                f"<td class='status'>{_esc(point.get('data_status'))}</td>"
                "</tr>"
            )

    provenance_rows = ""
    if series_payload:
        for key, value in (series_payload.get("source") or {}).items():
            provenance_rows += f"<tr><td>{_esc(key)}</td><td>{_esc(value)}</td></tr>"

    warning_block = (
        f"<div class='banner'>{_esc(warning)}</div>" if warning else ""
    )
    error_block = f"<div class='banner banner-error'>{error_note}</div>" if error_note else ""

    scope = (
        "This dashboard provides near-real-time satellite monitoring of vegetation and surface-water "
        "indicators. Sentinel-2 revisit frequency, cloud cover and processing delays mean it does not "
        "provide continuous river-level or discharge monitoring. Satellite indices are not hydrological "
        "measurements."
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{_esc(basin.display_name)} — monitoring summary</title>
<style>
  @page {{ size: A4; margin: 18mm; }}
  body {{ font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; color: #111827; font-size: 12px; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  h2 {{ font-size: 14px; margin: 22px 0 6px; border-bottom: 1px solid #d1d5db; padding-bottom: 3px; }}
  .muted {{ color: #6b7280; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 6px; }}
  th, td {{ border: 1px solid #e5e7eb; padding: 5px 7px; text-align: left; vertical-align: top; }}
  th {{ background: #f3f4f6; font-weight: 600; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .banner {{ background: #fef3c7; border: 1px solid #f59e0b; padding: 8px 10px; margin: 10px 0; font-weight: 600; }}
  .banner-error {{ background: #fee2e2; border-color: #ef4444; font-weight: 500; }}
  .status {{ text-transform: uppercase; font-size: 10px; letter-spacing: .04em; }}
  footer {{ margin-top: 24px; color: #6b7280; font-size: 10px; }}
</style>
</head>
<body>
<h1>Ethiopian Transboundary River Monitoring — {_esc(basin.display_name)}</h1>
<p class="muted">Generated {_esc(generated)} · reporting window {_esc(window['start'])} to {_esc(window['end'])}</p>
{warning_block}
{error_block}

<h2>1. Basin</h2>
<table>
  <tr><th>Basin</th><td>{_esc(basin.display_name)}</td></tr>
  <tr><th>River</th><td>{_esc(basin.river_name)}</td></tr>
  <tr><th>Area (equal-area projection)</th><td>{_esc(record.get('area_km2'))} km²</td></tr>
  <tr><th>River corridor buffer area</th><td>{_esc(record.get('buffer_area_km2'))} km² ({_esc(record.get('buffer_distance_m'))} m buffer)</td></tr>
  <tr><th>Source data</th><td>{_esc(record.get('source', {}).get('basin_file'))} — {_esc(record.get('source', {}).get('basin_crs'))}</td></tr>
  <tr><th>Data notes</th><td>{_esc('; '.join(record.get('warnings', [])) or 'none')}</td></tr>
</table>

<h2>2. Satellite indicators for the reporting window</h2>
<table>
  <tr><th>Indicator</th><th>Value</th><th>Units</th><th>Observations</th><th>Valid pixels</th><th>Coverage</th><th>Latest observation</th><th>Status</th></tr>
  {indicator_rows or '<tr><td colspan="8">No statistics available for this window.</td></tr>'}
</table>

<h2>3. {_esc(indicator.label)} annual series ({_esc(start_year)}–{_esc(end_year)})</h2>
<table>
  <tr><th>Period</th><th>Value</th><th>Observations</th><th>Latest observation</th><th>Status</th></tr>
  {series_rows or '<tr><td colspan="5">No time series available.</td></tr>'}
</table>

<h2>4. Method and provenance</h2>
<table>
  {provenance_rows or '<tr><td>No processing metadata available.</td></tr>'}
  <tr><td>Area projection</td><td>{_esc(geometry.AREA_CRS_NAME)}</td></tr>
  <tr><td>Output CRS</td><td>EPSG:4326 (map) / Web Mercator (tiles)</td></tr>
</table>

<h2>5. Interpretation limits</h2>
<ul>
  <li>{_esc(indicator.caveat or 'See docs/SCIENTIFIC_METHODS.md for indicator limitations.')}</li>
  <li>NDVI decline alone is not evidence of drought; read it with rainfall, temperature and soil moisture.</li>
  <li>NDWI/MNDWI thresholds are documented heuristics, not locally validated water-body classifications.</li>
  <li>Green Legacy planting records are not a measure of tree survival, and NDVI does not count trees.</li>
</ul>

<h2>6. Scope</h2>
<p>{_esc(scope)}</p>

<footer>
  Ethiopian Transboundary River Monitoring Dashboard · Space Science and Geospatial Institute (SSGI) ·
  data sources and equations: docs/SCIENTIFIC_METHODS.md
</footer>
</body>
</html>"""


__all__ = ["build_report"]
