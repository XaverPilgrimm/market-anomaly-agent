"""quickstart.md scenarios 1-5 in-process: `cli.main` against a real local PostgreSQL, with a
fixture universe file and the shared fake price source from tests/support/fakes.py."""

from collections.abc import Callable
from pathlib import Path

import psycopg
import pytest

from market_anomaly_agent import cli
from market_anomaly_agent.data.source import PriceDataSource, PriceDataUnavailable
from market_anomaly_agent.rules import BENCHMARK_TICKER
from tests.support.fakes import (
    DAYS,
    GOOGL_SPEC,
    US1_SPECS,
    D,
    FakeSource,
    StockSpec,
    drop_bars,
    keep_paired_returns,
    write_universe_file,
)

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


class Detect:
    """Runs `cli.main` for D against `source`, which a test may replace between runs."""

    def __init__(self, source: FakeSource) -> None:
        self.source = source

    def __call__(self) -> int:
        def factory(api_key: str) -> PriceDataSource:
            return self.source

        return cli.main(ARGV, source_factory=factory)


@pytest.fixture
def make_detect(
    migrated_conn: Conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[[dict[str, StockSpec]], Detect]:
    """A `Detect` for a fixture universe of `specs`' tickers, writing to the test's schema."""
    row = migrated_conn.execute("SELECT current_schema()").fetchone()
    assert row is not None
    schema = str(row[0])
    url = migrated_conn.info.dsn
    password = migrated_conn.info.password

    def connect() -> Conn:
        return psycopg.connect(
            url, password=password, autocommit=True, options=f"-c search_path={schema}"
        )

    monkeypatch.setattr(cli, "UNIVERSE_DIR", tmp_path)
    monkeypatch.setattr(cli, "connect", connect)
    monkeypatch.setenv("PRICE_DATA_API_KEY", "test-key")

    def make(specs: dict[str, StockSpec]) -> Detect:
        size = write_universe_file(tmp_path, sorted(specs))
        monkeypatch.setattr(cli, "EXPECTED_UNIVERSE_SIZE", size)
        return Detect(FakeSource(specs))

    return make


@pytest.fixture
def detect(make_detect: Callable[[dict[str, StockSpec]], Detect]) -> Detect:
    return make_detect(SPECS)


@pytest.fixture
def source(detect: Detect) -> FakeSource:
    return detect.source


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


# --- US3: quickstart.md scenarios 4 and 5 (T041) ------------------------------------------------
#
# Every fixture universe keeps its missing_data skips at or below 20% of its tickers (FR-023).

FILLER = StockSpec(0.0, 1.0, 1.0, 0.0)  # z = 0: evaluated, never flagged
# 100 tickers: 99 fillers plus the GOOGL row every fixture universe has
HUNDRED = {**{f"N{i:02d}": FILLER for i in range(99)}, "GOOGL": GOOGL_SPEC}


def stored_for_d(conn: Conn) -> dict[str, int]:
    """Row count per run table for D."""
    counts: dict[str, int] = {}
    for table in TABLES:
        row = conn.execute(
            f"SELECT count(*) FROM {table} WHERE evaluated_date = %s".encode(), (D,)
        ).fetchone()
        assert row is not None
        counts[table] = int(str(row[0]))
    return counts


NOTHING_FOR_D = dict.fromkeys(TABLES, 0)


def stored_skips(conn: Conn) -> list[tuple[str, str]]:
    rows = conn.execute(
        "SELECT ticker, reason_code FROM skipped_stock_records ORDER BY ticker"
    ).fetchall()
    return [(str(t), str(r)) for t, r in rows]


def stored_evaluated(conn: Conn) -> list[str]:
    rows = conn.execute("SELECT ticker FROM evaluated_stock_records ORDER BY ticker").fetchall()
    return [str(r[0]) for r in rows]


@pytest.mark.integration
def test_short_history_is_stored_as_an_insufficient_history_skip(
    make_detect: Callable[[dict[str, StockSpec]], Detect], migrated_conn: Conn
) -> None:
    """Scenario 4."""
    detect = make_detect({**SPECS, "SHORT": FILLER})
    keep_paired_returns(detect.source, "SHORT", 45)

    assert detect() == 0

    assert stored_skips(migrated_conn) == [("SHORT", "insufficient_history")]
    assert stored_evaluated(migrated_conn) == sorted(SPECS)
    flagged = migrated_conn.execute("SELECT ticker FROM anomaly_cases").fetchall()
    assert [str(r[0]) for r in flagged] == ["FLAG"]


@pytest.mark.integration
def test_unusable_benchmark_exits_4_and_the_day_can_be_run_again(
    make_detect: Callable[[dict[str, StockSpec]], Detect],
    migrated_conn: Conn,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Scenario 5, FR-022."""
    detect = make_detect(SPECS)
    drop_bars(detect.source, BENCHMARK_TICKER, D)

    assert detect() == 4

    captured = capsys.readouterr()
    assert captured.out == ""
    assert BENCHMARK_TICKER in captured.err and D.isoformat() in captured.err
    assert stored_for_d(migrated_conn) == NOTHING_FOR_D

    detect.source = FakeSource(SPECS)
    assert detect() == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == SUMMARY
    assert runs_for_d(migrated_conn) == 1


def _miss_evaluated_day(source: FakeSource, count: int) -> list[str]:
    tickers = sorted(HUNDRED)[:count]
    for ticker in tickers:
        drop_bars(source, ticker, D)
    return tickers


@pytest.mark.integration
def test_21_of_100_missing_exits_4_and_the_day_can_be_run_again(
    make_detect: Callable[[dict[str, StockSpec]], Detect],
    migrated_conn: Conn,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Scenario 5, FR-023: more than 20% missing data stores nothing."""
    detect = make_detect(HUNDRED)
    _miss_evaluated_day(detect.source, 21)

    assert detect() == 4

    assert "21 of 100" in capsys.readouterr().err
    assert stored_for_d(migrated_conn) == NOTHING_FOR_D

    detect.source = FakeSource(HUNDRED)
    assert detect() == 0
    assert stored_evaluated(migrated_conn) == sorted(HUNDRED)
    assert stored_skips(migrated_conn) == []


@pytest.mark.integration
def test_exactly_20_of_100_missing_completes_with_20_skips(
    make_detect: Callable[[dict[str, StockSpec]], Detect], migrated_conn: Conn
) -> None:
    """FR-023: exactly 20% still completes."""
    detect = make_detect(HUNDRED)
    missing = _miss_evaluated_day(detect.source, 20)

    assert detect() == 0

    assert stored_skips(migrated_conn) == [(t, "missing_data") for t in missing]
    assert len(stored_evaluated(migrated_conn)) == 80
    assert runs_for_d(migrated_conn) == 1


@pytest.mark.integration
def test_rerun_with_an_unavailable_ticker_reproduces_the_day(
    make_detect: Callable[[dict[str, StockSpec]], Detect],
    migrated_conn: Conn,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A ticker the vendor has no data for has no stored bars, so an unchanged re-run still
    matches the stored snapshot (T040). 1 of 10 tickers is missing data."""
    specs = {**SPECS, **{f"N{i:02d}": FILLER for i in range(5)}}
    detect = make_detect(specs)
    detect.source.raises["BBB"] = PriceDataUnavailable("BBB: symbol not found")

    assert detect() == 0

    assert stored_skips(migrated_conn) == [("BBB", "missing_data")]
    bbb_bars = migrated_conn.execute(
        "SELECT count(*) FROM daily_price_bars WHERE ticker = 'BBB'"
    ).fetchone()
    assert bbb_bars == (0,)
    before = table_contents(migrated_conn, ALL_TABLES)
    capsys.readouterr()

    assert detect() == 0

    assert "reproduced" in capsys.readouterr().err
    assert table_contents(migrated_conn, ALL_TABLES) == before
