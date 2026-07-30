from __future__ import annotations

import json
import logging
import os
from datetime import datetime
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
                    CREATE TABLE IF NOT EXISTS briefings (
                        briefing_id TEXT PRIMARY KEY, saved_at TIMESTAMPTZ NOT NULL,
                        title TEXT, lat DOUBLE PRECISION, lon DOUBLE PRECISION, payload JSONB NOT NULL
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
        values = [(r.source, r.station_id, r.time_utc, r.ws_ms, r.wd_deg, r.qc_passed, json.dumps(r.qc_flags)) for r in rows]
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO observations (source, station_id, time_utc, ws_ms, wd_deg, qc_passed, qc_flags)
                    VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)
                    ON CONFLICT (source, station_id, time_utc) DO UPDATE SET
                      ws_ms=EXCLUDED.ws_ms, wd_deg=EXCLUDED.wd_deg, qc_passed=EXCLUDED.qc_passed, qc_flags=EXCLUDED.qc_flags
                """, values)
        except Exception:
            logger.exception("Could not archive observations")

    def save_forecasts(self, rows: Iterable[ForecastValue]) -> None:
        rows = list(rows)
        if not self.enabled or not rows:
            return
        values = [(r.model_id, r.run_time_utc, r.valid_time_utc, r.lat, r.lon, r.u10, r.v10, r.gust_ms, r.temp_c,
                   r.precip_mm, r.cloud_cover_pct, r.pressure_msl_hpa) for r in rows]
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO forecast_runs (model_id, run_time_utc, valid_time_utc, lat, lon, u10, v10, gust_ms, temp_c, precip_mm, cloud_cover_pct, pressure_msl_hpa)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (model_id, run_time_utc, valid_time_utc, lat, lon) DO UPDATE SET
                      u10=EXCLUDED.u10, v10=EXCLUDED.v10, gust_ms=EXCLUDED.gust_ms, temp_c=EXCLUDED.temp_c,
                      precip_mm=EXCLUDED.precip_mm, cloud_cover_pct=EXCLUDED.cloud_cover_pct, pressure_msl_hpa=EXCLUDED.pressure_msl_hpa
                """, values)
        except Exception:
            logger.exception("Could not archive forecast runs")

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
