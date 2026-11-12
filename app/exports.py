"""Export builders (CSV and printable summary).

Exports always carry the selection context and the source metadata, so a
downloaded table can be traced back to basin, indicator, window, dataset, scale,
mask and processing version.  Values that are missing stay empty — they are
never filled with zeros.
"""

from __future__ import annotations

import csv
import io
from typing import Any, Iterable, Optional

GENERATED_KEY = "generated_at"


def _writer(lines: list[list[Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    for line in lines:
        writer.writerow(line)
    return buffer.getvalue()


def _metadata_lines(pairs: Iterable[tuple[str, Any]]) -> list[list[Any]]:
    return [[f"# {key}", value if value is not None else ""] for key, value in pairs]


def timeseries_csv(payload: dict[str, Any]) -> str:
    """CSV for a time-series payload (one row per period)."""

    source = payload.get("source", {}) or {}
    header = _metadata_lines(
        [
            ("export", "time_series"),
            ("basin_id", payload.get("basin_id")),
            ("basin_name", payload.get("basin_name")),
            ("region", payload.get("region_label")),
            ("indicator", f"{payload.get('label')} ({payload.get('indicator')})"),
            ("units", payload.get("units")),
            ("aggregation", payload.get("aggregation")),
            ("window_start", (payload.get("window") or {}).get("start")),
            ("window_end", (payload.get("window") or {}).get("end")),
            ("dataset", source.get("dataset")),
            ("bands", ",".join(source.get("bands") or [])),
            ("equation", source.get("equation")),
            ("scale_m", source.get("scale_m")),
            ("mask_method", source.get("mask_method")),
            ("compositing", source.get("compositing")),
            ("processing_version", source.get("processing_version")),
            ("interpolation", payload.get("interpolation")),
            ("data_status", payload.get("data_status", "")),
            ("warning", payload.get("warning", "")),
            (GENERATED_KEY, payload.get(GENERATED_KEY)),
        ]
    )
    header.append(
        [
            "period",
            "year",
            "month",
            "value",
            "std_dev",
            "observation_count",
            "valid_pixel_count",
            "latest_observation",
            "data_status",
        ]
    )
    for point in payload.get("series", []) or []:
        header.append(
            [
                point.get("period"),
                point.get("year"),
                point.get("month") if point.get("month") is not None else "",
                point.get("value") if point.get("value") is not None else "",
                point.get("std_dev") if point.get("std_dev") is not None else "",
                point.get("observation_count"),
                point.get("valid_pixel_count") if point.get("valid_pixel_count") is not None else "",
                point.get("latest_observation") or "",
                point.get("data_status", ""),
            ]
        )
    return _writer(header)


def statistics_csv(payload: dict[str, Any]) -> str:
    """CSV for a basin statistics payload (one row per indicator)."""

    indicators: dict[str, Any] = payload.get("indicators", {}) or {}
    header = _metadata_lines(
        [
            ("export", "basin_statistics"),
            ("basin_id", payload.get("basin_id")),
            ("basin_name", payload.get("basin_name")),
            ("region", payload.get("region_label")),
            ("region_area_km2", payload.get("region_area_km2")),
            ("window_start", (payload.get("window") or {}).get("start")),
            ("window_end", (payload.get("window") or {}).get("end")),
            ("data_status", payload.get("data_status")),
            ("earth_engine_available", (payload.get("earth_engine") or {}).get("available")),
            ("warning", payload.get("warning", "")),
            (GENERATED_KEY, payload.get(GENERATED_KEY)),
        ]
    )
    header.append(
        [
            "indicator",
            "label",
            "value",
            "std_dev",
            "units",
            "dataset",
            "bands",
            "equation",
            "scale_m",
            "region_area_km2",
            "observation_count",
            "valid_pixel_count",
            "coverage_fraction",
            "latest_observation",
            "observation_age_days",
            "data_status",
            "caveat",
        ]
    )
    for entry in indicators.values():
        source = entry.get("source", {}) or {}
        header.append(
            [
                entry.get("indicator"),
                entry.get("label"),
                entry.get("value") if entry.get("value") is not None else "",
                entry.get("std_dev") if entry.get("std_dev") is not None else "",
                entry.get("units"),
                source.get("dataset"),
                ",".join(source.get("bands") or []),
                source.get("equation"),
                source.get("scale_m"),
                payload.get("region_area_km2"),
                entry.get("observation_count"),
                entry.get("valid_pixel_count") if entry.get("valid_pixel_count") is not None else "",
                entry.get("coverage_fraction") if entry.get("coverage_fraction") is not None else "",
                entry.get("latest_observation") or "",
                entry.get("observation_age_days") if entry.get("observation_age_days") is not None else "",
                entry.get("data_status"),
                entry.get("caveat") or entry.get("note") or "",
            ]
        )
    return _writer(header)


def change_csv(payload: dict[str, Any]) -> str:
    """CSV for a change-detection payload."""

    stats = payload.get("statistics", {}) or {}
    baseline = stats.get("baseline", {}) or {}
    comparison = stats.get("comparison", {}) or {}
    source = stats.get("source", {}) or {}
    rows = _metadata_lines(
        [
            ("export", "change_detection"),
            ("basin_id", payload.get("basin_id")),
            ("basin_name", payload.get("basin_name")),
            ("region", payload.get("region_label")),
            ("indicator", f"{stats.get('label')} ({stats.get('indicator')})"),
            ("units", stats.get("units")),
            ("dataset", source.get("dataset")),
            ("scale_m", source.get("scale_m")),
            ("processing_version", source.get("processing_version")),
            ("baseline_window", f"{(baseline.get('window') or {}).get('start')} to {(baseline.get('window') or {}).get('end')}"),
            ("comparison_window", f"{(comparison.get('window') or {}).get('start')} to {(comparison.get('window') or {}).get('end')}"),
            ("comparable", stats.get("comparable")),
            (GENERATED_KEY, payload.get(GENERATED_KEY)),
        ]
    )
    rows.append(["metric", "value", "unit"])
    rows.append(["baseline_mean", baseline.get("value") if baseline.get("value") is not None else "", stats.get("units")])
    rows.append(["baseline_observations", baseline.get("observation_count"), "scenes"])
    rows.append(["baseline_latest_observation", baseline.get("latest_observation") or "", "UTC"])
    rows.append(["comparison_mean", comparison.get("value") if comparison.get("value") is not None else "", stats.get("units")])
    rows.append(["comparison_observations", comparison.get("observation_count"), "scenes"])
    rows.append(["comparison_latest_observation", comparison.get("latest_observation") or "", "UTC"])
    rows.append(["difference", stats.get("difference") if stats.get("difference") is not None else "", stats.get("difference_units")])
    rows.append(["percentage_change", stats.get("percentage_change") if stats.get("percentage_change") is not None else "", "%"])
    rows.append(["percentage_change_basis", stats.get("percentage_change_basis"), ""])
    rows.append(["coverage_fraction", (stats.get("comparability_checks") or {}).get("coverage_fraction"), "fraction of region"])
    return _writer(rows)


def csv_filename(kind: str, basin_id: str, indicator: Optional[str] = None) -> str:
    parts = [kind, basin_id]
    if indicator:
        parts.append(indicator)
    return "_".join(parts) + ".csv"


__all__ = ["change_csv", "csv_filename", "statistics_csv", "timeseries_csv"]
