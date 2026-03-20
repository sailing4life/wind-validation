from wind_validation.app.location_fingerprint import _summarize_coast, _summarize_terrain


def test_summarize_coast_detects_open_water_sector():
    rows = [
        {"bearing_deg": 225.0, "sample_distance_km": 8.0, "effective_distance_km": 10.0, "sea_hit": True},
        {"bearing_deg": 247.5, "sample_distance_km": 8.0, "effective_distance_km": 9.0, "sea_hit": True},
        {"bearing_deg": 270.0, "sample_distance_km": 8.0, "effective_distance_km": 8.5, "sea_hit": True},
        {"bearing_deg": 292.5, "sample_distance_km": 16.0, "effective_distance_km": 20.0, "sea_hit": True},
        {"bearing_deg": 90.0, "sample_distance_km": 16.0, "effective_distance_km": 30.0, "sea_hit": False},
    ]

    summary = _summarize_coast(52.0, 4.0, rows)

    assert summary["status"] == "ok"
    assert summary["coastal"] is True
    assert 7.0 <= summary["nearest_open_water_km"] <= 12.0
    assert 220.0 <= summary["dominant_sea_bearing_deg"] <= 285.0
    assert summary["exposure_score"] > 0.3


def test_summarize_terrain_detects_channel_axis():
    center = 20.0
    rows = []
    for bearing in [0.0, 22.5, 45.0, 202.5, 225.0]:
        rows.append({"bearing_deg": bearing, "radius_km": 3.0, "elevation_m": 30.0})
        rows.append({"bearing_deg": bearing, "radius_km": 6.0, "elevation_m": 35.0})
    for bearing in [112.5, 135.0, 157.5, 292.5, 315.0, 337.5]:
        rows.append({"bearing_deg": bearing, "radius_km": 3.0, "elevation_m": 180.0})
        rows.append({"bearing_deg": bearing, "radius_km": 6.0, "elevation_m": 220.0})

    summary = _summarize_terrain(center, rows)

    assert summary["status"] == "ok"
    assert summary["relief_m"] >= 180.0
    assert summary["terrain_axis_deg"] in {45.0, 67.5, 225.0, 247.5}
    assert summary["channel_strength"] > 0.2
    assert summary["topo_potential"] > 0.08
