"""Tidal current atlas — SHOM C3D maps for the Pornichet racing area.

Renders the 13 hourly maps (PM-6 .. PM+6 relative to high water at
Saint-Nazaire) for spring (VE) and neap (ME) tides as PNGs with current
arrows in knots over an OSM basemap. Data is prepared offline by
scripts/prepare_shom_currents.py into app/data/shom_currents_pornichet.json.gz.

Two views: "bay" zooms in on Pornichet / La Baule (just outside the harbour),
"area" shows the wider approaches (Le Croisic to the Loire estuary).
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

# Optional navigator notes drawn on every map: [{"lat":.., "lon":.., "text":".."}]
ANNOTATIONS_PATH = Path(__file__).resolve().parent / "data" / "current_annotations.json"

# Arrow length is clipped so channel hotspots (e.g. the Le Croisic traict at
# 6+ kt) don't dominate the map; colour still shows true speed
ARROW_MAX_KT = 2.5

# Auto-detected speed hotspots: strongest jets, spatially separated
HOTSPOT_MIN_KT = 2.5
HOTSPOT_MIN_SEP_DEG = 0.045
HOTSPOT_MAX_N = 6

VIEWS: dict[str, dict] = {
    "area": {
        "label": "Wider area (Le Croisic – Loire)",
        "bbox": {"lon_min": -2.75, "lon_max": -2.15, "lat_min": 47.15, "lat_max": 47.35},
        "cell_deg": 0.012,   # one arrow per ~1 km
        "tile_zoom": 12,
    },
    "bay": {
        "label": "Baie de La Baule / Pornichet",
        "bbox": {"lon_min": -2.46, "lon_max": -2.27, "lat_min": 47.215, "lat_max": 47.30},
        "cell_deg": 0.005,   # keep nearly every mesh point
        "tile_zoom": 13,
    },
}

_lock = threading.Lock()
_data: dict | None = None
_png_cache: dict[tuple[str, int, str], bytes] = {}
_view_cache: dict[str, dict] = {}   # view -> {"idx": ndarray, "basemap": (img, extent)}


def _load() -> dict:
    global _data
    with _lock:
        if _data is None:
            with gzip.open(DATA_PATH, "rt", encoding="utf-8") as f:
                raw = json.load(f)
            lat = np.asarray(raw["lat"])
            lon = np.asarray(raw["lon"])

            vmax_kt = 0.0
            for regime in raw["regimes"].values():
                for h in regime.values():
                    vmax_kt = max(vmax_kt, max(h["vit"]) * MS_TO_KT)

            raw["_lat"] = lat
            raw["_lon"] = lon
            raw["_vmax_kt"] = math.ceil(vmax_kt * 2) / 2
            raw["_hotspots"] = _compute_hotspots(raw, lat, lon)

            raw["_annotations"] = []
            if ANNOTATIONS_PATH.exists():
                try:
                    raw["_annotations"] = json.loads(ANNOTATIONS_PATH.read_text(encoding="utf-8"))
                except Exception as exc:
                    logger.warning("current_annotations.json unreadable: %s", exc)

            _data = raw
            logger.info("Current atlas loaded: %d points, vmax %.1f kt, %d hotspots",
                        len(lat), raw["_vmax_kt"], len(raw["_hotspots"]))
        return _data


def _compute_hotspots(raw: dict, lat: np.ndarray, lon: np.ndarray) -> list[dict]:
    """Strongest spring-tide jets, greedily picked with a minimum separation."""
    ve = raw["regimes"]["VE"]
    stack = np.stack([np.asarray(ve[str(h)]["vit"]) for h in range(-6, 7)])   # (13, N)
    vmax_kt = stack.max(axis=0) * MS_TO_KT

    picked: list[int] = []
    for i in np.argsort(-vmax_kt):
        if vmax_kt[i] < HOTSPOT_MIN_KT or len(picked) >= HOTSPOT_MAX_N:
            break
        if any((lat[i] - lat[j]) ** 2 + (lon[i] - lon[j]) ** 2 < HOTSPOT_MIN_SEP_DEG ** 2
               for j in picked):
            continue
        picked.append(int(i))

    return [{"i": i, "lat": float(lat[i]), "lon": float(lon[i]), "max_kt": round(float(vmax_kt[i]), 1)}
            for i in picked]


def _view_setup(view: str) -> dict:
    """Per-view decimation index + basemap, cached."""
    d = _load()
    with _lock:
        if view not in _view_cache:
            cfg = VIEWS[view]
            bbox = cfg["bbox"]
            lat, lon = d["_lat"], d["_lon"]
            inside = np.where(
                (lon >= bbox["lon_min"]) & (lon <= bbox["lon_max"])
                & (lat >= bbox["lat_min"]) & (lat <= bbox["lat_max"])
            )[0]

            # Keep the point closest to each grid-cell centre
            cell = cfg["cell_deg"]
            keep: dict[tuple[int, int], tuple[int, float]] = {}
            for i in inside:
                cx = int((lon[i] - bbox["lon_min"]) / cell)
                cy = int((lat[i] - bbox["lat_min"]) / cell)
                ccx = bbox["lon_min"] + (cx + 0.5) * cell
                ccy = bbox["lat_min"] + (cy + 0.5) * cell
                dist = (lon[i] - ccx) ** 2 + (lat[i] - ccy) ** 2
                cur = keep.get((cx, cy))
                if cur is None or dist < cur[1]:
                    keep[(cx, cy)] = (int(i), dist)
            idx = np.asarray(sorted(i for i, _ in keep.values()), dtype=int)

            from .windmap import _get_basemap  # noqa: PLC0415 — reuse OSM tile stitcher
            basemap = _get_basemap(
                bbox["lat_min"], bbox["lat_max"], bbox["lon_min"], bbox["lon_max"],
                zoom=cfg["tile_zoom"],
            )
            _view_cache[view] = {"idx": idx, "basemap": basemap}
            logger.info("Current atlas view %s: %d arrows", view, len(idx))
        return _view_cache[view]


def get_meta() -> dict:
    d = _load()
    return {
        "product": d["product"],
        "pm_reference": d["pm_reference"],
        "views": {k: v["label"] for k, v in VIEWS.items()},
        "hours": list(range(-6, 7)),
        "regimes": {"VE": "Spring tide (vive-eau, coeff 95)", "ME": "Neap tide (morte-eau, coeff 45)"},
        "vmax_kt": d["_vmax_kt"],
        "n_points": len(d["_lat"]),
        "hotspots": [{k: h[k] for k in ("lat", "lon", "max_kt")} for h in d["_hotspots"]],
    }


def _pm_label(hour: int) -> str:
    return f"PM{hour:+d}" if hour else "PM"


def render_map(regime: str, hour: int, view: str = "area") -> bytes:
    regime = regime.upper()
    if regime not in ("VE", "ME"):
        raise ValueError("regime must be VE or ME")
    if not -6 <= hour <= 6:
        raise ValueError("hour must be in -6..6")
    if view not in VIEWS:
        raise ValueError(f"view must be one of {sorted(VIEWS)}")

    key = (regime, hour, view)
    if key in _png_cache:
        return _png_cache[key]

    d = _load()
    setup = _view_setup(view)
    cfg = VIEWS[view]
    bbox = cfg["bbox"]

    hours = d["regimes"][regime][str(hour)]
    idx = setup["idx"]
    lat = d["_lat"][idx]
    lon = d["_lon"][idx]
    vit_kt = np.asarray(hours["vit"])[idx] * MS_TO_KT
    dir_to = np.radians(np.asarray(hours["dir"])[idx])

    # dir is "going to" from true north: u = east component, v = north component
    disp_kt = np.minimum(vit_kt, ARROW_MAX_KT)
    u = disp_kt * np.sin(dir_to)
    v = disp_kt * np.cos(dir_to)

    img, extent = setup["basemap"]

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

    # Hotspots: strongest jets, with slack hour for the regime being drawn
    for hs in d["_hotspots"]:
        if not (bbox["lon_min"] <= hs["lon"] <= bbox["lon_max"]
                and bbox["lat_min"] <= hs["lat"] <= bbox["lat_max"]):
            continue
        vits = [d["regimes"][regime][str(h)]["vit"][hs["i"]] for h in range(-6, 7)]
        slack = int(np.argmin(vits)) - 6
        ax.plot(hs["lon"], hs["lat"], marker="o", ms=11, mfc="none",
                mec="#dc2626", mew=1.8, zorder=5)
        ax.annotate(
            f"jet ≤ {hs['max_kt']:.1f} kt · slack {_pm_label(slack)}",
            xy=(hs["lon"], hs["lat"]), xytext=(9, 9), textcoords="offset points",
            fontsize=7.5, color="#7f1d1d", zorder=5,
            bbox={"boxstyle": "round,pad=0.25", "fc": "white", "ec": "#dc2626", "alpha": 0.85, "lw": 0.8},
        )

    # Custom navigator notes (app/data/current_annotations.json)
    for note in d["_annotations"]:
        try:
            nlat, nlon, text = float(note["lat"]), float(note["lon"]), str(note["text"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (bbox["lon_min"] <= nlon <= bbox["lon_max"]
                and bbox["lat_min"] <= nlat <= bbox["lat_max"]):
            continue
        ax.plot(nlon, nlat, marker="D", ms=6, mfc="#1d4ed8", mec="white", mew=0.8, zorder=5)
        ax.annotate(
            text, xy=(nlon, nlat), xytext=(9, -12), textcoords="offset points",
            fontsize=7.5, color="#1e3a8a", zorder=5,
            bbox={"boxstyle": "round,pad=0.25", "fc": "white", "ec": "#1d4ed8", "alpha": 0.85, "lw": 0.8},
        )

    regime_label = "Spring tide (VE)" if regime == "VE" else "Neap tide (ME)"
    ax.set_title(
        f"Tidal current — {_pm_label(hour)} ({d['pm_reference']})  ·  {regime_label}  ·  surface",
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
