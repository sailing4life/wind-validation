from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .domain import Observation
from .forecast_broker import ForecastBroker
from .observation_broker import ObservationBroker
from .repositories import InMemoryRepository
from .services import build_pair_rows
from .storage import PostgresStore


class IngestionService:
    def __init__(self, repo: InMemoryRepository, forecast_broker: ForecastBroker, observation_broker: ObservationBroker, store: PostgresStore) -> None:
        self.repo = repo
        self.forecast_broker = forecast_broker
        self.observation_broker = observation_broker
        self.store = store
        self.run_count = 0

    def refresh(self) -> None:
        refreshed = self.forecast_broker.refresh_recent_forecasts(hours_back=72)
        for model_id, rows in refreshed.items():
            self.repo.replace_forecasts_for_model(model_id, rows)
            self.store.save_forecasts(rows)
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(hours=2)
        all_observations: list[Observation] = []
        for country in {station.country for station in self.repo.stations}:
            station_ids = {station.station_id for station in self.repo.stations if station.country == country}
            observations, _ = self.observation_broker.get_observations(country, station_ids, window_start, now)
            self.store.save_observations(observations)
            all_observations.extend(observations)
        self._archive_pairs(all_observations, window_start, now)
        self.run_count += 1

    def _archive_pairs(self, observations: list[Observation], window_start: datetime, now: datetime) -> None:
        """Grow the durable verification-pair archive on every refresh, so
        calibration history accumulates without anyone pressing Validate."""
        if not self.store.enabled or not observations:
            return
        stations_by_id = {s.station_id: s for s in self.repo.stations}
        fc_index: dict = defaultdict(list)
        for fv in self.repo.forecasts:
            fc_index[(fv.model_id, fv.valid_time_utc)].append(fv)
        # On-demand GRIB models (ALADIN, OpenWRF) are absent from the background
        # refresh, but runs archived during validations and forecasts can still
        # be paired against fresh observations here.
        on_demand_ids = [m.model_id for m in self.repo.models if m.on_demand]
        if on_demand_ids:
            for station in stations_by_id.values():
                for fv in self.store.load_forecasts(
                    on_demand_ids, window_start - timedelta(hours=1), now + timedelta(hours=1),
                    station.lat, station.lon, radius_km=50.0,
                ):
                    fc_index[(fv.model_id, fv.valid_time_utc)].append(fv)
        pair_rows = build_pair_rows(observations, stations_by_id, fc_index)
        if pair_rows:
            self.store.save_forecast_observation_pairs(pair_rows)
