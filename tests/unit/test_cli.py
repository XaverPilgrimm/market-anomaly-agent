"""`detect` CLI (contracts/cli-contract.md) with detection, storage and the price source faked.

The fakes replace module-level names in `market_anomaly_agent.cli`: `run_detection`, `connect`,
`load_run`, `load_bar_snapshot`, `save_run`, `load_universe_tickers` and
`TwelveDataPriceDataSource`. The trading calendar and `snapshots_match` are the real ones.
"""

import dataclasses
import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from types import TracebackType
from typing import Any, Self

import pytest

from market_anomaly_agent import cli
from market_anomaly_agent.data.source import DailyBar, PriceDataError, PriceDataSource
from market_anomaly_agent.detection import (
    AnomalyCase,
    BenchmarkUnavailableError,
    DetectionResult,
    EvaluatedRecord,
    SkippedRecord,
    TooMuchMissingDataError,
)
from market_anomaly_agent.storage.repository import StoredRun

API_KEY = "td-SECRET-api-key-0042"
DATABASE_URL = "postgresql://market_anomaly_agent:SECRET-db-pw@db.example:5432/maa"
D = date(2026, 10, 27)
SUMMARY = (
    "evaluated_date=2026-10-27 status=completed flagged=10 evaluated=94 skipped=10 rule_version=1"
)


def record(ticker: str, z: str) -> EvaluatedRecord:
    return EvaluatedRecord(
        ticker=ticker,
        return_=Decimal("0.01"),
        expected_return=Decimal("0.004"),
        abnormal_return=Decimal("0.006"),
        z_score=Decimal(z),
        alpha=Decimal("0.0001"),
        beta=Decimal("1.05"),
        estimation_window_days=120,
    )


def build_result() -> DetectionResult:
    """14 qualifying (10 flagged + 4 cap_exceeded), 80 other evaluated, 6 not evaluated."""
    qualifying = [f"Q{i:02d}" for i in range(14)]
    others = [f"E{i:02d}" for i in range(80)]
    missing = [f"M{i}" for i in range(6)]
    bars = {
        "QQQ": (DailyBar(D, Decimal("480.10"), Decimal("482.50"), 31_000_000),),
        "Q00": (DailyBar(D, Decimal("101.25"), Decimal("103.40"), 2_900_000),),
    }
    return DetectionResult(
        evaluated_date=D,
        rule_version="1",
        computed_at=datetime(2026, 10, 28, 11, 0, tzinfo=UTC),
        benchmark_ticker="QQQ",
        benchmark_return=Decimal("0.005"),
        bars=bars,
        evaluated=tuple(
            [record(t, f"{9 - i * 0.5:.2f}") for i, t in enumerate(qualifying)]
            + [record(t, "0.40") for t in others]
        ),
        anomalies=tuple(
            AnomalyCase(t, rank, Decimal("2.0"), None) for rank, t in enumerate(qualifying[:10], 1)
        ),
        skipped=tuple(
            [
                SkippedRecord(t, "cap_exceeded", f"rank {r} of 14")
                for r, t in enumerate(qualifying[10:], 11)
            ]
            + [SkippedRecord(t, "missing_data", "no bar") for t in missing]
        ),
    )


def stored_run_for(result: DetectionResult) -> StoredRun:
    return StoredRun(
        evaluated_date=result.evaluated_date,
        rule_version=result.rule_version,
        computed_at=result.computed_at,
        benchmark_ticker=result.benchmark_ticker,
        benchmark_return=result.benchmark_return,
        evaluated_count=len(result.evaluated),
        flagged_count=len(result.anomalies),
        skipped_count=len(result.skipped),
    )


class FakeConnection:
    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


class Fakes:
    def __init__(self) -> None:
        self.result = build_result()
        self.detect_error: Exception | None = None
        self.stored: StoredRun | None = None
        self.stored_bars: dict[str, list[DailyBar]] = {}
        self.saved: list[DetectionResult] = []
        self.detect_calls: list[tuple[date, list[str]]] = []
        self.source_keys: list[str] = []

    def run_detection(
        self,
        evaluated_date: date,
        tickers: Sequence[str],
        source: PriceDataSource,
        now: datetime,
    ) -> DetectionResult:
        self.detect_calls.append((evaluated_date, list(tickers)))
        if self.detect_error is not None:
            raise self.detect_error
        return self.result

    def make_source(self, api_key: str) -> PriceDataSource:
        self.source_keys.append(api_key)
        return object()  # type: ignore[return-value]  # never called: detection is faked

    def load_run(self, conn: Any, evaluated_date: date) -> StoredRun | None:
        return self.stored

    def load_bar_snapshot(self, conn: Any, evaluated_date: date) -> dict[str, list[DailyBar]]:
        return self.stored_bars

    def save_run(self, conn: Any, result: DetectionResult) -> None:
        self.saved.append(result)


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> Fakes:
    f = Fakes()
    monkeypatch.setenv("PRICE_DATA_API_KEY", API_KEY)
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setattr(cli, "run_detection", f.run_detection)
    monkeypatch.setattr(cli, "TwelveDataPriceDataSource", f.make_source)
    monkeypatch.setattr(cli, "connect", FakeConnection)
    monkeypatch.setattr(cli, "load_run", f.load_run)
    monkeypatch.setattr(cli, "load_bar_snapshot", f.load_bar_snapshot)
    monkeypatch.setattr(cli, "save_run", f.save_run)
    monkeypatch.setattr(cli, "load_universe_tickers", lambda: ["AAA", "BBB"])
    return f


def last_line(text: str) -> str:
    return text.strip().splitlines()[-1]


@pytest.mark.parametrize(
    "argv",
    [[], ["--date"], ["--date", "2026-13-45"], ["--date", "yesterday"], ["2026-10-27"]],
)
def test_usage_errors_exit_2(fakes: Fakes, argv: list[str]) -> None:
    assert cli.main(argv) == 2
    assert fakes.detect_calls == []


def test_non_trading_day_exits_2(fakes: Fakes, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--date", "2026-10-24"]) == 2  # a Saturday
    assert fakes.detect_calls == []
    assert "2026-10-24" in capsys.readouterr().err


def test_fresh_run_saves_and_prints_summary(
    fakes: Fakes, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--date", "2026-10-27"]) == 0

    assert fakes.detect_calls == [(D, ["AAA", "BBB"])]
    assert fakes.source_keys == [API_KEY]
    assert fakes.saved == [fakes.result]
    assert last_line(capsys.readouterr().out) == SUMMARY


def test_rerun_with_matching_snapshot_reproduces_without_saving(
    fakes: Fakes, capsys: pytest.CaptureFixture[str]
) -> None:
    fakes.stored = stored_run_for(fakes.result)
    fakes.stored_bars = {t: list(bars) for t, bars in fakes.result.bars.items()}

    assert cli.main(["--date", "2026-10-27"]) == 0

    assert fakes.saved == []
    assert last_line(capsys.readouterr().out) == SUMMARY


def test_rerun_with_differing_snapshot_rejects_with_3(
    fakes: Fakes, capsys: pytest.CaptureFixture[str]
) -> None:
    fakes.stored = stored_run_for(fakes.result)
    fakes.stored_bars = {t: list(bars) for t, bars in fakes.result.bars.items()}
    corrected = dataclasses.replace(fakes.stored_bars["Q00"][0], close=Decimal("103.41"))
    fakes.stored_bars["Q00"] = [corrected]

    assert cli.main(["--date", "2026-10-27"]) == 3

    assert fakes.saved == []
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "2026-10-27" in captured.err


@pytest.mark.parametrize(
    "error",
    [
        BenchmarkUnavailableError("benchmark QQQ has no bar on 2026-10-27"),
        TooMuchMissingDataError("21 of 100 tickers have missing data"),
    ],
)
def test_unusable_input_exits_4_and_stores_nothing(
    fakes: Fakes, capsys: pytest.CaptureFixture[str], error: Exception
) -> None:
    fakes.detect_error = error

    assert cli.main(["--date", "2026-10-27"]) == 4

    assert fakes.saved == []
    captured = capsys.readouterr()
    assert captured.out == ""
    assert str(error) in captured.err


def test_price_data_error_exits_1(fakes: Fakes) -> None:
    fakes.detect_error = PriceDataError("AAA: giving up after 3 retries (HTTP 503)")

    assert cli.main(["--date", "2026-10-27"]) == 1
    assert fakes.saved == []


def test_unexpected_error_exits_1_without_leaking_secrets(
    fakes: Fakes, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    fakes.detect_error = RuntimeError(
        f"boom using {API_KEY} against {DATABASE_URL} (password SECRET-db-pw)"
    )

    assert cli.main(["--date", "2026-10-27"]) == 1

    assert fakes.saved == []
    captured = capsys.readouterr()
    outputs = [captured.out, captured.err, caplog.text]
    for secret in (API_KEY, DATABASE_URL, "SECRET-db-pw"):
        assert all(secret not in text for text in outputs)
    assert "RuntimeError" in captured.err + caplog.text

    tracebacks = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.ERROR and "Traceback (most recent call last)" in r.getMessage()
    ]
    assert len(tracebacks) == 1
    assert "in run_detection" in tracebacks[0]  # the frame that raised, in tests' Fakes
    assert tracebacks[0].endswith("RuntimeError: boom using *** against *** (password ***)")
    assert "Traceback (most recent call last)" in captured.err


def test_missing_api_key_exits_1_naming_the_variable(
    fakes: Fakes, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("PRICE_DATA_API_KEY")

    assert cli.main(["--date", "2026-10-27"]) == 1

    assert fakes.detect_calls == []
    assert "PRICE_DATA_API_KEY" in capsys.readouterr().err


def test_progress_logs_go_to_stderr_and_stdout_is_only_the_summary(
    fakes: Fakes, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """T053 (1): INFO is on by default; progress never reaches stdout."""

    def detect_with_progress(
        evaluated_date: date, tickers: Sequence[str], source: PriceDataSource, now: datetime
    ) -> DetectionResult:
        logging.getLogger("market_anomaly_agent.detection").info("fetched AAA (2 of 3)")
        logging.getLogger("market_anomaly_agent.data.twelvedata_source").warning(
            "BBB: HTTP 503, retrying in 2 s"
        )
        return fakes.run_detection(evaluated_date, tickers, source, now)

    monkeypatch.setattr(cli, "run_detection", detect_with_progress)

    assert cli.main(["--date", "2026-10-27"]) == 0

    captured = capsys.readouterr()
    assert "INFO market_anomaly_agent.detection: fetched AAA (2 of 3)" in captured.err
    assert (
        "WARNING market_anomaly_agent.data.twelvedata_source: BBB: HTTP 503, retrying in 2 s"
        in captured.err
    )
    assert captured.out == SUMMARY + "\n"
