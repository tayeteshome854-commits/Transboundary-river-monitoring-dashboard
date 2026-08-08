from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import uvicorn

from gee_service import (
    get_basin_statistics,
    variable_timeseries,
    get_tile_layer,
    get_basin_geojson,
    get_buffer_geojson,
    get_green_legacy_geojson
)

# ==========================================
# FASTAPI APP
# ==========================================

app = FastAPI(
    title="Ethiopian Transboundary rivers monitoring Dashboard API",
    version="4.0"
)

# ==========================================
# CORS
# ==========================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# BASE DIRECTORY
# ==========================================

BASE_DIR = Path(__file__).parent

# ==========================================
# STATIC FILES
# ==========================================

app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR)),
    name="static"
)

# ==========================================
# SERVE INDEX
# ==========================================

@app.get("/")
async def serve_index():
    return FileResponse(BASE_DIR / "index.html")

# ==========================================
# API STATUS
# ==========================================

@app.get("/api/status")
async def api_status():

    return {
        "status": "running",
        "message": "GLTB API Active",
        "version": "4.0"
    }

# ==========================================
# BASIN STATS
# ==========================================

@app.get("/api/basin/{basin_name}/stats")
async def basin_stats(basin_name: str):

    try:

        return get_basin_statistics(basin_name)

    except HTTPException:
        raise

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )
# ==========================================
# BASIN GEOJSON
# ==========================================

@app.get("/api/basin/{basin_name}/geojson")
async def basin_geojson(basin_name: str):

    try:

        return get_basin_geojson(basin_name)

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

# ==========================================
# BUFFER GEOJSON
# ==========================================

@app.get("/api/buffer/{basin_name}/geojson")
async def buffer_geojson(basin_name: str):

    try:

        return get_buffer_geojson(basin_name)

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

# ==========================================
# GREEN LEGACY GEOJSON
# ==========================================

@app.get("/api/green-legacy/geojson")
async def green_legacy_geojson():

    try:

        return get_green_legacy_geojson()

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

# ==========================================
# TIMESERIES
# ==========================================

@app.get("/api/basin/{basin_name}/timeseries/{variable}")
async def basin_timeseries(
    basin_name: str,
    variable: str
):

    try:

        variable = variable.upper()

        if variable not in [
    "NDVI",
    "EVI",
    "NDWI",
    "MNDWI",
    "RAINFALL",
    "LST",
    "AIR_TEMP",
    "ET",
    "SOIL_MOISTURE"
]:
            raise HTTPException(
                status_code=400,
                detail="Invalid variable"
            )

        return variable_timeseries(
            basin_name,
            variable
        )

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

# ==========================================
# TILE LAYER
# ==========================================

@app.get("/api/tiles/{basin_name}/{variable}/{year}")
async def tile_layer(
    basin_name: str,
    variable: str,
    year: int
):

    try:

        variable = variable.upper()

        if variable not in [
            "NDVI",
            "EVI",
            "NDWI",
            "MNDWI",
            "RAINFALL",
            "LST",
            "AIR_TEMP",
            "ET",
            "SOIL_MOISTURE"
        ]:

            raise HTTPException(
                status_code=400,
                detail="Invalid variable"
            )

        tile_url = get_tile_layer(
            basin_name,
            variable,
            year
        )

        return {
            "tile_url": tile_url
        }

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )
# ==========================================
# RUN SERVER
# ==========================================

if __name__ == "__main__":

    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8010,
        reload=True
    )