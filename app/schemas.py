from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class ValidatePointRequest(BaseModel):
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    hours_back: int = Field(default=48, ge=1, le=168)
    radius_km: float = Field(default=50.0, gt=0.0, le=150.0)


class StationDTO(BaseModel):
    station_id: str
    source: str
    country: str
    lat: float
    lon: float
    elevation_m: float | None = None
    included: bool = True


class ObservationPointDTO(BaseModel):
    station_id: str
    source: str
    lat: float
    lon: float
    time_utc: datetime
    ws_ms: float
    wd_deg: float


class GribPointDTO(BaseModel):
    model_id: str
    lat: float
    lon: float
    time_utc: datetime
    u10: float
    v10: float
    ws_ms: float
    wd_deg: float


class QueryPointForecastDTO(BaseModel):
    model_id: str
    lat: float
    lon: float
    time_utc: datetime
    ws_ms: float
    wd_deg: float


class TimeSeriesPointDTO(BaseModel):
    time_utc: datetime
    obs_ws_ms: float | None = None
    obs_wd_deg: float | None = None
    model_ws_ms: float | None = None
    model_wd_deg: float | None = None
    model_ws_ms_obs: float | None = None
    model_wd_deg_obs: float | None = None


class ModelTimeSeriesDTO(BaseModel):
    model_id: str
    points: list[TimeSeriesPointDTO]


class ModelMetricDTO(BaseModel):
    model_id: str
    provider: str
    n_samples: int
    vector_rmse_uv: float | None
    mae_ws: float | None
    rmse_ws: float | None
    bias_ws: float | None
    dir_err_deg: float | None
    status: str
    reasons: list[str] = Field(default_factory=list)
    run_time_utc: datetime | None = None


class ValidatePointResponse(BaseModel):
    query_id: str
    lat: float
    lon: float
    window_start_utc: datetime
    window_end_utc: datetime
    radius_km: float
    models: list[ModelMetricDTO]
    winner_model_id: str | None = None
    stations_used: list[StationDTO]
    observation_points: list[ObservationPointDTO]
    grib_points: list[GribPointDTO]
    query_point_forecast: QueryPointForecastDTO | None = None
    time_series: list[ModelTimeSeriesDTO]
    source_provenance: list[str]
    computed_at_utc: datetime


class CoverageModelDTO(BaseModel):
    model_id: str
    provider: str
    category: str
    priority: int
    coverage_bbox: dict[str, float]
    is_global_baseline: bool
    status: str


class FreshnessDTO(BaseModel):
    sources: dict[str, datetime | None]
    models: dict[str, datetime | None]
