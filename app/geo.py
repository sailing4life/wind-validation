from __future__ import annotations

from math import asin, cos, radians, sin, sqrt


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0
    d_lat = radians(lat2 - lat1)
    d_lon = radians(lon2 - lon1)
    a = sin(d_lat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(d_lon / 2) ** 2
    c = 2 * asin(sqrt(a))
    return radius_km * c


def in_bbox(lat: float, lon: float, bbox: dict[str, float]) -> bool:
    return bbox["min_lat"] <= lat <= bbox["max_lat"] and bbox["min_lon"] <= lon <= bbox["max_lon"]


def detect_country(lat: float, lon: float) -> str:
    if 50.5 <= lat <= 53.8 and 3.0 <= lon <= 7.3:
        return "NL"
    if 41.0 <= lat <= 51.5 and -5.6 <= lon <= 9.7:
        return "FR"
    if 35.0 <= lat <= 47.2 and 6.0 <= lon <= 18.7:
        return "IT"
    return "OTHER"