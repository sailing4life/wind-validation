from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.catalog import select_candidate_models
from app.repositories import InMemoryRepository
from app.schemas import ForecastPushRequest


def test_fuxicfd_is_the_top_candidate_at_palma_only():
    models = InMemoryRepository().models
    palma, _ = select_candidate_models(lat=39.499, lon=2.702, catalog=models, coverage_availability={})
    assert palma[0].model_id == "fuxicfd_palma"

    # The tiny bay domain must not leak the model into other regions.
    elsewhere, _ = select_candidate_models(lat=45.46, lon=9.19, catalog=models, coverage_availability={})
    assert all(m.model_id != "fuxicfd_palma" for m in elsewhere)


def test_push_schema_requires_points_and_accepts_a_run():
    with pytest.raises(ValidationError):
        ForecastPushRequest(model_id="fuxicfd_palma", run_time_utc=datetime(2026, 7, 30, 6, tzinfo=UTC), points=[])

    req = ForecastPushRequest(
        model_id="fuxicfd_palma",
        run_time_utc=datetime(2026, 7, 30, 6, tzinfo=UTC),
        points=[{"valid_time_utc": datetime(2026, 7, 30, 7, tzinfo=UTC), "lat": 39.5, "lon": 2.7, "u10": 1.0, "v10": -4.0}],
    )
    assert req.points[0].gust_ms is None
