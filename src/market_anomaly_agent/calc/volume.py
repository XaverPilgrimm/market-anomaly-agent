"""Volume ratio for flagged cases (FR-011, Clarification Q3)."""

from collections.abc import Sequence
from dataclasses import dataclass

from market_anomaly_agent.rules import MIN_VOLUME_DAYS


@dataclass(frozen=True)
class VolumeRatioResult:
    ratio: float | None
    unavailable_reason: str | None


def volume_ratio(evaluated_volume: int, window_volumes: Sequence[int | None]) -> VolumeRatioResult:
    """Evaluated day's volume over the average of the days in the window that have volume data.

    `None` entries are days without volume data; they are left out of the average, not counted
    as zero. With fewer than MIN_VOLUME_DAYS present, or a zero average, the ratio is unavailable.
    """
    present = [v for v in window_volumes if v is not None]
    if len(present) < MIN_VOLUME_DAYS:
        return VolumeRatioResult(
            ratio=None,
            unavailable_reason=(
                f"only {len(present)} of {len(window_volumes)} trading days have volume data "
                f"(need {MIN_VOLUME_DAYS})"
            ),
        )
    average = sum(present) / len(present)
    if average == 0:
        return VolumeRatioResult(ratio=None, unavailable_reason="average volume is 0")
    return VolumeRatioResult(ratio=evaluated_volume / average, unavailable_reason=None)
