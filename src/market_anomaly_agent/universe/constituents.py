"""Reads the committed, frozen Nasdaq-100 universe snapshot file (FR-001)."""

import csv
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
