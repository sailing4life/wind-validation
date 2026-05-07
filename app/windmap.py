"""Wind map GIF generator — native Météo-France GRIB grids via meteofetch.

Data sources
------------
* ``arpege025``  – ARPEGE 0.25° global model (default; good for NL / North Sea)
* ``arome025``   – AROME 0.025° high-res model (France + neighbours incl. NL coast)

Each model is fetched via meteofetch (paquet='SP2') which returns U10/V10 as
xarray DataArrays at the native grid spacing — no artificial point-grid
re-sampling.

Rendering per frame
-------------------
1. OpenStreetMap basemap (OSM zoom 9)
2. Smooth wind-speed shading  (pcolormesh, Gouraud, semi-transparent, full grid)
3. Meteorological wind barbs  (half=5 kt, full=10 kt, flag=50 kt; subsampled
   for AROME so barbs don't overlap)

Requires
--------
* System:  libeccodes-dev  (apt-get) or  brew install eccodes  (macOS)
* Python:  meteofetch  matplotlib  Pillow  numpy

Runs **synchronously** — call via ``asyncio.to_thread()`` from async endpoints.
"""
from __future__ import annotations

import gc
import io
import math
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta

import httpx
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import numpy as np
from PIL import Image

logger = logging.getLogger("wind_validation.windmap")

# ── display constants ─────────────────────────────────────────────────────────
# Half-extents: ~25 km lat × ~40 km lon at 52°N → ≈ 50×80 km map
# (lon half-extent is wider to compensate for cos(lat) compression)
AREA_LAT_DEG = 0.23   # ~25 km
AREA_LON_DEG = 0.38   # ~26 km at 52°N  (0.38 × cos(52°) × 111 ≈ 26 km)
OSM_ZOOM  = 11        # detailed coastline, waterways, harbour features
TILE_PX   = 256
FRAME_MS  = 600       # ms per GIF frame
MAX_WS_KT = 35.0      # top of colour scale (knots)
MS_TO_KT  = 1.943844
# CartoDB Voyager — land/water clearly differentiated, clean for wind overlays
TILE_URL  = "https://{sub}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png"
TILE_SUBS = ("a", "b", "c", "d")
TILE_UA   = "wind-validation/1.0 (contact: admin@jellelourens.nl)"
FIG_W_PX  = 900
FIG_H_PX  = 680

# Target one wind barb per ~4 km regardless of model resolution
# AROME001 (0.01°) → stride≈4;  AROME0025 (0.025°) → stride≈2;  ICON-D2 (0.02°) → stride≈2
BARB_SPACING_DEG = 0.04

# ── wind colormap (built once at import time) ──────────────────────────────────
_n = 256
_t = np.linspace(0, 1, _n)
_base = mcolors.LinearSegmentedColormap.from_list(
    "_wind_rgb", ["#3b82f6", "#22c55e", "#ef4444", "#a855f7"]
)(_t)
_base[:, 3] = 0.08 + _t * 0.64   # alpha: 0.08 (calm, transparent) → 0.72 (strong)
CMAP_WIND = mcolors.ListedColormap(_base, name="wind_kt")
NORM_WIND = mcolors.Normalize(vmin=0, vmax=MAX_WS_KT)


# ── OSM tile helpers ──────────────────────────────────────────────────────────

def _tile_xy(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    r = math.radians(lat)
    y = int((1.0 - math.log(math.tan(r) + 1.0 / math.cos(r)) / math.pi) / 2.0 * n)
    return x, y


def _tile_nw(x: int, y: int, zoom: int) -> tuple[float, float]:
    n = 2 ** zoom
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lat, lon


def _fetch_one_tile(x: int, y: int, zoom: int) -> Image.Image:
    sub = TILE_SUBS[(x + y) % len(TILE_SUBS)]
    url = TILE_URL.format(sub=sub, z=zoom, x=x, y=y)
    with httpx.Client(headers={"User-Agent": TILE_UA}, timeout=15) as c:
        resp = c.get(url)
        resp.raise_for_status()
    return Image.open(io.BytesIO(resp.content)).convert("RGB")


def _get_basemap(
    lat_min: float, lat_max: float,
    lon_min: float, lon_max: float,
    zoom: int = OSM_ZOOM,
) -> tuple[Image.Image, tuple[float, float, float, float]]:
    """Stitch OSM tiles; returns (image, (lon_min, lon_max, lat_min, lat_max))."""
    x0, y0 = _tile_xy(lat_max, lon_min, zoom)
    x1, y1 = _tile_xy(lat_min, lon_max, zoom)
    cols, rows = x1 - x0 + 1, y1 - y0 + 1
    canvas = Image.new("RGB", (cols * TILE_PX, rows * TILE_PX), (220, 220, 220))
    tile_list = [(x0 + c, y0 + r) for r in range(rows) for c in range(cols)]
    with ThreadPoolExecutor(max_workers=min(len(tile_list), 12)) as pool:
        futs = {
            pool.submit(_fetch_one_tile, tx, ty, zoom): (tx - x0, ty - y0)
            for tx, ty in tile_list
        }
        for fut in as_completed(futs):
            cx, ry = futs[fut]
            try:
                canvas.paste(fut.result(), (cx * TILE_PX, ry * TILE_PX))
            except Exception as exc:
                logger.warning("Tile (%d,%d) failed: %s", cx + x0, ry + y0, exc)
    lat_nw, lon_nw = _tile_nw(x0,     y0,     zoom)
    lat_se, lon_se = _tile_nw(x1 + 1, y1 + 1, zoom)
    return canvas, (lon_nw, lon_se, lat_se, lat_nw)


# ── time helpers ───────────────────────────────────────────────────────────────

def _np_dt_to_label(t) -> str:
    try:
        ts_s = int(np.datetime64(t, "s").astype("int64"))
        dt = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=ts_s)
        return dt.strftime("%d %b  %H:%M UTC")
    except Exception:
        return str(t)[:16]


def _np_dt_to_iso(t) -> str:
    try:
        ts_s = int(np.datetime64(t, "s").astype("int64"))
        dt = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=ts_s)
        return dt.strftime("%Y-%m-%dT%H:%M:00Z")
    except Exception:
        return ""


def _dt_to_label(dt: datetime) -> str:
    return dt.strftime("%d %b  %H:%M UTC")


def _dt_to_iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:00Z")


# ── GRIB source routing ────────────────────────────────────────────────────────

def _model_to_grib_source(model_param: str) -> str:
    """Map an Open-Meteo model_param string to a GRIB data source key."""
    p = model_param.lower()
    if p.startswith("local_upload:"):
        return "local_upload"
    if "knmi" in p or "harmonie" in p:
        return "harmonie_s3"
    if "icon" in p:
        return "icon_d2_dwd"
    if "openwrf" in p or "wrf" in p:
        return "openwrf"
    if "arpege" in p or "ecmwf" in p:
        return "arpege025"
    return "arome025"


# ── shared GRIB helpers ────────────────────────────────────────────────────────

def _cfgrib_wind(path: str):
    """Open a GRIB file and return (u_da, v_da) DataArrays for 10 m wind."""
    import cfgrib  # noqa: PLC0415
    for filter_keys in [
        {"typeOfLevel": "heightAboveGround", "level": 10},
        {"typeOfLevel": "heightAboveGround"},
        {},
    ]:
        try:
            datasets = cfgrib.open_datasets(path, filter_by_keys=filter_keys, indexpath=None)
        except Exception:
            continue
        for ds in datasets:
            u_da = v_da = None
            for u_name in ("u10", "u", "10u", "U10", "U_10M"):
                if u_name in ds:
                    u_da = ds[u_name]
                    break
            for v_name in ("v10", "v", "10v", "V10", "V_10M"):
                if v_name in ds:
                    v_da = ds[v_name]
                    break
            if u_da is not None and v_da is not None:
                return u_da, v_da
    raise ValueError(f"10 m wind components not found in {path}")


def _clip_and_pack(
    u_da, v_da,
    lat_min: float, lat_max: float,
    lon_min: float, lon_max: float,
    max_hours: int,
    run_dt: datetime | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[str], list[str]]:
    """Clip DataArrays to bbox and return the standard 6-tuple.

    Memory strategy: read coordinate vectors first (tiny), compute clip indices,
    read time axis (tiny), then load the full data array to numpy ONCE per
    variable and clip immediately — avoids the sortby() full-array copy that
    previously doubled peak memory usage.
    """
    # ── coordinates (lazy access — no data loaded yet) ────────────────────────
    lats_all = u_da.latitude.values
    lons_all = u_da.longitude.values.copy()
    if lons_all.max() > 180:
        lons_all = np.where(lons_all > 180, lons_all - 360, lons_all)

    lat_idx = np.where((lats_all >= lat_min) & (lats_all <= lat_max))[0]
    lon_idx = np.where((lons_all >= lon_min) & (lons_all <= lon_max))[0]
    if not len(lat_idx) or not len(lon_idx):
        raise ValueError(
            f"No data in bbox ({lat_min:.2f}–{lat_max:.2f} N, "
            f"{lon_min:.2f}–{lon_max:.2f} E)"
        )

    lats = lats_all[lat_idx]
    lons = lons_all[lon_idx]

    # ── time axis (still lazy) ────────────────────────────────────────────────
    if "time" in u_da.dims and u_da.time.size > 1:
        t_vals = u_da.time.values[:max_hours]
    elif hasattr(u_da, "valid_time"):
        t_vals = np.atleast_1d(u_da.valid_time.values)[:max_hours]
    else:
        t_vals = np.array([])

    # ── load + clip: one variable at a time, free full array immediately ──────
    # Peak memory = one full variable array + one clipped array (not 2× full).
    # We do NOT call sortby() — that materialises a sorted copy of everything.
    def _materialise(da) -> np.ndarray:
        raw = np.array(da.values, dtype=float)        # full grid, all timesteps
        if raw.ndim == 2:
            raw = raw[np.newaxis]
        raw = raw[:max_hours]
        clipped = raw[np.ix_(np.arange(raw.shape[0]), lat_idx, lon_idx)]
        del raw
        return clipped

    u_arr = _materialise(u_da);  u_da = None
    v_arr = _materialise(v_da);  v_da = None
    gc.collect()

    # ── ensure ascending latitude order ──────────────────────────────────────
    if len(lats) > 1 and lats[0] > lats[-1]:
        lats  = lats[::-1]
        u_arr = u_arr[:, ::-1, :]
        v_arr = v_arr[:, ::-1, :]

    # ── labels & times ────────────────────────────────────────────────────────
    n_t = u_arr.shape[0]
    if len(t_vals):
        t_vals = t_vals[:n_t]
        labels    = [_np_dt_to_label(t) for t in t_vals]
        times_utc = [_np_dt_to_iso(t)   for t in t_vals]
    elif run_dt is not None:
        labels    = [_dt_to_label(run_dt)]
        times_utc = [_dt_to_iso(run_dt)]
    else:
        labels    = [f"T+{i:02d}h" for i in range(n_t)]
        times_utc = [""] * n_t

    return lats, lons, u_arr, v_arr, labels, times_utc


# ── Harmonie S3 ────────────────────────────────────────────────────────────────
# Public S3: harmonie-files.s3.eu-west-1.amazonaws.com/download/
# Pattern: harmonie_xy_{YYYY-MM-DD}_{HH:02d}.grb   (3-hourly runs)

_HARMONIE_S3_BASE = (
    "https://harmonie-files.s3.eu-west-1.amazonaws.com/download/"
    "harmonie_xy_{date}_{run:02d}.grb"
)


def _fetch_harmonie_s3(
    lat_min: float, lat_max: float,
    lon_min: float, lon_max: float,
    max_hours: int,
) -> tuple:
    import os, tempfile  # noqa: PLC0415

    now = datetime.now(UTC)
    resp = run_dt_used = None
    for h_back in range(0, 25):
        cand = (now - timedelta(hours=h_back)).replace(minute=0, second=0, microsecond=0)
        if cand.hour % 3 != 0:
            continue
        url = _HARMONIE_S3_BASE.format(date=cand.strftime("%Y-%m-%d"), run=cand.hour)
        try:
            with httpx.Client(timeout=120) as client:
                r = client.get(url)
            if r.status_code == 200:
                resp = r
                run_dt_used = cand
                logger.info("Harmonie S3 run %s (%d B)", run_dt_used, len(r.content))
                break
            logger.debug("Harmonie S3 %s → HTTP %d", url, r.status_code)
        except Exception as exc:
            logger.debug("Harmonie S3 %s: %s", url, exc)

    if resp is None:
        raise RuntimeError("No recent Harmonie GRIB found on S3 (tried last 24 h)")

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".grb", delete=False) as f:
            f.write(resp.content)
            tmp_path = f.name
        del resp; gc.collect()   # free download bytes before parsing (can be 200 MB+)
        u_da, v_da = _cfgrib_wind(tmp_path)
        return _clip_and_pack(u_da, v_da, lat_min, lat_max, lon_min, lon_max, max_hours)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ── ICON-D2 DWD open data ──────────────────────────────────────────────────────
# https://opendata.dwd.de/weather/nwp/icon-d2/grib/{RUN}/{var}/
# icon-d2_germany_regular-lat-lon_single-level_{YYYYMMDDHH}_{FFF}_2d_{var}.grib2.bz2
# One file per forecast step (000–048); 3-hourly model cycles.

_ICON_D2_BASE = "https://opendata.dwd.de/weather/nwp/icon-d2/grib"
_ICON_D2_FILE = (
    "icon-d2_germany_regular-lat-lon_single-level"
    "_{run_str}_{step:03d}_2d_{var}.grib2.bz2"
)


def _fetch_icon_d2_dwd(
    lat_min: float, lat_max: float,
    lon_min: float, lon_max: float,
    max_hours: int,
) -> tuple:
    import bz2 as _bz2, os, tempfile  # noqa: PLC0415

    # Locate latest available run (probe FFF=000 u_10m)
    now = datetime.now(UTC)
    run_dt = run_str = None
    for h_back in range(0, 25):
        cand = (now - timedelta(hours=h_back)).replace(minute=0, second=0, microsecond=0)
        if cand.hour % 3 != 0:
            continue
        cand_str = cand.strftime("%Y%m%d%H")
        probe = (
            f"{_ICON_D2_BASE}/{cand.hour:02d}/u_10m/"
            + _ICON_D2_FILE.format(run_str=cand_str, step=0, var="u_10m")
        )
        try:
            with httpx.Client(timeout=10) as c:
                if c.head(probe).status_code == 200:
                    run_dt = cand
                    run_str = cand_str
                    break
        except Exception:
            continue

    if run_dt is None:
        raise RuntimeError("No recent ICON-D2 run found on DWD open data")
    logger.info("ICON-D2 run %s", run_dt)

    steps = list(range(0, min(max_hours + 1, 49)))

    def _dl_step(var: str, step: int):
        url = (
            f"{_ICON_D2_BASE}/{run_dt.hour:02d}/{var}/"
            + _ICON_D2_FILE.format(run_str=run_str, step=step, var=var)
        )
        with httpx.Client(timeout=45) as c:
            r = c.get(url)
            r.raise_for_status()
        raw = _bz2.decompress(r.content)
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".grib2", delete=False) as f:
                f.write(raw)
                tmp = f.name
            import cfgrib  # noqa: PLC0415
            datasets = cfgrib.open_datasets(tmp, indexpath=None)
            for ds in datasets:
                for vname in (var.replace("_", ""), "u10", "v10", "u", "v"):
                    if vname in ds:
                        da = ds[vname]
                        lats = da.latitude.values
                        lons = da.longitude.values
                        arr  = np.array(da.values, dtype=float)
                        vt   = da.valid_time.values if hasattr(da, "valid_time") else None
                        return step, var, arr, lats, lons, vt
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        return step, var, None, None, None, None

    u_by_step: dict[int, np.ndarray] = {}
    v_by_step: dict[int, np.ndarray] = {}
    times_by_step: dict[int, object] = {}
    lats_ref = lons_ref = None

    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {
            pool.submit(_dl_step, var, step): (var, step)
            for step in steps for var in ("u_10m", "v_10m")
        }
        for fut in as_completed(futs):
            try:
                step, var, arr, lats, lons, vt = fut.result()
                if arr is None:
                    continue
                if lats_ref is None:
                    lats_ref, lons_ref = lats, lons
                if var == "u_10m":
                    u_by_step[step] = arr
                    times_by_step[step] = vt
                else:
                    v_by_step[step] = arr
            except Exception as exc:
                logger.warning("ICON-D2 step failed: %s", exc)

    if not u_by_step or lats_ref is None:
        raise RuntimeError("Failed to download any ICON-D2 steps")

    sorted_steps = sorted(s for s in steps if s in u_by_step and s in v_by_step)

    # Clip to bbox
    lons_norm = np.where(lons_ref > 180, lons_ref - 360, lons_ref)
    lat_mask = (lats_ref >= lat_min) & (lats_ref <= lat_max)
    lon_mask = (lons_norm >= lon_min) & (lons_norm <= lon_max)
    if not lat_mask.any() or not lon_mask.any():
        raise ValueError(
            f"No ICON-D2 data in bbox ({lat_min:.2f}–{lat_max:.2f} N, "
            f"{lon_min:.2f}–{lon_max:.2f} E)"
        )

    lats = lats_ref[lat_mask]
    lons = lons_norm[lon_mask]
    u_arr = np.stack([u_by_step[s][np.ix_(lat_mask, lon_mask)] for s in sorted_steps])
    v_arr = np.stack([v_by_step[s][np.ix_(lat_mask, lon_mask)] for s in sorted_steps])

    labels = []
    times_utc = []
    for s in sorted_steps:
        vt = times_by_step.get(s)
        frame_dt = run_dt + timedelta(hours=s)
        if vt is not None:
            try:
                ts_s = int(np.datetime64(vt, "s").astype("int64"))
                frame_dt = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=ts_s)
            except Exception:
                pass
        labels.append(_dt_to_label(frame_dt))
        times_utc.append(_dt_to_iso(frame_dt))

    return lats, lons, u_arr, v_arr, labels, times_utc


# ── OpenWRF (openskiron.org) ───────────────────────────────────────────────────
# Pattern: https://openskiron.org/gribs_wrf_{res}/{Region}_{res}_WRF_WAM_{YYMMDD}-{HH}.grb.bz2
# Date suffix uses YYMMDD (e.g. 260325 for 2026-03-25).  Runs: 00, 06, 12, 18 UTC.

_OPENWRF_12KM_BASE = "https://openskiron.org/gribs_wrf_12km"
_OPENWRF_4KM_BASE  = "https://openskiron.org/gribs_wrf_4km"

# (name, lat_min, lat_max, lon_min, lon_max)  — smallest area wins
_OPENWRF_4KM_REGIONS = [
    ("Baleares",          38.5, 41.0,  0.5,  5.0),
    ("Gulf_of_Lion",      41.0, 44.5,  2.0,  7.5),
    ("Ligurian",          42.5, 45.0,  5.5, 10.5),
    ("Corsica",           41.0, 43.5,  7.5, 10.5),
    ("Sardinia",          37.5, 41.5,  7.5, 10.5),
    ("Tyrrhenian",        37.5, 44.5,  9.5, 15.0),
    ("Sicily",            35.5, 39.5, 11.5, 16.5),
    ("Adriatic_North",    44.5, 47.0, 12.0, 15.0),
    ("Adriatic_Central",  41.0, 46.0, 12.5, 16.5),
    ("Adriatic_South",    38.5, 42.5, 14.5, 21.0),
    ("Ionian_Islands",    36.5, 40.5, 19.5, 23.5),
    ("Aegean_NW",         38.0, 42.0, 22.0, 27.0),
    ("Aegean_NE",         38.0, 42.0, 25.5, 29.5),
    ("Aegean_SW",         35.0, 39.0, 22.0, 27.0),
    ("Aegean_SE",         35.0, 39.0, 25.5, 29.5),
]
_OPENWRF_12KM_REGIONS = [
    ("Aegean",         33.5, 43.0, 19.0, 30.5),
    ("Ionian",         34.0, 42.0, 14.0, 24.0),
    ("Taurus",         30.0, 40.0, 25.0, 42.0),
    ("Italy",          37.0, 47.0,  7.0, 20.0),
    ("Spain",          35.0, 44.5, -9.5,  4.5),
    ("France",         42.0, 51.5, -5.5, 10.5),
    ("Atlantic_Coast", 34.0, 51.0,-16.0,  1.0),
    ("Channel",        47.5, 56.0, -8.0,  5.0),
]


def _openwrf_region(center_lat: float, center_lon: float) -> tuple[str, str] | None:
    """Return (region_name, resolution) for the best matching OpenWRF file."""
    for table, res in [(_OPENWRF_4KM_REGIONS, "4km"), (_OPENWRF_12KM_REGIONS, "12km")]:
        best = best_area = None
        for name, lat0, lat1, lon0, lon1 in table:
            if lat0 <= center_lat <= lat1 and lon0 <= center_lon <= lon1:
                area = (lat1 - lat0) * (lon1 - lon0)
                if best_area is None or area < best_area:
                    best, best_area = (name, res), area
        if best:
            return best
    return None


def _fetch_openwrf(
    center_lat: float,
    center_lon: float,
    lat_min: float, lat_max: float,
    lon_min: float, lon_max: float,
    max_hours: int,
) -> tuple:
    import bz2 as _bz2, os, tempfile  # noqa: PLC0415

    region_res = _openwrf_region(center_lat, center_lon)
    if region_res is None:
        raise ValueError(
            f"Location ({center_lat:.2f} N, {center_lon:.2f} E) is outside all "
            "OpenWRF regions. OpenWRF covers the Mediterranean basin only."
        )
    region, resolution = region_res
    base_url = _OPENWRF_4KM_BASE if resolution == "4km" else _OPENWRF_12KM_BASE

    now = datetime.now(UTC)
    resp = run_dt_used = None
    for h_back in range(0, 25):
        cand = (now - timedelta(hours=h_back)).replace(minute=0, second=0, microsecond=0)
        if cand.hour not in (0, 6, 12, 18):
            continue
        date_sfx = cand.strftime("%y%m%d")   # YYMMDD e.g. 260325
        url = f"{base_url}/{region}_{resolution}_WRF_WAM_{date_sfx}-{cand.hour:02d}.grb.bz2"
        try:
            with httpx.Client(timeout=120) as client:
                r = client.get(url)
            if r.status_code == 200:
                resp = r
                run_dt_used = cand
                logger.info("OpenWRF %s (%s) run %s (%d B)", region, resolution, run_dt_used, len(r.content))
                break
            logger.info("OpenWRF %s → HTTP %d", url, r.status_code)
        except Exception as exc:
            logger.warning("OpenWRF %s: %s", url, exc)

    if resp is None:
        raise RuntimeError(
            f"No recent OpenWRF GRIB found for region '{region}' ({resolution}, tried last 24 h)"
        )

    import bz2 as _bz2  # noqa: PLC0415
    raw = _bz2.decompress(resp.content)
    del resp; gc.collect()
    return _openwrf_eccodes_clip(raw, lat_min, lat_max, lon_min, lon_max, max_hours, run_dt_used)


def _openwrf_eccodes_clip(
    raw: bytes,
    lat_min: float, lat_max: float,
    lon_min: float, lon_max: float,
    max_hours: int,
    run_dt: "datetime",
) -> tuple:
    """Extract u/v 10 m wind from a WRF GRIB1 file using eccodes (NCEP paramId 33/34)
    and return the standard 6-tuple expected by generate_wind_frames/gif."""
    import eccodes  # noqa: PLC0415

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".grb", delete=False) as f:
            f.write(raw)
            tmp_path = f.name
        del raw; gc.collect()

        u_steps: list[tuple[int, np.ndarray]] = []
        v_steps: list[tuple[int, np.ndarray]] = []
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
                    if edition == 1:
                        try:
                            iop = eccodes.codes_get(msg, "indicatorOfParameter")
                        except Exception:
                            iop = -1
                        is_u = iop == 33
                        is_v = iop == 34
                    else:
                        try:
                            param_id = eccodes.codes_get(msg, "paramId")
                        except Exception:
                            param_id = -1
                        is_u = param_id in (33, 131)
                        is_v = param_id in (34, 132)
                    if not (is_u or is_v):
                        continue
                    ni = eccodes.codes_get(msg, "Ni")
                    nj = eccodes.codes_get(msg, "Nj")
                    values = eccodes.codes_get_values(msg).reshape(nj, ni)
                    if lats_2d is None:
                        lats_2d = eccodes.codes_get_array(msg, "latitudes").reshape(nj, ni)
                        lons_2d = eccodes.codes_get_array(msg, "longitudes").reshape(nj, ni)
                    try:
                        step = int(str(eccodes.codes_get(msg, "stepRange")).split("-")[-1])
                    except Exception:
                        step = len(u_steps) if is_u else len(v_steps)
                    if is_u:
                        u_steps.append((step, values))
                    else:
                        v_steps.append((step, values))
                finally:
                    eccodes.codes_release(msg)

        if not u_steps or not v_steps:
            raise ValueError(f"No u/v wind in OpenWRF GRIB (u={len(u_steps)}, v={len(v_steps)})")

        u_steps.sort(key=lambda x: x[0])
        v_steps.sort(key=lambda x: x[0])

        lats = lats_2d[:, 0]
        lons = lons_2d[0, :]
        if lons.max() > 180:
            lons = np.where(lons > 180, lons - 360, lons)

        lat_mask = (lats >= lat_min) & (lats <= lat_max)
        lon_mask = (lons >= lon_min) & (lons <= lon_max)
        if not lat_mask.any() or not lon_mask.any():
            raise ValueError(f"No data in bbox ({lat_min:.2f}–{lat_max:.2f} N, {lon_min:.2f}–{lon_max:.2f} E)")

        lat_idx = np.where(lat_mask)[0]
        lon_idx = np.where(lon_mask)[0]

        u_arr = np.stack([d for _, d in u_steps[:max_hours]])[:, lat_idx][:, :, lon_idx]
        v_arr = np.stack([d for _, d in v_steps[:max_hours]])[:, lat_idx][:, :, lon_idx]
        steps = [s for s, _ in u_steps[:max_hours]]

        if lats[0] > lats[-1]:   # ensure ascending latitude
            lat_idx_sorted = np.argsort(lats[lat_idx])
            lats_clip = lats[lat_idx][lat_idx_sorted]
            u_arr = u_arr[:, lat_idx_sorted, :]
            v_arr = v_arr[:, lat_idx_sorted, :]
        else:
            lats_clip = lats[lat_idx]

        labels    = [f"{run_dt + timedelta(hours=s):%d %b %H:%M}" for s in steps]
        times_utc = [(run_dt + timedelta(hours=s)).strftime("%Y-%m-%dT%H:%M:%SZ") for s in steps]

        gc.collect()
        return lats_clip, lons[lon_idx], u_arr, v_arr, labels, times_utc
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ── meteofetch fallback (AROME / ARPEGE) ──────────────────────────────────────

def _fetch_meteofetch(
    lat_min: float, lat_max: float,
    lon_min: float, lon_max: float,
    model: str,
    max_hours: int,
) -> tuple:
    try:
        import meteofetch as _mf  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            f"meteofetch import failed: {exc}\n"
            "macOS: brew install eccodes && pip install meteofetch\n"
            "Linux: apt-get install libeccodes-dev && pip install meteofetch"
        ) from exc

    if model == "arpege025":
        candidates = [("arpege025", _mf.Arpege025)]
    else:
        # AROME001 (0.01°) domain is ~1750×2800 = 5 M pts → 2 GB/variable at 48 steps.
        # Use AROME0025 only to keep peak memory well under 8 GB.
        candidates = [("arome025", _mf.Arome0025)]

    last_exc: Exception | None = None
    for name, Model in candidates:
        logger.info("Fetching %s GRIB (SP1) via meteofetch…", name)
        try:
            ds = Model.get_latest_forecast(paquet="SP1")
        except Exception as exc:
            logger.warning("meteofetch %s failed: %s", name, exc)
            last_exc = exc
            continue

        if "u10" not in ds or "v10" not in ds:
            logger.warning("%s: u10/v10 absent (keys: %s), trying next", name, list(ds.keys()))
            del ds; gc.collect()
            continue

        # Extract only u10/v10 and free the rest of ds (other variables, potentially large)
        u_da = ds["u10"]
        v_da = ds["v10"]
        del ds; gc.collect()

        try:
            result = _clip_and_pack(
                u_da, v_da,
                lat_min, lat_max, lon_min, lon_max,
                max_hours,
            )
            logger.info("Using %s (%d frames)", name, result[2].shape[0])
            return result
        except ValueError as exc:
            # bbox outside this model's domain — try next resolution
            logger.info("%s bbox miss (%s), trying fallback", name, exc)
            last_exc = exc
            del u_da, v_da; gc.collect()
            continue

    raise RuntimeError(
        f"All meteofetch candidates failed for bbox "
        f"({lat_min:.2f}–{lat_max:.2f} N, {lon_min:.2f}–{lon_max:.2f} E). "
        f"Last error: {last_exc}"
    )


# ── local upload ───────────────────────────────────────────────────────────────

def _fetch_local_grib(
    lat_min: float, lat_max: float,
    lon_min: float, lon_max: float,
    file_path: str,
    max_hours: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[str], list[str]]:
    """Read U10/V10 from a user-uploaded GRIB file and clip to bbox."""
    u_da, v_da = _cfgrib_wind(file_path)
    return _clip_and_pack(u_da, v_da, lat_min, lat_max, lon_min, lon_max, max_hours)


# ── dispatcher ─────────────────────────────────────────────────────────────────

def _fetch_grib_grid(
    lat_min: float, lat_max: float,
    lon_min: float, lon_max: float,
    model_param: str = "knmi_seamless",
    max_hours: int = 48,
    center_lat: float | None = None,
    center_lon: float | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[str], list[str]]:
    """Fetch native-grid U10/V10 from the best available source for *model_param*.

    Returns (lats, lons, u_arr, v_arr, labels, times_utc).
    """
    source = _model_to_grib_source(model_param)
    logger.info("GRIB source for '%s' → %s", model_param, source)

    if source == "local_upload":
        file_path = model_param[len("local_upload:"):]
        return _fetch_local_grib(lat_min, lat_max, lon_min, lon_max, file_path, max_hours)

    if source == "harmonie_s3":
        return _fetch_harmonie_s3(lat_min, lat_max, lon_min, lon_max, max_hours)

    if source == "icon_d2_dwd":
        return _fetch_icon_d2_dwd(lat_min, lat_max, lon_min, lon_max, max_hours)

    if source == "openwrf":
        clat = center_lat if center_lat is not None else (lat_min + lat_max) / 2
        clon = center_lon if center_lon is not None else (lon_min + lon_max) / 2
        return _fetch_openwrf(clat, clon, lat_min, lat_max, lon_min, lon_max, max_hours)

    mf_model = "arpege025" if source == "arpege025" else "arome025"
    return _fetch_meteofetch(lat_min, lat_max, lon_min, lon_max, mf_model, max_hours)


# ── frame rendering ────────────────────────────────────────────────────────────

def _render_frame(
    lats:       np.ndarray,   # (Y,) ascending
    lons:       np.ndarray,   # (X,) ascending
    u_ms:       np.ndarray,   # (Y, X)  m/s eastward
    v_ms:       np.ndarray,   # (Y, X)  m/s northward
    basemap:    Image.Image,
    extent:     tuple[float, float, float, float],
    center_lat: float,
    center_lon: float,
    label:      str,
    barb_stride: int = 1,
    dpi: int = 96,
    fig_w_px: int = FIG_W_PX,
    fig_h_px: int = FIG_H_PX,
) -> Image.Image:
    lon_min, lon_max, lat_min, lat_max = extent

    fig, ax = plt.subplots(figsize=(fig_w_px / dpi, fig_h_px / dpi), dpi=dpi)

    # ── 1. OSM basemap ────────────────────────────────────────────────────────
    ax.imshow(
        basemap,
        extent=[lon_min, lon_max, lat_min, lat_max],
        origin="upper", aspect="auto", zorder=0,
    )

    # ── 2. Wind-speed shading — smooth pcolormesh with alpha fade ────────────
    speeds_kt = np.sqrt(u_ms**2 + v_ms**2) * MS_TO_KT
    speeds_kt = np.where(np.isfinite(speeds_kt), speeds_kt, 0.0)

    LON_MESH, LAT_MESH = np.meshgrid(lons, lats)

    ax.pcolormesh(
        LON_MESH, LAT_MESH, speeds_kt,
        cmap=CMAP_WIND, norm=NORM_WIND,
        shading="gouraud", zorder=1,
    )

    # ── 3. Wind barbs — subsampled to BARB_SPACING_DEG density ───────────────
    s = barb_stride
    lats_b = lats[::s]
    lons_b = lons[::s]
    u_b    = (u_ms * MS_TO_KT)[::s, ::s]
    v_b    = (v_ms * MS_TO_KT)[::s, ::s]
    LON_B, LAT_B = np.meshgrid(lons_b, lats_b)

    ax.barbs(
        LON_B, LAT_B, u_b, v_b,
        barb_increments=dict(half=5, full=10, flag=50),
        length=7,
        linewidth=0.85,
        barbcolor="#1e3a8a",
        flagcolor="#dc2626",
        pivot="middle",
        zorder=3,
    )

    # ── 4. Colorbar ───────────────────────────────────────────────────────────
    sm = plt.cm.ScalarMappable(cmap=CMAP_WIND, norm=NORM_WIND)
    sm.set_array([])
    cb = fig.colorbar(sm, ax=ax, fraction=0.026, pad=0.02)
    cb.set_label("Wind speed (kt)", fontsize=9)
    cb.set_ticks([0, 5, 10, 15, 20, 25, 30, 35])
    cb.ax.tick_params(labelsize=8)

    # ── 5. Centre marker ──────────────────────────────────────────────────────
    ax.plot(center_lon, center_lat,
            marker="x", color="#1d4ed8",
            markersize=11, markeredgewidth=2.2, zorder=4)

    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)
    ax.set_title(label, fontsize=11, fontweight="bold", pad=5)
    ax.tick_params(labelsize=8)
    ax.set_xlabel("Lon", fontsize=8)
    ax.set_ylabel("Lat", fontsize=8)

    fig.tight_layout(pad=0.5)
    buf = io.BytesIO()
    try:
        fig.savefig(buf, format="png", dpi=dpi)
    finally:
        plt.close(fig)
        del LON_MESH, LAT_MESH, speeds_kt
        gc.collect()
    buf.seek(0)
    return Image.open(buf).copy()


# ── GIF assembly ───────────────────────────────────────────────────────────────

def _to_gif(frames: list[Image.Image], duration_ms: int) -> bytes:
    if not frames:
        raise ValueError("No frames to encode")
    pal = [f.convert("P", palette=Image.ADAPTIVE, colors=256) for f in frames]
    buf = io.BytesIO()
    pal[0].save(buf, format="GIF",
                save_all=True, append_images=pal[1:],
                loop=0, duration=duration_ms, optimize=False)
    buf.seek(0)
    return buf.read()


# ── main entry point ───────────────────────────────────────────────────────────

def _filter_by_range(
    times_utc: list[str],
    labels: list[str],
    u_arr: np.ndarray,
    v_arr: np.ndarray,
    start_iso: str = "",
    end_iso: str = "",
) -> tuple[list[str], list[str], np.ndarray, np.ndarray]:
    """Return (times_utc, labels, u, v) filtered to [start_iso, end_iso]."""
    if not start_iso and not end_iso:
        return times_utc, labels, u_arr, v_arr
    start_dt = datetime.fromisoformat(start_iso.rstrip("Z")).replace(tzinfo=UTC) if start_iso else None
    end_dt   = datetime.fromisoformat(end_iso.rstrip("Z")).replace(tzinfo=UTC)   if end_iso   else None
    keep = []
    for i, t in enumerate(times_utc):
        if not t:
            keep.append(i)
            continue
        dt = datetime.fromisoformat(t.rstrip("Z")).replace(tzinfo=UTC)
        if start_dt and dt < start_dt:
            continue
        if end_dt and dt > end_dt:
            continue
        keep.append(i)
    if not keep:
        return times_utc, labels, u_arr, v_arr  # no match — return all
    return (
        [times_utc[i] for i in keep],
        [labels[i]    for i in keep],
        u_arr[keep],
        v_arr[keep],
    )


def generate_wind_gif(
    lat: float,
    lon: float,
    hours: int,
    endpoint_url: str = "",      # unused — kept for API compat with main.py
    model_param:  str = "",      # maps to meteofetch model name (see below)
    start_iso:    str = "",      # ISO8601 UTC — only include frames >= this time
    end_iso:      str = "",      # ISO8601 UTC — only include frames <= this time
) -> bytes:
    """Generate animated wind barb GIF from native Météo-France GRIB data.

    ``model_param`` is the Open-Meteo model ID; we map it to the matching
    meteofetch model:
      * ``meteofrance_arome_france_hd`` → arome025
      * anything else                   → arpege025  (global, always works)
    """
    import concurrent.futures

    lat_min, lat_max = lat - AREA_LAT_DEG, lat + AREA_LAT_DEG
    lon_min, lon_max = lon - AREA_LON_DEG, lon + AREA_LON_DEG

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        bmap_fut = pool.submit(_get_basemap, lat_min, lat_max, lon_min, lon_max)
        grid_fut = pool.submit(
            _fetch_grib_grid,
            lat_min, lat_max, lon_min, lon_max,
            model_param, hours, lat, lon,
        )
        basemap, extent = bmap_fut.result()
        lats, lons, u_arr, v_arr, labels, times_utc = grid_fut.result()

    times_utc, labels, u_arr, v_arr = _filter_by_range(
        times_utc, labels, u_arr, v_arr, start_iso, end_iso
    )

    # Compute barb stride so barb spacing ≈ BARB_SPACING_DEG
    lat_res = float(abs(lats[1] - lats[0])) if len(lats) > 1 else BARB_SPACING_DEG
    barb_stride = max(1, round(BARB_SPACING_DEG / lat_res))
    logger.info(
        "Grid %dx%d (%.3f°), barb stride=%d, %d frames",
        len(lats), len(lons), lat_res, barb_stride, len(labels),
    )

    frames: list[Image.Image] = []
    for t_idx, label in enumerate(labels):
        frames.append(_render_frame(
            lats, lons,
            u_arr[t_idx], v_arr[t_idx],
            basemap, extent,
            lat, lon, label,
            barb_stride=barb_stride,
        ))
    del u_arr, v_arr, basemap
    gc.collect()

    return _to_gif(frames, FRAME_MS)


def generate_wind_frames(
    lat: float,
    lon: float,
    hours: int,
    endpoint_url: str = "",
    model_param: str = "",
    step_hours: int = 3,
    start_iso: str = "",
    end_iso: str = "",
) -> list[dict]:
    """Return a list of {label, png_b64} dicts — one per ``step_hours`` timestep.

    Suitable for embedding as ``<img src="data:image/png;base64,...">`` in an HTML report.
    """
    import base64
    import concurrent.futures

    lat_min, lat_max = lat - AREA_LAT_DEG, lat + AREA_LAT_DEG
    lon_min, lon_max = lon - AREA_LON_DEG, lon + AREA_LON_DEG

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        bmap_fut = pool.submit(_get_basemap, lat_min, lat_max, lon_min, lon_max)
        grid_fut = pool.submit(
            _fetch_grib_grid,
            lat_min, lat_max, lon_min, lon_max,
            model_param, hours, lat, lon,
        )
        basemap, extent = bmap_fut.result()
        lats, lons, u_arr, v_arr, labels, times_utc = grid_fut.result()

    times_utc, labels, u_arr, v_arr = _filter_by_range(
        times_utc, labels, u_arr, v_arr, start_iso, end_iso
    )

    lat_res = float(abs(lats[1] - lats[0])) if len(lats) > 1 else BARB_SPACING_DEG
    barb_stride = max(1, round(BARB_SPACING_DEG / lat_res))

    FRAME_DPI    = 96
    FRAME_W_PX   = 900
    FRAME_H_PX   = 680

    result: list[dict] = []
    for t_idx, (label, time_utc) in enumerate(zip(labels, times_utc)):
        if t_idx % max(1, step_hours) != 0:
            continue
        img = _render_frame(
            lats, lons,
            u_arr[t_idx], v_arr[t_idx],
            basemap, extent,
            lat, lon, label,
            barb_stride=barb_stride,
            dpi=FRAME_DPI,
            fig_w_px=FRAME_W_PX,
            fig_h_px=FRAME_H_PX,
        )
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        png_b64 = base64.b64encode(buf.read()).decode()
        buf.close()
        del img
        result.append({"label": label, "time_utc": time_utc, "png_b64": png_b64})
        gc.collect()

    del u_arr, v_arr, basemap
    gc.collect()
    return result
