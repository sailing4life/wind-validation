from datetime import datetime, timedelta, timezone

from wind_validation.app.domain import Observation
from wind_validation.app.qc import qc_observations


def test_qc_drops_invalid_wind_values():
    now = datetime.now(timezone.utc)
    rows = [
        Observation("S1", "knmi", now, -1.0, 10),
        Observation("S1", "knmi", now + timedelta(hours=1), 10.0, 370),
        Observation("S1", "knmi", now + timedelta(hours=2), 6.0, 180),
    ]
    cleaned = qc_observations(rows, now - timedelta(hours=1), now + timedelta(hours=3))
    passed = [r for r in cleaned if r.qc_passed]
    assert len(passed) == 1
    assert passed[0].ws_ms == 6.0


def test_qc_sensor_stuck_flag():
    now = datetime.now(timezone.utc)
    rows = [Observation("S2", "knmi", now + timedelta(hours=i), 5.0, 90.0) for i in range(7)]
    cleaned = qc_observations(rows, now, now + timedelta(hours=10))
    assert any("sensor_stuck" in (row.qc_flags or []) for row in cleaned)