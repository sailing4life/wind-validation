from __future__ import annotations

import logging
import math
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

try:
    _AMSTERDAM = ZoneInfo("Europe/Amsterdam")
except ZoneInfoNotFoundError:
    _AMSTERDAM = UTC

from .config import Settings
from .domain import Observation, Station
from .geo import detect_country, haversine_km
from .repositories import InMemoryRepository

logger = logging.getLogger("wind_validation.adapters")


def _normalize_api_key(raw: str | None) -> str:
    if raw is None:
        return ""
    key = raw.strip().strip('"').strip("'")
    low = key.lower()
    if low.startswith("authorization:"):
        key = key.split(":", 1)[1].strip()
        low = key.lower()
    if low.startswith("bearer "):
        key = key[7:].strip()
    return key


class BaseSourceAdapter:
    source_name = "unknown"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def list_stations(self, repo: InMemoryRepository, lat: float, lon: float, radius_km: float) -> list[Station]:
        return [s for s in repo.nearby_stations(lat, lon, radius_km) if s.source == self.source_name]

    def get_obs(self, repo: InMemoryRepository, station_ids: set[str], start: datetime, end: datetime) -> list[Observation]:
        return []


class KnmiAdapter(BaseSourceAdapter):
    source_name = "knmi"
    # KNMI hourly observations via daggegevens.knmi.nl — no API key required,
    # returns plain text CSV. FH = wind speed (0.1 m/s), DD = direction (degrees).
    _DAGGEGEVENS_URL = "https://www.daggegevens.knmi.nl/klimatologie/uurgegevens"

    def _live_enabled(self) -> bool:
        return self.settings.live_observations_enabled

    def _parse_uurgegevens(
        self,
        text: str,
        ext_to_internal: dict[str, str],
        start: datetime,
        end: datetime,
    ) -> list[Observation]:
        rows: list[Observation] = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 5:
                continue
            try:
                stn = parts[0]
                internal_id = ext_to_internal.get(stn)
                if not internal_id:
                    continue
                date_str = parts[1]   # YYYYMMDD
                hh = int(parts[2])    # 1–24 (hour ending, in Amsterdam local time)
                dd_raw = parts[3]     # wind direction (degrees), may be empty
                fh_raw = parts[4]     # wind speed (0.1 m/s), may be empty
                if not dd_raw or not fh_raw:
                    continue
                wd_deg = float(dd_raw)
                ws_ms = float(fh_raw) / 10.0
                # daggegevens timestamps are in Amsterdam local time — convert to UTC
                base_naive = datetime.strptime(date_str, "%Y%m%d")
                if hh == 24:
                    ts_local = (base_naive + timedelta(days=1)).replace(tzinfo=_AMSTERDAM)
                else:
                    ts_local = (base_naive + timedelta(hours=hh)).replace(tzinfo=_AMSTERDAM)
                ts = ts_local.astimezone(UTC)
                if ts < start or ts > end:
                    continue
                rows.append(Observation(station_id=internal_id, source=self.source_name, time_utc=ts, ws_ms=ws_ms, wd_deg=wd_deg))
            except (ValueError, IndexError):
                continue
        return rows

    def get_obs(self, repo: InMemoryRepository, station_ids: set[str], start: datetime, end: datetime) -> list[Observation]:
        if not self._live_enabled():
            return []

        stations = [s for s in repo.stations if s.station_id in station_ids and s.source == self.source_name and s.external_id]
        if not stations:
            return []

        ext_to_internal = {s.external_id: s.station_id for s in stations}
        knmi_stns = ":".join(s.external_id for s in stations)

        def _knmi_hhstr(dt_utc: datetime) -> str:
            # daggegevens uses Amsterdam local time with HH=01–24 (not 00–23)
            dt_local = dt_utc.astimezone(_AMSTERDAM)
            if dt_local.hour == 0:
                prev = dt_local - timedelta(days=1)
                return prev.strftime("%Y%m%d") + "24"
            return dt_local.strftime("%Y%m%d%H")

        try:
            with httpx.Client(timeout=self.settings.request_timeout_seconds) as client:
                resp = client.get(
                    self._DAGGEGEVENS_URL,
                    params={
                        "stns": knmi_stns,
                        "vars": "FH:DD",
                        "start": _knmi_hhstr(start),
                        "end": _knmi_hhstr(end),
                    },
                )
                resp.raise_for_status()
                parsed = self._parse_uurgegevens(resp.text, ext_to_internal, start, end)
                if parsed:
                    latest_ts = max(r.time_utc for r in parsed)
                    logger.info("KNMI fetch: %d obs, latest=%s", len(parsed), latest_ts.isoformat())
                else:
                    logger.warning("KNMI fetch returned 0 observations for window %s–%s", start.isoformat(), end.isoformat())
                return parsed
        except httpx.HTTPStatusError as exc:
            logger.warning("KNMI live fetch failed with status=%s for %s", exc.response.status_code, exc.request.url)
        except Exception as exc:
            logger.warning("KNMI live fetch failed", exc_info=exc)
        return []


class MeteoFranceAdapter(BaseSourceAdapter):
    source_name = "meteofrance"

    def _live_enabled(self) -> bool:
        return self.settings.live_observations_enabled and bool(self.settings.meteofrance_observation_url)

    def get_obs(self, repo: InMemoryRepository, station_ids: set[str], start: datetime, end: datetime) -> list[Observation]:
        if not self._live_enabled():
            return []

        stations = [s for s in repo.stations if s.station_id in station_ids and s.source == self.source_name]
        if not stations:
            return []

        headers = {}
        if self.settings.meteofrance_api_key:
            headers["Authorization"] = f"Bearer {self.settings.meteofrance_api_key}"

        parsed_rows: list[Observation] = []
        try:
            with httpx.Client(timeout=self.settings.request_timeout_seconds) as client:
                for station in stations:
                    station_ref = station.external_id or station.station_id
                    resp = client.get(
                        self.settings.meteofrance_observation_url,
                        headers=headers,
                        params={
                            "station": station_ref,
                            "start": start.isoformat(),
                            "end": end.isoformat(),
                        },
                    )
                    resp.raise_for_status()
                    payload = resp.json()
                    for row in payload if isinstance(payload, list) else payload.get("data", []):
                        ts_raw = row.get("time_utc") or row.get("date") or row.get("time")
                        if not ts_raw:
                            continue
                        try:
                            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                        except ValueError:
                            continue
                        if ts < start or ts > end:
                            continue
                        ws = row.get("ws_ms") or row.get("ff") or row.get("wind_speed")
                        wd = row.get("wd_deg") or row.get("dd") or row.get("wind_direction")
                        if ws is None or wd is None:
                            continue
                        parsed_rows.append(
                            Observation(
                                station_id=station.station_id,
                                source=self.source_name,
                                time_utc=ts.astimezone(UTC),
                                ws_ms=float(ws),
                                wd_deg=float(wd),
                            )
                        )
        except Exception as exc:
            logger.warning("Meteo-France live fetch failed", exc_info=exc)
            return []

        return parsed_rows


class IsdAdapter(BaseSourceAdapter):
    source_name = "isd"

    def _live_enabled(self) -> bool:
        return self.settings.live_observations_enabled

    def _parse_wnd(self, raw: str) -> tuple[float, float] | None:
        # NOAA ISD WND field: DDD,quality,code,FFF,quality (FFF in 0.1 m/s)
        if not raw:
            return None
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) < 4:
            return None
        try:
            wd = float(parts[0])
            spd_tenth = float(parts[3])
        except ValueError:
            return None
        if wd >= 999 or spd_tenth >= 9999:
            return None
        return spd_tenth / 10.0, wd

    def get_obs(self, repo: InMemoryRepository, station_ids: set[str], start: datetime, end: datetime) -> list[Observation]:
        if not self._live_enabled():
            return []

        station_map = {s.external_id: s.station_id for s in repo.stations if s.station_id in station_ids and s.source == self.source_name and s.external_id}
        if not station_map:
            return []

        headers = {}
        if self.settings.ncei_token:
            headers["token"] = self.settings.ncei_token

        try:
            with httpx.Client(timeout=self.settings.request_timeout_seconds) as client:
                resp = client.get(
                    self.settings.ncei_api_base_url,
                    headers=headers,
                    params={
                        "dataset": self.settings.ncei_dataset,
                        "stations": ",".join(station_map.keys()),
                        "startDate": start.strftime("%Y-%m-%dT%H:%M:%S"),
                        "endDate": end.strftime("%Y-%m-%dT%H:%M:%S"),
                        "format": "json",
                        "includeAttributes": "false",
                    },
                )
                resp.raise_for_status()
                payload = resp.json()
        except Exception as exc:
            logger.warning("ISD live fetch failed", exc_info=exc)
            return []

        rows: list[Observation] = []
        for item in payload if isinstance(payload, list) else []:
            ext = item.get("STATION")
            internal_station = station_map.get(ext)
            if not internal_station:
                continue
            parsed = self._parse_wnd(item.get("WND", ""))
            if parsed is None:
                continue
            ws, wd = parsed
            date_raw = item.get("DATE")
            if not date_raw:
                continue
            try:
                # NCEI returns naive UTC timestamps — must not use astimezone() on naive dt
                raw_str = str(date_raw).rstrip("Z")
                ts_naive = datetime.fromisoformat(raw_str)
                ts = ts_naive.replace(tzinfo=UTC)
            except ValueError:
                continue
            if ts < start or ts > end:
                continue
            rows.append(Observation(station_id=internal_station, source=self.source_name, time_utc=ts, ws_ms=ws, wd_deg=wd))

        if rows:
            latest_ts = max(r.time_utc for r in rows)
            logger.info("ISD fetch: %d obs, latest=%s", len(rows), latest_ts.isoformat())
        else:
            logger.warning("ISD fetch returned 0 observations for stations=%s window %s–%s",
                           list(station_map.keys()), start.isoformat(), end.isoformat())
        return rows


class MetarAdapter(BaseSourceAdapter):
    """Aviation Weather Center METAR API — free, no auth, < 60 min latency.

    Stations are discovered dynamically via bbox query; no catalog entries needed.
    Station IDs use the format "METAR_{ICAO}" (e.g. "METAR_EHAM").
    """
    source_name = "metar"
    _AWC_URL = "https://aviationweather.gov/api/data/metar"
    _KNOTS_TO_MS = 0.514444

    def _live_enabled(self) -> bool:
        return self.settings.live_observations_enabled

    @staticmethod
    def _bbox(lat: float, lon: float, radius_km: float) -> str:
        dlat = radius_km / 111.0
        dlon = radius_km / (111.0 * math.cos(math.radians(lat)))
        return f"{lat - dlat:.3f},{lon - dlon:.3f},{lat + dlat:.3f},{lon + dlon:.3f}"

    def list_stations(self, repo: InMemoryRepository, lat: float, lon: float, radius_km: float) -> list[Station]:
        """Discover airports within radius by querying AWC with a bbox."""
        if not self._live_enabled():
            return []
        try:
            with httpx.Client(timeout=self.settings.request_timeout_seconds) as client:
                resp = client.get(self._AWC_URL, params={
                    "bbox":   self._bbox(lat, lon, radius_km),
                    "format": "json",
                    "hours":  2,
                })
                resp.raise_for_status()
                payload = resp.json()
        except Exception as exc:
            logger.warning("METAR list_stations failed", exc_info=exc)
            return []

        stations: list[Station] = []
        seen: set[str] = set()
        for item in payload if isinstance(payload, list) else []:
            icao = item.get("icaoId") or item.get("stationId")
            if not icao or icao in seen:
                continue
            seen.add(icao)
            slat = item.get("lat")
            slon = item.get("lon")
            if slat is None or slon is None:
                continue
            slat, slon = float(slat), float(slon)
            if haversine_km(lat, lon, slat, slon) > radius_km:
                continue
            stations.append(Station(
                station_id=f"METAR_{icao}",
                source=self.source_name,
                country=detect_country(slat, slon),
                lat=slat,
                lon=slon,
                elevation_m=item.get("elev"),
                external_id=icao,
            ))
        logger.info("METAR discovered %d airports within %.0f km of (%.2f, %.2f)", len(stations), radius_km, lat, lon)
        return stations

    def get_obs(self, repo: InMemoryRepository, station_ids: set[str], start: datetime, end: datetime) -> list[Observation]:
        if not self._live_enabled():
            return []

        # Derive ICAO codes from "METAR_{ICAO}" station IDs
        icao_to_id = {sid[6:]: sid for sid in station_ids if sid.startswith("METAR_")}
        if not icao_to_id:
            return []

        hours_back = min(72, max(1, int((end - start).total_seconds() / 3600) + 1))

        # Batch into groups of 100 to stay within URL limits
        icao_list = list(icao_to_id.keys())
        rows: list[Observation] = []
        try:
            with httpx.Client(timeout=max(self.settings.request_timeout_seconds, 20.0)) as client:
                for i in range(0, len(icao_list), 100):
                    batch = icao_list[i:i + 100]
                    try:
                        resp = client.get(self._AWC_URL, params={
                            "ids":    ",".join(batch),
                            "format": "json",
                            "hours":  hours_back,
                        })
                        resp.raise_for_status()
                        payload = resp.json()
                    except Exception as exc:
                        logger.warning("METAR batch fetch failed", exc_info=exc)
                        continue

                    for item in payload if isinstance(payload, list) else []:
                        icao     = item.get("icaoId") or item.get("stationId")
                        internal = icao_to_id.get(icao)
                        if not internal:
                            continue
                        wdir = item.get("wdir")
                        wspd = item.get("wspd")
                        if wdir is None or wspd is None:
                            continue
                        try:
                            wd_deg = float(wdir)
                            ws_ms  = float(wspd) * self._KNOTS_TO_MS
                        except (TypeError, ValueError):
                            continue
                        obs_time_raw = item.get("obsTime")
                        if obs_time_raw is None:
                            continue
                        try:
                            if isinstance(obs_time_raw, (int, float)):
                                ts = datetime.fromtimestamp(obs_time_raw, tz=UTC)
                            else:
                                ts = datetime.fromisoformat(str(obs_time_raw).replace("Z", "+00:00"))
                                if ts.tzinfo is None:
                                    ts = ts.replace(tzinfo=UTC)
                        except (ValueError, OSError):
                            continue
                        if ts < start or ts > end:
                            continue
                        rows.append(Observation(
                            station_id=internal, source=self.source_name,
                            time_utc=ts, ws_ms=ws_ms, wd_deg=wd_deg,
                        ))
        except Exception as exc:
            logger.warning("METAR fetch failed", exc_info=exc)
            return []

        if rows:
            logger.info("METAR fetch: %d obs from %d airports, latest=%s",
                        len(rows), len(icao_to_id), max(r.time_utc for r in rows).isoformat())
        else:
            logger.warning("METAR fetch: 0 obs for %d airports", len(icao_to_id))
        return rows


class BrightSkyAdapter(BaseSourceAdapter):
    """Bright Sky — wraps DWD Germany SYNOP (~10 min latency, free, no auth).

    Stations discovered dynamically via Bright Sky sources API.
    Station IDs use the format "BS_{wmo_id}".
    """
    source_name = "brightsky"
    _SOURCES_URL = "https://api.brightsky.dev/sources"
    _WEATHER_URL = "https://api.brightsky.dev/weather"

    def _live_enabled(self) -> bool:
        return self.settings.live_observations_enabled

    def list_stations(self, repo: InMemoryRepository, lat: float, lon: float, radius_km: float) -> list[Station]:
        if not self._live_enabled():
            return []
        try:
            with httpx.Client(timeout=self.settings.request_timeout_seconds) as client:
                resp = client.get(self._SOURCES_URL, params={
                    "lat":      lat,
                    "lon":      lon,
                    "max_dist": int(radius_km * 1000),
                })
                resp.raise_for_status()
                payload = resp.json()
        except Exception as exc:
            logger.warning("BrightSky list_stations failed", exc_info=exc)
            return []

        stations: list[Station] = []
        for src in payload.get("sources", []):
            wmo_id = src.get("wmo_station_id") or src.get("id")
            slat   = src.get("lat")
            slon   = src.get("lon")
            if not wmo_id or slat is None or slon is None:
                continue
            stations.append(Station(
                station_id=f"BS_{wmo_id}",
                source=self.source_name,
                country=detect_country(float(slat), float(slon)),
                lat=float(slat),
                lon=float(slon),
                elevation_m=src.get("height"),
                external_id=str(wmo_id),
            ))
        logger.info("BrightSky discovered %d DWD stations within %.0f km", len(stations), radius_km)
        return stations

    def get_obs(self, repo: InMemoryRepository, station_ids: set[str], start: datetime, end: datetime) -> list[Observation]:
        if not self._live_enabled():
            return []

        # Derive WMO IDs from "BS_{wmo_id}" station IDs
        wmo_to_id = {sid[3:]: sid for sid in station_ids if sid.startswith("BS_")}
        if not wmo_to_id:
            return []

        rows: list[Observation] = []
        try:
            with httpx.Client(timeout=self.settings.request_timeout_seconds) as client:
                for wmo_id, internal_id in wmo_to_id.items():
                    try:
                        resp = client.get(self._WEATHER_URL, params={
                            "wmo_station_id": wmo_id,
                            "date":           start.strftime("%Y-%m-%dT%H:%M"),
                            "last_date":      end.strftime("%Y-%m-%dT%H:%M"),
                            "tz":             "UTC",
                            "units":          "si",
                        })
                        resp.raise_for_status()
                        payload = resp.json()
                    except Exception as exc:
                        logger.warning("BrightSky fetch failed for WMO %s", wmo_id, exc_info=exc)
                        continue
                    for record in payload.get("weather", []):
                        wd    = record.get("wind_direction")
                        ws    = record.get("wind_speed")
                        ts_raw = record.get("timestamp")
                        if wd is None or ws is None or not ts_raw:
                            continue
                        try:
                            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                            if ts.tzinfo is None:
                                ts = ts.replace(tzinfo=UTC)
                        except ValueError:
                            continue
                        if ts < start or ts > end:
                            continue
                        rows.append(Observation(
                            station_id=internal_id, source=self.source_name,
                            time_utc=ts, ws_ms=float(ws), wd_deg=float(wd),
                        ))
        except Exception as exc:
            logger.warning("BrightSky fetch failed", exc_info=exc)
            return []

        if rows:
            logger.info("BrightSky fetch: %d obs, latest=%s", len(rows), max(r.time_utc for r in rows).isoformat())
        else:
            logger.warning("BrightSky fetch: 0 obs for %d stations", len(wmo_to_id))
        return rows


class ItalyRegionalAdapter(BaseSourceAdapter):
    source_name = "italy_regional"
