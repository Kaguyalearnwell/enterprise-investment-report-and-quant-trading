"""Load data from the database, run the engine, store everything (append-only).

Modes (Prompt 04 §9.8, never mixed):
  * portfolio     - the whole universe with capital and risk limits (full-market backtest);
  * single_stock  - one stock, same engine and rules, its own run and report.

Universe filter (Prompt 04 §14.2): stocks whose latest data-quality verdict is FAIL are
excluded; with require_quality_verdict, stocks never validated are excluded too. The
exclusions are stored with the run.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import math
import uuid
from collections.abc import Sequence

import pandas as pd

from backend.backtesting import performance as pf
from backend.backtesting.engine import Bar, PortfolioEngine
from backend.backtesting.execution import CostModel
from backend.database.db_manager import DatabaseManager, utc_now
from backend.database.schema import ENTRY_BITS

log = logging.getLogger(__name__)
CODE_VERSION = "backtest/1"


def cost_model(cfg: dict, scenario: str, slippage_end: str) -> CostModel:
    c, s = cfg["costs"], cfg["costs"][scenario]
    lo, hi = s["slippage_bps"]
    return CostModel(commission_rate=c["commission_rate"], commission_discount=s["commission_discount"],
                     min_commission=c["min_commission"], sell_tax_rate=c["sell_tax_rate"],
                     slippage_bps=float(lo if slippage_end == "low" else hi),
                     odd_lot_extra_bps=cfg["backtest"]["odd_lot_extra_slippage_bps"])


class Backtester:
    def __init__(self, db: DatabaseManager, cfg: dict):
        self.db, self.cfg = db, cfg

    # ---- universe ----------------------------------------------------------------------------
    def universe(self, stock_ids: Sequence[str] | None) -> tuple[list[str], dict]:
        ids = list(stock_ids) if stock_ids else self.db.read_sql(
            "SELECT DISTINCT stock_id FROM strategy_signals ORDER BY 1").stock_id.tolist()
        v = self.db.latest_verdicts(ids).set_index("stock_id")["verdict"].to_dict()
        excluded = {"FAIL": sorted(s for s in ids if v.get(s) == "FAIL")}
        if self.cfg["backtest"]["require_quality_verdict"]:
            excluded["no_verdict"] = sorted(s for s in ids if s not in v)
        drop = {s for lst in excluded.values() for s in lst}
        return [s for s in ids if s not in drop], excluded

    # ---- data --------------------------------------------------------------------------------
    def load(self, ids: list[str], vid: str, entry_version: str, start: str, end: str):
        ivers = self.db.read_sql("SELECT indicator_version FROM strategy_versions WHERE strategy_version_id=?",
                                 (vid,))["indicator_version"].iloc[0]
        ph = ",".join("?" * len(ids))
        days = self.db.get_trading_days(start, end, as_of=end)
        q = f"""SELECT r.stock_id, r.date, r.open, r.high, r.low, r.close, r.has_price,
                       a.price_factor, a.adj_close, i.ma34, i.atr14_raw, l.limit_up, l.limit_down, l.has_limit
                FROM daily_prices_raw r
                JOIN daily_prices_adj a ON a.stock_id = r.stock_id AND a.date = r.date
                LEFT JOIN technical_indicators i ON i.stock_id = r.stock_id AND i.date = r.date AND i.indicator_version = ?
                LEFT JOIN price_limits l ON l.stock_id = r.stock_id AND l.date = r.date
                WHERE r.stock_id IN ({ph}) AND r.date BETWEEN ? AND ?"""
        df = self.db.read_sql(q, (ivers, *ids, start, end))
        bars: dict[str, dict[str, Bar]] = {s: {} for s in ids}
        nan = math.nan
        for r in df.itertuples(index=False):
            lim = r.has_limit == 1
            bars[r.stock_id][r.date] = Bar(
                r.open if r.has_price else nan, r.high if r.has_price else nan, r.low if r.has_price else nan,
                r.close if r.has_price else nan, bool(r.has_price), float(r.price_factor),
                r.adj_close if r.adj_close is not None else nan, r.ma34 if r.ma34 is not None else nan,
                r.atr14_raw if r.atr14_raw is not None else nan,
                float(r.limit_up) if lim and r.limit_up is not None else None,
                float(r.limit_down) if lim and r.limit_down is not None else None)
        bit = ENTRY_BITS[entry_version]
        sig = self.db.read_sql(f"""SELECT s.stock_id, s.date, i.close_raw, i.atr14_raw, i.turnover_ma20
                                   FROM strategy_signals s JOIN technical_indicators i
                                     ON i.stock_id = s.stock_id AND i.date = s.date AND i.indicator_version = ?
                                   WHERE s.strategy_version_id = ? AND (s.entries & {bit}) > 0
                                     AND s.stock_id IN ({ph}) AND s.date BETWEEN ? AND ?""",
                               (ivers, vid, *ids, start, end))
        signals: dict[str, list[dict]] = {}
        for r in sig.itertuples(index=False):
            if r.atr14_raw is None or math.isnan(r.atr14_raw):
                continue
            signals.setdefault(r.date, []).append({"stock_id": r.stock_id, "signal_date": r.date, "close": r.close_raw,
                                                   "atr": r.atr14_raw, "turnover": r.turnover_ma20 or 0.0})
        acts = self.db.read_sql(f"""SELECT stock_id, ex_date, action_type, before_price, reference_price
                                    FROM corporate_actions WHERE stock_id IN ({ph}) AND ex_date BETWEEN ? AND ?""",
                                (*ids, start, end))
        actions: dict[str, list[dict]] = {}
        for r in acts.to_dict("records"):
            actions.setdefault(r["ex_date"], []).append(r)
        m = self.db.read_sql(f"SELECT stock_id, stock_name, industry_category, delisting_date, last_price_date "
                             f"FROM security_master WHERE stock_id IN ({ph})", ids)
        meta = {r.stock_id: {"name": r.stock_name, "industry": r.industry_category, "delisting_date": r.delisting_date,
                             "last_price_date": r.last_price_date} for r in m.itertuples(index=False)}
        return days, bars, signals, actions, meta, int(len(sig))

    # ---- run -----------------------------------------------------------------------------------
    def run(self, *, strategy_version_id: str, entry_version: str = "E", stock_ids: Sequence[str] | None = None,
            mode: str = "portfolio", start: str = "2015-01-01", end: str | None = None, cost_scenario: str = "base",
            slippage_end: str = "low", trailing_basis: str | None = None, notes: str | None = None) -> dict:
        end = end or self.db.read_sql("SELECT MAX(date) d FROM strategy_signals").d[0]
        basis = trailing_basis or self.cfg["backtest"]["trailing_basis"]
        ids, excluded = self.universe(stock_ids)
        if mode == "single_stock" and len(ids) != 1:
            raise ValueError(f"single_stock mode needs exactly one usable stock, got {ids} (excluded: {excluded})")
        if not ids:
            raise ValueError(f"no usable stocks (excluded: {excluded})")
        days, bars, signals, actions, meta, n_sig = self.load(ids, strategy_version_id, entry_version, start, end)
        cost = cost_model(self.cfg, cost_scenario, slippage_end)
        res = PortfolioEngine(self.cfg, cost, basis).run(days, bars, signals, actions, meta)
        run_id = f"bt-{dt.datetime.now(dt.UTC).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
        dv = self.db.read_sql("SELECT MAX(version_id) v FROM data_versions").v[0]
        status = "formal" if not excluded.get("no_verdict") and dv is not None else "exploratory"
        params = {"cfg": self.cfg, "cost": cost.__dict__, "signals_in_period": n_sig}
        self.db.insert("backtest_runs", [{          # append-only: a run is never updated
            "run_id": run_id, "created_at": utc_now(), "mode": mode, "strategy_version_id": strategy_version_id,
            "entry_version": entry_version, "start_date": days[0], "end_date": days[-1], "universe_size": len(ids),
            "excluded": json.dumps(excluded), "cost_scenario": cost_scenario, "slippage_bps": cost.slippage_bps,
            "trailing_basis": basis, "params": json.dumps(params, default=str, sort_keys=True),
            "data_version_id": None if dv is None or pd.isna(dv) else int(dv), "code_version": CODE_VERSION,
            "status": status, "notes": notes}])
        trades = pd.DataFrame(res.trades)
        daily = pd.DataFrame(res.daily)
        for table, rows in (("simulated_trades", trades), ("portfolio_daily", daily),
                            ("portfolio_positions", pd.DataFrame(res.positions)), ("portfolio_cash", pd.DataFrame(res.cash))):
            if not rows.empty:
                x = rows.assign(run_id=run_id)
                self.db.insert(table, x.astype(object).where(x.notna(), None))
        metrics = self.metrics(trades, daily, days)
        self.db.insert("performance_metrics", [{"run_id": run_id, "segment": seg, "metric": k,
                                                "value": None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)}
                                               for seg, mm in metrics.items() for k, v in mm.items()])
        log.info("backtest %s: %d trades, final equity %.0f", run_id, len(trades),
                 daily["equity"].iloc[-1] if not daily.empty else float("nan"))
        return {"run_id": run_id, "status": status, "excluded": excluded, "skipped": res.skipped, "metrics": metrics,
                "universe": len(ids)}

    def metrics(self, trades: pd.DataFrame, daily: pd.DataFrame, days: list[str]) -> dict[str, dict]:
        rf = self.cfg["backtest"]["risk_free_rate"]
        sp = self.cfg["split"]
        out = {"ALL": {**pf.equity_metrics(daily, rf), **pf.trade_metrics(trades, daily)}}
        for seg, (a, b) in pf.split_segments(days, (sp["in_sample"], sp["validation"], sp["out_of_sample"]),
                                             sp["embargo_bars"]).items():
            d = daily[(daily["date"] >= a) & (daily["date"] <= b)]
            t = trades[(trades["entry_date"] >= a) & (trades["entry_date"] <= b)] if not trades.empty else trades
            out[seg] = {**pf.equity_metrics(d, rf), **pf.trade_metrics(t, d)}
        if not daily.empty:
            for y, r in pf.annual_returns(daily).items():
                out.setdefault(f"YEAR:{y}", {})["return"] = float(r)
        if not trades.empty:
            for y, row in pf.grouped_win_rates(trades, "year").iterrows():
                out.setdefault(f"YEAR:{y}", {}).update({"trades": row.trades, "win_rate": row.win_rate})
        return out
