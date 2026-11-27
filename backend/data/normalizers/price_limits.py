"""Daily price limits (漲跌停價).

Rules (TWSE/TPEx ordinary shares):
* reference price = last published close before the day (prices are RAW, never adjusted);
* limit up   = reference * (1 + pct), rounded DOWN to the tick of that price level;
* limit down = reference * (1 - pct), rounded UP to the tick of that price level;
* pct = 7% before 2015-06-01, 10% from 2015-06-01;
* on an ex-rights / ex-dividend / capital-reduction / par-value-change day the
  exchange publishes the reference price and limits -> those official values are used.

Known gap (documented, not guessed): newly listed stocks have no price limit for
their first trading days; the exact rule history is not verified here, so such
days are computed like any other and are surfaced by the 'price outside limits' check.
Decimal arithmetic avoids float rounding errors at tick boundaries.
"""
from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

import pandas as pd

LIMIT_CHANGE_DATE = "2015-06-01"
_TICKS = [(Decimal(10), Decimal("0.01")), (Decimal(50), Decimal("0.05")), (Decimal(100), Decimal("0.1")),
          (Decimal(500), Decimal("0.5")), (Decimal(1000), Decimal("1"))]


def tick_size(price: float | Decimal) -> Decimal:
    p = Decimal(str(price))
    for upper, tick in _TICKS:
        if p < upper:
            return tick
    return Decimal("5")


def _round_to_tick(x: Decimal, rounding: str) -> Decimal:
    t = tick_size(x)
    return (x / t).to_integral_value(rounding=rounding) * t


def limit_prices(reference: float, date: str) -> tuple[float, float, float]:
    pct = Decimal("0.07") if date < LIMIT_CHANGE_DATE else Decimal("0.10")
    ref = Decimal(str(reference))
    up = _round_to_tick(ref * (1 + pct), ROUND_FLOOR)
    down = _round_to_tick(ref * (1 - pct), ROUND_CEILING)
    return float(up), float(down), float(pct)


NEW_LISTING_FREE_DAYS = 5   # evidence: official TPEx 'next-day limits' show no limit for 1294, 1295, 6488 right after listing


def compute_limits(raw: pd.DataFrame, actions: pd.DataFrame, ingested_at: str,
                   listing_date: str | None = None) -> pd.DataFrame:
    """raw: daily_prices_raw of ONE stock; actions: its corporate_actions (official event-day limits).

    listing_date (when known): bars before it are emerging-board bars (no limit), and the first
    NEW_LISTING_FREE_DAYS priced bars from it have no limit either.
    """
    if raw.empty:
        return pd.DataFrame()
    r = raw.sort_values("date").reset_index(drop=True)
    last_close = r["close"].where(r["has_price"] == 1).ffill().shift(1)
    official = {}
    if not actions.empty:
        for _, a in actions.iterrows():
            if pd.notna(a.get("limit_up")) and pd.notna(a.get("limit_down")) and pd.notna(a.get("reference_price")):
                official[a["ex_date"]] = (float(a["reference_price"]), float(a["limit_up"]), float(a["limit_down"]))
    free_days: set[str] = set()
    if listing_date:
        priced = r.loc[(r["has_price"] == 1) & (r["date"] >= listing_date), "date"]
        free_days = set(r.loc[r["date"] < listing_date, "date"]) | set(priced.head(NEW_LISTING_FREE_DAYS))
    rows = []
    for d, ref in zip(r["date"], last_close):
        if d in free_days:
            rows.append((d, None if pd.isna(ref) else float(ref), None, None, 0, None, "computed"))
        elif d in official:
            ref_o, up, down = official[d]
            pct = 0.07 if d < LIMIT_CHANGE_DATE else 0.10
            rows.append((d, ref_o, up, down, 1, pct, "official_event"))
        elif pd.notna(ref):
            up, down, pct = limit_prices(float(ref), d)
            rows.append((d, float(ref), up, down, 1, pct, "computed"))
        else:
            rows.append((d, None, None, None, 0, None, "computed"))     # first bar: no reference known
    out = pd.DataFrame(rows, columns=["date", "reference_price", "limit_up", "limit_down", "has_limit", "limit_pct",
                                      "basis"])
    out.insert(0, "stock_id", r["stock_id"].iloc[0])
    out["source"] = "derived:daily_prices_raw+corporate_actions"
    out["ingested_at"] = ingested_at
    return out.astype(object).where(out.notna(), None)
