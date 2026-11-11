"""Reads the committed, frozen Nasdaq-100 universe snapshot file (FR-001)."""

import csv
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

COLUMNS = ("ticker", "company_name", "snapshot_date", "source", "share_class_note")
FILE_PATTERN = "nasdaq100_*.csv"


@dataclass(frozen=True)
class UniverseConstituent:
    ticker: str
    company_name: str
    snapshot_date: date
    source: str
    share_class_note: str | None


class UniverseFileError(Exception):
    """The universe snapshot file is missing, ambiguous or malformed."""


def find_universe_file(directory: Path) -> Path:
    matches = sorted(directory.glob(FILE_PATTERN))
    if len(matches) != 1:
        raise UniverseFileError(
            f"expected exactly one {FILE_PATTERN} in {directory}, found {len(matches)}"
        )
    return matches[0]


def read_universe_file(path: Path) -> tuple[UniverseConstituent, ...]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise UniverseFileError(f"{path.name}: missing column(s) {', '.join(missing)}")
        constituents = [_parse_row(path, line, row) for line, row in enumerate(reader, start=2)]
    return tuple(sorted(constituents, key=lambda c: c.ticker))


def _parse_row(path: Path, line: int, row: dict[str, str]) -> UniverseConstituent:
    raw_date = row["snapshot_date"].strip()
    try:
        snapshot_date = date.fromisoformat(raw_date)
    except ValueError:
        raise UniverseFileError(
            f"{path.name} line {line}: snapshot_date {raw_date!r} is not YYYY-MM-DD"
        ) from None
    note = (row["share_class_note"] or "").strip()
    return UniverseConstituent(
        ticker=row["ticker"].strip(),
        company_name=row["company_name"].strip(),
        snapshot_date=snapshot_date,
        source=row["source"].strip(),
        share_class_note=note or None,
    )


def validate_universe(
    constituents: Sequence[UniverseConstituent], expected_count: int = 100
) -> None:
    """Raise UniverseFileError unless the universe is one documented snapshot (FR-001) with
    exactly one share class per company, GOOGL kept and GOOG excluded (FR-002)."""
    if len(constituents) != expected_count:
        raise UniverseFileError(f"universe has {len(constituents)} rows, expected {expected_count}")
    dates = sorted({c.snapshot_date.isoformat() for c in constituents})
    if len(dates) > 1:
        raise UniverseFileError(f"rows have more than one snapshot_date: {', '.join(dates)}")
    no_source = [c.ticker for c in constituents if not c.source.strip()]
    if no_source:
        raise UniverseFileError(f"rows without a source: {', '.join(no_source)}")
    repeated = sorted(t for t, n in Counter(c.ticker for c in constituents).items() if n > 1)
    if repeated:
        raise UniverseFileError(f"tickers repeat: {', '.join(repeated)}")

    by_ticker = {c.ticker: c for c in constituents}
    if "GOOG" in by_ticker:
        raise UniverseFileError("GOOG must be excluded; Alphabet is kept as GOOGL (FR-002)")
    googl = by_ticker.get("GOOGL")
    if googl is None:
        raise UniverseFileError("GOOGL is missing; Alphabet is kept as GOOGL (FR-002)")
    if not (googl.share_class_note or "").strip():
        raise UniverseFileError("GOOGL needs a share_class_note naming the excluded class (FR-002)")
