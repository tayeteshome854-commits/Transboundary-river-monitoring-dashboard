"""Export tests: every download must carry context, provenance and real values."""

from __future__ import annotations

from app import exports


STATS_PAYLOAD = {
    "basin_id": "tekeze",
    "basin_name": "Tekeze",
    "region_label": "Tekeze — whole basin",
    "region_area_km2": 61667.7,
    "window": {"start": "2026-08-01", "end": "2026-11-01"},
    "data_status": "current",
    "earth_engine": {"available": True},
    "generated_at": "2026-11-12T09:00:00Z",
    "indicators": {
        "ndvi": {
            "indicator": "ndvi",
            "label": "NDVI",
            "value": 0.6364,
            "std_dev": 0.05,
            "units": "dimensionless (-1 to 1)",
            "observation_count": 12,
            "valid_pixel_count": 600000,
            "coverage_fraction": 0.97,
            "latest_observation": "2026-10-28T10:00:00Z",
            "observation_age_days": 5.0,
            "data_status": "current",
            "caveat": "Not proof of drought on its own.",
            "source": {
                "dataset": "COPERNICUS/S2_SR_HARMONIZED",
                "bands": ["B8", "B4"],
                "equation": "(B8 - B4) / (B8 + B4)",
                "scale_m": 10,
            },
        },
        "ndwi": {
            "indicator": "ndwi",
            "label": "NDWI",
            "value": None,
            "std_dev": None,
            "units": "dimensionless (-1 to 1)",
            "observation_count": 0,
            "valid_pixel_count": None,
            "coverage_fraction": None,
            "latest_observation": None,
            "data_status": "insufficient_observations",
            "source": {"dataset": "COPERNICUS/S2_SR_HARMONIZED", "bands": ["B3", "B8"], "scale_m": 10},
        },
    },
}


SERIES_PAYLOAD = {
    "basin_id": "tekeze",
    "basin_name": "Tekeze",
    "region_label": "Tekeze — whole basin",
    "indicator": "ndvi",
    "label": "NDVI",
    "units": "dimensionless (-1 to 1)",
    "aggregation": "year",
    "window": {"start": "2024-01-01", "end": "2026-01-01"},
    "interpolation": "none — gaps are reported as missing observations",
    "generated_at": "2026-11-12T09:00:00Z",
    "source": {
        "dataset": "COPERNICUS/S2_SR_HARMONIZED",
        "bands": ["B8", "B4"],
        "equation": "(B8 - B4) / (B8 + B4)",
        "scale_m": 10,
        "mask_method": "SCL classes plus QA60",
        "compositing": "per-pixel median",
        "processing_version": "test",
    },
    "series": [
        {"period": "2024", "year": 2024, "month": None, "value": 0.6, "observation_count": 8,
         "latest_observation": "2024-08-01T10:00:00Z", "data_status": "current", "valid_pixel_count": 500},
        {"period": "2025", "year": 2025, "month": None, "value": None, "observation_count": 0,
         "latest_observation": None, "data_status": "insufficient_observations", "valid_pixel_count": 0},
    ],
}


def test_statistics_csv_carries_selection_and_provenance():
    csv_text = exports.statistics_csv(STATS_PAYLOAD)
    lines = csv_text.splitlines()
    assert any(line.startswith("# basin_name,Tekeze") for line in lines)
    assert any("COPERNICUS/S2_SR_HARMONIZED" in line for line in lines)
    assert any("(B8 - B4) / (B8 + B4)" in line for line in lines)
    header = next(line for line in lines if line.startswith("indicator,label"))
    assert "valid_pixel_count" in header and "coverage_fraction" in header


def test_statistics_csv_leaves_missing_values_empty():
    csv_text = exports.statistics_csv(STATS_PAYLOAD)
    ndwi_row = next(line for line in csv_text.splitlines() if line.startswith("ndwi,"))
    fields = ndwi_row.split(",")
    # value and std_dev are the 3rd and 4th columns and must be blank, not zero.
    assert fields[2] == ""
    assert fields[3] == ""
    assert "insufficient_observations" in ndwi_row


def test_timeseries_csv_records_gaps_and_metadata():
    csv_text = exports.timeseries_csv(SERIES_PAYLOAD)
    assert "# interpolation,none — gaps are reported as missing observations" in csv_text
    assert "# dataset,COPERNICUS/S2_SR_HARMONIZED" in csv_text
    rows = [line for line in csv_text.splitlines() if line.startswith("2024,") or line.startswith("2025,")]
    assert len(rows) == 2
    assert rows[1].split(",")[3] == ""  # 2025 value is missing


def test_change_csv_reports_comparability():
    payload = {
        "basin_id": "tekeze",
        "basin_name": "Tekeze",
        "region_label": "Tekeze — whole basin",
        "generated_at": "2026-11-12T09:00:00Z",
        "statistics": {
            "label": "NDVI",
            "indicator": "ndvi",
            "units": "dimensionless",
            "difference_units": "dimensionless (comparison minus baseline)",
            "baseline": {"window": {"start": "2025-07-01", "end": "2025-09-01"}, "value": 0.50,
                         "observation_count": 6, "latest_observation": "2025-08-01T00:00:00Z"},
            "comparison": {"window": {"start": "2026-07-01", "end": "2026-09-01"}, "value": 0.58,
                           "observation_count": 9, "latest_observation": "2026-08-01T00:00:00Z"},
            "difference": 0.08,
            "percentage_change": 16.0,
            "percentage_change_basis": "((comparison - baseline) / |baseline|) * 100",
            "comparable": True,
            "comparability_checks": {"coverage_fraction": 0.93},
            "source": {"dataset": "COPERNICUS/S2_SR_HARMONIZED", "scale_m": 10, "processing_version": "test"},
        },
    }
    csv_text = exports.change_csv(payload)
    assert "comparable,True" in csv_text
    assert "percentage_change,16.0,%" in csv_text
    assert "coverage_fraction,0.93" in csv_text


def test_csv_filenames_are_descriptive():
    assert exports.csv_filename("statistics", "abay") == "statistics_abay.csv"
    assert exports.csv_filename("timeseries", "omo", "mndwi") == "timeseries_omo_mndwi.csv"
