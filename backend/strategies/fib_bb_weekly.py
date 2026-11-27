"""Daily signal generation for one symbol.

Input DataFrame (DatetimeIndex of trading days, ascending), columns:

    open, high, low, close, volume       raw (unadjusted) prices, volume in SHARES
    adj_open, adj_high, adj_low, adj_close, adj_volume
                                         back-adjusted for dividends / splits
    tradable (optional, bool)            False for suspended, full-delivery (全額交割)
                                         or disposition (處置) stocks on that day

Price basis
-----------
* Signals use adjusted prices. Every condition is a ratio or an ordering, so it
  is invariant to the uniform rescaling that later corporate actions apply to a
  back-adjusted history. Hence the back-adjusted series is causal for signals.
* Liquidity, execution, limit-up/down, tick size and share counts use raw prices.
* ATR for stops is converted to raw price units: atr_raw = atr_adj * close / adj_close.

Timing
------
Every column is known at the CLOSE of its bar t. The execution engine may act
on it no earlier than the OPEN of bar t+1.
"""
from __future__ import annotations

import pandas as pd

from backend.indicators.core import bars_since_true_within, bollinger, percent_rank, sma, wilder_atr
from backend.indicators.weekly import weekly_filter

ADJ_COLS = {"open": "adj_open", "high": "adj_high", "low": "adj_low", "close": "adj_close", "volume": "adj_volume"}

# Ablation versions (Prompt 03 section 11). Liquidity / tradability / warm-up are a
# common eligibility mask applied to every version, so all versions trade the same universe.
VERSIONS: dict[str, tuple[str, ...]] = {
    "A": ("daily_trend",),
    "B": ("daily_trend", "recent_squeeze"),
    "C": ("daily_trend", "recent_squeeze", "breakout"),
    "D": ("daily_trend", "recent_squeeze", "breakout", "volume_confirm"),
    "E": ("daily_trend", "recent_squeeze", "breakout", "volume_confirm", "weekly_trend"),
    # Extra control from the Stage 1 review: breakout without the MA filter.
    "S0": ("recent_squeeze", "breakout", "volume_confirm"),
}


def compute_features(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    t, bb, br, rk, wk = cfg["trend"], cfg["bollinger"], cfg["breakout"], cfg["risk"], cfg["weekly"]
    c = df["adj_close"]
    f = pd.DataFrame(index=df.index)

    # --- trend -----------------------------------------------------------------
    f["ma_fast"] = sma(c, t["ma_fast"])
    f["ma_mid"] = sma(c, t["ma_mid"])
    f["ma_slow"] = sma(c, t["ma_slow"])
    k = t["slope_lookback"]
    f["slope_slow"] = (f["ma_slow"] - f["ma_slow"].shift(k)) / k
    f["daily_trend"] = (f["ma_fast"] > f["ma_mid"]) & (f["ma_mid"] > f["ma_slow"]) & (f["slope_slow"] > 0)
    # research diagnostics (not used as filters until tested)
    f["gap_fast_mid"] = (f["ma_fast"] - f["ma_mid"]) / c
    f["gap_mid_slow"] = (f["ma_mid"] - f["ma_slow"]) / c
    f["ma_tangle"] = (f[["ma_fast", "ma_mid", "ma_slow"]].max(axis=1) - f[["ma_fast", "ma_mid", "ma_slow"]].min(axis=1)) / c
    run = f["daily_trend"].astype(int)
    f["trend_age"] = run.groupby((run == 0).cumsum()).cumsum()

    # --- volatility compression ------------------------------------------------
    f = f.join(bollinger(c, bb["period"], bb["num_std"], bb["std_ddof"]))
    f["bbw_pct"] = percent_rank(f["bbw"], bb["bbw_pct_lookback"])
    f["squeeze"] = f["bbw_pct"] <= bb["squeeze_pct"]
    f["recent_squeeze"] = bars_since_true_within(f["squeeze"], bb["squeeze_recent_bars"])

    # --- breakout & volume -----------------------------------------------------
    f["breakout"] = c > f["bb_upper"]
    f["volume_ma"] = sma(df["adj_volume"], br["volume_ma"])          # includes bar t, like Pine ta.sma(volume, 20)
    f["volume_confirm"] = df["adj_volume"] > br["volume_mult"] * f["volume_ma"]

    # --- liquidity (raw prices, NT$) ------------------------------------------
    f["turnover_ma"] = sma(df["close"] * df["volume"], br["turnover_ma"])
    f["liquid"] = f["turnover_ma"] >= br["min_turnover"]

    # --- weekly filter (previous completed week only) --------------------------
    w = weekly_filter(df, wk, ADJ_COLS)
    f["weekly_trend"] = w["weekly_trend"]
    f["weekly_valid"] = w["weekly_valid"]
    f["weekly_source_week_end"] = w["source_week_end"]

    # --- risk inputs -----------------------------------------------------------
    atr_adj = wilder_atr(df["adj_high"], df["adj_low"], c, rk["atr_period"])
    f["atr_raw"] = atr_adj * df["close"] / c
    f["below_exit_ma_n"] = (
        (c < sma(c, rk["trend_exit_ma"])).astype(int).rolling(rk["trend_exit_bars"], min_periods=rk["trend_exit_bars"]).sum()
        == rk["trend_exit_bars"]
    )

    # --- common eligibility ----------------------------------------------------
    warm = f[["ma_slow", "slope_slow", "bbw_pct", "atr_raw", "turnover_ma"]].notna().all(axis=1) & f["weekly_valid"]
    tradable = df["tradable"].astype(bool) if "tradable" in df else True
    f["eligible"] = warm & f["liquid"] & tradable
    return f


def entry_signals(f: pd.DataFrame, versions: dict[str, tuple[str, ...]] = VERSIONS) -> pd.DataFrame:
    """Entry event = first bar on which (eligible AND all version conditions) becomes True.

    A rising-edge event is used for every version so that state-type versions
    (A, B) and event-type versions (C-E) are compared on the same footing.
    """
    out = pd.DataFrame(index=f.index)
    for name, conds in versions.items():
        state = f["eligible"].copy()
        for c in conds:
            state &= f[c].fillna(False).astype(bool)
        out[f"entry_{name}"] = state & ~state.shift(1, fill_value=False)
    return out


def generate(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    f = compute_features(df, cfg)
    return f.join(entry_signals(f))
