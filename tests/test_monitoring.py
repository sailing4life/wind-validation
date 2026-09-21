from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from app.domain import ForecastValue, ModelDefinition, Station
from app.monitoring import LocationMonitoringService, compare_forecasts, monitoring_active

UTC = timezone.utc


def envelope(ws=5, wd=359, computed="2026-01-01T10:00:00+00:00"):
    return {"computed_at_utc": computed, "forecast": {"models": [{"model_id": "test", "hours": [
        {"time_utc": "2026-01-01T15:00:00+00:00", "ws_ms": ws, "wd_deg": wd}]}]}, "run_comparison": None}


def test_comparison_aligns_future_hours_and_wraps_direction():
    prior = envelope()
    current = envelope(6, 1, "2026-01-01T11:00:00+00:00")
    result = compare_forecasts(prior, current, datetime(2026, 1, 1, 12, tzinfo=UTC))
    assert result["kind"] == "forecast_snapshots"
    model = result["models"][0]
    assert model["mean_ws_change_ms"] == 1
    assert model["mean_wd_change_deg"] == 2
    assert compare_forecasts(prior, current, datetime(2026, 1, 1, 16, tzinfo=UTC)) is None
    prior["run_comparison"] = result
    assert compare_forecasts(prior, envelope(), datetime(2026, 1, 1, 12, tzinfo=UTC)) == result


def test_monitoring_dates_inclusive_and_opt_in():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert not monitoring_active({}, now)
    assert monitoring_active({"monitoring_enabled": True, "monitoring_start": "2026-01-01", "monitoring_end": "2026-01-01"}, now)
    assert not monitoring_active({"monitoring_enabled": True, "monitoring_end": "2025-12-31"}, now)


class MemoryStore:
    enabled = True
    def __init__(self):
        self.locations = [{"id": 1, "name": "Muiden", "lat": 52.33, "lon": 5.07, "radius_km": 30,
                           "monitoring_enabled": True}]
        self.snapshots = {}
        self.forecasts = []
        self.events = []
    def list_locations(self): return self.locations
    def get_location(self, ident): return next((l for l in self.locations if l["id"] == ident), None)
    def read_location_snapshot(self, ident): return deepcopy(self.snapshots.get(ident))
    def save_location_snapshot(self, ident, payload): self.snapshots[ident] = deepcopy(payload)
    def save_forecasts(self, rows):
        self.forecasts.extend(rows)
        self.events.append("archive")
    def record_location_refresh_error(self, ident, message):
        self.snapshots[ident]["last_error"] = message


def test_monitoring_archives_pin_and_discovered_stations_before_validation_and_keeps_last_good():
    store = MemoryStore()
    station = Station("dynamic", "test", "NL", 52.4, 5.1)
    model = ModelDefinition("test", "test", "global", {"min_lat": -90, "max_lat": 90, "min_lon": -180, "max_lon": 180}, {})
    repo = SimpleNamespace(models=[model], stations=[], point_country=lambda *_: "NL")
    def fetch(model, coords, start, end):
        return [ForecastValue(model.model_id, start, end, lat, lon, 2, 3,
                              run_time_source="fetched_snapshot", fetched_at_utc=start) for lat, lon in coords]
    adapter = SimpleNamespace(fetch_forecast_with_extras=Mock(side_effect=fetch))
    def validate(*args, **kwargs):
        store.events.append("validate")
        assert kwargs["force_refresh"] is True
        assert kwargs["fetch_historical_forecasts"] is False
        return {"query_id": "q", "winner_model_id": "test", "models": []}
    validation = SimpleNamespace(validate_point=validate, forecast_point=Mock(return_value=envelope()["forecast"]))
    service = LocationMonitoringService(repo, SimpleNamespace(openmeteo=adapter),
        SimpleNamespace(list_stations=lambda *_: [station]), validation, store)
    assert service.snapshot(1)["status"] == "pending"
    assert service.refresh() == 1
    assert store.events == ["archive", "validate"]
    assert {(r.lat, r.lon) for r in store.forecasts} == {(52.33, 5.07), (52.4, 5.1)}
    assert station in repo.stations
    assert validation.forecast_point.call_args.kwargs == {"radius_km": 30}
    good = service.snapshot(1)
    validation.forecast_point.return_value = {"models": []}
    service.refresh()
    failed = service.snapshot(1)
    assert failed["status"] == "ready"
    assert failed["computed_at_utc"] == good["computed_at_utc"]
    assert failed["forecast"] == good["forecast"]
    assert failed["last_error"]
    # A separate web process can read the persisted failure alongside good data.
    fresh_service = LocationMonitoringService(repo, None, None, None, store)
    assert fresh_service.snapshot(1)["last_error"]
