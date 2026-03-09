from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from .config import Settings
from .domain import ForecastValue
from .forecast_adapters import OpenMeteoForecastAdapter
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
        end   = now + timedelta(hours=12)
        coords = list({(s.lat, s.lon) for s in self.repo.stations})

        updated: dict[str, list[ForecastValue]] = {}
        for i, model in enumerate(self.repo.models):
            if i > 0:
                time.sleep(1.0)  # stay within Open-Meteo free-tier rate limit
            rows = self.openmeteo.fetch_model_at_coords(model, coords, start, end)
            if rows:
                updated[model.model_id] = rows
        return updated
