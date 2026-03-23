"""Wind map GIF generator.

Fetches a 7×7 grid of 10-m wind forecasts from Open-Meteo (single batch
request), renders each forecast hour as a matplotlib frame with:

  • OpenStreetMap basemap
  • Smooth wind-speed shading (pcolormesh, semi-transparent)
  • Meteorological wind barbs (half=5 kt, full=10 kt, flag=50 kt)

No eccodes / GRIB2 dependency — uses only matplotlib, Pillow, numpy and
httpx (already in the project).

Runs **synchronously** — call via ``asyncio.to_thread()`` from async endpoints.
"""
from __future__ import annotations

import io
import math
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

import httpx
import matplotlib
matplotlib.use("Agg")               # non-interactive backend — must be set first
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import numpy as np
from PIL import Image

logger = logging.getLogger("wind_validation.windmap")

# ── constants ─────────────────────────────────────────────────────────────────
GRID_N      = 7        # N × N forecast points
LAT_SPACING = 0.25     # degrees between rows   (~28 km)
LON_SPACING = 0.30     # degrees between columns (~21 km at 52°N)
AREA_DEG    = 1.1      # half-extent of map in both lat and lon degrees
OSM_ZOOM    = 9        # higher zoom = more road/coast detail
TILE_PX     = 256
FRAME_MS    = 600      # ms per GIF frame
MAX_WS_KT   = 35.0     # top of colour scale (knots)
MS_TO_KT    = 1.943844
OSM_UA      = "wind-validation/1.0"
FIG_W_PX    = 900
FIG_H_PX    = 680


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
    x0, y0 = _tile_xy(lat_max, lon_min, zoom)   # top-left  (y increases southward)
    x1, y1 = _tile_xy(lat_min, lon_max, zoom)   # bottom-right

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
    return canvas, (lon_nw, lon_se, lat_se, lat_nw)  # lon_min, lon_max, lat_min, lat_max


# ── Open-Meteo wind grid fetch ────────────────────────────────────────────────

def _fetch_openmeteo_wind_grid(
    coords: list[tuple[float, float]],
    hours: int,
    endpoint_url: str,
    model_param: str,
) -> list[dict]:
    """One batch request for all grid points; returns list of hourly payloads."""
    params: dict = {
        "latitude":  ",".join(str(lat) for lat, _ in coords),
        "longitude": ",".join(str(lon) for _, lon in coords),
        "hourly":    "wind_speed_10m,wind_direction_10m",
        "wind_speed_unit": "ms",
        "forecast_hours": min(hours, 120),
        "timezone": "UTC",
    }
    if model_param:
        params["models"] = model_param

    with httpx.Client(timeout=30) as c:
        resp = c.get(endpoint_url, params=params)
        resp.raise_for_status()

    payload = resp.json()
    if not isinstance(payload, list):
        payload = [payload]
    return payload


# ── frame rendering ────────────────────────────────────────────────────────────

def _render_frame(
    lats_unique: np.ndarray,    # (GRID_N,)  ascending
    lons_unique: np.ndarray,    # (GRID_N,)  ascending
    speeds_ms:   np.ndarray,    # (GRID_N, GRID_N)  m/s, row=lat col=lon
    dirs_deg:    np.ndarray,    # (GRID_N, GRID_N)  meteorological FROM direction
    basemap:     Image.Image,
    extent:      tuple[float, float, float, float],  # lon_min, lon_max, lat_min, lat_max
    center_lat:  float,
    center_lon:  float,
    label:       str,
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

    # ── 2. Wind-speed shading (pcolormesh, semi-transparent) ──────────────────
    speeds_kt = speeds_ms * MS_TO_KT
    # Replace NaN/masked values with 0 for clean plotting
    speeds_kt_clean = np.where(np.isfinite(speeds_kt), speeds_kt, 0.0)

    LON_MESH, LAT_MESH = np.meshgrid(lons_unique, lats_unique)  # (GRID_N, GRID_N)
    norm_shade = mcolors.Normalize(vmin=0, vmax=MAX_WS_KT)
    cmap_shade = plt.get_cmap("YlOrRd")

    ax.pcolormesh(
        LON_MESH, LAT_MESH, speeds_kt_clean,
        cmap=cmap_shade, norm=norm_shade,
        alpha=0.42, shading="gouraud", zorder=1,
    )

    # ── 3. Wind barbs ─────────────────────────────────────────────────────────
    # Convert m/s → kt, direction → (u, v) components in kt
    rad = np.radians(dirs_deg)
    u_kt = -speeds_kt * np.sin(rad)   # eastward  (travel direction)
    v_kt = -speeds_kt * np.cos(rad)   # northward

    # Mask points where data is missing
    valid = np.isfinite(speeds_ms) & np.isfinite(dirs_deg)
    u_plot = np.where(valid, u_kt, np.nan)
    v_plot = np.where(valid, v_kt, np.nan)

    ax.barbs(
        LON_MESH, LAT_MESH, u_plot, v_plot,
        barb_increments=dict(half=5, full=10, flag=50),
        length=7,
        linewidth=0.85,
        barbcolor="#1e3a8a",
        flagcolor="#dc2626",
        pivot="middle",
        zorder=3,
    )

    # ── 4. Colorbar (knots) ───────────────────────────────────────────────────
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
        raise ValueError("No frames to encode into GIF")
    palette_frames = [
        f.convert("P", palette=Image.ADAPTIVE, colors=256) for f in frames
    ]
    buf = io.BytesIO()
    palette_frames[0].save(
        buf, format="GIF",
        save_all=True, append_images=palette_frames[1:],
        loop=0, duration=duration_ms,
        optimize=False,
    )
    buf.seek(0)
    return buf.read()


# ── main entry point ───────────────────────────────────────────────────────────

def generate_wind_gif(
    lat: float,
    lon: float,
    hours: int,
    endpoint_url: str,
    model_param: str,
) -> bytes:
    """Generate an animated GIF with wind barbs and speed shading on an OSM basemap.

    Parameters
    ----------
    lat, lon      : Centre of the map in decimal degrees.
    hours         : Number of forecast hours (capped at 120).
    endpoint_url  : Open-Meteo forecast API URL for the chosen model.
    model_param   : Open-Meteo ``models=`` parameter value.
    """
    import concurrent.futures

    half = GRID_N // 2

    # Unique lat/lon axes of the grid (ascending)
    lats_unique = np.array([round(lat + (i - half) * LAT_SPACING, 4) for i in range(GRID_N)])
    lons_unique = np.array([round(lon + (j - half) * LON_SPACING, 4) for j in range(GRID_N)])

    # Flat list of (lat, lon) in row-major order (lat outer, lon inner)
    coords: list[tuple[float, float]] = [
        (float(la), float(lo)) for la in lats_unique for lo in lons_unique
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        bmap_fut = pool.submit(
            _get_basemap,
            lat - AREA_DEG, lat + AREA_DEG,
            lon - AREA_DEG, lon + AREA_DEG,
        )
        wind_fut = pool.submit(
            _fetch_openmeteo_wind_grid,
            coords, hours, endpoint_url, model_param,
        )
        basemap, extent = bmap_fut.result()
        wind_payloads = wind_fut.result()

    if not wind_payloads:
        raise ValueError("No wind data returned from Open-Meteo")

    times = wind_payloads[0].get("hourly", {}).get("time", [])
    if not times:
        raise ValueError("Empty time series from Open-Meteo")

    logger.info("Rendering %d frames for %.4fN %.4fE…", len(times), lat, lon)

    frames: list[Image.Image] = []
    for t_idx, t_raw in enumerate(times):
        # Extract flat speed+direction for all grid points at this time step
        speeds_flat = []
        dirs_flat   = []
        for pl in wind_payloads:
            h = pl.get("hourly", {})
            ws_list = h.get("wind_speed_10m", [])
            wd_list = h.get("wind_direction_10m", [])
            try:
                ws = float(ws_list[t_idx]) if t_idx < len(ws_list) and ws_list[t_idx] is not None else float("nan")
                wd = float(wd_list[t_idx]) if t_idx < len(wd_list) and wd_list[t_idx] is not None else float("nan")
            except (TypeError, ValueError):
                ws, wd = float("nan"), float("nan")
            speeds_flat.append(ws)
            dirs_flat.append(wd)

        # Reshape to (GRID_N, GRID_N) — row = lat index, col = lon index
        speeds_grid = np.array(speeds_flat, dtype=float).reshape(GRID_N, GRID_N)
        dirs_grid   = np.array(dirs_flat,   dtype=float).reshape(GRID_N, GRID_N)

        try:
            dt    = datetime.fromisoformat(t_raw).replace(tzinfo=UTC)
            label = dt.strftime("%d %b  %H:%M UTC")
        except ValueError:
            label = t_raw

        frames.append(_render_frame(
            lats_unique, lons_unique,
            speeds_grid, dirs_grid,
            basemap, extent,
            lat, lon, label,
        ))

    return _to_gif(frames, FRAME_MS)
