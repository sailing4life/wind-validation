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
Regional live-observation adapters are enabled automatically for their coverage:
SMHI + Swedish Maritime Administration ViVa (Sweden), Rijkswaterstaat (NL),
NOAA/NDBC (US waters), and IMGW (Poland). `RWS_OBSERVATION_URL` is optional:
set it to a supported Waterinfo GeoJSON export/proxy with `station_id`,
`time_utc`, `ws_ms`, and `wd_deg` properties. This avoids treating a changing
browser-only Waterinfo interface as a production API.
For the Bay of Palma, the SOCIB buoy is included as a fixed 10-minute wind-observation source. Set `SOCIB_BUOY_ENABLED=false` to disable it, `SOCIB_PALMA_OPENDAP_URL` to override its public OPeNDAP endpoint, or `SOCIB_REQUEST_TIMEOUT_SECONDS` (default: 30) for a slower SOCIB response.

Dublin Bay Buoy (`DUBLIN_BAY_BUOY`, Irish Lights MMSI 992501301) is included at
53.33° N, 6.10° W, using the publisher's dashboard position. Its hourly mean wind,
wind-from direction and gusts are fetched from the
[public Developer API](https://dublinbaybuoy.com/developers). Knots are converted
to m/s internally; source UTC timestamps are preserved. The station is treated
as a buoy for calibration relevance and is selected only within the requested
radius. Following a saved location near Dublin includes it in background
collection. `DUBLIN_BAY_BUOY_ENABLED=false` disables it;
`DUBLIN_BAY_API_URL` and `DUBLIN_BAY_API_KEY` can override the public endpoint/key.
No signup is required. Responses are cached for ten minutes, with bounded,
paginated time-window queries. Source outages return no new readings; old readings
are never relabelled as current. Observed gusts are also archived and shown in Now.

Data from Dublin Bay Buoy (dublinbaybuoy.com), sourced from Irish Lights MetOcean
and Open-Meteo. Irish Lights observations are published under CC BY 4.0.
This integration uses buoy wind observations; the provider's forecast-skill
statistics and marine forecasts are not substituted for this app's validation.

If a live source call fails, that source/model returns no rows for that refresh cycle.

## Notes

- V1 is wind-only (10m speed + direction).
- Primary ranking metric: vector RMSE on U/V.
- No synthetic seeded data is used for observations or forecasts.

## Followed locations and the Forecast workspace

Forecast is the default workspace. Save a location, enable **Automatically
follow**, and save its monitoring settings. Optional start/end dates are inclusive
UTC dates. The last selected location, or the first followed location, opens on
the next visit. Free map points still support forecasts on demand.

The collector discovers stations around each active location, archives forecasts
at the location and station coordinates, and prepares a 48-hour forecast plus
validation evidence. Page loads read that prepared result from Postgres; they do
not wait for weather sources. An incomplete or failed refresh retains the last
complete result and reports the failure. The browser checks for saved updates
every minute while visible. Open-Meteo future point requests share an hourly
cache; observation/derived-result cycles default to ten minutes, plus processing
time. This is polling of fetched forecasts, not detection of every source run.

Use one continuously running collector with the same `DATABASE_URL` as the web
service:

```bash
python -m app.worker
```

Set `BACKGROUND_REFRESH_ENABLED=false` on the web service when using this
standalone worker. Alternatively a single always-running web process can collect
with `BACKGROUND_REFRESH_ENABLED=true` (default). Do not run multiple collectors.
A sleeping/stopped web service cannot collect; an open browser is never required.
The worker requires an available Postgres database and retries failed cycles.

Schema additions are applied by `PostgresStore.initialize()` at startup. Existing
locations remain unfollowed until enabled. Moving a saved location invalidates
its prepared result. The API additions are:

- `PATCH /api/locations/{id}/monitoring`: `monitoring_enabled`, optional
  `monitoring_start` and `monitoring_end` (`YYYY-MM-DD` or null).
- `GET /api/locations/{id}/snapshot`: location, status, computation time,
  validation, forecast and previous-forecast comparison. This endpoint only reads.
- `POST /api/forecast`: now also accepts `radius_km`; prior validation and
  `winner_model_id` are optional.

## Bias windows and forecast provenance

Live wind correction first uses the last **3 hours**. Insufficient evidence
falls back to 6, 24 or 48 hours, historical calibration, or raw forecasts. Live
correction requires multiple observation times and a latest paired observation
no more than two hours old. The response reports the window/source actually used.
Model weights are independently computed over **48 hours**, regardless of the
Analysis history selector. Historical local-hour/regime/lead-matched errors still
support uncertainty. Bias drift compares two adjacent three-hour windows on the
same stations; it is a diagnostic and does not automatically alter correction.

Forecast initialization time and first fetch time are recorded separately.
Open-Meteo responses without explicit run metadata are labelled fetched snapshots
and can only verify observations after their recorded availability. The UI's
comparison matches raw model values at the same future times; it is labelled a
comparison of saved forecasts, not necessarily distinct model initializations.
Only changed forecasts update that comparison.

Existing archived rows without recorded provenance are retained and marked
`legacy_unknown`, and no longer count as proven forecast skill. Valid evidence
accumulates from subsequent collection (or forecasts with trusted source run
metadata); a newly followed location can initially show raw forecasts. The
collector stores point values rather than large GRIB files. Archive retention
is not yet automatic; monitor database growth for long-running deployments.
