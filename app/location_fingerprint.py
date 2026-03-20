from __future__ import annotations

import logging
from math import asin, atan2, cos, degrees, radians, sin, sqrt

import httpx

from .cache import TTLCache
from .config import Settings
from .geo import haversine_km

logger = logging.getLogger("wind_validation.location_fingerprint")

BEARINGS_16 = [i * 22.5 for i in range(16)]
COAST_RADII_KM = [4.0, 8.0, 16.0, 32.0]
TERRAIN_RADII_KM = [1.5, 3.0, 6.0, 12.0]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _destination_point(lat: float, lon: float, bearing_deg: float, distance_km: float) -> tuple[float, float]:
    radius_km = 6371.0
    bearing = radians(bearing_deg)
    lat1 = radians(lat)
    lon1 = radians(lon)
    angular_distance = distance_km / radius_km

    lat2 = asin(
        sin(lat1) * cos(angular_distance)
        + cos(lat1) * sin(angular_distance) * cos(bearing)
    )
    lon2 = lon1 + atan2(
        sin(bearing) * sin(angular_distance) * cos(lat1),
        cos(angular_distance) - sin(lat1) * sin(lat2),
    )
    return degrees(lat2), ((degrees(lon2) + 540.0) % 360.0) - 180.0


def _circular_mean_weighted(values: list[tuple[float, float]]) -> float | None:
    usable = [(angle, weight) for angle, weight in values if weight > 0]
    if not usable:
        return None
    s = sum(weight * sin(radians(angle)) for angle, weight in usable)
    c = sum(weight * cos(radians(angle)) for angle, weight in usable)
    return (degrees(atan2(s, c)) + 360.0) % 360.0


def _direction_range_deg(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    norm = sorted(((value % 360.0) + 360.0) % 360.0 for value in values)
    max_gap = 0.0
    for i in range(1, len(norm)):
        max_gap = max(max_gap, norm[i] - norm[i - 1])
    max_gap = max(max_gap, norm[0] + 360.0 - norm[-1])
    return 360.0 - max_gap


def _axis_diff_deg(direction_deg: float | None, axis_deg: float | None) -> float | None:
    if direction_deg is None or axis_deg is None:
        return None
    diff = abs(((direction_deg - axis_deg + 90.0) % 180.0) - 90.0)
    return float(diff)


def _summarize_coast(origin_lat: float, origin_lon: float, rows: list[dict]) -> dict:
    best_by_bearing: dict[float, dict] = {}
    for row in rows:
        if not row.get("sea_hit"):
            continue
        bearing = float(row["bearing_deg"])
        effective_distance = float(row["effective_distance_km"])
        prev = best_by_bearing.get(bearing)
        if prev is None or effective_distance < prev["effective_distance_km"]:
            best_by_bearing[bearing] = {
                "bearing_deg": bearing,
                "effective_distance_km": effective_distance,
                "sample_distance_km": float(row["sample_distance_km"]),
            }

    if not best_by_bearing:
        return {
            "status": "no_open_water_found",
            "coastal": False,
            "nearest_open_water_km": None,
            "dominant_sea_bearing_deg": None,
            "shoreline_axis_deg": None,
            "sea_sector_width_deg": None,
            "exposure_score": 0.0,
            "sea_bearings_deg": [],
        }

    hits = list(best_by_bearing.values())
    nearest = min(hit["effective_distance_km"] for hit in hits)
    weighted_bearings = [
        (hit["bearing_deg"], 1.0 / max(1.0, hit["effective_distance_km"]))
        for hit in hits
    ]
    dominant = _circular_mean_weighted(weighted_bearings)
    sector_width = _direction_range_deg([hit["bearing_deg"] for hit in hits])
    exposure_score = sum(
        max(0.0, 1.0 - hit["effective_distance_km"] / 35.0)
        for hit in hits
    ) / max(1, len(hits))
    coastal = nearest <= 28.0 and len(hits) >= 2

    return {
        "status": "ok",
        "coastal": coastal,
        "nearest_open_water_km": round(nearest, 1),
        "dominant_sea_bearing_deg": round(dominant, 1) if dominant is not None else None,
        "shoreline_axis_deg": round(((dominant + 90.0) % 180.0), 1) if dominant is not None else None,
        "sea_sector_width_deg": round(sector_width, 1),
        "exposure_score": round(_clamp(exposure_score, 0.0, 1.0), 3),
        "sea_bearings_deg": sorted(int(hit["bearing_deg"]) for hit in hits),
    }


def _summarize_terrain(center_elevation_m: float | None, rows: list[dict]) -> dict:
    if center_elevation_m is None or not rows:
        return {
            "status": "terrain_unavailable",
            "center_elevation_m": center_elevation_m,
            "relief_m": None,
            "terrain_axis_deg": None,
            "channel_strength": 0.0,
            "topo_potential": 0.0,
        }

    by_bearing: dict[float, list[float]] = {bearing: [] for bearing in BEARINGS_16}
    elevations = [center_elevation_m]
    for row in rows:
        bearing = float(row["bearing_deg"])
        elevation = row.get("elevation_m")
        if elevation is None:
            continue
        rel = float(elevation) - center_elevation_m
        by_bearing[bearing].append(rel)
        elevations.append(float(elevation))

    barriers: list[float] = []
    openness: list[float] = []
    for bearing in BEARINGS_16:
        rels = by_bearing.get(bearing, [])
        if rels:
            barriers.append(sum(max(rel, 0.0) for rel in rels) / len(rels))
            openness.append(sum(max(-rel, 0.0) for rel in rels) / len(rels))
        else:
            barriers.append(0.0)
            openness.append(0.0)

    relief = max(elevations) - min(elevations) if len(elevations) >= 2 else 0.0
    axis_scores: list[float] = []
    for idx, _ in enumerate(BEARINGS_16):
        opp = (idx + 8) % 16
        axis_scores.append(
            barriers[idx] + barriers[opp] - 0.35 * (openness[idx] + openness[opp])
        )
    best_idx = min(range(16), key=lambda idx: axis_scores[idx])
    terrain_axis = BEARINGS_16[best_idx]
    opp_idx = (best_idx + 8) % 16
    left_idx = (best_idx + 4) % 16
    right_idx = (best_idx - 4) % 16

    along_barrier = (barriers[best_idx] + barriers[opp_idx]) / 2.0
    cross_barrier = (barriers[left_idx] + barriers[right_idx]) / 2.0
    bilateral_ratio = min(barriers[left_idx], barriers[right_idx]) / (max(barriers[left_idx], barriers[right_idx]) + 1.0)
    channel_strength = _clamp((cross_barrier - along_barrier) / max(relief, 40.0), 0.0, 1.0)
    relief_factor = _clamp(relief / 450.0, 0.0, 1.0)
    topo_potential = _clamp(relief_factor * (0.6 * channel_strength + 0.4 * bilateral_ratio), 0.0, 1.0)

    return {
        "status": "ok",
        "center_elevation_m": round(center_elevation_m, 1),
        "relief_m": round(relief, 1),
        "terrain_axis_deg": round(terrain_axis, 1),
        "channel_strength": round(channel_strength, 3),
        "topo_potential": round(topo_potential, 3),
    }


class LocationFingerprintService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cache: TTLCache[dict] = TTLCache(ttl_seconds=settings.fingerprint_cache_ttl_seconds)

    def _cache_key(self, lat: float, lon: float) -> str:
        return f"{round(lat, 3)}:{round(lon, 3)}"

    def fingerprint(self, lat: float, lon: float) -> dict:
        cache_key = self._cache_key(lat, lon)
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        coast = self._coast_fingerprint(lat, lon)
        terrain = self._terrain_fingerprint(lat, lon)
        result = {"coast": coast, "terrain": terrain}
        self.cache.set(cache_key, result)
        return result

    def _coast_fingerprint(self, lat: float, lon: float) -> dict:
        samples = []
        coords: list[tuple[float, float]] = []
        for bearing in BEARINGS_16:
            for radius_km in COAST_RADII_KM:
                sample_lat, sample_lon = _destination_point(lat, lon, bearing, radius_km)
                samples.append({
                    "bearing_deg": bearing,
                    "sample_distance_km": radius_km,
                    "lat": sample_lat,
                    "lon": sample_lon,
                })
                coords.append((sample_lat, sample_lon))

        try:
            payloads = self._fetch_marine_points(coords)
        except Exception as exc:
            logger.warning("Coast fingerprint fetch failed", exc_info=exc)
            return {
                "status": "coast_fetch_failed",
                "coastal": False,
                "nearest_open_water_km": None,
                "dominant_sea_bearing_deg": None,
                "shoreline_axis_deg": None,
                "sea_sector_width_deg": None,
                "exposure_score": 0.0,
                "sea_bearings_deg": [],
            }

        rows: list[dict] = []
        for sample, payload in zip(samples, payloads):
            grid_lat = payload.get("latitude")
            grid_lon = payload.get("longitude")
            current = payload.get("current", {})
            wave_height = current.get("wave_height") if isinstance(current, dict) else None
            if grid_lat is None or grid_lon is None:
                continue
            match_distance_km = haversine_km(sample["lat"], sample["lon"], float(grid_lat), float(grid_lon))
            sea_hit = wave_height is not None and match_distance_km <= 12.0
            rows.append({
                "bearing_deg": sample["bearing_deg"],
                "sample_distance_km": sample["sample_distance_km"],
                "match_distance_km": match_distance_km,
                "effective_distance_km": sample["sample_distance_km"] + 0.35 * match_distance_km,
                "sea_hit": sea_hit,
            })
        return _summarize_coast(lat, lon, rows)

    def _terrain_fingerprint(self, lat: float, lon: float) -> dict:
        center_elevation = None
        sample_rows: list[dict] = []
        coords: list[tuple[float, float]] = [(lat, lon)]
        meta: list[dict] = [{"bearing_deg": None, "radius_km": 0.0}]
        for bearing in BEARINGS_16:
            for radius_km in TERRAIN_RADII_KM:
                sample_lat, sample_lon = _destination_point(lat, lon, bearing, radius_km)
                coords.append((sample_lat, sample_lon))
                meta.append({"bearing_deg": bearing, "radius_km": radius_km})

        try:
            elevations = self._fetch_elevations(coords)
        except Exception as exc:
            logger.warning("Terrain fingerprint fetch failed", exc_info=exc)
            return {
                "status": "terrain_fetch_failed",
                "center_elevation_m": None,
                "relief_m": None,
                "terrain_axis_deg": None,
                "channel_strength": 0.0,
                "topo_potential": 0.0,
            }

        for idx, elevation in enumerate(elevations):
            info = meta[idx]
            if idx == 0:
                center_elevation = elevation
                continue
            sample_rows.append({
                "bearing_deg": info["bearing_deg"],
                "radius_km": info["radius_km"],
                "elevation_m": elevation,
            })
        return _summarize_terrain(center_elevation, sample_rows)

    def _fetch_elevations(self, coords: list[tuple[float, float]]) -> list[float | None]:
        with httpx.Client(timeout=self.settings.request_timeout_seconds) as client:
            resp = client.get(
                self.settings.openmeteo_elevation_url,
                params={
                    "latitude": ",".join(str(lat) for lat, _ in coords),
                    "longitude": ",".join(str(lon) for _, lon in coords),
                },
            )
            resp.raise_for_status()
        payload = resp.json()
        elevations = payload.get("elevation", [])
        if not isinstance(elevations, list):
            elevations = [elevations]
        out: list[float | None] = []
        for value in elevations:
            if value is None:
                out.append(None)
                continue
            try:
                out.append(float(value))
            except (TypeError, ValueError):
                out.append(None)
        return out

    def _fetch_marine_points(self, coords: list[tuple[float, float]]) -> list[dict]:
        with httpx.Client(timeout=self.settings.request_timeout_seconds) as client:
            resp = client.get(
                self.settings.openmeteo_marine_url,
                params={
                    "latitude": ",".join(str(lat) for lat, _ in coords),
                    "longitude": ",".join(str(lon) for _, lon in coords),
                    "current": "wave_height",
                    "cell_selection": "sea",
                    "timezone": "UTC",
                },
            )
            resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, list):
            return payload
        return [payload]
