"""OpenWRF forecast adapter — openskiron.org GRIB files.

Downloads the latest OpenWRF run for the region covering the requested
coordinates, parses u10/v10 with cfgrib, and returns ForecastValue objects
via nearest-neighbour grid lookup.

Geographic coverage: Mediterranean basin only (Aegean, Adriatic, general Med).
"""
from __future__ import annotations

import bz2
import gc
import io
import logging
import math
import os
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

import httpx
import numpy as np

from .domain import ForecastValue, ModelDefinition

logger = logging.getLogger("wind_validation.openwrf")

MODEL_ID = "openwrf"

# ── OpenWRF region definitions ────────────────────────────────────────────────
class _Region(NamedTuple):
    name: str
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float


REGIONS_4KM: list[_Region] = [
    _Region("Baleares",          38.5, 41.0,  0.5,  5.0),
    _Region("Gulf_of_Lion",      41.0, 44.5,  2.0,  7.5),
    _Region("Ligurian",          42.5, 45.0,  5.5, 10.5),
    _Region("Corsica",           41.0, 43.5,  7.5, 10.5),
    _Region("Sardinia",          37.5, 41.5,  7.5, 10.5),
    _Region("Tyrrhenian",        37.5, 44.5,  9.5, 15.0),
    _Region("Sicily",            35.5, 39.5, 11.5, 16.5),
    _Region("Adriatic_North",    44.5, 47.0, 12.0, 15.0),
    _Region("Adriatic_Central",  41.0, 46.0, 12.5, 16.5),
    _Region("Adriatic_South",    38.5, 42.5, 14.5, 21.0),
    _Region("Ionian_Islands",    36.5, 40.5, 19.5, 23.5),
    _Region("Aegean_NW",         38.0, 42.0, 22.0, 27.0),
    _Region("Aegean_NE",         38.0, 42.0, 25.5, 29.5),
    _Region("Aegean_SW",         35.0, 39.0, 22.0, 27.0),
    _Region("Aegean_SE",         35.0, 39.0, 25.5, 29.5),
]
REGIONS_12KM: list[_Region] = [
    # Names must match file prefix on openskiron.org (Spain_12km_WRF_WAM_…)
    _Region("Aegean",         33.5, 43.0, 19.0, 30.5),
    _Region("Ionian",         34.0, 42.0, 14.0, 24.0),
    _Region("Taurus",         30.0, 40.0, 25.0, 42.0),
    _Region("Italy",          37.0, 47.0,  7.0, 20.0),
    _Region("Spain",          35.0, 44.5, -9.5,  4.5),
    _Region("France",         42.0, 51.5, -5.5, 10.5),
    _Region("Atlantic_Coast", 34.0, 51.0,-16.0,  1.0),
    _Region("Channel",        47.5, 56.0, -8.0,  5.0),
]
# Keep legacy alias for external callers
REGIONS = REGIONS_12KM

_BASE_12KM = "https://openskiron.org/gribs_wrf_12km"
_BASE_4KM  = "https://openskiron.org/gribs_wrf_4km"
_RUN_HOURS = (0, 6, 12, 18)   # UTC cycles


def _find_in(regions: list[_Region], lat: float, lon: float) -> _Region | None:
    best: _Region | None = None
    best_area = float("inf")
    for r in regions:
        if r.lat_min <= lat <= r.lat_max and r.lon_min <= lon <= r.lon_max:
            area = (r.lat_max - r.lat_min) * (r.lon_max - r.lon_min)
            if area < best_area:
                best, best_area = r, area
    return best


def find_region(lat: float, lon: float) -> _Region | None:
    """Return the most specific 12 km region containing (lat, lon)."""
    return _find_in(REGIONS_12KM, lat, lon)


def find_best_region(lat: float, lon: float) -> tuple[_Region, str] | None:
    """Return (region, resolution) — tries 4km first, falls back to 12km."""
    r4 = _find_in(REGIONS_4KM, lat, lon)
    if r4:
        return r4, "4km"
    r12 = _find_in(REGIONS_12KM, lat, lon)
    if r12:
        return r12, "12km"
    return None


# ── GRIB grid data container ──────────────────────────────────────────────────

class _GridData(NamedTuple):
    lats: np.ndarray               # (Y,)
    lons: np.ndarray               # (X,)
    u_arr: np.ndarray              # (T, Y, X)
    v_arr: np.ndarray              # (T, Y, X)
    times: list[datetime]          # length T
    run_dt: datetime
    gust_arr:   np.ndarray | None = None   # (T, Y, X) surface gust  m/s
    temp_arr:   np.ndarray | None = None   # (T, Y, X) 2 m temp      °C
    msl_arr:    np.ndarray | None = None   # (T, Y, X) MSL pressure  Pa
    precip_arr: np.ndarray | None = None   # (T, Y, X) accum precip  kg/m²
    cloud_arr:  np.ndarray | None = None   # (T, Y, X) total cloud   0-100 %
    cape_arr:   np.ndarray | None = None   # (T, Y, X) CAPE          J/kg


# ── Grid cache (avoid re-downloading the same run) ───────────────────────────

_cache_lock = threading.Lock()
_cache: dict[tuple[str, str], _GridData] = {}   # (region_name, run_iso) → grid


def _cache_key(region_name: str, resolution: str, run_dt: datetime) -> tuple[str, str, str]:
    return (region_name, resolution, run_dt.strftime("%Y%m%d%H"))


# ── Download + parse ──────────────────────────────────────────────────────────

def _download_grib(region: _Region, resolution: str, run_dt: datetime) -> bytes | None:
    date_sfx = run_dt.strftime("%y%m%d")   # YYMMDD e.g. 260325 for 2026-03-25
    base = _BASE_4KM if resolution == "4km" else _BASE_12KM
    url = f"{base}/{region.name}_{resolution}_WRF_WAM_{date_sfx}-{run_dt.hour:02d}.grb.bz2"
    try:
        with httpx.Client(timeout=120) as c:
            r = c.get(url)
        if r.status_code == 200:
            logger.info("OpenWRF %s (%s) run %s downloaded (%d B)",
                        region.name, resolution, run_dt, len(r.content))
            raw = bz2.decompress(r.content)
            return raw
        logger.info("OpenWRF %s %s → HTTP %d", region.name, url, r.status_code)
    except Exception as exc:
        logger.warning("OpenWRF %s %s: %s", region.name, url, exc)
    return None


def _parse_grib(raw: bytes, run_dt: datetime) -> _GridData:
    """Parse OpenWRF GRIB file using eccodes directly.

    WRF uses GRIB1 with NCEP parameter tables (paramId 33 = u-wind, 34 = v-wind)
    which cfgrib cannot name; the wind messages appear as 'unknown' in cfgrib.
    Scanning with eccodes avoids the naming problem entirely.
    """
    import eccodes  # noqa: PLC0415

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".grb", delete=False) as f:
            f.write(raw)
            tmp_path = f.name

        # WRF GRIB1 (centre=kwbc) uses NCEP parameter tables; eccodes reports paramId=0
        # for all atmospheric fields. Use indicatorOfParameter (GRIB1) instead.
        # Confirmed from file scan: iop=33→u, 34→v, 180→gust, 11→temp2m,
        #   2→msl, 61→precip, 71→cloud, 157→CAPE
        _IOP_ROLE = {   # GRIB1 indicatorOfParameter (NCEP table 2)
            33: "u",   34: "v",
            180: "gust",
            11: "temp",
            2: "msl",
            61: "precip",
            71: "cloud",
            157: "cape",
        }
        _PID_ROLE = {   # GRIB2 paramId fallback (ECMWF)
            131: "u",  132: "v",
            228: "gust",
            130: "temp",
            151: "msl",
            164: "cloud",
            59: "cape",
        }

        buckets: dict[str, list[tuple[int, np.ndarray]]] = {
            k: [] for k in ("u", "v", "gust", "temp", "msl", "precip", "cloud", "cape")
        }
        lats_2d = lons_2d = None

        with open(tmp_path, "rb") as fh:
            while True:
                try:
                    msg = eccodes.codes_grib_new_from_file(fh)
                except Exception:
                    break
                if msg is None:
                    break
                try:
                    try:
                        edition = eccodes.codes_get(msg, "edition")
                    except Exception:
                        edition = 1

                    role = None
                    if edition == 1:
                        try:
                            iop = eccodes.codes_get(msg, "indicatorOfParameter")
                            role = _IOP_ROLE.get(iop)
                        except Exception:
                            pass
                    else:
                        try:
                            pid = eccodes.codes_get(msg, "paramId")
                            role = _PID_ROLE.get(pid)
                        except Exception:
                            pass
                    if role is None:
                        continue

                    try:
                        ni = eccodes.codes_get(msg, "Ni")
                        nj = eccodes.codes_get(msg, "Nj")
                        values = eccodes.codes_get_values(msg).reshape(nj, ni)
                    except Exception as _e:
                        logger.debug("OpenWRF eccodes reshape: %s", _e)
                        continue

                    if lats_2d is None:
                        try:
                            lats_2d = eccodes.codes_get_array(msg, "latitudes").reshape(nj, ni)
                            lons_2d = eccodes.codes_get_array(msg, "longitudes").reshape(nj, ni)
                        except Exception:
                            pass

                    try:
                        step = int(str(eccodes.codes_get(msg, "stepRange")).split("-")[-1])
                    except Exception:
                        step = len(buckets[role])

                    buckets[role].append((step, values))
                finally:
                    eccodes.codes_release(msg)

        if not buckets["u"] or not buckets["v"]:
            raise ValueError(
                f"10 m wind not found in OpenWRF GRIB "
                f"(u={len(buckets['u'])}, v={len(buckets['v'])})"
            )

        def _stack(key: str) -> np.ndarray | None:
            lst = buckets[key]
            if not lst:
                return None
            lst.sort(key=lambda x: x[0])
            return np.stack([d for _, d in lst]).astype(float)

        for k in buckets:
            buckets[k].sort(key=lambda x: x[0])

        u_arr    = _stack("u")
        v_arr    = _stack("v")
        gust_arr = _stack("gust")
        temp_arr = _stack("temp")
        if temp_arr is not None:
            temp_arr = temp_arr - 273.15       # K → °C
        msl_arr    = _stack("msl")
        precip_arr = _stack("precip")
        cloud_arr  = _stack("cloud")
        cape_arr   = _stack("cape")

        if lats_2d is None:
            raise ValueError("Could not extract lat/lon from GRIB")

        lats_raw = lats_2d[:, 0]
        lons_raw = lons_2d[0, :]
        if lons_raw.max() > 180:
            lons_raw = np.where(lons_raw > 180, lons_raw - 360, lons_raw)

        times: list[datetime] = [
            run_dt + timedelta(hours=s) for s, _ in buckets["u"]
        ]

        gc.collect()
        return _GridData(
            lats=lats_raw, lons=lons_raw,
            u_arr=u_arr, v_arr=v_arr,
            times=times, run_dt=run_dt,
            gust_arr=gust_arr, temp_arr=temp_arr, msl_arr=msl_arr,
            precip_arr=precip_arr, cloud_arr=cloud_arr, cape_arr=cape_arr,
        )
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _get_grid(region: _Region, resolution: str) -> _GridData | None:
    """Return cached or freshly-downloaded grid for *region* at *resolution*."""
    now = datetime.now(UTC)
    for h_back in range(0, 25):
        cand = (now - timedelta(hours=h_back)).replace(minute=0, second=0, microsecond=0)
        if cand.hour not in _RUN_HOURS:
            continue
        key = _cache_key(region.name, resolution, cand)
        with _cache_lock:
            if key in _cache:
                return _cache[key]
        raw = _download_grib(region, resolution, cand)
        if raw is None:
            continue
        try:
            grid = _parse_grib(raw, cand)
        except Exception as exc:
            logger.warning("OpenWRF parse failed for %s %s: %s", region.name, cand, exc)
            continue
        with _cache_lock:
            _cache.clear()   # evict old entries (one region at a time to save RAM)
            _cache[key] = grid
        return grid
    return None


# ── Point extraction ──────────────────────────────────────────────────────────

def _nearest_idx(lats: np.ndarray, lons: np.ndarray, lat: float, lon: float):
    """Return (iy, ix) of the nearest grid point."""
    if lats.ndim == 1:
        iy = int(np.argmin(np.abs(lats - lat)))
        ix = int(np.argmin(np.abs(lons - lon)))
    else:
        dist = (lats - lat) ** 2 + (lons - lon) ** 2
        iy, ix = np.unravel_index(dist.argmin(), dist.shape)
        iy, ix = int(iy), int(ix)
    return iy, ix


def _uv_to_ws_wd(u: float, v: float) -> tuple[float, float]:
    ws = math.sqrt(u * u + v * v)
    wd = (270.0 - math.degrees(math.atan2(v, u))) % 360.0
    return ws, wd


# ── Public interface ──────────────────────────────────────────────────────────

class OpenWrfAdapter:
    """Fetches OpenWRF point forecasts from openskiron.org GRIB files."""

    def fetch_model_at_coords(
        self,
        model: ModelDefinition,
        coords: list[tuple[float, float]],
        start: datetime,
        end: datetime,
    ) -> list[ForecastValue]:
        if model.model_id != MODEL_ID:
            return []

        rows: list[ForecastValue] = []
        for lat, lon in coords:
            region_res = find_best_region(lat, lon)
            if region_res is None:
                continue
            region, resolution = region_res
            grid = _get_grid(region, resolution)
            if grid is None:
                logger.warning("OpenWRF: no grid available for (%.2f, %.2f) region=%s (%s)",
                               lat, lon, region.name, resolution)
                continue

            iy, ix = _nearest_idx(grid.lats, grid.lons, lat, lon)

            def _pt(arr: "np.ndarray | None", t: int) -> "float | None":
                if arr is None or t >= arr.shape[0]:
                    return None
                v = float(arr[t, iy, ix])
                return v if not math.isnan(v) else None

            for t_idx, valid_dt in enumerate(grid.times):
                if valid_dt < start or valid_dt > end:
                    continue
                if t_idx >= grid.u_arr.shape[0]:
                    break
                rows.append(ForecastValue(
                    model_id=MODEL_ID,
                    run_time_utc=grid.run_dt,
                    valid_time_utc=valid_dt,
                    lat=lat,
                    lon=lon,
                    u10=float(grid.u_arr[t_idx, iy, ix]),
                    v10=float(grid.v_arr[t_idx, iy, ix]),
                    gust_ms=_pt(grid.gust_arr, t_idx),
                    temp_c=_pt(grid.temp_arr, t_idx),
                    pressure_msl_hpa=(_pt(grid.msl_arr, t_idx) or 0) / 100 or None,
                    precip_mm=_pt(grid.precip_arr, t_idx),
                    cloud_cover_pct=_pt(grid.cloud_arr, t_idx),
                    cape_jkg=_pt(grid.cape_arr, t_idx),
                ))

        return rows

    def fetch_forecast_with_extras(
        self,
        model: ModelDefinition,
        coords: list[tuple[float, float]],
        start: datetime,
        end: datetime,
    ) -> list[ForecastValue]:
        """Same as fetch_model_at_coords — OpenWRF has no extra diagnostic fields."""
        return self.fetch_model_at_coords(model, coords, start, end)
