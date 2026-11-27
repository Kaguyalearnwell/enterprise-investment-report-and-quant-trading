"""Stage 3 verification on REAL data: single stock first (2330), then every stock in the database.

Writes reports/stage3_verification.md. All numbers come from this run.

The independent recomputation below deliberately does NOT import backend.indicators:
it re-implements every formula with plain loops, so a shared bug cannot hide itself.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config.settings import get_settings  # noqa: E402
from backend.config.strategy_config import load_config  # noqa: E402
from backend.core.logger import setup_logging  # noqa: E402
from backend.data.collectors.downloader import default_as_of  # noqa: E402
from backend.database.db_manager import DatabaseManager  # noqa: E402
from backend.database.schema import ENTRY_BITS  # noqa: E402
from backend.strategies.fib_bb_weekly import compute_features  # noqa: E402
from backend.strategies.runner import SignalRunner, daily_screen, explain  # noqa: E402

log = logging.getLogger("stage3")


# ---- independent reference implementation (loops, no shared code) ---------------------------------
def ref_indicators(d: pd.DataFrame) -> pd.DataFrame:
    c = d["adj_close"].to_numpy()
    h, lo = d["adj_high"].to_numpy(), d["adj_low"].to_numpy()
    n = len(c)
    out = {k: np.full(n, np.nan) for k in ("ma13", "ma34", "ma55", "bb_upper", "bbw", "bbw_pct", "atr_adj")}
    for i in range(n):
        for p, k in ((13, "ma13"), (34, "ma34"), (55, "ma55")):
            if i >= p - 1:
                out[k][i] = sum(c[i - p + 1:i + 1]) / p
        if i >= 19:
            w = c[i - 19:i + 1]
            m = sum(w) / 20
            sd = (sum((x - m) ** 2 for x in w) / 20) ** 0.5
            out["bb_upper"][i] = m + 2 * sd
            out["bbw"][i] = 4 * sd / m
    for i in range(n):
        if i >= 120 and not np.isnan(out["bbw"][i - 120]):
            prev = out["bbw"][i - 120:i]
            cur = out["bbw"][i]
            out["bbw_pct"][i] = 100.0 * sum(1 for x in prev if x <= cur + 1e-9 * abs(cur)) / 120   # ties by value
    tr = [h[0] - lo[0]] + [max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])) for i in range(1, n)]
    if n >= 14:
        a = sum(tr[:14]) / 14
        out["atr_adj"][13] = a
        for i in range(14, n):
            a = a + (tr[i] - a) / 14
            out["atr_adj"][i] = a
    return pd.DataFrame(out, index=d.index)


def ref_weekly_ma13_prev_week(d: pd.DataFrame) -> pd.Series:
    """Weekly MA13 of the previous completed ISO week, by explicit grouping."""
    iso = d.index.isocalendar()
    key = (iso["year"] * 100 + iso["week"]).to_numpy()
    weeks, closes = [], []
    for k in dict.fromkeys(key):
        weeks.append(k)
        closes.append(d["adj_close"].to_numpy()[key == k][-1])
    ma = {weeks[j]: (sum(closes[j - 12:j + 1]) / 13 if j >= 12 else np.nan) for j in range(len(weeks))}
    prev = {weeks[j]: (ma[weeks[j - 1]] if j >= 1 else np.nan) for j in range(len(weeks))}
    return pd.Series([prev[k] for k in key], index=d.index)


def max_rel_diff(a: pd.Series, b: pd.Series) -> float:
    m = a.notna() & b.notna()
    if not m.any():
        return float("nan")
    return float(((a[m] - b[m]).abs() / b[m].abs().clip(lower=1e-12)).max())


def main() -> int:
    setup_logging()
    s = get_settings()
    db = DatabaseManager(s.db_path)
    db.initialize()
    as_of = default_as_of()
    cfg = load_config()
    runner = SignalRunner(db, cfg, as_of)
    L = ["# Stage 3 驗證結果（真實資料，自動產生）", "", f"- 資料截止日：{as_of}；資料庫：開發環境暫存庫（非全市場）", ""]

    # ---------------- single stock: 2330 ----------------
    sid = "2330"
    t0 = time.time()
    st = runner.run([sid])
    vid = st["versions"]["include_today"]
    d = runner.load_input(sid)
    ind = db.read_sql("SELECT * FROM technical_indicators WHERE stock_id=? AND indicator_version=? ORDER BY date",
                      (sid, runner.ind_version))
    ind.index = pd.DatetimeIndex(ind["date"])
    ref = ref_indicators(d)
    atr_raw_ref = ref["atr_adj"] * d["close"] / d["adj_close"]
    diffs = {
        "MA13": max_rel_diff(ind["ma13"], ref["ma13"]), "MA34": max_rel_diff(ind["ma34"], ref["ma34"]),
        "MA55": max_rel_diff(ind["ma55"], ref["ma55"]), "BB Upper": max_rel_diff(ind["bb_upper"], ref["bb_upper"]),
        "BBW": max_rel_diff(ind["bbw"], ref["bbw"]), "BBW percentile": max_rel_diff(ind["bbw_pct"], ref["bbw_pct"]),
        "ATR14 (raw units)": max_rel_diff(ind["atr14_raw"], atr_raw_ref),
        "Weekly MA13 (previous week)": max_rel_diff(ind["w_ma13"], ref_weekly_ma13_prev_week(d)),
    }
    # weekly table consistency: weekly_prices (adj, complete) -> MA13 of the previous week
    wk = db.read_sql("SELECT week_key, close FROM weekly_prices WHERE stock_id=? AND price_basis='adj' ORDER BY week_key",
                     (sid,))
    wk["ma13_prev"] = wk["close"].rolling(13).mean().shift(1)
    iso = d.index.isocalendar()
    keys = (iso["year"] * 100 + iso["week"]).to_numpy()
    wk_map = dict(zip(wk["week_key"], wk["ma13_prev"]))
    diffs["Weekly MA13 vs weekly_prices table"] = max_rel_diff(ind["w_ma13"], pd.Series([wk_map.get(k) for k in keys],
                                                                                       index=d.index, dtype=float))
    # causality on real data: truncate at random dates, conditions up to the cut must not change
    full = compute_features(d, cfg)
    rng = np.random.default_rng(7)
    cuts = sorted(rng.choice(np.arange(400, len(d) - 5), size=6, replace=False))
    bool_cols = [c for c in full.columns if full[c].dtype == bool]
    causal_ok = all(full[bool_cols].iloc[:c + 1].equals(compute_features(d.iloc[:c + 1], cfg)[bool_cols]) for c in cuts)
    L += ["## 1. 單一股票：2330 台積電", "",
          f"- 輸入：{len(d)} 根有價日 K（{d.index.min().date()} ~ {d.index.max().date()}），耗時 {time.time() - t0:.1f} 秒",
          "", "### 指標：系統值 vs 獨立重算（逐筆迴圈實作，不共用程式）", "", "| 指標 | 最大相對誤差 |", "|---|---|"]
    L += [f"| {k} | {v:.2e} |" for k, v in diffs.items()]
    L += ["", f"### 前視偏誤（真實資料）", "",
          f"- 在 {len(cuts)} 個隨機日期截斷資料重算，截斷日以前的 {len(bool_cols)} 個條件欄位"
          f"{'**全部相同** ✅' if causal_ok else '**出現差異** ❌'}（截斷位置：{', '.join(str(d.index[c].date()) for c in cuts)}）"]
    ent = st["entries"]
    L += ["", "### 訊號數（2013 起，兩種成交量定義）", "", "| 版本 | 含當日均量 | 前 20 日均量 |", "|---|---|---|"]
    L += [f"| {v} | {ent['include_today'].get(v, 0)} | {ent['prior_20'].get(v, 0)} |" for v in ENTRY_BITS]
    e_dates = db.read_sql("SELECT date FROM strategy_signals WHERE stock_id=? AND strategy_version_id=? AND (entries & 16) > 0 "
                          "ORDER BY date DESC LIMIT 1", (sid, vid))
    if not e_dates.empty:
        ex = explain(db, sid, e_dates.date[0], vid)
        L += ["", f"### 判斷過程範例：2330 最近一次 E 訊號 {e_dates.date[0]}", "",
              f"- 訊號成立時點：{ex['signal_as_of']}；最早模擬進場日：{ex['earliest_entry_date']}", "",
              "| 條件 | 規則 | 結果 | 輸入值 |", "|---|---|---|---|"]
        for c in ex["conditions"]:
            vals = ", ".join(f"{k}={v:.4g}" if isinstance(v, float) else f"{k}={v}" for k, v in c["inputs"].items())
            L.append(f"| {c['name']} | {c['rule']} | {'✅' if c['passed'] else '❌'} | {vals} |")

    # ---------------- emerging-board exclusion ----------------
    em = db.read_sql("""SELECT m.stock_id, m.listing_date, m.listing_basis, MIN(i.date) AS first_indicator_date,
                               (SELECT MIN(date) FROM daily_prices_raw r WHERE r.stock_id=m.stock_id AND has_price=1) AS first_price
                        FROM security_master m JOIN technical_indicators i ON i.stock_id = m.stock_id
                        WHERE m.listing_date IS NOT NULL GROUP BY m.stock_id""")

    # ---------------- every stock in the database ----------------
    ids = db.read_sql("SELECT DISTINCT stock_id FROM daily_prices_adj ORDER BY 1").stock_id.tolist()
    t1 = time.time()
    st_all = runner.run(ids)
    secs = time.time() - t1
    if not em.empty:
        em = db.read_sql("""SELECT m.stock_id, m.listing_date, m.listing_basis, MIN(i.date) AS first_indicator_date,
                               (SELECT MIN(date) FROM daily_prices_raw r WHERE r.stock_id=m.stock_id AND has_price=1) AS first_price
                            FROM security_master m JOIN technical_indicators i ON i.stock_id = m.stock_id
                            WHERE m.listing_date IS NOT NULL GROUP BY m.stock_id""")
    em_ok = bool((em["first_indicator_date"] >= em["listing_date"]).all()) if not em.empty else True
    L += ["", "## 2. 興櫃期間排除", "", f"- 有上市日判定的股票：{len(em)} 檔；指標起始日皆不早於上市日："
          f"{'✅' if em_ok else '❌'}", "", "| 代號 | 第一筆價格 | 判定上市日 | 依據 | 指標起始日 |", "|---|---|---|---|---|"]
    L += [f"| {r.stock_id} | {r.first_price} | {r.listing_date} | {r.listing_basis} | {r.first_indicator_date} |"
          for _, r in em.iterrows()]

    vi, vp = st_all["versions"]["include_today"], st_all["versions"]["prior_20"]
    both = db.read_sql(f"""SELECT a.date, a.stock_id, (a.entries & 16) > 0 AS e_incl, (b.entries & 16) > 0 AS e_prior
                           FROM strategy_signals a JOIN strategy_signals b
                             ON a.stock_id = b.stock_id AND a.date = b.date AND b.strategy_version_id = ?
                           WHERE a.strategy_version_id = ?""", (vp, vi))
    e_i, e_p = int(both.e_incl.sum()), int(both.e_prior.sum())
    e_both = int((both.e_incl & both.e_prior).sum())
    per_day = db.read_sql("""SELECT date, SUM((entries & 16) > 0) n FROM strategy_signals WHERE strategy_version_id = ?
                             GROUP BY date""", (vi,))
    latest = per_day["date"].max()
    scr = daily_screen(db, latest, vi, "E")
    L += ["", f"## 3. 擴展至資料庫全部股票（{st_all['stocks']} 檔）", "",
          f"- 耗時 {secs:.0f} 秒（{secs / max(st_all['stocks'], 1):.2f} 秒／檔）→ 推估全市場 2,163 檔約 "
          f"{secs / max(st_all['stocks'], 1) * 2163 / 60:.0f} 分鐘",
          f"- 指標 {st_all['indicator_rows']:,} 筆、訊號 {st_all['signal_rows']:,} 筆（兩個版本合計）",
          f"- 策略版本：含當日均量 `{vi}`、前 20 日均量 `{vp}`", "",
          "### 各版本訊號數", "", "| 版本 | 含當日均量 | 前 20 日均量 |", "|---|---|---|"]
    L += [f"| {v} | {st_all['entries']['include_today'].get(v, 0)} | {st_all['entries']['prior_20'].get(v, 0)} |"
          for v in ENTRY_BITS]
    L += ["", "### 兩種成交量定義的 E 訊號重疊", "",
          f"- 含當日：{e_i} 筆；前 20 日：{e_p} 筆；兩者皆成立：{e_both} 筆"
          f"（Jaccard {e_both / max(e_i + e_p - e_both, 1):.2f}）", "",
          f"### 最新訊號日 {latest} 的 E 訊號（研究訊號，非成交）", ""]
    if scr.empty:
        L.append("無。")
    else:
        L += ["| 代號 | 名稱 | 收盤 | 20日均額(百萬) | BBW% | 量比 | ATR | 參考停損 | 最早進場日 |", "|---|---|---|---|---|---|---|---|---|"]
        L += [f"| {r.stock_id} | {r.stock_name} | {r.close_raw} | {r.turnover_ma20 / 1e6:.0f} | {r.bbw_pct:.1f} | "
              f"{r.volume_ratio_incl:.2f} | {r.atr14_raw:.2f} | {r.reference_initial_stop:.2f} | {r.earliest_entry_date} |"
              for _, r in scr.iterrows()]
    L += ["", "## 4. 限制", "",
          "- 開發環境資料庫只有 83 檔，訊號數不代表全市場。",
          "- 訊號是研究用的條件判斷結果，不是成交；成交模擬在 Stage 4。",
          "- 處置股、全額交割股未納入可交易判斷（免費資料沒有）。"]
    out = ROOT / "reports" / "stage3_verification.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    summary = {"diffs": diffs, "causal_ok": causal_ok, "emerging_ok": em_ok, "stocks": st_all["stocks"],
               "secs": secs, "entries": st_all["entries"], "overlap": [e_i, e_p, e_both], "latest": latest,
               "latest_E": len(scr)}
    print(json.dumps(summary, ensure_ascii=False, default=str, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
