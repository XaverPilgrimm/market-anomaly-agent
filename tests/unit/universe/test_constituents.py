from datetime import date
from pathlib import Path

import pytest

from market_anomaly_agent.universe.constituents import (
    UniverseConstituent,
    UniverseFileError,
    find_universe_file,
    read_universe_file,
    validate_universe,
)

HEADER = "ticker,company_name,snapshot_date,source,share_class_note\n"
SOURCE = "https://www.invesco.com/qqq-holdings.csv"


def write_csv(path: Path, body: str, header: str = HEADER) -> Path:
    path.write_text(header + body, encoding="utf-8")
    return path


def test_reads_rows_sorted_by_ticker(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "nasdaq100_2026-10-28.csv",
        f"MSFT,Microsoft Corp,2026-10-28,{SOURCE},\n"
        f'GOOGL,Alphabet Inc Class A,2026-10-28,{SOURCE},"GOOGL kept, GOOG excluded"\n',
    )

    assert read_universe_file(path) == (
        UniverseConstituent(
            ticker="GOOGL",
            company_name="Alphabet Inc Class A",
            snapshot_date=date(2026, 10, 28),
            source=SOURCE,
            share_class_note="GOOGL kept, GOOG excluded",
        ),
        UniverseConstituent(
            ticker="MSFT",
            company_name="Microsoft Corp",
            snapshot_date=date(2026, 10, 28),
            source=SOURCE,
            share_class_note=None,
        ),
    )


def test_missing_column_raises(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "nasdaq100_2026-10-28.csv",
        f"MSFT,Microsoft Corp,2026-10-28,{SOURCE}\n",
        header="ticker,company_name,snapshot_date,source\n",
    )

    with pytest.raises(UniverseFileError, match="share_class_note"):
        read_universe_file(path)


def test_unparseable_date_raises(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "nasdaq100_2026-10-28.csv",
        f"MSFT,Microsoft Corp,28.10.2026,{SOURCE},\n",
    )

    with pytest.raises(UniverseFileError, match="28.10.2026"):
        read_universe_file(path)


def test_find_universe_file_returns_the_single_snapshot(tmp_path: Path) -> None:
    expected = write_csv(tmp_path / "nasdaq100_2026-10-28.csv", "")
    (tmp_path / "notes.csv").write_text("x\n", encoding="utf-8")

    assert find_universe_file(tmp_path) == expected


def test_find_universe_file_raises_when_none(tmp_path: Path) -> None:
    with pytest.raises(UniverseFileError):
        find_universe_file(tmp_path)


def test_find_universe_file_raises_when_several(tmp_path: Path) -> None:
    write_csv(tmp_path / "nasdaq100_2026-10-28.csv", "")
    write_csv(tmp_path / "nasdaq100_2026-11-02.csv", "")

    with pytest.raises(UniverseFileError):
        find_universe_file(tmp_path)


# --- validate_universe (T032, FR-001, FR-002) ---------------------------------------------------

SNAPSHOT = date(2026, 10, 28)
GOOGL_NOTE = "GOOGL kept, GOOG excluded"


def constituent(
    ticker: str,
    snapshot_date: date = SNAPSHOT,
    source: str = SOURCE,
    share_class_note: str | None = None,
) -> UniverseConstituent:
    return UniverseConstituent(
        ticker=ticker,
        company_name=f"{ticker} Inc",
        snapshot_date=snapshot_date,
        source=source,
        share_class_note=share_class_note,
    )


def valid_universe(size: int = 5) -> list[UniverseConstituent]:
    """GOOGL with its note plus `size - 1` other tickers."""
    others = [constituent(f"T{i:03d}") for i in range(size - 1)]
    return sorted(
        [constituent("GOOGL", share_class_note=GOOGL_NOTE), *others], key=lambda c: c.ticker
    )


def test_valid_small_fixture_passes() -> None:
    universe = valid_universe()

    validate_universe(universe, expected_count=len(universe))


def test_valid_100_row_universe_passes_with_the_default_count() -> None:
    validate_universe(valid_universe(100))


@pytest.mark.parametrize("size", [99, 101])
def test_row_count_other_than_100_raises(size: int) -> None:
    with pytest.raises(UniverseFileError, match=str(size)):
        validate_universe(valid_universe(size))


def test_more_than_one_snapshot_date_raises() -> None:
    universe = valid_universe()
    universe[-1] = constituent(universe[-1].ticker, snapshot_date=date(2026, 11, 2))

    with pytest.raises(UniverseFileError, match="snapshot_date"):
        validate_universe(universe, expected_count=len(universe))


@pytest.mark.parametrize("source", ["", "   "])
def test_empty_source_raises(source: str) -> None:
    universe = valid_universe()
    universe[-1] = constituent(universe[-1].ticker, source=source)

    with pytest.raises(UniverseFileError, match=universe[-1].ticker):
        validate_universe(universe, expected_count=len(universe))


def test_repeated_ticker_raises() -> None:
    universe = valid_universe()
    universe.append(constituent(universe[-1].ticker))

    with pytest.raises(UniverseFileError, match=universe[-1].ticker):
        validate_universe(universe, expected_count=len(universe))


def test_goog_present_raises() -> None:
    universe = [*valid_universe(), constituent("GOOG")]

    with pytest.raises(UniverseFileError, match="GOOG"):
        validate_universe(universe, expected_count=len(universe))


@pytest.mark.parametrize("note", [None, ""])
def test_googl_without_share_class_note_raises(note: str | None) -> None:
    universe = [c for c in valid_universe() if c.ticker != "GOOGL"]
    universe.append(constituent("GOOGL", share_class_note=note))

    with pytest.raises(UniverseFileError, match="GOOGL"):
        validate_universe(universe, expected_count=len(universe))


def test_universe_without_googl_raises() -> None:
    """FR-002: Alphabet stays in the universe as GOOGL."""
    universe = [c for c in valid_universe(6) if c.ticker != "GOOGL"]

    with pytest.raises(UniverseFileError, match="GOOGL"):
        validate_universe(universe, expected_count=len(universe))
