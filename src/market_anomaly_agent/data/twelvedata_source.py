"""Twelve Data adapter for PriceDataSource (research.md §1 and §6).

Requests are sequential and throttled to the free Basic plan: at most 8 per minute and 800 per
adapter instance (the daily allowance). The API key travels only in the Authorization header,
so it never appears in URLs, logs or error messages.
"""

import logging
import time
from collections.abc import Callable, Mapping
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import requests

from market_anomaly_agent.data.source import DailyBar, PriceDataError, PriceDataUnavailable

logger = logging.getLogger(__name__)

URL = "https://api.twelvedata.com/time_series"
MIN_INTERVAL_SECONDS = 60 / 8  # 8 requests per minute
MAX_REQUESTS = 800  # requests per day on the free plan
MAX_RETRIES = 3
RATE_LIMIT_WAIT_SECONDS = 60.0
BACKOFF_SECONDS = (2.0, 4.0, 8.0)
TIMEOUT_SECONDS = 30.0
UNAVAILABLE_CODES = {404}  # no matching data; 400 (invalid parameter) must fail loudly
# Every Nasdaq-100 stock and QQQ are Nasdaq-listed; pin the listing instead of relying on the
# vendor's undocumented resolution of multi-listed symbols (contract, "Listing resolution").
EXCHANGE = "NASDAQ"
CURRENCY = "USD"


class HttpResponse(Protocol):
    status_code: int

    def json(self) -> Any: ...


class HttpSession(Protocol):
    def get(
        self,
        url: str,
        *,
        params: Mapping[str, str],
        headers: Mapping[str, str],
        timeout: float,
    ) -> HttpResponse: ...


class _Retry(Exception):
    def __init__(self, reason: str, wait: float | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.wait = wait  # None means: use the regular backoff


class TwelveDataPriceDataSource:
    def __init__(
        self,
        api_key: str,
        session: HttpSession | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._api_key = api_key
        self._session: HttpSession = session if session is not None else requests.Session()
        self._sleep = sleep
        self._clock = clock
        self._last_request_at: float | None = None
        self._requests_made = 0

    def get_daily_bars(self, ticker: str, start: date, end: date) -> list[DailyBar]:
        params = {
            "symbol": ticker,
            "interval": "1day",
            "adjust": "all",  # the API default is "splits", which leaves dividends unadjusted
            "start_date": start.isoformat(),
            # One day past `end`, so `end` is included whether or not the API treats
            # end_date as exclusive; the result is filtered back to [start, end] below.
            "end_date": (end + timedelta(days=1)).isoformat(),
            "order": "asc",
            "timezone": "America/New_York",
            "outputsize": "5000",
            "exchange": EXCHANGE,
        }
        values = self._fetch(ticker, params)
        bars = [_parse_bar(ticker, v) for v in values]
        return sorted(
            (b for b in bars if start <= b.trading_date <= end), key=lambda b: b.trading_date
        )

    def _fetch(self, ticker: str, params: Mapping[str, str]) -> list[Any]:
        for attempt in range(MAX_RETRIES + 1):
            try:
                return self._request_once(ticker, params)
            except _Retry as retry:
                if attempt == MAX_RETRIES:
                    raise PriceDataError(
                        f"{ticker}: giving up after {MAX_RETRIES} retries ({retry.reason})"
                    ) from None
                wait = retry.wait if retry.wait is not None else BACKOFF_SECONDS[attempt]
                logger.warning("%s: %s, retrying in %.0f s", ticker, retry.reason, wait)
                self._sleep(wait)
        raise AssertionError("unreachable")

    def _request_once(self, ticker: str, params: Mapping[str, str]) -> list[Any]:
        self._throttle()
        try:
            response = self._session.get(
                URL,
                params=params,
                headers={"Authorization": f"apikey {self._api_key}"},
                timeout=TIMEOUT_SECONDS,
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise _Retry(type(exc).__name__) from None

        if response.status_code == 429:
            raise _Retry("HTTP 429", RATE_LIMIT_WAIT_SECONDS)
        if response.status_code >= 500:
            raise _Retry(f"HTTP {response.status_code}")
        if response.status_code != 200:
            raise PriceDataError(f"{ticker}: unexpected HTTP {response.status_code}")

        try:
            body = response.json()
        except ValueError:
            raise PriceDataError(f"{ticker}: response is not JSON") from None
        if not isinstance(body, dict):
            raise PriceDataError(f"{ticker}: unexpected response shape")

        if body.get("status") == "error":
            code = body.get("code")
            message = self._scrub(str(body.get("message", "")))
            if code == 429:
                raise _Retry("rate limited", RATE_LIMIT_WAIT_SECONDS)
            if isinstance(code, int) and code >= 500:
                raise _Retry(f"vendor error {code}")
            if code in UNAVAILABLE_CODES:
                raise PriceDataUnavailable(f"{ticker}: {message}")
            raise PriceDataError(f"{ticker}: vendor error {code}: {message}")

        _check_listing(ticker, body.get("meta"))
        values = body.get("values")
        if not isinstance(values, list):
            raise PriceDataError(f"{ticker}: response has no 'values' list")
        return values

    def _throttle(self) -> None:
        if self._requests_made >= MAX_REQUESTS:
            raise PriceDataError(f"request budget of {MAX_REQUESTS} per run exhausted")
        if self._last_request_at is not None:
            elapsed = self._clock() - self._last_request_at
            if elapsed < MIN_INTERVAL_SECONDS:
                self._sleep(MIN_INTERVAL_SECONDS - elapsed)
        self._last_request_at = self._clock()
        self._requests_made += 1

    def _scrub(self, text: str) -> str:
        return text.replace(self._api_key, "***") if self._api_key else text


def _check_listing(ticker: str, meta: Any) -> None:
    """Raise unless the response is for the Nasdaq listing in US dollars. Not retried: asking
    again would get the same listing back."""
    fields = meta if isinstance(meta, dict) else {}
    exchange, currency = fields.get("exchange"), fields.get("currency")
    if exchange != EXCHANGE or currency != CURRENCY:
        raise PriceDataError(
            f"{ticker}: expected exchange {EXCHANGE} and currency {CURRENCY}, "
            f"got exchange {exchange} and currency {currency}"
        )


def _parse_bar(ticker: str, value: Any) -> DailyBar:
    try:
        trading_date = date.fromisoformat(str(value["datetime"])[:10])
        volume = Decimal(str(value["volume"]))
        if volume != volume.to_integral_value():
            raise ValueError("volume is not a whole number")
        return DailyBar(
            trading_date=trading_date,
            open=Decimal(str(value["open"])),
            close=Decimal(str(value["close"])),
            volume=int(volume),
        )
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise PriceDataError(f"{ticker}: malformed bar ({type(exc).__name__})") from None
