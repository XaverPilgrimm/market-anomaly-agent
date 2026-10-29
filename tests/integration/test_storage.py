import dataclasses
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest

from market_anomaly_agent.data.source import DailyBar
from market_anomaly_agent.detection import (
    AnomalyCase,
    DetectionResult,
    EvaluatedRecord,
    SkippedRecord,
)
from market_anomaly_agent.storage.migrate import apply_migrations
from market_anomaly_agent.storage.repository import (
    load_bar_snapshot,
    load_run,
    save_run,
)

Conn = psycopg.Connection[tuple[object, ...]]

D = date(2026, 10, 27)
PREV = date(2026, 10, 26)
RESULT_TABLES = (
    "detection_runs",
    "daily_price_bars",
    "evaluated_stock_records",
    "anomaly_cases",
    "skipped_stock_records",
)

EXPECTED_PRIMARY_KEYS = {
    "universe_constituents": ["ticker"],
    "detection_runs": ["evaluated_date"],
    "daily_price_bars": ["evaluated_date", "ticker", "trading_date"],
    "evaluated_stock_records": ["evaluated_date", "ticker"],
    "anomaly_cases": ["evaluated_date", "ticker"],
    "skipped_stock_records": ["evaluated_date", "ticker"],
}


def primary_keys(conn: psycopg.Connection[tuple[object, ...]]) -> dict[str, list[str]]:
    rows = conn.execute(
        """
        SELECT tc.table_name, kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON kcu.constraint_schema = tc.constraint_schema
         AND kcu.constraint_name = tc.constraint_name
        WHERE tc.constraint_type = 'PRIMARY KEY'
          AND tc.table_schema = current_schema()
        ORDER BY tc.table_name, kcu.ordinal_position
        """
    ).fetchall()
    keys: dict[str, list[str]] = {}
    for table, column in rows:
        keys.setdefault(str(table), []).append(str(column))
    return keys


@pytest.mark.integration
def test_migrations_apply_once(db_conn: psycopg.Connection[tuple[object, ...]]) -> None:
    assert apply_migrations(db_conn) == ["0001_init.sql"]
    assert apply_migrations(db_conn) == []

    applied = db_conn.execute("SELECT filename FROM schema_migrations").fetchall()
    assert applied == [("0001_init.sql",)]

    keys = primary_keys(db_conn)
    assert {table: keys.get(table) for table in EXPECTED_PRIMARY_KEYS} == EXPECTED_PRIMARY_KEYS


@pytest.mark.integration
def test_detection_runs_has_no_skip_status(
    db_conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    apply_migrations(db_conn)

    columns = db_conn.execute(
        """
        SELECT column_name, is_nullable
        FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'detection_runs'
        ORDER BY ordinal_position
        """
    ).fetchall()
    assert [name for name, _ in columns] == [
        "evaluated_date",
        "detection_rule_version",
        "computed_at",
        "benchmark_ticker",
        "benchmark_return",
    ]
    nullable = {str(name): str(is_nullable) for name, is_nullable in columns}
    assert nullable["benchmark_return"] == "NO"

    with pytest.raises(psycopg.errors.NotNullViolation):
        db_conn.execute(
            """
            INSERT INTO detection_runs
                (evaluated_date, detection_rule_version, computed_at, benchmark_ticker,
                 benchmark_return)
            VALUES ('2026-10-27', '1', now(), 'QQQ', NULL)
            """
        )


# --- save_run / load_run / load_bar_snapshot (T021) -------------------------------------------


def bar(day: date, close: str, volume: int) -> DailyBar:
    return DailyBar(trading_date=day, open=Decimal(close), close=Decimal(close), volume=volume)


def evaluated(ticker: str, z: str, window: int = 120) -> EvaluatedRecord:
    return EvaluatedRecord(
        ticker=ticker,
        return_=Decimal("0.0213"),
        expected_return=Decimal("0.0061"),
        abnormal_return=Decimal("0.0152"),
        z_score=Decimal(z),
        alpha=Decimal("0.0004"),
        beta=Decimal("1.13"),
        estimation_window_days=window,
    )


def anomaly(ticker: str, rank: int) -> AnomalyCase:
    return AnomalyCase(
        ticker=ticker,
        rank=rank,
        volume_ratio=Decimal("2.75"),
        volume_ratio_unavailable_reason=None,
    )


def make_result(
    evaluated_records: tuple[EvaluatedRecord, ...],
    anomalies: tuple[AnomalyCase, ...] = (),
    skipped: tuple[SkippedRecord, ...] = (),
) -> DetectionResult:
    bars = {
        "QQQ": (bar(PREV, "480.10", 30_000_000), bar(D, "482.50", 31_000_000)),
        **{
            r.ticker: (bar(PREV, "101.25", 1_200_000), bar(D, "103.40", 2_900_000))
            for r in evaluated_records
        },
    }
    return DetectionResult(
        evaluated_date=D,
        rule_version="1",
        computed_at=datetime(2026, 10, 28, 11, 0, tzinfo=UTC),
        benchmark_ticker="QQQ",
        benchmark_return=Decimal("0.004998958550302020"),
        bars=bars,
        evaluated=evaluated_records,
        anomalies=anomalies,
        skipped=skipped,
    )


def basic_result() -> DetectionResult:
    return make_result(
        (evaluated("AAA", "4.21"), evaluated("BBB", "0.37")),
        anomalies=(anomaly("AAA", 1),),
        skipped=(SkippedRecord("CCC", "missing_data", "no bar on 2026-10-27"),),
    )


def row_counts(conn: Conn) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in RESULT_TABLES:
        row = conn.execute(f"SELECT count(*) FROM {table}".encode()).fetchone()
        assert row is not None
        counts[table] = int(str(row[0]))
    return counts


NO_ROWS = dict.fromkeys(RESULT_TABLES, 0)


@pytest.mark.integration
def test_save_run_writes_every_row_and_loads_them_back(migrated_conn: Conn) -> None:
    result = basic_result()

    save_run(migrated_conn, result)

    assert row_counts(migrated_conn) == {
        "detection_runs": 1,
        "daily_price_bars": 6,  # QQQ, AAA, BBB x 2 days
        "evaluated_stock_records": 2,
        "anomaly_cases": 1,
        "skipped_stock_records": 1,
    }
    stored = load_run(migrated_conn, D)
    assert stored is not None
    assert stored.evaluated_date == D
    assert stored.rule_version == "1"
    assert stored.computed_at == result.computed_at
    assert stored.benchmark_ticker == "QQQ"
    assert stored.benchmark_return == result.benchmark_return
    assert (stored.evaluated_count, stored.flagged_count, stored.skipped_count) == (2, 1, 1)

    snapshot = load_bar_snapshot(migrated_conn, D)
    assert snapshot == {ticker: list(bars) for ticker, bars in result.bars.items()}
    assert all(isinstance(b.close, Decimal) for bars in snapshot.values() for b in bars)


@pytest.mark.integration
def test_bar_source_is_recorded(migrated_conn: Conn) -> None:
    save_run(migrated_conn, basic_result())

    sources = migrated_conn.execute("SELECT DISTINCT source FROM daily_price_bars").fetchall()
    assert sources == [("twelvedata",)]


@pytest.mark.integration
def test_failure_part_way_leaves_no_rows(migrated_conn: Conn) -> None:
    """estimation_window_days = 200 passes the application checks but violates the table's
    CHECK (60-120), so the insert fails after detection_runs and the bars were written."""
    result = make_result((evaluated("AAA", "0.5", window=200),))

    with pytest.raises(psycopg.errors.CheckViolation):
        save_run(migrated_conn, result)

    assert row_counts(migrated_conn) == NO_ROWS


@pytest.mark.integration
def test_load_run_returns_none_for_unknown_day(migrated_conn: Conn) -> None:
    assert load_run(migrated_conn, D) is None
    assert load_bar_snapshot(migrated_conn, D) == {}


@pytest.mark.integration
def test_second_save_for_same_day_raises_and_keeps_first(migrated_conn: Conn) -> None:
    first = basic_result()
    save_run(migrated_conn, first)
    second = dataclasses.replace(first, benchmark_return=Decimal("0.0099"))

    with pytest.raises(psycopg.errors.UniqueViolation):
        save_run(migrated_conn, second)

    stored = load_run(migrated_conn, D)
    assert stored is not None
    assert stored.benchmark_return == first.benchmark_return
    assert row_counts(migrated_conn)["daily_price_bars"] == 6


@pytest.mark.integration
def test_cap_exceeded_stocks_keep_their_evaluated_rows(migrated_conn: Conn) -> None:
    tickers = [f"Q{i:02d}" for i in range(14, 0, -1)]  # Q14 has the largest z
    records = tuple(evaluated(t, f"{2.5 + 0.1 * (15 - n):.2f}") for n, t in enumerate(tickers, 1))
    kept = tuple(anomaly(t, rank) for rank, t in enumerate(tickers[:10], 1))
    over_cap = tuple(
        SkippedRecord(t, "cap_exceeded", f"abs z=2.5000, rank {rank} of 14 qualifying")
        for rank, t in enumerate(tickers[10:], 11)
    )

    save_run(migrated_conn, make_result(records, kept, over_cap))

    counts = row_counts(migrated_conn)
    assert counts["evaluated_stock_records"] == 14
    assert counts["anomaly_cases"] == 10
    assert counts["skipped_stock_records"] == 4
    orphans = migrated_conn.execute(
        """
        SELECT s.ticker FROM skipped_stock_records s
        LEFT JOIN evaluated_stock_records e USING (evaluated_date, ticker)
        WHERE s.reason_code = 'cap_exceeded' AND e.ticker IS NULL
        """
    ).fetchall()
    assert orphans == []


def invariant_violations() -> list[tuple[str, DetectionResult]]:
    """Each case breaks exactly one data-model.md cross-entity invariant; every other rule
    (including "|z| >= 2 means anomaly case or cap_exceeded skip") holds."""
    records = (evaluated("AAA", "1.21"), evaluated("BBB", "0.80"))
    flagged = (evaluated("AAA", "4.21"), evaluated("BBB", "0.80"))
    eleven = tuple(evaluated(f"T{i:02d}", "5.00") for i in range(11))
    return [
        (
            "cap_exceeded skip without an evaluated row",
            make_result(records, skipped=(SkippedRecord("ZZZ", "cap_exceeded", "rank 11"),)),
        ),
        (
            "missing_data skip with an evaluated row",
            make_result(records, skipped=(SkippedRecord("BBB", "missing_data", "gap"),)),
        ),
        (
            "insufficient_history skip with an evaluated row",
            make_result(records, skipped=(SkippedRecord("BBB", "insufficient_history", "45"),)),
        ),
        (
            "ticker both an anomaly case and skipped",
            make_result(
                flagged,
                anomalies=(anomaly("AAA", 1),),
                skipped=(SkippedRecord("AAA", "cap_exceeded", "rank 11"),),
            ),
        ),
        (
            "more than 10 anomaly cases",
            make_result(
                eleven, anomalies=tuple(anomaly(r.ticker, n) for n, r in enumerate(eleven, 1))
            ),
        ),
    ]


@pytest.mark.integration
@pytest.mark.parametrize(
    "result", [r for _, r in invariant_violations()], ids=[n for n, _ in invariant_violations()]
)
def test_invariant_violations_raise_before_any_write(
    migrated_conn: Conn, result: DetectionResult
) -> None:
    with pytest.raises(ValueError):
        save_run(migrated_conn, result)

    assert row_counts(migrated_conn) == NO_ROWS
