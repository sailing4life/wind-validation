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


def circular_delta(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def calibrate(samples: list[CalibrationSample], target_u: float, target_v: float, now: datetime, lead_hours: float) -> dict:
    """Recent, regime-weighted U/V correction with an honest effective sample size."""
    speed, direction = uv_to_speed_dir(target_u, target_v)
    weighted: list[tuple[float, float, float]] = []
    for row in samples:
        ms, md = uv_to_speed_dir(row.model_u, row.model_v)
        age_h = max(0.0, (now - row.time_utc).total_seconds() / 3600.0)
        # Six-hour recency half-life; retain analogous older cases gently.
        w_age = 0.5 ** (age_h / 6.0)
        w_dir = math.exp(-0.5 * (circular_delta(direction, md) / 45.0) ** 2)
        w_speed = math.exp(-0.5 * ((speed - ms) / 4.0) ** 2)
        w = w_age * w_dir * w_speed
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
        "ws_p10_ms": p10, "ws_p90_ms": p90,
        "wd_p10_deg": (cd - dir_half) % 360, "wd_p90_deg": (cd + dir_half) % 360,
    }
