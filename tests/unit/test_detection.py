"""Detection orchestration against a fake price source (no database, no network).

The fixture history and its exact market-model numbers are described in tests/support/fakes.py.
"""

import dataclasses
import re
from decimal import Decimal

import pytest

from market_anomaly_agent.calendar import fetch_window_start
from market_anomaly_agent.data.source import DailyBar
from market_anomaly_agent.detection import DetectionResult, run_detection, snapshots_match
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
