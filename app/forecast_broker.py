from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .config import Settings
from .domain import ForecastValue
from .forecast_adapters import ForecastFetchRequest, OpenMeteoForecastAdapter
from .repositories import InMemoryRepository


class ForecastBroker:
    def __init__(self, repo: InMemoryRepository, settings: Settings) -> None:
        self.repo = repo
        self.settings = settings
        self.openmeteo = OpenMeteoForecastAdapter(settings)

    def refresh_recent_forecasts(self, hours_back: int = 72) -> dict[str, list[ForecastValue]]:
        if not self.settings.live_forecasts_enabled:
            return {}

        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        start = now - timedelta(hours=hours_back)
        end   = now + timedelta(hours=12)   # include 12h of future forecasts
        request = ForecastFetchRequest(stations=self.repo.stations, start=start, end=end)

        updated: dict[str, list[ForecastValue]] = {}
        for model in self.repo.models:
            rows = self.openmeteo.fetch_model(model, request)
            if rows:
                updated[model.model_id] = rows
        return updated
