from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.catalog import default_model_catalog
from app.config import Settings
from app.domain import ForecastValue
from app.repositories import InMemoryRepository
from app.schemas import ForecastResponse
from app.services import ValidationService


@pytest.fixture
def forecast_service(monkeypatch):
    repo = InMemoryRepository()
    repo.models = [m for m in default_model_catalog() if m.model_id == "icon_eu"]
    repo.stations = []
    archive = []
    store = SimpleNamespace(
        save_forecasts=Mock(), save_observations=Mock(), save_forecast_observation_pairs=Mock(),
        recent_forecast_observation_pairs=lambda *args, **kwargs: [],
        load_forecasts=lambda ids, start, end, *args, **kwargs: [
            row for row in archive if row.model_id in ids and start <= row.valid_time_utc <= end
        ],
    )
    source = SimpleNamespace(fetch_model_at_coords=Mock(return_value=[]),
                             fetch_forecast_with_extras=Mock(return_value=[]))
    observations = SimpleNamespace(list_stations=lambda *args: [], get_observations=lambda *args: ([], []))
    monkeypatch.setattr("app.services.fetch_eps_sigma", lambda *args: {})
    service = ValidationService(repo, observations, source, Settings(), store=store)
    return service, source, store, archive


def test_loading_forecast_does_not_download_history_but_explicit_analysis_still_can(forecast_service):
    service, source, _, _ = forecast_service
    service.forecast_point(52, 5, "", 0, None, 48)
    source.fetch_model_at_coords.assert_not_called()
    source.fetch_forecast_with_extras.assert_called_once()
    service.validate_point(52, 5, 48, 50)
    source.fetch_model_at_coords.assert_called_once()


def test_empty_archive_does_not_fabricate_fallback_data(forecast_service):
    service, _, _, _ = forecast_service
    result = service.forecast_point(52, 5, "", 0, None, 48)
    assert result["models"] == []
    assert result["archive_fallbacks"] == []


def test_openmeteo_outage_uses_exact_point_archive_and_preserves_age(forecast_service):
    service, _, store, archive = forecast_service
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    collected = now - timedelta(hours=6)
    valid = now + timedelta(hours=3)

    def row(fetched, valid_at=valid, lat=52, speed=5):
        return ForecastValue("icon_eu", fetched, valid_at, lat, 5, 0, -speed,
                             run_time_source="fetched_snapshot", fetched_at_utc=fetched)

    archive.extend([
        row(collected - timedelta(hours=3), speed=4), row(collected),
        row(now, lat=52.1, speed=99),  # newer station data must not replace the pin
        row(now + timedelta(hours=1), speed=99),  # invalid future provenance
        row(collected, valid_at=now - timedelta(hours=2)),
        row(collected, valid_at=now + timedelta(hours=50)),
    ])
    result = service.forecast_point(52, 5, "", 0, None, 48)
    assert len(result["models"]) == 1
    hours = result["models"][0]["hours"]
    assert len(hours) == 1
    assert hours[0]["time_utc"] == valid
    assert hours[0]["ws_ms"] == 5
    assert result["archive_fallbacks"] == [{
        "model_id": "icon_eu", "fetched_at_utc": collected, "last_valid_time_utc": valid,
    }]
    store.save_forecasts.assert_not_called()  # archive rows are never saved as newly fetched
    serialized = ForecastResponse.model_validate(result).model_dump(mode="json")
    assert serialized["archive_fallbacks"][0]["fetched_at_utc"] == collected.isoformat().replace("+00:00", "Z")
