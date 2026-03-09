# Wind Validation App

Point-based wind model validation app for NL/FR/IT.

## Run

```bash
python -m venv .venv
.venv\\Scripts\\activate
pip install -r wind_validation/requirements.txt
uvicorn wind_validation.app.main:app --reload
```

Open `http://localhost:8000`.

Before running, copy `wind_validation/.env.example` to `wind_validation/.env` and fill your keys.
The app auto-loads `wind_validation/.env` on startup.

## API

- `POST /v1/validate-point`
- `GET /v1/models/coverage`
- `GET /v1/stations/nearby`
- `GET /v1/health/freshness`

## Live Sources

This app is live-source only by default:

```powershell
$env:LIVE_OBSERVATIONS_ENABLED="true"
$env:LIVE_FORECASTS_ENABLED="true"
```

Source settings:

- `METEOFRANCE_OBSERVATION_URL`: URL returning station observations for params `station`, `start`, `end`
- `METEOFRANCE_API_KEY`: bearer token for Meteo-France endpoint (if required)
- `NCEI_TOKEN`: NOAA/NCEI token (optional for some endpoints)
- `REQUEST_TIMEOUT_SECONDS`: HTTP timeout, default `8`
- `REFRESH_INTERVAL_SECONDS`: scheduler interval in seconds, default `600` (10 minutes)
- `OPENMETEO_KNMI_URL`: defaults to `https://api.open-meteo.com/v1/forecast`
- `OPENMETEO_KNMI_MODEL`: Open-Meteo model name for harmonie_nl, default `harmonie_seamless`
- `OPENMETEO_METEOFRANCE_URL`: defaults to `https://api.open-meteo.com/v1/forecast`
- `OPENMETEO_METEOFRANCE_MODEL`: Open-Meteo model name for arome_fr, default `meteofrance_seamless`
- `OPENMETEO_DWD_URL`: defaults to `https://api.open-meteo.com/v1/forecast`
- `OPENMETEO_DWD_MODEL`: Open-Meteo model name for icon_it, default `icon_seamless`
- `OPENMETEO_ECMWF_URL`: defaults to `https://api.open-meteo.com/v1/forecast`
- `OPENMETEO_ECMWF_MODEL`: Open-Meteo model name for ecmwf_global, default `ecmwf_ifs04`

KNMI observations use `daggegevens.knmi.nl` (free, no API key needed).

If a live source call fails, that source/model returns no rows for that refresh cycle.

## Notes

- V1 is wind-only (10m speed + direction).
- Primary ranking metric: vector RMSE on U/V.
- No synthetic seeded data is used for observations or forecasts.
