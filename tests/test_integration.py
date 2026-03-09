from fastapi.testclient import TestClient

from wind_validation.app.main import app


client = TestClient(app)


def test_it_point_uses_isd_provenance():
    response = client.post(
        "/v1/validate-point",
        json={"lat": 45.46, "lon": 9.19, "hours_back": 48, "radius_km": 50},
    )
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["source_provenance"], list)


def test_models_report_live_only_statuses():
    response = client.post(
        "/v1/validate-point",
        json={"lat": 52.1, "lon": 5.1, "hours_back": 48, "radius_km": 50},
    )
    assert response.status_code == 200
    body = response.json()
    assert all(row["status"] in {"ok", "insufficient_data", "excluded"} for row in body["models"])
