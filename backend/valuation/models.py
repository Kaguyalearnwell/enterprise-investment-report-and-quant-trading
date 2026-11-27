"""Valuation tools (Prompt 01 §7, Prompt 04 §7). Pure functions; ASSUMPTIONS ARE INPUTS.

Nothing here chooses a growth rate, margin or WACC. Every assumption must be supplied by the
research process together with its source, and stored with the result (Prompt 04 §13).
Units: currency amounts in the same unit throughout (e.g. NT$), rates as decimals.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from scipy.optimize import brentq


@dataclass(frozen=True)
class DCFInput:
    revenue0: float                     # last actual (TTM) revenue
    growth: Sequence[float]             # revenue growth per forecast year
    operating_margin: Sequence[float]   # EBIT / revenue per forecast year
    tax_rate: float
    reinvestment_rate: Sequence[float]  # (capex - D&A + change in working capital) / revenue per year
    wacc: float
    terminal_growth: float
    net_cash: float                     # cash minus debt (negative = net debt)
    shares: float                       # diluted shares
    sources: dict = field(default_factory=dict)   # where every assumption came from

    def validate(self) -> None:
        n = len(self.growth)
        if not (len(self.operating_margin) == len(self.reinvestment_rate) == n and n >= 1):
            raise ValueError("growth, operating_margin and reinvestment_rate need the same length >= 1")
        if self.wacc <= self.terminal_growth:
            raise ValueError("WACC must exceed the terminal growth rate")
        if self.shares <= 0:
            raise ValueError("shares must be positive")


def dcf(x: DCFInput) -> dict:
    """FCFF = EBIT (1 - t) - reinvestment; Gordon terminal value on the year after the last forecast year."""
    x.validate()
    rev, pv, rows = x.revenue0, 0.0, []
    for i, (g, m, rr) in enumerate(zip(x.growth, x.operating_margin, x.reinvestment_rate), start=1):
        rev *= 1 + g
        fcff = rev * m * (1 - x.tax_rate) - rev * rr
        df = (1 + x.wacc) ** -i
        pv += fcff * df
        rows.append({"year": i, "revenue": rev, "fcff": fcff, "discount_factor": df, "pv": fcff * df})
    n = len(x.growth)
    fcff_next = rows[-1]["fcff"] * (1 + x.terminal_growth)
    tv = fcff_next / (x.wacc - x.terminal_growth)
    pv_tv = tv * (1 + x.wacc) ** -n
    ev = pv + pv_tv
    equity = ev + x.net_cash
    return {"enterprise_value": ev, "equity_value": equity, "value_per_share": equity / x.shares,
            "pv_forecast": pv, "pv_terminal": pv_tv, "terminal_share_of_ev": pv_tv / ev if ev else math.nan,
            "years": rows, "assumptions": x.__dict__}


def reverse_dcf_growth(price: float, x: DCFInput, lo: float = -0.5, hi: float = 1.0) -> float:
    """Constant revenue growth over the forecast years that makes the DCF value equal `price`."""
    n = len(x.growth)

    def f(g):
        y = DCFInput(**{**x.__dict__, "growth": [g] * n})
        return dcf(y)["value_per_share"] - price

    if f(lo) * f(hi) > 0:
        return math.nan                      # no growth rate in [lo, hi] justifies the price
    return brentq(f, lo, hi, xtol=1e-10)


def reverse_dcf_margin(price: float, x: DCFInput, lo: float = -0.5, hi: float = 0.9) -> float:
    """Constant operating margin that makes the DCF value equal `price`."""
    n = len(x.growth)

    def f(m):
        y = DCFInput(**{**x.__dict__, "operating_margin": [m] * n})
        return dcf(y)["value_per_share"] - price

    if f(lo) * f(hi) > 0:
        return math.nan
    return brentq(f, lo, hi, xtol=1e-10)


def scenario_value(scenarios: dict[str, tuple[float, float]]) -> dict:
    """{'bear': (value, probability), ...} -> probability-weighted value. Probabilities must sum to 1."""
    p = sum(prob for _, prob in scenarios.values())
    if abs(p - 1) > 1e-9:
        raise ValueError(f"scenario probabilities sum to {p}, not 1")
    ev = sum(v * prob for v, prob in scenarios.values())
    return {"weighted_value": ev, "scenarios": scenarios}


def margin_of_safety(fair_value: float, price: float) -> dict:
    return {"discount_to_fair_value": 1 - price / fair_value if fair_value else math.nan,
            "upside": fair_value / price - 1 if price else math.nan}


def historical_percentile(history: Sequence[float], current: float, min_years: float = 3.0,
                          obs_per_year: int = 252) -> float | None:
    """Percentile (0-1) of `current` within its own history; None when history is too short.
    Prompt 01 §7.1: never invent a percentile without enough history."""
    h = [v for v in history if v is not None and not math.isnan(v) and v > 0]
    if len(h) < min_years * obs_per_year:
        return None
    return sum(1 for v in h if v <= current) / len(h)
