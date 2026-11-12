# Installation and Deployment Guide

---

## 1. Requirements

| Component | Version | Notes |
| --- | --- | --- |
| Python | 3.10 – 3.12 | developed and tested on 3.11 |
| Node.js | optional | only for the front-end smoke test (`tests/frontend_smoke.mjs`) |
| Earth Engine account | — | with access to the project holding the basin assets (optional, see §4) |
| Disk | ~200 MB | dependencies + derived geometry cache |

All Python dependencies are listed in `requirements.txt` (runtime) and
`requirements-dev.txt` (tests). Versions are constrained, not floating.

---

## 2. Installation

```bash
git clone https://github.com/tayeteshome854-commits/Transboundary-river-monitoring-dashboard.git
cd Transboundary-river-monitoring-dashboard

python3 -m venv .venv
source .venv/bin/activate                 # Windows: .venv\Scripts\activate

pip install -r requirements.txt
pip install -r requirements-dev.txt       # only needed to run the tests
```

No hidden local configuration is required: the application starts and serves basin geometry,
Green Legacy records, the map and the API documentation even before Earth Engine is configured.

---

## 3. Configuration

Copy the template and edit it:

```bash
cp .env.example .env
```

| Variable | Default | Purpose |
| --- | --- | --- |
| `EE_PROJECT_ID` | `enduring-art-494812-u2` | Earth Engine project |
| `EE_SERVICE_ACCOUNT_KEY` | *(unset)* | path to a service-account JSON key (server deployments) |
| `EE_TIMEOUT_SECONDS` | `90` | per-call timeout |
| `EE_MAX_RETRIES` | `2` | retries for transient failures (429/5xx/timeouts) |
| `MAX_CLOUD_COVER_PERCENT` | `70` | scene-level cloud screen |
| `MIN_OBSERVATIONS` | `1` | minimum observations for a statistic to be published |
| `FRESH_DAYS` / `STALE_DAYS` | `16` / `45` | freshness thresholds |
| `DEFAULT_WINDOW_DAYS` | `90` | default look-back window |
| `BASIN_SIMPLIFY_M` / `BUFFER_SIMPLIFY_M` / `GREEN_SIMPLIFY_M` | `150` / `60` / `25` | GeoJSON simplification tolerances |
| `CACHE_TTL_SECONDS` / `CACHE_MAX_ITEMS` | `3600` / `256` | product cache |
| `ALLOW_DEMO_MODE` | `false` | allow labelled synthetic data when Earth Engine is down |
| `CORS_ORIGINS` | `*` | comma separated list; credentials are enabled only when it is not `*` |
| `HYDROLOGY_PROVIDER` | *(unset)* | name of a registered gauge adapter |
| `APP_ENV` | `development` | `production` disables auto-reload |
| `LOG_LEVEL` / `LOG_FORMAT` | `INFO` / `json` | `LOG_FORMAT=text` for human-readable logs |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | bind address |

**Never commit `.env` or a service-account key.** Both are in `.gitignore`.

---

## 4. Earth Engine authentication

Authenticate **once**, outside the server process — the application deliberately never calls
`ee.Authenticate()`, because it is interactive and would hang a service.

**Option A — user credentials (development):**

```bash
python -c "import ee; ee.Authenticate()"
python -c "import ee; ee.Initialize(project='enduring-art-494812-u2'); print(ee.data.numAssets({'type':'FOLDER'})['count'])"
```

**Option B — service account (server / container):**

1. Create a service account in the Google Cloud project and grant it Earth Engine access.
2. Download its JSON key to a path outside the repository, e.g. `/etc/secrets/ee-key.json`.
3. Set `EE_SERVICE_ACCOUNT_KEY=/etc/secrets/ee-key.json` and `EE_PROJECT_ID=<project>`.

**Basin assets are optional.** Zonal statistics, time series and map layers are computed from the
geometry in `shapefiles/`, so the uploaded Earth Engine assets are no longer required for
satellite products. The asset ids remain in `app/config.py` for provenance and for deployments
that prefer server-side assets; override with `EE_ASSET_ROOT` if they live elsewhere.

---

## 5. Running

```bash
# development (auto-reload)
python main.py

# or explicitly
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Then open:

| URL | Purpose |
| --- | --- |
| `/` | the dashboard |
| `/api/docs` | interactive API documentation |
| `/api/health` | application health |
| `/api/readiness` | Earth Engine / vector-data readiness |

Verify the deployment:

```bash
curl -s localhost:8000/api/health
curl -s localhost:8000/api/basins | head -c 300
curl -s localhost:8000/api/readiness | python -m json.tool | head -20
```

`/api/readiness` returning **503 with `status: "degraded"`** is expected before Earth Engine is
authenticated; the dashboard still serves boundaries, Green Legacy data and the API.

---

## 6. Production

```bash
APP_ENV=production \
CORS_ORIGINS=https://dashboard.example.org \
ALLOW_DEMO_MODE=false \
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

Recommendations:

* put the app behind a TLS-terminating reverse proxy (nginx, Caddy, ALB);
* set `CORS_ORIGINS` to the exact dashboard origin(s) — never `*` in production;
* keep `ALLOW_DEMO_MODE=false` so synthetic data can never be served;
* run with several workers only if you accept per-process caches, or move the cache to Redis;
* ship logs (JSON lines on stdout) to your aggregator; secrets are never logged;
* mount `data/cache` on a writable volume (derived geometry is regenerated automatically if it is
  missing);
* monitor `/api/health` for liveness and `/api/readiness` for data-source availability — they
  answer different questions.

### systemd unit

```ini
[Unit]
Description=Ethiopian Transboundary River Monitoring Dashboard
After=network-online.target

[Service]
WorkingDirectory=/opt/river-dashboard
EnvironmentFile=/etc/river-dashboard.env
ExecStart=/opt/river-dashboard/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
Restart=on-failure
User=riverdash

[Install]
WantedBy=multi-user.target
```

### Docker

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV HOST=0.0.0.0 PORT=8000
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Build and run:

```bash
docker build -t river-dashboard .
docker run --rm -p 8000:8000 --env-file .env river-dashboard
```

---

## 7. Pre-generating the geometry cache (optional)

The first request for each layer derives and caches simplified GeoJSON under `data/cache/`.
To warm it ahead of time:

```bash
python scripts/build_geojson.py
```

This is useful for slow first loads of the Green Legacy layer. The cache is keyed on source file
mtimes, so editing a shapefile invalidates the entry automatically.

---

## 8. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `earthengine-api could not be imported` | dependency missing | `pip install -r requirements.txt` |
| readiness `degraded`, message mentions credentials | not authenticated | §4 |
| readiness `degraded`, message mentions the project | `EE_PROJECT_ID` wrong or no access | check the project id and IAM |
| `422 insufficient_observations` | no cloud-free scene in the window | widen the window or raise `MAX_CLOUD_COVER_PERCENT` |
| Green Legacy layer is slow the first time | 21,429 polygons being simplified | run `scripts/build_geojson.py` |
| Basins look offset | a client is reprojecting already-reprojected data | consume the GeoJSON as EPSG:4326; do not reproject |
