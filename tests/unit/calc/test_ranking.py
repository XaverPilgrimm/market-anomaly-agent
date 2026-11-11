from market_anomaly_agent.calc.ranking import apply_daily_cap


def test_keeps_ten_largest_and_ranks_the_rest() -> None:
    """14 qualifying stocks (spec US1 scenario 2): T14 has the largest |z|, T01 the smallest."""
    scores = {f"T{i:02d}": 2.0 + i / 10 for i in range(1, 15)}
    scores["LOW"] = 1.5  # does not qualify

    result = apply_daily_cap(scores, threshold=2.0, cap=10)

    assert result.kept == [(f"T{i:02d}", 15 - i) for i in range(14, 4, -1)]
    assert result.cap_exceeded == [("T04", 11), ("T03", 12), ("T02", 13), ("T01", 14)]


def test_threshold_is_inclusive() -> None:
    result = apply_daily_cap({"AT": 2.0, "BELOW": 1.999}, threshold=2.0, cap=10)

    assert result.kept == [("AT", 1)]
    assert result.cap_exceeded == []


def test_negative_scores_rank_by_absolute_value() -> None:
    result = apply_daily_cap({"UP": 3.0, "DOWN": -4.0, "FLAT": -1.0}, threshold=2.0, cap=10)

    assert result.kept == [("DOWN", 1), ("UP", 2)]


def test_tie_at_the_cap_keeps_alphabetically_earlier_ticker() -> None:
    """Ranks 1-9 are clear; ZZZ and AAA tie on |z| = 2.5 for rank 10 (FR-010)."""
    scores = {f"T{i}": 11.0 - i for i in range(9)}  # 11.0 down to 3.0, all above 2.5
    scores["ZZZ"] = 2.5
    scores["AAA"] = -2.5

    result = apply_daily_cap(scores, threshold=2.0, cap=10)

    assert ("AAA", 10) in result.kept
    assert result.cap_exceeded == [("ZZZ", 11)]


def test_same_input_same_output() -> None:
    scores = {"B": 3.0, "A": 3.0, "C": -3.0, "D": 2.1}

    assert apply_daily_cap(scores, 2.0, 2) == apply_daily_cap(
        dict(reversed(scores.items())), 2.0, 2
    )
