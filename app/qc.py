from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from .domain import Observation


def qc_observations(rows: list[Observation], start: datetime, end: datetime) -> list[Observation]:
    grouped: dict[str, list[Observation]] = defaultdict(list)
    for row in rows:
        grouped[row.station_id].append(row)

    cleaned: list[Observation] = []
    for station_rows in grouped.values():
        station_rows.sort(key=lambda r: r.time_utc)
        stuck_count = 1
        prev: Observation | None = None
        for row in station_rows:
            flags: list[str] = []
            if row.time_utc < start or row.time_utc > end:
                flags.append("outside_window")
            if row.ws_ms < 0 or row.ws_ms > 75:
                flags.append("speed_out_of_range")
            if row.wd_deg < 0 or row.wd_deg >= 360:
                flags.append("direction_out_of_range")

            if prev is not None:
                delta_t = row.time_utc - prev.time_utc
                if delta_t <= timedelta(hours=2) and abs(row.ws_ms - prev.ws_ms) > 20:
                    flags.append("speed_jump")
                if abs(row.ws_ms - prev.ws_ms) < 1e-6 and abs(row.wd_deg - prev.wd_deg) < 1e-6:
                    stuck_count += 1
                else:
                    stuck_count = 1
            if stuck_count >= 6:
                flags.append("sensor_stuck")

            row.qc_flags = flags
            row.qc_passed = len(flags) == 0
            cleaned.append(row)
            prev = row

    return cleaned