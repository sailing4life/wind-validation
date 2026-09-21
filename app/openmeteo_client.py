"""Shared, bounded HTTP cache and rate-limit protection for Open-Meteo.

One gate serves forecasts, validation and optional weather endpoints in this
process. Deploy one collector; separate processes still have separate budgets.
"""
from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from collections import OrderedDict
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from .config import SETTINGS

logger = logging.getLogger("wind_validation.openmeteo")

def response_fetched_at(response: httpx.Response) -> datetime:
    """Reusing cached data must never advance its recorded availability."""
    value = response.extensions.get("openmeteo_fetched_at")
    return value if isinstance(value, datetime) else datetime.now(UTC)


def _retry_seconds(response: httpx.Response) -> float:
    raw = response.headers.get("Retry-After", "")
    try:
        seconds = float(raw)
        if math.isfinite(seconds):
            return max(1, seconds)
    except ValueError:
        try:
            until = parsedate_to_datetime(raw)
            if until.tzinfo is None:
                until = until.replace(tzinfo=UTC)
            return max(1, (until - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            pass
    # Open-Meteo may return the quota window in its JSON error instead of a header.
    reason = response.text.lower()
    if "daily" in reason or "day" in reason:
        return 86400
    if "hour" in reason:
        return 3600
    if "minute" in reason or "minutely" in reason:
        return 60
    return 300


class OpenMeteoClient:
    def __init__(self, ttl_seconds: float = 3600, min_interval_seconds: float | None = None,
                 max_entries: int = 128, max_bytes: int = 32 * 1024 * 1024) -> None:
        self.ttl_seconds = ttl_seconds
        self.min_interval_seconds = (
            SETTINGS.openmeteo_min_interval_seconds if min_interval_seconds is None else min_interval_seconds
        )
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._cache: OrderedDict[tuple, tuple[float, httpx.Response]] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()
        self._next_request_at = 0.0
        self._blocked_until = 0.0

    def cooldown_remaining(self) -> int:
        return max(0, math.ceil(self._blocked_until - time.monotonic()))

    def _key(self, url: str, params: dict) -> tuple:
        # Relative forecast_hours rolls each hour; day-based requests roll at
        # midnight. A cached response from yesterday must not cover today's query.
        now = datetime.now(UTC)
        period = now.strftime("%Y-%m-%dT%H") if any(
            key in params for key in ("forecast_hours", "past_hours", "current")
        ) else now.date().isoformat()
        return url, tuple(sorted(httpx.QueryParams(params).multi_items())), period

    def _discard(self, key: tuple) -> None:
        _, response = self._cache.pop(key)
        self._bytes -= len(response.content)

    def get(self, client: httpx.Client, url: str, params: dict) -> httpx.Response:
        key = self._key(url, params)
        # Serializing cache misses also merges simultaneous identical requests.
        # Async callers run this in a worker thread, never on the event loop.
        with self._lock:
            now = time.monotonic()
            for expired in [k for k, (until, _) in self._cache.items() if until <= now]:
                self._discard(expired)
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached[1]
            remaining = self.cooldown_remaining()
            if remaining:
                return httpx.Response(429, request=httpx.Request("GET", url, params=params),
                                      headers={"Retry-After": str(remaining)}, json={
                                          "error": True,
                                          "reason": f"Open-Meteo is rate limited; retry in {remaining} seconds.",
                                      })
            delay = self._next_request_at - now
            if delay > 0:
                time.sleep(delay)
            try:
                response = client.get(url, params=params)
            finally:
                self._next_request_at = time.monotonic() + self.min_interval_seconds
            if response.status_code == 429:
                cooldown = _retry_seconds(response)
                self._blocked_until = time.monotonic() + cooldown
                try:
                    payload = response.json()
                except ValueError:
                    payload = {}
                reason = payload.get("reason") if isinstance(payload, dict) else None
                logger.warning("Open-Meteo rate limit: pausing uncached requests for %.0f seconds; reason: %s",
                               cooldown, str(reason or "not supplied")[:300].replace("\n", " ").replace("\r", " "))
                return response  # no per-model retries or sleeps during a quota block
            ttl = self.ttl_seconds if response.is_success else 600 if response.status_code == 400 else 0
            if response.is_success:
                response.extensions["openmeteo_fetched_at"] = datetime.now(UTC)
            # Remember unsupported variable combinations too, avoiding repeated
            # diagnostic fallback requests. Never cache 429 or transient 5xx.
            size = len(response.content)
            if ttl > 0 and size <= self.max_bytes and self.max_entries > 0:
                while self._cache and (len(self._cache) >= self.max_entries or self._bytes + size > self.max_bytes):
                    self._discard(next(iter(self._cache)))
                self._cache[key] = (time.monotonic() + ttl, response)
                self._bytes += size
            return response


openmeteo_client = OpenMeteoClient()


def get_openmeteo(client: httpx.Client, url: str, params: dict) -> httpx.Response:
    return openmeteo_client.get(client, url, params)


async def async_get_openmeteo(url: str, params: dict, timeout: float = 30) -> httpx.Response:
    def fetch() -> httpx.Response:
        with httpx.Client(timeout=timeout) as client:
            return get_openmeteo(client, url, params)
    return await asyncio.to_thread(fetch)
