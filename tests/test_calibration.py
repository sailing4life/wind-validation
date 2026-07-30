from datetime import UTC, datetime, timedelta

from app.calibration import CalibrationSample, calibrate


def test_calibration_corrects_uv_and_returns_an_uncertainty_band():
    now = datetime(2026, 7, 30, 12, tzinfo=UTC)
    samples = [
        CalibrationSample(now - timedelta(hours=i), 0.0, -5.0, 1.0, -5.0)
        for i in range(10)
    ]

    result = calibrate(samples, 0.0, -5.0, now, lead_hours=1)

    assert result["status"] == "bootstrap"
    assert result["n_effective"] >= 5
    assert result["ws_ms"] > 5.0
    assert result["ws_p10_ms"] <= result["ws_ms"] <= result["ws_p90_ms"]


def test_hour_conditioning_rejects_an_opposite_daily_phase():
    now = datetime(2026, 7, 30, 12, tzinfo=UTC)
    samples = [
        CalibrationSample(now - timedelta(days=i), 0.0, -5.0, 1.0, -5.0, local_solar_hour=12.0)
        for i in range(12)
    ] + [
        CalibrationSample(now - timedelta(days=i), 0.0, -5.0, 8.0, -5.0, local_solar_hour=0.0)
        for i in range(12)
    ]

    result = calibrate(
        samples, 0.0, -5.0, now, lead_hours=1,
        recency_half_life_hours=None, target_local_solar_hour=12.0, hour_sigma_hours=1.5,
    )

    # Midnight errors must not contaminate a noon sea-breeze estimate.
    assert result["n_effective"] >= 5
    assert 5.05 < result["ws_ms"] < 5.25
