"""Detection orchestration against a fake price source (no database, no network).

The fixture history and its exact market-model numbers are described in tests/support/fakes.py.
"""

import dataclasses
import math
import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest

from market_anomaly_agent.calendar import fetch_window_start
from market_anomaly_agent.data.source import DailyBar, PriceDataError, PriceDataUnavailable
from market_anomaly_agent.detection import (
    BenchmarkUnavailableError,
    DetectionResult,
    TooMuchMissingDataError,
    run_detection,
    snapshots_match,
)
from market_anomaly_agent.rules import BENCHMARK_TICKER, RULE_VERSION
from tests.support.fakes import (
    BENCHMARK_RETURN_ON_D,
    DAYS,
    NOW,
    US1_SPECS,
    D,
    FakeSource,
    StockSpec,
)


def run(specs: dict[str, StockSpec]) -> tuple[DetectionResult, FakeSource]:
    source = FakeSource(specs)
    return run_detection(D, list(specs), source, NOW), source


def test_fixture_window_is_122_trading_days() -> None:
    assert len(DAYS) == 122
    assert DAYS[-1] == D


def test_flags_the_one_large_abnormal_move() -> None:
    """Spec US1 scenario 1: FLAG has z ~ 9.92 >= 2; its volume ratio is
    3,000,000 / average(20 x 1,000,000) = 3.0."""
    result, _ = run(US1_SPECS)

    assert [(a.ticker, a.rank) for a in result.anomalies] == [("FLAG", 1)]
    case = result.anomalies[0]
    assert case.volume_ratio is not None
    assert float(case.volume_ratio) == pytest.approx(3.0, rel=1e-12)
    assert case.volume_ratio_unavailable_reason is None


def test_every_stock_is_evaluated_with_stored_form_numbers() -> None:
    """Spec US1 scenario 4: non-flagged stocks are evaluated records, not skipped."""
    result, _ = run(US1_SPECS)

    assert [r.ticker for r in result.evaluated] == ["AAA", "BBB", "CCC", "FLAG"]
    assert result.skipped == ()
    for record in result.evaluated:
        spec = US1_SPECS[record.ticker]
        numbers = (
            record.return_,
            record.expected_return,
            record.abnormal_return,
            record.z_score,
            record.alpha,
            record.beta,
        )
        assert all(isinstance(n, Decimal) for n in numbers)
        assert float(record.alpha) == pytest.approx(spec.alpha, abs=1e-12)
        assert float(record.beta) == pytest.approx(spec.beta, abs=1e-9)
        assert float(record.expected_return) == pytest.approx(spec.expected_on_d, abs=1e-12)
        assert float(record.return_) == pytest.approx(spec.expected_on_d + spec.abnormal, abs=1e-12)
        assert float(record.abnormal_return) == pytest.approx(spec.abnormal, abs=1e-12)
        assert float(record.z_score) == pytest.approx(spec.z, abs=1e-8)
        assert record.estimation_window_days == 120


def test_run_metadata_and_benchmark_return() -> None:
    result, _ = run(US1_SPECS)

    assert result.evaluated_date == D
    assert result.rule_version == RULE_VERSION
    assert result.computed_at == NOW
    assert result.benchmark_ticker == BENCHMARK_TICKER
    assert isinstance(result.benchmark_return, Decimal)
    assert float(result.benchmark_return) == pytest.approx(BENCHMARK_RETURN_ON_D, abs=1e-15)


def test_result_carries_the_fetched_bar_snapshot() -> None:
    result, source = run(US1_SPECS)

    assert set(result.bars) == {*US1_SPECS, BENCHMARK_TICKER}
    for ticker, bars in result.bars.items():
        assert list(bars) == source.bars[ticker]


def test_fetches_the_window_for_every_ticker_and_the_benchmark() -> None:
    _, source = run(US1_SPECS)

    start = fetch_window_start(D)
    assert sorted(source.calls) == sorted(
        (ticker, start, D) for ticker in [*US1_SPECS, BENCHMARK_TICKER]
    )


CAP_SPECS = {f"Q{i:02d}": StockSpec(0.0, 1.0, 1.0, 0.005 + 0.001 * i) for i in range(1, 15)}


def test_fourteen_qualifying_keep_ten_and_record_four_cap_exceeded() -> None:
    """Spec US1 scenario 2: all 14 have z >= 2.97; Q14 is largest, Q01 smallest."""
    result, _ = run(CAP_SPECS)

    assert len(result.evaluated) == 14
    assert {r.ticker for r in result.evaluated} == set(CAP_SPECS)
    assert [(a.ticker, a.rank) for a in result.anomalies] == [
        (f"Q{i:02d}", 15 - i) for i in range(14, 4, -1)
    ]

    assert [(s.ticker, s.reason_code) for s in result.skipped] == [
        (f"Q{i:02d}", "cap_exceeded") for i in range(1, 5)
    ]
    expected_rank = {"Q04": 11, "Q03": 12, "Q02": 13, "Q01": 14}
    for skip in result.skipped:
        assert skip.reason_detail is not None
        match = re.fullmatch(r"abs z=(\d+\.\d{4}), rank (\d+) of 14 qualifying", skip.reason_detail)
        assert match is not None, skip.reason_detail
        assert float(match.group(1)) == pytest.approx(abs(CAP_SPECS[skip.ticker].z), abs=1e-4)
        assert int(match.group(2)) == expected_rank[skip.ticker]

    anomaly_tickers = {a.ticker for a in result.anomalies}
    assert anomaly_tickers.isdisjoint(s.ticker for s in result.skipped)


def test_same_input_gives_equal_results() -> None:
    """FR-020."""
    first, _ = run(US1_SPECS)
    second, _ = run(US1_SPECS)

    assert first == second


# --- snapshots_match (T029, FR-018) -------------------------------------------------------------


def snapshot() -> dict[str, list[DailyBar]]:
    prev = DAYS[-2]
    return {
        "AAA": [
            DailyBar(prev, Decimal("101.25"), Decimal("102.50"), 1_200_000),
            DailyBar(D, Decimal("102.60"), Decimal("103.40"), 2_900_000),
        ],
        BENCHMARK_TICKER: [
            DailyBar(prev, Decimal("480.10"), Decimal("481.00"), 30_000_000),
            DailyBar(D, Decimal("481.20"), Decimal("482.50"), 31_000_000),
        ],
    }


def test_snapshots_match_when_values_are_equal() -> None:
    stored = snapshot()
    fetched = {t: tuple(bars) for t, bars in snapshot().items()}

    assert snapshots_match(stored, fetched)


def test_snapshots_match_compares_decimal_values_not_their_spelling() -> None:
    fetched = snapshot()
    fetched["AAA"][0] = dataclasses.replace(fetched["AAA"][0], close=Decimal("102.500"))

    assert snapshots_match(snapshot(), fetched)


@pytest.mark.parametrize(
    "changed",
    [
        DailyBar(DAYS[-2], Decimal("101.26"), Decimal("102.50"), 1_200_000),
        DailyBar(DAYS[-2], Decimal("101.25"), Decimal("102.49"), 1_200_000),
        DailyBar(DAYS[-2], Decimal("101.25"), Decimal("102.50"), 1_200_001),
    ],
    ids=["open", "close", "volume"],
)
def test_snapshots_differ_on_any_changed_value(changed: DailyBar) -> None:
    fetched = snapshot()
    fetched["AAA"][0] = changed

    assert not snapshots_match(snapshot(), fetched)


def test_snapshots_differ_on_a_missing_bar() -> None:
    fetched = snapshot()
    del fetched["AAA"][0]

    assert not snapshots_match(snapshot(), fetched)


def test_snapshots_differ_on_an_extra_bar() -> None:
    fetched = snapshot()
    fetched["AAA"].insert(0, DailyBar(DAYS[-3], Decimal(100), Decimal("101.25"), 900_000))

    assert not snapshots_match(snapshot(), fetched)


def test_snapshots_differ_on_a_moved_trading_date() -> None:
    fetched = snapshot()
    fetched["AAA"][0] = dataclasses.replace(fetched["AAA"][0], trading_date=DAYS[-3])

    assert not snapshots_match(snapshot(), fetched)


def test_snapshots_differ_on_a_missing_or_extra_ticker() -> None:
    fewer = snapshot()
    del fewer["AAA"]
    more = snapshot()
    more["BBB"] = list(more["AAA"])

    assert not snapshots_match(snapshot(), fewer)
    assert not snapshots_match(snapshot(), more)


# --- US3: skips (T038) and run-level aborts (T039) ----------------------------------------------
#
# Every fixture universe keeps its missing_data skips at or below 20% of its tickers (FR-023).

FILLER = StockSpec(0.0, 1.0, 1.0, 0.0)  # z = 0: evaluated, never flagged
BIG_MOVE = StockSpec(0.001, 1.5, 1.0, 0.02, volume_on_d=3_000_000)  # z ~ +9.92 on full history
FLAT = StockSpec(0.0, 0.0, 0.0, 0.0)  # constant price: every return is exactly 0


def fillers(count: int) -> dict[str, StockSpec]:
    return {f"N{i:02d}": FILLER for i in range(count)}


def keep_paired_returns(source: FakeSource, ticker: str, paired: int) -> None:
    """Cut `ticker`'s history so exactly `paired` returns exist before D (bars -> returns - 1,
    minus D's own return)."""
    source.bars[ticker] = source.bars[ticker][-(paired + 2) :]


def drop_bars(source: FakeSource, ticker: str, *days: date) -> None:
    source.bars[ticker] = [b for b in source.bars[ticker] if b.trading_date not in days]


def outcomes(result: DetectionResult) -> dict[str, str]:
    """Each ticker's outcome per data-model.md's cross-entity invariants, or "invalid"."""
    evaluated = {r.ticker for r in result.evaluated}
    flagged = {a.ticker for a in result.anomalies}
    skips = {s.ticker: s.reason_code for s in result.skipped}
    found: dict[str, str] = {}
    for ticker in evaluated | flagged | skips.keys():
        skip = skips.get(ticker)
        if ticker in evaluated and ticker in flagged and skip is None:
            found[ticker] = "flagged"
        elif ticker in evaluated and ticker not in flagged and skip == "cap_exceeded":
            found[ticker] = "cap_exceeded"
        elif ticker in evaluated and ticker not in flagged and skip is None:
            found[ticker] = "evaluated"
        elif (
            ticker not in evaluated
            and ticker not in flagged
            and skip
            in (
                "insufficient_history",
                "missing_data",
            )
        ):
            found[ticker] = skip
        else:
            found[ticker] = "invalid"
    return found


def skip_of(result: DetectionResult, ticker: str) -> tuple[str, str]:
    [skip] = [s for s in result.skipped if s.ticker == ticker]
    assert skip.reason_detail is not None
    return skip.reason_code, skip.reason_detail


def test_45_days_of_history_is_skipped_as_insufficient_history() -> None:
    """Spec US3 scenario 1: no partial-window evaluation, even for a big move."""
    specs = {"SHORT": BIG_MOVE, **fillers(9)}
    source = FakeSource(specs)
    keep_paired_returns(source, "SHORT", 45)

    result = run_detection(D, list(specs), source, NOW)

    code, detail = skip_of(result, "SHORT")
    assert code == "insufficient_history"
    assert re.search(r"\b45\b", detail) and re.search(r"\b60\b", detail), detail
    assert "SHORT" not in {r.ticker for r in result.evaluated}
    assert result.anomalies == ()


def test_80_days_of_history_is_evaluated_on_those_80_returns() -> None:
    """Spec US3 scenario 2. Over the last 80 days the fixture's residual pattern is still
    orthogonal to the benchmark, so alpha and beta stay exact and the residual std becomes
    k * 0.002 * sqrt(80 / 78)."""
    spec = StockSpec(0.0005, 1.2, 1.0, 0.004)
    specs = {"MID": spec, **fillers(9)}
    source = FakeSource(specs)
    keep_paired_returns(source, "MID", 80)

    result = run_detection(D, list(specs), source, NOW)

    [record] = [r for r in result.evaluated if r.ticker == "MID"]
    assert record.estimation_window_days == 80
    assert float(record.alpha) == pytest.approx(spec.alpha, abs=1e-12)
    assert float(record.beta) == pytest.approx(spec.beta, abs=1e-9)
    residual_std = spec.k * 0.002 * math.sqrt(80 / 78)
    assert float(record.z_score) == pytest.approx(spec.abnormal / residual_std, abs=1e-8)
    assert "MID" not in {s.ticker for s in result.skipped}


@pytest.mark.parametrize("missing_day", [D, DAYS[-2]], ids=["evaluated-day", "previous-day"])
def test_a_missing_bar_on_the_evaluated_or_previous_day_is_missing_data(missing_day: date) -> None:
    """Spec US3 scenario 3: the reason names the missing date."""
    specs = {"GAP": BIG_MOVE, **fillers(9)}
    source = FakeSource(specs)
    drop_bars(source, "GAP", missing_day)

    result = run_detection(D, list(specs), source, NOW)

    code, detail = skip_of(result, "GAP")
    assert code == "missing_data"
    assert missing_day.isoformat() in detail
    assert outcomes(result)["GAP"] == "missing_data"


def test_a_ticker_the_vendor_has_no_data_for_is_missing_data() -> None:
    specs = {"GONE": FILLER, **fillers(9)}
    source = FakeSource(specs)
    source.raises["GONE"] = PriceDataUnavailable("GONE: symbol not found")

    result = run_detection(D, list(specs), source, NOW)

    assert skip_of(result, "GONE")[0] == "missing_data"
    assert outcomes(result)["GONE"] == "missing_data"


def test_zero_residual_variance_is_missing_data() -> None:
    specs = {"FLAT": FLAT, **fillers(9)}

    result = run_detection(D, list(specs), FakeSource(specs), NOW)

    assert skip_of(result, "FLAT") == ("missing_data", "zero residual variance")
    assert outcomes(result)["FLAT"] == "missing_data"


def test_every_ticker_gets_exactly_one_outcome() -> None:
    """data-model.md cross-entity invariants over a universe that has all four outcomes:
    23 tickers, 4 of them missing_data (17%)."""
    qualifying = {f"Q{i:02d}": StockSpec(0.0, 1.0, 1.0, 0.005 + 0.001 * i) for i in range(1, 12)}
    specs = {
        **qualifying,  # 11 with z >= 2.98: Q02..Q11 flagged, Q01 cap_exceeded
        "SHORT": BIG_MOVE,
        "MID": FILLER,
        "NOD": FILLER,
        "NOPREV": FILLER,
        "GONE": FILLER,
        "FLAT": FLAT,
        **fillers(6),
    }
    source = FakeSource(specs)
    keep_paired_returns(source, "SHORT", 45)
    keep_paired_returns(source, "MID", 80)
    drop_bars(source, "NOD", D)
    drop_bars(source, "NOPREV", DAYS[-2])
    source.raises["GONE"] = PriceDataUnavailable("GONE: symbol not found")

    result = run_detection(D, list(specs), source, NOW)

    assert outcomes(result) == {
        **{t: "flagged" for t in qualifying if t != "Q01"},
        "Q01": "cap_exceeded",
        "SHORT": "insufficient_history",
        "MID": "evaluated",
        "NOD": "missing_data",
        "NOPREV": "missing_data",
        "GONE": "missing_data",
        "FLAT": "missing_data",
        **{t: "evaluated" for t in fillers(6)},
    }


def test_flagged_ticker_with_9_volume_days_keeps_its_flag_without_a_ratio() -> None:
    """FR-011: 11 of the 20 days before D have no bar (D's previous day is kept, so D still has
    a return); the move stays flagged, with volume_ratio None and the reason set."""
    specs = {"VOL": BIG_MOVE, **fillers(9)}
    source = FakeSource(specs)
    drop_bars(source, "VOL", *DAYS[-21:-10])

    result = run_detection(D, list(specs), source, NOW)

    [case] = result.anomalies
    assert case.ticker == "VOL"
    assert case.volume_ratio is None
    assert case.volume_ratio_unavailable_reason is not None
    assert re.search(r"\b9\b", case.volume_ratio_unavailable_reason)


def _no_benchmark_bar_on_d(source: FakeSource) -> None:
    drop_bars(source, BENCHMARK_TICKER, D)


def _no_benchmark_bar_on_previous_day(source: FakeSource) -> None:
    drop_bars(source, BENCHMARK_TICKER, DAYS[-2])


def _benchmark_unavailable(source: FakeSource) -> None:
    source.raises[BENCHMARK_TICKER] = PriceDataUnavailable("QQQ: symbol not found")


@pytest.mark.parametrize(
    ("break_benchmark", "cause"),
    [
        (_no_benchmark_bar_on_d, D.isoformat()),
        (_no_benchmark_bar_on_previous_day, DAYS[-2].isoformat()),
        (_benchmark_unavailable, "symbol not found"),
    ],
    ids=["no-bar-on-evaluated-day", "no-bar-on-previous-day", "unavailable"],
)
def test_unusable_benchmark_aborts_the_run(
    break_benchmark: Callable[[FakeSource], None], cause: str
) -> None:
    """FR-022: the message names the benchmark and the cause; nothing is returned."""
    specs = fillers(10)
    source = FakeSource(specs)
    break_benchmark(source)

    with pytest.raises(BenchmarkUnavailableError) as info:
        run_detection(D, list(specs), source, NOW)

    assert BENCHMARK_TICKER in str(info.value)
    assert cause in str(info.value)


def _hundred_with_missing(count: int) -> tuple[dict[str, StockSpec], FakeSource]:
    """100 tickers; the first `count` are missing data, half without a bar on D and half
    unknown to the vendor."""
    specs = fillers(100)
    source = FakeSource(specs)
    for i, ticker in enumerate(list(specs)[:count]):
        if i % 2 == 0:
            drop_bars(source, ticker, D)
        else:
            source.raises[ticker] = PriceDataUnavailable(f"{ticker}: symbol not found")
    return specs, source


def test_21_of_100_missing_data_aborts_the_run() -> None:
    """FR-023: more than 20% missing data."""
    specs, source = _hundred_with_missing(21)

    with pytest.raises(TooMuchMissingDataError, match="21 of 100"):
        run_detection(D, list(specs), source, NOW)


def test_exactly_20_of_100_missing_data_completes() -> None:
    """FR-023: exactly 20% still completes, with the 20 skips recorded."""
    specs, source = _hundred_with_missing(20)

    result = run_detection(D, list(specs), source, NOW)

    assert (
        sorted(s.ticker for s in result.skipped if s.reason_code == "missing_data")
        == list(specs)[:20]
    )
    assert len(result.evaluated) == 80


def test_insufficient_history_does_not_count_toward_the_missing_data_limit() -> None:
    specs = fillers(100)
    source = FakeSource(specs)
    for ticker in list(specs)[:25]:
        keep_paired_returns(source, ticker, 50)

    result = run_detection(D, list(specs), source, NOW)

    assert [s.reason_code for s in result.skipped] == ["insufficient_history"] * 25
    assert len(result.evaluated) == 75


@pytest.mark.parametrize("ticker", ["N03", BENCHMARK_TICKER])
def test_price_data_error_propagates_unchanged(ticker: str) -> None:
    """A vendor failure is not a data gap: it is not caught (cli exits 1)."""
    specs = fillers(10)
    source = FakeSource(specs)
    error = PriceDataError("retries exhausted")
    source.raises[ticker] = error

    with pytest.raises(PriceDataError) as info:
        run_detection(D, list(specs), source, NOW)

    assert info.value is error
