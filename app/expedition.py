from __future__ import annotations

import csv
import io
import logging
import math
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import numpy as np

from .config import Settings
from .domain import ForecastValue, ModelDefinition
from .forecast_adapters import OpenMeteoForecastAdapter
from .geo import haversine_km, in_bbox
from .scoring import compute_metrics, speed_dir_to_uv, uv_to_speed_dir

logger = logging.getLogger("wind_validation.expedition")

KNOTS_TO_MS = 0.514444

# Models that fetch wind from their own GRIB source (not Open-Meteo).
# These download the latest available run from the provider's open-data portal.
_GRIB_MODEL_IDS = {"aladin_cz", "openwrf"}


def parse_expedition_csv(data: bytes, interval_min: int) -> list[dict]:
    """Parse a sailing expedition .proc.csv and downsample to interval_min resolution.

    Expects columns: UtcDate, UtcTime, Lat, Lon, TWS (knots), TWD (degrees).
    Returns list of {time_utc, lat, lon, tws_ms, twd_deg}.
    """
    text = data.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    raw: list[dict] = []
    for row in reader:
        try:
            date_str = (row.get("UtcDate") or "").strip()
            time_str = (row.get("UtcTime") or "").strip()
            lat_str  = (row.get("Lat") or "").strip()
            lon_str  = (row.get("Lon") or "").strip()
            tws_str  = (row.get("TWS") or "").strip()
            twd_str  = (row.get("TWD") or "").strip()

            if not all([date_str, time_str, lat_str, lon_str, tws_str, twd_str]):
                continue

            # UtcTime can be "HH:MM:SS.xxx" — take first 8 chars for strptime
            dt = datetime.strptime(
                f"{date_str} {time_str[:8]}", "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=UTC)

            lat     = float(lat_str)
            lon     = float(lon_str)
            tws_kt  = float(tws_str)
            twd_deg = float(twd_str)

            if abs(lat) < 0.001 and abs(lon) < 0.001:
                continue  # no GPS fix
            if not (0 <= tws_kt <= 150):
                continue  # implausible wind speed

            raw.append({
                "time_utc": dt,
                "lat":      lat,
                "lon":      lon,
                "tws_ms":   tws_kt * KNOTS_TO_MS,
                "twd_deg":  twd_deg % 360,
            })
        except (ValueError, KeyError):
            continue

    if not raw:
        return []

    raw.sort(key=lambda x: x["time_utc"])

    # Bucket into interval_min bins; take the middle sample per bucket.
    interval_sec = interval_min * 60
    buckets: dict[int, list[dict]] = {}
    for s in raw:
        b = int(s["time_utc"].timestamp()) // interval_sec
        buckets.setdefault(b, []).append(s)

    return [buckets[b][len(buckets[b]) // 2] for b in sorted(buckets)]


def _fetch_grib_fc_values(
    model_id: str,
    samples: list[dict],
    start: datetime,
    end: datetime,
) -> list[ForecastValue]:
    """Download GRIB for *model_id* and extract ForecastValue objects at sample positions.

    Uses the same GRIB fetchers as the windmap tab (latest available run from the
    provider's open-data portal).  Only works for recent expeditions where the
    run still exists on the server; older data silently returns an empty list.
    """
    # lazy import — windmap has heavy dependencies (matplotlib, etc.)
    from .windmap import _fetch_aladin_cz  # noqa: PLC0415
    from .openwrf_adapter import OpenWrfAdapter  # noqa: PLC0415

    exp_lats = [s["lat"] for s in samples]
    exp_lons = [s["lon"] for s in samples]
    lat_min  = min(exp_lats) - 0.5
    lat_max  = max(exp_lats) + 0.5
    lon_min  = min(exp_lons) - 0.5
    lon_max  = max(exp_lons) + 0.5
    max_hours = int((end - start).total_seconds() // 3600) + 49

    if model_id == "openwrf":
        # OpenWrfAdapter already returns ForecastValue objects
        try:
            from .catalog import default_model_catalog  # noqa: PLC0415
            model_def = next((m for m in default_model_catalog() if m.model_id == "openwrf"), None)
            if model_def is None:
                return []
            coords = list({(round(s["lat"], 3), round(s["lon"], 3)) for s in samples})
            return OpenWrfAdapter().fetch_model_at_coords(model_def, coords, start, end)
        except Exception as exc:
            logger.info("OpenWRF GRIB fetch skipped: %s", exc)
            return []

    # aladin_cz — fetch spatial grid, then extract nearest points
    if model_id != "aladin_cz":
        return []

    try:
        lats_g, lons_g, u_arr, v_arr, _, times_iso = \
            _fetch_aladin_cz(lat_min, lat_max, lon_min, lon_max, max_hours)
    except Exception as exc:
        logger.info("ALADIN-CZ GRIB fetch skipped: %s", exc)
        return []

    # Parse ISO times from windmap output
    grid_times: list[datetime | None] = []
    for t_iso in times_iso:
        try:
            grid_times.append(datetime.fromisoformat(t_iso.replace("Z", "+00:00")))
        except Exception:
            grid_times.append(None)

    rows: list[ForecastValue] = []
    for s in samples:
        sample_hour = s["time_utc"].replace(minute=0, second=0, microsecond=0)

        # Find closest grid timestep (within 1.5 h)
        best_i = best_diff = None
        for i, gt in enumerate(grid_times):
            if gt is None:
                continue
            diff = abs((gt - sample_hour).total_seconds())
            if best_diff is None or diff < best_diff:
                best_i, best_diff = i, diff

        if best_i is None or best_diff > 5400:
            continue

        iy = int(np.argmin(np.abs(lats_g - s["lat"])))
        ix = int(np.argmin(np.abs(lons_g - s["lon"])))

        if best_i >= u_arr.shape[0]:
            continue

        u = float(u_arr[best_i, iy, ix])
        v = float(v_arr[best_i, iy, ix])

        if not (math.isfinite(u) and math.isfinite(v)):
            continue

        rows.append(ForecastValue(
            model_id=model_id,
            run_time_utc=grid_times[best_i],
            valid_time_utc=grid_times[best_i],
            lat=float(lats_g[iy]),
            lon=float(lons_g[ix]),
            u10=u,
            v10=v,
        ))

    return rows


def validate_expedition(
    samples: list[dict],
    catalog: list[ModelDefinition],
    forecast_adapter: OpenMeteoForecastAdapter,
    settings: Settings,
) -> dict:
    """Compare expedition log wind observations against NWP model forecasts.

    Returns a dict shaped like ValidatePointResponse so the frontend can reuse
    the same ranking table and chart components.
    """
    if not samples:
        raise ValueError("No valid samples")

    start = samples[0]["time_utc"]
    end   = samples[-1]["time_utc"]

    lats = [s["lat"] for s in samples]
    lons = [s["lon"] for s in samples]
    route_bbox = {
        "lat_min": min(lats) - 0.5,
        "lat_max": max(lats) + 0.5,
        "lon_min": min(lons) - 0.5,
        "lon_max": max(lons) + 0.5,
    }

    def _overlaps(model: ModelDefinition) -> bool:
        b = model.coverage_bbox
        return not (
            route_bbox["lat_max"] < b["min_lat"] or route_bbox["lat_min"] > b["max_lat"]
            or route_bbox["lon_max"] < b["min_lon"] or route_bbox["lon_min"] > b["max_lon"]
        )

    candidates = [
        m for m in catalog
        if m.status == "ACTIVE"
        and _overlaps(m)
        and (m.model_id in forecast_adapter.endpoint_map or m.model_id in _GRIB_MODEL_IDS)
    ]

    # One representative GPS position per hour for Open-Meteo batch fetching.
    hour_buckets: dict[datetime, list[dict]] = defaultdict(list)
    for s in samples:
        hour = s["time_utc"].replace(minute=0, second=0, microsecond=0)
        hour_buckets[hour].append(s)

    rep_coords: list[tuple[float, float]] = []
    seen: set[tuple[float, float]] = set()
    for hour in sorted(hour_buckets):
        mid   = hour_buckets[hour][len(hour_buckets[hour]) // 2]
        coord = (round(mid["lat"], 3), round(mid["lon"], 3))
        if coord not in seen:
            seen.add(coord)
            rep_coords.append(coord)

    if len(rep_coords) > 60:
        step = len(rep_coords) // 60 + 1
        rep_coords = rep_coords[::step]

    # Fetch forecast data — Open-Meteo for standard models, GRIB for special ones.
    fc_index: dict[tuple[str, datetime], list] = defaultdict(list)
    openmeteo_count = 0
    for model in candidates:
        if model.model_id in _GRIB_MODEL_IDS:
            try:
                fvs = _fetch_grib_fc_values(model.model_id, samples, start, end)
            except Exception as exc:
                logger.warning("GRIB fetch failed for %s: %s", model.model_id, exc)
                fvs = []
        else:
            if openmeteo_count > 0:
                time.sleep(3.0)  # respect Open-Meteo rate limit
            openmeteo_count += 1
            in_cov = [(lat, lon) for lat, lon in rep_coords if in_bbox(lat, lon, model.coverage_bbox)]
            if not in_cov:
                continue
            try:
                fvs = forecast_adapter.fetch_model_at_coords(model, in_cov, start, end)
            except Exception as exc:
                logger.warning("Expedition Open-Meteo fetch failed for %s: %s", model.model_id, exc)
                fvs = []

        for fv in fvs:
            fc_index[(fv.model_id, fv.valid_time_utc)].append(fv)

    def nearest_fc(model_id: str, slat: float, slon: float, t: datetime):
        hour = t.replace(minute=0, second=0, microsecond=0)
        for h in [hour, hour - timedelta(hours=1), hour + timedelta(hours=1)]:
            cands = fc_index.get((model_id, h), [])
            if cands:
                return min(cands, key=lambda r: haversine_km(slat, slon, r.lat, r.lon))
        return None

    model_results: list[dict] = []
    time_series: list[dict]   = []

    for model in candidates:
        obs_uv: list[tuple[float, float]] = []
        fc_uv:  list[tuple[float, float]] = []
        ts_pts: list[dict] = []

        for s in samples:
            fc = nearest_fc(model.model_id, s["lat"], s["lon"], s["time_utc"])
            if fc is None:
                ts_pts.append({
                    "time_utc":        s["time_utc"],
                    "obs_ws_ms":       s["tws_ms"],
                    "obs_wd_deg":      s["twd_deg"],
                    "model_ws_ms":     None,
                    "model_wd_deg":    None,
                    "model_ws_ms_obs": None,
                    "model_wd_deg_obs": None,
                })
                continue

            o_u, o_v = speed_dir_to_uv(s["tws_ms"], s["twd_deg"])
            obs_uv.append((o_u, o_v))
            fc_uv.append((fc.u10, fc.v10))

            fc_ws, fc_wd = uv_to_speed_dir(fc.u10, fc.v10)
            ts_pts.append({
                "time_utc":         s["time_utc"],
                "obs_ws_ms":        s["tws_ms"],
                "obs_wd_deg":       s["twd_deg"],
                "model_ws_ms":      fc_ws,
                "model_wd_deg":     fc_wd,
                "model_ws_ms_obs":  fc_ws,
                "model_wd_deg_obs": fc_wd,
            })

        time_series.append({"model_id": model.model_id, "points": ts_pts})

        if len(obs_uv) < settings.min_samples:
            model_results.append({
                "model_id":       model.model_id,
                "provider":       model.provider,
                "n_samples":      len(obs_uv),
                "vector_rmse_uv": None,
                "mae_ws":         None,
                "rmse_ws":        None,
                "bias_ws":        None,
                "dir_err_deg":    None,
                "status":         "insufficient_data",
                "reasons":        ["below_min_samples"],
                "run_time_utc":   None,
            })
            continue

        m = compute_metrics(obs_uv, fc_uv)
        model_results.append({
            "model_id":       model.model_id,
            "provider":       model.provider,
            "n_samples":      m.n_samples,
            "vector_rmse_uv": m.vector_rmse_uv,
            "mae_ws":         m.mae_ws,
            "rmse_ws":        m.rmse_ws,
            "bias_ws":        m.bias_ws,
            "dir_err_deg":    m.dir_err_deg,
            "status":         "ok",
            "reasons":        [],
            "run_time_utc":   None,
        })

    ok = [r for r in model_results if r["status"] == "ok" and r["vector_rmse_uv"] is not None]
    ok.sort(key=lambda r: r["vector_rmse_uv"])
    winner = ok[0]["model_id"] if ok else None
    model_results.sort(key=lambda r: (0 if r["status"] == "ok" else 1, r["vector_rmse_uv"] or 9999))

    return {
        "n_samples":        len(samples),
        "window_start_utc": start,
        "window_end_utc":   end,
        "route_bbox":       route_bbox,
        "models":           model_results,
        "winner_model_id":  winner,
        "time_series":      time_series,
        "track":            [{"time_utc": s["time_utc"], "lat": s["lat"], "lon": s["lon"]} for s in samples],
    }
