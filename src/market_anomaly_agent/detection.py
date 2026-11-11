"""Daily anomaly detection for one evaluated trading day (FR-005 to FR-011, FR-014).

Pure orchestration over a PriceDataSource: no database access. Calculations run in float64 and
every stored number is converted with Decimal(repr(value)) ("Shared decisions" in tasks.md).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from market_anomaly_agent.calc.market_model import (
    abnormal_return,
    expected_return,
    fit_market_model,
    z_score,
)
from market_anomaly_agent.calc.ranking import apply_daily_cap
from market_anomaly_agent.calc.returns import simple_returns
from market_anomaly_agent.calc.volume import volume_ratio
from market_anomaly_agent.calendar import fetch_window_start, trading_days_between
from market_anomaly_agent.data.source import DailyBar, PriceDataSource
from market_anomaly_agent.rules import (
    BENCHMARK_TICKER,
    DAILY_CAP,
    MAX_WINDOW_DAYS,
    RULE_VERSION,
    VOLUME_WINDOW_DAYS,
    Z_THRESHOLD,
)


class RunAbortedError(Exception):
    """The run cannot produce a result for the day; nothing is stored (contracts/cli-contract.md
    exit 4)."""


class BenchmarkUnavailableError(RunAbortedError):
    """The benchmark has no usable data for the evaluated day (FR-022)."""


class TooMuchMissingDataError(RunAbortedError):
    """More than MAX_MISSING_DATA_PERCENT of the universe is missing data (FR-023)."""


@dataclass(frozen=True)
class EvaluatedRecord:
    ticker: str
    return_: Decimal
    expected_return: Decimal
    abnormal_return: Decimal
    z_score: Decimal
    alpha: Decimal
    beta: Decimal
    estimation_window_days: int


@dataclass(frozen=True)
class AnomalyCase:
    ticker: str
    rank: int
    volume_ratio: Decimal | None
    volume_ratio_unavailable_reason: str | None


@dataclass(frozen=True)
class SkippedRecord:
    ticker: str
    reason_code: str  # insufficient_history, missing_data or cap_exceeded
    reason_detail: str | None


@dataclass(frozen=True)
class DetectionResult:
    evaluated_date: date
    rule_version: str
    computed_at: datetime
    benchmark_ticker: str
    benchmark_return: Decimal
    bars: Mapping[str, Sequence[DailyBar]]  # every bar fetched, per ticker incl. the benchmark
    evaluated: tuple[EvaluatedRecord, ...]  # sorted by ticker
    anomalies: tuple[AnomalyCase, ...]  # sorted by rank
    skipped: tuple[SkippedRecord, ...]  # sorted by ticker


def snapshots_match(
    stored: Mapping[str, Sequence[DailyBar]], fetched: Mapping[str, Sequence[DailyBar]]
) -> bool:
    """True when both hold the same tickers and trading dates with equal open, close and volume
    values (FR-018). Decimals compare by value, so 102.5 equals 102.50."""

    def keyed(snapshot: Mapping[str, Sequence[DailyBar]]) -> dict[tuple[str, date], DailyBar]:
        return {(t, b.trading_date): b for t, bars in snapshot.items() for b in bars}

    def bar_count(snapshot: Mapping[str, Sequence[DailyBar]]) -> int:
        return sum(len(bars) for bars in snapshot.values())

    if set(stored) != set(fetched) or bar_count(stored) != bar_count(fetched):
        return False
    return keyed(stored) == keyed(fetched)


def _to_decimal(value: float) -> Decimal:
    return Decimal(repr(value))


def run_detection(
    evaluated_date: date, tickers: Sequence[str], source: PriceDataSource, now: datetime
) -> DetectionResult:
    """Evaluate every ticker in `tickers` for `evaluated_date` against BENCHMARK_TICKER.

    Assumes complete data; skip handling and the run-level checks are added with US3 (T040).
    """
    start = fetch_window_start(evaluated_date)
    days = trading_days_between(start, evaluated_date)
    estimation_days = days[:-1]

    benchmark_bars = tuple(source.get_daily_bars(BENCHMARK_TICKER, start, evaluated_date))
    benchmark_returns = simple_returns({b.trading_date: b.close for b in benchmark_bars}, days)
    benchmark_on_d = benchmark_returns[evaluated_date]

    bars: dict[str, tuple[DailyBar, ...]] = {BENCHMARK_TICKER: benchmark_bars}
    records: dict[str, EvaluatedRecord] = {}
    scores: dict[str, float] = {}
    for ticker in sorted(tickers):
        ticker_bars = tuple(source.get_daily_bars(ticker, start, evaluated_date))
        bars[ticker] = ticker_bars
        returns = simple_returns({b.trading_date: b.close for b in ticker_bars}, days)

        paired = [d for d in estimation_days if d in returns and d in benchmark_returns]
        paired = paired[-MAX_WINDOW_DAYS:]
        model = fit_market_model(
            [returns[d] for d in paired], [benchmark_returns[d] for d in paired]
        )
        actual = returns[evaluated_date]
        expected = expected_return(model, benchmark_on_d)
        abnormal = abnormal_return(actual, expected)
        z = z_score(abnormal, model.residual_std)

        scores[ticker] = z
        records[ticker] = EvaluatedRecord(
            ticker=ticker,
            return_=_to_decimal(actual),
            expected_return=_to_decimal(expected),
            abnormal_return=_to_decimal(abnormal),
            z_score=_to_decimal(z),
            alpha=_to_decimal(model.alpha),
            beta=_to_decimal(model.beta),
            estimation_window_days=model.n,
        )

    cap = apply_daily_cap(scores, Z_THRESHOLD, DAILY_CAP)
    qualifying_count = len(cap.kept) + len(cap.cap_exceeded)

    volume_days = days[-1 - VOLUME_WINDOW_DAYS : -1]
    anomalies: list[AnomalyCase] = []
    for ticker, rank in cap.kept:
        volumes = {b.trading_date: b.volume for b in bars[ticker]}
        ratio = volume_ratio(volumes[evaluated_date], [volumes.get(d) for d in volume_days])
        anomalies.append(
            AnomalyCase(
                ticker=ticker,
                rank=rank,
                volume_ratio=None if ratio.ratio is None else _to_decimal(ratio.ratio),
                volume_ratio_unavailable_reason=ratio.unavailable_reason,
            )
        )

    skipped = [
        SkippedRecord(
            ticker=ticker,
            reason_code="cap_exceeded",
            reason_detail=(
                f"abs z={abs(scores[ticker]):.4f}, rank {rank} of {qualifying_count} qualifying"
            ),
        )
        for ticker, rank in cap.cap_exceeded
    ]

    return DetectionResult(
        evaluated_date=evaluated_date,
        rule_version=RULE_VERSION,
        computed_at=now,
        benchmark_ticker=BENCHMARK_TICKER,
        benchmark_return=_to_decimal(benchmark_on_d),
        bars=bars,
        evaluated=tuple(records[t] for t in sorted(records)),
        anomalies=tuple(anomalies),
        skipped=tuple(sorted(skipped, key=lambda s: s.ticker)),
    )
