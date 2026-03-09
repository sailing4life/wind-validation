from __future__ import annotations

from .forecast_broker import ForecastBroker
from .repositories import InMemoryRepository


class IngestionService:
    def __init__(self, repo: InMemoryRepository, forecast_broker: ForecastBroker) -> None:
        self.repo = repo
        self.forecast_broker = forecast_broker
        self.run_count = 0

    def refresh(self) -> None:
        refreshed = self.forecast_broker.refresh_recent_forecasts(hours_back=72)
        for model_id, rows in refreshed.items():
            self.repo.replace_forecasts_for_model(model_id, rows)
        self.run_count += 1
