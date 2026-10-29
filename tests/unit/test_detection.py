"""Detection orchestration against a fake price source (no database, no network).

The price history is constructed so the market-model numbers are known exactly:

- For the 120 trading days before the evaluated day D (index i = 1..120), the benchmark return is
  m_i = +0.01 for even i and -0.01 for odd i (mean 0).
- A stock's return is s_i = alpha + beta * m_i + k * e_i, where e_i = +0.002 for i % 4 in (1, 2)
  and -0.002 otherwise. e has mean 0 and is orthogonal to m (sum of m_i * e_i over every block of
  four days is 0), so OLS recovers alpha and beta exactly and the residuals are k * e_i.
- Residual standard deviation with n - 2 degrees of freedom:
  sqrt(120 * (k * 0.002)^2 / 118) = k * 0.002 * sqrt(120 / 118).
- On D the benchmark return is 0.005 and the stock return is alpha + beta * 0.005 + abnormal.
"""

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from market_anomaly_agent.calendar import fetch_window_start, trading_days_between
from market_anomaly_agent.data.source import DailyBar
from market_anomaly_agent.detection import DetectionResult, run_detection
from market_anomaly_agent.rules import BENCHMARK_TICKER, RULE_VERSION

D = date(2026, 10, 27)
NOW = datetime(2026, 10, 28, 11, 0, tzinfo=UTC)
DAYS = trading_days_between(fetch_window_start(D), D)  # 122 days: index 0 .. 121 (= D)
BENCHMARK_RETURN_ON_D = 0.005
RESIDUAL_UNIT = 0.002 * math.sqrt(120 / 118)  # residual_std for k = 1


@dataclass(frozen=True)
class StockSpec:
    alpha: float
    beta: float
    k: float  # residual scale
    abnormal: float  # abnormal return on D
    base_volume: int = 1_000_000
    volume_on_d: int = 1_000_000

    @property
    def residual_std(self) -> float:
        return self.k * RESIDUAL_UNIT

    @property
    def expected_on_d(self) -> float:
        return self.alpha + self.beta * BENCHMARK_RETURN_ON_D

    @property
    def z(self) -> float:
        return self.abnormal / self.residual_std


def benchmark_return(i: int) -> float:
    return BENCHMARK_RETURN_ON_D if i == len(DAYS) - 1 else (0.01 if i % 2 == 0 else -0.01)


def residual_pattern(i: int) -> float:
    return 0.002 if i % 4 in (1, 2) else -0.002


def build_bars(returns: Sequence[float], start: Decimal, volumes: Sequence[int]) -> list[DailyBar]:
    """Bars over DAYS whose closes produce exactly `returns` (index 1..121)."""
    closes = [start]
    for r in returns:
        closes.append(closes[-1] * (1 + Decimal(repr(r))))
    return [
        DailyBar(trading_date=day, open=close, close=close, volume=volume)
        for day, close, volume in zip(DAYS, closes, volumes, strict=True)
    ]


def stock_bars(spec: StockSpec) -> list[DailyBar]:
    last = len(DAYS) - 1
    returns = [
        spec.alpha + spec.beta * benchmark_return(i) + spec.k * residual_pattern(i)
        if i < last
        else spec.expected_on_d + spec.abnormal
        for i in range(1, len(DAYS))
    ]
    volumes = [spec.base_volume] * last + [spec.volume_on_d]
    return build_bars(returns, Decimal(100), volumes)


def benchmark_bars() -> list[DailyBar]:
    returns = [benchmark_return(i) for i in range(1, len(DAYS))]
    return build_bars(returns, Decimal(400), [50_000_000] * len(DAYS))


class FakeSource:
    def __init__(self, specs: dict[str, StockSpec]) -> None:
        self.bars = {ticker: stock_bars(spec) for ticker, spec in specs.items()}
        self.bars[BENCHMARK_TICKER] = benchmark_bars()
        self.calls: list[tuple[str, date, date]] = []

    def get_daily_bars(self, ticker: str, start: date, end: date) -> list[DailyBar]:
        self.calls.append((ticker, start, end))
        return [b for b in self.bars[ticker] if start <= b.trading_date <= end]


US1_SPECS = {
    "FLAG": StockSpec(0.001, 1.5, 1.0, 0.02, volume_on_d=3_000_000),  # z ~ +9.92
    "AAA": StockSpec(0.0005, 1.2, 1.0, 0.0),  # z = 0
    "CCC": StockSpec(-0.0002, 1.0, 0.5, -0.0015),  # z ~ -1.49
    "BBB": StockSpec(0.0, 0.8, 1.5, 0.001),  # z ~ +0.33
}


def run(specs: dict[str, StockSpec]) -> tuple[DetectionResult, FakeSource]:
    source = FakeSource(specs)
    return run_detection(D, list(specs), source, NOW), source


def test_fixture_window_is_122_trading_days() -> None:
    assert len(DAYS) == 122
    assert DAYS[-1] == D


def test_flags_the_one_large_abnormal_move() -> None:
    """Spec US1 scenario 1: FLAG has z ~ 9.92 >= 2; its volume ratio is
    3,000,000 / average(20 x 1,000,000) = 3.0."""
    result, _ = run(US1_SPECS)

    assert [(a.ticker, a.rank) for a in result.anomalies] == [("FLAG", 1)]
    case = result.anomalies[0]
    assert case.volume_ratio is not None
    assert float(case.volume_ratio) == pytest.approx(3.0, rel=1e-12)
    assert case.volume_ratio_unavailable_reason is None


def test_every_stock_is_evaluated_with_stored_form_numbers() -> None:
    """Spec US1 scenario 4: non-flagged stocks are evaluated records, not skipped."""
    result, _ = run(US1_SPECS)

    assert [r.ticker for r in result.evaluated] == ["AAA", "BBB", "CCC", "FLAG"]
    assert result.skipped == ()
    for record in result.evaluated:
        spec = US1_SPECS[record.ticker]
        numbers = (
            record.return_,
            record.expected_return,
            record.abnormal_return,
            record.z_score,
            record.alpha,
            record.beta,
        )
        assert all(isinstance(n, Decimal) for n in numbers)
        assert float(record.alpha) == pytest.approx(spec.alpha, abs=1e-12)
        assert float(record.beta) == pytest.approx(spec.beta, abs=1e-9)
        assert float(record.expected_return) == pytest.approx(spec.expected_on_d, abs=1e-12)
        assert float(record.return_) == pytest.approx(spec.expected_on_d + spec.abnormal, abs=1e-12)
        assert float(record.abnormal_return) == pytest.approx(spec.abnormal, abs=1e-12)
        assert float(record.z_score) == pytest.approx(spec.z, abs=1e-8)
        assert record.estimation_window_days == 120


def test_run_metadata_and_benchmark_return() -> None:
    result, _ = run(US1_SPECS)

    assert result.evaluated_date == D
    assert result.rule_version == RULE_VERSION
    assert result.computed_at == NOW
    assert result.benchmark_ticker == BENCHMARK_TICKER
    assert isinstance(result.benchmark_return, Decimal)
    assert float(result.benchmark_return) == pytest.approx(BENCHMARK_RETURN_ON_D, abs=1e-15)


def test_result_carries_the_fetched_bar_snapshot() -> None:
    result, source = run(US1_SPECS)

    assert set(result.bars) == {*US1_SPECS, BENCHMARK_TICKER}
    for ticker, bars in result.bars.items():
        assert list(bars) == source.bars[ticker]


def test_fetches_the_window_for_every_ticker_and_the_benchmark() -> None:
    _, source = run(US1_SPECS)

    start = fetch_window_start(D)
    assert sorted(source.calls) == sorted(
        (ticker, start, D) for ticker in [*US1_SPECS, BENCHMARK_TICKER]
    )


CAP_SPECS = {f"Q{i:02d}": StockSpec(0.0, 1.0, 1.0, 0.005 + 0.001 * i) for i in range(1, 15)}


def test_fourteen_qualifying_keep_ten_and_record_four_cap_exceeded() -> None:
    """Spec US1 scenario 2: all 14 have z >= 2.97; Q14 is largest, Q01 smallest."""
    result, _ = run(CAP_SPECS)

    assert len(result.evaluated) == 14
    assert {r.ticker for r in result.evaluated} == set(CAP_SPECS)
    assert [(a.ticker, a.rank) for a in result.anomalies] == [
        (f"Q{i:02d}", 15 - i) for i in range(14, 4, -1)
    ]

    assert [(s.ticker, s.reason_code) for s in result.skipped] == [
        (f"Q{i:02d}", "cap_exceeded") for i in range(1, 5)
    ]
    expected_rank = {"Q04": 11, "Q03": 12, "Q02": 13, "Q01": 14}
    for skip in result.skipped:
        assert skip.reason_detail is not None
        match = re.fullmatch(r"abs z=(\d+\.\d{4}), rank (\d+) of 14 qualifying", skip.reason_detail)
        assert match is not None, skip.reason_detail
        assert float(match.group(1)) == pytest.approx(abs(CAP_SPECS[skip.ticker].z), abs=1e-4)
        assert int(match.group(2)) == expected_rank[skip.ticker]

    anomaly_tickers = {a.ticker for a in result.anomalies}
    assert anomaly_tickers.isdisjoint(s.ticker for s in result.skipped)


def test_same_input_gives_equal_results() -> None:
    """FR-020."""
    first, _ = run(US1_SPECS)
    second, _ = run(US1_SPECS)

    assert first == second
