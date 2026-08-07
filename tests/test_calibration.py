from datetime import UTC, datetime, timedelta

from app.calibration import CalibrationSample, band_from_sigma, blend_hour, calibrate, scale_gust


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


def test_lead_matching_widens_the_band_at_long_lead():
    now = datetime(2026, 7, 30, 12, tzinfo=UTC)
    # Short-lead pairs verified tightly, long-lead pairs scattered widely.
    # Wind from the north (0, -5): the along-wind axis is the V residual.
    samples = [
        CalibrationSample(now - timedelta(days=i), 0.0, -5.0, 0.0, -5.0 + 0.5 * (-1) ** i, lead_hours=2.0)
        for i in range(12)
    ] + [
        CalibrationSample(now - timedelta(days=i), 0.0, -5.0, 0.0, -5.0 + 3.0 * (-1) ** i, lead_hours=40.0)
        for i in range(12)
    ]

    short = calibrate(samples, 0.0, -5.0, now, lead_hours=2, recency_half_life_hours=None, target_lead_hours=2.0)
    long = calibrate(samples, 0.0, -5.0, now, lead_hours=40, recency_half_life_hours=None, target_lead_hours=40.0)

    # Lead matching must keep the long-lead band clearly wider than short lead.
    assert long["sigma_along_ms"] > 1.8 * short["sigma_along_ms"]
    assert long["ws_p90_ms"] - long["ws_p10_ms"] > short["ws_p90_ms"] - short["ws_p10_ms"]


def test_a_single_outlier_does_not_blow_up_the_band():
    now = datetime(2026, 7, 30, 12, tzinfo=UTC)
    # 20 tight pairs and one gross outlier that a plain RMS would let dominate.
    samples = [
        CalibrationSample(now - timedelta(days=i), 0.0, -5.0, 0.0, -5.3, local_solar_hour=12.0)
        for i in range(20)
    ] + [
        CalibrationSample(now - timedelta(days=20), 0.0, -5.0, 0.0, 15.0, local_solar_hour=12.0)
    ]

    result = calibrate(samples, 0.0, -5.0, now, lead_hours=1, recency_half_life_hours=None)

    # The outlier's 20 m/s residual would push a plain-RMS band past ~5 m/s;
    # the robust scale keeps it modest.
    assert result["sigma_along_ms"] < 2.5


def test_shrinkage_widens_a_tiny_sample_toward_the_prior():
    now = datetime(2026, 7, 30, 12, tzinfo=UTC)
    # Six near-identical pairs: raw scatter is ~0, but six samples cannot
    # justify a near-zero band, so the prior must widen it.
    samples = [
        CalibrationSample(now - timedelta(days=i), 0.0, -5.0, 0.0, -5.01, local_solar_hour=12.0)
        for i in range(6)
    ]

    result = calibrate(samples, 0.0, -5.0, now, lead_hours=1, recency_half_life_hours=None)

    assert result["sigma_along_ms"] > 0.8


def test_blend_hour_averages_centres_and_keeps_the_band_honest():
    members = [
        {"weight": 0.5, "u": 3.0, "v": 0.0, "sigma_along_ms": 1.0, "sigma_cross_ms": 0.5, "n_effective": 20.0},
        {"weight": 0.5, "u": 5.0, "v": 0.0},  # uncalibrated member: centre only
    ]

    out = blend_hour(members)

    assert abs(out["ws_ms"] - 4.0) < 1e-9
    # Band comes from the calibrated member alone; sigmas are averaged, never
    # variance-reduced as if the models were independent.
    assert abs(out["sigma_along_ms"] - 1.0) < 1e-9
    assert abs((out["ws_p90_ms"] - out["ws_ms"]) - 1.2816) < 1e-6
    assert blend_hour([]) is None


def test_band_from_sigma_brackets_the_mean():
    b = band_from_sigma(10.0, 90.0, sigma_along_ms=2.0, sigma_cross_ms=1.0)
    assert b["ws_p10_ms"] < 10.0 < b["ws_p90_ms"]
    assert abs((b["ws_p90_ms"] - b["ws_p10_ms"]) - 2 * 1.2816 * 2.0) < 1e-6


def test_gust_scales_with_the_correction_without_using_the_band():
    # Correction lifts 5 → 8 m/s: the 1.4 gust factor rides along (7 → 11.2).
    assert abs(scale_gust(7.0, 5.0, 8.0) - 11.2) < 1e-9
    # A wide p90 band is uncertainty in mean wind, not a gust forecast.
    assert scale_gust(7.0, 5.0, 5.0) == 7.0
    # Near-calm: the ratio is meaningless, shift additively instead.
    assert scale_gust(3.0, 0.5, 2.5) == 5.0
    assert scale_gust(None, 5.0, 8.0) is None
