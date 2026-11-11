"""`detect --date YYYY-MM-DD` (contracts/cli-contract.md).

Exit codes: 0 completed or reproduced, 1 unexpected error, 2 usage error, 3 re-run rejected
because the price data changed (FR-018), 4 input data unusable for the day (FR-022, FR-023).
Secrets are read from the environment only and scrubbed from every message (constitution IV).
"""

import argparse
import logging
import os
import sys
import traceback
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlsplit

from market_anomaly_agent import calendar
from market_anomaly_agent.data.source import PriceDataError, PriceDataSource
from market_anomaly_agent.data.twelvedata_source import TwelveDataPriceDataSource
from market_anomaly_agent.detection import RunAbortedError, run_detection, snapshots_match
from market_anomaly_agent.storage.db import ENV_VAR as DATABASE_URL_VAR
from market_anomaly_agent.storage.db import connect
from market_anomaly_agent.storage.repository import (
    StoredRun,
    load_bar_snapshot,
    load_run,
    load_universe_once,
    save_run,
)
from market_anomaly_agent.universe.constituents import (
    find_universe_file,
    read_universe_file,
    validate_universe,
)

API_KEY_VAR = "PRICE_DATA_API_KEY"
UNIVERSE_DIR = Path(__file__).parent / "universe"
EXPECTED_UNIVERSE_SIZE = 100  # rows in the committed Nasdaq-100 snapshot (FR-001, FR-002)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_REJECTED = 3
EXIT_UNUSABLE_INPUT = 4

log = logging.getLogger("market_anomaly_agent")


class _UsageError(Exception):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        self.print_usage(sys.stderr)
        raise _UsageError(message)


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a YYYY-MM-DD date") from None


def _secrets() -> list[str]:
    values = [os.environ.get(API_KEY_VAR, ""), os.environ.get(DATABASE_URL_VAR, "")]
    try:
        values.append(urlsplit(values[1]).password or "")
    except ValueError:
        pass
    # Longest first, so a password inside the URL does not leave the rest of the URL behind.
    return sorted((v for v in values if v), key=len, reverse=True)


def _scrub(text: str) -> str:
    for secret in _secrets():
        text = text.replace(secret, "***")
    return text


def load_universe_tickers() -> list[str]:
    """Read and validate the committed universe file (FR-001, FR-002), check it against the
    frozen table, loading it on the first run (FR-003), and return its tickers.

    Runs before any price data is fetched, so a bad or changed file fails fast.
    """
    constituents = read_universe_file(find_universe_file(UNIVERSE_DIR))
    validate_universe(constituents, expected_count=EXPECTED_UNIVERSE_SIZE)
    with connect() as conn:
        load_universe_once(conn, constituents)
    return [c.ticker for c in constituents]


def _summary(evaluated_date: date, flagged: int, evaluated: int, skipped: int, rule: str) -> str:
    return (
        f"evaluated_date={evaluated_date.isoformat()} status=completed flagged={flagged} "
        f"evaluated={evaluated} skipped={skipped} rule_version={rule}"
    )


def _stored_summary(run: StoredRun) -> str:
    return _summary(
        run.evaluated_date,
        run.flagged_count,
        run.evaluated_count,
        run.skipped_count,
        run.rule_version,
    )


class _StderrHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Writes to whatever sys.stderr is when main() runs."""


def _configure_logging() -> None:
    log.handlers = [h for h in log.handlers if not isinstance(h, _StderrHandler)]
    handler = _StderrHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def main(
    argv: Sequence[str] | None = None,
    *,
    source_factory: Callable[[str], PriceDataSource] | None = None,
) -> int:
    _configure_logging()
    parser = _ArgumentParser(prog="detect", description=__doc__.splitlines()[0])
    parser.add_argument("--date", required=True, type=_parse_date, help="evaluated trading day")
    try:
        args = parser.parse_args(argv)
    except _UsageError as exc:
        print(f"detect: error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    evaluated_date: date = args.date
    if not calendar.is_trading_day(evaluated_date):
        print(
            f"detect: error: {evaluated_date.isoformat()} is not a NASDAQ trading day",
            file=sys.stderr,
        )
        return EXIT_USAGE

    try:
        return _run(evaluated_date, source_factory or TwelveDataPriceDataSource)
    except RunAbortedError as exc:
        log.error("run for %s aborted, nothing stored: %s", evaluated_date, _scrub(str(exc)))
        return EXIT_UNUSABLE_INPUT
    except PriceDataError as exc:
        log.error("price data unavailable, nothing stored: %s", _scrub(str(exc)))
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 - contract: any other failure exits 1
        log.error("unexpected %s, nothing stored: %s", type(exc).__name__, _scrub(str(exc)))
        # Formatted here rather than via exc_info, so the traceback passes through _scrub too.
        log.error("traceback:\n%s", _scrub("".join(traceback.format_exception(exc))).rstrip())
        return EXIT_ERROR


def _run(evaluated_date: date, source_factory: Callable[[str], PriceDataSource]) -> int:
    api_key = os.environ.get(API_KEY_VAR)
    if not api_key:
        log.error("%s is not set", API_KEY_VAR)
        return EXIT_ERROR
    tickers = load_universe_tickers()
    result = run_detection(evaluated_date, tickers, source_factory(api_key), datetime.now(UTC))

    with connect() as conn:
        stored = load_run(conn, evaluated_date)
        if stored is not None:
            if not snapshots_match(load_bar_snapshot(conn, evaluated_date), result.bars):
                log.error(
                    "rejected: %s was already evaluated and the price data fetched now differs "
                    "from the snapshot stored by that run (FR-018); nothing written",
                    evaluated_date,
                )
                return EXIT_REJECTED
            log.info("%s already stored with the same price data; reproduced", evaluated_date)
            print(_stored_summary(stored))
            return EXIT_OK

        save_run(conn, result)
    print(
        _summary(
            evaluated_date,
            len(result.anomalies),
            len(result.evaluated),
            len(result.skipped),
            result.rule_version,
        )
    )
    return EXIT_OK
