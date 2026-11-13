"""Daily anomaly detection for one evaluated trading day (FR-005 to FR-011, FR-014).

Pure orchestration over a PriceDataSource: no database access. Calculations run in float64 and
every stored number is converted with Decimal(repr(value)) ("Shared decisions" in tasks.md).
"""

import logging
from collections.abc import Callable, Mapping, Sequence
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
from market_anomaly_agent.data.source import DailyBar, PriceDataSource, PriceDataUnavailable
from market_anomaly_agent.rules import (
    BENCHMARK_TICKER,
    DAILY_CAP,
    MAX_MISSING_DATA_PERCENT,
    MAX_WINDOW_DAYS,
    MIN_WINDOW_DAYS,
    RULE_VERSION,
    VOLUME_WINDOW_DAYS,
    Z_THRESHOLD,
)

log = logging.getLogger(__name__)


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
    fetched_at: datetime  # when the fetch started; every bar of the run carries it
    computed_at: datetime  # when the computation was performed, after every fetch (FR-014)
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


def _missing_day(bars: Sequence[DailyBar], days: Sequence[date]) -> date | None:
    """The first of `days` without a bar in `bars`, or None when all are present."""
    present = {b.trading_date for b in bars}
    return next((d for d in days if d not in present), None)


def _skip(ticker: str, reason_code: str, reason_detail: str) -> SkippedRecord:
    log.info("skipped %s: %s (%s)", ticker, reason_code, reason_detail)
    return SkippedRecord(ticker=ticker, reason_code=reason_code, reason_detail=reason_detail)


def run_detection(
    evaluated_date: date,
    tickers: Sequence[str],
    source: PriceDataSource,
    clock: Callable[[], datetime],
) -> DetectionResult:
    """Evaluate every ticker in `tickers` for `evaluated_date` against BENCHMARK_TICKER.

    `clock` is read exactly twice: when the fetch starts (`fetched_at`) and, after every bar has
    been fetched, right before the computation (`computed_at`, FR-014).

    A ticker without usable data is skipped with `missing_data` or `insufficient_history` (FR-012,
    FR-017). Raises BenchmarkUnavailableError (FR-022) or TooMuchMissingDataError (FR-023) before
    returning anything; PriceDataError from the source is never caught.
    """
    start = fetch_window_start(evaluated_date)
    days = trading_days_between(start, evaluated_date)
    estimation_days = days[:-1]
    return_days = days[-2:]  # a return on D needs the bars of D and the previous trading day
    fetch_count = len(tickers) + 1  # the benchmark is fetched first
    log.info(
        "evaluating %s: %d tickers plus benchmark %s",
        evaluated_date.isoformat(),
        len(tickers),
        BENCHMARK_TICKER,
    )

    fetched_at = clock()
    try:
        benchmark_bars = tuple(source.get_daily_bars(BENCHMARK_TICKER, start, evaluated_date))
    except PriceDataUnavailable as exc:
        log.info("fetched %s (1 of %d): no data", BENCHMARK_TICKER, fetch_count)
        raise BenchmarkUnavailableError(
            f"benchmark {BENCHMARK_TICKER} is unavailable from the price source: {exc}"
        ) from exc
    log.info("fetched %s (1 of %d)", BENCHMARK_TICKER, fetch_count)
    gap = _missing_day(benchmark_bars, return_days)
    if gap is not None:
        raise BenchmarkUnavailableError(
            f"benchmark {BENCHMARK_TICKER} has no bar on {gap.isoformat()}, so it has no return "
            f"on {evaluated_date.isoformat()}"
        )
    benchmark_returns = simple_returns({b.trading_date: b.close for b in benchmark_bars}, days)
    benchmark_on_d = benchmark_returns[evaluated_date]

    bars: dict[str, tuple[DailyBar, ...]] = {BENCHMARK_TICKER: benchmark_bars}
    fetched: dict[str, tuple[DailyBar, ...]] = {}  # in sorted ticker order
    skipped: list[SkippedRecord] = []
    for position, ticker in enumerate(sorted(tickers), 2):
        try:
            ticker_bars = tuple(source.get_daily_bars(ticker, start, evaluated_date))
        except PriceDataUnavailable as exc:
            log.info("fetched %s (%d of %d): no data", ticker, position, fetch_count)
            skipped.append(_skip(ticker, "missing_data", f"no data from the price source: {exc}"))
            continue
        log.info("fetched %s (%d of %d)", ticker, position, fetch_count)
        if ticker_bars:  # no key for no bars, matching a snapshot read back from storage
            bars[ticker] = ticker_bars
        fetched[ticker] = ticker_bars

    # Every bar is fetched; the computation starts now (FR-014).
    computed_at = clock()
    records: dict[str, EvaluatedRecord] = {}
    scores: dict[str, float] = {}
    for ticker, ticker_bars in fetched.items():
        gap = _missing_day(ticker_bars, return_days)
        if gap is not None:
            skipped.append(_skip(ticker, "missing_data", f"no bar on {gap.isoformat()}"))
            continue

        returns = simple_returns({b.trading_date: b.close for b in ticker_bars}, days)
        paired = [d for d in estimation_days if d in returns and d in benchmark_returns]
        if len(paired) < MIN_WINDOW_DAYS:
            skipped.append(
                _skip(
                    ticker,
                    "insufficient_history",
                    f"{len(paired)} paired returns before {evaluated_date.isoformat()}, "
                    f"{MIN_WINDOW_DAYS} required",
                )
            )
            continue
        paired = paired[-MAX_WINDOW_DAYS:]
        model = fit_market_model(
            [returns[d] for d in paired], [benchmark_returns[d] for d in paired]
        )
        actual = returns[evaluated_date]  # present: both return_days bars were checked above
        expected = expected_return(model, benchmark_on_d)
        abnormal = abnormal_return(actual, expected)
        try:
            z = z_score(abnormal, model.residual_std)
        except ValueError:
            detail = (
                "zero residual variance"
                if model.residual_std == 0.0
                else f"unusable residual standard deviation {model.residual_std!r}"
            )
            skipped.append(_skip(ticker, "missing_data", detail))
            continue

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

    missing = sum(1 for s in skipped if s.reason_code == "missing_data")
    if missing * 100 > len(tickers) * MAX_MISSING_DATA_PERCENT:
        raise TooMuchMissingDataError(
            f"{missing} of {len(tickers)} tickers are missing data for "
            f"{evaluated_date.isoformat()}, more than {MAX_MISSING_DATA_PERCENT}% (FR-023)"
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

    skipped += [
        _skip(
            ticker,
            "cap_exceeded",
            f"abs z={abs(scores[ticker]):.4f}, rank {rank} of {qualifying_count} qualifying",
        )
        for ticker, rank in cap.cap_exceeded
    ]

    return DetectionResult(
        evaluated_date=evaluated_date,
        rule_version=RULE_VERSION,
        fetched_at=fetched_at,
        computed_at=computed_at,
        benchmark_ticker=BENCHMARK_TICKER,
        benchmark_return=_to_decimal(benchmark_on_d),
        bars=bars,
        evaluated=tuple(records[t] for t in sorted(records)),
        anomalies=tuple(anomalies),
        skipped=tuple(sorted(skipped, key=lambda s: s.ticker)),
    )
