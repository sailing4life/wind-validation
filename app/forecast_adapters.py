from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from .config import Settings
from .domain import ForecastValue, ModelDefinition, Station
from .geo import in_bbox
from .scoring import speed_dir_to_uv

logger = logging.getLogger("wind_validation.forecast_adapters")


@dataclass(slots=True)
class ForecastFetchRequest:
    stations: list[Station]
    start: datetime
    end: datetime


class OpenMeteoForecastAdapter:
    """Fetches hourly wind forecast values from Open-Meteo for configured models."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # Regular forecast endpoints (used for near-future data)
        self.endpoint_map = {
            "harmonie_nl":  settings.openmeteo_knmi_url,
            "arome_hd":     settings.openmeteo_arome_hd_url,
            "icon_it":      settings.openmeteo_dwd_url,
            "icon_eu":      settings.openmeteo_icon_eu_url,
            "arpege":       settings.openmeteo_arpege_url,
            "ecmwf_global": settings.openmeteo_ecmwf_url,
        }
        self.model_param_map = {
            "harmonie_nl":  settings.openmeteo_knmi_model,
            "arome_hd":     settings.openmeteo_arome_hd_model,
            "icon_it":      settings.openmeteo_dwd_model,
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
        Set include_extras=True to also fetch windgusts_10m, temperature_2m, precipitation."""
        hourly_vars = "wind_speed_10m,wind_direction_10m"
        if include_extras:
            hourly_vars += ",windgusts_10m,temperature_2m,precipitation"
        params: dict = {
            "latitude":        ",".join(str(lat) for lat, _ in in_cov),
            "longitude":       ",".join(str(lon) for _, lon in in_cov),
            "hourly":          hourly_vars,
            "wind_speed_unit": "ms",
            "timezone":        "UTC",
        }
        if past_days > 0:
            params["past_days"] = past_days
        if forecast_days > 0:
            params["forecast_days"] = forecast_days
        if model_param:
            params["models"] = model_param

        try:
            with httpx.Client(timeout=self.settings.request_timeout_seconds) as client:
                resp = client.get(url, params=params)
                if resp.status_code == 429:
                    logger.debug("429 from %s, retrying after 5s", url)
                    time.sleep(5)
                    resp = client.get(url, params=params)
                resp.raise_for_status()
        except Exception as exc:
            logger.warning("Batch fetch failed from %s: %s", url, exc)
            return []

        payload = resp.json()
        if not isinstance(payload, list):
            payload = [payload]

        now_utc = datetime.now(UTC)
        rows: list[ForecastValue] = []
        for i, (lat, lon) in enumerate(in_cov):
            if i >= len(payload):
                break
            hourly = payload[i].get("hourly", {})
            gusts   = hourly.get("windgusts_10m", []) if include_extras else []
            temps   = hourly.get("temperature_2m", []) if include_extras else []
            precips = hourly.get("precipitation",  []) if include_extras else []
            for j, (t_raw, ws, wd) in enumerate(zip(
                hourly.get("time", []),
                hourly.get("wind_speed_10m", []),
                hourly.get("wind_direction_10m", []),
            )):
                try:
                    valid_time = datetime.fromisoformat(str(t_raw)).replace(tzinfo=UTC)
                    ws_ms, wd_deg = float(ws), float(wd)
                except (TypeError, ValueError):
                    continue
                if valid_time < start or valid_time > end:
                    continue
                u10, v10 = speed_dir_to_uv(ws_ms, wd_deg)
                gust_ms: float | None = None
                temp_c: float | None = None
                precip_mm: float | None = None
                if include_extras:
                    try:
                        gust_ms = float(gusts[j]) if j < len(gusts) and gusts[j] is not None else None
                    except (TypeError, ValueError):
                        pass
                    try:
                        temp_c = float(temps[j]) if j < len(temps) and temps[j] is not None else None
                    except (TypeError, ValueError):
                        pass
                    try:
                        precip_mm = float(precips[j]) if j < len(precips) and precips[j] is not None else None
                    except (TypeError, ValueError):
                        pass
                # Estimate run_time as valid_time−6h, capped to never exceed now.
                # This gives ≈now for future valid_times (regular forecast API)
                # and a plausible past value for historical data.
                run_time = min(valid_time - timedelta(hours=6), now_utc)
                rows.append(ForecastValue(
                    model_id=model_id,
                    run_time_utc=run_time,
                    valid_time_utc=valid_time,
                    lat=lat, lon=lon, u10=u10, v10=v10,
                    gust_ms=gust_ms, temp_c=temp_c, precip_mm=precip_mm,
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
        in_cov = [(lat, lon) for lat, lon in coords if in_bbox(lat, lon, model.coverage_bbox)]
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
                    past_days=past_days, forecast_days=1,
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
                if rows:  # already made a past call; pause before the future call
                    time.sleep(0.5)
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
        in_cov = [(lat, lon) for lat, lon in coords if in_bbox(lat, lon, model.coverage_bbox)]
        if not in_cov:
            return []
        endpoint = self._endpoint(model.model_id)
        if not endpoint:
            return []
        now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        forecast_days = max(1, math.ceil((end - now).total_seconds() / 86400) + 1)
        model_param = self.model_param_map.get(model.model_id, "")
        return self._fetch_batch(
            endpoint, model.model_id, model_param, in_cov,
            past_days=0, forecast_days=forecast_days,
            start=start, end=end,
            include_extras=True,
        )

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
                    resp = client.get(self.previous_runs_url, params=params)
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
                payload = resp.json().get("hourly", {})
                times = payload.get("time", [])
                ws_values = payload.get("wind_speed_10m", [])
                wd_values = payload.get("wind_direction_10m", [])
                for t_raw, ws, wd in zip(times, ws_values, wd_values):
                    try:
                        valid_time = datetime.fromisoformat(str(t_raw)).replace(tzinfo=UTC)
                        ws_ms = float(ws)
                        wd_deg = float(wd)
                    except (TypeError, ValueError):
                        continue
                    if valid_time < request.start or valid_time > request.end:
                        continue
                    u10, v10 = speed_dir_to_uv(ws_ms, wd_deg)
                    rows.append(
                        ForecastValue(
                            model_id=model.model_id,
                            run_time_utc=valid_time - timedelta(hours=6),
                            valid_time_utc=valid_time,
                            lat=station.lat,
                            lon=station.lon,
                            u10=u10,
                            v10=v10,
                        )
                    )

        return rows
