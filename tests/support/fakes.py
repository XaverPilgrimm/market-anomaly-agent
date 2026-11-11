"""A fake price source with a hand-built history, shared by the detection unit tests and the
end-to-end integration tests (no database, no network).

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
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from market_anomaly_agent.calendar import fetch_window_start, trading_days_between
from market_anomaly_agent.data.source import DailyBar
from market_anomaly_agent.rules import BENCHMARK_TICKER

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


# A non-qualifying Alphabet stock (z = 0), so end-to-end universes can include GOOGL (FR-002).
GOOGL_SPEC = StockSpec(0.0003, 1.1, 1.0, 0.0)
GOOGL_NOTE = "GOOGL kept, GOOG excluded"


def write_universe_file(directory: Path, tickers: Sequence[str]) -> int:
    """Write a fixture universe that passes `validate_universe`: one row per ticker plus GOOGL
    with its share_class_note, never GOOG. Returns the row count."""
    if "GOOG" in tickers:
        raise ValueError("fixture universes must exclude GOOG (FR-002)")
    rows = sorted({*tickers, "GOOGL"})
    lines = ["ticker,company_name,snapshot_date,source,share_class_note"]
    lines += [
        f"{t},{t} Inc.,2026-10-01,https://example.test/holdings.csv,"
        + (f'"{GOOGL_NOTE}"' if t == "GOOGL" else "")
        for t in rows
    ]
    (directory / "nasdaq100_2026-10-01.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(rows)
