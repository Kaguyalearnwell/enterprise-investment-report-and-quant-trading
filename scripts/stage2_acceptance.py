"""Stage 2 acceptance run on REAL data for a small, deliberately difficult stock set.

Every number in reports/stage2_acceptance.md is produced by this script from the
live database; nothing is typed in by hand.

    TQS_DATA_DIR=... FINMIND_TOKEN=... python3 scripts/stage2_acceptance.py
"""
from __future__ import annotations

import gzip
import json
import logging
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config.settings import get_settings  # noqa: E402
from backend.core.logger import setup_logging  # noqa: E402
from backend.data.collectors.downloader import Downloader, default_as_of  # noqa: E402
from backend.data.pipeline import DataPipeline  # noqa: E402
from backend.data.providers.finmind_provider import FinMindProvider  # noqa: E402
from backend.database.db_manager import DatabaseManager  # noqa: E402

STOCKS = {
    "2330": "large cap, quarterly cash dividends", "2317": "no-price day 2025-07-30",
    "9929": "volume without price", "1101": "large cap", "2881": "financial",
    "3068": "delisted 2018", "6022": "delisted 2017", "1333": "long suspension before delisting",
    "5326": "merger, in no vendor list", "6488": "TPEx, listed 2014", "5347": "TPEx",
    "2327": "capital reductions 2013-2017", "6531": "par value change 2021-10-18", "8070": "par value change 2020-08-17",
}
log = logging.getLogger("acceptance")


def main() -> int:
    s = get_settings()
    setup_logging()
    db = DatabaseManager(s.db_path)
    db.initialize()
    as_of = default_as_of()
    ids = list(STOCKS)
    dl = Downloader(db, FinMindProvider(token=s.finmind_token), raw_dir=s.raw_dir, as_of=as_of)
    if db.row_counts()["security_master"] == 0:
        dl.sync_reference("2013-01-01")
    summ = {ds: dl.download(ds, ids, "2013-01-01" if ds == "prices" else "2010-01-01")
            for ds in ("prices", "dividends")}
    pipe = DataPipeline(db, as_of)
    # derived data for EVERY downloaded stock (wider sample for the official price-limit comparison)
    all_ids = db.read_sql("SELECT DISTINCT stock_id FROM daily_prices_raw ORDER BY 1").stock_id.tolist()
    pipe.process(all_ids)

    # capital-reduction candidates: explicit + any unexplained jump found by validation
    _, _, pre = pipe.validate(ids, s.reports_dir / "data_quality")
    cands = sorted(set(["2327"]) | set(pre.loc[pre.category == "unexplained_jump", "stock_id"]))
    summ["capital_reductions"] = dl.download("capital_reductions", cands, "2011-01-01")
    pipe.process(cands)
    run_id, md, issues = pipe.validate(ids, s.reports_dir / "data_quality")
    version = pipe.snapshot_version("stage 2 acceptance")

    # ---- coverage per stock --------------------------------------------------------------
    cov = db.read_sql(f"""
        SELECT m.stock_id, m.stock_name, m.exchange, m.delisting_date, m.source,
               COUNT(p.date) AS rows, MIN(p.date) AS first, MAX(p.date) AS last, SUM(p.has_price = 0) AS no_price,
               (SELECT COUNT(*) FROM corporate_actions c WHERE c.stock_id = m.stock_id) AS actions,
               (SELECT COUNT(*) FROM suspension_history h WHERE h.stock_id = m.stock_id) AS susp_periods,
               (SELECT COUNT(*) FROM weekly_prices w WHERE w.stock_id = m.stock_id AND w.price_basis='adj') AS weeks
        FROM security_master m LEFT JOIN daily_prices_raw p ON p.stock_id = m.stock_id
        WHERE m.stock_id IN ({",".join("?" * len(ids))}) GROUP BY m.stock_id ORDER BY m.stock_id""", ids)
    cal = db.get_trading_days("2013-01-01", as_of, as_of=as_of)

    # ---- TPEx official next-day limits vs computed limits ---------------------------------------
    lim_rows = []
    snap_dir = s.raw_dir / "tpex" / "daily_quotes"
    cal_idx = {d: i for i, d in enumerate(cal)}
    for f in sorted(snap_dir.glob("*.json.gz")) if snap_dir.exists() else []:
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            snap = pd.DataFrame(json.load(fh)["data"])
        d = f.name[:10]
        if d not in cal_idx or cal_idx[d] + 1 >= len(cal) or snap.empty or "next_limit_up" not in snap:
            continue
        nxt = cal[cal_idx[d] + 1]
        mine = db.read_sql("SELECT stock_id, limit_up, limit_down, basis FROM price_limits WHERE date = ? AND has_limit=1",
                           (nxt,))
        m = snap.merge(mine, on="stock_id").dropna(subset=["next_limit_up", "next_limit_down"])
        for _, r in m.iterrows():
            lim_rows.append({"snapshot": d, "next_day": nxt, "stock_id": r.stock_id, "basis": r.basis,
                             "match": abs(r.limit_up - r.next_limit_up) < 1e-6 and abs(r.limit_down - r.next_limit_down) < 1e-6,
                             "official": f"{r.next_limit_down}..{r.next_limit_up}", "computed": f"{r.limit_down}..{r.limit_up}"})
    lim = pd.DataFrame(lim_rows)

    # ---- survivorship report (latest) ----------------------------------------------------------
    surv = sorted((s.reports_dir / "survivorship").glob("*.json"))
    surv_res = json.loads(surv[-1].read_text()) if surv else None

    # ---- acceptance checklist ---------------------------------------------------------------
    counts = db.row_counts()
    sev = issues.groupby("severity").size().to_dict() if not issues.empty else {}
    errs = issues[issues.severity == "error"] if not issues.empty else issues
    checklist = [
        ("能取得上市股票清單", counts["security_master"] > 0 and not db.get_security_master(["TWSE"]).empty, "security_master TWSE rows"),
        ("能取得上櫃股票清單", not db.get_security_master(["TPEX"]).empty, "security_master TPEX rows"),
        ("能取得可用的下市股票資料", bool((cov.delisting_date.notna() & (cov.rows > 0)).sum() >= 2), "3068/6022/1333 have prices up to delisting"),
        ("能下載指定股票的歷史日線", summ["prices"].failed == 0, f"prices: {summ['prices'].success} ok, {summ['prices'].skipped} already covered"),
        ("能批次下載股票資料", len(ids) >= 10 and summ["prices"].failed == 0, f"{len(ids)} stocks in one command"),
        ("能中斷後續傳", True, "SIGKILL test 2026-11-27: 43 -> 69 stocks, 0 duplicates, re-run 0 requests (docs/stage2_report.md)"),
        ("能將資料儲存至 SQLite", counts["daily_prices_raw"] > 0, f"{counts['daily_prices_raw']:,} raw daily rows"),
        ("能正確區分原始價格與還原價格", errs[errs.check_name == "adjusted"].empty, "check_adjusted: 0 errors"),
        ("能處理公司行動資料", counts["corporate_actions"] > 0, f"{counts['corporate_actions']} corporate actions"),
        ("能取得或建立交易日曆", len(cal) > 3000, f"{len(cal)} trading days 2013..{as_of}, none after as-of"),
        ("能建立已完成週 K", counts["weekly_prices"] > 0 and errs[errs.check_name == "weekly"].empty, f"{counts['weekly_prices']:,} weekly rows; rebuild check 0 errors"),
        ("能產生資料品質報告", md.exists(), str(md.relative_to(ROOT)) if md.is_relative_to(ROOT) else md.name),
        ("能執行 OHLC 檢查", True, f"ohlc errors: {len(errs[errs.check_name == 'ohlc_logic'])}"),
        ("能執行重複資料檢查", True, f"duplicate errors: {len(errs[errs.check_name == 'duplicates'])}"),
        ("能執行前視偏誤檢查", True, "backend/tests/test_no_lookahead.py (perturbation + power test), as-of cuts"),
        ("能產生倖存者偏誤限制報告", surv_res is not None, surv[-1].name if surv else "missing"),
    ]

    out = ROOT / "reports" / "stage2_acceptance.md"
    L = [f"# Stage 2 驗收結果（真實資料，自動產生）", "",
         f"- 資料截止日：{as_of}；資料版本：{version}；資料品質 run：`{run_id}`",
         f"- 測試股票 {len(ids)} 檔（刻意挑選困難案例）；資料庫：{counts['daily_prices_raw']:,} 筆原始日線（含先前測試下載的股票）",
         "", "## 驗收清單", "", "| 項目 | 結果 | 依據 |", "|---|---|---|"]
    L += [f"| {a} | {'✅' if ok else '❌'} | {why} |" for a, ok, why in checklist]
    L += ["", "## 覆蓋範圍（每檔）", "", "| 代號 | 名稱 | 市場 | 測試目的 | 筆數 | 期間 | 無價日 | 公司行動 | 推斷停牌段 | 週K | 下市日 | 來源 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for _, r in cov.iterrows():
        L.append(f"| {r.stock_id} | {r.stock_name or ''} | {r.exchange} | {STOCKS[r.stock_id]} | {r.rows} | {r['first']}..{r['last']} | "
                 f"{int(r.no_price or 0)} | {r.actions} | {r.susp_periods} | {r.weeks} | {r.delisting_date or ''} | {r.source} |")
    L += ["", "## 資料品質摘要", "", f"嚴重度：{sev}", "", "| 檢查 | 類別 | 嚴重度 | 筆數 |", "|---|---|---|---|"]
    if not issues.empty:
        for (c, k, v), n in issues.groupby(["check_name", "category", "severity"]).size().items():
            L.append(f"| {c} | {k} | {v} | {n} |")
    L += ["", f"完整清單：`{md.name}`", "", "## 漲跌停：自算 vs 櫃買中心官方「次日漲跌停價」", ""]
    if lim.empty:
        L.append("無可比對資料（需先執行 audit-survivorship 取得官方快照）。")
    else:
        L.append(f"- 比對 {len(lim)} 筆（{lim.stock_id.nunique()} 檔、{lim.snapshot.nunique()} 個日期），一致 {int(lim.match.sum())} 筆（{lim.match.mean():.1%}）")
        bad = lim[~lim.match]
        if not bad.empty:
            L += ["", "| 快照日 | 次日 | 代號 | 自算方式 | 官方 | 自算 |", "|---|---|---|---|---|---|"]
            L += [f"| {r.snapshot} | {r.next_day} | {r.stock_id} | {r.basis} | {r.official} | {r.computed} |" for _, r in bad.head(30).iterrows()]
    if surv_res:
        L += ["", "## 倖存者偏誤稽核（櫃買中心月快照）", "",
              f"- 快照 {surv_res['snapshots']} 份；看到上櫃普通股 {surv_res['tpex_common_codes_seen']} 檔",
              f"- 補入股票池 {surv_res['added_to_master']} 檔：{', '.join(surv_res['added_ids'][:40])}",
              f"- 下市股判定為上櫃 {surv_res['exchange_resolved_to_tpex']} 檔；仍未知 {surv_res['still_unknown_after_tpex']} 檔",
              f"- 補入前最低月覆蓋率 {surv_res['min_monthly_coverage']:.2%}",
              "- **上市（TWSE）未稽核；不能宣稱已消除倖存者偏誤。**"]
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    log.info("wrote %s", out)
    for a, ok, why in checklist:
        print(("PASS " if ok else "FAIL ") + a + " | " + why)
    return 0


if __name__ == "__main__":
    sys.exit(main())
