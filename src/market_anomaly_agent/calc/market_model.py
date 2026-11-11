"""Market-model regression, abnormal return and z-score (FR-006 to FR-008).

Plain NumPy closed-form OLS (research.md §4): beta = Sxy / Sxx, alpha = mean(y) - beta * mean(x),
residual standard deviation with n - 2 degrees of freedom.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class MarketModel:
    alpha: float
    beta: float
    residual_std: float
    n: int


def fit_market_model(stock: Sequence[float], benchmark: Sequence[float]) -> MarketModel:
    """Regress `stock` returns on `benchmark` returns over the same days."""
    if len(stock) != len(benchmark):
        raise ValueError(f"length mismatch: {len(stock)} stock vs {len(benchmark)} benchmark")
    n = len(stock)
    if n < 3:
        raise ValueError(f"need at least 3 observations for n - 2 degrees of freedom, got {n}")

    y = np.asarray(stock, dtype=np.float64)
    x = np.asarray(benchmark, dtype=np.float64)
    dx = x - x.mean()
    sxx = float(np.dot(dx, dx))
    if sxx == 0.0:
        raise ValueError("benchmark returns are constant, so beta is undefined")

    beta = float(np.dot(dx, y - y.mean())) / sxx
    alpha = float(y.mean()) - beta * float(x.mean())
    residuals = y - (alpha + beta * x)
    residual_std = math.sqrt(float(np.dot(residuals, residuals)) / (n - 2))
    return MarketModel(alpha=alpha, beta=beta, residual_std=residual_std, n=n)


def expected_return(model: MarketModel, benchmark_return: float) -> float:
    return model.alpha + model.beta * benchmark_return


def abnormal_return(actual: float, expected: float) -> float:
    return actual - expected


def z_score(abnormal: float, residual_std: float) -> float:
    if not residual_std > 0.0 or not math.isfinite(residual_std):
        raise ValueError(
            f"z-score needs a positive residual standard deviation, got {residual_std}"
        )
    return abnormal / residual_std
