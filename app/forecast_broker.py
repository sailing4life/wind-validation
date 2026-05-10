from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from .config import Settings
from .domain import ForecastValue
from .forecast_adapters import OpenMeteoForecastAdapter
from .openwrf_adapter import OpenWrfAdapter
from .repositories import InMemoryRepository

_OPENWRF_ID   = "openwrf"
_ALADIN_CZ_ID = "aladin_cz"   # windmap-only GRIB source — no point-forecast adapter


class ForecastBroker:
    def __init__(self, repo: InMemoryRepository, settings: Settings) -> None:
        self.repo = repo
        self.settings = settings
        self.openmeteo = OpenMeteoForecastAdapter(settings)
        self.openwrf = OpenWrfAdapter()

    def refresh_recent_forecasts(self, hours_back: int = 72) -> dict[str, list[ForecastValue]]:
        if not self.settings.live_forecasts_enabled:
            return {}

        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        start = now - timedelta(hours=hours_back)
        end   = now + timedelta(hours=12)
        coords = list({(s.lat, s.lon) for s in self.repo.stations})

        updated: dict[str, list[ForecastValue]] = {}
        om_index = 0
        for model in self.repo.models:
            if model.model_id in (_OPENWRF_ID, _ALADIN_CZ_ID):
                # These are GRIB-based on-demand sources with no Open-Meteo backing.
                # Skip in background refresh; wind maps fetch data on request.
                continue
            if om_index > 0:
                time.sleep(3.0)  # stay within Open-Meteo free-tier rate limit
            rows = self.openmeteo.fetch_model_at_coords(model, coords, start, end)
            om_index += 1
            if rows:
                updated[model.model_id] = rows
        return updated
