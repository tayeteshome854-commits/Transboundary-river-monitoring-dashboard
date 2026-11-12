#!/usr/bin/env python3
"""Warm the derived-geometry cache.

The application derives simplified GeoJSON for every vector layer on first use
and caches it under ``data/cache/``.  Running this script ahead of time removes
that first-request latency (the Green Legacy layer has 21,429 polygons).

Usage:
    python scripts/build_geojson.py            # build everything
    python scripts/build_geojson.py --clear    # delete the cache first
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import geometry  # noqa: E402
from app.config import BASINS  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clear", action="store_true", help="delete the existing cache first")
    args = parser.parse_args()

    if args.clear:
        geometry.clear_geometry_cache()
        print("cleared geometry cache")

    started = time.time()
    geometry.basins_catalog()
    print("basin catalog built")

    for basin in BASINS:
        for kind in ("basin", "buffer"):
            geometry.basin_geojson(basin.id, kind)
        print(f"  {basin.id}: basin + buffer")

    geometry.green_legacy_summary()
    geometry.green_legacy_by_wereda()
    geometry.green_legacy_geojson(basin_id=None, mode="points", limit=5000)
    print("green legacy summary + wereda aggregate + points")

    entries = sorted(geometry.CACHE_DIR.glob("*.geojson")) if geometry.CACHE_DIR.exists() else []
    total = sum(entry.stat().st_size for entry in entries)
    print(
        f"done in {time.time() - started:.1f}s — {len(entries)} cache entries, "
        f"{total / 1024:.0f} kB in {geometry.CACHE_DIR}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
