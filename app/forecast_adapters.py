from __future__ import annotations

import logging
import math
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
        self.endpoint_map = {
            "harmonie_nl":  settings.openmeteo_knmi_url,
            "arome_fr":     settings.openmeteo_meteofrance_url,
            "icon_it":      settings.openmeteo_dwd_url,
            "icon_eu":      settings.openmeteo_icon_eu_url,
            "arpege":       settings.openmeteo_arpege_url,
            "ecmwf_global": settings.openmeteo_ecmwf_url,
        }
        self.model_param_map = {
            "harmonie_nl":  settings.openmeteo_knmi_model,
            "arome_fr":     settings.openmeteo_meteofrance_model,
            "icon_it":      settings.openmeteo_dwd_model,
            "icon_eu":      settings.openmeteo_icon_eu_model,
            "arpege":       settings.openmeteo_arpege_model,
            "ecmwf_global": settings.openmeteo_ecmwf_model,
        }

    def _endpoint(self, model_id: str) -> str | None:
        return self.endpoint_map.get(model_id)

    def fetch_model_at_coords(
        self,
        model: ModelDefinition,
        coords: list[tuple[float, float]],
        start: datetime,
        end: datetime,
    ) -> list[ForecastValue]:
        """Fetch forecasts for multiple (lat, lon) pairs in a single batched request."""
        endpoint = self._endpoint(model.model_id)
        if not endpoint:
            return []

        in_cov = [(lat, lon) for lat, lon in coords if in_bbox(lat, lon, model.coverage_bbox)]
        if not in_cov:
            return []

        past_days   = max(1, math.ceil((end - start).total_seconds() / 86400))
        model_param = self.model_param_map.get(model.model_id, "")

        params: dict = {
            "latitude":        ",".join(str(lat) for lat, _ in in_cov),
            "longitude":       ",".join(str(lon) for _, lon in in_cov),
            "hourly":          "wind_speed_10m,wind_direction_10m",
            "wind_speed_unit": "ms",
            "past_days":       past_days,
            "forecast_days":   1,
            "timezone":        "UTC",
        }
        if model_param:
            params["models"] = model_param

        try:
            with httpx.Client(timeout=self.settings.request_timeout_seconds) as client:
                resp = client.get(endpoint, params=params)
                resp.raise_for_status()
        except Exception as exc:
            logger.warning("On-demand batch fetch failed for %s", model.model_id, exc_info=exc)
            return []

        payload = resp.json()
        if not isinstance(payload, list):
            payload = [payload]

        rows: list[ForecastValue] = []
        for i, (lat, lon) in enumerate(in_cov):
            if i >= len(payload):
                break
            hourly = payload[i].get("hourly", {})
            for t_raw, ws, wd in zip(
                hourly.get("time", []),
                hourly.get("wind_speed_10m", []),
                hourly.get("wind_direction_10m", []),
            ):
                try:
                    valid_time = datetime.fromisoformat(str(t_raw)).replace(tzinfo=UTC)
                    ws_ms, wd_deg = float(ws), float(wd)
                except (TypeError, ValueError):
                    continue
                if valid_time < start or valid_time > end:
                    continue
                u10, v10 = speed_dir_to_uv(ws_ms, wd_deg)
                rows.append(ForecastValue(
                    model_id=model.model_id,
                    run_time_utc=valid_time - timedelta(hours=6),
                    valid_time_utc=valid_time,
                    lat=lat, lon=lon, u10=u10, v10=v10,
                ))
        return rows

    def fetch_model(self, model: ModelDefinition, request: ForecastFetchRequest) -> list[ForecastValue]:
        endpoint = self._endpoint(model.model_id)
        if not endpoint:
            return []

        rows: list[ForecastValue] = []
        past_days = max(1, math.ceil((request.end - request.start).total_seconds() / 86400))
        stations = [s for s in request.stations if in_bbox(s.lat, s.lon, model.coverage_bbox)]
        if not stations:
            return []

        model_param = self.model_param_map.get(model.model_id, "")
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
                if model_param:
                    params["models"] = model_param
                try:
                    resp = client.get(endpoint, params=params)
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
