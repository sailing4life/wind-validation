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
