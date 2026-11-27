"""Causal technical indicators.

Every function returns a Series aligned to the input index where the value at
bar t uses only data at bars <= t, so it is known at the close of bar t.
Definitions are chosen to match TradingView Pine Script v6 built-ins, so the
Python backtest and the Pine monitor can be cross-validated bar by bar:

    sma             == ta.sma
    rolling_std     == ta.stdev(src, n)            (biased=true, population std)
    rma             == ta.rma                      (seeded with the SMA of the first n values)
    wilder_atr      == ta.atr                      (rma of ta.tr(true))
    percent_rank    == ta.percentrank              (share of the previous n values <= current)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def rolling_std(s: pd.Series, n: int, ddof: int = 0) -> pd.Series:
    # pandas defaults to ddof=1; Bollinger Bands and Pine use the population std.
    return s.rolling(n, min_periods=n).std(ddof=ddof)


def rma(s: pd.Series, n: int) -> pd.Series:
    """Wilder's moving average, seeded with the SMA of the first n valid values."""
    x = s.to_numpy(dtype=float)
    out = np.full_like(x, np.nan)
    valid = np.flatnonzero(~np.isnan(x))
    if len(valid) < n:
        return pd.Series(out, index=s.index)
    start = valid[0]
    seed_end = start + n - 1
    if np.isnan(x[start : seed_end + 1]).any():
        raise ValueError("rma requires a contiguous block of valid values after the first one")
    out[seed_end] = x[start : seed_end + 1].mean()
    alpha = 1.0 / n
    for i in range(seed_end + 1, len(x)):
        xi = x[i]
        out[i] = out[i - 1] if np.isnan(xi) else out[i - 1] + alpha * (xi - out[i - 1])
    return pd.Series(out, index=s.index)


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    # Pine ta.tr(true): first bar falls back to high - low.
    tr.iloc[0] = high.iloc[0] - low.iloc[0]
    return tr


def wilder_atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    return rma(true_range(high, low, close), n)


def percent_rank(s: pd.Series, n: int) -> pd.Series:
    """Percent (0-100) of the previous n values that are <= the current value.

    The current bar is NOT part of the comparison window: window = [t-n, t-1].
    NaN until n previous valid values exist, or if any of them is NaN.
    """
    x = s.to_numpy(dtype=float)
    out = np.full_like(x, np.nan)
    if len(x) > n:
        prev = sliding_window_view(x[:-1], n)          # row j covers bars [j, j+n-1]
        cur = x[n:]                                   # bar j+n
        with np.errstate(invalid="ignore"):
            pct = (prev <= cur[:, None]).sum(axis=1) * 100.0 / n
        bad = np.isnan(prev).any(axis=1) | np.isnan(cur)
        pct[bad] = np.nan
        out[n:] = pct
    return pd.Series(out, index=s.index)


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0, ddof: int = 0) -> pd.DataFrame:
    mid = sma(close, n)
    sd = rolling_std(close, n, ddof=ddof)
    upper = mid + k * sd
    lower = mid - k * sd
    return pd.DataFrame({"bb_mid": mid, "bb_upper": upper, "bb_lower": lower, "bbw": (upper - lower) / mid})


def bars_since_true_within(cond: pd.Series, n: int) -> pd.Series:
    """True if cond was True on at least one of the last n bars, including the current one."""
    return cond.fillna(False).astype(int).rolling(n, min_periods=n).max().eq(1)
