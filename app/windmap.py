"""Wind map GIF generator.

Fetches gridded 10-m wind (U10 / V10) from Météo-France via the *meteofetch*
package, renders each forecast hour as a matplotlib frame with an
OpenStreetMap basemap, and encodes the result as an animated GIF.

Runs **synchronously** — call via ``asyncio.to_thread()`` from async endpoints.

Requirements (in addition to the project base deps):
    pip install meteofetch matplotlib Pillow numpy
On macOS you may also need:  brew install eccodes
"""
from __future__ import annotations

import io
import math
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta

import httpx
import matplotlib
matplotlib.use("Agg")                   # non-interactive backend, must be set first
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import numpy as np
from PIL import Image

logger = logging.getLogger("wind_validation.windmap")

# ── constants ─────────────────────────────────────────────────────────────────
OSM_ZOOM   = 8
TILE_PX    = 256
FRAME_MS   = 600            # ms per GIF frame
MAX_WS_MS  = 18.0           # m/s ≈ 35 kt  — top of colour scale
MS_TO_KT   = 1.943844
OSM_UA     = "wind-validation/1.0"
AREA_DEG   = 1.75           # half-extent of map in degrees (lat & lon each side)


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
    """Download and stitch OSM tiles covering the bbox.

    Returns ``(image, (lon_min, lon_max, lat_min, lat_max))`` where the extent
    matches the stitched canvas so it can be passed directly to Axes.imshow.
    """
    x0, y0 = _tile_xy(lat_max, lon_min, zoom)   # top-left  (y inc. southward)
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
    return canvas, (lon_nw, lon_se, lat_se, lat_nw)   # (lon_min, lon_max, lat_min, lat_max)


# ── meteofetch wind data ───────────────────────────────────────────────────────

def _np_dt_to_label(t) -> str:
    """Convert a numpy datetime64 value to a human-readable UTC string."""
    try:
        ts_s = int(np.datetime64(t, "s").astype("int64"))
        dt = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=ts_s)
        return dt.strftime("%d %b  %H:%M UTC")
    except Exception:
        return str(t)[:16]


def _fetch_meteofrance_grid(
    lat_min: float, lat_max: float,
    lon_min: float, lon_max: float,
    model: str = "arpege025",
    max_hours: int = 48,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[str]]:
    """Fetch gridded U10 / V10 from Météo-France via meteofetch.

    Returns ``(lats, lons, u10, v10, time_labels)`` where
    ``u10`` and ``v10`` are shaped ``(T, Y, X)``.

    Supported *model* values: ``"arpege025"`` (global, 0.25°) or
    ``"arome025"`` (France + neighbours, 0.025°).
    """
    try:
        if model == "arome025":
            from meteofetch import Arome025 as Model  # noqa: PLC0415
        else:
            from meteofetch import Arpege025 as Model  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "meteofetch is not installed. Install it with:\n"
            "  pip install meteofetch\n"
            "On macOS you may also need:  brew install eccodes"
        ) from exc

    logger.info("Fetching %s gridded wind via meteofetch (paquet=SP2)…", model)
    try:
        ds = Model.get_latest_forecast(paquet="SP2")
    except Exception as exc:
        raise RuntimeError(f"meteofetch forecast fetch failed: {exc}") from exc

    if "u10" not in ds or "v10" not in ds:
        raise ValueError(
            f"u10/v10 not found in {model} SP2 dataset. "
            f"Available keys: {list(ds.keys())}"
        )

    u_all: object = ds["u10"]
    v_all: object = ds["v10"]

    lats_all = u_all.latitude.values    # type: ignore[attr-defined]
    lons_all = u_all.longitude.values   # type: ignore[attr-defined]

    # Longitude: wrap if needed (some models use 0–360 instead of -180–180)
    if lons_all.max() > 180:
        lons_all = np.where(lons_all > 180, lons_all - 360, lons_all)

    lat_mask = (lats_all >= lat_min) & (lats_all <= lat_max)
    lon_mask = (lons_all >= lon_min) & (lons_all <= lon_max)

    if not lat_mask.any() or not lon_mask.any():
        raise ValueError(
            f"Location ({lat_min:.1f}–{lat_max:.1f} N, "
            f"{lon_min:.1f}–{lon_max:.1f} E) not covered by {model}. "
            "Try model='arpege025' for global coverage."
        )

    u_clip = u_all.isel(latitude=lat_mask, longitude=lon_mask)  # type: ignore[attr-defined]
    v_clip = v_all.isel(latitude=lat_mask, longitude=lon_mask)  # type: ignore[attr-defined]

    lats = u_clip.latitude.values   # type: ignore[attr-defined]
    lons = u_clip.longitude.values  # type: ignore[attr-defined]
    u_arr = np.array(u_clip.values, dtype=float)
    v_arr = np.array(v_clip.values, dtype=float)

    # Ensure (T, Y, X)
    if u_arr.ndim == 2:
        u_arr = u_arr[np.newaxis]
        v_arr = v_arr[np.newaxis]

    # Trim to requested hours
    t_vals = u_clip.time.values  # type: ignore[attr-defined]
    n_steps = min(len(t_vals), max_hours)
    u_arr = u_arr[:n_steps]
    v_arr = v_arr[:n_steps]
    t_vals = t_vals[:n_steps]

    time_labels = [_np_dt_to_label(t) for t in t_vals]
    return lats, lons, u_arr, v_arr, time_labels


# ── frame rendering ────────────────────────────────────────────────────────────

FIG_W_PX = 860
FIG_H_PX = 640


def _render_frame(
    lats: np.ndarray,
    lons: np.ndarray,
    u_grid: np.ndarray,
    v_grid: np.ndarray,
    basemap: Image.Image,
    extent: tuple[float, float, float, float],
    center_lat: float,
    center_lon: float,
    label: str,
    stride: int = 1,
) -> Image.Image:
    """Render one time-step as a PIL Image.

    *stride* sub-samples the grid so dense models (AROME 0.025°) don't
    produce unreadably crowded arrows.
    """
    lon_min, lon_max, lat_min, lat_max = extent
    lon_range = lon_max - lon_min
    lat_range = lat_max - lat_min
    dpi = 96

    fig, ax = plt.subplots(figsize=(FIG_W_PX / dpi, FIG_H_PX / dpi), dpi=dpi)

    # Basemap (origin='upper' because OSM tiles run top-down)
    ax.imshow(
        basemap,
        extent=[lon_min, lon_max, lat_min, lat_max],
        origin="upper",
        aspect="auto",
        zorder=0,
    )

    # Sub-sample grid if needed
    lats_s = lats[::stride]
    lons_s = lons[::stride]
    u_s = u_grid[::stride, ::stride]
    v_s = v_grid[::stride, ::stride]

    LON_GRID, LAT_GRID = np.meshgrid(lons_s, lats_s)
    speeds = np.sqrt(u_s ** 2 + v_s ** 2)

    # ── arrow direction correction for equirectangular projection ────────────
    # At latitude φ, 1° lon is cos(φ) × 1° lat in km. We compensate so arrows
    # point in the true compass direction on screen.
    lat_c = math.radians((lat_min + lat_max) / 2)
    # Factor that scales U (degrees-lon) relative to V (degrees-lat) on screen:
    #   screen_pixels_per_deg_lon × u_corrected = screen_pixels_per_deg_lat × v
    u_corr = (FIG_H_PX / FIG_W_PX) * (lon_range / lat_range) / math.cos(lat_c)
    u_display = u_s * u_corr

    # Arrow scale: MAX_WS_MS wind spans ~60 % of grid spacing in lat degrees
    dy = abs(lats_s[1] - lats_s[0]) if len(lats_s) > 1 else 0.25
    arrow_scale = MAX_WS_MS / (dy * 0.60)

    norm = mcolors.Normalize(vmin=0, vmax=MAX_WS_MS)
    cmap = plt.get_cmap("YlOrRd")

    ax.quiver(
        LON_GRID, LAT_GRID,
        u_display, v_s,
        speeds,
        cmap=cmap, norm=norm,
        scale=arrow_scale, scale_units="xy",
        width=0.004, headwidth=5, headlength=6,
        alpha=0.88, zorder=2,
    )

    # Colorbar in knots
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    cb = fig.colorbar(sm, ax=ax, fraction=0.028, pad=0.02)
    cb.set_label("Wind speed (kt)", fontsize=9)
    kt_targets = [0, 5, 10, 15, 20, 25, 30, 35]
    ms_ticks = [k / MS_TO_KT for k in kt_targets if k / MS_TO_KT <= MAX_WS_MS * 1.05]
    cb.set_ticks(ms_ticks)
    cb.set_ticklabels([f"{t * MS_TO_KT:.0f}" for t in ms_ticks], fontsize=8)

    # Centre cross
    ax.plot(
        center_lon, center_lat,
        marker="x", color="#1d4ed8",
        markersize=11, markeredgewidth=2.2, zorder=3,
    )

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
        buf,
        format="GIF",
        save_all=True,
        append_images=palette_frames[1:],
        loop=0,
        duration=duration_ms,
        optimize=False,   # optimize=True can corrupt palette frames
    )
    buf.seek(0)
    return buf.read()


# ── main entry point ───────────────────────────────────────────────────────────

def generate_wind_gif(
    lat: float,
    lon: float,
    hours: int = 48,
    model: str = "arpege025",
) -> bytes:
    """Generate an animated GIF of 10-m wind vectors on an OSM basemap.

    Parameters
    ----------
    lat, lon : float
        Centre of the map in decimal degrees.
    hours : int
        Number of forecast hours to include (capped at model limit).
    model : str
        ``"arpege025"`` (global, 0.25°, good for North Sea / NL) or
        ``"arome025"`` (0.025°, France + neighbours, higher resolution).

    Returns
    -------
    bytes
        Raw GIF file contents.
    """
    import concurrent.futures  # local to keep top-level imports light

    lat_min = lat - AREA_DEG
    lat_max = lat + AREA_DEG
    lon_min = lon - AREA_DEG
    lon_max = lon + AREA_DEG

    # Fetch basemap tiles and wind data in parallel threads
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        bmap_fut = pool.submit(_get_basemap, lat_min, lat_max, lon_min, lon_max)
        wind_fut = pool.submit(
            _fetch_meteofrance_grid,
            lat_min, lat_max, lon_min, lon_max,
            model, hours,
        )
        basemap, extent = bmap_fut.result()
        lats, lons, u_arr, v_arr, time_labels = wind_fut.result()

    logger.info(
        "Rendering %d frames for %.4fN %.4fE (%s)…",
        len(time_labels), lat, lon, model,
    )

    # For high-res AROME (0.025°) sub-sample so arrows aren't too crowded
    grid_spacing_lat = abs(lats[1] - lats[0]) if len(lats) > 1 else 0.25
    stride = max(1, round(0.25 / grid_spacing_lat))   # target ~0.25° arrow spacing

    frames: list[Image.Image] = []
    for t_idx, label in enumerate(time_labels):
        frame = _render_frame(
            lats=lats,
            lons=lons,
            u_grid=u_arr[t_idx],
            v_grid=v_arr[t_idx],
            basemap=basemap,
            extent=extent,
            center_lat=lat,
            center_lon=lon,
            label=label,
            stride=stride,
        )
        frames.append(frame)

    return _to_gif(frames, FRAME_MS)
