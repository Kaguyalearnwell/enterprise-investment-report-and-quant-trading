"""Survivorship audit against official TPEx monthly snapshots.

For the first trading day of every month the official TPEx quote list (which lists
every TPEx security that existed that day, including ones delisted later) is
compared with security_master:

  * a common stock in the snapshot but in no vendor list  -> added to security_master
    (source 'tpex_snapshot') and reported as 'missing_from_vendor_lists';
  * a delisted stock with exchange UNKNOWN that appears in a TPEx snapshot within
    3 months before its delisting date -> exchange set to TPEX (tracked revision);
    otherwise it stays UNKNOWN (it may have moved to TWSE before delisting);
  * TPEx presence periods are written to security_history (evidence 'tpex_monthly_snapshot').

TWSE (上市) cannot be audited this way from the development environment (official
site blocked, Stage 2.1). The report states this limitation explicitly.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import logging
from pathlib import Path

import pandas as pd

from backend.database.db_manager import DatabaseManager, utc_now

log = logging.getLogger(__name__)
COMMON = r"[1-9]\d{3}"


def month_first_trading_days(days: list[str], start: str, end: str) -> list[str]:
    s = pd.Series(sorted(d for d in days if start <= d <= end))
    return s.groupby(s.str[:7]).min().tolist()


class SurvivorshipAudit:
    def __init__(self, db: DatabaseManager, tpex, raw_dir: Path | None = None):
        self.db, self.tpex, self.raw_dir = db, tpex, raw_dir

    def _snapshot(self, d: str) -> pd.DataFrame:
        cache = self.raw_dir / "tpex" / "daily_quotes" / f"{d}.json.gz" if self.raw_dir else None
        if cache is not None and cache.exists():
            with gzip.open(cache, "rt", encoding="utf-8") as fh:
                return pd.DataFrame(json.load(fh)["data"])
        df = self.tpex.daily_quotes(dt.date.fromisoformat(d))
        if cache is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(cache, "wt", encoding="utf-8") as fh:
                json.dump({"date": d, "downloaded_at": utc_now(), "data": df.to_dict("records")}, fh, ensure_ascii=False)
        return df

    def run(self, start: str, end: str, run_id: str | None = None) -> dict:
        run_id = run_id or f"survivorship-{utc_now()}"
        days = self.db.get_trading_days(start, end, as_of=end)
        dates = month_first_trading_days(days, start, end)
        master = self.db.get_security_master().set_index("stock_id")
        seen: dict[str, list[str]] = {}
        names: dict[str, str] = {}
        monthly = []
        for i, d in enumerate(dates, 1):
            snap = self._snapshot(d)
            com = snap[snap["stock_id"].astype(str).str.fullmatch(COMMON)] if not snap.empty else snap
            ids = set(com["stock_id"]) if not com.empty else set()
            for sid, nm in zip(com.get("stock_id", []), com.get("stock_name", [])):
                seen.setdefault(sid, []).append(d)
                names[sid] = nm
            missing = sorted(ids - set(master.index))
            monthly.append({"date": d, "tpex_common": len(ids), "in_master": len(ids) - len(missing),
                            "missing": len(missing), "missing_ids": missing})
            if i % 12 == 0:
                log.info("survivorship audit: %d/%d snapshots", i, len(dates))
        issues, new_master, exch_updates, history = [], [], [], []
        now = utc_now()
        for sid, ds in seen.items():
            history.append({"stock_id": sid, "valid_from": min(ds), "valid_to": max(ds), "stock_name": names[sid],
                            "exchange": "TPEX", "security_type": "common", "evidence": "tpex_monthly_snapshot",
                            "source": "tpex", "ingested_at": now})
            if sid not in master.index:
                new_master.append({"stock_id": sid, "stock_name": names[sid], "exchange": "TPEX",
                                   "industry_category": None, "security_type": "common", "delisting_date": None,
                                   "source": "tpex_snapshot", "updated_at": now})
                issues.append(self._issue(run_id, sid, min(ds), "missing_from_vendor_lists",
                                          f"in official TPEx snapshots {min(ds)}..{max(ds)} but in no FinMind list"))
                continue
            m = master.loc[sid]
            if m["exchange"] == "UNKNOWN" and pd.notna(m["delisting_date"]):
                gap = (pd.Timestamp(m["delisting_date"]) - pd.Timestamp(max(ds))).days
                if 0 <= gap <= 92:
                    exch_updates.append(sid)
                else:
                    issues.append(self._issue(run_id, sid, max(ds), "exchange_still_unknown",
                                              f"last seen on TPEx {max(ds)}, delisted {m['delisting_date']} "
                                              f"({gap} days later): may have moved to TWSE", "info"))
        if new_master:
            self.db.upsert("security_master", new_master)
        if exch_updates:
            rows = master.loc[exch_updates].reset_index()[["stock_id", "stock_name", "industry_category",
                                                           "security_type", "delisting_date", "source"]]
            rows["exchange"] = "TPEX"
            rows["source"] = rows["source"] + "+tpex_snapshot"
            rows["updated_at"] = now
            self.db.upsert("security_master", rows)
        if history:
            self.db.upsert("security_history", history)
        if issues:
            self.db.insert("data_validation_log", issues)
        result = {
            "run_id": run_id, "start": start, "end": end, "snapshots": len(dates),
            "tpex_common_codes_seen": len(seen), "added_to_master": len(new_master),
            "added_ids": [r["stock_id"] for r in new_master], "exchange_resolved_to_tpex": len(exch_updates),
            "still_unknown_after_tpex": int(self.db.read_sql(
                "SELECT COUNT(*) n FROM security_master WHERE exchange='UNKNOWN' AND security_type='common'").n[0]),
            "monthly": monthly,
            "min_monthly_coverage": min((m["in_master"] / m["tpex_common"]) for m in monthly if m["tpex_common"])
            if monthly else None,
            "twse_audited": False,
            "limitation": "TWSE (上市) was not audited: the official site blocks the development environment.",
        }
        return result

    @staticmethod
    def _issue(run_id, sid, date, category, detail, severity="warning"):
        return {"run_id": run_id, "table_name": "security_master", "stock_id": sid, "date": date,
                "check_name": "survivorship_audit", "severity": severity, "category": category, "detail": detail,
                "action": "flag", "created_at": utc_now()}


def write_survivorship_report(res: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    name = res["run_id"].replace(":", "")
    (out_dir / f"{name}.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    cov = res["min_monthly_coverage"]
    lines = [
        f"# 倖存者偏誤稽核報告 `{res['run_id']}`", "",
        f"- 稽核期間：{res['start']} ~ {res['end']}，櫃買中心官方月快照 {res['snapshots']} 份",
        f"- 快照中出現過的上櫃普通股：{res['tpex_common_codes_seen']} 檔",
        f"- FinMind 清單漏列、已補入股票池：{res['added_to_master']} 檔 {', '.join(res['added_ids'][:30])}",
        f"- 下市股市場別判定為上櫃：{res['exchange_resolved_to_tpex']} 檔；仍為未知：{res['still_unknown_after_tpex']} 檔",
        f"- 補入前最低月覆蓋率：{cov:.2%}" if cov is not None else "- 覆蓋率：無資料", "",
        "## 限制（不得忽略）", "",
        "- **上市（TWSE）股票未經官方比對**：證交所網站封鎖開發環境。上市部分的下市股完整性只依賴 FinMind 下市表。",
        "- 本稽核只能證明「上櫃股於每月第一個交易日」的覆蓋情況；月中上市又下市的股票可能未被看到。",
        "- **因此不能宣稱已消除倖存者偏誤。**", "",
        "## 逐月覆蓋（補入前）", "", "| 日期 | 上櫃普通股 | 在股票池 | 漏列 |", "|---|---|---|---|",
    ]
    lines += [f"| {m['date']} | {m['tpex_common']} | {m['in_master']} | {m['missing']} |" for m in res["monthly"]]
    md = out_dir / f"{name}.md"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md
