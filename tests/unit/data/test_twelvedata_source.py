import itertools
import logging
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
import requests

from market_anomaly_agent.data.source import DailyBar, PriceDataError, PriceDataUnavailable
from market_anomaly_agent.data.twelvedata_source import TwelveDataPriceDataSource

API_KEY = "secret-test-key-123"


class FakeResponse:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        return self._body


class FakeTime:
    """Fake clock and sleep: sleeping advances the clock, so no test really waits."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeSession:
    """Returns (or raises) the queued outcomes in order and records every call."""

    def __init__(self, outcomes: list[FakeResponse | Exception], time: FakeTime) -> None:
        self.outcomes = outcomes
        self.time = time
        self.calls: list[dict[str, Any]] = []

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, str],
        headers: Mapping[str, str],
        timeout: float,
    ) -> FakeResponse:
        self.calls.append(
            {"url": url, "params": dict(params), "headers": dict(headers), "at": self.time.now}
        )
        outcome = self.outcomes.pop(0) if self.outcomes else ok_response([])
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


NASDAQ_META = {
    "symbol": "AAPL",
    "interval": "1day",
    "currency": "USD",
    "exchange_timezone": "America/New_York",
    "exchange": "NASDAQ",
    "mic_code": "XNGS",
    "type": "Common Stock",
}


def ok_response(
    values: list[dict[str, str]], meta: dict[str, str] | None = NASDAQ_META
) -> FakeResponse:
    body: dict[str, Any] = {"status": "ok", "values": values}
    if meta is not None:
        body["meta"] = meta
    return FakeResponse(200, body)


def bar_json(day: str, open_: str = "101.25", close: str = "102.50") -> dict[str, str]:
    return {
        "datetime": day,
        "open": open_,
        "high": "103.00",
        "low": "100.00",
        "close": close,
        "volume": "1234567",
    }


def make_source(
    outcomes: list[FakeResponse | Exception],
) -> tuple[TwelveDataPriceDataSource, FakeSession, FakeTime]:
    time = FakeTime()
    session = FakeSession(outcomes, time)
    source = TwelveDataPriceDataSource(API_KEY, session=session, sleep=time.sleep, clock=time.clock)
    return source, session, time


def test_request_parameters_and_auth_header() -> None:
    source, session, _ = make_source([ok_response([])])

    source.get_daily_bars("AAPL", date(2026, 5, 1), date(2026, 10, 27))

    call = session.calls[0]
    assert call["url"] == "https://api.twelvedata.com/time_series"
    assert call["params"] == {
        "symbol": "AAPL",
        "interval": "1day",
        "adjust": "all",
        "start_date": "2026-05-01",
        "end_date": "2026-10-28",  # one day after the requested end
        "order": "asc",
        "timezone": "America/New_York",
        "outputsize": "5000",
        "exchange": "NASDAQ",
    }
    assert call["headers"] == {"Authorization": f"apikey {API_KEY}"}
    assert API_KEY not in call["url"]
    assert API_KEY not in str(call["params"])


def test_parses_bar_into_decimal_daily_bar() -> None:
    source, _, _ = make_source([ok_response([bar_json("2026-10-27")])])

    bars = source.get_daily_bars("AAPL", date(2026, 10, 27), date(2026, 10, 27))

    assert bars == [
        DailyBar(
            trading_date=date(2026, 10, 27),
            open=Decimal("101.25"),
            close=Decimal("102.50"),
            volume=1234567,
        )
    ]


def test_drops_bars_outside_range_and_sorts_ascending() -> None:
    values = [
        bar_json("2026-10-28"),
        bar_json("2026-10-27"),
        bar_json("2026-10-23"),
        bar_json("2026-10-22"),
    ]
    source, _, _ = make_source([ok_response(values)])

    bars = source.get_daily_bars("AAPL", date(2026, 10, 23), date(2026, 10, 27))

    assert [b.trading_date for b in bars] == [date(2026, 10, 23), date(2026, 10, 27)]


def without(key: str) -> dict[str, str]:
    return {k: v for k, v in NASDAQ_META.items() if k != key}


@pytest.mark.parametrize(
    "meta, got",
    [
        ({**NASDAQ_META, "exchange": "NYSE", "mic_code": "XNYS"}, "exchange NYSE and currency USD"),
        ({**NASDAQ_META, "exchange": "XETR", "currency": "EUR"}, "exchange XETR and currency EUR"),
        ({**NASDAQ_META, "currency": "EUR"}, "exchange NASDAQ and currency EUR"),
        (without("exchange"), "exchange None and currency USD"),
        (without("currency"), "exchange NASDAQ and currency None"),
        (None, "exchange None and currency None"),
    ],
    ids=[
        "other-exchange",
        "foreign-listing",
        "other-currency",
        "no-exchange",
        "no-currency",
        "no-meta",
    ],
)
def test_response_for_another_listing_raises_without_bars_or_retry(
    meta: dict[str, str] | None, got: str
) -> None:
    source, session, _ = make_source([ok_response([bar_json("2026-10-27")], meta=meta)])

    with pytest.raises(PriceDataError) as excinfo:
        source.get_daily_bars("AAPL", date(2026, 10, 27), date(2026, 10, 27))

    assert str(excinfo.value) == (f"AAPL: expected exchange NASDAQ and currency USD, got {got}")
    assert len(session.calls) == 1


def test_matching_meta_with_extra_fields_parses_bars() -> None:
    meta = {**NASDAQ_META, "symbol": "QQQ", "mic_code": "XNMS", "type": "ETF"}
    source, _, _ = make_source([ok_response([bar_json("2026-10-27")], meta=meta)])

    bars = source.get_daily_bars("QQQ", date(2026, 10, 27), date(2026, 10, 27))

    assert [b.trading_date for b in bars] == [date(2026, 10, 27)]


def test_error_body_404_no_matching_data_raises_unavailable() -> None:
    body = {"status": "error", "code": 404, "message": "no data matching the request"}
    source, _, _ = make_source([FakeResponse(200, body)])

    with pytest.raises(PriceDataUnavailable):
        source.get_daily_bars("NOPE", date(2026, 10, 1), date(2026, 10, 27))


def test_error_body_400_invalid_parameter_raises_error_without_retry() -> None:
    body = {"status": "error", "code": 400, "message": "invalid parameter"}
    source, session, _ = make_source([FakeResponse(200, body)])

    with pytest.raises(PriceDataError, match="400") as excinfo:
        source.get_daily_bars("AAPL", date(2026, 10, 1), date(2026, 10, 27))

    assert not isinstance(excinfo.value, PriceDataUnavailable)
    assert "AAPL" in str(excinfo.value)
    assert len(session.calls) == 1


def test_http_429_is_retried_after_60_seconds() -> None:
    source, session, time = make_source(
        [FakeResponse(429, {}), ok_response([bar_json("2026-10-27")])]
    )

    bars = source.get_daily_bars("AAPL", date(2026, 10, 27), date(2026, 10, 27))

    assert len(bars) == 1
    assert len(session.calls) == 2
    assert 60.0 in time.sleeps


def test_error_body_code_429_is_retried_after_60_seconds() -> None:
    limited = FakeResponse(200, {"status": "error", "code": 429, "message": "rate limit"})
    source, session, time = make_source([limited, ok_response([])])

    source.get_daily_bars("AAPL", date(2026, 10, 27), date(2026, 10, 27))

    assert len(session.calls) == 2
    assert 60.0 in time.sleeps


def test_server_errors_and_timeouts_back_off_2_4_8_then_raise() -> None:
    outcomes: list[FakeResponse | Exception] = [
        FakeResponse(500, {}),
        requests.Timeout("read timed out"),
        FakeResponse(503, {}),
        FakeResponse(502, {}),
    ]
    source, session, time = make_source(outcomes)

    with pytest.raises(PriceDataError):
        source.get_daily_bars("AAPL", date(2026, 10, 27), date(2026, 10, 27))

    assert len(session.calls) == 4  # first attempt + 3 retries
    backoffs = [s for s in time.sleeps if s in (2.0, 4.0, 8.0)]
    assert backoffs == [2.0, 4.0, 8.0]


def test_requests_are_spaced_at_least_7_5_seconds_apart() -> None:
    source, session, _ = make_source([ok_response([]), ok_response([]), ok_response([])])

    for ticker in ("AAPL", "MSFT", "NVDA"):
        source.get_daily_bars(ticker, date(2026, 10, 27), date(2026, 10, 27))

    times = [call["at"] for call in session.calls]
    assert all(later - earlier >= 7.5 for earlier, later in itertools.pairwise(times))


def test_801st_request_raises_without_calling_session() -> None:
    source, session, _ = make_source([])

    for _ in range(800):
        source.get_daily_bars("AAPL", date(2026, 10, 27), date(2026, 10, 27))
    with pytest.raises(PriceDataError):
        source.get_daily_bars("AAPL", date(2026, 10, 27), date(2026, 10, 27))

    assert len(session.calls) == 800


def test_api_key_never_appears_in_errors_or_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    echoed = {"status": "error", "code": 401, "message": f"bad key {API_KEY}"}
    source, _, _ = make_source(
        [FakeResponse(500, {}), FakeResponse(429, {}), FakeResponse(200, echoed)]
    )

    with pytest.raises(PriceDataError) as excinfo:
        source.get_daily_bars("AAPL", date(2026, 10, 27), date(2026, 10, 27))

    assert API_KEY not in str(excinfo.value)
    assert all(API_KEY not in record.getMessage() for record in caplog.records)


def test_every_wait_before_a_retry_is_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    """T053 (4): the reason and the wait in seconds, never the API key."""
    caplog.set_level(logging.DEBUG)
    limited = FakeResponse(200, {"status": "error", "code": 429, "message": "rate limit"})
    source, _, _ = make_source(
        [FakeResponse(503, {}), requests.Timeout("read timed out"), limited, ok_response([])]
    )

    source.get_daily_bars("AAPL", date(2026, 10, 27), date(2026, 10, 27))

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == [
        "AAPL: HTTP 503, retrying in 2 s",
        "AAPL: Timeout, retrying in 4 s",
        "AAPL: rate limited, retrying in 60 s",
    ]
    assert all(API_KEY not in record.getMessage() for record in caplog.records)
