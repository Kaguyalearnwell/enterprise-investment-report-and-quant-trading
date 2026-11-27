"""Shared data for Stage 5 experiments: load once, simulate many signal sets.

* The simulation window ends at the last Validation day: OOS dates are never simulated,
  so OOS performance cannot leak into any experiment, not even in memory.
* Entry signals for ANY combination of conditions are rebuilt from the stored condition
  bitmasks (rising edge of eligible AND all conditions, per stock, over its priced days),
  so experiments do not need new strategy versions.
"""
from __future__ import annotations

import json
import math
from collections.abc import Sequence

import numpy as np
import pandas as pd

from backend.backtesting import performance as pf
from backend.backtesting.backtester import Backtester, cost_model
from backend.backtesting.engine import PortfolioEngine
from backend.database.db_manager import DatabaseManager
from backend.database.schema import CONDITION_BITS

CONDITIONS = ("daily_trend", "recent_squeeze", "breakout", "volume_confirm", "weekly_trend")


class ExperimentContext:
    def __init__(self, db: DatabaseManager, cfg: dict, strategy_version_id: str, stock_ids: Sequence[str] | None = None,
                 start: str = "2015-01-01", full_end: str | None = None):
        self.db, self.cfg, self.vid = db, cfg, strategy_version_id
        bt = Backtester(db, cfg)
        ids, self.excluded = bt.universe(stock_ids)
        self.ids = ids
        full_end = full_end or db.read_sql("SELECT MAX(date) d FROM strategy_signals").d[0]
        all_days = db.get_trading_days(start, full_end, as_of=full_end)
        sp = cfg["split"]
        self.segments = pf.split_segments(all_days, (sp["in_sample"], sp["validation"], sp["out_of_sample"]),
                                          sp["embargo_bars"])
        self.start, self.end = all_days[0], self.segments["VALIDATION"][1]     # OOS never simulated
        self.days, self.bars, _, self.actions, self.meta, _ = bt.load(ids, strategy_version_id, "E", self.start, self.end)
        ivers = db.read_sql("SELECT indicator_version FROM strategy_versions WHERE strategy_version_id=?",
                            (strategy_version_id,))["indicator_version"].iloc[0]
        ph = ",".join("?" * len(ids))
        rows = db.read_sql(f"""SELECT s.stock_id, s.date, s.conditions, i.close_raw, i.atr14_raw, i.turnover_ma20
                               FROM strategy_signals s JOIN technical_indicators i
                                 ON i.stock_id = s.stock_id AND i.date = s.date AND i.indicator_version = ?
                               WHERE s.strategy_version_id = ? AND s.stock_id IN ({ph}) AND s.date <= ?
                               ORDER BY s.stock_id, s.date""", (ivers, strategy_version_id, *ids, self.end))
        self.rows = rows
        self.eligible = (rows["conditions"].to_numpy() & CONDITION_BITS["eligible"]) > 0

    # ---- signals ------------------------------------------------------------------------------
    def state(self, conds: Sequence[str]) -> np.ndarray:
        c = self.rows["conditions"].to_numpy()
        st = self.eligible.copy()
        for name in conds:
            st &= (c & CONDITION_BITS[name]) > 0
        return st

    def signals_for(self, conds: Sequence[str]) -> dict[str, list[dict]]:
        st = self.state(conds)
        prev = np.r_[False, st[:-1]]
        same = np.r_[False, self.rows["stock_id"].to_numpy()[1:] == self.rows["stock_id"].to_numpy()[:-1]]
        edge = st & ~(prev & same)
        return self._to_signals(edge)

    def _to_signals(self, mask: np.ndarray) -> dict[str, list[dict]]:
        sel = self.rows[mask & (self.rows["date"] >= self.start).to_numpy()]
        out: dict[str, list[dict]] = {}
        for r in sel.itertuples(index=False):
            if r.atr14_raw is None or (isinstance(r.atr14_raw, float) and math.isnan(r.atr14_raw)):
                continue
            out.setdefault(r.date, []).append({"stock_id": r.stock_id, "signal_date": r.date, "close": r.close_raw,
                                               "atr": r.atr14_raw, "turnover": r.turnover_ma20 or 0.0})
        return out

    def eligible_pool(self) -> dict[str, list[dict]]:
        return self._to_signals(self.eligible)

    @staticmethod
    def random_like(template: dict[str, list[dict]], pool: dict[str, list[dict]], rng: np.random.Generator):
        """Same signal dates and counts as `template`, stocks drawn at random from that day's eligible pool."""
        out = {}
        for d, sigs in template.items():
            cand = pool.get(d, [])
            if not cand:
                continue
            k = min(len(sigs), len(cand))
            idx = rng.choice(len(cand), size=k, replace=False)
            out[d] = [cand[i] for i in idx]
        return out

    # ---- simulation ------------------------------------------------------------------------------
    def simulate(self, signals: dict[str, list[dict]], cost_scenario: str = "base", slippage_end: str = "low",
                 basis: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
        cost = cost_model(self.cfg, cost_scenario, slippage_end)
        res = PortfolioEngine(self.cfg, cost, basis or self.cfg["backtest"]["trailing_basis"]).run(
            self.days, self.bars, signals, self.actions, self.meta)
        return pd.DataFrame(res.trades), pd.DataFrame(res.daily)

    def metrics(self, trades: pd.DataFrame, daily: pd.DataFrame, segments=("IS", "VALIDATION")) -> dict[str, dict]:
        rf = self.cfg["backtest"]["risk_free_rate"]
        out = {}
        for seg in segments:
            a, b = self.segments[seg]
            d = daily[(daily["date"] >= a) & (daily["date"] <= b)]
            t = trades[(trades["entry_date"] >= a) & (trades["entry_date"] <= b)] if not trades.empty else trades
            out[seg] = {**pf.equity_metrics(d, rf), **pf.trade_metrics(t, d)}
        return out

    @staticmethod
    def daily_returns(daily: pd.DataFrame, a: str, b: str) -> pd.Series:
        d = daily[(daily["date"] >= a) & (daily["date"] <= b)].set_index("date")["equity"].astype(float)
        return d.pct_change().dropna()

