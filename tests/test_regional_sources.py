from app.config import Settings
from app.geo import detect_country
from app.observation_broker import ObservationBroker
from app.repositories import InMemoryRepository


def test_regional_source_routes_are_selected_by_sailing_area():
    broker = ObservationBroker(InMemoryRepository(), Settings())

    assert [source.source_name for source in broker._source_order("SE")][:2] == ["viva", "smhi"]
    assert broker._source_order("NL")[2].source_name == "rws"
    assert broker._source_order("US")[0].source_name == "ndbc"
    assert broker._source_order("PL")[0].source_name == "imgw"


def test_country_detection_routes_sweden_poland_and_us_networks():
    assert detect_country(57.70, 11.90) == "SE"
    assert detect_country(54.60, 18.80) == "PL"
    assert detect_country(25.80, -80.10) == "US"
