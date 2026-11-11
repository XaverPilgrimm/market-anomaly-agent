"""quickstart.md scenarios 1-3 in-process: `cli.main` against a real local PostgreSQL, with a
fixture universe file and the shared fake price source from tests/support/fakes.py."""

from collections.abc import Callable
from pathlib import Path

import psycopg
import pytest

from market_anomaly_agent import cli
from market_anomaly_agent.data.source import PriceDataSource
from tests.support.fakes import DAYS, US1_SPECS, D, FakeSource

Conn = psycopg.Connection[tuple[object, ...]]

TABLES = (
    "detection_runs",
    "daily_price_bars",
    "evaluated_stock_records",
    "anomaly_cases",
    "skipped_stock_records",
)
ARGV = ["--date", D.isoformat()]
SUMMARY = (
    f"evaluated_date={D.isoformat()} status=completed flagged=1 evaluated=4 skipped=0 "
    "rule_version=1"
)


def write_universe(directory: Path, tickers: list[str]) -> None:
    lines = ["ticker,company_name,snapshot_date,source,share_class_note"]
    lines += [f"{t},{t} Inc.,2026-10-01,https://example.test/holdings.csv," for t in tickers]
    (directory / "nasdaq100_2026-10-01.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def table_contents(conn: Conn) -> dict[str, list[tuple[object, ...]]]:
    return {
        table: conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2".encode()).fetchall()
        for table in TABLES
    }


@pytest.fixture
def detect(
    migrated_conn: Conn,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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

    write_universe(tmp_path, sorted(US1_SPECS))
    monkeypatch.setattr(cli, "UNIVERSE_DIR", tmp_path)
    monkeypatch.setattr(cli, "connect", connect)
    monkeypatch.setenv("PRICE_DATA_API_KEY", "test-key")
    source = FakeSource(US1_SPECS)

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
    assert len(contents["daily_price_bars"]) == 5 * 122  # 4 stocks + QQQ, full window each
    assert [str(r[1]) for r in contents["evaluated_stock_records"]] == sorted(US1_SPECS)
    assert [(str(r[1]), r[2]) for r in contents["anomaly_cases"]] == [("FLAG", 1)]
    assert contents["skipped_stock_records"] == []


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
