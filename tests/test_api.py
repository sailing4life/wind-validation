from fastapi.testclient import TestClient

from wind_validation.app.main import app


client = TestClient(app)


def test_validate_point_contract():
    response = client.post(
        "/v1/validate-point",
        json={"lat": 52.1, "lon": 5.1, "hours_back": 48, "radius_km": 50},
    )
    assert response.status_code == 200
    body = response.json()
    assert "query_id" in body
    assert "models" in body
    assert "stations_used" in body


def test_validate_point_rejects_invalid_payload():
    response = client.post(
        "/v1/validate-point",
        json={"lat": 123.0, "lon": 5.1, "hours_back": 48, "radius_km": 50},
    )
    assert response.status_code == 422


def test_stations_endpoint():
    response = client.get("/v1/stations/nearby", params={"lat": 52.1, "lon": 5.1, "radius_km": 50})
    assert response.status_code == 200
    body = response.json()
    assert "stations" in body