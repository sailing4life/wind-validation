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
OSM_UA    = "wind-validation/1.0"
FIG_W_PX  = 900
FIG_H_PX  = 680

# Barb density target — AROME025 (0.025°) needs stride=2 → one barb per ~5 km
BARB_SPACING_DEG = 0.05


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
    url = f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png"
    with httpx.Client(headers={"User-Agent": OSM_UA}, timeout=15) as c:
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


# ── meteofetch GRIB fetch ─────────────────────────────────────────────────────

def _np_dt_to_label(t) -> str:
    try:
        ts_s = int(np.datetime64(t, "s").astype("int64"))
        dt = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=ts_s)
        return dt.strftime("%d %b  %H:%M UTC")
    except Exception:
        return str(t)[:16]


def _fetch_grib_grid(
    lat_min: float, lat_max: float,
    lon_min: float, lon_max: float,
    model: str = "arpege025",
    max_hours: int = 48,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[str]]:
    """Fetch native-grid U10 / V10 via meteofetch.

    Returns
    -------
    lats      : (Y,)  ascending degrees north
    lons      : (X,)  ascending degrees east
    u10       : (T, Y, X)  m/s eastward
    v10       : (T, Y, X)  m/s northward
    labels    : list[str]  time label per frame
    """
    try:
        if model == "arome025":
            from meteofetch import Arome0025 as Model  # noqa: PLC0415
        else:
            from meteofetch import Arpege025 as Model  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            f"meteofetch import failed: {exc}\n"
            "Ensure meteofetch is installed and the eccodes C library is available.\n"
            "macOS: brew install eccodes && pip install meteofetch\n"
            "Linux: apt-get install libeccodes-dev && pip install meteofetch"
        ) from exc

    logger.info("Fetching %s GRIB (SP2) via meteofetch…", model)
    try:
        ds = Model.get_latest_forecast(paquet="SP2")
    except Exception as exc:
        raise RuntimeError(f"meteofetch download failed: {exc}") from exc

    if "u10" not in ds or "v10" not in ds:
        raise ValueError(
            f"u10/v10 absent from {model} SP2. Keys present: {list(ds.keys())}"
        )

    u_da = ds["u10"]
    v_da = ds["v10"]

    # Sort so latitude is ascending (some models deliver north→south)
    u_da = u_da.sortby("latitude")
    v_da = v_da.sortby("latitude")

    lats_all = u_da.latitude.values
    lons_all = u_da.longitude.values

    # Normalise longitudes from 0–360 to -180–180 if needed
    if lons_all.max() > 180:
        lons_all = np.where(lons_all > 180, lons_all - 360, lons_all)

    lat_mask = (lats_all >= lat_min) & (lats_all <= lat_max)
    lon_mask = (lons_all >= lon_min) & (lons_all <= lon_max)

    if not lat_mask.any() or not lon_mask.any():
        raise ValueError(
            f"No {model} data within "
            f"({lat_min:.1f}–{lat_max:.1f} N, {lon_min:.1f}–{lon_max:.1f} E). "
            "Try model='arpege025' for global coverage."
        )

    u_clip = u_da.isel(latitude=lat_mask, longitude=lon_mask)
    v_clip = v_da.isel(latitude=lat_mask, longitude=lon_mask)

    lats = u_clip.latitude.values
    lons = u_clip.longitude.values
    u_arr = np.array(u_clip.values, dtype=float)
    v_arr = np.array(v_clip.values, dtype=float)

    if u_arr.ndim == 2:
        u_arr = u_arr[np.newaxis]
        v_arr = v_arr[np.newaxis]

    t_vals = u_clip.time.values[:max_hours]
    u_arr  = u_arr[:max_hours]
    v_arr  = v_arr[:max_hours]
    labels = [_np_dt_to_label(t) for t in t_vals]

    return lats, lons, u_arr, v_arr, labels


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
) -> Image.Image:
    lon_min, lon_max, lat_min, lat_max = extent
    dpi = 96

    fig, ax = plt.subplots(figsize=(FIG_W_PX / dpi, FIG_H_PX / dpi), dpi=dpi)

    # ── 1. OSM basemap ────────────────────────────────────────────────────────
    ax.imshow(
        basemap,
        extent=[lon_min, lon_max, lat_min, lat_max],
        origin="upper", aspect="auto", zorder=0,
    )

    # ── 2. Wind-speed shading — full native resolution ─────────────────────
    speeds_kt = np.sqrt(u_ms**2 + v_ms**2) * MS_TO_KT
    speeds_kt = np.where(np.isfinite(speeds_kt), speeds_kt, 0.0)

    LON_MESH, LAT_MESH = np.meshgrid(lons, lats)
    norm_shade = mcolors.Normalize(vmin=0, vmax=MAX_WS_KT)
    cmap_shade = plt.get_cmap("YlOrRd")

    ax.pcolormesh(
        LON_MESH, LAT_MESH, speeds_kt,
        cmap=cmap_shade, norm=norm_shade,
        alpha=0.42, shading="gouraud", zorder=1,
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
    sm = plt.cm.ScalarMappable(cmap=cmap_shade, norm=norm_shade)
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
    fig.savefig(buf, format="png", dpi=dpi)
    plt.close(fig)
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

def generate_wind_gif(
    lat: float,
    lon: float,
    hours: int,
    endpoint_url: str = "",      # unused — kept for API compat with main.py
    model_param:  str = "",      # maps to meteofetch model name (see below)
) -> bytes:
    """Generate animated wind barb GIF from native Météo-France GRIB data.

    ``model_param`` is the Open-Meteo model ID; we map it to the matching
    meteofetch model:
      * ``meteofrance_arome_france_hd`` → arome025
      * anything else                   → arpege025  (global, always works)
    """
    import concurrent.futures

    # Map Open-Meteo model param → meteofetch class name.
    # At 50 km scale ARPEGE (0.25°) only has 2–3 points in the bbox — useless.
    # Default to AROME025 (0.025°, ~5 km); fall back to ARPEGE for non-EU locations.
    if "arpege" in model_param.lower() or "ecmwf" in model_param.lower():
        mf_model = "arpege025"
    else:
        mf_model = "arome025"

    lat_min, lat_max = lat - AREA_LAT_DEG, lat + AREA_LAT_DEG
    lon_min, lon_max = lon - AREA_LON_DEG, lon + AREA_LON_DEG

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        bmap_fut = pool.submit(_get_basemap, lat_min, lat_max, lon_min, lon_max)
        grid_fut = pool.submit(_fetch_grib_grid,
                               lat_min, lat_max, lon_min, lon_max,
                               mf_model, hours)
        basemap, extent = bmap_fut.result()
        lats, lons, u_arr, v_arr, labels = grid_fut.result()

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

    return _to_gif(frames, FRAME_MS)
