"""Performance metrics (Prompt 04 §9.5). Every metric is computed from the simulated daily
equity curve or from CLOSED trades; open positions at the end are excluded from trade stats.

Definitions
-----------
* Annualisation: 252 trading days.
* Break-even trade: |net return| < 0.1% after costs (counted separately from wins and losses).
* Win rate = wins / (wins + losses + break-evens), with a 95% Wilson score interval.
* Expectancy: mean net P&L per trade (NT$), mean R multiple, mean net return.
* Turnover: total bought value / mean equity, per year.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

ANN = 252
BREAKEVEN = 0.001


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (math.nan, math.nan)
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (centre - half, centre + half)


def _streak(flags: list[bool]) -> int:
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


def equity_metrics(daily: pd.DataFrame, rf: float = 0.0) -> dict:
    if daily.empty or len(daily) < 2:
        return {}
    eq = daily.set_index(pd.DatetimeIndex(daily["date"]))["equity"].astype(float)
    r = eq.pct_change().dropna()
    years = len(r) / ANN
    total = eq.iloc[-1] / eq.iloc[0] - 1
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1 if years > 0 and eq.iloc[-1] > 0 else math.nan
    ex = r - rf / ANN
    vol = r.std(ddof=1) * math.sqrt(ANN)
    downside = math.sqrt((np.minimum(ex, 0) ** 2).mean()) * math.sqrt(ANN)
    peak = eq.cummax()
    dd = eq / peak - 1
    mdd = float(-dd.min())
    under = (dd < 0).astype(int).to_numpy()
    longest = cur = 0
    for u in under:
        cur = cur + 1 if u else 0
        longest = max(longest, cur)
    trough = dd.idxmin()
    after = eq[trough:]
    rec = after[after >= peak[trough]]
    recovery = int(eq.index.get_loc(rec.index[0]) - eq.index.get_loc(trough)) if len(rec) else math.nan
    return {
        "total_return": float(total), "cagr": float(cagr), "volatility": float(vol),
        "sharpe": float(ex.mean() / r.std(ddof=1) * math.sqrt(ANN)) if r.std(ddof=1) > 0 else math.nan,
        "sortino": float(ex.mean() * ANN / downside) if downside > 0 else math.nan,
        "downside_deviation": float(downside), "max_drawdown": mdd,
        "calmar": float(cagr / mdd) if mdd > 0 and not math.isnan(cagr) else math.nan,
        "max_drawdown_duration_days": float(longest), "average_drawdown": float(-dd[dd < 0].mean()) if (dd < 0).any() else 0.0,
        "recovery_days_from_max_drawdown": float(recovery),
        "exposure_mean": float(daily["exposure"].mean()), "years": float(years),
    }


def annual_returns(daily: pd.DataFrame) -> pd.Series:
    eq = daily.set_index(pd.DatetimeIndex(daily["date"]))["equity"].astype(float)
    last = eq.groupby(eq.index.year).last()
    first_prev = last.shift(1).fillna(eq.iloc[0])
    return last / first_prev - 1


def monthly_returns(daily: pd.DataFrame) -> pd.Series:
    eq = daily.set_index(pd.DatetimeIndex(daily["date"]))["equity"].astype(float)
    last = eq.groupby([eq.index.year, eq.index.month]).last()
    return last / last.shift(1).fillna(eq.iloc[0]) - 1


def trade_metrics(trades: pd.DataFrame, daily: pd.DataFrame | None = None) -> dict:
    t = trades[trades["is_closed"] == 1] if not trades.empty else trades
    n = len(t)
    if n == 0:
        return {"trades": 0}
    ret = t["return_pct"].astype(float)
    win, loss = ret > BREAKEVEN, ret < -BREAKEVEN
    be = ~(win | loss)
    k = int(win.sum())
    lo, hi = wilson(k, n)
    gp, gl = float(t.loc[t["pnl"] > 0, "pnl"].sum()), float(-t.loc[t["pnl"] < 0, "pnl"].sum())
    avg_w = float(t.loc[win, "pnl"].mean()) if win.any() else math.nan
    avg_l = float(-t.loc[loss, "pnl"].mean()) if loss.any() else math.nan
    order = t.sort_values(["exit_date", "trade_id"])
    years = (pd.Timestamp(daily["date"].iloc[-1]) - pd.Timestamp(daily["date"].iloc[0])).days / 365.25 \
        if daily is not None and len(daily) > 1 else math.nan
    out = {
        "trades": n, "wins": k, "losses": int(loss.sum()), "breakevens": int(be.sum()),
        "win_rate": k / n, "win_rate_ci_low": lo, "win_rate_ci_high": hi,
        "gross_profit": gp, "gross_loss": gl, "net_profit": float(t["pnl"].sum()),
        "profit_factor": gp / gl if gl > 0 else math.nan,
        "average_win": avg_w, "average_loss": avg_l,
        "payoff_ratio": avg_w / avg_l if avg_l and not math.isnan(avg_l) and avg_l > 0 else math.nan,
        "expectancy_pnl": float(t["pnl"].mean()), "expectancy_r": float(t["r_multiple"].astype(float).mean()),
        "average_trade_return": float(ret.mean()), "median_trade_return": float(ret.median()),
        "max_trade_profit": float(t["pnl"].max()), "max_trade_loss": float(t["pnl"].min()),
        "max_consecutive_wins": _streak(list(order["return_pct"] > BREAKEVEN)),
        "max_consecutive_losses": _streak(list(order["return_pct"] < -BREAKEVEN)),
        "average_holding_days": float(t["holding_days"].mean()), "total_costs": float(t["costs"].sum()),
        "trades_per_year": n / years if years and years > 0 else math.nan,
    }
    if daily is not None and len(daily) > 1 and years and years > 0:
        out["annual_turnover"] = float(t["invested"].sum() / daily["equity"].mean() / years)
    return out


def grouped_win_rates(trades: pd.DataFrame, by: str) -> pd.DataFrame:
    t = trades[trades["is_closed"] == 1].copy()
    if t.empty:
        return pd.DataFrame()
    if by == "year":
        t["year"] = t["exit_date"].str[:4]
    g = t.groupby(by)
    out = pd.DataFrame({"trades": g.size(), "win_rate": g["return_pct"].apply(lambda r: (r > BREAKEVEN).mean()),
                        "net_profit": g["pnl"].sum(), "avg_r": g["r_multiple"].mean(),
                        "avg_holding_days": g["holding_days"].mean()})
    ci = [wilson(int((g.get_group(k)["return_pct"] > BREAKEVEN).sum()), len(g.get_group(k))) for k in out.index]
    out["win_rate_ci_low"], out["win_rate_ci_high"] = [c[0] for c in ci], [c[1] for c in ci]
    return out.sort_values("trades", ascending=False)


def split_segments(days: list[str], fractions=(0.6, 0.2, 0.2), embargo: int = 40) -> dict[str, tuple[str, str]]:
    """Chronological IS / Validation / OOS ranges with an embargo gap between segments."""
    n = len(days)
    a = int(n * fractions[0])
    b = int(n * (fractions[0] + fractions[1]))
    seg = {"IS": (0, a - 1), "VALIDATION": (min(a + embargo, n - 1), b - 1), "OOS": (min(b + embargo, n - 1), n - 1)}
    return {k: (days[i], days[j]) for k, (i, j) in seg.items() if i <= j}
