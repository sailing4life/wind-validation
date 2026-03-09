from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(slots=True)
class MetricsResult:
    n_samples: int
    vector_rmse_uv: float
    mae_ws: float
    rmse_ws: float
    bias_ws: float
    dir_err_deg: float


def speed_dir_to_uv(ws_ms: float, wd_deg: float) -> tuple[float, float]:
    theta = math.radians(wd_deg)
    u = -ws_ms * math.sin(theta)
    v = -ws_ms * math.cos(theta)
    return u, v


def uv_to_speed_dir(u: float, v: float) -> tuple[float, float]:
    speed = math.sqrt(u * u + v * v)
    direction = (math.degrees(math.atan2(-u, -v)) + 360.0) % 360.0
    return speed, direction


def circular_direction_error_deg(obs_deg: float, fc_deg: float) -> float:
    diff = abs(obs_deg - fc_deg) % 360.0
    return min(diff, 360.0 - diff)


def compute_metrics(obs_uv: list[tuple[float, float]], fc_uv: list[tuple[float, float]]) -> MetricsResult:
    if not obs_uv or len(obs_uv) != len(fc_uv):
        raise ValueError("obs_uv and fc_uv must have equal non-zero length")

    n = len(obs_uv)
    vec_sq = []
    speed_abs = []
    speed_sq = []
    speed_bias = []
    dir_err = []

    for (ou, ov), (fu, fv) in zip(obs_uv, fc_uv):
        du = fu - ou
        dv = fv - ov
        vec_sq.append(du * du + dv * dv)

        ows, owd = uv_to_speed_dir(ou, ov)
        fws, fwd = uv_to_speed_dir(fu, fv)

        diff_ws = fws - ows
        speed_abs.append(abs(diff_ws))
        speed_sq.append(diff_ws * diff_ws)
        speed_bias.append(diff_ws)
        dir_err.append(circular_direction_error_deg(owd, fwd))

    return MetricsResult(
        n_samples=n,
        vector_rmse_uv=math.sqrt(sum(vec_sq) / n),
        mae_ws=sum(speed_abs) / n,
        rmse_ws=math.sqrt(sum(speed_sq) / n),
        bias_ws=sum(speed_bias) / n,
        dir_err_deg=sum(dir_err) / n,
    )