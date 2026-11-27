"""Determine when a stock started trading on TWSE/TPEx (as opposed to the emerging board 興櫃).

Why (Stage 2 acceptance, real data): FinMind's TaiwanStockPrice also returns the
EMERGING-board history of stocks that later listed (e.g. 6488 環球晶 2014-10 .. 2015-09,
6531 愛普 2015-06 .. 2016-05). On the emerging board there is no opening auction: FinMind
documents that `open` holds the PREVIOUS DAY'S AVERAGE price, so open often lies outside
[low, high], and there is no daily price limit. These bars must not be traded as if listed.

Evidence used, strongest first:
  1. `emerging_exit_date` from TaiwanStockInfo's retained 'emerging' row (date it left the board);
  2. the price record: an initial chain of bars whose open lies outside [low, high]
     (each such bar within `max_gap` bars of the previous one, starting near the first bar);
  3. the first official TPEx monthly snapshot containing the stock (upper bound, monthly granularity).
The listing date is the first priced bar after the latest emerging evidence. Raw bars before it
are kept (never deleted) and classified as pre-listing by the validator.
"""
from __future__ import annotations

import pandas as pd

TOL = 1e-9


def emerging_like(raw: pd.DataFrame) -> pd.Series:
    p = raw[raw["has_price"] == 1]
    return (p["open"] > p["high"] + TOL) | (p["open"] < p["low"] - TOL) | (p["open"] <= 0)


def emerging_prefix_end(raw: pd.DataFrame, start_within: int = 10, max_gap: int = 30, min_bad: int = 3) -> str | None:
    """Last date of an emerging-like chain that starts within the first `start_within` priced bars.

    A chain needs at least `min_bad` emerging-like bars, so one bad bar near the start of an
    already-listed stock's history is reported by the validator instead of being taken for an
    emerging-board period (6488 has 84 such bars, 6531 has 91).
    """
    p = raw[raw["has_price"] == 1].sort_values("date").reset_index(drop=True)
    if p.empty:
        return None
    flags = emerging_like(p).to_numpy()
    pos = [i for i, f in enumerate(flags) if f]
    if not pos or pos[0] >= start_within:
        return None
    end = pos[0]
    for i in pos[1:]:
        if i - end > max_gap:
            break
        end = i
    if sum(1 for i in pos if i <= end) < min_bad:
        return None
    return str(p.loc[end, "date"])


def resolve_listing(raw: pd.DataFrame, emerging_exit_date: str | None,
                    tpex_first_seen: str | None) -> dict:
    """Return {'listing_date', 'listing_basis', 'emerging_to'} for ONE stock (listing_date None = not determined)."""
    p = raw[raw["has_price"] == 1].sort_values("date")
    if p.empty:
        return {"listing_date": None, "listing_basis": None, "emerging_to": None}
    prefix_end = emerging_prefix_end(raw)
    candidates = [(d, b) for d, b in [(emerging_exit_date, "emerging_exit"), (prefix_end, "inferred_from_prices")] if d]
    if not candidates:
        return {"listing_date": None, "listing_basis": None, "emerging_to": None}
    emerging_to, basis = max(candidates)
    after = p.loc[p["date"] > emerging_to, "date"]
    listing = after.iloc[0] if not after.empty else None
    if tpex_first_seen and listing and listing > tpex_first_seen:
        # the official snapshot already shows the stock on TPEx: trust it (monthly granularity)
        listing, basis = tpex_first_seen, "tpex_first_seen"
    return {"listing_date": listing, "listing_basis": basis, "emerging_to": emerging_to}
