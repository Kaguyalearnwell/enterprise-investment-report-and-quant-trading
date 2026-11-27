"""Factor designs, leave-one-out, matched random-entry benchmark, condition redundancy (Stage 5 §4.2-4.4).

Cell definitions (all share the same exits, sizing, costs and capital limits):
  MA   = daily_trend
  SQBO = recent_squeeze AND breakout          (the BB squeeze-breakout module)
  W    = weekly_trend
  VOL  = volume_confirm (kept only where a design includes it: A-E, S0, leave-one-out)
  "none" cell = matched random entry: same dates and counts as the MA-only cell, stocks drawn at random
  from that day's eligible pool (liquidity + warm-up + listed), averaged over the random draws.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.backtesting import performance as pf
from backend.experiments import stats
from backend.experiments.context import ExperimentContext
from backend.experiments.registry import Registry
from backend.strategies.fib_bb_weekly import VERSIONS

MA, SQBO, W = ("daily_trend",), ("recent_squeeze", "breakout"), ("weekly_trend",)
CELLS_2X2 = {"MA+SQBO": MA + SQBO, "MA": MA, "SQBO": SQBO}
CELLS_2X2X2 = {**CELLS_2X2, "MA+SQBO+W": MA + SQBO + W, "MA+W": MA + W, "SQBO+W": SQBO + W, "W": W}
E_CONDS = VERSIONS["E"]
LOO = {f"E-{c}": tuple(x for x in E_CONDS if x != c) for c in E_CONDS}
KEY = ["trades", "win_rate", "win_rate_ci_low", "win_rate_ci_high", "profit_factor", "expectancy_r", "average_win",
       "average_loss", "cagr", "max_drawdown", "sharpe", "sortino", "average_holding_days", "exposure_mean",
       "total_costs"]


def _seg_trades(ctx: ExperimentContext, trades: pd.DataFrame, seg: str) -> pd.DataFrame:
    a, b = ctx.segments[seg]
    t = trades[(trades["is_closed"] == 1)] if not trades.empty else trades
    return t[(t["entry_date"] >= a) & (t["entry_date"] <= b)] if not t.empty else t


def run_cells(ctx: ExperimentContext, reg: Registry, eid: str, cells: dict[str, tuple], **sim) -> dict:
    out = {}
    for label, conds in cells.items():
        trades, daily = ctx.simulate(ctx.signals_for(conds), **sim)
        m = ctx.metrics(trades, daily)
        reg.trial(eid, label, {"conditions": list(conds), **sim}, "IS+VALIDATION",
                  {seg: {k: m[seg].get(k) for k in KEY} for seg in m})
        out[label] = {"metrics": m, "trades": trades, "daily": daily}
    return out


def random_cell(ctx: ExperimentContext, template: dict, n: int, seed: int, **sim) -> list[dict]:
    pool = ctx.eligible_pool()
    rng = np.random.default_rng(seed)
    res = []
    for _ in range(n):
        trades, daily = ctx.simulate(ctx.random_like(template, pool, rng), **sim)
        res.append({"metrics": ctx.metrics(trades, daily), "trades": trades, "daily": daily})
    return res


def effects(ctx: ExperimentContext, cells: dict, none_trades: pd.DataFrame, seg: str = "IS", n_boot: int = 2000,
            seed: int = 0) -> dict:
    """MA and SQBO main effects and their interaction on expectancy (mean R per trade), with bootstrap CIs."""
    r = {k: _seg_trades(ctx, v["trades"], seg)["r_multiple"].astype(float).dropna().to_numpy() for k, v in cells.items()}
    r["none"] = _seg_trades(ctx, none_trades, seg)["r_multiple"].astype(float).dropna().to_numpy()
    if any(len(v) == 0 for v in (r["MA+SQBO"], r["MA"], r["SQBO"], r["none"])):
        return {"available": False, "n": {k: len(v) for k, v in r.items()}}

    def est(x11, x10, x01, x00):
        m11, m10, m01, m00 = x11.mean(), x10.mean(), x01.mean(), x00.mean()
        return {"ma_main": (m11 + m10 - m01 - m00) / 2, "sqbo_main": (m11 + m01 - m10 - m00) / 2,
                "interaction": (m11 - m10) - (m01 - m00)}

    point = est(r["MA+SQBO"], r["MA"], r["SQBO"], r["none"])
    rng = np.random.default_rng(seed)
    boots = {k: [] for k in point}
    for _ in range(n_boot):
        e = est(*[rng.choice(r[k], len(r[k])) for k in ("MA+SQBO", "MA", "SQBO", "none")])
        for k in e:
            boots[k].append(e[k])
    out = {"available": True, "segment": seg, "n": {k: int(len(v)) for k, v in r.items()},
           "cell_mean_r": {k: float(v.mean()) for k, v in r.items()}}
    for k, v in point.items():
        b = np.asarray(boots[k])
        out[k] = {"estimate": float(v), "ci95": [float(np.percentile(b, 2.5)), float(np.percentile(b, 97.5))],
                  "p_le_zero": float((b <= 0).mean())}
    return out


def random_benchmark(ctx: ExperimentContext, strategy: dict, randoms: list[dict], seg: str = "IS") -> dict:
    out = {"segment": seg, "n_random": len(randoms)}
    for k in ("expectancy_r", "profit_factor", "sharpe", "cagr", "win_rate", "max_drawdown"):
        s = strategy["metrics"][seg].get(k)
        vals = np.asarray([x["metrics"][seg].get(k) for x in randoms], float)
        vals = vals[~np.isnan(vals)]
        if s is None or np.isnan(s) or len(vals) == 0:
            continue
        better = (vals <= s).mean() if k != "max_drawdown" else (vals >= s).mean()
        worse_or_equal = (vals >= s).sum() if k != "max_drawdown" else (vals <= s).sum()
        out[k] = {"strategy": float(s), "random_median": float(np.median(vals)),
                  "random_p05": float(np.percentile(vals, 5)), "random_p95": float(np.percentile(vals, 95)),
                  "percentile": float(better), "p_value_one_sided": float((worse_or_equal + 1) / (len(vals) + 1))}
    return out


def redundancy(ctx: ExperimentContext, seg_end: str) -> pd.DataFrame:
    """Pairwise co-occurrence, conditional probability, Phi, mutual information on eligible stock-days."""
    from backend.database.schema import CONDITION_BITS

    rows = ctx.rows[(ctx.rows["date"] >= ctx.start) & (ctx.rows["date"] <= seg_end)]
    c = rows["conditions"].to_numpy()
    elig = (c & CONDITION_BITS["eligible"]) > 0
    names = ["daily_trend", "squeeze", "recent_squeeze", "breakout", "volume_confirm", "weekly_trend"]
    x = {n: ((c & CONDITION_BITS[n]) > 0)[elig] for n in names}
    out = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            xa, xb = x[a], x[b]
            both = (xa & xb).sum()
            out.append({"x": a, "y": b, "p_x": xa.mean(), "p_y": xb.mean(), "p_both": both / len(xa),
                        "p_y_given_x": both / xa.sum() if xa.sum() else np.nan,
                        "p_x_given_y": both / xb.sum() if xb.sum() else np.nan,
                        "phi": stats.phi(xa, xb), "mutual_information_bits": stats.mutual_information(xa, xb)})
    return pd.DataFrame(out)


def incremental_filtering(ctx: ExperimentContext, seg_end: str) -> pd.DataFrame:
    """For each E condition: of the stock-days passing all OTHER E conditions, what share does it remove?"""
    from backend.database.schema import CONDITION_BITS

    rows = ctx.rows[(ctx.rows["date"] >= ctx.start) & (ctx.rows["date"] <= seg_end)]
    c = rows["conditions"].to_numpy()
    base = (c & CONDITION_BITS["eligible"]) > 0
    out = []
    for cond in E_CONDS:
        others = base.copy()
        for o in E_CONDS:
            if o != cond:
                others &= (c & CONDITION_BITS[o]) > 0
        keep = others & ((c & CONDITION_BITS[cond]) > 0)
        out.append({"condition": cond, "days_passing_others": int(others.sum()), "days_passing_all": int(keep.sum()),
                    "share_removed": 1 - keep.sum() / others.sum() if others.sum() else np.nan})
    return pd.DataFrame(out)


def trade_mean_ci(r: np.ndarray, n_boot: int = 4000, seed: int = 0) -> dict:
    r = np.asarray(r, float)
    if len(r) == 0:
        return {"n": 0}
    rng = np.random.default_rng(seed)
    b = np.array([rng.choice(r, len(r)).mean() for _ in range(n_boot)])
    return {"n": int(len(r)), "mean": float(r.mean()), "ci95": [float(np.percentile(b, 2.5)), float(np.percentile(b, 97.5))],
            "p_le_zero": float((b <= 0).mean())}


__all__ = ["CELLS_2X2", "CELLS_2X2X2", "LOO", "run_cells", "random_cell", "effects", "random_benchmark", "redundancy",
           "incremental_filtering", "trade_mean_ci", "pf"]
