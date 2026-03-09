from __future__ import annotations

from datetime import datetime

from .adapters import BaseSourceAdapter, BrightSkyAdapter, IsdAdapter, ItalyRegionalAdapter, KnmiAdapter, MetarAdapter, MeteoFranceAdapter
from .config import Settings
from .domain import Observation, Station
from .qc import qc_observations
from .repositories import InMemoryRepository


class ObservationBroker:
    def __init__(self, repo: InMemoryRepository, settings: Settings) -> None:
        self.repo = repo
        self.settings = settings
        self._knmi = KnmiAdapter(settings)
        self._mf = MeteoFranceAdapter(settings)
        self._isd = IsdAdapter(settings)
        self._it = ItalyRegionalAdapter(settings)
        self._metar = MetarAdapter(settings)
        self._brightsky = BrightSkyAdapter(settings)

    def _source_order(self, country: str) -> list[BaseSourceAdapter]:
        if country == "NL":
            return [self._metar, self._knmi, self._isd]
        if country == "FR":
            return [self._metar, self._mf, self._isd]
        if country == "IT":
            extra = [self._it] if self.settings.italy_regional_enabled else []
            return [self._metar] + extra + [self._isd]
        # OTHER includes DE, BE, GB, etc. — METAR + BrightSky (DWD) + ISD fallback
        return [self._metar, self._brightsky, self._isd]

    def list_stations(self, country: str, lat: float, lon: float, radius_km: float) -> list[Station]:
        stations: list[Station] = []
        seen: set[str] = set()
        for adapter in self._source_order(country):
            for row in adapter.list_stations(self.repo, lat, lon, radius_km):
                if row.station_id not in seen:
                    seen.add(row.station_id)
                    stations.append(row)
        return stations

    def get_observations(
        self,
        country: str,
        station_ids: set[str],
        start: datetime,
        end: datetime,
    ) -> tuple[list[Observation], list[str]]:
        rows: list[Observation] = []
        provenance: list[str] = []
        for adapter in self._source_order(country):
            source_rows = adapter.get_obs(self.repo, station_ids, start, end)
            if source_rows:
                rows.extend(source_rows)
                provenance.append(adapter.source_name)

        cleaned = qc_observations(rows, start, end)
        passed = [r for r in cleaned if r.qc_passed]
        return passed, provenance
