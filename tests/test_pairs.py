from datetime import UTC, datetime

from app.domain import ForecastValue, Observation, Station
from app.services import build_pair_rows, nearest_forecast


def _fv(run_hour: int, valid_hour: int) -> ForecastValue:
    return ForecastValue(
        model_id="m1",
        run_time_utc=datetime(2026, 7, 30, run_hour, tzinfo=UTC),
        valid_time_utc=datetime(2026, 7, 30, valid_hour, tzinfo=UTC),
        lat=39.5, lon=2.7, u10=0.0, v10=-5.0,
    )


def test_build_pair_rows_is_causal_and_prefers_the_newest_available_run():
    station = Station("S1", "socib", "ES", 39.5, 2.7, station_type="buoy")
    obs = Observation("S1", "socib", datetime(2026, 7, 30, 10, 15, tzinfo=UTC), 5.0, 0.0)
    old_run, newer_run, hindsight_run = _fv(6, 10), _fv(9, 10), _fv(11, 10)
    fc_index = {("m1", datetime(2026, 7, 30, 10, tzinfo=UTC)): [old_run, newer_run, hindsight_run]}

    rows = build_pair_rows([obs], {"S1": station}, fc_index)

    assert len(rows) == 1
    assert rows[0]["run_time_utc"] == newer_run.run_time_utc
    assert rows[0]["lead_hours"] == 1.0
    assert rows[0]["station_type"] == "buoy"


def test_nearest_forecast_returns_none_when_only_hindsight_runs_exist():
    hindsight_run = _fv(11, 10)

    nearest = nearest_forecast([hindsight_run], 39.5, 2.7, not_after=datetime(2026, 7, 30, 10, 15, tzinfo=UTC))

    assert nearest is None
