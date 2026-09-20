from __future__ import annotations

import json
import logging
import math
import os
from datetime import date, datetime, timedelta
from typing import Iterable

from .domain import ForecastValue, Observation

logger = logging.getLogger("wind_validation.storage")


class PostgresStore:
    """Optional durable archive. Disabled locally unless DATABASE_URL is set."""

    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url or os.getenv("DATABASE_URL")
        self.enabled = bool(self.database_url)

    def _connect(self):
        import psycopg  # installed in the production image via requirements.txt
        return psycopg.connect(self.database_url, connect_timeout=5)

    def initialize(self) -> None:
        if not self.enabled:
            logger.info("Postgres archive disabled: DATABASE_URL is not configured")
            return
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS observations (
                        source TEXT NOT NULL, station_id TEXT NOT NULL, time_utc TIMESTAMPTZ NOT NULL,
                        ws_ms DOUBLE PRECISION NOT NULL, wd_deg DOUBLE PRECISION NOT NULL,
                        qc_passed BOOLEAN NOT NULL DEFAULT TRUE, qc_flags JSONB,
                        archived_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        PRIMARY KEY (source, station_id, time_utc)
                    );
                    CREATE INDEX IF NOT EXISTS observations_station_time_idx ON observations (station_id, time_utc DESC);
                    ALTER TABLE observations ADD COLUMN IF NOT EXISTS gust_ms DOUBLE PRECISION;
                    CREATE TABLE IF NOT EXISTS forecast_runs (
                        model_id TEXT NOT NULL, run_time_utc TIMESTAMPTZ NOT NULL, valid_time_utc TIMESTAMPTZ NOT NULL,
                        lat DOUBLE PRECISION NOT NULL, lon DOUBLE PRECISION NOT NULL,
                        u10 DOUBLE PRECISION NOT NULL, v10 DOUBLE PRECISION NOT NULL,
                        gust_ms DOUBLE PRECISION, temp_c DOUBLE PRECISION, precip_mm DOUBLE PRECISION,
                        cloud_cover_pct DOUBLE PRECISION, pressure_msl_hpa DOUBLE PRECISION,
                        archived_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        PRIMARY KEY (model_id, run_time_utc, valid_time_utc, lat, lon)
                    );
                    CREATE INDEX IF NOT EXISTS forecast_valid_idx ON forecast_runs (model_id, valid_time_utc DESC);
                    CREATE TABLE IF NOT EXISTS forecast_observation_pairs (
                        model_id TEXT NOT NULL, run_time_utc TIMESTAMPTZ NOT NULL, valid_time_utc TIMESTAMPTZ NOT NULL,
                        station_id TEXT NOT NULL, station_source TEXT NOT NULL, station_type TEXT NOT NULL,
                        station_lat DOUBLE PRECISION NOT NULL, station_lon DOUBLE PRECISION NOT NULL,
                        obs_time_utc TIMESTAMPTZ NOT NULL, model_u DOUBLE PRECISION NOT NULL, model_v DOUBLE PRECISION NOT NULL,
                        obs_u DOUBLE PRECISION NOT NULL, obs_v DOUBLE PRECISION NOT NULL,
                        lead_hours DOUBLE PRECISION NOT NULL, local_solar_hour DOUBLE PRECISION NOT NULL,
                        archived_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        PRIMARY KEY (model_id, run_time_utc, valid_time_utc, station_id)
                    );
                    CREATE INDEX IF NOT EXISTS forecast_obs_pairs_model_time_idx
                        ON forecast_observation_pairs (model_id, obs_time_utc DESC);
                    CREATE TABLE IF NOT EXISTS briefings (
                        briefing_id TEXT PRIMARY KEY, saved_at TIMESTAMPTZ NOT NULL,
                        title TEXT, lat DOUBLE PRECISION, lon DOUBLE PRECISION, payload JSONB NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS saved_locations (
                        location_id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                        lat DOUBLE PRECISION NOT NULL, lon DOUBLE PRECISION NOT NULL,
                        radius_km DOUBLE PRECISION NOT NULL DEFAULT 50, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    );
                    ALTER TABLE forecast_runs ADD COLUMN IF NOT EXISTS run_time_source TEXT NOT NULL DEFAULT 'legacy_unknown';
                    ALTER TABLE forecast_runs ADD COLUMN IF NOT EXISTS fetched_at_utc TIMESTAMPTZ;
                    ALTER TABLE forecast_runs ADD COLUMN IF NOT EXISTS shortwave_wm2 DOUBLE PRECISION;
                    ALTER TABLE forecast_runs ADD COLUMN IF NOT EXISTS cape_jkg DOUBLE PRECISION;
                    ALTER TABLE forecast_runs ADD COLUMN IF NOT EXISTS boundary_layer_height_m DOUBLE PRECISION;
                    ALTER TABLE forecast_observation_pairs ADD COLUMN IF NOT EXISTS run_time_source TEXT NOT NULL DEFAULT 'legacy_unknown';
                    ALTER TABLE forecast_observation_pairs ADD COLUMN IF NOT EXISTS fetched_at_utc TIMESTAMPTZ;
                    ALTER TABLE saved_locations ADD COLUMN IF NOT EXISTS monitoring_enabled BOOLEAN NOT NULL DEFAULT FALSE;
                    ALTER TABLE saved_locations ADD COLUMN IF NOT EXISTS monitoring_start DATE;
                    ALTER TABLE saved_locations ADD COLUMN IF NOT EXISTS monitoring_end DATE;
                    CREATE TABLE IF NOT EXISTS location_snapshots (
                        location_id BIGINT PRIMARY KEY REFERENCES saved_locations(location_id) ON DELETE CASCADE,
                        current_payload JSONB NOT NULL, previous_payload JSONB,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    );
                """)
            logger.info("Postgres archive schema ready")
        except Exception:
            logger.exception("Postgres archive initialization failed; continuing without persistence")
            self.enabled = False

    def save_observations(self, rows: Iterable[Observation]) -> None:
        rows = list(rows)
        if not self.enabled or not rows:
            return
        values = [(r.source, r.station_id, r.time_utc, r.ws_ms, r.wd_deg, r.qc_passed, json.dumps(r.qc_flags), r.gust_ms) for r in rows]
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO observations (source, station_id, time_utc, ws_ms, wd_deg, qc_passed, qc_flags, gust_ms)
                    VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                    ON CONFLICT (source, station_id, time_utc) DO UPDATE SET
                      ws_ms=EXCLUDED.ws_ms, wd_deg=EXCLUDED.wd_deg, qc_passed=EXCLUDED.qc_passed, qc_flags=EXCLUDED.qc_flags, gust_ms=EXCLUDED.gust_ms
                """, values)
        except Exception:
            logger.exception("Could not archive observations")

    def save_forecasts(self, rows: Iterable[ForecastValue]) -> None:
        rows = list(rows)
        if not self.enabled or not rows:
            return
        values = [(r.model_id, r.run_time_utc, r.valid_time_utc, r.lat, r.lon, r.u10, r.v10, r.gust_ms, r.temp_c,
                   r.precip_mm, r.cloud_cover_pct, r.pressure_msl_hpa, r.shortwave_wm2, r.cape_jkg,
                   r.boundary_layer_height_m, r.run_time_source, r.fetched_at_utc) for r in rows]
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO forecast_runs (model_id, run_time_utc, valid_time_utc, lat, lon, u10, v10, gust_ms, temp_c, precip_mm, cloud_cover_pct, pressure_msl_hpa, shortwave_wm2, cape_jkg, boundary_layer_height_m, run_time_source, fetched_at_utc)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (model_id, run_time_utc, valid_time_utc, lat, lon) DO NOTHING
                """, values)
        except Exception:
            logger.exception("Could not archive forecast runs")

    def save_forecast_observation_pairs(self, rows: Iterable[dict]) -> None:
        """Archive only causal pairs: the forecast run predates the observation."""
        rows = list(rows)
        if not self.enabled or not rows:
            return
        values = [(
            r["model_id"], r["run_time_utc"], r["valid_time_utc"], r["station_id"], r["station_source"],
            r["station_type"], r["station_lat"], r["station_lon"], r["obs_time_utc"], r["model_u"],
            r["model_v"], r["obs_u"], r["obs_v"], r["lead_hours"], r["local_solar_hour"],
            r.get("run_time_source", "source"), r.get("fetched_at_utc"),
        ) for r in rows if r["run_time_utc"] <= r["obs_time_utc"]
            and (r.get("run_time_source", "source") == "source" or (
                r.get("run_time_source") == "fetched_snapshot" and r.get("fetched_at_utc") is not None
                and r["fetched_at_utc"] <= r["obs_time_utc"]
                and r["fetched_at_utc"] <= r["valid_time_utc"]))]
        if not values:
            return
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO forecast_observation_pairs (
                      model_id,run_time_utc,valid_time_utc,station_id,station_source,station_type,station_lat,station_lon,
                      obs_time_utc,model_u,model_v,obs_u,obs_v,lead_hours,local_solar_hour,run_time_source,fetched_at_utc
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (model_id,run_time_utc,valid_time_utc,station_id) DO NOTHING
                """, values)
        except Exception:
            logger.exception("Could not archive forecast-observation pairs")

    @staticmethod
    def _bbox(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
        lat_delta = radius_km / 111.0
        lon_delta = radius_km / (111.0 * max(0.2, math.cos(math.radians(lat))))
        return lat - lat_delta, lat + lat_delta, lon - lon_delta, lon + lon_delta

    def recent_forecast_observation_pairs(
        self, model_id: str, now: datetime, lat: float, lon: float,
        radius_km: float = 75.0, days: int = 60, limit: int = 20000,
    ) -> list[dict]:
        """Return durable verification pairs near a point; the fine-grained
        relevance weighting is resolved at query time."""
        if not self.enabled:
            return []
        lat_min, lat_max, lon_min, lon_max = self._bbox(lat, lon, radius_km)
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute("""
                    SELECT station_id,station_source,station_type,station_lat,station_lon,obs_time_utc,
                           model_u,model_v,obs_u,obs_v,local_solar_hour,lead_hours
                    FROM forecast_observation_pairs
                    WHERE model_id=%s AND obs_time_utc >= %s AND obs_time_utc <= %s
                      AND station_lat BETWEEN %s AND %s AND station_lon BETWEEN %s AND %s
                      AND (run_time_source='source' OR (run_time_source='fetched_snapshot' AND fetched_at_utc <= obs_time_utc AND fetched_at_utc <= valid_time_utc))
                    ORDER BY obs_time_utc DESC
                    LIMIT %s
                """, (model_id, now - timedelta(days=days), now, lat_min, lat_max, lon_min, lon_max, limit))
                keys = ("station_id", "station_source", "station_type", "station_lat", "station_lon", "obs_time_utc",
                        "model_u", "model_v", "obs_u", "obs_v", "local_solar_hour", "lead_hours")
                return [dict(zip(keys, row)) for row in cur.fetchall()]
        except Exception:
            logger.exception("Could not load forecast-observation pairs")
            return []

    def load_forecasts(
        self, model_ids: list[str], start: datetime, end: datetime,
        lat: float, lon: float, radius_km: float = 75.0, limit: int = 50000,
    ) -> list[ForecastValue]:
        """Read archived point forecasts back, e.g. when an on-demand GRIB
        source (ALADIN, OpenWRF) is unreachable during an event."""
        if not self.enabled or not model_ids:
            return []
        lat_min, lat_max, lon_min, lon_max = self._bbox(lat, lon, radius_km)
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute("""
                    SELECT model_id, run_time_utc, valid_time_utc, lat, lon, u10, v10,
                           gust_ms, temp_c, precip_mm, cloud_cover_pct, pressure_msl_hpa,
                           shortwave_wm2, cape_jkg, boundary_layer_height_m, run_time_source, fetched_at_utc
                    FROM forecast_runs
                    WHERE model_id = ANY(%s) AND valid_time_utc >= %s AND valid_time_utc <= %s
                      AND lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s
                    ORDER BY valid_time_utc DESC, run_time_utc DESC
                    LIMIT %s
                """, (model_ids, start, end, lat_min, lat_max, lon_min, lon_max, limit))
                return [ForecastValue(*row) for row in cur.fetchall()]
        except Exception:
            logger.exception("Could not load archived forecasts")
            return []

    def save_briefing(self, briefing_id: str, payload: dict, saved_at: datetime) -> None:
        if not self.enabled:
            return
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO briefings (briefing_id, saved_at, title, lat, lon, payload)
                    VALUES (%s,%s,%s,%s,%s,%s::jsonb)
                    ON CONFLICT (briefing_id) DO UPDATE SET payload=EXCLUDED.payload, saved_at=EXCLUDED.saved_at
                """, (briefing_id, saved_at, payload.get("title"), payload.get("lat"), payload.get("lon"), json.dumps(payload)))
        except Exception:
            logger.exception("Could not archive briefing")

    @staticmethod
    def _location(row) -> dict:
        keys = ("id", "name", "lat", "lon", "radius_km", "monitoring_enabled", "monitoring_start", "monitoring_end")
        return {k: v.isoformat() if isinstance(v, date) else v for k, v in zip(keys, row)}

    def list_locations(self) -> list[dict]:
        if not self.enabled:
            return []
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT location_id, name, lat, lon, radius_km, monitoring_enabled, monitoring_start, monitoring_end FROM saved_locations ORDER BY name")
            return [self._location(row) for row in cur.fetchall()]

    def get_location(self, location_id: int) -> dict | None:
        if not self.enabled:
            return None
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT location_id, name, lat, lon, radius_km, monitoring_enabled, monitoring_start, monitoring_end FROM saved_locations WHERE location_id=%s", (location_id,))
            row = cur.fetchone()
            return self._location(row) if row else None

    def save_location(self, name: str, lat: float, lon: float, radius_km: float) -> dict:
        with self._connect() as conn, conn.cursor() as cur:
            # Location identity includes its coordinates. Invalidate prepared data if they change.
            cur.execute("""DELETE FROM location_snapshots WHERE location_id IN
                (SELECT location_id FROM saved_locations WHERE name=%s AND (lat<>%s OR lon<>%s OR radius_km<>%s))""", (name, lat, lon, radius_km))
            cur.execute("""INSERT INTO saved_locations (name,lat,lon,radius_km) VALUES (%s,%s,%s,%s)
                ON CONFLICT (name) DO UPDATE SET lat=EXCLUDED.lat,lon=EXCLUDED.lon,radius_km=EXCLUDED.radius_km
                RETURNING location_id,name,lat,lon,radius_km,monitoring_enabled,monitoring_start,monitoring_end""", (name,lat,lon,radius_km))
            return self._location(cur.fetchone())

    def update_location_monitoring(self, location_id: int, monitoring_enabled: bool,
                                   monitoring_start: str | date | None = None,
                                   monitoring_end: str | date | None = None) -> dict | None:
        start = date.fromisoformat(monitoring_start) if isinstance(monitoring_start, str) else monitoring_start
        end = date.fromisoformat(monitoring_end) if isinstance(monitoring_end, str) else monitoring_end
        if start and end and start > end:
            raise ValueError("Monitoring start must precede end")
        if not self.enabled:
            return None
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("""UPDATE saved_locations SET monitoring_enabled=%s,monitoring_start=%s,monitoring_end=%s
                WHERE location_id=%s RETURNING location_id,name,lat,lon,radius_km,monitoring_enabled,monitoring_start,monitoring_end""",
                (monitoring_enabled, start, end, location_id))
            row = cur.fetchone()
            return self._location(row) if row else None

    def read_location_snapshot(self, location_id: int) -> dict | None:
        if not self.enabled:
            return None
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT current_payload FROM location_snapshots WHERE location_id=%s", (location_id,))
            row = cur.fetchone()
            return row[0] if row else None

    def save_location_snapshot(self, location_id: int, payload: dict) -> None:
        if not self.enabled:
            return
        with self._connect() as conn, conn.cursor() as cur:
            location = payload["location"]
            cur.execute("""INSERT INTO location_snapshots (location_id,current_payload)
                SELECT location_id,%s::jsonb FROM saved_locations
                WHERE location_id=%s AND lat=%s AND lon=%s AND radius_km=%s
                ON CONFLICT (location_id) DO UPDATE SET previous_payload=location_snapshots.current_payload,
                    current_payload=EXCLUDED.current_payload,updated_at=now()""",
                (json.dumps(payload, default=lambda value: value.isoformat() if isinstance(value, (datetime, date)) else str(value)),
                 location_id, location["lat"], location["lon"], location["radius_km"]))
            if cur.rowcount == 0:
                raise ValueError("Location changed while its snapshot was being prepared")

    def record_location_refresh_error(self, location_id: int, message: str) -> None:
        """Persist worker health without replacing the last complete forecast."""
        if not self.enabled:
            return
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO location_snapshots (location_id,current_payload)
                VALUES (%s,%s::jsonb)
                ON CONFLICT (location_id) DO UPDATE SET
                    current_payload=location_snapshots.current_payload || jsonb_build_object('last_error',%s::text),
                    updated_at=now()""", (location_id, json.dumps({"status": "error", "computed_at_utc": None,
                    "validation": None, "forecast": None, "run_comparison": None, "last_error": message}), message))
