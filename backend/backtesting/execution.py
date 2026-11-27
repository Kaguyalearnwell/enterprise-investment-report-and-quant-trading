"""Simulated execution rules (Taiwan cash equities). Pure functions, no state.

All prices here are RAW (unadjusted) prices: fills, ticks, limits, fees and taxes are
computed on what could actually be traded at the time (Phase 1 §6, Prompt 04 §8).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from backend.data.normalizers.price_limits import tick_size

EPS = 1e-9


def round_to_tick(price: float, side: str) -> float:
    """Buy fills round UP to the tick, sell fills round DOWN (the conservative direction)."""
    p = Decimal(str(price))
    t = tick_size(p)
    mode = ROUND_CEILING if side == "buy" else ROUND_FLOOR
    return float((p / t).to_integral_value(rounding=mode) * t)


@dataclass(frozen=True)
class CostModel:
    commission_rate: float = 0.001425
    commission_discount: float = 0.6
    min_commission: float = 20.0
    sell_tax_rate: float = 0.003
    slippage_bps: float = 5.0
    odd_lot_extra_bps: float = 10.0

    def commission(self, value: float) -> float:
        if value <= 0:
            return 0.0
        return max(self.min_commission, value * self.commission_rate * self.commission_discount)

    def tax(self, value: float) -> float:
        return value * self.sell_tax_rate

    def fill_price(self, reference: float, side: str, odd_lot: bool = False) -> float:
        bps = self.slippage_bps + (self.odd_lot_extra_bps if odd_lot else 0.0)
        raw = reference * (1 + bps / 1e4) if side == "buy" else reference * (1 - bps / 1e4)
        return round_to_tick(raw, side)


def lot_shares(shares: float, date: str, whole_lots_until: str, board_lot: int = 1000) -> int:
    """Board lots only before intraday odd-lot trading existed; single shares afterwards."""
    n = int(math.floor(shares + EPS))
    if date < whole_lots_until:
        return (n // board_lot) * board_lot
    return max(n, 0)


def is_locked(high: float, low: float, limit: float | None) -> bool:
    """One-price day at the limit (一價到底): no fill possible on the opposite side."""
    return limit is not None and abs(high - low) < EPS and abs(high - limit) < EPS


def entry_check(open_: float, limit_up: float | None, signal_close: float, signal_atr: float,
                max_gap_atr: float) -> str | None:
    """Reason the next-open entry is NOT filled, or None if it can be filled."""
    if limit_up is not None and open_ >= limit_up - EPS:
        return "limit_up_open"
    if open_ > signal_close + max_gap_atr * signal_atr + EPS:
        return "gap_above_chase_limit"
    return None


def stop_fill(open_: float, high: float, low: float, stop: float, limit_down: float | None) -> tuple[str, float] | None:
    """Stop-market order for the day. Returns ('gap' | 'touch', reference price) or None.

    * open at or below the stop -> filled at the OPEN (a gap through the stop loses more than 1R);
    * otherwise low touches the stop -> filled at the stop;
    * a one-price limit-down day cannot be sold: returns ('locked', nan) so the caller defers.
    """
    if low > stop + EPS:
        return None
    if is_locked(high, low, limit_down):
        return ("locked", float("nan"))
    if open_ <= stop + EPS:
        return ("gap", open_)
    return ("touch", stop)


def size_position(*, equity: float, cash: float, price: float, stop: float, market_value: float,
                  industry_value: float, open_risk: float, risk_pct: float, max_weight: float,
                  max_industry: float, max_exposure: float, max_open_risk: float,
                  cost: CostModel) -> tuple[float, str]:
    """Shares before lot rounding and the binding constraint (Phase 1 §5)."""
    per_share_risk = price - stop
    if per_share_risk <= EPS or price <= EPS:
        return 0.0, "invalid_stop"
    caps = {
        "risk_budget": equity * risk_pct / per_share_risk,
        "max_weight": equity * max_weight / price,
        "max_industry": max(equity * max_industry - industry_value, 0.0) / price,
        "max_exposure": max(equity * max_exposure - market_value, 0.0) / price,
        "max_open_risk": max(equity * max_open_risk - open_risk, 0.0) / per_share_risk,
        "cash": max(cash - cost.min_commission, 0.0) / (price * (1 + cost.commission_rate * cost.commission_discount)),
    }
    binding = min(caps, key=caps.get)
    return caps[binding], binding
