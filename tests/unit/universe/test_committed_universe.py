"""The committed universe file (T036) must keep passing the frozen-universe rules (FR-001 to
FR-003), so any later edit to it fails the unit suite."""

from market_anomaly_agent import cli
from market_anomaly_agent.universe.constituents import (
    find_universe_file,
    read_universe_file,
    validate_universe,
)


def test_committed_universe_file_is_valid() -> None:
    """Read from the directory the CLI reads, so this checks the file `detect` actually uses."""
    constituents = read_universe_file(find_universe_file(cli.UNIVERSE_DIR))

    validate_universe(constituents, expected_count=100)
