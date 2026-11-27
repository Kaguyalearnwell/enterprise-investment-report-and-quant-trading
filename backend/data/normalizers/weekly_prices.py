"""Build weekly_prices from daily bars (raw and adjusted bases).

* Uses the SAME aggregation as the Phase 1 weekly filter (backend.indicators.weekly.resample_weekly),
  so the stored weekly bars and the strategy's weekly filter can never disagree.
* Only days with a published price contribute OHLC; volume is the sum of those days.
* week_end = the last TRADING day of that ISO week according to trading_calendar
  (a closed Friday makes Thursday the week end).
* complete = 1 only if the week is strictly before the as-of week. The current week
  is stored with complete = 0 and must never feed a historical signal.
"""
from __future__ import annotations

import pandas as pd

from backend.indicators.weekly import resample_weekly, week_key


def build_weekly(daily: pd.DataFrame, trading_days: list[str], as_of: str, computed_at: str) -> pd.DataFrame:
    """daily: rows of ONE stock with raw OHLCV, has_price, and adj_* columns (adj may be missing)."""
    # Never aggregate data after as_of (regression: rebuilding as of a past date used later bars).
    d = daily[(daily["has_price"] == 1) & (daily["date"] <= as_of)].copy()
    if d.empty:
        return pd.DataFrame()
    d.index = pd.DatetimeIndex(d["date"])
    cal = pd.DatetimeIndex(sorted(t for t in trading_days if t <= as_of))
    cal_end = pd.Series(cal, index=week_key(cal).to_numpy()).groupby(level=0).max()
    as_of_key = int(week_key(pd.DatetimeIndex([as_of])).iloc[0])
    out = []
    bases = {"raw": {"open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"}}
    if "adj_close" in d and d["adj_close"].notna().all():
        bases["adj"] = {"open": "adj_open", "high": "adj_high", "low": "adj_low", "close": "adj_close",
                        "volume": "adj_volume"}
    for basis, cols in bases.items():
        w = resample_weekly(d, cols)
        starts = d.groupby(week_key(d.index).to_numpy())["date"].min()
        counts = d.groupby(week_key(d.index).to_numpy())["date"].count()
        out.append(pd.DataFrame({
            "stock_id": d["stock_id"].iloc[0],
            "week_key": w.index.astype(int),
            "price_basis": basis,
            "week_start": starts.reindex(w.index).to_numpy(),
            "week_end": [cal_end.get(k, e).strftime("%Y-%m-%d") for k, e in zip(w.index, w["week_end"])],
            "open": w["open"].astype(float), "high": w["high"].astype(float), "low": w["low"].astype(float),
            "close": w["close"].astype(float), "volume": w["volume"].astype(float),
            "trading_days": counts.reindex(w.index).astype(int).to_numpy(),
            "complete": (w.index.astype(int) < as_of_key).astype(int),
            "built_as_of": as_of,
            "computed_at": computed_at,
        }))
    return pd.concat(out, ignore_index=True)
