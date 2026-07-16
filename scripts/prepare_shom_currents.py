"""Prepare SHOM C3D tidal-current data for the currents atlas page.

Reads the SHOM "Courants 3D" ASCII product (extracted from e.g. C3D_LOIRE.7z),
converts Lambert-93 to WGS84, crops to the racing area and writes a compact
gzipped JSON that the app serves. Run once per SHOM product update:

    python scripts/prepare_shom_currents.py --src <dir>\\C3D_LOIRE\\ASCII

Product conventions (SHOM "Produit de courants marins 3D"):
  - vit : current speed in m/s
  - dir : direction the current flows TOWARD, degrees from true north
  - 13 hourly files = PM-6 .. PM+6 relative to high water at Saint-Nazaire
  - VE = vive-eau (spring tide, coeff 95), ME = morte-eau (neap, coeff 45)
  - plan 13 = surface, 7 = mid-depth, 1 = bottom
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

import numpy as np
import pyproj

# Pornichet - La Baule - Le Pouliguen bay + approaches (Le Croisic to Saint-Nazaire)
BBOX = {"lon_min": -2.75, "lon_max": -2.15, "lat_min": 47.15, "lat_max": 47.35}

LEVEL_DIR = "SURFACE"   # plan 13
REGIMES = ("VE", "ME")
HOURS = range(1, 14)    # file 01..13 -> PM-6..PM+6


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="Path to the C3D ASCII directory (contains VE/ ME/)")
    ap.add_argument("--out", default="app/data/shom_currents_pornichet.json.gz")
    args = ap.parse_args()
    src = Path(args.src)

    tr = pyproj.Transformer.from_crs(2154, 4326, always_xy=True)

    # Mesh is identical across all files — build the crop mask once
    first = np.loadtxt(next((src / "VE" / LEVEL_DIR).glob("*_01data*.txt")), skiprows=1)
    lon, lat = tr.transform(first[:, 0], first[:, 1])
    mask = (
        (lon >= BBOX["lon_min"]) & (lon <= BBOX["lon_max"])
        & (lat >= BBOX["lat_min"]) & (lat <= BBOX["lat_max"])
    )
    print(f"mesh: {len(lon)} points, {mask.sum()} inside bbox")

    payload: dict = {
        "product": "SHOM C3D LOIRE (surface)",
        "pm_reference": "Saint-Nazaire",
        "units": {"vit": "m/s", "dir": "deg going-to"},
        "bbox": BBOX,
        "lat": [round(v, 5) for v in lat[mask]],
        "lon": [round(v, 5) for v in lon[mask]],
        "depth_m": [round(v, 1) for v in first[mask, 4]],
        "regimes": {},
    }

    for regime in REGIMES:
        hours: dict = {}
        for h in HOURS:
            matches = list((src / regime / LEVEL_DIR).glob(f"*_{h:02d}data*.txt"))
            if not matches:
                raise FileNotFoundError(f"missing hour {h:02d} for {regime}")
            data = np.loadtxt(matches[0], skiprows=1)
            if not np.allclose(data[:, :2], first[:, :2]):
                raise ValueError(f"mesh mismatch in {matches[0].name}")
            rel = h - 7   # 01->-6 (PM-6) .. 07->0 (PM) .. 13->+6
            hours[str(rel)] = {
                "vit": [round(v, 3) for v in data[mask, 2]],
                "dir": [round(v, 1) for v in data[mask, 3]],
            }
            print(f"{regime} PM{rel:+d}: max {data[mask, 2].max():.2f} m/s")
        payload["regimes"][regime] = hours

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KiB)")


if __name__ == "__main__":
    main()
