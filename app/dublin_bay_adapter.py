"""Hourly Irish Lights wind observations through the Dublin Bay Buoy public API.

API/units/attribution: https://dublinbaybuoy.com/developers
Position: the publisher's dashboard BUOY_POS (MMSI 992501301).
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone

import httpx

from .adapters import BaseSourceAdapter
from .cache import TTLCache
from .domain import Observation

logger = logging.getLogger("wind_validation.dublin_bay")
UTC = timezone.utc
KNOT_TO_MS = 1852.0 / 3600.0


class DublinBayBuoyAdapter(BaseSourceAdapter):
    source_name = "dublin_bay_buoy"
    STATION_ID = "DUBLIN_BAY_BUOY"
    PAGE_SIZE = 1000

    def __init__(self, settings):
        super().__init__(settings)
        self._cache: TTLCache[list[dict]] = TTLCache(600)

    def _enabled(self) -> bool:
        return self.settings.live_observations_enabled and self.settings.dublin_bay_buoy_enabled

    def list_stations(self, repo, lat, lon, radius_km):
        return super().list_stations(repo, lat, lon, radius_km) if self._enabled() else []

    @classmethod
    def parse_readings(cls, payload, start: datetime, end: datetime) -> list[Observation]:
        if not isinstance(payload, list):
            return []
        rows = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                ts = datetime.fromisoformat(str(item["timestamp"]).replace("Z", "+00:00"))
                # The API's timestamps are UTC, including any timezone-free ISO values.
                ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)
                speed, direction = float(item["avg_wind"]), float(item["wind_dir"])
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if not start <= ts <= end or not math.isfinite(speed) or not math.isfinite(direction):
                continue
            if speed < 0 or speed * KNOT_TO_MS > 75 or not 0 <= direction <= 360:
                continue
            gust = None
            try:
                value = float(item.get("gust_speed")) * KNOT_TO_MS
                if math.isfinite(value) and speed * KNOT_TO_MS <= value <= 100:
                    gust = value
            except (TypeError, ValueError, OverflowError):
                pass
            rows[ts] = Observation(
                station_id=cls.STATION_ID, source=cls.source_name, time_utc=ts,
                ws_ms=speed * KNOT_TO_MS, wd_deg=direction % 360, gust_ms=gust,
            )
        return sorted(rows.values(), key=lambda row: row.time_utc)

    def get_obs(self, repo, station_ids: set[str], start: datetime, end: datetime) -> list[Observation]:
        if not self._enabled() or self.STATION_ID not in station_ids or end < start:
            return []
        # Shared hourly bounds reuse the same response for adjacent location
        # queries. Always filter back to the exact caller's observation window.
        lower = start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        upper = end.astimezone(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        cache_key = f"{lower.isoformat()}:{upper.isoformat()}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return self.parse_readings(cached, start, end)
        try:
            payload = []
            with httpx.Client(timeout=self.settings.request_timeout_seconds) as client:
                for page in range(20):
                    response = client.get(
                        self.settings.dublin_bay_api_url.rstrip("/") + "/readings",
                        headers={"apikey": self.settings.dublin_bay_api_key},
                        params=[("select", "timestamp,avg_wind,wind_dir,gust_speed"),
                                ("timestamp", "gte." + lower.isoformat()),
                                ("timestamp", "lte." + upper.isoformat()),
                                ("order", "timestamp.asc"), ("limit", self.PAGE_SIZE),
                                ("offset", page * self.PAGE_SIZE)],
                    )
                    response.raise_for_status()
                    batch = response.json()
                    if not isinstance(batch, list):
                        raise ValueError("Unexpected readings response")
                    payload.extend(batch)
                    if len(batch) < self.PAGE_SIZE:
                        self._cache.set(cache_key, payload)
                        return self.parse_readings(payload, start, end)
                raise ValueError("Readings window exceeds pagination limit")
        except Exception as exc:
            logger.warning("Dublin Bay Buoy observations unavailable: %s", exc)
            return []
