from datetime import date

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
