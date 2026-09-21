"""Offline checks for the user-facing monitoring and prepared forecast contract."""
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main


@pytest.fixture
def location_api(monkeypatch):
    location = {
        "id": 7, "name": "Muiden", "lat": 52.33, "lon": 5.07, "radius_km": 35,
        "monitoring_enabled": False, "monitoring_start": None, "monitoring_end": None,
    }

    def update(location_id, enabled, start, end):
        location.update(monitoring_enabled=enabled, monitoring_start=start, monitoring_end=end)
        return dict(location)

    store = SimpleNamespace(
        enabled=True,
        get_location=lambda location_id: dict(location) if location_id == 7 else None,
        list_locations=lambda: [dict(location)],
        update_location_monitoring=update,
        save_location=lambda name, lat, lon, radius: {"id": 8, "name": name, "lat": lat, "lon": lon, "radius_km": radius},
    )
    monkeypatch.setattr(main, "store", store)
    return TestClient(main.app), location, store


def test_monitoring_period_roundtrips_and_rejects_reversed_dates(location_api):
    client, location, _ = location_api
    response = client.patch("/api/locations/7/monitoring", json={
        "monitoring_enabled": True, "monitoring_start": "2026-09-20", "monitoring_end": "2026-09-25",
    })
    assert response.status_code == 200
    assert response.json()["monitoring_start"] == "2026-09-20"
    assert location["monitoring_start"] == date(2026, 9, 20)
    response = client.patch("/api/locations/7/monitoring", json={
        "monitoring_enabled": True, "monitoring_start": "2026-09-25", "monitoring_end": "2026-09-20",
    })
    assert response.status_code == 422
    assert location["monitoring_end"] == date(2026, 9, 25)


def test_missing_location_and_disabled_storage_are_explicit(location_api):
    client, _, store = location_api
    assert client.get("/api/locations/999/snapshot").status_code == 404
    assert client.patch("/api/locations/999/monitoring", json={"monitoring_enabled": True}).status_code == 404
    store.enabled = False
    assert client.get("/api/locations/7/snapshot").status_code == 503
    assert client.patch("/api/locations/7/monitoring", json={"monitoring_enabled": True}).status_code == 503


def test_snapshot_reads_prepared_result_without_fetching_sources(location_api, monkeypatch):
    client, location, _ = location_api
    snapshot = {"location": location, "status": "ready", "computed_at_utc": "2026-09-20T14:00:00Z",
                "validation": {"observation_points": []}, "forecast": {"models": []},
                "run_comparison": {"status": "insufficient_history"}}
    monkeypatch.setattr(main, "monitoring_service", SimpleNamespace(snapshot=lambda location_id: snapshot))

    def never_fetch(*args, **kwargs):
        raise AssertionError("Reading a prepared snapshot must not fetch or recalculate")

    monkeypatch.setattr(main.validation_service, "forecast_point", never_fetch)
    monkeypatch.setattr(main.ingestion_service, "refresh", never_fetch)
    response = client.get("/api/locations/7/snapshot")
    assert response.status_code == 200
    assert response.json()["computed_at_utc"] == snapshot["computed_at_utc"]
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["refresh_interval_seconds"] == main.SETTINGS.refresh_interval_seconds


def test_save_location_validates_coordinates_and_normalizes_name(location_api):
    client, _, _ = location_api
    assert client.post("/api/locations", json={"name": "x", "lat": 91, "lon": 5}).status_code == 422
    assert client.post("/api/locations", json={"name": "  ", "lat": 52, "lon": 5}).status_code == 422
    response = client.post("/api/locations", json={"name": "  Muiden  ", "lat": 52, "lon": 5})
    assert response.status_code == 200
    assert response.json()["name"] == "Muiden"


def test_free_point_forecast_accepts_no_validation_id_and_preserves_bias_metadata(monkeypatch):
    seen = {}

    def forecast(**kwargs):
        seen.update(kwargs)
        return {"winner_model_id": "", "bias_ws_ms": 0, "hours_ahead": 48,
                "models": [{"model_id": "test", "hours": [{
                    "time_utc": "2026-09-20T15:00:00Z", "ws_ms": 5,
                    "calibration_bias_source": "recent_3h", "calibration_bias_window_hours": 3,
                }]}], "calibration": {"bias_window_hours": 3, "model_weight_window_hours": 48}}

    monkeypatch.setattr(main.validation_service, "forecast_point", forecast)
    response = TestClient(main.app).post("/api/forecast", json={"lat": 52, "lon": 5, "radius_km": 30})
    assert response.status_code == 200
    assert seen["radius_km"] == 30
    assert seen["query_id"] is None
    assert seen["winner_model_id"] == ""
    assert response.json()["models"][0]["hours"][0]["calibration_bias_source"] == "recent_3h"
