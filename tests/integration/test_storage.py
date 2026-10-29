import psycopg
import pytest

from market_anomaly_agent.storage.migrate import apply_migrations

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
