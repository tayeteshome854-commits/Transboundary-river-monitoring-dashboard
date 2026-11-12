"""Ethiopian Transboundary River Monitoring Dashboard - application package.

Layout
------
``app.config``      environment-driven settings, basin registry
``app.indicators``  indicator registry (bands, equations, scales, units, palettes)
``app.geometry``    local vector data (shapefiles) -> validated GeoJSON
``app.ee_client``   lazy Earth Engine initialisation, availability, health
``app.satellite``   Sentinel-2 / ancillary collection building, masking, compositing
``app.statistics``  zonal statistics with valid-pixel accounting
``app.timeseries``  annual / monthly time series
``app.change``      baseline vs comparison period difference products
``app.tiles``       Earth Engine tile URLs and visualisation parameters
``app.exports``     CSV / GeoJSON / PDF export builders
``app.demo``        isolated, clearly-labelled DEMONSTRATION DATA provider
``app.main``        FastAPI application and HTTP routes
"""

__version__ = "5.0.0"
