"""Wind map GIF generator.

Fetches a 7×7 grid of 10-m wind forecasts from Open-Meteo (single batch
request), renders each forecast hour as a matplotlib frame on an
OpenStreetMap basemap, and encodes the result as an animated GIF.

No eccodes / GRIB2 dependency — uses only matplotlib, Pillow, numpy and
httpx (already in the project).

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
matplotlib.use("Agg")               # non-interactive backend, set before other matplotlib imports
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import numpy as np
from PIL import Image

logger = logging.getLogger("wind_validation.windmap")

# ── grid & display constants ──────────────────────────────────────────────────
GRID_N       = 7        # N×N forecast points
LAT_SPACING  = 0.25     # degrees between rows   (~28 km)
LON_SPACING  = 0.30     # degrees between columns (~21 km at 52°N)
OSM_ZOOM     = 8
TILE_PX      = 256
FRAME_MS     = 600      # ms per GIF frame
MAX_WS_MS    = 18.0     # m/s ≈ 35 kt  — top of colour scale
MS_TO_KT     = 1.943844
OSM_UA       = "wind-validation/1.0"
FIG_W_PX     = 860
FIG_H_PX     = 640


# ── OSM tile helpers ──────────────────────────────────────────────────────────

def _tile_xy(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    r = math.radians(lat)
    y = int((1.0 - math.log(math.tan(r) + 1.0 / math.cos(r)) / math.pi) / 2.0 * n)
    return x, y


def _tile_nw(x: int, y: int, zoom: int) -> tuple[float, float]:
    """NW corner (lat, lon) of tile (x, y, zoom)."""
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
    """Stitch OSM tiles covering the bbox.

    Returns ``(image, (lon_min, lon_max, lat_min, lat_max))`` extent.
    """
    x0, y0 = _tile_xy(lat_max, lon_min, zoom)   # top-left  (y increases south)
    x1, y1 = _tile_xy(lat_min, lon_max, zoom)   # bottom-right

    cols, rows = x1 - x0 + 1, y1 - y0 + 1
    canvas = Image.new("RGB", (cols * TILE_PX, rows * TILE_PX), (220, 220, 220))

    tile_list = [(x0 + c, y0 + r) for r in range(rows) for c in range(cols)]
    with ThreadPoolExecutor(max_workers=min(len(tile_list), 9)) as pool:
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
    return canvas, (lon_nw, lon_se, lat_se, lat_nw)  # (lon_min, lon_max, lat_min, lat_max)


# ── Open-Meteo wind grid fetch ────────────────────────────────────────────────

def _fetch_openmeteo_wind_grid(
    coords: list[tuple[float, float]],
    hours: int,
    endpoint_url: str,
    model_param: str,
) -> list[dict]:
    """Fetch wind_speed_10m + wind_direction_10m for all grid coords in one call.

    Open-Meteo accepts comma-separated lat/lon lists and returns a JSON array,
    one element per location in the same order.
    """
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

def _wd_to_uv(speed: float, direction_deg: float) -> tuple[float, float]:
    """Meteorological wind direction → (u_east, v_north) pointing in travel direction."""
    rad = math.radians(direction_deg)
    return -speed * math.sin(rad), -speed * math.cos(rad)


def _render_frame(
    grid_lats: list[float],
    grid_lons: list[float],
    speeds: list[float | None],
    dirs: list[float | None],
    basemap: Image.Image,
    extent: tuple[float, float, float, float],
    center_lat: float,
    center_lon: float,
    label: str,
) -> Image.Image:
    lon_min, lon_max, lat_min, lat_max = extent
    lon_range = lon_max - lon_min
    lat_range = lat_max - lat_min
    dpi = 96

    fig, ax = plt.subplots(figsize=(FIG_W_PX / dpi, FIG_H_PX / dpi), dpi=dpi)

    # Basemap (origin='upper': OSM tiles run top-to-bottom)
    ax.imshow(
        basemap,
        extent=[lon_min, lon_max, lat_min, lat_max],
        origin="upper",
        aspect="auto",
        zorder=0,
    )

    # Build valid arrow arrays
    xs, ys, u_raw, vs, cs = [], [], [], [], []
    for lat, lon, ws, wd in zip(grid_lats, grid_lons, speeds, dirs):
        if ws is None or wd is None:
            continue
        u, v = _wd_to_uv(ws, wd)
        xs.append(lon)
        ys.append(lat)
        u_raw.append(u)
        vs.append(v)
        cs.append(ws)

    norm = mcolors.Normalize(vmin=0, vmax=MAX_WS_MS)
    cmap = plt.get_cmap("YlOrRd")

    if xs:
        # Correct U so arrows point in the true compass direction on an
        # equirectangular map.  At latitude φ, 1° lon = cos(φ) × 1° lat in km.
        lat_c = math.radians((lat_min + lat_max) / 2)
        u_corr = (FIG_H_PX / FIG_W_PX) * (lon_range / lat_range) / math.cos(lat_c)
        us = [u * u_corr for u in u_raw]

        # Scale: MAX_WS_MS arrow spans ~60 % of grid spacing (lat degrees)
        arrow_scale = MAX_WS_MS / (LAT_SPACING * 0.60)

        ax.quiver(
            xs, ys, us, vs, cs,
            cmap=cmap, norm=norm,
            scale=arrow_scale, scale_units="xy",
            width=0.004, headwidth=5, headlength=6,
            alpha=0.88, zorder=2,
        )

    # Colorbar labelled in knots
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    cb = fig.colorbar(sm, ax=ax, fraction=0.028, pad=0.02)
    cb.set_label("Wind speed (kt)", fontsize=9)
    kt_targets = [0, 5, 10, 15, 20, 25, 30, 35]
    ms_ticks = [k / MS_TO_KT for k in kt_targets if k / MS_TO_KT <= MAX_WS_MS * 1.05]
    cb.set_ticks(ms_ticks)
    cb.set_ticklabels([f"{t * MS_TO_KT:.0f}" for t in ms_ticks], fontsize=8)

    # Centre location marker
    ax.plot(center_lon, center_lat,
            marker="x", color="#1d4ed8",
            markersize=11, markeredgewidth=2.2, zorder=3)

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
    """Generate an animated GIF of 10-m wind vectors on an OSM basemap.

    Parameters
    ----------
    lat, lon      : Centre of the map in decimal degrees.
    hours         : Number of forecast hours (capped at 120).
    endpoint_url  : Open-Meteo forecast API URL for the chosen model.
    model_param   : Open-Meteo ``models=`` parameter value.

    Returns
    -------
    bytes  Raw GIF file contents.
    """
    import concurrent.futures

    half = GRID_N // 2

    coords: list[tuple[float, float]] = [
        (round(lat + (i - half) * LAT_SPACING, 4),
         round(lon + (j - half) * LON_SPACING, 4))
        for i in range(GRID_N)
        for j in range(GRID_N)
    ]
    grid_lats = [c[0] for c in coords]
    grid_lons = [c[1] for c in coords]

    # Basemap extent (slightly wider than the arrow grid)
    pad_lat = (half + 0.5) * LAT_SPACING
    pad_lon = (half + 0.5) * LON_SPACING

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        bmap_fut = pool.submit(
            _get_basemap,
            lat - pad_lat, lat + pad_lat,
            lon - pad_lon, lon + pad_lon,
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
        speeds_t: list[float | None] = []
        dirs_t:   list[float | None] = []

        for pl in wind_payloads:
            h = pl.get("hourly", {})
            ws_list = h.get("wind_speed_10m", [])
            wd_list = h.get("wind_direction_10m", [])
            try:
                ws = float(ws_list[t_idx]) if t_idx < len(ws_list) and ws_list[t_idx] is not None else None
                wd = float(wd_list[t_idx]) if t_idx < len(wd_list) and wd_list[t_idx] is not None else None
            except (TypeError, ValueError):
                ws, wd = None, None
            speeds_t.append(ws)
            dirs_t.append(wd)

        try:
            dt = datetime.fromisoformat(t_raw).replace(tzinfo=UTC)
            label = dt.strftime("%d %b  %H:%M UTC")
        except ValueError:
            label = t_raw

        frames.append(_render_frame(
            grid_lats, grid_lons,
            speeds_t, dirs_t,
            basemap, extent,
            lat, lon, label,
        ))

    return _to_gif(frames, FRAME_MS)
