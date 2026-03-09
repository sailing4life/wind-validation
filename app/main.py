from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .catalog import catalog_as_dict
from .config import SETTINGS
from .forecast_broker import ForecastBroker
from .ingestion import IngestionService
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
validation_service = ValidationService(repo, broker, forecast_broker.openmeteo, SETTINGS)

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
    return FileResponse(STATIC_DIR / "index.html")


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
