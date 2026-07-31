from datetime import UTC, datetime, timedelta

from app.domain import ForecastValue
from app.services import ValidationService

NOW = datetime(2026, 7, 31, 12, tzinfo=UTC)
Z90 = 1.2816


def _member_hours(local_sigma: float):
    cal = {
        "status": "calibrated", "ws_ms": 5.0, "wd_deg": 0.0, "n_effective": 25.0,
        "sigma_along_ms": local_sigma, "sigma_cross_ms": local_sigma / 2, "historical_n_effective": 25.0,
    }
    hours = {}
    for h in (2, 4, 6, 8, 12, 18, 24):
        t = NOW + timedelta(hours=h)
        fv = ForecastValue("a", NOW, t, 39.5, 2.7, 0.0, -5.0, gust_ms=9.0)
        hours[t] = (fv, dict(cal))
    return {"a": hours}


def _eps(sigma_along):
    return {NOW + timedelta(hours=h): (sigma_along, sigma_along / 2) for h in (2, 4, 6, 8, 12, 18, 24)}


def test_band_is_the_ensemble_spread_deflated_to_local_skill():
    # Local error is half the raw ensemble spread → the band should be about
    # half the raw ensemble band, not the raw (over-dispersed) spread.
    blend, summary = ValidationService._build_blend(
        _member_hours(local_sigma=1.0), {"a": 1.0}, _eps(2.0), NOW, eps_factor_default=0.65,
    )
    assert summary["uncertainty_source"] == "eps_calibrated"
    assert abs(summary["eps_factor"] - 0.5) < 0.05
    h = blend["hours"][0]
    band = h["ws_p90_ms"] - h["ws_p10_ms"]
    raw_eps_band = 2 * Z90 * 2.0
    assert band < 0.6 * raw_eps_band  # clearly tighter than the raw ensemble


def test_band_grows_with_lead_following_the_ensemble():
    eps = {NOW + timedelta(hours=h): (1.0 + 0.15 * h, 0.5) for h in (2, 4, 6, 8, 12, 18, 24)}
    blend, _ = ValidationService._build_blend(
        _member_hours(local_sigma=1.0), {"a": 1.0}, eps, NOW, eps_factor_default=0.65,
    )
    widths = [h["ws_p90_ms"] - h["ws_p10_ms"] for h in blend["hours"] if h.get("ws_p10_ms") is not None]
    assert widths == sorted(widths)
    assert widths[-1] > widths[0]


def test_falls_back_to_local_band_without_an_ensemble():
    blend, summary = ValidationService._build_blend(
        _member_hours(local_sigma=1.0), {"a": 1.0}, {}, NOW, eps_factor_default=0.65,
    )
    assert summary["uncertainty_source"] == "blend"
    assert all(h.get("ws_p10_ms") is not None for h in blend["hours"])
