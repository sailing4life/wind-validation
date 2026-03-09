from wind_validation.app.catalog import default_model_catalog, select_candidate_models


def test_catalog_selects_coverage_models_plus_baseline():
    catalog = default_model_catalog()
    availability = {m.model_id: 1.0 for m in catalog}
    selected, reasons = select_candidate_models(
        lat=52.1,
        lon=5.1,
        catalog=catalog,
        coverage_availability=availability,
    )
    ids = [m.model_id for m in selected]
    assert "harmonie_nl" in ids
    assert "ecmwf_global" in ids
    assert reasons == {}


def test_catalog_excludes_low_availability_model():
    catalog = default_model_catalog()
    availability = {m.model_id: 1.0 for m in catalog}
    availability["harmonie_nl"] = 0.7
    selected, reasons = select_candidate_models(
        lat=52.1,
        lon=5.1,
        catalog=catalog,
        coverage_availability=availability,
    )
    ids = [m.model_id for m in selected]
    assert "harmonie_nl" not in ids
    assert reasons["harmonie_nl"] == "excluded_missing_coverage"