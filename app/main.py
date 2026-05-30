from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

logger = logging.getLogger("wind_validation")

import httpx
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
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

# Only one windmap generation at a time — GRIB download + rendering is memory-heavy
_windmap_sem = asyncio.Semaphore(1)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
# Use /tmp on Linux (always writable); fall back to app/uploads on Windows dev
_tmp_base = Path("/tmp") if Path("/tmp").exists() else BASE_DIR
UPLOAD_DIR = _tmp_base / "wind_validation_uploads"
try:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    UPLOAD_DIR = BASE_DIR / "uploads"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# upload_id -> absolute Path for locally uploaded GRIBs (in-memory, process lifetime)
_uploaded_gribs: dict[str, Path] = {}

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
    if model_id.startswith("upload_"):
        upload_id = model_id[len("upload_"):]
        path = _uploaded_gribs.get(upload_id)
        if path is None:
            raise ValueError(f"Uploaded GRIB '{upload_id}' not found (may have been deleted).")
        return ("", f"local_upload:{path}")
    if model_id == "aladin_cz":
        return ("", "aladin_cz")
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
    lat:       float = Query(..., ge=-90.0,  le=90.0),
    lon:       float = Query(..., ge=-180.0, le=180.0),
    hours:     int   = Query(48,  ge=1,      le=120),
    model:     str   = Query("harmonie_nl"),
    start_iso: str   = Query(""),
    end_iso:   str   = Query(""),
) -> Response:
    """Generate an animated wind-map GIF from native GRIB data, optionally range-filtered."""
    from .windmap import generate_wind_gif  # lazy import — only needed when called

    endpoint_url, model_param = _windmap_model_params(model)

    if _windmap_sem.locked():
        return Response(content="A wind map is already being generated. Try again in a moment.",
                        status_code=503, media_type="text/plain")
    try:
        async with _windmap_sem:
            gif_bytes: bytes = await asyncio.to_thread(
                generate_wind_gif, lat, lon, hours, endpoint_url, model_param, start_iso, end_iso,
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


@app.get("/api/windmap-frames")
async def windmap_frames(
    lat:       float = Query(..., ge=-90.0,  le=90.0),
    lon:       float = Query(..., ge=-180.0, le=180.0),
    hours:     int   = Query(24,  ge=1,      le=48),   # capped at 48 to limit memory
    model:     str   = Query("harmonie_nl"),
    step:      int   = Query(3,   ge=1,      le=24),
    start_iso: str   = Query(""),
    end_iso:   str   = Query(""),
) -> Response:
    """Return JSON list of {label, time_utc, png_b64} wind-map frames for the briefing."""
    import json
    from .windmap import generate_wind_frames  # noqa: PLC0415

    _, model_param = _windmap_model_params(model)

    if _windmap_sem.locked():
        return Response(content="A wind map is already being generated. Try again in a moment.",
                        status_code=503, media_type="text/plain")
    try:
        async with _windmap_sem:
            frames = await asyncio.to_thread(
                generate_wind_frames, lat, lon, hours, "", model_param, step, start_iso, end_iso,
            )
    except ValueError as exc:
        return Response(content=str(exc), status_code=400, media_type="text/plain")
    except Exception as exc:
        return Response(content=str(exc), status_code=503, media_type="text/plain")

    return Response(content=json.dumps({"frames": frames}), media_type="application/json")


_ALLOWED_GRIB_SUFFIXES = {".grib", ".grb", ".grib2", ".grb2"}


@app.post("/api/upload-grib")
async def upload_grib(file: UploadFile = File(...)) -> dict:
    """Accept a GRIB file upload, validate it contains 10 m wind, return an upload_id."""
    suffix = Path(file.filename or "upload.grib").suffix.lower()
    if suffix not in _ALLOWED_GRIB_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"Unsupported file type '{suffix}'. Expected .grib/.grb/.grib2")
    upload_id = uuid.uuid4().hex
    dest = UPLOAD_DIR / f"{upload_id}{suffix}"
    try:
        with dest.open("wb") as fout:
            shutil.copyfileobj(file.file, fout)
    finally:
        file.file.close()
    def _validate():
        from .windmap import _cfgrib_wind  # noqa: PLC0415
        u_da, v_da = _cfgrib_wind(str(dest))
        del u_da, v_da

    try:
        await asyncio.to_thread(_validate)
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Could not read 10 m wind from GRIB: {exc}")
    _uploaded_gribs[upload_id] = dest
    return {"upload_id": upload_id, "filename": file.filename, "model_id": f"upload_{upload_id}"}


@app.post("/api/validate-expedition-log")
async def validate_expedition_log(
    file: UploadFile = File(...),
    interval_min: int = Query(10, ge=2, le=60),
    grib_speed: UploadFile | None = File(default=None),
    grib_dir: UploadFile | None = File(default=None),
    grib_label: str = Query("custom_grib", max_length=40, pattern=r"^[\w\-]+$"),
) -> dict:
    """Parse a sailing expedition .proc.csv and compare wind against NWP models.

    Optionally supply a speed GRIB + direction GRIB pair (raw or bz2-compressed
    ALADIN format) to include an additional custom model in the comparison.
    """
    from .expedition import parse_expedition_csv, validate_expedition, _extract_from_aladin_gribs  # noqa: PLC0415

    fname = (file.filename or "").lower()
    if not (fname.endswith(".csv") or fname.endswith(".log")):
        raise HTTPException(status_code=400, detail="Expected a .csv or .log file")

    data = await file.read()
    speed_bytes = await grib_speed.read() if grib_speed else None
    dir_bytes   = await grib_dir.read()   if grib_dir   else None

    def _run() -> dict:
        samples = parse_expedition_csv(data, interval_min)
        if not samples:
            raise ValueError(
                "No valid wind samples found. "
                "Supported formats: native Expedition log (starts with !Boat,...) "
                "or processed .proc.csv with UtcDate, UtcTime, Lat, Lon, TWS, TWD columns."
            )
        extra: dict | None = None
        if speed_bytes and dir_bytes:
            start = samples[0]["time_utc"]
            end   = samples[-1]["time_utc"]
            fvs = _extract_from_aladin_gribs(speed_bytes, dir_bytes, samples, start, end, grib_label)
            if fvs:
                extra = {grib_label: fvs}
        return validate_expedition(samples, repo.models, forecast_broker.openmeteo, SETTINGS, extra_fvs=extra)

    try:
        result = await asyncio.to_thread(_run)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Expedition log validation failed")
        raise HTTPException(status_code=500, detail=str(exc))

    return result


@app.get("/api/forecast-ensemble")
async def forecast_ensemble(
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
    hours: int = Query(120, ge=6, le=240),
) -> dict:
    """Fetch ICON-EPS ensemble percentiles (p10/p25/p50/p75/p90) for TWS and TWD."""
    import math

    MS_TO_KT = 1.94384

    # Request base variable names — the ensemble API returns all member columns
    # automatically (wind_speed_10m_member00 … wind_speed_10m_memberN).
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "wind_speed_10m,wind_direction_10m",
        "models": "icon_seamless",
        "wind_speed_unit": "ms",
        "forecast_hours": min(hours, 240),
        "timezone": "UTC",
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(SETTINGS.openmeteo_ensemble_url, params=params)
            resp.raise_for_status()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Ensemble API unavailable: {exc}")

    hourly = resp.json().get("hourly", {})
    times = [t if t.endswith("Z") else t + "Z" for t in hourly.get("time", [])]

    # Detect available member columns from the response
    avail_ws = sorted(k for k in hourly if k.startswith("wind_speed_10m_member"))
    avail_wd = sorted(k for k in hourly if k.startswith("wind_direction_10m_member"))

    def _pct(sv: list, p: float) -> float:
        n = len(sv)
        idx = p / 100.0 * (n - 1)
        lo = int(idx)
        hi = min(lo + 1, n - 1)
        return sv[lo] + (idx - lo) * (sv[hi] - sv[lo])

    def _circ_mean(angles: list) -> float:
        s = sum(math.sin(math.radians(a)) for a in angles)
        c = sum(math.cos(math.radians(a)) for a in angles)
        return math.degrees(math.atan2(s, c)) % 360

    # TWS percentiles (kt)
    tws: dict = {"times": times, "p10": [], "p25": [], "p50": [], "p75": [], "p90": []}
    for i in range(len(times)):
        vals = sorted(
            hourly[k][i] * MS_TO_KT
            for k in avail_ws
            if hourly[k] and i < len(hourly[k]) and hourly[k][i] is not None
        )
        if not vals:
            for p in ("p10", "p25", "p50", "p75", "p90"):
                tws[p].append(None)
        else:
            tws["p10"].append(round(_pct(vals, 10), 2))
            tws["p25"].append(round(_pct(vals, 25), 2))
            tws["p50"].append(round(_pct(vals, 50), 2))
            tws["p75"].append(round(_pct(vals, 75), 2))
            tws["p90"].append(round(_pct(vals, 90), 2))

    # TWD: circular mean + signed angular deviation percentiles
    twd: dict = {"times": times, "p50": [], "p10_dev": [], "p25_dev": [], "p75_dev": [], "p90_dev": []}
    for i in range(len(times)):
        vals = [
            hourly[k][i]
            for k in avail_wd
            if hourly[k] and i < len(hourly[k]) and hourly[k][i] is not None
        ]
        if not vals:
            twd["p50"].append(None)
            for d in ("p10_dev", "p25_dev", "p75_dev", "p90_dev"):
                twd[d].append(None)
        else:
            cm = _circ_mean(vals)
            twd["p50"].append(round(cm, 1))
            diffs = sorted(((v - cm + 180) % 360 - 180) for v in vals)
            twd["p10_dev"].append(round(_pct(diffs, 10), 1))
            twd["p25_dev"].append(round(_pct(diffs, 25), 1))
            twd["p75_dev"].append(round(_pct(diffs, 75), 1))
            twd["p90_dev"].append(round(_pct(diffs, 90), 1))

    spreads = [
        tws["p90"][i] - tws["p10"][i]
        for i in range(len(times))
        if tws["p90"][i] is not None and tws["p10"][i] is not None
    ]
    mean_spread = round(sum(spreads) / len(spreads), 1) if spreads else 0.0
    spread_label = "tight" if mean_spread < 4 else "wide" if mean_spread > 10 else "moderate"

    return {
        "model": "icon_seamless",
        "n_members": len(avail_ws),
        "tws": tws,
        "twd": twd,
        "spread_kt": mean_spread,
        "spread_label": spread_label,
    }


@app.delete("/api/upload-grib/{upload_id}")
async def delete_grib(upload_id: str) -> dict:
    """Remove a previously uploaded GRIB file."""
    path = _uploaded_gribs.pop(upload_id, None)
    if path and path.exists():
        path.unlink()
    return {"ok": True}


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
