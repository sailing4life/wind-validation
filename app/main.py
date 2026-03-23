from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .catalog import catalog_as_dict
from .config import SETTINGS
from .forecast_broker import ForecastBroker
from .ingestion import IngestionService
from .location_fingerprint import LocationFingerprintService
from .observation_broker import ObservationBroker
from .repositories import InMemoryRepository
from .schemas import ForecastRequest, ForecastResponse, FreshnessDTO, ValidatePointRequest, ValidatePointResponse
from .services import ValidationService, run_hourly_refresh

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

repo = InMemoryRepository()
forecast_broker = ForecastBroker(repo, SETTINGS)
ingestion_service = IngestionService(repo, forecast_broker)
broker = ObservationBroker(repo, SETTINGS)
fingerprint_service = LocationFingerprintService(SETTINGS)
validation_service = ValidationService(repo, broker, forecast_broker.openmeteo, SETTINGS, fingerprint_service=fingerprint_service)

_stop_event = asyncio.Event()
_refresh_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _refresh_task
    await asyncio.to_thread(ingestion_service.refresh)
    _refresh_task = asyncio.create_task(
        run_hourly_refresh(ingestion_service, SETTINGS.refresh_interval_seconds, _stop_event)
    )
    try:
        yield
    finally:
        _stop_event.set()
        if _refresh_task is not None:
            await _refresh_task


app = FastAPI(title=SETTINGS.app_name, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.post("/v1/validate-point", response_model=ValidatePointResponse)
def validate_point(payload: ValidatePointRequest) -> dict:
    return validation_service.validate_point(
        lat=payload.lat,
        lon=payload.lon,
        hours_back=payload.hours_back,
        radius_km=payload.radius_km,
    )


@app.post("/api/forecast", response_model=ForecastResponse)
def forecast(payload: ForecastRequest) -> dict:
    return validation_service.forecast_point(
        lat=payload.lat,
        lon=payload.lon,
        winner_model_id=payload.winner_model_id,
        bias_ws_ms=payload.bias_ws_ms,
        hours_ahead=payload.hours_ahead,
    )


@app.get("/v1/models/coverage")
def model_coverage() -> list[dict]:
    return catalog_as_dict(repo.models)


@app.get("/v1/stations/nearby")
def stations_nearby(
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
    radius_km: float = Query(50.0, gt=0.0, le=150.0),
) -> dict:
    country = repo.point_country(lat, lon)
    rows = broker.list_stations(country, lat, lon, radius_km)
    return {
        "country": country,
        "stations": [
            {
                "station_id": s.station_id,
                "source": s.source,
                "country": s.country,
                "lat": s.lat,
                "lon": s.lon,
                "elevation_m": s.elevation_m,
            }
            for s in rows
        ],
        "count": len(rows),
    }


@app.get("/v1/health/freshness", response_model=FreshnessDTO)
def freshness() -> dict:
    return {
        "sources": repo.freshness.sources,
        "models": repo.freshness.models,
    }


def _windmap_model_params(model_id: str) -> tuple[str, str]:
    """Map a forecast model ID to (endpoint_url, model_param) for Open-Meteo."""
    model_map = {
        "harmonie_nl":  (SETTINGS.openmeteo_knmi_url,     SETTINGS.openmeteo_knmi_model),
        "arome_hd":     (SETTINGS.openmeteo_arome_hd_url, SETTINGS.openmeteo_arome_hd_model),
        "icon_eu":      (SETTINGS.openmeteo_icon_eu_url,  SETTINGS.openmeteo_icon_eu_model),
        "arpege":       (SETTINGS.openmeteo_arpege_url,   SETTINGS.openmeteo_arpege_model),
        "ecmwf_global": (SETTINGS.openmeteo_ecmwf_url,    SETTINGS.openmeteo_ecmwf_model),
    }
    return model_map.get(model_id, model_map["harmonie_nl"])


@app.get("/api/windmap-gif")
async def windmap_gif(
    lat:   float = Query(..., ge=-90.0,  le=90.0),
    lon:   float = Query(..., ge=-180.0, le=180.0),
    hours: int   = Query(48,  ge=1,      le=120),
    model: str   = Query("harmonie_nl"),
) -> Response:
    """Generate an animated wind-map GIF via Open-Meteo point-forecast grid."""
    from .windmap import generate_wind_gif  # lazy import — only needed when called

    endpoint_url, model_param = _windmap_model_params(model)

    try:
        gif_bytes: bytes = await asyncio.to_thread(
            generate_wind_gif, lat, lon, hours, endpoint_url, model_param,
        )
    except ValueError as exc:
        return Response(content=str(exc), status_code=400, media_type="text/plain")
    except Exception as exc:
        return Response(content=str(exc), status_code=503, media_type="text/plain")

    filename = f"windmap_{lat:.3f}N_{lon:.3f}E_{model}.gif"
    return Response(
        content=gif_bytes,
        media_type="image/gif",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/ocean-current")
async def ocean_current(
    lat:   float = Query(..., ge=-90.0,  le=90.0),
    lon:   float = Query(..., ge=-180.0, le=180.0),
    hours: int   = Query(48,  ge=1,      le=120),
) -> dict:
    """Fetch hourly ocean current (speed in kt + direction) from Open-Meteo marine API."""
    params = {
        "latitude":  lat,
        "longitude": lon,
        "hourly":    "ocean_current_velocity,ocean_current_direction",
        "wind_speed_unit": "kn",    # request knots directly
        "forecast_hours": min(hours, 120),
        "timezone": "UTC",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(SETTINGS.openmeteo_marine_url, params=params)
        resp.raise_for_status()

    data = resp.json()
    hourly = data.get("hourly", {})
    times  = hourly.get("time", [])
    speeds = hourly.get("ocean_current_velocity", [])
    dirs   = hourly.get("ocean_current_direction", [])

    hours_out = []
    for t, s, d in zip(times, speeds, dirs):
        hours_out.append({
            "time_utc":    t if t.endswith("Z") else t + "Z",
            "speed_kt":    round(float(s), 2) if s is not None else None,
            "direction_deg": round(float(d), 1) if d is not None else None,
        })

    return {"hours": hours_out}
