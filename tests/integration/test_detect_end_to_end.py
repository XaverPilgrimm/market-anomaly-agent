"""quickstart.md scenarios 1-3 in-process: `cli.main` against a real local PostgreSQL, with a
fixture universe file and the shared fake price source from tests/support/fakes.py."""

from collections.abc import Callable
from pathlib import Path

import psycopg
import pytest

from market_anomaly_agent import cli
from market_anomaly_agent.data.source import PriceDataSource
from tests.support.fakes import DAYS, GOOGL_SPEC, US1_SPECS, D, FakeSource, write_universe_file

Conn = psycopg.Connection[tuple[object, ...]]

TABLES = (
    "detection_runs",
    "daily_price_bars",
    "evaluated_stock_records",
    "anomaly_cases",
    "skipped_stock_records",
)
ALL_TABLES = (*TABLES, "universe_constituents")
ARGV = ["--date", D.isoformat()]
SPECS = {**US1_SPECS, "GOOGL": GOOGL_SPEC}  # the fixture universe always includes GOOGL
SUMMARY = (
    f"evaluated_date={D.isoformat()} status=completed flagged=1 evaluated=5 skipped=0 "
    "rule_version=1"
)


def table_contents(
    conn: Conn, tables: tuple[str, ...] = TABLES
) -> dict[str, list[tuple[object, ...]]]:
    return {
        table: conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2".encode()).fetchall()
        for table in tables
    }


@pytest.fixture
def source() -> FakeSource:
    return FakeSource(SPECS)


@pytest.fixture
def detect(
    migrated_conn: Conn,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: FakeSource,
) -> Callable[[], int]:
    """`cli.main` for D, writing to the test's schema and reading the fixture universe."""
    row = migrated_conn.execute("SELECT current_schema()").fetchone()
    assert row is not None
    schema = str(row[0])
    url = migrated_conn.info.dsn
    password = migrated_conn.info.password

    def connect() -> Conn:
        return psycopg.connect(
            url, password=password, autocommit=True, options=f"-c search_path={schema}"
        )

    size = write_universe_file(tmp_path, sorted(SPECS))
    monkeypatch.setattr(cli, "UNIVERSE_DIR", tmp_path)
    monkeypatch.setattr(cli, "EXPECTED_UNIVERSE_SIZE", size)
    monkeypatch.setattr(cli, "connect", connect)
    monkeypatch.setenv("PRICE_DATA_API_KEY", "test-key")

    def factory(api_key: str) -> PriceDataSource:
        return source

    return lambda: cli.main(ARGV, source_factory=factory)


def runs_for_d(conn: Conn) -> int:
    row = conn.execute(
        "SELECT count(*) FROM detection_runs WHERE evaluated_date = %s", (D,)
    ).fetchone()
    assert row is not None
    return int(str(row[0]))


@pytest.mark.integration
def test_first_run_stores_the_day(
    detect: Callable[[], int], migrated_conn: Conn, capsys: pytest.CaptureFixture[str]
) -> None:
    """Scenario 1."""
    assert detect() == 0

    assert capsys.readouterr().out.strip().splitlines()[-1] == SUMMARY
    contents = table_contents(migrated_conn)
    assert runs_for_d(migrated_conn) == 1
    assert len(contents["daily_price_bars"]) == 6 * 122  # 5 stocks + QQQ, full window each
    assert [str(r[1]) for r in contents["evaluated_stock_records"]] == sorted(SPECS)
    assert [(str(r[1]), r[2]) for r in contents["anomaly_cases"]] == [("FLAG", 1)]
    assert contents["skipped_stock_records"] == []
    frozen = migrated_conn.execute("SELECT ticker FROM universe_constituents ORDER BY 1")
    assert [str(r[0]) for r in frozen.fetchall()] == sorted(SPECS)


@pytest.mark.integration
def test_rerun_with_unchanged_data_writes_nothing(
    detect: Callable[[], int], migrated_conn: Conn, capsys: pytest.CaptureFixture[str]
) -> None:
    """Scenario 2."""
    assert detect() == 0
    before = table_contents(migrated_conn)
    capsys.readouterr()

    assert detect() == 0

    assert capsys.readouterr().out.strip().splitlines()[-1] == SUMMARY
    assert runs_for_d(migrated_conn) == 1
    assert table_contents(migrated_conn) == before


@pytest.mark.integration
def test_rerun_after_a_stored_bar_changed_is_rejected(
    detect: Callable[[], int], migrated_conn: Conn, capsys: pytest.CaptureFixture[str]
) -> None:
    """Scenario 3: a vendor correction after the original run, simulated by editing the
    stored snapshot."""
    assert detect() == 0
    migrated_conn.execute(
        """
        UPDATE daily_price_bars SET close = close + 0.01
        WHERE evaluated_date = %s AND ticker = 'FLAG' AND trading_date = %s
        """,
        (D, DAYS[-2]),
    )
    before = table_contents(migrated_conn)
    capsys.readouterr()

    assert detect() == 3

    captured = capsys.readouterr()
    assert captured.out == ""
    assert D.isoformat() in captured.err
    assert table_contents(migrated_conn) == before


UNIVERSE_FILE = "nasdaq100_2026-10-01.csv"


@pytest.mark.integration
def test_invalid_universe_file_exits_1_before_fetching(
    detect: Callable[[], int],
    migrated_conn: Conn,
    source: FakeSource,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """FR-002 (T034): a universe file that fails validation stops the run before any fetch."""
    universe = tmp_path / UNIVERSE_FILE  # AAA's row becomes GOOG; the row count still matches
    universe.write_text(
        universe.read_text(encoding="utf-8").replace("AAA,AAA Inc.", "GOOG,GOOG Inc."),
        encoding="utf-8",
    )
    before = table_contents(migrated_conn, ALL_TABLES)

    assert detect() == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "UniverseFileError" in captured.err and "GOOG must be excluded" in captured.err
    assert source.calls == []
    assert table_contents(migrated_conn, ALL_TABLES) == before
    assert all(rows == [] for rows in before.values())


@pytest.mark.integration
def test_universe_file_changed_after_first_run_exits_1_before_fetching(
    detect: Callable[[], int],
    migrated_conn: Conn,
    source: FakeSource,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """FR-003 (T035): once frozen, an edited universe file stops the run before any fetch."""
    assert detect() == 0
    universe = tmp_path / UNIVERSE_FILE
    universe.write_text(
        universe.read_text(encoding="utf-8").replace("FLAG Inc.", "FLAG Corp."), encoding="utf-8"
    )
    before = table_contents(migrated_conn, ALL_TABLES)
    source.calls.clear()
    capsys.readouterr()

    assert detect() == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "UniverseFrozenError" in captured.err and "edited FLAG" in captured.err
    assert source.calls == []
    assert table_contents(migrated_conn, ALL_TABLES) == before
