"""Stage 6.2 point-in-time availability rules. Dates are real calendar dates; values are illustrative."""
import pytest

from backend.config.strategy_config import load_config
from backend.data.normalizers.availability import (Calendar, announcement_available, revenue_available,
                                                   statement_available, statement_deadline)

CFG = load_config()["fundamentals"]
# 2024-05: 15th is a Wednesday; 2026-07: 13th Monday, 14th Tuesday
CAL = Calendar(["2024-05-14", "2024-05-15", "2024-05-16", "2024-05-17", "2025-03-31", "2025-04-01",
                "2026-07-10", "2026-07-13", "2026-07-14", "2024-02-20", "2024-02-21", "2019-11-14", "2019-11-15"])


def test_statement_deadlines():
    assert statement_deadline("2024-03-31", CFG) == "2024-05-15"
    assert statement_deadline("2024-09-30", CFG) == "2024-11-14"
    assert statement_deadline("2024-12-31", CFG) == "2025-03-31"
    with pytest.raises(ValueError):
        statement_deadline("2024-04-30", CFG)


def test_statement_usable_the_trading_day_after_the_deadline():
    assert statement_available("2024-03-31", CAL, CFG) == ("2024-05-16", "statutory_deadline")
    assert statement_available("2024-12-31", CAL, CFG)[0] == "2025-04-01"


def test_revenue_create_time_and_fallback():
    # REAL observation: 2330 June-2026 revenue collected 2026-07-13 -> usable 07-14, not 07-11
    assert revenue_available("2026-06", "2026-07-13", CAL, CFG) == ("2026-07-14", "create_time")
    # history without create_time: after the 20th of the next month
    assert revenue_available("2024-01", "", CAL, CFG) == ("2024-02-21", "fallback_rule")
    # the 2026-04-21 create_time is FinMind's back-fill marker, not a collection date
    assert revenue_available("2024-01", "2026-04-21", CAL, CFG)[1] == "fallback_rule"


def test_announcement_after_close_is_next_day():
    # REAL: 2330 dividend announcement 2019-11-14 17:01:07 (after the close) -> 2019-11-15
    assert announcement_available("2019-11-14", "17:01:07", CAL, CFG) == ("2019-11-15", "announcement")
    assert announcement_available("2019-11-14", "10:00:00", CAL, CFG) == ("2019-11-14", "announcement")
