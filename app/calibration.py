from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from .scoring import uv_to_speed_dir


@dataclass(slots=True)
class CalibrationSample:
    time_utc: datetime
    model_u: float
    model_v: float
    obs_u: float
    obs_v: float
    # Spatial/source relevance is supplied by ValidationService.  Keeping it
    # on the sample lets the same calibration code work for recent and stored
    # verification pairs.
    relevance_weight: float = 1.0
    local_solar_hour: float | None = None
    lead_hours: float | None = None


def circular_delta(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _weighted_median(pairs: list[tuple[float, float]]) -> float:
    """Weighted median of (weight, value) pairs."""
    if not pairs:
        return 0.0
    ordered = sorted(pairs, key=lambda p: p[1])
    half = sum(w for w, _ in ordered) / 2.0
    acc = 0.0
    for w, x in ordered:
        acc += w
        if acc >= half:
            return x
    return ordered[-1][1]


def _robust_sigma(values: list[tuple[float, float]], total: float) -> float:
    """Outlier-resistant scale for the (weight, residual) pairs.

    A MAD prescale sets a clip level, then a single winsorized weighted RMS is
    taken. One bad verification pair can no longer inflate the band, while a
    genuine two-sided spread (e.g. sea-breeze timing) survives the ±3σ clip.
    """
    if not values:
        return 0.0
    med = _weighted_median(values)
    mad = _weighted_median([(w, abs(x - med)) for w, x in values])
    s0 = 1.4826 * mad
    if s0 <= 1e-6:
        # MAD degenerates when most residuals are identical (a tight bulk plus
        # a few outliers). Mean absolute deviation stays non-zero and still
        # bounds a lone outlier's leverage, so the clip level remains sane.
        mnad = sum(w * abs(x - med) for w, x in values) / total
        s0 = 1.2533 * mnad
    if s0 <= 1e-6:
        return 0.0  # truly constant residuals
    clip = 3.0 * s0
    return math.sqrt(sum(w * min(max(x, -clip), clip) ** 2 for w, x in values) / total)


def calibrate(
    samples: list[CalibrationSample],
    target_u: float,
    target_v: float,
    now: datetime,
    lead_hours: float,
    *,
    recency_half_life_hours: float | None = 6.0,
    target_local_solar_hour: float | None = None,
    hour_sigma_hours: float | None = None,
    target_lead_hours: float | None = None,
) -> dict:
    """Regime-weighted U/V correction with optional local-hour conditioning."""
    speed, direction = uv_to_speed_dir(target_u, target_v)
    # Errors grow with forecast lead, so prefer verification pairs whose lead
    # matches the target hour. The tolerance widens with lead: at +60h a +40h
    # pair is fine evidence, at +6h it is not.
    lead_sigma = None if target_lead_hours is None else 12.0 + 0.5 * max(0.0, target_lead_hours)
    weighted: list[tuple[float, float, float]] = []
    for row in samples:
        ms, md = uv_to_speed_dir(row.model_u, row.model_v)
        age_h = max(0.0, (now - row.time_utc).total_seconds() / 3600.0)
        # Recent pairs help the live bias. Durable history uses a much longer
        # half-life, because it is primarily evidence for the uncertainty band.
        w_age = 1.0 if recency_half_life_hours is None else 0.5 ** (age_h / recency_half_life_hours)
        w_dir = math.exp(-0.5 * (circular_delta(direction, md) / 45.0) ** 2)
        w_speed = math.exp(-0.5 * ((speed - ms) / 4.0) ** 2)
        w_hour = 1.0
        if target_local_solar_hour is not None and row.local_solar_hour is not None and hour_sigma_hours:
            hour_delta = abs((target_local_solar_hour - row.local_solar_hour + 12.0) % 24.0 - 12.0)
            w_hour = math.exp(-0.5 * (hour_delta / hour_sigma_hours) ** 2)
        w_lead = 1.0
        if lead_sigma is not None and row.lead_hours is not None:
            w_lead = math.exp(-0.5 * ((target_lead_hours - row.lead_hours) / lead_sigma) ** 2)
        w = row.relevance_weight * w_age * w_dir * w_speed * w_hour * w_lead
        if w > 0.002:
            weighted.append((w, row.obs_u - row.model_u, row.obs_v - row.model_v))
    if not weighted:
        return {"status": "insufficient_history", "n_effective": 0.0}

    total = sum(w for w, _, _ in weighted)
    n_eff = total * total / sum(w * w for w, _, _ in weighted)
    if n_eff < 5:
        return {"status": "insufficient_history", "n_effective": round(n_eff, 1)}

    bu = sum(w * du for w, du, _ in weighted) / total
    bv = sum(w * dv for w, _, dv in weighted) / total
    # Local updates should fade into the NWP forecast at longer lead times.
    attenuation = math.exp(-max(0.0, lead_hours) / 10.0)
    cu, cv = target_u + attenuation * bu, target_v + attenuation * bv
    cs, cd = uv_to_speed_dir(cu, cv)

    # Residual scatter projected onto along/cross-wind axes gives practical p10–p90 bands.
    along, cross = [], []
    theta = math.radians(cd)
    for w, du, dv in weighted:
        ru, rv = du - bu, dv - bv
        along.append((w, -ru * math.sin(theta) - rv * math.cos(theta)))
        cross.append((w, -ru * math.cos(theta) + rv * math.sin(theta)))
    sig_s = _robust_sigma(along, total)
    sig_c = _robust_sigma(cross, total)
    # Shrink toward a modest prior so a small, noisy sample cannot claim an
    # extreme band (nor an implausibly tight one). With more effective samples
    # the observed scatter dominates. The prior is a typical 10 m NWP wind RMSE.
    n0 = 8.0
    prior_s = max(1.5, 0.12 * cs)
    prior_c = max(1.0, 0.10 * cs)
    sig_s = math.sqrt((n_eff * sig_s ** 2 + n0 * prior_s ** 2) / (n_eff + n0))
    sig_c = math.sqrt((n_eff * sig_c ** 2 + n0 * prior_c ** 2) / (n_eff + n0))
    return {
        "status": "bootstrap" if n_eff < 15 else "calibrated",
        "n_effective": round(n_eff, 1), "bias_u": bu, "bias_v": bv,
        "attenuation": attenuation, "ws_ms": cs, "wd_deg": cd,
        "sigma_along_ms": sig_s, "sigma_cross_ms": sig_c,
        **band_from_sigma(cs, cd, sig_s, sig_c),
    }


def band_from_sigma(ws_ms: float, wd_deg: float, sigma_along_ms: float, sigma_cross_ms: float) -> dict:
    """p10-p90 speed and direction band from along/cross-wind residual scales."""
    z90 = 1.2816
    dir_half = min(90.0, math.degrees(math.atan2(z90 * sigma_cross_ms, max(ws_ms, 0.5))))
    return {
        "ws_p10_ms": max(0.0, ws_ms - z90 * sigma_along_ms),
        "ws_p90_ms": ws_ms + z90 * sigma_along_ms,
        "wd_p10_deg": (wd_deg - dir_half) % 360.0,
        "wd_p90_deg": (wd_deg + dir_half) % 360.0,
    }


def scale_gust(
    gust_ms: float | None,
    raw_ws_ms: float,
    corrected_ws_ms: float,
    ws_p90_ms: float | None = None,
) -> float | None:
    """Keep the model's gust factor over the calibrated wind, and never report
    a gust below the p90 mean wind: if the mean reaches p90, gusts exceed it."""
    if gust_ms is None:
        return None
    if raw_ws_ms > 1.0:
        scaled = gust_ms * min(2.0, max(0.5, corrected_ws_ms / raw_ws_ms))
    else:
        # The gust factor is meaningless in near-calm; shift additively instead.
        scaled = gust_ms + (corrected_ws_ms - raw_ws_ms)
    scaled = max(scaled, corrected_ws_ms)
    if ws_p90_ms is not None:
        scaled = max(scaled, ws_p90_ms)
    return scaled


def blend_hour(members: list[dict]) -> dict | None:
    """Combine one forecast hour across models into a consensus.

    Each member: {"weight", "u", "v", optional "sigma_along_ms",
    "sigma_cross_ms", "n_effective"}. Centres are blended in U/V space, where
    partially uncorrelated model errors cancel. Sigmas are averaged rather than
    variance-reduced: model errors are correlated, so treating members as
    independent evidence would make the band dishonestly narrow.
    """
    total = sum(m["weight"] for m in members)
    if total <= 0:
        return None
    u = sum(m["weight"] * m["u"] for m in members) / total
    v = sum(m["weight"] * m["v"] for m in members) / total
    ws, wd = uv_to_speed_dir(u, v)
    out = {"u": u, "v": v, "ws_ms": ws, "wd_deg": wd}

    with_sigma = [m for m in members if m.get("sigma_along_ms") is not None]
    if with_sigma:
        sig_total = sum(m["weight"] for m in with_sigma)
        sig_s = sum(m["weight"] * m["sigma_along_ms"] for m in with_sigma) / sig_total
        sig_c = sum(m["weight"] * m["sigma_cross_ms"] for m in with_sigma) / sig_total
        n_eff = sum(m["weight"] * m.get("n_effective", 0.0) for m in with_sigma) / sig_total
        out.update({
            "sigma_along_ms": sig_s, "sigma_cross_ms": sig_c, "n_effective": round(n_eff, 1),
            **band_from_sigma(ws, wd, sig_s, sig_c),
        })
    return out
