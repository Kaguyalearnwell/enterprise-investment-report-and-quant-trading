"""Backward price adjustment from corporate actions (method `backward_ratio_v1`).

For every event E with factor f_E = reference_price / before_price:

    price_factor(d) = product of f_E over all events with ex_date E > d
    adj_price(d)    = raw_price(d) * price_factor(d)

* The latest bar is never changed (factor 1); history before each event is scaled
  so the series is continuous across the event.
* Signals use only ratios/orderings of adjusted prices inside a trailing window,
  which are invariant to the uniform rescaling a later event applies, so the
  adjusted series is causal for signals (Phase 1 §2.2, test_signals_scale_invariant).
* Volume: share-count events (split, reverse split, par-value change, pure stock
  dividend) scale earlier volume by 1/f_E. Cash dividends do not change share count.
  For capital reductions and combined cash+stock dividends the share ratio is not
  in the free data, so volume is left unscaled and the event is reported.
* Execution must NEVER use these prices (see backend/database/schema.py).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

METHOD = "backward_ratio_v1"
SHARE_EVENTS = {"split", "reverse_split", "par_value_change", "stock_dividend"}
VOLUME_UNKNOWN_EVENTS = {"capital_reduction", "cash_and_stock_dividend"}


def event_factors(actions: pd.DataFrame) -> pd.DataFrame:
    """One row per ex_date: combined price factor and volume factor."""
    if actions.empty:
        return pd.DataFrame(columns=["ex_date", "f_price", "f_volume", "volume_exact", "types"])
    a = actions[actions["adjustment_factor"].notna() & (actions["adjustment_factor"] > 0)].copy()
    a["f_volume"] = np.where(a["action_type"].isin(SHARE_EVENTS), 1.0 / a["adjustment_factor"], 1.0)
    a["volume_exact"] = ~a["action_type"].isin(VOLUME_UNKNOWN_EVENTS)
    g = a.groupby("ex_date")
    return pd.DataFrame({
        "f_price": g["adjustment_factor"].prod(),
        "f_volume": g["f_volume"].prod(),
        "volume_exact": g["volume_exact"].all(),
        "types": g["action_type"].agg(lambda s: ",".join(sorted(s))),
    }).reset_index().sort_values("ex_date")


def adjust_prices(raw: pd.DataFrame, actions: pd.DataFrame, computed_at: str) -> pd.DataFrame:
    """raw: daily_prices_raw rows of ONE stock; actions: its corporate_actions rows."""
    if raw.empty:
        return pd.DataFrame()
    r = raw.sort_values("date").reset_index(drop=True)
    ev = event_factors(actions)
    dates = r["date"].to_numpy()
    pf = np.ones(len(r))
    vf = np.ones(len(r))
    for _, e in ev.iterrows():
        before = dates < e["ex_date"]
        pf[before] *= e["f_price"]
        vf[before] *= e["f_volume"]
    factor_as_of = ev["ex_date"].max() if not ev.empty else r["date"].max()
    has = r["has_price"].to_numpy() == 1
    out = pd.DataFrame({
        "stock_id": r["stock_id"],
        "date": r["date"],
        "adj_open": np.where(has, r["open"].astype(float) * pf, np.nan),
        "adj_high": np.where(has, r["high"].astype(float) * pf, np.nan),
        "adj_low": np.where(has, r["low"].astype(float) * pf, np.nan),
        "adj_close": np.where(has, r["close"].astype(float) * pf, np.nan),
        "adj_volume": r["volume"].astype(float) * vf,
        "price_factor": pf,
        "volume_factor": vf,
        "method": METHOD,
        "factor_as_of": factor_as_of,
        "computed_at": computed_at,
    })
    return out
