from datetime import UTC, date, datetime

import pytest

from market_anomaly_agent import calendar
from market_anomaly_agent.calendar import (
    fetch_window_start,
    is_trading_day,
    previous_trading_day,
    trading_days_before,
    trading_days_between,
)


def test_weekend_is_not_a_trading_day() -> None:
    assert not is_trading_day(date(2025, 11, 29))  # Saturday


def test_market_holidays_are_not_trading_days() -> None:
    assert not is_trading_day(date(2025, 12, 25))  # Christmas
    assert not is_trading_day(date(2025, 7, 4))  # Independence Day


def test_early_close_session_is_a_full_trading_day() -> None:
    assert is_trading_day(date(2025, 11, 28))  # day after Thanksgiving, closes 13:00


def test_previous_trading_day_skips_holiday() -> None:
    assert previous_trading_day(date(2025, 12, 26)) == date(2025, 12, 24)


def test_previous_trading_day_crosses_new_year() -> None:
    assert previous_trading_day(date(2026, 1, 2)) == date(2025, 12, 31)


def test_trading_days_before_is_ascending_and_ignores_dst_change() -> None:
    # DST starts in New York on Sunday 2026-03-08; Monday 2026-03-09 is the evaluated day.
    assert trading_days_before(date(2026, 3, 9), 5) == [
        date(2026, 3, 2),
        date(2026, 3, 3),
        date(2026, 3, 4),
        date(2026, 3, 5),
        date(2026, 3, 6),
    ]


def test_trading_days_between_is_inclusive() -> None:
    assert trading_days_between(date(2025, 12, 24), date(2025, 12, 29)) == [
        date(2025, 12, 24),
        date(2025, 12, 26),
        date(2025, 12, 29),
    ]


def test_fetch_window_start_is_121_trading_days_before() -> None:
    d = date(2026, 10, 27)
    start = fetch_window_start(d)
    assert start == trading_days_before(d, 121)[0]
    # 121 days before plus the evaluated day itself.
    assert len(trading_days_between(start, d)) == 122


# latest_completed_trading_day: the most recent NASDAQ trading day strictly before the New York
# calendar date of `now` (002 spec FR-001, contracts/cli-contract.md).


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        pytest.param(
            datetime(2026, 11, 10, 11, 0, tzinfo=UTC), date(2026, 11, 9), id="tuesday-to-monday"
        ),
        pytest.param(
            datetime(2026, 11, 14, 11, 0, tzinfo=UTC), date(2026, 11, 13), id="saturday-to-friday"
        ),
        pytest.param(
            # Fri 2026-11-27 is the early-close day after Thanksgiving (Thu 2026-11-26).
            datetime(2026, 11, 27, 11, 0, tzinfo=UTC),
            date(2026, 11, 25),
            id="morning-after-holiday",
        ),
        pytest.param(
            # 23:30 New York on trading day 2026-11-27: that day's own date is never returned.
            datetime(2026, 11, 28, 4, 30, tzinfo=UTC),
            date(2026, 11, 25),
            id="late-evening-of-a-trading-day",
        ),
        pytest.param(
            # 00:30 UTC on 2026-11-10 is still 2026-11-09 (19:30 EST) in New York.
            datetime(2026, 11, 10, 0, 30, tzinfo=UTC),
            date(2026, 11, 6),
            id="new-york-date-behind-utc-date",
        ),
        pytest.param(
            # Before the 2026-11-01 change (EDT, UTC-4): 04:30 UTC is 00:30 on Fri 2026-10-30.
            # Under EST it would still be Thursday, giving 2026-10-28.
            datetime(2026, 10, 30, 4, 30, tzinfo=UTC),
            date(2026, 10, 29),
            id="before-dst-change-uses-edt",
        ),
        pytest.param(
            # After the change (EST, UTC-5): 04:30 UTC is 23:30 on Mon 2026-11-02.
            # Under EDT it would already be Tuesday, giving 2026-11-02.
            datetime(2026, 11, 3, 4, 30, tzinfo=UTC),
            date(2026, 10, 30),
            id="after-dst-change-uses-est",
        ),
        pytest.param(
            datetime(2026, 10, 31, 11, 0, tzinfo=UTC), date(2026, 10, 30), id="saturday-before-dst"
        ),
        pytest.param(
            datetime(2026, 11, 3, 11, 0, tzinfo=UTC), date(2026, 11, 2), id="tuesday-after-dst"
        ),
    ],
)
def test_latest_completed_trading_day(now: datetime, expected: date) -> None:
    assert calendar.latest_completed_trading_day(now) == expected


def test_latest_completed_trading_day_rejects_a_naive_datetime() -> None:
    with pytest.raises(ValueError):
        calendar.latest_completed_trading_day(datetime(2026, 11, 10, 11, 0))
