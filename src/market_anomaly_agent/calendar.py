"""US equity market trading days on the NASDAQ calendar, in New York time (FR-013).

Only session dates matter here, so early-close sessions are ordinary trading days.
"""

from datetime import date, datetime, timedelta
from functools import cache
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal

_TZ = "America/New_York"
FETCH_LOOKBACK_DAYS = 121  # up to 120 returns before the evaluated day need 121 closes


@cache
def _calendar() -> mcal.MarketCalendar:
    return mcal.get_calendar("NASDAQ")


def trading_days_between(start: date, end: date) -> list[date]:
    """Trading days from `start` to `end`, both inclusive, ascending."""
    if end < start:
        return []
    sessions = _calendar().valid_days(start_date=start, end_date=end, tz=_TZ)
    return [ts.date() for ts in sessions]


def is_trading_day(d: date) -> bool:
    return trading_days_between(d, d) == [d]


def trading_days_before(d: date, n: int) -> list[date]:
    """The `n` trading days strictly before `d`, ascending."""
    if n <= 0:
        return []
    # Roughly 252 trading days per 365 calendar days; widen until enough are found.
    span = n * 2 + 14
    while True:
        days = trading_days_between(d - timedelta(days=span), d - timedelta(days=1))
        if len(days) >= n:
            return days[-n:]
        span *= 2


def previous_trading_day(d: date) -> date:
    return trading_days_before(d, 1)[0]


def fetch_window_start(d: date) -> date:
    """First day of the price window fetched for evaluated day `d`."""
    return trading_days_before(d, FETCH_LOOKBACK_DAYS)[0]


def latest_completed_trading_day(now: datetime) -> date:
    """The most recent trading day strictly before `now`'s calendar date in New York (002 FR-001).

    A trading day's own date is never returned, even late in the evening, so a day is never
    evaluated from bars that may still be provisional. `now` must be timezone-aware.
    """
    if now.tzinfo is None:
        raise ValueError("now must be a timezone-aware datetime")
    return previous_trading_day(now.astimezone(ZoneInfo(_TZ)).date())
