from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class ModelDefinition:
    model_id: str
    provider: str
    category: str
    coverage_bbox: dict[str, float]
    priority_by_country: dict[str, int]
    is_global_baseline: bool = False
    status: str = "ACTIVE"


@dataclass(slots=True)
class Station:
    station_id: str
    source: str
    country: str
    lat: float
    lon: float
    elevation_m: float | None = None
    external_id: str | None = None
    station_type: str = "synop"
    active: bool = True


@dataclass(slots=True)
class Observation:
    station_id: str
    source: str
    time_utc: datetime
    ws_ms: float
    wd_deg: float
    qc_passed: bool = True
    qc_flags: list[str] | None = None


@dataclass(slots=True)
class ForecastValue:
    model_id: str
    run_time_utc: datetime
    valid_time_utc: datetime
    lat: float
    lon: float
    u10: float
    v10: float
    gust_ms: float | None = None
    temp_c: float | None = None
    precip_mm: float | None = None


@dataclass(slots=True)
class ScoreRow:
    model_id: str
    provider: str
    n_samples: int
    vector_rmse_uv: float | None
    mae_ws: float | None
    rmse_ws: float | None
    bias_ws: float | None
    dir_err_deg: float | None
    status: str
    reasons: list[str]
