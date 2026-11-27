"""Stage 6.1: reproducible verification of FinMind fundamental datasets (free / register tier).

Writes reports/stage6_1_fundamental_verification.json. About 15 paced API calls.
Questions answered from live data (not assumed):
  * which fundamental datasets are usable, their fields and date semantics;
  * whether an ANNOUNCEMENT date exists (point-in-time use in backtests);
  * coverage for delisted stocks (survivorship of fundamental data);
  * observed month-revenue availability vs the statutory 10th-of-next-month deadline.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path

import pandas as pd
import requests

URL = "https://api.finmindtrade.com/api/v4/data"
OUT = Path(__file__).resolve().parents[1] / "reports" / "stage6_1_fundamental_verification.json"
S = requests.Session()
S.headers["User-Agent"] = "taiwan-quant-research/verify"
if os.environ.get("FINMIND_TOKEN"):
    S.headers["Authorization"] = f"Bearer {os.environ['FINMIND_TOKEN']}"


def get(dataset: str, **p) -> dict:
    time.sleep(2.0)
    r = S.get(URL, params={"dataset": dataset, **p}, timeout=60)
    b = r.json()
    return {"http": r.status_code, "msg": b.get("msg"), "data": b.get("data") or []}


def summary(res: dict) -> dict:
    d = pd.DataFrame(res["data"])
    out = {"http": res["http"], "msg": res["msg"], "rows": len(d), "fields": list(d.columns)}
    if len(d):
        out["date_min"], out["date_max"] = d["date"].min(), d["date"].max()
        if "type" in d:
            out["n_types"] = int(d["type"].nunique())
            out["types_sample"] = sorted(d["type"].unique())[:25]
        out["has_announcement_field"] = any(c.lower().startswith("announce") or c == "create_time" for c in d.columns)
    return out


def main() -> None:
    ev = {"run_at_utc": dt.datetime.now(dt.UTC).isoformat(), "checks": {}}
    C = ev["checks"]
    for ds in ("TaiwanStockFinancialStatements", "TaiwanStockBalanceSheet", "TaiwanStockCashFlowsStatement"):
        C[f"{ds} 2330"] = summary(get(ds, data_id="2330", start_date="2023-01-01", end_date="2024-12-31"))
    C["TaiwanStockFinancialStatements 2881 (financial)"] = summary(get("TaiwanStockFinancialStatements", data_id="2881",
                                                                       start_date="2024-01-01", end_date="2024-06-30"))
    C["TaiwanStockFinancialStatements 3068 (delisted 2018)"] = summary(get("TaiwanStockFinancialStatements", data_id="3068",
                                                                           start_date="2013-01-01"))
    C["TaiwanStockMonthRevenue 3068 (delisted 2018)"] = summary(get("TaiwanStockMonthRevenue", data_id="3068",
                                                                    start_date="2013-01-01"))
    C["TaiwanStockDividend 2330"] = summary(get("TaiwanStockDividend", data_id="2330", start_date="2020-01-01"))
    C["TaiwanStockPER 2330"] = summary(get("TaiwanStockPER", data_id="2330", start_date="2024-01-02", end_date="2024-01-10"))
    C["TaiwanStockFinancialStatements all stocks one date"] = summary(get("TaiwanStockFinancialStatements",
                                                                          start_date="2024-03-31"))
    # month revenue: observed availability (create_time exists from 2026-04-21; that first day is a back-fill value)
    obs = []
    for sid in ("2330", "2317", "5347", "1101", "2881"):
        d = pd.DataFrame(get("TaiwanStockMonthRevenue", data_id=sid, start_date="2026-04-01")["data"])
        d = d[(d.get("create_time", "") != "") & (d.get("create_time", "") != "2026-04-21")] if len(d) else d
        for r in d.itertuples(index=False):
            obs.append({"stock_id": sid, "revenue_month": f"{r.revenue_year}-{int(r.revenue_month):02d}",
                        "create_time": r.create_time, "day_of_month": int(r.create_time[8:10])})
    o = pd.DataFrame(obs)
    C["month_revenue_observed_availability"] = {
        "observations": len(o), "after_10th": int((o["day_of_month"] > 10).sum()) if len(o) else None,
        "after_15th": int((o["day_of_month"] > 15).sum()) if len(o) else None,
        "max_day_of_month": int(o["day_of_month"].max()) if len(o) else None, "rows": obs}
    for host in ("https://mops.twse.com.tw/mops/web/index",):
        try:
            r = S.get(host, timeout=30)
            C[f"reachability {host}"] = {"http": r.status_code, "blocked_by_waf": "FOR SECURITY REASONS" in r.text}
        except requests.RequestException as exc:
            C[f"reachability {host}"] = {"error": repr(exc)}
    OUT.write_text(json.dumps(ev, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: {kk: v.get(kk) for kk in ("rows", "msg", "date_min", "date_max", "has_announcement_field",
                                                    "after_10th", "after_15th", "max_day_of_month", "blocked_by_waf")
                          if kk in v} for k, v in C.items()}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
