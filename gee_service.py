import ee
import datetime
from cachetools import TTLCache
import logging

from ee import collection

# =====================================
# LOGGING
# =====================================

logging.basicConfig(level=logging.INFO)

# =====================================
# EARTH ENGINE INIT
# =====================================

try:

    ee.Initialize(
        project='enduring-art-494812-u2'
    )

    logging.info(
        "✓ Earth Engine initialized"
    )

except Exception:

    ee.Authenticate()

    ee.Initialize(
        project='enduring-art-494812-u2'
    )

# =====================================
# CACHE
# =====================================

stats_cache = TTLCache(
    maxsize=100,
    ttl=3600
)

timeseries_cache = TTLCache(
    maxsize=100,
    ttl=3600
)

# =====================================
# BASIN ASSETS
# =====================================

BASINS = {

    'Abay':
    'projects/enduring-art-494812-u2/assets/Abay_basin',

    'Baro_Akobo':
    'projects/enduring-art-494812-u2/assets/Baro_Akobo_basin_new',

    'Genale_Dawa':
    'projects/enduring-art-494812-u2/assets/Genale_Dawa_basin',

    'Omo':
    'projects/enduring-art-494812-u2/assets/Omo_basin',

    'Tekeze':
    'projects/enduring-art-494812-u2/assets/Tekeze_basin',

    'Wabe_Shabale':
    'projects/enduring-art-494812-u2/assets/Wabe_Shabale_basin'
}

# =====================================
# BUFFER ASSETS
# =====================================

BUFFERS = {

    'Abay':
    'projects/enduring-art-494812-u2/assets/Abay_river_buffer',

    'Baro_Akobo':
    'projects/enduring-art-494812-u2/assets/Baro_Akobo_river_buffer',

    'Genale_Dawa':
    'projects/enduring-art-494812-u2/assets/Genale_Dawa_river_buffer',

    'Omo':
    'projects/enduring-art-494812-u2/assets/Omo_river_buffer',

    'Tekeze':
    'projects/enduring-art-494812-u2/assets/Tekeze_river_buffer',

    'Wabe_Shabale':
    'projects/enduring-art-494812-u2/assets/Wabe_Shabale_river_buffer'
}

# =====================================
# GREEN LEGACY
# =====================================

GREEN_LEGACY = (
    'projects/enduring-art-494812-u2/assets/Green_legacy'
)

# =====================================
# LOAD BASIN
# =====================================

def get_basin(name):

    return (
        ee.FeatureCollection(
            BASINS[name]
        )
        .map(
            lambda f: f.simplify(1000)
        )
    )

# =====================================
# LOAD BUFFER
# =====================================

def get_buffer(name):

    return (
        ee.FeatureCollection(
            BUFFERS[name]
        )
        .map(
            lambda f: f.simplify(500)
        )
    )

# =====================================
# LOAD GREEN LEGACY
# =====================================

def get_green_legacy():

    return (
        ee.FeatureCollection(
            GREEN_LEGACY
        )
        .map(
            lambda f: f.simplify(500)
        )
    )

# =====================================
# CLOUD MASK
# =====================================

def mask_s2_clouds(image):

    qa = image.select('QA60')

    cloudBitMask = 1 << 10
    cirrusBitMask = 1 << 11

    mask = (
        qa.bitwiseAnd(cloudBitMask).eq(0)
        .And(
            qa.bitwiseAnd(cirrusBitMask).eq(0)
        )
    )

    return (
        image
        .updateMask(mask)
        .divide(10000)
    )

# =====================================
# ADD INDICES
# =====================================

def add_indices(img):

    ndvi = (
        img.normalizedDifference(
            ['B8', 'B4']
        )
        .rename('NDVI')
    )

    ndwi = (
        img.normalizedDifference(
            ['B3', 'B8']
        )
        .rename('NDWI')
    )

    mndwi = (
        img.normalizedDifference(
            ['B3', 'B11']
        )
        .rename('MNDWI')
    )

    evi = (
        img.expression(
            '2.5 * ((NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1))',
            {
                'NIR': img.select('B8'),
                'RED': img.select('B4'),
                'BLUE': img.select('B2')
            }
        )
        .rename('EVI')
    )

    return img.addBands([
        ndvi,
        ndwi,
        mndwi,
        evi
    ])

# =====================================
# SENTINEL COLLECTION
# =====================================

def sentinel_collection(
    start_date,
    end_date,
    region
):

    return (
        ee.ImageCollection(
            'COPERNICUS/S2_SR_HARMONIZED'
        )
        .filterBounds(region)
        .filterDate(
            start_date,
            end_date
        )
        .filter(
            ee.Filter.lt(
                'CLOUDY_PIXEL_PERCENTAGE',
                30
            )
        )
        .map(mask_s2_clouds)
        .map(add_indices)
    )

# =====================================
# RAINFALL
# =====================================

def rainfall_collection(
    start,
    end,
    region
):

    collection = (
        ee.ImageCollection(
            "UCSB-CHG/CHIRPS/DAILY"
        )
        .filterDate(start, end)
        .filterBounds(region)
    )

    rainfall = (
        collection
        .sum()
        .rename("RAINFALL")
    )

    return rainfall

# =====================================
# LAND SURFACE TEMPERATURE
# =====================================

def lst_collection(
    start,
    end,
    region
):

    collection = (
        ee.ImageCollection(
            "MODIS/061/MOD11A2"
        )
        .filterDate(start, end)
        .filterBounds(region)
        .select("LST_Day_1km")
    )

    lst = (
        collection
        .mean()
        .multiply(0.02)
        .subtract(273.15)
        .rename("LST")
    )

    return lst

# =====================================
# AIR TEMPERATURE
# =====================================

def airtemp_collection(
    start,
    end,
    region
):

    collection = (
        ee.ImageCollection(
            "ECMWF/ERA5_LAND/DAILY_AGGR"
        )
        .filterDate(start, end)
        .filterBounds(region)
        .select("temperature_2m")
    )

    temp = (
        collection
        .mean()
        .subtract(273.15)
        .rename("AIR_TEMP")
    )

    return temp

# =====================================
# EVAPOTRANSPIRATION
# =====================================

def et_collection(
    start,
    end,
    region
):

    collection = (
        ee.ImageCollection(
            "MODIS/061/MOD16A2GF"
        )
        .filterDate(start, end)
        .filterBounds(region)
        .select("ET")
    )

    count = collection.size()

    image = ee.Image(
        ee.Algorithms.If(
            count.gt(0),

            collection.mean()
            .multiply(0.1)
            .rename("ET"),

            ee.Image.constant(0)
            .rename("ET")
        )
    )

    return image

    collection = (
        ee.ImageCollection(
            "MODIS/061/MOD16A2"
        )
        .filterDate(start, end)
        .filterBounds(region)
        .select("ET")
    )

    et = (
        collection
        .mean()
        .multiply(0.1)
        .rename("ET")
    )

    return et

# =====================================
# SOIL MOISTURE
# =====================================

def soil_moisture_collection(
    start,
    end,
    region
):

    collection = (
        ee.ImageCollection(
            "NASA/SMAP/SPL4SMGP/007"
        )
        .filterDate(start, end)
        .filterBounds(region)
        .select("sm_surface")
    )

    soil = ee.Image(
        ee.Algorithms.If(
            collection.size().gt(0),
            collection.mean(),
            ee.Image.constant(0)
        )
    ).rename("SOIL_MOISTURE")

    return soil

# =====================================
# BASIN STATISTICS
# =====================================

def get_basin_statistics(
    basin_name
):

    if basin_name in stats_cache:

        return stats_cache[
            basin_name
        ]

    basin = get_basin(
        basin_name
    )
    buffer_zone = get_buffer(
        basin_name
    )
    end_date = datetime.datetime.now()

    start_date = (
        end_date -
        datetime.timedelta(days=60)
    )

    image = (
        sentinel_collection(
            start_date.strftime('%Y-%m-%d'),
            end_date.strftime('%Y-%m-%d'),
            basin
        )
        .median()
    )

    stats = (
        image.select([
            'NDVI',
            'NDWI',
            'MNDWI'
        ])
        .reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=basin.geometry(),
            scale=5000,
            bestEffort=True,
            maxPixels=1e9
        )
    )

    result = stats.getInfo()

    output = {

        'NDVI':
        round(
            result.get('NDVI', 0),
            4
        ),

        'NDWI':
        round(
            result.get('NDWI', 0),
            4
        ),

        'MNDWI':
        round(
            result.get('MNDWI', 0),
            4
        )
    }

    stats_cache[
        basin_name
    ] = output

    return output

# =====================================
# VARIABLE TIMESERIES
# =====================================

def variable_timeseries(
    basin_name,
    variable
):

    cache_key = f"{basin_name}_{variable}"

    if cache_key in timeseries_cache:
        return timeseries_cache[cache_key]

    basin = get_basin(basin_name)
    buffer_zone = get_buffer(basin_name)

    current_year = datetime.datetime.now().year
    years = list(
        range(2015, current_year)
    )

    results = []

    for year in years:

        start = f"{year}-01-01"
        end = f"{year}-12-31"

        try:

            # =========================
            # NDVI
            # =========================

            if variable == "NDVI":

                image = (
                    ee.ImageCollection(
                        "MODIS/061/MOD13Q1"
                    )
                    .filterDate(start, end)
                    .filterBounds(basin)
                    .select("NDVI")
                    .mean()
                    .multiply(0.0001)
                )
            # =========================
            # EVI
            # =========================
            elif variable == "EVI":

                green = get_green_legacy()

                image = (
                    sentinel_collection(
                        start,
                        end,
                        green
                    )
                    .median()
                    .select("EVI")
                    .clip(green)
                )
            # =========================
            # NDWI
            # =========================
            elif variable == "NDWI":

                image = (
                    sentinel_collection(
                        start,
                        end,
                        buffer_zone
                    )
                    .median()
                    .select("NDWI")
                    .clip(buffer_zone)
                )
            # =========================
            # MNDWI
            # =========================
            elif variable == "MNDWI":

                image = (
                    sentinel_collection(
                        start,
                        end,
                        buffer_zone
                    )
                    .median()
                    .select("MNDWI")
                    .clip(buffer_zone)
                )

            # =========================
            # RAINFALL
            # =========================

            elif variable == "RAINFALL":

                image = rainfall_collection(
                    start,
                    end,
                    basin
                )

            # =========================
            # LST
            # =========================

            elif variable == "LST":

                image = lst_collection(
                    start,
                    end,
                    basin
                )

            # =========================
            # AIR TEMP
            # =========================

            elif variable == "AIR_TEMP":

                image = airtemp_collection(
                    start,
                    end,
                    basin
                )

            # =========================
            # ET
            # =========================

            elif variable == "ET":

                image = et_collection(
                    start,
                    end,
                    basin
                )

            # =========================
            # SOIL MOISTURE
            # =========================

            elif variable == "SOIL_MOISTURE":

                image = soil_moisture_collection(
                    start,
                    end,
                    basin
                )

            else:

                continue

            if variable in ["NDWI", "MNDWI"]:
                stat_geometry = buffer_zone.geometry()
            elif variable == "EVI":
                stat_geometry = get_green_legacy().geometry()
            else:
                stat_geometry = basin.geometry()

            stat = image.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=stat_geometry,
                scale=5000,
                bestEffort=True,
                tileScale=16,
                maxPixels=1e9
            )

            dictionary = stat.getInfo()

            value = dictionary.get(variable)

            if value is not None:
                v = round(float(value), 4)
            else:
                v = None

            results.append({
                "year": year,
                "value": v
            })

        except Exception as e:

            print(
                f"Timeseries Error {year}: {e}"
            )

            results.append({
                "year": year,
                "value": None
            })

    timeseries_cache[cache_key] = results

    return results

# =====================================
# TILE LAYER
# =====================================

def get_tile_layer(
    basin_name,
    variable='NDVI',
    year=2025
):

    basin = get_basin(
        basin_name
    )

    buffer_zone = get_buffer(
        basin_name
    )

    start = f"{year}-01-01"
    end = f"{year}-12-31"

    # =====================================
    # NDVI
    # =====================================

    if variable == "NDVI":

        image = (
            sentinel_collection(
                start,
                end,
                basin
            )
            .median()
            .select("NDVI")
        )

        vis = {
            'min': -0.2,
            'max': 0.8,
            'palette': [
                'brown',
                'yellow',
                'green'
            ]
        }
    # =====================================
    # EVI
    # =====================================

    elif variable == "EVI":

        image = (
            sentinel_collection(
                start,
                end,
                get_green_legacy()
            )
            .median()
            .select("EVI")
            .clip(get_green_legacy())
        )

        vis = {
            'min': -0.2,
            'max': 1,
            'palette': [
                'brown',
                'yellow',
                'green',
                'darkgreen'
            ]
        }
    # =====================================
    # NDWI
    # =====================================

    elif variable == "NDWI":

        image = (
            sentinel_collection(
                start,
                end,
                buffer_zone
            )
            .median()
            .select("NDWI")
            .clip(buffer_zone)
        )

        vis = {
            'min': -1,
            'max': 1,
            'palette': [
                'white',
                'blue'
            ]
        }

    # =====================================
    # MNDWI
    # =====================================

    elif variable == "MNDWI":

        image = (
            sentinel_collection(
                start,
                end,
                buffer_zone
            )
            .median()
            .select("MNDWI")
            .clip(buffer_zone)
        )

        vis = {
            'min': -1,
            'max': 1,
            'palette': [
                'black',
                'cyan'
            ]
        }

    # =====================================
    # RAINFALL
    # =====================================

    elif variable == "RAINFALL":

        image = rainfall_collection(
            start,
            end,
            basin
        )

        vis = {
            'min': 0,
            'max': 400,
            'palette': [
                'white',
                'blue',
                'darkblue'
            ]
        }

    # =====================================
    # LST
    # =====================================

    elif variable == "LST":

        image = lst_collection(
            start,
            end,
            basin
        )

        vis = {
            'min': 10,
            'max': 45,
            'palette': [
                'blue',
                'yellow',
                'red'
            ]
        }

    # =====================================
    # AIR TEMP
    # =====================================

    elif variable == "AIR_TEMP":

        image = airtemp_collection(
            start,
            end,
            basin
        )

        vis = {
            'min': 5,
            'max': 40,
            'palette': [
                'purple',
                'cyan',
                'orange',
                'red'
            ]
        }

    # =====================================
    # ET
    # =====================================

    elif variable == "ET":

        image = et_collection(
            start,
            end,
            basin
        )

        vis = {
            'min': 0,
            'max': 80,
            'palette': [
                'white',
                'lightgreen',
                'darkgreen'
            ]
        }

    # =====================================
    # SOIL MOISTURE
    # =====================================

    elif variable == "SOIL_MOISTURE":

        image = soil_moisture_collection(
            start,
            end,
            basin
        )

        vis = {
            'min': 0,
            'max': 50,
            'palette': [
                'brown',
                'yellow',
                'blue'
            ]
        }

    else:

        raise Exception(
            f"Unsupported variable: {variable}"
        )

    if variable not in [
        "NDWI",
        "MNDWI",
        "EVI"
    ]:
        image = image.clip(basin)

    map_id = image.getMapId(vis)

    return map_id['tile_fetcher'].url_format
# =====================================
# GEOJSON
# =====================================

def get_geojson(
    feature_collection
):

    geometry = (
        feature_collection
        .geometry()
        .getInfo()
    )

    return {

        "type":
        "FeatureCollection",

        "features":
        [
            {
                "type":
                "Feature",

                "geometry":
                geometry,

                "properties":
                {}
            }
        ]
    }

# =====================================
# BASIN GEOJSON
# =====================================

def get_basin_geojson(
    name
):

    return get_geojson(
        get_basin(name)
    )

# =====================================
# BUFFER GEOJSON
# =====================================

def get_buffer_geojson(
    name
):

    return get_geojson(
        get_buffer(name)
    )

# =====================================
# GREEN LEGACY GEOJSON
# =====================================

def get_green_legacy_geojson():

    return get_geojson(
        get_green_legacy()
    )