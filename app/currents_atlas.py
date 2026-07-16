"""Tidal current atlas — SHOM C3D maps for the Pornichet racing area.

Renders the 13 hourly maps (PM-6 .. PM+6 relative to high water at
Saint-Nazaire) for spring (VE) and neap (ME) tides as PNGs with current
arrows in knots over an OSM basemap. Data is prepared offline by
scripts/prepare_shom_currents.py into app/data/shom_currents_pornichet.json.gz.
"""
from __future__ import annotations

import gzip
import io
import json
import logging
import math
import threading
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import numpy as np

logger = logging.getLogger("wind_validation.currents_atlas")

MS_TO_KT = 1.94384
DATA_PATH = Path(__file__).resolve().parent / "data" / "shom_currents_pornichet.json.gz"

# Arrow decimation: one arrow per grid cell of this size (degrees)
CELL_DEG = 0.012

# Arrow length is clipped here so channel hotspots (e.g. the Le Pouliguen
# entrance at 6+ kt) don't dominate the map; colour still shows true speed
ARROW_MAX_KT = 2.5

_lock = threading.Lock()
_data: dict | None = None
_png_cache: dict[tuple[str, int], bytes] = {}
_basemap_cache: tuple | None = None


def _load() -> dict:
    global _data
    with _lock:
        if _data is None:
            with gzip.open(DATA_PATH, "rt", encoding="utf-8") as f:
                raw = json.load(f)
            lat = np.asarray(raw["lat"])
            lon = np.asarray(raw["lon"])

            # Decimate once: keep the point closest to each grid-cell centre
            cell_x = np.floor((lon - lon.min()) / CELL_DEG).astype(int)
            cell_y = np.floor((lat - lat.min()) / CELL_DEG).astype(int)
            keep: dict[tuple[int, int], int] = {}
            for i, (cx, cy) in enumerate(zip(cell_x, cell_y)):
                ccx = lon.min() + (cx + 0.5) * CELL_DEG
                ccy = lat.min() + (cy + 0.5) * CELL_DEG
                d = (lon[i] - ccx) ** 2 + (lat[i] - ccy) ** 2
                cur = keep.get((cx, cy))
                if cur is None or d < cur[1]:
                    keep[(cx, cy)] = (i, d)
            idx = np.asarray(sorted(i for i, _ in keep.values()))

            vmax_kt = 0.0
            for regime in raw["regimes"].values():
                for h in regime.values():
                    vmax_kt = max(vmax_kt, max(h["vit"]) * MS_TO_KT)

            raw["_lat"] = lat
            raw["_lon"] = lon
            raw["_idx"] = idx
            raw["_vmax_kt"] = math.ceil(vmax_kt * 2) / 2
            _data = raw
            logger.info("Current atlas loaded: %d points, %d arrows, vmax %.1f kt",
                        len(lat), len(idx), raw["_vmax_kt"])
        return _data


def get_meta() -> dict:
    d = _load()
    return {
        "product": d["product"],
        "pm_reference": d["pm_reference"],
        "bbox": d["bbox"],
        "hours": list(range(-6, 7)),
        "regimes": {"VE": "Spring tide (vive-eau, coeff 95)", "ME": "Neap tide (morte-eau, coeff 45)"},
        "vmax_kt": d["_vmax_kt"],
        "n_points": len(d["_lat"]),
    }


def _get_basemap_cached(bbox: dict):
    global _basemap_cache
    from .windmap import _get_basemap  # noqa: PLC0415 — reuse OSM tile stitcher
    with _lock:
        if _basemap_cache is None:
            _basemap_cache = _get_basemap(
                bbox["lat_min"], bbox["lat_max"], bbox["lon_min"], bbox["lon_max"], zoom=12,
            )
        return _basemap_cache


def render_map(regime: str, hour: int) -> bytes:
    regime = regime.upper()
    if regime not in ("VE", "ME"):
        raise ValueError("regime must be VE or ME")
    if not -6 <= hour <= 6:
        raise ValueError("hour must be in -6..6")

    key = (regime, hour)
    if key in _png_cache:
        return _png_cache[key]

    d = _load()
    hours = d["regimes"][regime][str(hour)]
    idx = d["_idx"]
    lat = d["_lat"][idx]
    lon = d["_lon"][idx]
    vit_kt = np.asarray(hours["vit"])[idx] * MS_TO_KT
    dir_to = np.radians(np.asarray(hours["dir"])[idx])

    # dir is "going to" from true north: u = east component, v = north component
    disp_kt = np.minimum(vit_kt, ARROW_MAX_KT)
    u = disp_kt * np.sin(dir_to)
    v = disp_kt * np.cos(dir_to)

    bbox = d["bbox"]
    img, extent = _get_basemap_cached(bbox)

    mid_lat = 0.5 * (bbox["lat_min"] + bbox["lat_max"])
    aspect = 1.0 / math.cos(math.radians(mid_lat))
    dlon = bbox["lon_max"] - bbox["lon_min"]
    dlat = bbox["lat_max"] - bbox["lat_min"]
    fig_w = 11.0
    fig_h = fig_w * (dlat * aspect) / dlon

    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=110)
    ax.imshow(img, extent=extent, aspect=aspect, alpha=0.85, zorder=0)

    norm = mcolors.Normalize(vmin=0, vmax=d["_vmax_kt"])
    q = ax.quiver(
        lon, lat, u, v, vit_kt,
        cmap="turbo", norm=norm,
        angles="uv", scale_units="inches", scale=3.2,
        width=0.0022, headwidth=3.6, headlength=4.2, headaxislength=3.6,
        zorder=3,
    )
    ax.quiverkey(q, 0.895, 0.055, 1.0, "1 kt", labelpos="E", coordinates="axes",
                 fontproperties={"size": 9}, zorder=4)

    cbar = fig.colorbar(q, ax=ax, fraction=0.032, pad=0.015)
    cbar.set_label("Current (kt)", fontsize=9)
    cbar.ax.tick_params(labelsize=8)

    regime_label = "Spring tide (VE)" if regime == "VE" else "Neap tide (ME)"
    pm = f"PM{hour:+d}" if hour else "PM"
    ax.set_title(
        f"Tidal current — {pm} ({d['pm_reference']})  ·  {regime_label}  ·  surface",
        fontsize=11,
    )
    ax.set_xlim(bbox["lon_min"], bbox["lon_max"])
    ax.set_ylim(bbox["lat_min"], bbox["lat_max"])
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_edgecolor("#94a3b8")
    fig.text(0.01, 0.008, "Source: SHOM C3D LOIRE — PM = high water Saint-Nazaire",
             fontsize=7, color="#64748b")
    fig.tight_layout(pad=0.6)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    png = buf.getvalue()
    _png_cache[key] = png
    return png
