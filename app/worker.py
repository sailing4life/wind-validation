"""Run one durable location collector: python -m app.worker.

Set DATABASE_URL and run the web process with BACKGROUND_REFRESH_ENABLED=false
when this standalone process is used. The worker does not import the web app.
"""
from __future__ import annotations

import logging
import signal
import threading

from .config import SETTINGS
from .forecast_broker import ForecastBroker
from .location_fingerprint import LocationFingerprintService
from .monitoring import LocationMonitoringService
from .observation_broker import ObservationBroker
from .repositories import InMemoryRepository
from .services import ValidationService
from .storage import PostgresStore


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("wind_validation.worker")
    store = PostgresStore()
    store.initialize()
    if not store.enabled:
        raise SystemExit("The location worker requires an available Postgres DATABASE_URL")
    repo = InMemoryRepository()
    forecasts = ForecastBroker(repo, SETTINGS)
    observations = ObservationBroker(repo, SETTINGS)
    validation = ValidationService(repo, observations, forecasts.openmeteo, SETTINGS,
                                   fingerprint_service=LocationFingerprintService(SETTINGS), store=store)
    monitoring = LocationMonitoringService(repo, forecasts, observations, validation, store)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    while not stop.is_set():
        try:
            monitoring.refresh()
        except Exception:
            logger.exception("Location collection failed; retrying on the next cycle")
        stop.wait(max(30, SETTINGS.refresh_interval_seconds))


if __name__ == "__main__":
    main()
