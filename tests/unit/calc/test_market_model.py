import math

import pytest

from market_anomaly_agent.calc.market_model import (
    abnormal_return,
    expected_return,
    fit_market_model,
    z_score,
)

BENCHMARK = [0.01, 0.02, -0.01, 0.00, 0.03]
STOCK = [0.012, 0.025, -0.008, 0.001, 0.031]


def test_hand_checked_five_point_regression() -> None:
    """OLS of stock on benchmark returns (FR-006, FR-008), worked by hand.

    n = 5
    mean x (benchmark) = 0.05 / 5 = 0.01
    mean y (stock)     = 0.061 / 5 = 0.0122
    x - mean x = 0, 0.01, -0.02, -0.01, 0.02
    y - mean y = -0.0002, 0.0128, -0.0202, -0.0112, 0.0188
    Sxy = 0 + 0.000128 + 0.000404 + 0.000112 + 0.000376 = 0.00102
    Sxx = 0 + 0.0001 + 0.0004 + 0.0001 + 0.0004 = 0.001
    beta  = Sxy / Sxx = 1.02
    alpha = 0.0122 - 1.02 * 0.01 = 0.002
    fitted    = 0.0122, 0.0224, -0.0082, 0.002, 0.0326
    residuals = -0.0002, 0.0026, 0.0002, -0.001, -0.0016   (sum 0)
    sum of squared residuals = 4e-8 + 6.76e-6 + 4e-8 + 1e-6 + 2.56e-6 = 1.04e-5
    residual_std = sqrt(1.04e-5 / (n - 2)) = sqrt(3.4666...e-6) = 0.0018618986...
    """
    model = fit_market_model(STOCK, BENCHMARK)

    assert model.n == 5
    assert model.alpha == pytest.approx(0.002, abs=1e-12)
    assert model.beta == pytest.approx(1.02, abs=1e-12)
    assert model.residual_std == pytest.approx(math.sqrt(1.04e-5 / 3), abs=1e-12)


def test_hand_checked_expected_abnormal_and_z() -> None:
    """FR-007, FR-008 with the model above and a benchmark return of 0.015 on the day.

    expected = 0.002 + 1.02 * 0.015 = 0.0173
    abnormal = 0.03 - 0.0173 = 0.0127
    z        = 0.0127 / 0.0018618986... = 6.8209...
    """
    model = fit_market_model(STOCK, BENCHMARK)

    expected = expected_return(model, 0.015)
    abnormal = abnormal_return(0.03, expected)
    z = z_score(abnormal, model.residual_std)

    assert expected == pytest.approx(0.0173, abs=1e-12)
    assert abnormal == pytest.approx(0.0127, abs=1e-12)
    assert z == pytest.approx(0.0127 / math.sqrt(1.04e-5 / 3), rel=1e-12)


def test_perfect_fit_has_zero_residual_std_and_no_z_score() -> None:
    """y = 1 + 2x exactly: every residual is 0, so residual_std is 0 and z is undefined."""
    model = fit_market_model([1.0, 3.0, 5.0, 7.0], [0.0, 1.0, 2.0, 3.0])

    assert model.alpha == 1.0
    assert model.beta == 2.0
    assert model.residual_std == 0.0
    with pytest.raises(ValueError):
        z_score(0.01, model.residual_std)


def test_mismatched_lengths_raise() -> None:
    with pytest.raises(ValueError):
        fit_market_model([0.01, 0.02, 0.03], [0.01, 0.02])


def test_fewer_than_three_points_raise() -> None:
    with pytest.raises(ValueError):
        fit_market_model([0.01, 0.02], [0.01, 0.03])


def test_constant_benchmark_raises() -> None:
    """Sxx = 0 leaves beta undefined."""
    with pytest.raises(ValueError):
        fit_market_model([0.01, 0.02, 0.03], [0.01, 0.01, 0.01])
