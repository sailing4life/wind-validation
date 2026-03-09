from wind_validation.app.scoring import circular_direction_error_deg, compute_metrics


def test_circular_direction_error_wrap():
    assert circular_direction_error_deg(350, 10) == 20
    assert circular_direction_error_deg(10, 350) == 20


def test_compute_metrics_basic():
    obs = [(1.0, 0.0), (0.0, 1.0)]
    fc = [(1.0, 0.0), (0.5, 1.0)]
    result = compute_metrics(obs, fc)
    assert result.n_samples == 2
    assert result.vector_rmse_uv > 0
    assert result.mae_ws >= 0