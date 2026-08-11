"""Regional observation networks used by the validation broker.

Every adapter exposes ordinary ``Station`` and ``Observation`` objects, so the
existing QC, station-representativeness weighting and forecast/observation
archive apply consistently to national networks and marine buoys.
"""
from __future__ import annotations

import csv
import io
import logging
import math
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx

from .adapters import BaseSourceAdapter
from .domain import Observation, Station
from .geo import haversine_km
from .repositories import InMemoryRepository

logger = logging.getLogger("wind_validation.regional_adapters")
_KT_TO_MS = 0.514444
_STOCKHOLM = ZoneInfo("Europe/Stockholm")


def _time(raw: object, tz=UTC) -> datetime | None:
    """Parse ISO and the timestamp formats used by the public station feeds."""
    if raw is None:
        return None
    try:
        if isinstance(raw, (int, float)):
            # SMHI returns epoch milliseconds.
            return datetime.fromtimestamp(float(raw) / (1000 if float(raw) > 1e11 else 1), UTC)
        value = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(value)
        return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(UTC)
    except (TypeError, ValueError, OSError):
        return None


class SmhiAdapter(BaseSourceAdapter):
    """SMHI CORE stations; only quality-controlled Swedish observations."""
    source_name = "smhi"

    def _live_enabled(self) -> bool:
        return self.settings.live_observations_enabled

    def _stations(self, parameter: int) -> list[dict]:
        url = f"{self.settings.smhi_observation_url}/version/latest/parameter/{parameter}.json"
        with httpx.Client(timeout=self.settings.request_timeout_seconds) as client:
            response = client.get(url, params={"measuringStations": "CORE"})
            response.raise_for_status()
            return response.json().get("station", [])

    def list_stations(self, repo: InMemoryRepository, lat: float, lon: float, radius_km: float) -> list[Station]:
        if not self._live_enabled():
            return []
        try:
            rows = self._stations(4)  # wind speed; station metadata is shared by wind direction.
        except Exception:
            logger.warning("SMHI station discovery failed", exc_info=True)
            return []
        found = []
        for row in rows:
            try:
                slat, slon = float(row["latitude"]), float(row["longitude"])
                if not row.get("active", True) or haversine_km(lat, lon, slat, slon) > radius_km:
                    continue
                found.append(Station(f"SMHI_{row['id']}", self.source_name, "SE", slat, slon,
                                     elevation_m=float(row["height"]) if row.get("height") is not None else None,
                                     external_id=str(row["id"])))
            except (KeyError, TypeError, ValueError):
                continue
        return found

    def _values(self, station: str, parameter: int) -> list[dict]:
        url = f"{self.settings.smhi_observation_url}/version/latest/parameter/{parameter}/station/{station}/period/latest-day/data.json"
        with httpx.Client(timeout=self.settings.request_timeout_seconds) as client:
            response = client.get(url)
            response.raise_for_status()
            return response.json().get("value", [])

    def get_obs(self, repo: InMemoryRepository, station_ids: set[str], start: datetime, end: datetime) -> list[Observation]:
        stations = [s for s in repo.stations if s.station_id in station_ids and s.source == self.source_name]
        # Dynamically discovered stations are not in repo: retain their external id in the ID.
        external_ids = [s.external_id for s in stations if s.external_id] + [sid[5:] for sid in station_ids if sid.startswith("SMHI_")]
        rows: list[Observation] = []
        for ext in set(external_ids):
            try:
                speed = {_time(v.get("date")): v.get("value") for v in self._values(ext, 4)}
                direction = {_time(v.get("date")): v.get("value") for v in self._values(ext, 3)}
                for ts, raw_speed in speed.items():
                    raw_dir = direction.get(ts)
                    if ts is None or raw_speed is None or raw_dir is None or not start <= ts <= end:
                        continue
                    rows.append(Observation(f"SMHI_{ext}", self.source_name, ts, float(raw_speed), float(raw_dir)))
            except Exception:
                logger.warning("SMHI observation fetch failed for %s", ext, exc_info=True)
        return rows


class VivaAdapter(BaseSourceAdapter):
    """Swedish Maritime Administration ViVa coastal stations (mean wind)."""
    source_name = "viva"

    def _live_enabled(self) -> bool:
        return self.settings.live_observations_enabled

    def _get(self, suffix: str) -> dict:
        with httpx.Client(timeout=self.settings.request_timeout_seconds) as client:
            response = client.get(self.settings.viva_observation_url.rstrip("/") + "/" + suffix.lstrip("/"))
            response.raise_for_status()
            return response.json()

    def list_stations(self, repo: InMemoryRepository, lat: float, lon: float, radius_km: float) -> list[Station]:
        if not self._live_enabled():
            return []
        try:
            source = self._get("vivastation/").get("GetStationsResult", {}).get("Stations", [])
        except Exception:
            logger.warning("ViVa station discovery failed", exc_info=True)
            return []
        out = []
        for row in source:
            try:
                slat, slon = float(row["Lat"]), float(row["Lon"])
                if haversine_km(lat, lon, slat, slon) <= radius_km:
                    out.append(Station(f"VIVA_{row['ID']}", self.source_name, "SE", slat, slon,
                                       external_id=str(row["ID"]), station_type="coastal"))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def get_obs(self, repo: InMemoryRepository, station_ids: set[str], start: datetime, end: datetime) -> list[Observation]:
        rows = []
        for sid in station_ids:
            if not sid.startswith("VIVA_"):
                continue
            try:
                payload = self._get(f"ViVaStationWithDirection/{sid[5:]}")
                samples = payload.get("GetSingleStationWithDirectionsAsParametersResult", {}).get("Samples", [])
                mean = next((x for x in samples if str(x.get("Name", "")).lower() == "medelvind"), None)
                direction = next((x for x in samples if str(x.get("Name", "")).lower() == "vindriktning"), None)
                ts = _time((mean or {}).get("Updated"), _STOCKHOLM)
                if not mean or not direction or ts is None or not start <= ts <= end:
                    continue
                rows.append(Observation(sid, self.source_name, ts, float(str(mean["Value"]).split()[-1]), float(direction["Value"])))
            except Exception:
                logger.warning("ViVa observation fetch failed for %s", sid, exc_info=True)
        return rows


class NdbcAdapter(BaseSourceAdapter):
    """NOAA/NDBC latest buoy and C-MAN observations for US waters."""
    source_name = "ndbc"

    def _live_enabled(self) -> bool:
        return self.settings.live_observations_enabled

    def _latest(self) -> list[dict]:
        with httpx.Client(timeout=max(20.0, self.settings.request_timeout_seconds)) as client:
            response = client.get(self.settings.ndbc_latest_observations_url)
            response.raise_for_status()
        data = []
        for line in response.text.splitlines():
            if not line or line.startswith("#"):
                continue
            part = line.split()
            if len(part) < 11:
                continue
            try:
                data.append({"id": part[0], "lat": float(part[1]), "lon": float(part[2]),
                             "time": datetime(int(part[3]), int(part[4]), int(part[5]), int(part[6]), int(part[7]), tzinfo=UTC),
                             "wd": float(part[8]), "ws": float(part[9])})
            except (ValueError, IndexError):
                continue
        return data

    def list_stations(self, repo: InMemoryRepository, lat: float, lon: float, radius_km: float) -> list[Station]:
        if not self._live_enabled():
            return []
        try:
            return [Station(f"NDBC_{r['id']}", self.source_name, "US", r["lat"], r["lon"], station_type="buoy", external_id=r["id"])
                    for r in self._latest() if haversine_km(lat, lon, r["lat"], r["lon"]) <= radius_km]
        except Exception:
            logger.warning("NDBC station discovery failed", exc_info=True)
            return []

    def get_obs(self, repo: InMemoryRepository, station_ids: set[str], start: datetime, end: datetime) -> list[Observation]:
        wanted = {sid[5:] for sid in station_ids if sid.startswith("NDBC_")}
        if not wanted:
            return []
        try:
            return [Observation(f"NDBC_{r['id']}", self.source_name, r["time"], r["ws"] * _KT_TO_MS, r["wd"])
                    for r in self._latest() if r["id"] in wanted and 0 <= r["wd"] <= 360 and start <= r["time"] <= end]
        except Exception:
            logger.warning("NDBC observation fetch failed", exc_info=True)
            return []


class ImgwAdapter(BaseSourceAdapter):
    """IMGW current SYNOP observations; coastal stations are most relevant to sailing."""
    source_name = "imgw"
    _COASTAL = {"Swinoujscie": (53.91, 14.24), "Kolobrzeg": (54.18, 15.58), "Ustka": (54.58, 16.86), "Hel": (54.61, 18.81), "Gdansk": (54.35, 18.65)}

    def list_stations(self, repo: InMemoryRepository, lat: float, lon: float, radius_km: float) -> list[Station]:
        return [Station(f"IMGW_{name}", self.source_name, "PL", slat, slon, external_id=name, station_type="coastal")
                for name, (slat, slon) in self._COASTAL.items() if haversine_km(lat, lon, slat, slon) <= radius_km]

    def get_obs(self, repo: InMemoryRepository, station_ids: set[str], start: datetime, end: datetime) -> list[Observation]:
        wanted = {sid[5:] for sid in station_ids if sid.startswith("IMGW_")}
        if not wanted or not self.settings.live_observations_enabled:
            return []
        try:
            with httpx.Client(timeout=self.settings.request_timeout_seconds) as client:
                response = client.get(self.settings.imgw_observation_url)
                response.raise_for_status()
            out = []
            for row in response.json():
                name = str(row.get("stacja", ""))
                if name not in wanted:
                    continue
                ts = _time(f"{row.get('data_pomiaru')}T{int(row.get('godzina_pomiaru', 0)):02d}:00:00")
                ws, wd = row.get("predkosc_wiatru"), row.get("kierunek_wiatru")
                if ts and ws is not None and wd is not None and start <= ts <= end:
                    out.append(Observation(f"IMGW_{name}", self.source_name, ts, float(ws), float(wd)))
            return out
        except Exception:
            logger.warning("IMGW observation fetch failed", exc_info=True)
            return []


class RwsAdapter(BaseSourceAdapter):
    """Rijkswaterstaat water stations.

    Waterinfo has no stable unauthenticated machine endpoint for live wind.
    The adapter accepts a configured GeoJSON endpoint, allowing a supported RWS
    export/proxy to be connected without changing the calibration pipeline.
    """
    source_name = "rws"

    def list_stations(self, repo: InMemoryRepository, lat: float, lon: float, radius_km: float) -> list[Station]:
        return [station for station, _ in self._feed(lat, lon, radius_km)]

    def _feed(self, lat: float | None = None, lon: float | None = None, radius_km: float | None = None) -> list[tuple[Station, Observation]]:
        if not self.settings.live_observations_enabled or not self.settings.rws_observation_url:
            return []
        try:
            with httpx.Client(timeout=self.settings.request_timeout_seconds) as client:
                response = client.get(self.settings.rws_observation_url)
                response.raise_for_status()
            features = response.json().get("features", [])
        except Exception:
            logger.warning("RWS Waterinfo export fetch failed", exc_info=True)
            return []
        out = []
        for feature in features:
            try:
                prop = feature["properties"]
                flon, flat = feature["geometry"]["coordinates"][:2]
                station = Station(f"RWS_{prop['station_id']}", self.source_name, "NL", float(flat), float(flon),
                                  external_id=str(prop["station_id"]), station_type="coastal")
                if lat is not None and lon is not None and radius_km is not None and haversine_km(lat, lon, station.lat, station.lon) > radius_km:
                    continue
                ts = _time(prop.get("time_utc"))
                if ts is None:
                    continue
                out.append((station, Observation(station.station_id, self.source_name, ts, float(prop["ws_ms"]), float(prop["wd_deg"]))))
            except (KeyError, TypeError, ValueError, IndexError):
                continue
        return out

    def get_obs(self, repo: InMemoryRepository, station_ids: set[str], start: datetime, end: datetime) -> list[Observation]:
        return [obs for station, obs in self._feed() if station.station_id in station_ids and start <= obs.time_utc <= end]
