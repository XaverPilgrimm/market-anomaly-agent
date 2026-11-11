from datetime import date
from decimal import Decimal

import pytest

from market_anomaly_agent.calc.returns import simple_returns

MON, TUE, WED, THU = date(2026, 10, 26), date(2026, 10, 27), date(2026, 10, 28), date(2026, 10, 29)


def test_hand_checked_returns() -> None:
    """Closes 100 -> 102 -> 99.96 (FR-005: (close_t / close_prev) - 1).

    TUE: 102 / 100 - 1 = 0.02
    WED: 99.96 / 102 - 1 = 0.98 - 1 = -0.02
    MON is the first day in the sequence, so it has no return.
    """
    closes = {MON: Decimal(100), TUE: Decimal(102), WED: Decimal("99.96")}

    returns = simple_returns(closes, [MON, TUE, WED])

    assert returns.keys() == {TUE, WED}
    assert returns[TUE] == pytest.approx(0.02, abs=1e-15)
    assert returns[WED] == pytest.approx(-0.02, abs=1e-15)


def test_no_return_across_a_gap() -> None:
    """TUE has no close: TUE has no return, and WED has none because its previous trading day
    (TUE) has no close. THU's previous trading day (WED) has a close, so THU has a return."""
    closes = {MON: Decimal(100), WED: Decimal(104), THU: Decimal("106.08")}

    returns = simple_returns(closes, [MON, TUE, WED, THU])

    assert returns.keys() == {THU}
    assert returns[THU] == pytest.approx(0.02, abs=1e-15)  # 106.08 / 104 - 1


def test_first_day_has_no_return() -> None:
    returns = simple_returns({MON: Decimal(100)}, [MON])

    assert returns == {}
