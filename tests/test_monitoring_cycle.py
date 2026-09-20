"""Collector → archive → causal validation → calibrated API contract, offline."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.config import Settings
from app.domain import ForecastValue, ModelDefinition, Observation, Station
from app.monitoring import LocationMonitoringService
from app.repositories import InMemoryRepository
from app.schemas import ForecastResponse
from app.services import ValidationService

UTC = timezone.utc


class Clock(datetime):
    current = datetime(2026, 8, 1, 10, 10, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)


class Archive:
    enabled = True
    location = {"id": 1, "name": "Muiden", "lat": 52.33, "lon": 5.07, "radius_km": 30,
                "monitoring_enabled": True}

    def __init__(self):
        self.forecasts, self.observations, self.pairs = [], [], []
        self.snapshot = None

    def list_locations(self): return [self.location]
    def get_location(self, ident): return self.location if ident == 1 else None
    def read_location_snapshot(self, ident): return self.snapshot
    def save_location_snapshot(self, ident, value): self.snapshot = value
    def save_forecasts(self, rows): self.forecasts.extend(rows)
    def save_observations(self, rows): self.observations.extend(rows)
    def save_forecast_observation_pairs(self, rows): self.pairs.extend(rows)
    def recent_forecast_observation_pairs(self, *args, **kwargs): return []
    def load_forecasts(self, model_ids, start, end, lat, lon, **kwargs):
        return [r for r in self.forecasts if r.model_id in model_ids and start <= r.valid_time_utc <= end]
    def record_location_refresh_error(self, ident, message):
        raise AssertionError(message)


class ForecastSource:
    """All source timestamps are explicitly collection times, including backfills."""
    def fetch_forecast_with_extras(self, model, coords, start, end):
        return self.fetch_model_at_coords(model, coords, start, end)

    def fetch_model_at_coords(self, model, coords, start, end):
        result = []
        valid = start
        while valid <= end:
            for lat, lon in coords:
                result.append(ForecastValue(model.model_id, Clock.current, valid, lat, lon, 0, -5,
                              gust_ms=8, run_time_source="fetched_snapshot", fetched_at_utc=Clock.current))
            valid += timedelta(hours=1)
        return result


def test_real_validation_uses_earlier_collected_forecasts_in_next_monitoring_cycle(monkeypatch):
    monkeypatch.setattr("app.monitoring.datetime", Clock)
    monkeypatch.setattr("app.services.datetime", Clock)
    monkeypatch.setattr("app.services.fetch_eps_sigma", lambda *args: {})
    Clock.current = datetime(2026, 8, 1, 10, 10, tzinfo=UTC)
    repo = InMemoryRepository()
    repo.models = [ModelDefinition("test", "test", "global",
                    {"min_lat": -90, "max_lat": 90, "min_lon": -180, "max_lon": 180}, {})]
    repo.stations = []
    stations = [Station(str(i), "test", "NL", 52.33 + i * .01, 5.07) for i in range(3)]
    calls = []
    def observations(country, ids, start, end):
        calls.append(end)
        hours = (9,) if Clock.current.hour == 10 else (11, 12, 13)
        return ([Observation(s.station_id, s.source, Clock.current.replace(hour=hour, minute=0), 6, 0)
                 for s in stations for hour in hours], ["fake_source"])
    broker = SimpleNamespace(list_stations=lambda *args: stations, get_observations=observations)
    source = ForecastSource()
    settings = Settings(live_forecasts_enabled=True, min_samples=6)
    store = Archive()
    validation = ValidationService(repo, broker, source, settings, store=store)
    monitoring = LocationMonitoringService(repo, SimpleNamespace(openmeteo=source, settings=settings), broker, validation, store)

    initial = monitoring.refresh_location(1)
    assert initial["status"] == "ready"
    assert initial["validation"]["models"][0]["n_samples"] == 0
    assert store.pairs == []  # A fresh historical download is never earlier evidence.
    assert any(r.valid_time_utc.hour == 11 for r in store.forecasts)
    ForecastResponse.model_validate(initial["forecast"]).model_dump_json()

    Clock.current += timedelta(hours=3)
    next_snapshot = monitoring.refresh_location(1)
    assert next_snapshot["status"] == "ready"
    assert next_snapshot["validation"]["models"][0]["n_samples"] == 9
    assert len(store.pairs) == 9
    assert all(p["fetched_at_utc"].hour == 10 and p["fetched_at_utc"] < p["obs_time_utc"] for p in store.pairs)
    assert len(store.observations) == 12
    assert calls == [datetime(2026, 8, 1, 10, 10, tzinfo=UTC), datetime(2026, 8, 1, 13, 10, tzinfo=UTC)]
    forecast = ForecastResponse.model_validate(next_snapshot["forecast"])
    assert forecast.calibration["bias_source"] == "recent_3h"
    assert forecast.calibration["bias_window_hours"] == 3
    assert forecast.models[0].hours[0].corrected_ws_ms > forecast.models[0].hours[0].ws_ms
    assert forecast.model_dump_json()


def test_collector_respects_disabled_live_forecasts():
    store = Archive()
    store.snapshot = {"status": "ready", "forecast": {"models": []}, "computed_at_utc": "2026-08-01T10:00:00Z"}
    monitoring = LocationMonitoringService(None, SimpleNamespace(settings=Settings(live_forecasts_enabled=False)),
                                           None, None, store)
    assert monitoring.refresh() == 1
    assert monitoring.snapshot(1)["computed_at_utc"] == store.snapshot["computed_at_utc"]
    assert store.forecasts == []
