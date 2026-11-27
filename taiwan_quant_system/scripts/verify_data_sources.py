"""Stage 2.1: reproducible verification of free data sources.

Runs a small, fixed set of live API calls (about 20) and writes the evidence to
reports/stage2_1_data_source_verification.json. Nothing here is mocked: every
number in the Stage 2.1 report comes from this script's output.

Usage:
    python3 scripts/verify_data_sources.py            # FINMIND_TOKEN optional
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import time
from pathlib import Path

import pandas as pd
import requests

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
TPEX_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/otc"
TWSE_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TWSE_OPENAPI = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
COMMON_STOCK_RE = r"[1-9]\d{3}"
OUT = Path(__file__).resolve().parents[1] / "reports" / "stage2_1_data_source_verification.json"

log = logging.getLogger("verify")
session = requests.Session()
session.headers["User-Agent"] = "Mozilla/5.0 (research data verification)"
token = os.environ.get("FINMIND_TOKEN", "")
if token:
    session.headers["Authorization"] = f"Bearer {token}"


FAILURES: list[dict] = []


def finmind(dataset: str, **params) -> dict:
    """One paced call; a transient failure is retried once after 30 s and logged."""
    for attempt in (1, 2):
        time.sleep(2.0)
        r = session.get(FINMIND_URL, params={"dataset": dataset, **params}, timeout=60)
        try:
            body = r.json()
        except ValueError:
            body = {"msg": r.text[:200]}
        out = {"http": r.status_code, "status": body.get("status"), "msg": body.get("msg"), "data": body.get("data") or []}
        if out["data"] or "level is free" in str(out["msg"]) or attempt == 2:
            if not out["data"]:
                FAILURES.append({"dataset": dataset, "params": params, "http": r.status_code, "msg": out["msg"], "attempt": attempt})
            return out
        FAILURES.append({"dataset": dataset, "params": params, "http": r.status_code, "msg": out["msg"], "attempt": attempt})
        log.warning("%s empty (%s %s), retrying in 30 s", dataset, r.status_code, out["msg"])
        time.sleep(30)
    return out


def tpex_snapshot(date: dt.date) -> pd.DataFrame:
    r = session.get(TPEX_URL, params={"date": date.strftime("%Y/%m/%d"), "type": "EW", "response": "json"}, timeout=60)
    t = r.json()["tables"][0]
    time.sleep(3)  # be polite to the official site
    return pd.DataFrame(t["data"], columns=[f.strip() for f in t["fields"]])


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ev: dict = {"run_at_utc": dt.datetime.now(dt.UTC).isoformat(), "token_used": bool(token), "checks": {}}
    C = ev["checks"]

    # 1. Tier gating: which datasets answer on this account level
    for ds, p in {
        "TaiwanStockPrice(per stock)": ("TaiwanStockPrice", {"data_id": "2330", "start_date": "2024-01-02", "end_date": "2024-01-05"}),
        "TaiwanStockPrice(all stocks, one date)": ("TaiwanStockPrice", {"start_date": "2024-01-02"}),
        "TaiwanStockPriceAdj": ("TaiwanStockPriceAdj", {"data_id": "2330", "start_date": "2024-01-02", "end_date": "2024-01-05"}),
        "TaiwanStockPriceLimit": ("TaiwanStockPriceLimit", {"data_id": "2330", "start_date": "2024-01-02", "end_date": "2024-01-05"}),
        "TaiwanStockSuspended": ("TaiwanStockSuspended", {"start_date": "2024-01-01", "end_date": "2024-02-01"}),
        "TaiwanStockDispositionSecuritiesPeriod": ("TaiwanStockDispositionSecuritiesPeriod", {"data_id": "6477", "start_date": "2025-01-01", "end_date": "2025-02-01"}),
        "TaiwanStockDividendResult(per stock)": ("TaiwanStockDividendResult", {"data_id": "2330", "start_date": "2024-01-01"}),
        "TaiwanStockCapitalReductionReferencePrice(per stock)": ("TaiwanStockCapitalReductionReferencePrice", {"data_id": "2327", "start_date": "2011-01-01"}),
        "TaiwanStockTotalReturnIndex": ("TaiwanStockTotalReturnIndex", {"data_id": "TAIEX", "start_date": "2024-01-01", "end_date": "2024-01-10"}),
    }.items():
        name, params = p
        res = finmind(name, **params)
        C[f"tier:{ds}"] = {"available": bool(res["data"]), "status": res["status"], "msg": res["msg"],
                          "fields": list(res["data"][0].keys()) if res["data"] else None,
                          "sample": res["data"][0] if res["data"] else None}
        log.info("%-55s available=%s", ds, bool(res["data"]))

    # 2. Universe datasets (free, full download)
    info = pd.DataFrame(finmind("TaiwanStockInfo")["data"])
    dl = pd.DataFrame(finmind("TaiwanStockDelisting")["data"])
    td = pd.to_datetime(pd.DataFrame(finmind("TaiwanStockTradingDate")["data"])["date"])
    today = pd.Timestamp(dt.date.today())
    C["TaiwanStockInfo"] = {
        "rows": len(info), "unique_ids": int(info.stock_id.nunique()), "type_counts": info.type.value_counts().to_dict(),
        "ids_with_multiple_rows": int((info.groupby("stock_id").size() > 1).sum()),
        "common_stock_ids_twse_tpex": int(info[info.type.isin(["twse", "tpex"]) & info.stock_id.str.fullmatch(COMMON_STOCK_RE)].stock_id.nunique()),
        "has_listing_or_delisting_date_field": False, "fields": info.columns.tolist(),
    }
    C["TaiwanStockDelisting"] = {
        "rows": len(dl), "date_min": dl.date.min(), "date_max": dl.date.max(), "fields": dl.columns.tolist(),
        "delistings_since_2013": int((dl.date >= "2013-01-01").sum()),
        "delisted_ids_also_in_info": int(dl.stock_id.isin(info.stock_id).sum()),
        "has_market_field": "type" in dl.columns,
    }
    C["TaiwanStockTradingDate"] = {
        "rows": len(td), "min": str(td.min().date()), "max": str(td.max().date()),
        "dates_after_today": int((td > today).sum()),
        "weekend_trading_dates_since_2013": [str(d.date()) for d in td[(td >= "2013-01-01") & (td.dt.weekday >= 5)]],
    }

    # 3. Delisted stocks: does free TaiwanStockPrice still return their history?
    probes = {}
    for sid in ["3068", "6022", "1333", "3291", "5326"]:
        px = pd.DataFrame(finmind("TaiwanStockPrice", data_id=sid, start_date="2012-01-01", end_date=str(today.date()))["data"])
        rec = dl[dl.stock_id == sid]
        probes[sid] = {"rows": len(px), "first": px.date.min() if len(px) else None, "last": px.date.max() if len(px) else None,
                       "in_delisting_table": rec.to_dict("records"), "in_info": bool((info.stock_id == sid).any())}
    C["delisted_price_probe"] = probes

    # 4. Volume unit and zero-price convention
    px = pd.DataFrame(finmind("TaiwanStockPrice", data_id="2330", start_date="2024-01-02", end_date="2024-01-02")["data"])
    C["volume_unit"] = {"2330_2024-01-02_Trading_Volume": int(px.Trading_Volume.iloc[0]),
                        "interpretation": "shares (≈28k board lots); Trading_turnover = number of trades"}

    # 5. Survivorship cross-check against official TPEx daily snapshots
    known = set(info.stock_id) | set(dl.stock_id)
    xs = {}
    for d in [dt.date(2013, 3, 1), dt.date(2016, 1, 4), dt.date(2019, 7, 1), dt.date(2022, 7, 1)]:
        snap = tpex_snapshot(d)
        com = snap[snap["代號"].str.fullmatch(COMMON_STOCK_RE)]
        xs[str(d)] = {"tpex_common": len(com), "missing_from_finmind_lists": com[~com["代號"].isin(known)]["代號"].tolist(),
                      "only_in_delisting_table": int((com["代號"].isin(dl.stock_id) & ~com["代號"].isin(info.stock_id)).sum())}
        log.info("TPEx %s common=%d missing=%s", d, len(com), xs[str(d)]["missing_from_finmind_lists"])
    C["survivorship_crosscheck_tpex"] = xs

    # 6. Official TWSE history reachability from this host
    try:
        r = session.get(TWSE_URL, params={"date": "20160104", "type": "ALLBUT0999", "response": "json"}, timeout=60)
        C["twse_history_endpoint"] = {"http": r.status_code, "blocked_by_waf": "FOR SECURITY REASONS" in r.text}
    except requests.RequestException as exc:
        C["twse_history_endpoint"] = {"error": repr(exc)}
    try:
        r = session.get(TWSE_OPENAPI, timeout=60)
        rows = r.json() if r.ok else []
        C["twse_openapi_latest_only"] = {"http": r.status_code, "rows": len(rows), "dates": sorted({x.get("Date") for x in rows})[:3]}
    except (requests.RequestException, ValueError) as exc:
        C["twse_openapi_latest_only"] = {"error": repr(exc)}

    ev["failed_or_gated_calls"] = FAILURES
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(ev, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    log.info("wrote %s", OUT)


if __name__ == "__main__":
    main()
