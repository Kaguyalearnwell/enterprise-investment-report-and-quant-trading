"""Infer abnormal trading days and suspension periods from the price record.

Free data has no official suspension list (TaiwanStockSuspended is a paid dataset,
Stage 2.1). What the price record does show:
  * has_price = 0           -> no published price that day (no trade or suspended)
  * no row at all            -> the vendor returned nothing for a trading day
Consecutive such trading days between a stock's first and last priced day form one
inferred period. The reason is unknown, so periods are stored with basis='inferred'
and never presented as official.
"""
from __future__ import annotations

import pandas as pd


def infer_status(raw: pd.DataFrame, trading_days: list[str], delisting_date: str | None,
                 ingested_at: str, source: str = "inferred:daily_prices_raw") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (trading_status rows, suspension_history rows) for ONE stock."""
    if raw.empty:
        return pd.DataFrame(), pd.DataFrame()
    sid = raw["stock_id"].iloc[0]
    priced = raw.loc[raw["has_price"] == 1, "date"]
    if priced.empty:
        return pd.DataFrame(), pd.DataFrame()
    first, last = priced.min(), priced.max()
    cal = [d for d in sorted(trading_days) if first <= d <= last]
    by_date = raw.set_index("date")["has_price"].to_dict()
    kinds = {}
    for d in cal:
        if d not in by_date:
            kinds[d] = "missing_row"
        elif by_date[d] == 0:
            kinds[d] = "no_published_price"
    status = pd.DataFrame([{
        "stock_id": sid, "date": d, "status": "no_price", "suspension_flag": 0, "disposition_flag": 0,
        "full_delivery_flag": 0, "delisting_flag": 0, "basis": "inferred", "detail": k, "source": source,
        "ingested_at": ingested_at} for d, k in kinds.items()])
    if delisting_date:
        status = pd.concat([status, pd.DataFrame([{
            "stock_id": sid, "date": delisting_date, "status": "delisted", "suspension_flag": 0,
            "disposition_flag": 0, "full_delivery_flag": 0, "delisting_flag": 1, "basis": "official",
            "detail": f"last published price {last}", "source": "security_master", "ingested_at": ingested_at}])],
            ignore_index=True)
        status = status.drop_duplicates(["stock_id", "date"], keep="last")
    # collapse consecutive abnormal trading days into periods
    idx = {d: i for i, d in enumerate(cal)}
    periods, cur = [], []
    for d in kinds:
        if cur and idx[d] != idx[cur[-1]] + 1:
            periods.append(cur)
            cur = []
        cur.append(d)
    if cur:
        periods.append(cur)
    susp = pd.DataFrame([{
        "stock_id": sid, "start_date": p[0], "end_date": p[-1], "trading_days": len(p), "basis": "inferred",
        "reason": ",".join(sorted({kinds[d] for d in p})), "source": source, "ingested_at": ingested_at}
        for p in periods])
    return status, susp
