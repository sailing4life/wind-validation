from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from .config import Settings
from .domain import ForecastValue, ModelDefinition, Station
from .geo import in_bbox
from .openmeteo_client import get_openmeteo, response_fetched_at
from .scoring import speed_dir_to_uv

logger = logging.getLogger("wind_validation.forecast_adapters")

CORE_HOURLY_VARS = ["wind_speed_10m", "wind_direction_10m"]
EXTRA_BASE_HOURLY_VARS = ["windgusts_10m", "temperature_2m", "precipitation"]
EXTRA_DIAG_HOURLY_VARS = [
    "cloud_cover",
    "pressure_msl",
    "shortwave_radiation",
    "cape",
    "boundary_layer_height",
]
EXTRA_SAFE_HOURLY_VARS = ["cloud_cover", "pressure_msl", "shortwave_radiation"]


def _get_with_retry(client: httpx.Client, url: str, params: dict) -> httpx.Response:
    """Shared cache and quota cooldown; retries belong to later refresh cycles."""
    return get_openmeteo(client, url, params)


def _parse_utc(value) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _parse_optional_float(values: list, index: int) -> float | None:
    if index >= len(values):
        return None
    value = values[index]
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class ForecastFetchRequest:
    stations: list[Station]
    start: datetime
    end: datetime


class OpenMeteoForecastAdapter:
    """Fetches hourly wind forecast values from Open-Meteo for configured models."""

    def __init__(self, settings: Settings) -> None:
        self._future_cache: dict[tuple, tuple[float, datetime, datetime, list[ForecastValue]]] = {}
        self._future_lock = threading.Lock()
        self.settings = settings
        # Regular forecast endpoints (used for near-future data)
        self.endpoint_map = {
            "harmonie_nl":  settings.openmeteo_knmi_url,
            "harmonie_eu":  settings.openmeteo_harmonie_eu_url,
            "arome_hd":     settings.openmeteo_arome_hd_url,
            "icon_it":      settings.openmeteo_icon_it_url,
            "icon_eu":      settings.openmeteo_icon_eu_url,
            "arpege":       settings.openmeteo_arpege_url,
            "ecmwf_global": settings.openmeteo_ecmwf_url,
        }
        self.model_param_map = {
            "harmonie_nl":  settings.openmeteo_knmi_model,
            "harmonie_eu":  settings.openmeteo_harmonie_eu_model,
            "arome_hd":     settings.openmeteo_arome_hd_model,
            "icon_it":      settings.openmeteo_icon_it_model,
            "icon_eu":      settings.openmeteo_icon_eu_model,
            "arpege":       settings.openmeteo_arpege_model,
            "ecmwf_global": settings.openmeteo_ecmwf_model,
        }
        # Previous runs API — single endpoint, returns actual archived forecast runs
        self.previous_runs_url = settings.openmeteo_previous_runs_url
        self.previous_runs_model_map = {
            **self.model_param_map,
            # ECMWF needs an explicit model param on the unified previous-runs endpoint
            "ecmwf_global": settings.openmeteo_ecmwf_previous_runs_model,
        }
        # Models that must always use the regular API (previous runs archive not available)
        self.regular_api_only = {"ecmwf_global"}

    def _endpoint(self, model_id: str) -> str | None:
        return self.endpoint_map.get(model_id)

    def _fetch_batch(
        self,
        url: str,
        model_id: str,
        model_param: str,
        in_cov: list[tuple[float, float]],
        past_days: int,
        forecast_days: int,
        start: datetime,
        end: datetime,
        include_extras: bool = False,
    ) -> list[ForecastValue]:
        """Single HTTP fetch for a list of coordinates.

        include_extras=True asks Open-Meteo for the richer diagnostic fields used by
        the Weather tab. Some models do not expose every diagnostic variable, so we
        retry with a narrower variable set before giving up.
        """
        hourly_var_sets = [CORE_HOURLY_VARS]
        if include_extras:
            hourly_var_sets = [
                CORE_HOURLY_VARS + EXTRA_BASE_HOURLY_VARS + EXTRA_DIAG_HOURLY_VARS,
                CORE_HOURLY_VARS + EXTRA_BASE_HOURLY_VARS + EXTRA_SAFE_HOURLY_VARS,
                CORE_HOURLY_VARS + EXTRA_BASE_HOURLY_VARS,
                CORE_HOURLY_VARS,  # last resort — model has no gust/temp support
            ]

        payload = None
        with httpx.Client(timeout=self.settings.request_timeout_seconds) as client:
            for hourly_vars in hourly_var_sets:
                params: dict = {
                    "latitude": ",".join(str(lat) for lat, _ in in_cov),
                    "longitude": ",".join(str(lon) for _, lon in in_cov),
                    "hourly": ",".join(hourly_vars),
                    "wind_speed_unit": "ms",
                    "timezone": "UTC",
                }
                if past_days > 0:
                    params["past_days"] = past_days
                if forecast_days > 0:
                    params["forecast_days"] = forecast_days
                if model_param:
                    params["models"] = model_param

                try:
                    resp = _get_with_retry(client, url, params)
                    resp.raise_for_status()
                    payload = resp.json()
                    break
                except httpx.HTTPStatusError as exc:
                    can_fallback = include_extras and exc.response.status_code == 400 and hourly_vars is not hourly_var_sets[-1]
                    if can_fallback:
                        logger.info(
                            "Retrying %s forecast fetch with fewer diagnostics after HTTP 400",
                            model_id,
                        )
                        continue
                    logger.warning("Batch fetch failed from %s: %s", url, exc)
                    return []
                except Exception as exc:
                    logger.warning("Batch fetch failed from %s: %s", url, exc)
                    return []

        if payload is None:
            return []
        if not isinstance(payload, list):
            payload = [payload]

        now_utc = response_fetched_at(resp)
        rows: list[ForecastValue] = []
        for i, (lat, lon) in enumerate(in_cov):
            if i >= len(payload):
                break
            # Only explicit source metadata is an initialization time. The endpoint
            # name alone does not prove a particular run or historical availability.
            api_run_time: datetime | None = None
            run_time_raw = payload[i].get("run_time")
            if run_time_raw:
                try:
                    api_run_time = _parse_utc(run_time_raw)
                except (TypeError, ValueError):
                    pass

            hourly = payload[i].get("hourly", {})
            gusts = hourly.get("windgusts_10m", []) if include_extras else []
            temps = hourly.get("temperature_2m", []) if include_extras else []
            precips = hourly.get("precipitation", []) if include_extras else []
            clouds = hourly.get("cloud_cover", []) if include_extras else []
            pressures = hourly.get("pressure_msl", []) if include_extras else []
            radiations = hourly.get("shortwave_radiation", []) if include_extras else []
            capes = hourly.get("cape", []) if include_extras else []
            blh_values = hourly.get("boundary_layer_height", []) if include_extras else []
            for j, (t_raw, ws, wd) in enumerate(zip(
                hourly.get("time", []),
                hourly.get("wind_speed_10m", []),
                hourly.get("wind_direction_10m", []),
            )):
                try:
                    valid_time = _parse_utc(t_raw)
                    ws_ms, wd_deg = float(ws), float(wd)
                except (TypeError, ValueError):
                    continue
                if valid_time < start or valid_time > end:
                    continue
                u10, v10 = speed_dir_to_uv(ws_ms, wd_deg)
                gust_ms: float | None = None
                temp_c: float | None = None
                precip_mm: float | None = None
                cloud_cover_pct: float | None = None
                pressure_msl_hpa: float | None = None
                shortwave_wm2: float | None = None
                cape_jkg: float | None = None
                boundary_layer_height_m: float | None = None
                if include_extras:
                    gust_ms = _parse_optional_float(gusts, j)
                    temp_c = _parse_optional_float(temps, j)
                    precip_mm = _parse_optional_float(precips, j)
                    cloud_cover_pct = _parse_optional_float(clouds, j)
                    pressure_msl_hpa = _parse_optional_float(pressures, j)
                    shortwave_wm2 = _parse_optional_float(radiations, j)
                    cape_jkg = _parse_optional_float(capes, j)
                    boundary_layer_height_m = _parse_optional_float(blh_values, j)
                # Preserve actual collection time when the source omits initialization metadata.
                if api_run_time is not None:
                    run_time = api_run_time
                else:
                    run_time = now_utc
                rows.append(ForecastValue(
                    model_id=model_id,
                    run_time_utc=run_time,
                    valid_time_utc=valid_time,
                    lat=lat, lon=lon, u10=u10, v10=v10,
                    gust_ms=gust_ms,
                    temp_c=temp_c,
                    precip_mm=precip_mm,
                    cloud_cover_pct=cloud_cover_pct,
                    pressure_msl_hpa=pressure_msl_hpa,
                    shortwave_wm2=shortwave_wm2,
                    cape_jkg=cape_jkg,
                    boundary_layer_height_m=boundary_layer_height_m,
                    run_time_source="source" if api_run_time is not None else "fetched_snapshot",
                    fetched_at_utc=now_utc,
                ))
        return rows

    def fetch_model_at_coords(
        self,
        model: ModelDefinition,
        coords: list[tuple[float, float]],
        start: datetime,
        end: datetime,
    ) -> list[ForecastValue]:
        """Fetch forecasts using previous-runs API for past data, regular API for future."""
        in_cov = sorted({(lat, lon) for lat, lon in coords if in_bbox(lat, lon, model.coverage_bbox)})
        if not in_cov:
            return []

        now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        rows: list[ForecastValue] = []

        # Past portion
        if start < now:
            past_days = max(1, math.ceil((now - start).total_seconds() / 86400))
            endpoint = self._endpoint(model.model_id)
            if model.model_id in self.regular_api_only:
                # Use regular API directly (previous runs archive not available for this model)
                if endpoint:
                    fc_param = self.model_param_map.get(model.model_id, "")
                    rows.extend(self._fetch_batch(
                        endpoint, model.model_id, fc_param, in_cov,
                        past_days=past_days, forecast_days=1,
                        start=start, end=min(end, now),
                    ))
            else:
                prev_param = self.previous_runs_model_map.get(model.model_id, "")
                past_rows = self._fetch_batch(
                    self.previous_runs_url, model.model_id, prev_param, in_cov,
                    past_days=1, forecast_days=1,
                    start=start, end=min(end, now),
                )
                # Fall back to regular API if previous runs returned nothing
                if not past_rows and endpoint:
                    fc_param = self.model_param_map.get(model.model_id, "")
                    past_rows = self._fetch_batch(
                        endpoint, model.model_id, fc_param, in_cov,
                        past_days=past_days, forecast_days=1,
                        start=start, end=min(end, now),
                    )
                rows.extend(past_rows)

        # Future portion — use regular forecast API
        if end > now:
            endpoint = self._endpoint(model.model_id)
            if endpoint:
                model_param = self.model_param_map.get(model.model_id, "")
                rows.extend(self._fetch_batch(
                    endpoint, model.model_id, model_param, in_cov,
                    past_days=0, forecast_days=1,
                    start=now, end=end,
                ))

        return rows

    def fetch_forecast_with_extras(
        self,
        model: ModelDefinition,
        coords: list[tuple[float, float]],
        start: datetime,
        end: datetime,
    ) -> list[ForecastValue]:
        """Future-only fetch with gust and temperature included (for Forecast tab)."""
        with self._future_lock:
            return self._fetch_future(model, coords, start, end)

    def _fetch_future(self, model, coords, start, end) -> list[ForecastValue]:
        in_cov = sorted({(lat, lon) for lat, lon in coords if in_bbox(lat, lon, model.coverage_bbox)})
        if not in_cov:
            return []
        endpoint = self._endpoint(model.model_id)
        if not endpoint:
            return []
        now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        day_start = now.replace(hour=0)
        forecast_days = max(1, (end.date() - day_start.date()).days + 1)
        fetched_end = day_start + timedelta(days=forecast_days) - timedelta(hours=1)
        model_param = self.model_param_map.get(model.model_id, "")
        # Share hourly future collections between monitored locations and the
        # point forecast builder. Coordinates shared by nearby locations reuse data.
        cached_rows: list[ForecastValue] = []
        missing = []
        for lat, lon in in_cov:
            cached = self._future_cache.get((model.model_id, lat, lon))
            if cached and time.monotonic() - cached[0] < 3600 and cached[1] <= start and cached[2] >= end:
                cached_rows.extend(r for r in cached[3] if start <= r.valid_time_utc <= end)
            else:
                missing.append((lat, lon))
        if not missing:
            return cached_rows
        fetched = self._fetch_batch(
            endpoint, model.model_id, model_param, missing,
            past_days=0, forecast_days=forecast_days,
            start=day_start, end=fetched_end,
            include_extras=True,
        )
        collected = time.monotonic()
        # Bound the cache to live coordinates rather than accumulating old pins.
        self._future_cache = {k: v for k, v in self._future_cache.items() if collected - v[0] < 3600}
        for lat, lon in missing:
            point_rows = [r for r in fetched if r.lat == lat and r.lon == lon]
            if point_rows:
                self._future_cache[(model.model_id, lat, lon)] = (collected, day_start, fetched_end, point_rows)
        return cached_rows + [r for r in fetched if start <= r.valid_time_utc <= end]

    def fetch_model(self, model: ModelDefinition, request: ForecastFetchRequest) -> list[ForecastValue]:
        stations = [s for s in request.stations if in_bbox(s.lat, s.lon, model.coverage_bbox)]
        if not stations:
            return []

        now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        rows: list[ForecastValue] = []
        past_days = max(1, math.ceil((now - request.start).total_seconds() / 86400))
        prev_model_param = self.previous_runs_model_map.get(model.model_id, "")

        with httpx.Client(timeout=self.settings.request_timeout_seconds) as client:
            for station in stations:
                params: dict = {
                    "latitude": station.lat,
                    "longitude": station.lon,
                    "hourly": "wind_speed_10m,wind_direction_10m",
                    "wind_speed_unit": "ms",
                    "past_days": past_days,
                    "forecast_days": 1,
                    "timezone": "UTC",
                }
                if prev_model_param:
                    params["models"] = prev_model_param
                try:
                    resp = _get_with_retry(client, self.previous_runs_url, params)
                    resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    logger.warning(
                        "Forecast fetch failed for %s station %s: HTTP %s — %s",
                        model.model_id, station.station_id,
                        exc.response.status_code, exc.response.text[:200],
                    )
                    continue
                except Exception as exc:
                    logger.warning("Forecast fetch failed for %s station %s", model.model_id, station.station_id, exc_info=exc)
                    continue
                fetched_at = response_fetched_at(resp)
                data = resp.json()
                api_run_time: datetime | None = None
                run_time_raw = data.get("run_time")
                if run_time_raw:
                    try:
                        api_run_time = _parse_utc(run_time_raw)
                    except (TypeError, ValueError):
                        pass
                payload = data.get("hourly", {})
                times = payload.get("time", [])
                ws_values = payload.get("wind_speed_10m", [])
                wd_values = payload.get("wind_direction_10m", [])
                for t_raw, ws, wd in zip(times, ws_values, wd_values):
                    try:
                        valid_time = _parse_utc(t_raw)
                        ws_ms = float(ws)
                        wd_deg = float(wd)
                    except (TypeError, ValueError):
                        continue
                    if valid_time < request.start or valid_time > request.end:
                        continue
                    u10, v10 = speed_dir_to_uv(ws_ms, wd_deg)
                    run_time = api_run_time if api_run_time is not None else fetched_at
                    rows.append(
                        ForecastValue(
                            model_id=model.model_id,
                            run_time_utc=run_time,
                            valid_time_utc=valid_time,
                            lat=station.lat,
                            lon=station.lon,
                            u10=u10,
                            v10=v10,
                            run_time_source="source" if api_run_time is not None else "fetched_snapshot",
                            fetched_at_utc=fetched_at,
                        )
                    )

        return rows
