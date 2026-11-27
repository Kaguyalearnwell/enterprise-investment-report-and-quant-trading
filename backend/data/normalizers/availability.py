"""When may a fundamental value be used? (point-in-time rules, Stage 6.1 evidence)

Every rule returns the FIRST TRADING DAY on which a decision taken at that day's close may use
the value. Rules are conservative: if the exact publication time is unknown, the value becomes
usable on the trading day AFTER the deadline / collection date (it may have been published after
the close). Parameters live in config [fundamentals] and are research defaults until confirmed.
"""
from __future__ import annotations

import bisect
import datetime as dt


class Calendar:
    def __init__(self, trading_days: list[str]):
        self.days = sorted(trading_days)

    def next_after(self, date: str) -> str | None:
        """First trading day strictly after `date`."""
        i = bisect.bisect_right(self.days, date)
        return self.days[i] if i < len(self.days) else None

    def on_or_after(self, date: str) -> str | None:
        i = bisect.bisect_left(self.days, date)
        return self.days[i] if i < len(self.days) else None


def statement_deadline(period_end: str, cfg: dict) -> str:
    """Statutory filing deadline for a quarterly / annual statement ending on period_end."""
    y, m = int(period_end[:4]), int(period_end[5:7])
    q, a = cfg["quarterly_deadlines"], cfg["annual_deadline"]
    if m == 12:
        return f"{y + 1}-{a}"
    if m in (3, 6, 9):
        return f"{y}-{q[(m // 3) - 1]}"
    raise ValueError(f"unexpected statement period end {period_end}")


def statement_available(period_end: str, cal: Calendar, cfg: dict) -> tuple[str | None, str]:
    return cal.next_after(statement_deadline(period_end, cfg)), "statutory_deadline"


def revenue_available(revenue_month: str, create_time: str | None, cal: Calendar, cfg: dict) -> tuple[str | None, str]:
    """revenue_month 'YYYY-MM'. FinMind's create_time is a collection date (first values on 2026-04-21 are a back-fill)."""
    if create_time and create_time != "2026-04-21":
        return cal.next_after(create_time[:10]), "create_time"
    y, m = int(revenue_month[:4]), int(revenue_month[5:7])
    nxt = dt.date(y + (m == 12), m % 12 + 1, cfg["revenue_fallback_day"])
    return cal.next_after(nxt.isoformat()), "fallback_rule"


def announcement_available(date: str, time: str | None, cal: Calendar, cfg: dict) -> tuple[str | None, str]:
    if time and time[:5] < cfg["announcement_cutoff"] and cal.on_or_after(date) == date:
        return date, "announcement"
    return cal.next_after(date), "announcement"
