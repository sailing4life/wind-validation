from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.config import Settings
from app.dublin_bay_adapter import DublinBayBuoyAdapter, KNOT_TO_MS
from app.geo import detect_country
from app.observation_broker import ObservationBroker
from app.repositories import InMemoryRepository
from app.schemas import ObservationPointDTO
from app.storage import PostgresStore


START = datetime(2026, 9, 20, 10, tzinfo=UTC)
END = START + timedelta(hours=2)
READING = {"timestamp": "2026-09-20T10:00:00Z", "avg_wind": 9, "wind_dir": 162, "gust_speed": 11}


def adapter():
    return DublinBayBuoyAdapter(Settings(live_observations_enabled=True, dublin_bay_buoy_enabled=True))


def mock_http(monkeypatch, handler):
    client = httpx.Client
    monkeypatch.setattr("app.dublin_bay_adapter.httpx.Client", lambda **kw: client(transport=httpx.MockTransport(handler), **kw))


def test_mean_direction_gust_and_utc_are_preserved_with_correct_units():
    rows = adapter().parse_readings([READING, {**READING, "timestamp": "2026-09-20T12:00:00+01:00", "avg_wind": 0, "wind_dir": 360}], START, END)
    assert len(rows) == 2
    assert rows[0].ws_ms == pytest.approx(9 * KNOT_TO_MS)
    assert rows[0].gust_ms == pytest.approx(11 * KNOT_TO_MS)
    assert rows[0].wd_deg == 162
    assert rows[1].time_utc == START + timedelta(hours=1)
    assert rows[1].ws_ms == 0 and rows[1].wd_deg == 0
    dto = ObservationPointDTO(station_id=rows[0].station_id, source=rows[0].source,
                             lat=53.33, lon=-6.1, time_utc=rows[0].time_utc,
                             ws_ms=rows[0].ws_ms, wd_deg=rows[0].wd_deg, gust_ms=rows[0].gust_ms)
    assert dto.model_dump()["gust_ms"] == rows[0].gust_ms


def test_bad_rows_are_dropped_without_losing_good_wind_and_no_fake_gust():
    payload = [None, {}, {**READING, "avg_wind": None}, {**READING, "avg_wind": "nan"},
               {**READING, "avg_wind": -1}, {**READING, "wind_dir": 999},
               {**READING, "timestamp": "invalid"}, {**READING, "timestamp": "2026-09-19T10:00:00Z"},
               {**READING, "timestamp": "2026-09-21T10:00:00Z"},
               {**READING, "gust_speed": None}, {**READING, "gust_speed": 1}]
    rows = adapter().parse_readings(payload, START, END)
    assert len(rows) == 1  # duplicate hourly readings collapse to one timestamp
    assert rows[0].gust_ms is None
    assert rows[0].ws_ms == pytest.approx(9 * KNOT_TO_MS)
    assert adapter().parse_readings({"error": "no data"}, START, END) == []


def test_bounded_pagination_and_cache_preserve_requested_window(monkeypatch):
    requests = []
    def respond(request):
        requests.append(request)
        assert request.url.path == "/rest/v1/readings"
        assert request.headers["apikey"].startswith("sb_publishable_")
        assert len(request.url.params.get_list("timestamp")) == 2
        if request.url.params["offset"] == "0":
            return httpx.Response(200, json=[READING, {**READING, "timestamp": "2026-09-20T11:00:00Z"}])
        return httpx.Response(200, json=[{**READING, "timestamp": "2026-09-20T13:00:00Z"}])
    mock_http(monkeypatch, respond)
    source = adapter()
    source.PAGE_SIZE = 2
    rows = source.get_obs(None, {source.STATION_ID}, START, END)
    assert len(rows) == 2
    assert len(requests) == 2
    narrowed = source.get_obs(None, {source.STATION_ID}, START + timedelta(minutes=10), END)
    assert len(requests) == 2
    assert len(narrowed) == 1 and narrowed[0].time_utc.hour == 11


def test_disabled_or_unselected_source_never_calls_api(monkeypatch):
    mock_http(monkeypatch, lambda request: pytest.fail("Unexpected API request"))
    source = adapter()
    assert source.get_obs(None, {"OTHER"}, START, END) == []
    source.settings.dublin_bay_buoy_enabled = False
    assert source.get_obs(None, {source.STATION_ID}, START, END) == []
    assert source.list_stations(InMemoryRepository(), 53.33, -6.1, 50) == []
    source.settings.dublin_bay_buoy_enabled = True
    source.settings.live_observations_enabled = False
    assert source.get_obs(None, {source.STATION_ID}, START, END) == []


@pytest.mark.parametrize("status,payload", [(503, None), (200, {"message": "bad response"})])
def test_source_failures_return_no_fabricated_readings(monkeypatch, status, payload):
    mock_http(monkeypatch, lambda request: httpx.Response(status, json=payload))
    source = adapter()
    assert source.get_obs(None, {source.STATION_ID}, START, END) == []


def test_dublin_station_is_water_relevant_and_flows_through_observation_broker(monkeypatch):
    assert detect_country(53.33, -6.1) == "IE"
    repo = InMemoryRepository()
    source = adapter()
    assert source.list_stations(repo, 52.1, 5.1, 50) == []
    station = source.list_stations(repo, 53.33, -6.1, 10)[0]
    assert station.station_type == "buoy"
    mock_http(monkeypatch, lambda request: httpx.Response(200, json=[READING]))
    broker = ObservationBroker(repo, source.settings)
    rows, provenance = broker.get_observations("IE", {station.station_id}, START, END)
    assert provenance == [source.source_name]
    assert len(rows) == 1 and rows[0].qc_passed
    assert rows[0].gust_ms == pytest.approx(11 * KNOT_TO_MS)


def test_observed_gust_is_written_to_archive():
    calls = []
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def executemany(self, sql, values): calls.append((sql, values))
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def cursor(self): return Cursor()
    store = PostgresStore("unused")
    store._connect = Connection
    store.save_observations(adapter().parse_readings([READING], START, END))
    assert "gust_ms=EXCLUDED.gust_ms" in calls[0][0]
    assert calls[0][1][0][-1] == pytest.approx(11 * KNOT_TO_MS)
