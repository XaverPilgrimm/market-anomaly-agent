"""Flagging threshold and the daily cap (FR-009, FR-010)."""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class CapResult:
    kept: list[tuple[str, int]]  # (ticker, rank), ranks 1..cap
    cap_exceeded: list[tuple[str, int]]  # (ticker, rank), ranks cap+1..


def apply_daily_cap(scores: Mapping[str, float], threshold: float, cap: int) -> CapResult:
    """Rank tickers with |z| >= threshold by descending |z|, ties broken alphabetically by
    ticker, and keep the first `cap`; the rest keep their rank but exceed the cap."""
    qualifying = [(ticker, z) for ticker, z in scores.items() if abs(z) >= threshold]
    ranked = sorted(qualifying, key=lambda item: (-abs(item[1]), item[0]))
    ranks = [(ticker, rank) for rank, (ticker, _) in enumerate(ranked, start=1)]
    return CapResult(kept=ranks[:cap], cap_exceeded=ranks[cap:])
