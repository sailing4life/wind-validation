from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.cache import TTLCache
from app.calibration import CalibrationSample, bias_drift
from app.config import Settings
from app.domain import ForecastValue
from app.services import ValidationService, _bucket_by_solar_hour, nearest_forecast


NOW = datetime(2026, 8, 1, 12, tzinfo=UTC)


def service():
    value = object.__new__(ValidationService)
    value.settings = Settings()
    value._forecast_context = TTLCache(3600)
    value._validation_context = TTLCache(3600)
    return value


def sample(age, error=1.0, station="a"):
    return CalibrationSample(NOW - timedelta(hours=age), 0, -5, 0, -5-error,
                             station_id=station, local_solar_hour=12)


def forecast():
    return ForecastValue("model", NOW, NOW + timedelta(hours=1), 52, 5, 0, -5)


def test_live_bias_uses_three_hours_even_when_old_errors_have_opposite_sign():
    recent = [sample(age, 1, station) for age in (0.25, 1.25, 2.25) for station in ("a", "b", "c")]
    older = [sample(age, -3) for age in range(4, 49)]
    cal = service()._calibrate_member_hour(forecast(), recent + older, {}, NOW, 0)
    assert cal["bias_window_hours"] == 3
    assert cal["bias_source"] == "recent_3h"
    assert cal["bias_v"] == pytest.approx(-1)
    assert cal["ws_ms"] > 5


def test_sparse_recent_data_expands_to_six_hours():
    cal = service()._calibrate_member_hour(forecast(), [sample(age) for age in (0.5, 1.3, 2.1, 3, 4, 5)], {}, NOW, 0)
    assert cal["bias_source"] == "fallback_6h"
    assert cal["bias_window_hours"] == 6


def test_stale_observations_cannot_drive_a_live_correction():
    cal = service()._calibrate_member_hour(forecast(), [sample(age) for age in range(3, 49)], {}, NOW, 0)
    assert cal["status"] == "insufficient_history"
    assert cal["bias_source"] == "raw"
    assert cal["latest_observation_utc"] == NOW - timedelta(hours=3)


def test_historical_fallback_is_explicit_and_retains_historical_uncertainty():
    history = _bucket_by_solar_hour([sample(24 * day) for day in range(1, 25)])
    cal = service()._calibrate_member_hour(forecast(), [], history, NOW, 0)
    assert cal["bias_source"] == "historical"
    assert cal["bias_window_hours"] is None
    assert cal["uncertainty_source"] == "historical_hour_regime"


def test_live_bias_rejects_one_time_with_many_stations_and_future_samples():
    samples = [sample(0.5, station=str(i)) for i in range(20)] + [sample(-1, 10)]
    cal = service()._calibrate_member_hour(forecast(), samples, {}, NOW, 0)
    assert cal["bias_source"] == "raw"


def test_station_changes_do_not_create_bias_drift():
    same_station = [sample(age, 1) for age in (0.5, 1.5, 3.5, 4.5)]
    new_station = [sample(age, 10, "new") for age in (0.5, 1.5)]
    departed_station = [sample(age, -10, "departed") for age in (3.5, 4.5)]
    result = bias_drift(same_station + new_station + departed_station, NOW)
    assert result["status"] == "stable"
    assert result["station_ids"] == ["a"]
    assert result["delta_speed_ms"] == 0


def test_bias_drift_detects_change_on_matched_stations():
    rows = [sample(age, 2 if age < 3 else 0) for age in (0.5, 1.5, 3.5, 4.5)]
    result = bias_drift(rows, NOW)
    assert result["status"] == "changing"
    assert result["delta_speed_ms"] == 2
    assert bias_drift(rows[:2], NOW)["status"] == "insufficient_data"


def test_forecast_preparation_ignores_analysis_window_and_reuses_fixed_evidence():
    svc = service()
    actual = datetime.now(UTC)
    now = actual.replace(minute=0, second=0, microsecond=0)
    analysis = {"lat": 52, "lon": 5, "radius_km": 50, "hours_back": 24,
                "window_end_utc": now, "computed_at_utc": actual, "weights": {"wrong": 1}}
    fixed = {**analysis, "hours_back": 48, "weights": {"correct": 1}}
    svc._validation_context.set("analysis", analysis)
    calls = []

    def validate(lat, lon, hours, radius):
        calls.append((lat, lon, hours, radius))
        svc._validation_context.set("fixed", fixed)
        return {"query_id": "fixed"}

    svc.validate_point = validate
    assert svc._prepare_forecast_context(52, 5, 50, now, "analysis")["weights"] == {"correct": 1}
    assert svc._prepare_forecast_context(52, 5, 50, now, None)["weights"] == {"correct": 1}
    assert calls == [(52, 5, 48, 50)]


def test_causal_pairing_rejects_legacy_and_later_fetched_snapshots():
    legacy = forecast()
    legacy.run_time_source = "legacy_unknown"
    snapshot = forecast()
    snapshot.run_time_source = "fetched_snapshot"
    snapshot.fetched_at_utc = NOW + timedelta(minutes=1)
    assert nearest_forecast([legacy, snapshot], 52, 5, not_after=NOW) is None
    snapshot.fetched_at_utc = NOW
    assert nearest_forecast([legacy, snapshot], 52, 5, not_after=NOW) is snapshot


def test_forecast_without_validation_request_prepares_weights_and_ignores_legacy_bias(monkeypatch):
    svc = service()
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    model = SimpleNamespace(model_id="model", status="ACTIVE")
    svc.repo = SimpleNamespace(models=[model])
    svc.store = None
    svc.fingerprint_service = None
    fv = ForecastValue("model", now, now, 52, 5, 0, -5)
    svc.forecast_adapter = SimpleNamespace(fetch_forecast_with_extras=lambda *args: [fv])
    monkeypatch.setattr("app.services.fetch_eps_sigma", lambda *args: {})
    calls = []

    def validate(lat, lon, hours, radius):
        calls.append(hours)
        rows = [CalibrationSample(now - timedelta(hours=age), 0, -5, 0, -6, station_id=sid)
                for age in (0, 1, 2) for sid in ("a", "b", "c")]
        svc._validation_context.set("prepared", {
            "lat": lat, "lon": lon, "radius_km": radius, "hours_back": hours,
            "window_end_utc": now, "computed_at_utc": datetime.now(UTC),
            "weights": {"model": 1}, "winner_model_id": "model", "samples": {"model": rows},
        })
        return {"query_id": "prepared"}

    svc.validate_point = validate
    result = svc.forecast_point(52, 5, "analysis_winner", 99, None, 24)
    repeated = svc.forecast_point(52, 5, "different_winner", -99, None, 24)
    assert calls == [48]
    assert result["winner_model_id"] == "model"
    assert result["calibration"]["bias_source"] == "recent_3h"
    assert result["bias_ws_ms"] == pytest.approx(-1)
    assert repeated["bias_ws_ms"] == result["bias_ws_ms"]
    assert result["models"][0]["hours"][0]["calibration_bias_window_hours"] == 3
