import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from threading import Event
from unittest.mock import Mock

import httpx
import pytest

import app.openmeteo_client as om
from app.catalog import default_model_catalog
from app.config import Settings
from app.forecast_adapters import OpenMeteoForecastAdapter

URL = "https://api.open-meteo.com/v1/forecast"


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(om.time, "monotonic", lambda: now[0])
    return now


def test_identical_concurrent_requests_make_one_http_call():
    gate = om.OpenMeteoClient(min_interval_seconds=0)
    entered, release = Event(), Event()

    def fetch(request):
        entered.set()
        assert release.wait(5)
        return httpx.Response(200, json={"hourly": {}})

    with httpx.Client(transport=httpx.MockTransport(fetch)) as client, ThreadPoolExecutor(2) as pool:
        first = pool.submit(gate.get, client, URL, {"latitude": 52})
        assert entered.wait(5)
        second = pool.submit(gate.get, client, URL, {"latitude": 52})
        release.set()
        assert first.result() is second.result()


def test_rate_limit_blocks_other_models_and_endpoints_but_serves_cache(clock):
    gate = om.OpenMeteoClient(min_interval_seconds=0)
    fetch = Mock(side_effect=[httpx.Response(200, json={"hourly": {}}),
                              httpx.Response(429, headers={"Retry-After": "120"}),
                              httpx.Response(200, json={"hourly": {}})])
    with httpx.Client(transport=httpx.MockTransport(fetch)) as client:
        saved = gate.get(client, URL, {"models": "icon_eu"})
        assert gate.get(client, URL, {"models": "ecmwf"}).status_code == 429
        assert gate.get(client, "https://marine-api.open-meteo.com/v1/marine", {}).status_code == 429
        assert fetch.call_count == 2
        assert gate.get(client, URL, {"models": "icon_eu"}) is saved
        clock[0] += 119
        assert gate.get(client, URL, {}).headers["Retry-After"] == "1"
        clock[0] += 1
        assert gate.get(client, URL, {}).status_code == 200
        assert fetch.call_count == 3


@pytest.mark.parametrize(("reason", "seconds"), [
    ("Minutely API request limit exceeded", 60),
    ("Hourly API request limit exceeded", 3600),
    ("Daily API request limit exceeded", 86400),
    ("Too many requests", 300),
])
def test_quota_window_is_respected_without_retry_after(reason, seconds):
    assert om._retry_seconds(httpx.Response(429, json={"reason": reason})) == seconds


def test_retry_after_supports_http_date():
    until = datetime.now(UTC) + timedelta(minutes=5)
    response = httpx.Response(429, headers={"Retry-After": format_datetime(until)})
    assert 298 < om._retry_seconds(response) <= 300


def test_cache_expires_and_keeps_original_collection_time(clock):
    gate = om.OpenMeteoClient(ttl_seconds=60, min_interval_seconds=0)
    fetch = Mock(side_effect=lambda request: httpx.Response(200, json={"hourly": {}}))
    with httpx.Client(transport=httpx.MockTransport(fetch)) as client:
        first = gate.get(client, URL, {})
        collected = om.response_fetched_at(first)
        clock[0] += 59
        assert om.response_fetched_at(gate.get(client, URL, {})) == collected
        assert fetch.call_count == 1
        clock[0] += 1
        assert gate.get(client, URL, {}) is not first
        assert fetch.call_count == 2


def test_cache_is_bounded_and_failed_diagnostics_do_not_repeat():
    gate = om.OpenMeteoClient(min_interval_seconds=0, max_entries=2, max_bytes=20)
    fetch = Mock(side_effect=lambda request: httpx.Response(400, content=b"unsupported"))
    with httpx.Client(transport=httpx.MockTransport(fetch)) as client:
        gate.get(client, URL, {"hourly": "cape"})
        gate.get(client, URL, {"hourly": "cape"})
        assert fetch.call_count == 1
        gate.get(client, URL, {"hourly": "boundary_layer_height"})
        assert gate._bytes <= 20
        gate.get(client, URL, {"hourly": "cape"})
        assert fetch.call_count == 3


def test_cache_keys_roll_for_relative_hour_and_day_requests(monkeypatch):
    class FrozenDate(datetime):
        current = datetime(2026, 9, 21, 10, tzinfo=UTC)

        @classmethod
        def now(cls, tz=None):
            return cls.current

    monkeypatch.setattr(om, "datetime", FrozenDate)
    gate = om.OpenMeteoClient()
    hourly = gate._key(URL, {"forecast_hours": 48})
    daily = gate._key(URL, {"forecast_days": 3})
    FrozenDate.current += timedelta(hours=1)
    assert gate._key(URL, {"forecast_hours": 48}) != hourly
    assert gate._key(URL, {"forecast_days": 3}) == daily
    FrozenDate.current += timedelta(days=1)
    assert gate._key(URL, {"forecast_days": 3}) != daily


def test_only_cache_misses_are_paced(monkeypatch, clock):
    waits = []

    def wait(seconds):
        waits.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(om.time, "sleep", wait)
    gate = om.OpenMeteoClient(min_interval_seconds=1)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as client:
        gate.get(client, URL, {"models": "icon_eu"})
        gate.get(client, URL, {"models": "icon_eu"})
        assert waits == []
        gate.get(client, URL, {"models": "ecmwf"})
        assert waits == [1]


def test_async_and_sync_callers_share_the_same_cache(monkeypatch):
    gate = om.OpenMeteoClient(min_interval_seconds=0)
    monkeypatch.setattr(om, "openmeteo_client", gate)
    fetch = Mock(side_effect=lambda request: httpx.Response(200, json={"hourly": {}}))
    real_client = httpx.Client
    monkeypatch.setattr(om.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(fetch)))
    with real_client(transport=httpx.MockTransport(fetch)) as client:
        first = om.get_openmeteo(client, URL, {"latitude": 52})
    second = asyncio.run(om.async_get_openmeteo(URL, {"latitude": 52}))
    assert first is second
    assert fetch.call_count == 1


def test_future_cache_keeps_full_days_and_preserves_fetch_time(monkeypatch):
    gate = om.OpenMeteoClient(min_interval_seconds=0)
    monkeypatch.setattr(om, "openmeteo_client", gate)
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    model = next(m for m in default_model_catalog() if m.model_id == "icon_eu")

    def response(request):
        day_start = now.replace(hour=0)
        n = int(request.url.params["forecast_days"]) * 24
        return httpx.Response(200, json={"hourly": {
            "time": [(day_start + timedelta(hours=i)).isoformat() for i in range(n)],
            "wind_speed_10m": [5] * n, "wind_direction_10m": [270] * n,
        }})

    fetch = Mock(side_effect=response)
    real_client = httpx.Client
    monkeypatch.setattr(om.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(fetch)))
    adapter = OpenMeteoForecastAdapter(Settings())
    first = adapter.fetch_forecast_with_extras(model, [(52, 5)], now, now + timedelta(hours=24))
    # Asking for the complete final day used to trigger a duplicate HTTP call.
    day_end = (now + timedelta(hours=24)).replace(hour=23)
    second = adapter.fetch_forecast_with_extras(model, [(52, 5)], now, day_end)
    assert fetch.call_count == 1
    assert len(second) >= len(first)
    assert second[-1].valid_time_utc == day_end
    # A second adapter shares the raw HTTP cache and its original provenance.
    third = OpenMeteoForecastAdapter(Settings()).fetch_forecast_with_extras(model, [(52, 5)], now, day_end)
    assert fetch.call_count == 1
    assert {r.fetched_at_utc for r in third} == {r.fetched_at_utc for r in first}
    assert {r.run_time_utc for r in third} == {r.run_time_utc for r in first}


def test_adapter_stops_fallback_requests_after_a_rate_limit(monkeypatch):
    gate = om.OpenMeteoClient(min_interval_seconds=0)
    monkeypatch.setattr(om, "openmeteo_client", gate)
    fetch = Mock(side_effect=lambda request: httpx.Response(429, headers={"Retry-After": "3600"}))
    real_client = httpx.Client
    monkeypatch.setattr(om.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(fetch)))
    adapter = OpenMeteoForecastAdapter(Settings())
    now = datetime.now(UTC)
    models = [m for m in default_model_catalog() if m.model_id in {"icon_eu", "ecmwf_global"}]
    for model in models:
        assert adapter.fetch_model_at_coords(model, [(52, 5)], now - timedelta(hours=48), now) == []
        assert adapter.fetch_forecast_with_extras(model, [(52, 5)], now, now + timedelta(hours=48)) == []
    assert fetch.call_count == 1
