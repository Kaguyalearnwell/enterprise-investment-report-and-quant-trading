"""Event-driven portfolio simulator (historical simulation only; no real orders).

Daily order of events for trading day d (docs/phase1_math_spec.md §4):

  0. ex-date corporate actions on held positions (cash dividends credited; splits,
     par-value changes, stock dividends, capital reductions rescale shares at the
     official reference price, preserving value);
  1. OPEN  - exits decided at the previous close (trend break, time stop, deferred stops);
             a one-price limit-down day cannot be sold -> deferred to the next day;
  2. OPEN  - entries from the previous close's signals: no fill if the open is at
             limit-up or above close + max_gap_atr * ATR; size from the previous
             close's equity; whole lots before 2020-10-26;
  3. INTRADAY - stop-market orders for every position (also the ones opened today);
             a gap through the stop fills at the open;
  4. CLOSE - update the highest close and the trailing stop (only up), evaluate the
             trend and time exits for tomorrow, collect today's signals for tomorrow.

Nothing in step k uses information that is only known after step k.
Trailing basis 'adjusted' (user decision C1): highest close, ATR and the stop level live in
adjusted prices and are converted to raw prices with that day's price factor, so an
ex-dividend drop does not trigger the stop. Basis 'raw' is the comparison variant: cash
dividends are NOT adjusted (share-count events still are, otherwise a split would stop out).
"""
from __future__ import annotations

import logging
import math
import zlib
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from backend.backtesting.execution import CostModel, entry_check, is_locked, lot_shares, size_position, stop_fill

log = logging.getLogger(__name__)
SHARE_EVENTS = {"split", "reverse_split", "par_value_change", "stock_dividend", "capital_reduction"}
CASH_EVENTS = {"cash_dividend", "cash_and_stock_dividend"}


@dataclass
class Bar:
    open: float
    high: float
    low: float
    close: float
    priced: bool
    pf: float              # adj / raw price factor of the day
    adj_close: float
    ma34_adj: float
    atr_raw: float
    limit_up: float | None
    limit_down: float | None


@dataclass
class Position:
    trade_id: int
    stock_id: str
    industry: str | None
    signal_date: str
    entry_date: str
    entry_price: float           # raw fill AT ENTRY (reported as is)
    shares: float                # current shares (changes with splits / stock dividends / capital reductions)
    initial_stop: float          # raw initial stop AT ENTRY (reported as is)
    stop_basis_entry: float      # entry price in the trailing basis
    stop_basis: float            # current stop in the trailing basis (only rises)
    r_basis: float               # 1R in the trailing basis
    hc_basis: float = -math.inf  # highest close since entry in the trailing basis
    stop_raw: float = 0.0        # stop in raw prices as of the last priced bar (used for open risk)
    entry_shares: float = 0.0    # shares bought (R multiple is always measured on entry values)
    px_scale: float = 1.0        # cumulative ref/before of share-count events: entry_price * px_scale = current units
    bars_held: int = 0
    below_ma34: int = 0
    last_price: float = 0.0
    costs: float = 0.0
    dividends: float = 0.0
    invested: float = 0.0
    pending_exit: str | None = None


@dataclass
class EngineResult:
    trades: list[dict] = field(default_factory=list)
    daily: list[dict] = field(default_factory=list)
    positions: list[dict] = field(default_factory=list)
    cash: list[dict] = field(default_factory=list)
    skipped: dict = field(default_factory=dict)


class PortfolioEngine:
    def __init__(self, cfg: dict, cost: CostModel, trailing_basis: str = "adjusted"):
        self.cfg, self.cost, self.basis = cfg, cost, trailing_basis
        a, r, b = cfg["account"], cfg["risk"], cfg["backtest"]
        self.initial = float(a["initial_capital"])
        self.limits = dict(risk_pct=a["risk_per_trade"], max_weight=a["max_weight_per_stock"],
                           max_industry=a["max_weight_per_industry"], max_exposure=a["max_gross_exposure"],
                           max_open_risk=a["max_portfolio_open_risk"])
        self.stop_mult, self.trail_mult = r["stop_atr_mult"], r["trail_atr_mult"]
        self.trend_bars, self.time_bars, self.time_r = r["trend_exit_bars"], r["time_stop_bars"], r["time_stop_min_r"]
        self.max_gap_atr = r["max_gap_atr"]
        self.lots_until, self.board_lot = b["whole_lots_until"], b["board_lot"]
        self.rank, self.seed = b["signal_rank"], b["random_seed"]

    # -- helpers ------------------------------------------------------------------------
    def _to_basis(self, raw: float, bar: Bar) -> float:
        return raw * bar.pf if self.basis == "adjusted" else raw

    def _from_basis(self, x: float, bar: Bar) -> float:
        return x / bar.pf if self.basis == "adjusted" else x

    def run(self, days: Sequence[str], bars: dict[str, dict[str, Bar]], signals: dict[str, list[dict]],
            actions: dict[str, list[dict]], meta: dict[str, dict]) -> EngineResult:
        """days: trading days; bars[sid][date]; signals[date] -> [{stock_id, close, atr, turnover}];
        actions[date] -> [{stock_id, action_type, before_price, reference_price}];
        meta[sid] -> {industry, name, last_price_date, delisting_date}."""
        res = EngineResult()
        skipped = res.skipped
        cash, seq, tid = self.initial, 0, 0
        realized = 0.0
        pos: dict[str, Position] = {}
        pending: list[dict] = []
        prev_equity = self.initial

        def ledger(d, kind, amount, sid=None, trade=None):
            nonlocal seq
            seq += 1
            res.cash.append({"seq": seq, "date": d, "kind": kind, "amount": amount, "stock_id": sid, "trade_id": trade})

        def close_trade(p: Position, d: str, ref_price: float, reason: str, apply_slippage: bool = True):
            nonlocal cash, realized
            odd = p.shares % self.board_lot != 0
            px = self.cost.fill_price(ref_price, "sell", odd) if apply_slippage else ref_price
            value = p.shares * px
            fee, tax = self.cost.commission(value), self.cost.tax(value)
            cash += value - fee - tax
            ledger(d, "sell", value, p.stock_id, p.trade_id)
            ledger(d, "commission", -fee, p.stock_id, p.trade_id)
            ledger(d, "tax", -tax, p.stock_id, p.trade_id)
            p.costs += fee + tax
            pnl = value - fee - tax - p.invested + p.dividends
            realized += pnl
            risk = p.entry_shares * (p.entry_price - p.initial_stop)
            res.trades.append(self._trade_row(p, d, px, reason, pnl, risk, closed=True, meta=meta))
            del pos[p.stock_id]

        if days:
            ledger(days[0], "initial", self.initial)
        for d in days:
            # 0. corporate actions on held positions ------------------------------------------
            for a in actions.get(d, []):
                p = pos.get(a["stock_id"])
                if p is None or not a.get("before_price") or not a.get("reference_price"):
                    continue
                before, ref = float(a["before_price"]), float(a["reference_price"])
                if a["action_type"] in CASH_EVENTS:
                    div = p.shares * max(before - ref, 0.0)
                    cash += div
                    p.dividends += div
                    ledger(d, "dividend", div, p.stock_id, p.trade_id)
                elif a["action_type"] in SHARE_EVENTS and ref > 0:
                    p.shares *= before / ref
                    p.px_scale *= ref / before      # keep per-share prices in the same units as the shares
                    if self.basis == "raw":     # raw variant still follows share-count changes
                        f = ref / before
                        p.stop_basis *= f
                        p.stop_basis_entry *= f
                        p.r_basis *= f
                        p.hc_basis *= f

            # 1. open: pending exits -----------------------------------------------------------
            for sid, p in list(pos.items()):
                if p.pending_exit is None:
                    continue
                bar = bars[sid].get(d)
                if bar is None or not bar.priced:
                    continue
                if is_locked(bar.high, bar.low, bar.limit_down) and bar.open <= (bar.limit_down or 0) + 1e-9:
                    skipped["exit_deferred_limit_down"] = skipped.get("exit_deferred_limit_down", 0) + 1
                    continue
                close_trade(p, d, bar.open, p.pending_exit)

            # 2. open: entries -------------------------------------------------------------------
            if pending:
                mv = sum(p.shares * p.last_price for p in pos.values())
                open_risk = sum(p.shares * max(p.entry_price * p.px_scale - p.stop_raw, 0.0) for p in pos.values())
                for s in self._ranked(pending, d):
                    sid = s["stock_id"]
                    if sid in pos:
                        continue
                    bar = bars.get(sid, {}).get(d)
                    if bar is None or not bar.priced:
                        skipped["no_price_on_entry_day"] = skipped.get("no_price_on_entry_day", 0) + 1
                        continue
                    why = entry_check(bar.open, bar.limit_up, s["close"], s["atr"], self.max_gap_atr)
                    if why:
                        skipped[why] = skipped.get(why, 0) + 1
                        continue
                    odd_allowed = d >= self.lots_until
                    fill = self.cost.fill_price(bar.open, "buy")
                    stop = fill - self.stop_mult * s["atr"]
                    ind = meta.get(sid, {}).get("industry")
                    ind_val = sum(p.shares * p.last_price for p in pos.values() if p.industry == ind and ind)
                    n, binding = size_position(equity=prev_equity, cash=cash, price=fill, stop=stop, market_value=mv,
                                               industry_value=ind_val, open_risk=open_risk, cost=self.cost,
                                               **self.limits)
                    shares = lot_shares(n, d, self.lots_until, self.board_lot)
                    if odd_allowed and shares % self.board_lot:
                        fill = self.cost.fill_price(bar.open, "buy", odd_lot=True)
                        stop = fill - self.stop_mult * s["atr"]
                        n, binding = size_position(equity=prev_equity, cash=cash, price=fill, stop=stop,
                                                   market_value=mv, industry_value=ind_val, open_risk=open_risk,
                                                   cost=self.cost, **self.limits)
                        shares = lot_shares(n, d, self.lots_until, self.board_lot)
                    if shares <= 0:
                        skipped[f"size_zero:{binding}"] = skipped.get(f"size_zero:{binding}", 0) + 1
                        continue
                    value = shares * fill
                    fee = self.cost.commission(value)
                    if value + fee > cash + 1e-6:
                        skipped["insufficient_cash"] = skipped.get("insufficient_cash", 0) + 1
                        continue
                    tid += 1
                    cash -= value + fee
                    ledger(d, "buy", -value, sid, tid)
                    ledger(d, "commission", -fee, sid, tid)
                    e_b, s_b = self._to_basis(fill, bar), self._to_basis(stop, bar)
                    p = Position(tid, sid, ind, s["signal_date"], d, fill, float(shares), stop, e_b, s_b, e_b - s_b,
                                 last_price=bar.open, stop_raw=stop, costs=fee, invested=value + fee,
                                 entry_shares=float(shares))
                    pos[sid] = p
                    mv += value
                    open_risk += shares * (fill - stop)
                    skipped[f"filled:{binding}"] = skipped.get(f"filled:{binding}", 0) + 1
            pending = []

            # 3. intraday stops -------------------------------------------------------------------
            for sid, p in list(pos.items()):
                bar = bars[sid].get(d)
                if bar is None or not bar.priced:
                    continue
                stop_raw = self._from_basis(p.stop_basis, bar)
                hit = stop_fill(bar.open, bar.high, bar.low, stop_raw, bar.limit_down)
                if hit is None:
                    continue
                kind, ref = hit
                reason = "STOP_INITIAL" if p.stop_basis <= (p.stop_basis_entry - p.r_basis) + 1e-9 else "STOP_TRAIL"
                if kind == "locked":
                    p.pending_exit = reason + "_DEFERRED"
                    skipped["stop_locked_limit_down"] = skipped.get("stop_locked_limit_down", 0) + 1
                    continue
                close_trade(p, d, ref, reason + ("_GAP" if kind == "gap" else ""))

            # 4. close: update positions, exits for tomorrow, delistings ------------------------------
            for sid, p in list(pos.items()):
                bar = bars[sid].get(d)
                m = meta.get(sid, {})
                if (bar is None or not bar.priced) and m.get("delisting_date") and d >= m["delisting_date"]:
                    close_trade(p, d, p.last_price, "DELISTED")
                    continue
                if bar is None or not bar.priced:
                    continue
                p.last_price = bar.close
                p.bars_held += 1
                c_b = self._to_basis(bar.close, bar)
                p.hc_basis = max(p.hc_basis, c_b)
                atr_b = self._to_basis(bar.atr_raw, bar) if not math.isnan(bar.atr_raw) else math.nan
                if not math.isnan(atr_b):
                    p.stop_basis = max(p.stop_basis, p.hc_basis - self.trail_mult * atr_b)
                p.stop_raw = self._from_basis(p.stop_basis, bar)
                p.below_ma34 = p.below_ma34 + 1 if (not math.isnan(bar.ma34_adj) and bar.adj_close < bar.ma34_adj) else 0
                if p.pending_exit is None:
                    if p.below_ma34 >= self.trend_bars:
                        p.pending_exit = "TREND_MA34"
                    elif p.bars_held >= self.time_bars and p.hc_basis < p.stop_basis_entry + self.time_r * p.r_basis:
                        p.pending_exit = "TIME_STOP"
            pending = [s for s in signals.get(d, []) if s["stock_id"] not in pos]

            # mark to market ------------------------------------------------------------------------
            mv = sum(p.shares * p.last_price for p in pos.values())
            equity = cash + mv
            unreal = sum(p.shares * p.last_price - p.invested + p.dividends for p in pos.values())
            o_risk = sum(p.shares * max(p.entry_price * p.px_scale - p.stop_raw, 0.0) for p in pos.values())
            res.daily.append({"date": d, "equity": equity, "cash": cash, "market_value": mv,
                              "exposure": mv / equity if equity > 0 else 0.0, "open_risk": o_risk,
                              "n_positions": len(pos), "realized_pnl": realized, "unrealized_pnl": unreal})
            for p in pos.values():
                b = bars[p.stock_id].get(d)
                res.positions.append({"date": d, "stock_id": p.stock_id, "trade_id": p.trade_id, "shares": p.shares,
                                      "last_price": p.last_price, "market_value": p.shares * p.last_price,
                                      "stop_price": p.stop_raw,
                                      "unrealized_pnl": p.shares * p.last_price - p.invested + p.dividends,
                                      "priced_today": int(b is not None and b.priced)})
            prev_equity = equity

        for p in list(pos.values()):       # still open at the end: marked to market, not a real exit
            pnl = p.shares * p.last_price - p.invested + p.dividends
            risk = p.entry_shares * (p.entry_price - p.initial_stop)
            res.trades.append(self._trade_row(p, None, p.last_price, "OPEN_AT_END", pnl, risk, closed=False, meta=meta))
        return res

    def _ranked(self, pending: list[dict], d: str) -> list[dict]:
        # Canonical order first, so the result never depends on the order the signals were loaded in
        # (regression: the database and the experiment context delivered the same signals in different orders).
        pending = sorted(pending, key=lambda s: s["stock_id"])
        if self.rank == "turnover":
            return sorted(pending, key=lambda s: -s.get("turnover", 0.0))
        rng = np.random.default_rng(self.seed + zlib.crc32(d.encode()))
        order = rng.permutation(len(pending))
        return [pending[i] for i in order]

    @staticmethod
    def _trade_row(p: Position, exit_date, exit_price, reason, pnl, risk, closed, meta) -> dict:
        return {"trade_id": p.trade_id, "stock_id": p.stock_id, "stock_name": meta.get(p.stock_id, {}).get("name"),
                "industry": p.industry, "signal_date": p.signal_date, "entry_date": p.entry_date,
                "entry_price": p.entry_price, "initial_stop": p.initial_stop, "shares": p.entry_shares,
                "invested": p.invested, "exit_date": exit_date, "exit_price": exit_price, "exit_reason": reason,
                "costs": p.costs, "dividends": p.dividends, "pnl": pnl,
                "return_pct": pnl / p.invested if p.invested else None,
                "r_multiple": pnl / risk if risk > 0 else None, "holding_days": p.bars_held, "is_closed": int(closed)}

