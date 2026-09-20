from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from app.config import Settings
from app.domain import ForecastValue
from app.forecast_adapters import OpenMeteoForecastAdapter
from app.storage import PostgresStore

UTC = timezone.utc


def test_missing_initialization_is_fetch_snapshot_and_cannot_appear_before_collection(monkeypatch):
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    payload = {"hourly": {"time": [(now - timedelta(hours=2)).isoformat(), (now + timedelta(hours=2)).isoformat()],
                         "wind_speed_10m": [5, 6], "wind_direction_10m": [350, 10]}}
    response = MagicMock()
    response.json.return_value = payload
    monkeypatch.setattr("app.forecast_adapters._get_with_retry", lambda *a: response)
    before = datetime.now(UTC)
    rows = OpenMeteoForecastAdapter(Settings())._fetch_batch("https://example.invalid", "test", "", [(52, 5)],
               1, 1, now - timedelta(hours=3), now + timedelta(hours=3))
    assert len(rows) == 2
    assert all(row.run_time_source == "fetched_snapshot" for row in rows)
    assert all(row.run_time_utc == row.fetched_at_utc >= before for row in rows)
    assert rows[0].run_time_utc > rows[0].valid_time_utc


def test_explicit_source_run_preserved_separately_from_fetch_time(monkeypatch):
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    run = now - timedelta(hours=6)
    response = MagicMock()
    response.json.return_value = {"run_time": run.astimezone(timezone(timedelta(hours=2))).isoformat(), "hourly": {
        "time": [now.isoformat()], "wind_speed_10m": [5], "wind_direction_10m": [350]}}
    monkeypatch.setattr("app.forecast_adapters._get_with_retry", lambda *a: response)
    row = OpenMeteoForecastAdapter(Settings())._fetch_batch("https://example.invalid", "test", "", [(52, 5)],
                                                          1, 1, now, now)[0]
    assert row.run_time_source == "source"
    assert row.run_time_utc == run
    assert row.fetched_at_utc > run


def test_storage_read_retains_snapshot_provenance_and_diagnostic_fields(monkeypatch):
    now = datetime.now(UTC)
    values = ("test", now, now + timedelta(hours=1), 52.0, 5.0, 1.0, 2.0,
              8.0, 12.0, 0.0, 50.0, 1012.0, 350.0, 40.0, 1200.0, "fetched_snapshot", now)
    conn = MagicMock()
    cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
    cur.fetchall.return_value = [values]
    store = PostgresStore("postgresql://fake")
    monkeypatch.setattr(store, "_connect", lambda: conn)
    rows = store.load_forecasts(["test"], now, now + timedelta(hours=3), 52, 5)
    assert rows == [ForecastValue(*values)]
    assert rows[0].run_time_source == "fetched_snapshot"
    assert rows[0].fetched_at_utc == now
    assert rows[0].shortwave_wm2 == 350


def test_future_collection_reuses_shared_station_and_pin_data(monkeypatch):
    from app.catalog import default_model_catalog
    model = next(m for m in default_model_catalog() if m.model_id == "ecmwf_global")
    adapter = OpenMeteoForecastAdapter(Settings())
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    end = now + timedelta(hours=48)
    def fetch(url, model_id, param, coords, **kwargs):
        return [ForecastValue(model_id, now, end, lat, lon, 1, 2,
                              run_time_source="fetched_snapshot", fetched_at_utc=now) for lat, lon in coords]
    network = MagicMock(side_effect=fetch)
    monkeypatch.setattr(adapter, "_fetch_batch", network)
    shared = adapter.fetch_forecast_with_extras(model, [(52, 5), (52.1, 5.1)], now, end)
    pin = adapter.fetch_forecast_with_extras(model, [(52, 5)], now, end)
    assert network.call_count == 1
    assert pin == [shared[0]]
    neighbor = adapter.fetch_forecast_with_extras(model, [(52.1, 5.1), (52.2, 5.2)], now, end)
    assert network.call_count == 2
    assert network.call_args.args[3] == [(52.2, 5.2)]
    assert len(neighbor) == 2


def test_storage_rejects_backfill_even_if_later_observation_arrives_after_collection(monkeypatch):
    valid = datetime(2026, 8, 1, 10, tzinfo=UTC)
    observation = valid + timedelta(minutes=20)
    base = {"model_id": "test", "valid_time_utc": valid, "station_id": "a", "station_source": "test",
            "station_type": "synop", "station_lat": 52, "station_lon": 5, "obs_time_utc": observation,
            "model_u": 0, "model_v": -5, "obs_u": 0, "obs_v": -6, "lead_hours": 1,
            "local_solar_hour": 10, "run_time_source": "fetched_snapshot"}
    late = valid + timedelta(minutes=16)
    early = valid - timedelta(hours=1)
    rows = [{**base, "run_time_utc": collected, "fetched_at_utc": collected} for collected in (early, late)]
    conn = MagicMock()
    cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
    store = PostgresStore("postgresql://fake")
    monkeypatch.setattr(store, "_connect", lambda: conn)
    store.save_forecast_observation_pairs(rows)
    saved = cur.executemany.call_args.args[1]
    assert len(saved) == 1
    assert saved[0][1] == early
