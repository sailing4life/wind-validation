from __future__ import annotations

from .forecast_broker import ForecastBroker
from .repositories import InMemoryRepository
from .observation_broker import ObservationBroker
from .storage import PostgresStore
from datetime import datetime, timedelta, timezone


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
        for country in {station.country for station in self.repo.stations}:
            station_ids = {station.station_id for station in self.repo.stations if station.country == country}
            observations, _ = self.observation_broker.get_observations(country, station_ids, now - timedelta(hours=2), now)
            self.store.save_observations(observations)
        self.run_count += 1
