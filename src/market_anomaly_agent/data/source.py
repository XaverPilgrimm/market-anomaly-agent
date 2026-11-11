"""Vendor-neutral price-data interface (contracts/price-data-source-interface.md)."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class DailyBar:
    trading_date: date
    open: Decimal  # split- and dividend-adjusted (FR-004)
    close: Decimal  # split- and dividend-adjusted (FR-004; FR-005 computes returns from this)
    volume: int


class PriceDataUnavailable(Exception):
    """The vendor reports no matching data for the ticker."""


class PriceDataError(Exception):
    """Retries or the request budget were exhausted, or the vendor sent something unusable."""


class PriceDataSource(Protocol):
    def get_daily_bars(self, ticker: str, start: date, end: date) -> list[DailyBar]:
        """Adjusted daily bars for `ticker` from `start` to `end`, inclusive, trading days only.

        Raises PriceDataUnavailable if the vendor reports no matching data for `ticker`, and
        PriceDataError for any other failure; returns a (possibly shorter) list, never raises,
        when some days within the range are missing.
        """
        ...
