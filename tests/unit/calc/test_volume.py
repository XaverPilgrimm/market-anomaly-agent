import pytest

from market_anomaly_agent.calc.volume import volume_ratio


def test_hand_checked_full_window() -> None:
    """20 days: 10 x 1,000 and 10 x 3,000 -> average 40,000 / 20 = 2,000.
    Evaluated day volume 5,000 -> ratio 5,000 / 2,000 = 2.5 (FR-011)."""
    result = volume_ratio(5000, [1000] * 10 + [3000] * 10)

    assert result.ratio == pytest.approx(2.5, abs=1e-15)
    assert result.unavailable_reason is None


def test_averages_over_available_days_only() -> None:
    """12 of 20 days present: 6 x 1,000 and 6 x 2,000 -> average 18,000 / 12 = 1,500.
    Evaluated day volume 3,000 -> ratio 2.0. The 8 missing days are ignored, not zeros."""
    window: list[int | None] = [1000] * 6 + [2000] * 6 + [None] * 8

    result = volume_ratio(3000, window)

    assert result.ratio == pytest.approx(2.0, abs=1e-15)
    assert result.unavailable_reason is None


def test_exactly_ten_days_still_computes() -> None:
    """10 of 20 days at 400 -> average 400; 1,000 / 400 = 2.5."""
    window: list[int | None] = [400] * 10 + [None] * 10

    result = volume_ratio(1000, window)

    assert result.ratio == pytest.approx(2.5, abs=1e-15)
    assert result.unavailable_reason is None


def test_nine_days_is_unavailable() -> None:
    window: list[int | None] = [400] * 9 + [None] * 11

    result = volume_ratio(1000, window)

    assert result.ratio is None
    assert result.unavailable_reason is not None
    assert "9 of 20" in result.unavailable_reason


def test_zero_average_is_unavailable() -> None:
    result = volume_ratio(1000, [0] * 20)

    assert result.ratio is None
    assert result.unavailable_reason is not None
