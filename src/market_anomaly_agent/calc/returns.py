"""Close-to-close returns from adjusted closes (FR-005)."""

import itertools
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal


def simple_returns(
    closes: Mapping[date, Decimal], trading_days: Sequence[date]
) -> dict[date, float]:
    """Return per trading day as close / previous trading day's close - 1.

    A day gets a return only when it and the trading day before it in `trading_days` both have a
    close; a return is never computed across a gap, and the first day never has one.
    """
    returns: dict[date, float] = {}
    for previous, day in itertools.pairwise(trading_days):
        if day in closes and previous in closes:
            returns[day] = float(closes[day] / closes[previous] - 1)
    return returns
