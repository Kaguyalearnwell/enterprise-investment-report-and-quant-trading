"""Stage 4 verification on REAL data (development database, 72 stocks - NOT the full market).

Writes reports/stage4_verification.md. Mechanical checks use the whole period; PERFORMANCE
is shown for the In-Sample segment only: Validation / OOS metrics are stored in the database
but sealed until Stage 5 pre-registers its evaluation thresholds (Prompt 04 §9.7).
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.backtesting.backtester import Backtester  # noqa: E402
from backend.config.settings import get_settings  # noqa: E402
from backend.config.strategy_config import load_config  # noqa: E402
from backend.core.logger import setup_logging  # noqa: E402
from backend.data.collectors.downloader import default_as_of  # noqa: E402
from backend.data.pipeline import DataPipeline  # noqa: E402
from backend.database.db_manager import DatabaseManager  # noqa: E402
from backend.strategies.runner import SignalRunner  # noqa: E402

log = logging.getLogger("stage4")
SHOW = ["trades", "win_rate", "win_rate_ci_low", "win_rate_ci_high", "profit_factor", "expectancy_r",
        "average_trade_return", "cagr", "max_drawdown", "sharpe", "sortino", "exposure_mean", "average_holding_days",
        "total_costs"]


def checks(db: DatabaseManager, run_id: str, cfg: dict) -> dict:
    q = lambda s: db.read_sql(s, (run_id,))  # noqa: E731
    daily = q("SELECT * FROM portfolio_daily WHERE run_id=? ORDER BY date")
    trades = q("SELECT * FROM simulated_trades WHERE run_id=?")
    cash_sum = q("SELECT SUM(amount) s FROM portfolio_cash WHERE run_id=?").s[0]
    days = db.get_trading_days("2010-01-01", "2099-12-31", as_of="2099-12-31")
    nxt = dict(zip(days[:-1], days[1:]))
    a = cfg["account"]
    out = {
        "ledger_reconciles": abs(cash_sum - daily["cash"].iloc[-1]) < 1e-4,
        "equity_identity": bool(((daily["equity"] - daily["cash"] - daily["market_value"]).abs() < 1e-4).all()),
        "never_filled_on_signal_day": bool((trades["entry_date"] > trades["signal_date"]).all()) if len(trades) else True,
        "filled_next_trading_day": bool((trades["entry_date"] == trades["signal_date"].map(nxt)).all()) if len(trades) else True,
        "whole_lots_before_2020_10_26": bool((trades.loc[trades.entry_date < "2020-10-26", "shares"] % 1000 == 0).all()),
        "max_exposure_ok": bool((daily["exposure"] <= a["max_gross_exposure"] + 0.02).all()),
        # Prompt 03/04: the 5% limit applies to the INITIAL risk taken at entry -> check on entry days
        "max_open_risk_ok": bool((daily[daily["date"].isin(set(trades["entry_date"]))].pipe(
            lambda x: x["open_risk"] / x["equity"]) <= a["max_portfolio_open_risk"] + 0.005).all()) if len(trades) else True,
        "cash_never_negative": bool((daily["cash"] >= -1e-6).all()),
    }
    if len(trades):
        eq_at = daily.set_index("date")["equity"]
        prev = {d: eq_at.get(p) for p, d in zip(days[:-1], days[1:])}
        w = trades.apply(lambda r: (r.invested / prev.get(r.entry_date)) if prev.get(r.entry_date) else 0.0, axis=1)
        out["max_weight_at_entry"] = float(w.max())
        out["max_weight_ok"] = bool((w <= a["max_weight_per_stock"] + 0.005).all())
    return out


def is_metrics(db, run_id) -> dict:
    m = db.read_sql("SELECT metric, value FROM performance_metrics WHERE run_id=? AND segment='IS'", (run_id,))
    return dict(zip(m.metric, m.value))


def main() -> int:
    setup_logging()
    s = get_settings()
    db = DatabaseManager(s.db_path)
    db.initialize()
    cfg = load_config()
    as_of = default_as_of()
    pipe = DataPipeline(db, as_of)
    ids = db.read_sql("SELECT DISTINCT stock_id FROM strategy_signals ORDER BY 1").stock_id.tolist()
    run_id_dq, _, issues = pipe.validate(ids, s.reports_dir / "data_quality")
    verdicts = db.read_sql("SELECT verdict, COUNT(*) n FROM data_quality_verdicts WHERE run_id=? GROUP BY 1", (run_id_dq,))
    pipe.snapshot_version("stage 4 verification")
    runner = SignalRunner(db, cfg, as_of)
    v_incl, v_prior = runner.register_version("include_today"), runner.register_version("prior_20")
    bt = Backtester(db, cfg)
    runs: dict[str, str] = {}

    def go(label, **kw):
        out = bt.run(start="2015-01-01", **kw)
        runs[label] = out["run_id"]
        return out

    single = go("single 2330 E", strategy_version_id=v_incl, entry_version="E", stock_ids=["2330"], mode="single_stock")
    for v in ("A", "B", "C", "D", "E", "S0"):
        go(f"portfolio {v}", strategy_version_id=v_incl, entry_version=v)
    go("portfolio E prior_20", strategy_version_id=v_prior, entry_version="E")
    for sc in ("optimistic", "base", "stress"):
        for end in ("low", "high"):
            if (sc, end) != ("base", "low"):
                go(f"E {sc}/{end}", strategy_version_id=v_incl, entry_version="E", cost_scenario=sc, slippage_end=end)
    runs["E base/low"] = runs["portfolio E"]
    go("E raw-basis trailing", strategy_version_id=v_incl, entry_version="E", trailing_basis="raw")

    excluded = json.loads(db.read_sql("SELECT excluded FROM backtest_runs WHERE run_id=?",
                                      (runs["portfolio E"],)).excluded[0])
    seg = db.read_sql("SELECT start_date, end_date FROM backtest_runs WHERE run_id=?", (runs["portfolio E"],)).iloc[0]
    L = ["# Stage 4 驗證結果（真實資料，自動產生）", "",
         f"- 資料截止日 {as_of}；回測期間 {seg.start_date} ~ {seg.end_date}；開發環境 72 檔（**非全市場**）",
         f"- 資料品質判定：{', '.join(f'{r.verdict} {r.n}' for r in verdicts.itertuples())}；"
         f"排除 FAIL {len(excluded.get('FAIL', []))} 檔 {excluded.get('FAIL', [])}、未驗證 {len(excluded.get('no_verdict', []))} 檔",
         "- **績效只顯示 In-Sample 區段**：Validation 與 OOS 已計算並存入資料庫，但在 Stage 5 預先登記評估門檻前不揭露，"
         "以免樣本外資料被用於調整策略。", ""]
    # mechanical checks
    L += ["## 1. 交易機制檢查（全期間、每一個回測）", "", "| 回測 | 現金帳 | 資產恆等式 | 不在訊號日成交 | 次一交易日成交 | 整張（進場時） | 曝險≤90% | 進場日投組風險≤5% | 單一持股≤10% | 現金≥0 |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    all_ok = True
    for label, rid in runs.items():
        c = checks(db, rid, cfg)
        keys = ["ledger_reconciles", "equity_identity", "never_filled_on_signal_day", "filled_next_trading_day",
                "whole_lots_before_2020_10_26", "max_exposure_ok", "max_open_risk_ok", "max_weight_ok", "cash_never_negative"]
        all_ok &= all(c.get(k, True) for k in keys)
        L.append(f"| {label} | " + " | ".join("✅" if c.get(k, True) else "❌" for k in keys) + " |")
    L += ["", f"全部通過：{'✅' if all_ok else '❌'}", ""]
    # skipped / exit reasons for portfolio E
    tr = db.read_sql("SELECT exit_reason, COUNT(*) n FROM simulated_trades WHERE run_id=? GROUP BY 1 ORDER BY 2 DESC",
                     (runs["portfolio E"],))
    sk = {k: v for k, v in bt_skipped(db, runs["portfolio E"]).items()}
    L += ["## 2. 投組 E：出場原因與未成交原因（全期間）", "", "| 出場原因 | 筆數 |", "|---|---|"]
    L += [f"| {r.exit_reason} | {r.n} |" for r in tr.itertuples()]
    L += ["", "| 進場結果 | 次數 |", "|---|---|"] + [f"| {k} | {v} |" for k, v in sorted(sk.items())]
    # performance, IS only
    def table(labels):
        rows = ["| 回測 | " + " | ".join(SHOW) + " |", "|" + "---|" * (len(SHOW) + 1)]
        for lb in labels:
            m = is_metrics(db, runs[lb])
            rows.append(f"| {lb} | " + " | ".join("" if m.get(k) is None else f"{m[k]:.4g}" for k in SHOW) + " |")
        return rows
    L += ["", "## 3. In-Sample 績效：消融版本（基準成本、滑價下限）", ""] + table(
        [f"portfolio {v}" for v in ("A", "B", "C", "D", "E", "S0")] + ["portfolio E prior_20"])
    L += ["", "## 4. In-Sample：E 版本成本敏感度", ""] + table(
        ["E optimistic/low", "E optimistic/high", "E base/low", "E base/high", "E stress/low", "E stress/high"])
    L += ["", "## 5. In-Sample：移動停損基礎（使用者決定 C1 的比較）", ""] + table(["E base/low", "E raw-basis trailing"])
    L += ["", "## 6. 個股回測：2330（In-Sample 期間的交易）", ""] + table(["single 2330 E"])
    t = db.read_sql("SELECT signal_date, entry_date, entry_price, initial_stop, shares, exit_date, exit_price, exit_reason, "
                    "ROUND(pnl) pnl, ROUND(r_multiple, 2) r FROM simulated_trades WHERE run_id=? AND entry_date <= "
                    "(SELECT MAX(date) FROM portfolio_daily WHERE run_id=? AND date <= ?) ORDER BY entry_date",
                    (runs["single 2330 E"], runs["single 2330 E"], is_end(db, runs["single 2330 E"])))
    if not t.empty:
        L += ["", "| 訊號日 | 進場日 | 進場價 | 初始停損 | 股數 | 出場日 | 出場價 | 原因 | 損益 | R |", "|---|---|---|---|---|---|---|---|---|---|"]
        L += [f"| {r.signal_date} | {r.entry_date} | {r.entry_price} | {r.initial_stop:.2f} | {r.shares:.0f} | {r.exit_date} | "
              f"{r.exit_price} | {r.exit_reason} | {r.pnl:.0f} | {r.r} |" for r in t.itertuples()]
    L += ["", "## 7. 解讀限制", "",
          "- 72 檔是開發測試用的樣本（含刻意挑選的困難案例），**不代表全市場，也不能用來判斷策略是否有效**。",
          "- 勝率信賴區間很寬時，代表交易筆數不足以下結論。",
          "- 處置股、全額交割股未模擬；產業上限使用目前產業分類；除權息、減資對持股的影響以官方參考價做價值保留近似。",
          "- 現金股利未扣稅（個人股利所得稅與二代健保依個人狀況而定）。"]
    out = ROOT / "reports" / "stage4_verification.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(json.dumps({"all_mechanical_checks_ok": all_ok, "runs": runs}, indent=1))
    return 0


def bt_skipped(db, run_id) -> dict:
    p = json.loads(db.read_sql("SELECT params FROM backtest_runs WHERE run_id=?", (run_id,)).params[0])
    return p.get("skipped", {})


def is_end(db, run_id) -> str:
    from backend.backtesting.performance import split_segments
    days = db.read_sql("SELECT date FROM portfolio_daily WHERE run_id=? ORDER BY date", (run_id,)).date.tolist()
    return split_segments(days)["IS"][1]


if __name__ == "__main__":
    sys.exit(main())
