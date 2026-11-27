"""Weekly trend filter with strict no-repaint alignment.

Rules
-----
1. A week is an ISO calendar week (Mon-Sun) of the *actual* trading calendar,
   i.e. it is built only from dates present in the daily data. Holidays, a
   closed Friday or a whole closed week (Lunar New Year) need no special case:
   a week with no trading simply has no weekly bar.
2. Weekly bar timestamp = the last trading day of that week.
3. Every daily bar of week w sees the weekly values of the previous weekly bar
   (week w-1, or the last earlier week that traded). The current, still
   forming week is never used, not even on its last trading day. This is the
   same value Pine returns from
       request.security(syminfo.tickerid, "W", expr[1], lookahead = barmerge.lookahead_on)
   so Python and TradingView agree on every daily bar.
"""
from __future__ import annotations

import pandas as pd

from backend.indicators.core import sma


def week_key(index: pd.DatetimeIndex) -> pd.Series:
    iso = index.isocalendar()
    return pd.Series(iso["year"].to_numpy() * 100 + iso["week"].to_numpy(), index=index, name="week_key")


def resample_weekly(daily: pd.DataFrame, cols: dict[str, str] | None = None) -> pd.DataFrame:
    """Aggregate daily OHLCV into weekly bars keyed by ISO week."""
    cols = cols or {"open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"}
    keys = week_key(daily.index)
    g = daily.groupby(keys.to_numpy())
    weekly = pd.DataFrame(
        {
            "open": g[cols["open"]].first(),
            "high": g[cols["high"]].max(),
            "low": g[cols["low"]].min(),
            "close": g[cols["close"]].last(),
            "volume": g[cols["volume"]].sum(),
            "week_end": g.apply(lambda x: x.index.max(), include_groups=False),
        }
    )
    weekly.index.name = "week_key"
    return weekly.sort_index()


def weekly_trend(weekly: pd.DataFrame, fast: int = 13, mid: int = 34, slow: int = 55) -> pd.DataFrame:
    c = weekly["close"]
    out = pd.DataFrame(index=weekly.index)
    out["w_ma_fast"] = sma(c, fast)
    out["w_ma_mid"] = sma(c, mid)
    out["w_ma_slow"] = sma(c, slow)
    out["weekly_trend"] = (
        (out["w_ma_fast"] > out["w_ma_mid"])
        & (out["w_ma_mid"] > out["w_ma_slow"])
        & (out["w_ma_slow"] > out["w_ma_slow"].shift(1))
    )
    out["weekly_valid"] = out["w_ma_slow"].notna() & out["w_ma_slow"].shift(1).notna()
    out["week_end"] = weekly["week_end"]
    return out


def align_previous_week(daily_index: pd.DatetimeIndex, weekly_values: pd.DataFrame) -> pd.DataFrame:
    """Map each daily bar to the values of the previous completed weekly bar."""
    prev = weekly_values.shift(1)                       # row for week w now holds week w-1
    prev = prev.rename(columns={"week_end": "source_week_end"})
    keys = week_key(daily_index)
    aligned = prev.reindex(keys.to_numpy())
    aligned.index = daily_index
    for c in ("weekly_trend", "weekly_valid"):
        if c in aligned:
            aligned[c] = aligned[c].astype("boolean").fillna(False).astype(bool)
    return aligned


def weekly_filter(daily: pd.DataFrame, cfg: dict | None = None, price_cols: dict[str, str] | None = None) -> pd.DataFrame:
    cfg = cfg or {}
    wk = resample_weekly(daily, price_cols)
    wt = weekly_trend(wk, cfg.get("ma_fast", 13), cfg.get("ma_mid", 34), cfg.get("ma_slow", 55))
    return align_previous_week(daily.index, wt)
