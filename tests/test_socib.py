from datetime import UTC, datetime

from app.adapters import SocibPalmaAdapter
from app.config import Settings
from app.geo import detect_country
from app.observation_broker import ObservationBroker
from app.repositories import InMemoryRepository


SOCIB_ASCII = """Dataset {\n} sample;\n---------------------------------------------\ntime[3]\n1762240800, 1762241400, 1762242000\nWIN_DIR[3]\n29, NaN, 361\nQC_WIN_DIR[3]\n1, 1, 2\nWIN_SPE[3]\n4.06, 3.75, 2.35\nQC_WIN_SPE[3]\n1, 1, 4\n"""


def test_socib_parser_keeps_only_in_window_good_wind_rows():
    start = datetime(2025, 11, 4, 7, 0, tzinfo=UTC)
    end = datetime(2025, 11, 4, 8, 0, tzinfo=UTC)

    rows = SocibPalmaAdapter._parse_observations(SOCIB_ASCII, start, end)

    assert len(rows) == 1
    assert rows[0].station_id == "SOCIB_BAHIA_PALMA"
    assert rows[0].ws_ms == 4.06
    assert rows[0].wd_deg == 29


def test_palma_routes_to_spain_and_lists_socib_buoy():
    assert detect_country(39.57, 2.65) == "ES"

    stations = ObservationBroker(InMemoryRepository(), Settings()).list_stations("ES", 39.57, 2.65, 50)

    assert any(station.station_id == "SOCIB_BAHIA_PALMA" for station in stations)
