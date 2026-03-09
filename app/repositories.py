from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .catalog import default_model_catalog
from .domain import ForecastValue, Station
from .geo import detect_country, haversine_km


@dataclass(slots=True)
class FreshnessState:
    sources: dict[str, datetime | None]
    models: dict[str, datetime | None]


class InMemoryRepository:
    def __init__(self) -> None:
        self.models = default_model_catalog()
        self.stations: list[Station] = self._station_catalog()
        self.forecasts: list[ForecastValue] = []
        self.freshness = FreshnessState(sources={}, models={})

    def _station_catalog(self) -> list[Station]:
        return [
            # KNMI stations — daggegevens.knmi.nl hourly obs (CET, ~1-3h delay)
            Station("NL001", "knmi", "NL", 52.10, 5.18, 5, external_id="260"),
            Station("NL002", "knmi", "NL", 52.30, 4.76, 2, external_id="240"),
            # NOAA ISD stations for NL — NCEI global-hourly (24-72h delay)
            Station("NL_ISD001", "isd", "NL", 52.10, 5.18, 4, external_id="062600-99999"),   # De Bilt
            Station("NL_ISD002", "isd", "NL", 52.31, 4.79, -4, external_id="062400-99999"),  # Schiphol
            # Meteo-France stations
            Station("FR001", "meteofrance", "FR", 48.85, 2.35, 35, external_id="071560-99999"),
            Station("FR002", "meteofrance", "FR", 43.60, 1.44, 146, external_id="076300-99999"),
            # NOAA ISD stations for FR — NCEI global-hourly (24-72h delay)
            Station("FR_ISD001", "isd", "FR", 48.72, 2.38, 119, external_id="071490-99999"),  # Paris CDG
            Station("FR_ISD002", "isd", "FR", 43.62, 1.38, 153, external_id="076300-99999"),  # Toulouse
            # Italy + EU ISD stations (24-72h delay)
            Station("IT001", "isd", "IT", 45.46, 9.19, 120, external_id="160800-99999"),
            Station("IT002", "isd", "IT", 41.90, 12.49, 21, external_id="162390-99999"),
            Station("EU001", "isd", "OTHER", 50.11, 8.68, 110, external_id="106370-99999"),
            # METAR and BrightSky stations are discovered dynamically at query time — no catalog entries needed.
        ]

    def nearby_stations(self, lat: float, lon: float, radius_km: float) -> list[Station]:
        candidates = [s for s in self.stations if s.active and haversine_km(lat, lon, s.lat, s.lon) <= radius_km]
        return sorted(candidates, key=lambda s: haversine_km(lat, lon, s.lat, s.lon))

    def forecasts_for_model_window(self, model_id: str, start: datetime, end: datetime) -> list[ForecastValue]:
        return [
            row
            for row in self.forecasts
            if row.model_id == model_id and start <= row.valid_time_utc <= end
        ]

    def nearest_forecast(self, model_id: str, lat: float, lon: float, valid_time: datetime) -> ForecastValue | None:
        same_time = [
            row
            for row in self.forecasts
            if row.model_id == model_id and row.valid_time_utc == valid_time
        ]
        if not same_time:
            return None
        return min(same_time, key=lambda r: haversine_km(lat, lon, r.lat, r.lon))

    def point_country(self, lat: float, lon: float) -> str:
        return detect_country(lat, lon)

    def replace_forecasts_for_model(self, model_id: str, new_rows: list[ForecastValue]) -> None:
        self.forecasts = [row for row in self.forecasts if row.model_id != model_id]
        self.forecasts.extend(new_rows)
        self.freshness.models[model_id] = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

    def clear_live_buffers(self) -> None:
        self.forecasts = []
