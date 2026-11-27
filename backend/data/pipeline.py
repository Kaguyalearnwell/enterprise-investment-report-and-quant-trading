"""Derived-data pipeline and data versioning.

    raw vendor data (daily_prices_raw, corporate_actions)
        -> daily_prices_adj   (backward_ratio_v1)
        -> price_limits       (computed + official event-day values)
        -> trading_status / suspension_history (inferred)
        -> weekly_prices      (raw + adj)
        -> validation         (data_validation_log + report)
        -> data_versions      (fingerprint of what the research used)

Derived tables are rebuilt from vendor data and a versioned method, so their
upserts do not write data_revisions (a new dividend rescales a stock's whole
adjusted history; vendor-data revisions remain fully tracked).
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from backend.config.data_sources import FINMIND_API_URL, FINMIND_DATASETS, FINMIND_REQUESTS_PER_HOUR, TPEX_DAILY_QUOTES_URL
from backend.data.normalizers.adjustments import adjust_prices
from backend.data.normalizers.price_limits import compute_limits
from backend.data.normalizers.trading_status import infer_status
from backend.data.normalizers.weekly_prices import build_weekly
from backend.data.validators import checks
from backend.data.validators.data_quality import DataQualityValidator
from backend.database.db_manager import DatabaseManager, utc_now

log = logging.getLogger(__name__)


class DataPipeline:
    def __init__(self, db: DatabaseManager, as_of: str):
        self.db, self.as_of = db, as_of

    def _stock_data(self, sid: str) -> tuple[pd.DataFrame, pd.DataFrame]:
        raw = self.db.get_daily_prices_raw([sid], "1990-01-01", self.as_of)
        act = self.db.read_sql("SELECT * FROM corporate_actions WHERE stock_id = ? AND ex_date <= ?", (sid, self.as_of))
        return raw, act

    def process(self, stock_ids: Sequence[str]) -> dict[str, int]:
        days = self.db.get_trading_days("1990-01-01", self.as_of, as_of=self.as_of)
        delist = self.db.read_sql("SELECT stock_id, delisting_date FROM security_master").set_index("stock_id")
        n = {"stocks": 0, "adj_rows": 0, "limit_rows": 0, "status_rows": 0, "suspension_periods": 0, "weekly_rows": 0}
        for i, sid in enumerate(stock_ids, 1):
            raw, act = self._stock_data(sid)
            if raw.empty:
                continue
            now = utc_now()
            adj = adjust_prices(raw, act, now)
            self.db.upsert("daily_prices_adj", adj, track_revisions=False)
            lim = compute_limits(raw, act, now)
            self.db.upsert("price_limits", lim, track_revisions=False)
            dd = delist["delisting_date"].get(sid) if sid in delist.index else None
            st, sp = infer_status(raw, days, dd if pd.notna(dd) else None, now)
            with self.db.transaction() as conn:   # inferred periods are rebuilt from scratch each time
                conn.execute("DELETE FROM suspension_history WHERE stock_id = ? AND basis = 'inferred'", (sid,))
                conn.execute("DELETE FROM trading_status WHERE stock_id = ? AND basis = 'inferred'", (sid,))
            if not st.empty:
                self.db.upsert("trading_status", st, track_revisions=False)
            if not sp.empty:
                self.db.upsert("suspension_history", sp, track_revisions=False)
            daily = raw.merge(adj[["stock_id", "date", "adj_open", "adj_high", "adj_low", "adj_close", "adj_volume"]],
                              on=["stock_id", "date"], how="left")
            wk = build_weekly(daily, days, self.as_of, now)
            if not wk.empty:
                self.db.upsert("weekly_prices", wk, track_revisions=False)
            n["stocks"] += 1
            n["adj_rows"] += len(adj)
            n["limit_rows"] += len(lim)
            n["status_rows"] += len(st)
            n["suspension_periods"] += len(sp)
            n["weekly_rows"] += len(wk)
            if i % 50 == 0:
                log.info("processed %d/%d stocks", i, len(stock_ids))
        return n

    def validate(self, stock_ids: Sequence[str] | None, report_dir: Path) -> tuple[str, Path, pd.DataFrame]:
        ids = list(stock_ids) if stock_ids else self.db.read_sql(
            "SELECT DISTINCT stock_id FROM daily_prices_raw").stock_id.tolist()
        days = self.db.get_trading_days("1990-01-01", self.as_of, as_of=self.as_of)
        extra = []
        for sid in ids:
            raw, act = self._stock_data(sid)
            adj = self.db.get_daily_prices_adj([sid], "1990-01-01", self.as_of)
            lim = self.db.read_sql("SELECT * FROM price_limits WHERE stock_id = ? AND date <= ?", (sid, self.as_of))
            extra += [checks.check_adjusted(adj, raw, act), checks.check_price_limits(raw, lim)]
            stored = self.db.read_sql("SELECT * FROM weekly_prices WHERE stock_id = ?", (sid,))
            if not stored.empty:
                daily = raw.merge(adj[["stock_id", "date", "adj_open", "adj_high", "adj_low", "adj_close",
                                       "adj_volume"]], on=["stock_id", "date"], how="left")
                rebuilt = build_weekly(daily, days, stored["built_as_of"].max(), utc_now())
                extra.append(checks.check_weekly(stored.drop(columns=["computed_at"]),
                                                 rebuilt.drop(columns=["computed_at"])))
        v = DataQualityValidator(self.db, self.as_of)
        run_id, issues = v.run(ids, extra=[e for e in extra if not e.empty])
        md = v.write_report(run_id, issues, report_dir, ids if stock_ids else None)
        return run_id, md, issues

    def snapshot_version(self, description: str) -> int:
        counts = self.db.row_counts()
        last_rev = self.db.read_sql("SELECT MAX(revision_id) m FROM data_revisions").m[0]
        stamps = {t: self.db.read_sql(f'SELECT MAX(ingested_at) m FROM "{t}"').m[0]
                  for t in ("daily_prices_raw", "corporate_actions", "trading_calendar")}
        fingerprint = json.dumps({"counts": counts, "last_revision": None if pd.isna(last_rev) else int(last_rev),
                                  "stamps": stamps, "as_of": self.as_of}, sort_keys=True, default=str)
        h = hashlib.sha256(fingerprint.encode()).hexdigest()
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO data_versions (created_at, as_of, description, row_counts, last_revision_id, content_hash) "
                "VALUES (?,?,?,?,?,?)", (utc_now(), self.as_of, description, json.dumps(counts),
                                        None if pd.isna(last_rev) else int(last_rev), h))
            return int(cur.lastrowid)


def seed_source_registry(db: DatabaseManager, verified_on: str = "2026-11-27") -> int:
    """Data-source facts verified in Stage 2.1 (docs/stage2_1_data_sources.md)."""
    now = utc_now()
    rows = []
    for name, spec in FINMIND_DATASETS.items():
        rows.append({
            "source_id": f"finmind:{name}", "name": name, "provider": "FinMind", "access_method": "HTTPS JSON API",
            "endpoint": FINMIND_API_URL, "tier_required": "free/register" if spec.free_tier else "backer/sponsor",
            "coverage_start": spec.coverage_start, "coverage_note": None if spec.coverage_start else "not documented",
            "fields": json.dumps(list(spec.fields)) if spec.fields else None,
            "usage_limits": f"{FINMIND_REQUESTS_PER_HOUR['anonymous']}/h anonymous, "
                            f"{FINMIND_REQUESTS_PER_HOUR['register']}/h with free token (docs); "
                            "HTTP 402 when exceeded; observed 402 after ~60 requests/h from the cloud environment"
                            + ("; free tier must query one stock per request" if spec.per_stock_only else ""),
            "update_frequency": "Mon-Fri after close (TaiwanStockPrice 17:30 Taipei, per docs)",
            "completeness_notes": {
                "TaiwanStockInfo": "not a current-listings table: keeps long-delisted ids; no listing/delisting dates",
                "TaiwanStockDelisting": "no exchange field; misses mergers/share swaps (e.g. 5326)",
                "TaiwanStockTradingDate": "contains FUTURE dates; must be cut at as-of",
                "TaiwanStockPrice": "includes delisted stocks; OHLC=0 on days without a published price",
                "TaiwanStockDividendResult": "cash+stock events give one combined value",
            }.get(name),
            "verification": "live API call (scripts/verify_data_sources.py) + official docs" if spec.free_tier
            else "live call returned 'Your level is free/register'; fields from official docs only",
            "verified_on": verified_on, "target_table": spec.target_table, "updated_at": now,
        })
    rows.append({
        "source_id": "tpex:daily_quotes", "name": "上櫃股票每日收盤行情", "provider": "TPEx (official)",
        "access_method": "HTTPS JSON", "endpoint": TPEX_DAILY_QUOTES_URL, "tier_required": "public",
        "coverage_start": None, "coverage_note": "verified for 2013-03-01 .. 2022-07-01 sample dates",
        "fields": json.dumps(["代號", "名稱", "收盤", "漲跌", "開盤", "最高", "最低", "成交股數", "成交金額(元)", "成交筆數",
                              "最後買價", "最後賣價", "發行股數", "次日漲停價", "次日跌停價"], ensure_ascii=False),
        "usage_limits": "undocumented; client waits >= 3 s between requests", "update_frequency": "daily",
        "completeness_notes": "TPEx only; includes securities later delisted", "verification": "live call",
        "verified_on": verified_on, "target_table": "security_history", "updated_at": now})
    rows.append({
        "source_id": "twse:history", "name": "TWSE 歷史行情 / ISIN", "provider": "TWSE (official)",
        "access_method": "HTTPS", "endpoint": "https://www.twse.com.tw/", "tier_required": "public",
        "coverage_start": None, "coverage_note": None, "fields": None, "usage_limits": None,
        "update_frequency": None, "completeness_notes": "BLOCKED from the development environment (WAF)",
        "verification": "live call returned the WAF block page", "verified_on": verified_on,
        "target_table": None, "updated_at": now})
    return db.upsert("data_source_registry", rows).written
