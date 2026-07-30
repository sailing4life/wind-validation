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
    def sigma(values: list[tuple[float, float]]) -> float:
        return math.sqrt(sum(w * x * x for w, x in values) / total) if values else 0.0
    sig_s = sigma(along)
    sig_c = sigma(cross)
    z90 = 1.2816
    p10, p90 = max(0.0, cs - z90 * sig_s), cs + z90 * sig_s
    dir_half = min(90.0, math.degrees(math.atan2(z90 * sig_c, max(cs, 0.5))))
    return {
        "status": "bootstrap" if n_eff < 15 else "calibrated",
        "n_effective": round(n_eff, 1), "bias_u": bu, "bias_v": bv,
        "attenuation": attenuation, "ws_ms": cs, "wd_deg": cd,
        "sigma_along_ms": sig_s, "sigma_cross_ms": sig_c,
        "ws_p10_ms": p10, "ws_p90_ms": p90,
        "wd_p10_deg": (cd - dir_half) % 360, "wd_p90_deg": (cd + dir_half) % 360,
    }


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
        z90 = 1.2816
        dir_half = min(90.0, math.degrees(math.atan2(z90 * sig_c, max(ws, 0.5))))
        out.update({
            "sigma_along_ms": sig_s, "sigma_cross_ms": sig_c, "n_effective": round(n_eff, 1),
            "ws_p10_ms": max(0.0, ws - z90 * sig_s), "ws_p90_ms": ws + z90 * sig_s,
            "wd_p10_deg": (wd - dir_half) % 360, "wd_p90_deg": (wd + dir_half) % 360,
        })
    return out
