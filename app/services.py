from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .cache import TTLCache
from .catalog import select_candidate_models
from .config import Settings
from .domain import ForecastValue, Observation, ScoreRow
from .forecast_adapters import OpenMeteoForecastAdapter
from .openwrf_adapter import OpenWrfAdapter as _OpenWrfAdapter, MODEL_ID as _OPENWRF_ID
from .geo import haversine_km
from .location_fingerprint import LocationFingerprintService
from .observation_broker import ObservationBroker
from .repositories import InMemoryRepository
from .scoring import compute_metrics, speed_dir_to_uv, uv_to_speed_dir

logger = logging.getLogger("wind_validation")

_ALADIN_CZ_ID = "aladin_cz"


def _fetch_aladin_cz_at_coords(
    coords: list[tuple[float, float]],
    start: datetime,
    end: datetime,
) -> list[ForecastValue]:
    """Fetch ČHMÚ ALADIN-CZ GRIB and extract ForecastValues at the given coordinates.

    Downloads up to two runs — the newest available plus the oldest that still
    covers *start* — so the full [start, end] window is populated even when
    start is 48 h in the past.  Newer-run data wins when both runs have the
    same valid_time for a given coordinate.
    """
    import math as _math  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    try:
        from .windmap import _probe_aladin_runs, _download_aladin_run  # noqa: PLC0415
    except Exception as exc:
        logger.info("ALADIN-CZ import skipped: %s", exc)
        return []

    if not coords:
        return []

    lats_c = [c[0] for c in coords]
    lons_c = [c[1] for c in coords]
    lat_min = min(lats_c) - 0.5
    lat_max = max(lats_c) + 0.5
    lon_min = min(lons_c) - 0.5
    lon_max = max(lons_c) + 0.5

    # Probe far enough back to find runs that cover start (each run covers +48 h)
    now = datetime.now(timezone.utc)
    hours_back = max(50, int((now - start).total_seconds() / 3600) + 6)
    runs = _probe_aladin_runs(hours_back=hours_back)
    if not runs:
        logger.info("ALADIN-CZ: no runs found in last %d h", hours_back)
        return []

    # Select: newest run (covers recent / future hours) +
    #         oldest run whose forecast reaches back to start (run_time + 48 h >= start)
    runs_to_use: list[tuple] = [runs[0]]  # newest first
    for r in reversed(runs):
        run_dt = r[0]
        if run_dt + timedelta(hours=48) >= start and run_dt != runs[0][0]:
            runs_to_use.append(r)
            break

    # Merge: process oldest first so newer-run data overwrites for same key
    merged: dict[tuple[float, float, datetime], ForecastValue] = {}
    for run_dt, speed_url, dir_url in reversed(runs_to_use):
        max_hours = int((end - run_dt).total_seconds() / 3600) + 2
        try:
            lats_g, lons_g, u_arr, v_arr, gust_arr, _, times_iso = _download_aladin_run(
                run_dt, speed_url, dir_url,
                lat_min, lat_max, lon_min, lon_max, max_hours,
            )
        except Exception as exc:
            logger.info("ALADIN-CZ run %s skipped: %s", run_dt, exc)
            continue

        grid_times: list[datetime | None] = []
        for t_iso in times_iso:
            try:
                grid_times.append(datetime.fromisoformat(t_iso.replace("Z", "+00:00")))
            except Exception:
                grid_times.append(None)

        for lat, lon in coords:
            iy = int(np.argmin(np.abs(lats_g - lat)))
            ix = int(np.argmin(np.abs(lons_g - lon)))
            for i, gt in enumerate(grid_times):
                if gt is None or gt < start or gt > end:
                    continue
                if i >= u_arr.shape[0]:
                    continue
                u = float(u_arr[i, iy, ix])
                v = float(v_arr[i, iy, ix])
                if not (_math.isfinite(u) and _math.isfinite(v)):
                    continue
                gust_ms: float | None = None
                if gust_arr is not None and i < gust_arr.shape[0]:
                    g = float(gust_arr[i, iy, ix])
                    gust_ms = g if _math.isfinite(g) else None
                merged[(float(lats_g[iy]), float(lons_g[ix]), gt)] = ForecastValue(
                    model_id=_ALADIN_CZ_ID,
                    run_time_utc=run_dt,
                    valid_time_utc=gt,
                    lat=float(lats_g[iy]),
                    lon=float(lons_g[ix]),
                    u10=u,
                    v10=v,
                    gust_ms=gust_ms,
                )

    return list(merged.values())


class ValidationService:
    def __init__(
        self,
        repo: InMemoryRepository,
        broker: ObservationBroker,
        forecast_adapter: OpenMeteoForecastAdapter,
        settings: Settings,
        fingerprint_service: LocationFingerprintService | None = None,
    ) -> None:
        self.repo = repo
        self.broker = broker
        self.forecast_adapter = forecast_adapter
        self.openwrf = _OpenWrfAdapter()
        self.settings = settings
        self.fingerprint_service = fingerprint_service
        self.cache: TTLCache[dict] = TTLCache(ttl_seconds=settings.cache_ttl_seconds)

    def _cache_key(
        self,
        lat: float,
        lon: float,
        radius_km: float,
        window_end: datetime,
        hours_back: int,
    ) -> str:
        return (
            f"{round(lat, 2)}:{round(lon, 2)}:{round(radius_km, 1)}:"
            f"{window_end.isoformat()}:{hours_back}"
        )

    def _availability_ratio(self, model_id: str, start: datetime, end: datetime) -> float:
        expected = int((end - start).total_seconds() // 3600) + 1
        if expected <= 0:
            return 0.0
        found_times = {row.valid_time_utc for row in self.repo.forecasts_for_model_window(model_id, start, end)}
        return min(1.0, len(found_times) / expected)

    @staticmethod
    def _circular_mean(angles: list[float]) -> float | None:
        if not angles:
            return None
        s = sum(math.sin(math.radians(a)) for a in angles)
        c = sum(math.cos(math.radians(a)) for a in angles)
        return math.degrees(math.atan2(s, c)) % 360

    def _query_point_forecast(self, model_id: str | None, lat: float, lon: float, valid_time: datetime) -> dict | None:
        if not model_id:
            return None
        fc = self.repo.nearest_forecast(model_id, lat, lon, valid_time)
        if fc is None:
            return None
        ws, wd = uv_to_speed_dir(fc.u10, fc.v10)
        return {"model_id": model_id, "lat": lat, "lon": lon, "time_utc": valid_time, "ws_ms": ws, "wd_deg": wd}

    @staticmethod
    def _build_query_point_forecast(
        model_id: str | None,
        lat: float,
        lon: float,
        valid_time: datetime,
        nearest_fc,
    ) -> dict | None:
        if not model_id:
            return None
        fc = nearest_fc(model_id, lat, lon, valid_time)
        if fc is None:
            return None
        ws, wd = uv_to_speed_dir(fc.u10, fc.v10)
        return {"model_id": model_id, "lat": lat, "lon": lon, "time_utc": valid_time, "ws_ms": ws, "wd_deg": wd}

    def _latest_obs_by_station(self, observations: list[Observation]) -> dict[str, Observation]:
        latest: dict[str, Observation] = {}
        for row in observations:
            prev = latest.get(row.station_id)
            if prev is None or row.time_utc > prev.time_utc:
                latest[row.station_id] = row
        return latest

    def _build_time_axis(self, start: datetime, end: datetime) -> list[datetime]:
        axis: list[datetime] = []
        t = start
        while t <= end:
            axis.append(t)
            t += timedelta(hours=1)
        return axis

    def validate_point(self, lat: float, lon: float, hours_back: int, radius_km: float) -> dict:
        now_hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        window_end = now_hour
        window_start = now_hour - timedelta(hours=hours_back)
        cache_key = self._cache_key(lat, lon, radius_km, window_end, hours_back)

        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        query_id = str(uuid4())
        country = self.repo.point_country(lat, lon)
        stations = self.broker.list_stations(country, lat, lon, radius_km)
        station_ids = {s.station_id for s in stations}
        observations, provenance = self.broker.get_observations(country, station_ids, window_start, window_end)

        candidates, excluded_reasons = select_candidate_models(
            lat=lat,
            lon=lon,
            catalog=self.repo.models,
            coverage_availability={},
            missing_threshold=1.0,
        )

        forecast_end = window_end

        # Fast forecast index: (model_id, valid_time) → list[ForecastValue]
        fc_index: dict[tuple[str, datetime], list] = defaultdict(list)
        for fv in self.repo.forecasts:
            fc_index[(fv.model_id, fv.valid_time_utc)].append(fv)

        # On-demand fetch: augment index with exact pin + obs station coordinates
        all_coords = list({(lat, lon)} | {(s.lat, s.lon) for s in stations})

        def _fetch_model(model):
            if model.model_id == _OPENWRF_ID:
                return self.openwrf.fetch_model_at_coords(model, all_coords, window_start, forecast_end)
            if model.model_id == _ALADIN_CZ_ID:
                return _fetch_aladin_cz_at_coords(all_coords, window_start, forecast_end)
            return self.forecast_adapter.fetch_model_at_coords(model, all_coords, window_start, forecast_end)

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(_fetch_model, m): m for m in candidates}
            for future in as_completed(futures):
                model = futures[future]
                try:
                    for fv in future.result():
                        fc_index[(fv.model_id, fv.valid_time_utc)].append(fv)
                except Exception as exc:
                    logger.warning("On-demand batch fetch failed for %s", model.model_id, exc_info=exc)

        # Track latest run time per model (for display)
        latest_run: dict[str, datetime] = {}
        for (mid, _), fvs in fc_index.items():
            for fv in fvs:
                if fv.run_time_utc and (mid not in latest_run or fv.run_time_utc > latest_run[mid]):
                    latest_run[mid] = fv.run_time_utc

        def nearest_fc(model_id: str, slat: float, slon: float, t: datetime):
            cands = fc_index.get((model_id, t), [])
            if not cands:
                return None
            return min(cands, key=lambda r: haversine_km(slat, slon, r.lat, r.lon))

        rows: list[ScoreRow] = []
        for model in candidates:
            obs_uv: list[tuple[float, float]] = []
            fc_uv: list[tuple[float, float]] = []
            for obs in observations:
                station = next((s for s in stations if s.station_id == obs.station_id), None)
                if station is None:
                    continue
                if haversine_km(lat, lon, station.lat, station.lon) > radius_km:
                    continue

                rounded_time = (obs.time_utc + timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0)
                nearest = nearest_fc(model.model_id, station.lat, station.lon, rounded_time)
                if nearest is None:
                    continue

                obs_uv.append(speed_dir_to_uv(obs.ws_ms, obs.wd_deg))
                fc_uv.append((nearest.u10, nearest.v10))

            if len(obs_uv) < self.settings.min_samples:
                rows.append(
                    ScoreRow(
                        model_id=model.model_id,
                        provider=model.provider,
                        n_samples=len(obs_uv),
                        vector_rmse_uv=None,
                        mae_ws=None,
                        rmse_ws=None,
                        bias_ws=None,
                        dir_err_deg=None,
                        status="insufficient_data",
                        reasons=["below_min_samples"],
                    )
                )
                continue

            metrics = compute_metrics(obs_uv, fc_uv)
            rows.append(
                ScoreRow(
                    model_id=model.model_id,
                    provider=model.provider,
                    n_samples=metrics.n_samples,
                    vector_rmse_uv=metrics.vector_rmse_uv,
                    mae_ws=metrics.mae_ws,
                    rmse_ws=metrics.rmse_ws,
                    bias_ws=metrics.bias_ws,
                    dir_err_deg=metrics.dir_err_deg,
                    status="ok",
                    reasons=[],
                )
            )

        for model_id, reason in excluded_reasons.items():
            m = next((x for x in self.repo.models if x.model_id == model_id), None)
            if m is None:
                continue
            rows.append(
                ScoreRow(
                    model_id=model_id,
                    provider=m.provider,
                    n_samples=0,
                    vector_rmse_uv=None,
                    mae_ws=None,
                    rmse_ws=None,
                    bias_ws=None,
                    dir_err_deg=None,
                    status="excluded",
                    reasons=[reason],
                )
            )

        ranked_ok = [r for r in rows if r.status == "ok" and r.vector_rmse_uv is not None]
        ranked_ok.sort(key=lambda r: (r.vector_rmse_uv, r.rmse_ws if r.rmse_ws is not None else 999, -r.n_samples))
        winner = ranked_ok[0].model_id if ranked_ok else None
        rows.sort(key=lambda r: (0 if r.status == "ok" else 1, r.vector_rmse_uv or 9999))

        latest_obs = self._latest_obs_by_station(observations)
        observation_points = []
        for station in stations:
            obs = latest_obs.get(station.station_id)
            if obs is None:
                continue
            observation_points.append(
                {
                    "station_id": station.station_id,
                    "source": obs.source,
                    "lat": station.lat,
                    "lon": station.lon,
                    "time_utc": obs.time_utc,
                    "ws_ms": obs.ws_ms,
                    "wd_deg": obs.wd_deg,
                }
            )

        grib_points = []
        candidate_ids = {m.model_id for m in candidates}
        for station in stations:
            for model_id in candidate_ids:
                fc = nearest_fc(model_id, station.lat, station.lon, window_end)
                if fc is None:
                    continue
                ws, wd = uv_to_speed_dir(fc.u10, fc.v10)
                grib_points.append(
                    {
                        "model_id": model_id,
                        "lat": station.lat,
                        "lon": station.lon,
                        "time_utc": fc.valid_time_utc,
                        "u10": fc.u10,
                        "v10": fc.v10,
                        "ws_ms": ws,
                        "wd_deg": wd,
                    }
                )

        obs_ws_by_hour: dict[datetime, list[float]] = defaultdict(list)
        obs_wd_by_hour: dict[datetime, list[float]] = defaultdict(list)
        for obs in observations:
            hour = obs.time_utc.replace(minute=0, second=0, microsecond=0)
            obs_ws_by_hour[hour].append(obs.ws_ms)
            obs_wd_by_hour[hour].append(obs.wd_deg)

        axis = self._build_time_axis(window_start, window_end)
        time_series = []
        for model in candidates:
            points = []
            for hour in axis:
                obs_ws_vals = obs_ws_by_hour.get(hour, [])
                obs_wd_vals = obs_wd_by_hour.get(hour, [])
                obs_mean_ws = sum(obs_ws_vals) / len(obs_ws_vals) if obs_ws_vals else None
                obs_mean_wd = self._circular_mean(obs_wd_vals)

                # Model at pin location
                fc_pin = nearest_fc(model.model_id, lat, lon, hour)
                model_ws_pin = model_wd_pin = None
                if fc_pin is not None:
                    model_ws_pin, model_wd_pin = uv_to_speed_dir(fc_pin.u10, fc_pin.v10)

                # Model averaged at observation station locations
                st_ws, st_wd = [], []
                for station in stations:
                    fc_st = nearest_fc(model.model_id, station.lat, station.lon, hour)
                    if fc_st is not None:
                        ws_s, wd_s = uv_to_speed_dir(fc_st.u10, fc_st.v10)
                        st_ws.append(ws_s)
                        st_wd.append(wd_s)
                model_ws_obs = sum(st_ws) / len(st_ws) if st_ws else None
                model_wd_obs = self._circular_mean(st_wd)

                points.append({
                    "time_utc":        hour,
                    "obs_ws_ms":       obs_mean_ws,
                    "obs_wd_deg":      obs_mean_wd,
                    "model_ws_ms":     model_ws_pin,
                    "model_wd_deg":    model_wd_pin,
                    "model_ws_ms_obs": model_ws_obs,
                    "model_wd_deg_obs": model_wd_obs,
                })
            time_series.append({"model_id": model.model_id, "points": points})

        result = {
            "query_id": query_id,
            "lat": lat,
            "lon": lon,
            "window_start_utc": window_start,
            "window_end_utc": window_end,
            "radius_km": radius_km,
            "models": [
                {
                    "model_id": row.model_id,
                    "provider": row.provider,
                    "n_samples": row.n_samples,
                    "vector_rmse_uv": row.vector_rmse_uv,
                    "mae_ws": row.mae_ws,
                    "rmse_ws": row.rmse_ws,
                    "bias_ws": row.bias_ws,
                    "dir_err_deg": row.dir_err_deg,
                    "status": row.status,
                    "reasons": row.reasons,
                    "run_time_utc": latest_run.get(row.model_id),
                }
                for row in rows
            ],
            "winner_model_id": winner,
            "stations_used": [
                {
                    "station_id": s.station_id,
                    "source": s.source,
                    "country": s.country,
                    "lat": s.lat,
                    "lon": s.lon,
                    "elevation_m": s.elevation_m,
                    "included": True,
                }
                for s in stations
            ],
            "observation_points": observation_points,
            "grib_points": grib_points,
            "query_point_forecast": self._build_query_point_forecast(winner, lat, lon, window_end, nearest_fc),
            "time_series": time_series,
            "source_provenance": provenance,
            "computed_at_utc": datetime.now(timezone.utc),
        }
        logger.info("validation_complete", extra={"query_id": query_id, "winner": winner, "stations": len(stations)})
        self.cache.set(cache_key, result)
        return result

    def forecast_point(
        self,
        lat: float,
        lon: float,
        winner_model_id: str,
        bias_ws_ms: float,
        hours_ahead: int,
    ) -> dict:
        from .scoring import uv_to_speed_dir

        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        end = now + timedelta(hours=hours_ahead)
        catalog = self.repo.models

        models_series = []
        for model in catalog:
            if model.status != "ACTIVE":
                continue
            try:
                if model.model_id == _OPENWRF_ID:
                    fvs = self.openwrf.fetch_forecast_with_extras(model, [(lat, lon)], now, end)
                elif model.model_id == _ALADIN_CZ_ID:
                    fvs = _fetch_aladin_cz_at_coords([(lat, lon)], now, end)
                else:
                    if models_series:  # sleep only after a real HTTP call was made
                        time.sleep(4.0)
                    fvs = self.forecast_adapter.fetch_forecast_with_extras(
                        model, [(lat, lon)], now, end
                    )
            except Exception as exc:
                logger.warning("forecast_point fetch failed for %s: %s", model.model_id, exc)
                fvs = []

            hours_list = []
            for fv in sorted(fvs, key=lambda x: x.valid_time_utc):
                ws, wd = uv_to_speed_dir(fv.u10, fv.v10)
                hours_list.append({
                    "time_utc": fv.valid_time_utc,
                    "ws_ms": ws,
                    "gust_ms": fv.gust_ms,
                    "wd_deg": wd,
                    "temp_c": fv.temp_c,
                    "precip_mm": fv.precip_mm,
                    "cloud_cover_pct": fv.cloud_cover_pct,
                    "pressure_msl_hpa": fv.pressure_msl_hpa,
                    "shortwave_wm2": fv.shortwave_wm2,
                    "cape_jkg": fv.cape_jkg,
                    "boundary_layer_height_m": fv.boundary_layer_height_m,
                })
            if hours_list:
                models_series.append({"model_id": model.model_id, "hours": hours_list})

        return {
            "winner_model_id": winner_model_id,
            "bias_ws_ms": bias_ws_ms,
            "hours_ahead": hours_ahead,
            "models": models_series,
            "location_fingerprint": self.fingerprint_service.fingerprint(lat, lon) if self.fingerprint_service else None,
        }


async def run_hourly_refresh(ingestion_service, refresh_seconds: int, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=refresh_seconds)
        except asyncio.TimeoutError:
            pass
        if not stop_event.is_set():
            await asyncio.to_thread(ingestion_service.refresh)
