"""Run all data-quality checks, store issues, write a report (Markdown + JSON).

Issues go to `data_validation_log` with one run_id per execution, so earlier runs
are never overwritten and the Dashboard can show how quality changes over time.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from backend.data.validators import checks
from backend.database.db_manager import DatabaseManager, utc_now

log = logging.getLogger(__name__)


class DataQualityValidator:
    def __init__(self, db: DatabaseManager, as_of: str):
        self.db, self.as_of = db, as_of

    def _load(self, stock_ids: Sequence[str] | None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[str]]:
        prices = self.db.get_daily_prices_raw(stock_ids, "1990-01-01", self.as_of)
        sql, params = "SELECT * FROM corporate_actions", []
        if stock_ids:
            sql += f" WHERE stock_id IN ({', '.join('?' for _ in stock_ids)})"
            params = list(stock_ids)
        actions = self.db.read_sql(sql, params)
        master = self.db.get_security_master()
        if stock_ids:
            master = master[master["stock_id"].isin(stock_ids)]
        days = self.db.get_trading_days("1990-01-01", self.as_of, as_of=self.as_of)
        return prices, actions, master, days

    def run(self, stock_ids: Sequence[str] | None = None, run_id: str | None = None,
            extra: Sequence[pd.DataFrame] = ()) -> tuple[str, pd.DataFrame]:
        run_id = run_id or f"dq-{utc_now()}"
        prices, actions, master, days = self._load(stock_ids)
        if not days:
            raise RuntimeError("trading_calendar is empty: run `download reference` first")
        parts = [
            checks.check_ohlc(prices), checks.check_no_price_days(prices), checks.check_volume(prices),
            checks.check_duplicates(prices), checks.check_calendar(prices, days, self.as_of),
            checks.check_returns(prices, actions), checks.check_stock_ids(master),
            checks.check_corporate_actions(actions, prices, days), *extra,
        ]
        issues = pd.concat([x for x in parts if not x.empty], ignore_index=True) if any(
            not x.empty for x in parts) else pd.DataFrame(columns=checks.ISSUE_COLUMNS)
        if not issues.empty:
            self.db.insert("data_validation_log", issues.assign(run_id=run_id, created_at=utc_now()))
        log.info("data quality run %s: %d issues", run_id, len(issues))
        return run_id, issues

    def write_report(self, run_id: str, issues: pd.DataFrame, out_dir: Path, scope: Sequence[str] | None) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        summ = checks.summarize(issues)
        counts = self.db.row_counts()
        stocks = sorted(issues["stock_id"].dropna().unique()) if not issues.empty else []
        lines = [
            f"# 資料品質報告 `{run_id}`", "",
            f"- 資料截止日（as-of）：{self.as_of}",
            f"- 檢查範圍：{'、'.join(scope) if scope else '全部股票'}",
            f"- 問題總數：{summ['total']}（{', '.join(f'{k}: {v}' for k, v in summ['by_severity'].items()) or '無'}）",
            "- 原則：檢查只分類與記錄，**不刪除任何資料**；`action` 為建議處置（keep／flag／exclude）。", "",
            "## 各檢查結果", "", "| 檢查 | 類別 | 嚴重度 | 筆數 |", "|---|---|---|---|",
        ]
        for r in summ["by_check"]:
            lines.append(f"| {r['check_name']} | {r['category']} | {r['severity']} | {r['n']} |")
        if not issues.empty:
            lines += ["", "## 各股票問題數", "", "| 股票 | error | warning | info |", "|---|---|---|---|"]
            piv = issues.pivot_table(index="stock_id", columns="severity", values="check_name", aggfunc="count",
                                     fill_value=0)
            for sid, r in piv.iterrows():
                lines.append(f"| {sid} | {r.get('error', 0)} | {r.get('warning', 0)} | {r.get('info', 0)} |")
            lines += ["", "## 需要注意的問題（error 與 warning，最多 60 筆）", "",
                      "| 股票 | 日期 | 檢查 | 類別 | 說明 | 建議 |", "|---|---|---|---|---|---|"]
            serious = issues[issues["severity"].isin(["error", "warning"])].sort_values(["stock_id", "date"])
            for _, r in serious.head(60).iterrows():
                lines.append(f"| {r.stock_id} | {r.date or ''} | {r.check_name} | {r.category} | "
                             f"{str(r.detail).replace('|', '/')} | {r.action} |")
        lines += ["", "## 資料表筆數", "", "| 資料表 | 筆數 |", "|---|---|"]
        lines += [f"| {t} | {n:,} |" for t, n in counts.items()]
        md = out_dir / f"{run_id.replace(':', '')}.md"
        md.write_text("\n".join(lines) + "\n", encoding="utf-8")
        (out_dir / f"{run_id.replace(':', '')}.json").write_text(
            json.dumps({"run_id": run_id, "as_of": self.as_of, "scope": list(scope or []), "summary": summ,
                        "stocks_with_issues": stocks, "row_counts": counts}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        return md
