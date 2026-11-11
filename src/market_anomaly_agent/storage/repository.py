"""Append-only storage of detection runs (data-model.md). This module never updates or deletes."""

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import psycopg

from market_anomaly_agent.data.source import DailyBar
from market_anomaly_agent.detection import DetectionResult
from market_anomaly_agent.rules import DAILY_CAP, Z_THRESHOLD

Conn = psycopg.Connection[tuple[object, ...]]

BAR_SOURCE = "twelvedata"
NOT_EVALUATED_REASONS = frozenset({"insufficient_history", "missing_data"})


@dataclass(frozen=True)
class StoredRun:
    evaluated_date: date
    rule_version: str
    computed_at: datetime
    benchmark_ticker: str
    benchmark_return: Decimal
    evaluated_count: int
    flagged_count: int
    skipped_count: int


def check_invariants(result: DetectionResult) -> None:
    """Raise ValueError when `result` breaks a data-model.md cross-entity invariant."""
    for name, rows in (
        ("evaluated", [r.ticker for r in result.evaluated]),
        ("anomaly", [a.ticker for a in result.anomalies]),
        ("skipped", [s.ticker for s in result.skipped]),
    ):
        repeated = sorted(t for t, n in Counter(rows).items() if n > 1)
        if repeated:
            raise ValueError(f"{name} records repeat tickers: {', '.join(repeated)}")

    if len(result.anomalies) > DAILY_CAP:
        raise ValueError(f"{len(result.anomalies)} anomaly cases exceed the cap of {DAILY_CAP}")

    z_by_ticker = {r.ticker: r.z_score for r in result.evaluated}
    flagged = {a.ticker for a in result.anomalies}
    skip_reason = {s.ticker: s.reason_code for s in result.skipped}

    for ticker in sorted(flagged):
        if ticker not in z_by_ticker:
            raise ValueError(f"anomaly case {ticker} has no evaluated record")
        if ticker in skip_reason:
            raise ValueError(f"{ticker} is both an anomaly case and skipped")
    for ticker, reason in sorted(skip_reason.items()):
        if reason in NOT_EVALUATED_REASONS and ticker in z_by_ticker:
            raise ValueError(f"{reason} skip {ticker} also has an evaluated record")
        if reason == "cap_exceeded" and (
            ticker not in z_by_ticker or abs(z_by_ticker[ticker]) < Decimal(repr(Z_THRESHOLD))
        ):
            raise ValueError(f"cap_exceeded skip {ticker} has no qualifying evaluated record")
    for ticker, z in sorted(z_by_ticker.items()):
        qualifies = abs(z) >= Decimal(repr(Z_THRESHOLD))
        if qualifies and ticker not in flagged and skip_reason.get(ticker) != "cap_exceeded":
            raise ValueError(f"{ticker} qualifies but is neither flagged nor cap_exceeded")


def save_run(conn: Conn, result: DetectionResult) -> None:
    """Store one run and everything it used, in a single transaction.

    `conn` must be in autocommit mode so that `transaction()` is a real transaction. Raises
    ValueError before any write if the result breaks an invariant; a second run for the same
    evaluated_date fails on the primary key and leaves the first untouched.
    """
    check_invariants(result)
    d = result.evaluated_date
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO detection_runs
                (evaluated_date, detection_rule_version, computed_at, benchmark_ticker,
                 benchmark_return)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                d,
                result.rule_version,
                result.computed_at,
                result.benchmark_ticker,
                result.benchmark_return,
            ),
        )
        # The bars were fetched within this run; computed_at stands in for the fetch time.
        cur.executemany(
            """
            INSERT INTO daily_price_bars
                (evaluated_date, ticker, trading_date, open, close, volume, source, fetched_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    d,
                    ticker,
                    b.trading_date,
                    b.open,
                    b.close,
                    b.volume,
                    BAR_SOURCE,
                    result.computed_at,
                )
                for ticker, bars in sorted(result.bars.items())
                for b in bars
            ],
        )
        cur.executemany(
            """
            INSERT INTO evaluated_stock_records
                (evaluated_date, ticker, "return", expected_return, abnormal_return, z_score,
                 alpha, beta, estimation_window_days)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    d,
                    r.ticker,
                    r.return_,
                    r.expected_return,
                    r.abnormal_return,
                    r.z_score,
                    r.alpha,
                    r.beta,
                    r.estimation_window_days,
                )
                for r in result.evaluated
            ],
        )
        cur.executemany(
            """
            INSERT INTO anomaly_cases
                (evaluated_date, ticker, rank, volume_ratio, volume_ratio_unavailable_reason)
            VALUES (%s, %s, %s, %s, %s)
            """,
            [
                (d, a.ticker, a.rank, a.volume_ratio, a.volume_ratio_unavailable_reason)
                for a in result.anomalies
            ],
        )
        cur.executemany(
            """
            INSERT INTO skipped_stock_records (evaluated_date, ticker, reason_code, reason_detail)
            VALUES (%s, %s, %s, %s)
            """,
            [(d, s.ticker, s.reason_code, s.reason_detail) for s in result.skipped],
        )


def load_run(conn: Conn, evaluated_date: date) -> StoredRun | None:
    row = conn.execute(
        """
        SELECT r.evaluated_date, r.detection_rule_version, r.computed_at, r.benchmark_ticker,
               r.benchmark_return,
               (SELECT count(*) FROM evaluated_stock_records e
                 WHERE e.evaluated_date = r.evaluated_date),
               (SELECT count(*) FROM anomaly_cases a WHERE a.evaluated_date = r.evaluated_date),
               (SELECT count(*) FROM skipped_stock_records s
                 WHERE s.evaluated_date = r.evaluated_date)
        FROM detection_runs r
        WHERE r.evaluated_date = %s
        """,
        (evaluated_date,),
    ).fetchone()
    if row is None:
        return None
    day, version, computed_at, ticker, benchmark_return, evaluated, flagged, skipped = row
    assert isinstance(day, date) and isinstance(computed_at, datetime)
    assert isinstance(benchmark_return, Decimal)
    return StoredRun(
        evaluated_date=day,
        rule_version=str(version),
        computed_at=computed_at,
        benchmark_ticker=str(ticker),
        benchmark_return=benchmark_return,
        evaluated_count=int(str(evaluated)),
        flagged_count=int(str(flagged)),
        skipped_count=int(str(skipped)),
    )


def load_bar_snapshot(conn: Conn, evaluated_date: date) -> dict[str, list[DailyBar]]:
    """The bars stored by the run for `evaluated_date`, per ticker in trading-date order."""
    rows = conn.execute(
        """
        SELECT ticker, trading_date, open, close, volume
        FROM daily_price_bars
        WHERE evaluated_date = %s
        ORDER BY ticker, trading_date
        """,
        (evaluated_date,),
    ).fetchall()
    snapshot: dict[str, list[DailyBar]] = {}
    for ticker, trading_date, open_, close, volume in rows:
        assert isinstance(trading_date, date)
        assert isinstance(open_, Decimal) and isinstance(close, Decimal)
        snapshot.setdefault(str(ticker), []).append(
            DailyBar(trading_date=trading_date, open=open_, close=close, volume=int(str(volume)))
        )
    return snapshot
