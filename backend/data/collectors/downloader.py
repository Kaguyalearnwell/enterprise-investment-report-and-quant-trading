"""Resumable, incremental downloader.

How resume works
----------------
Every request is recorded in `data_download_log` (dataset, stock, start, end, status).
Before fetching, the downloader computes what is still missing from the log:

    already covered  [cov_start ........ cov_end]
    requested       [start ............................ end]
    fetched           [start, cov_start-1]       head gap (history extended back)
                                [cov_end - overlap, end]   tail (new days + revision window)

Each stock is written and logged in its own transaction, so an interruption
(Ctrl-C, network loss, container restart) loses at most the stock in flight.
Re-running the same command continues where it stopped. 'failed' requests are
not counted as covered and are retried on the next run.

The overlap window re-downloads the last few days on every incremental update,
so a vendor correction is caught and recorded in `data_revisions`.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from backend.data.providers.base_provider import DataProvider, DatasetNotAvailable, FetchResult, ProviderError
from backend.database.db_manager import DatabaseManager, utc_now
from backend.core.retry import TransientError
from backend.data.normalizers.security_master import build_security_master

log = logging.getLogger(__name__)

DATASETS = {
    # name: (provider method, target table, dataset id in data_download_log, overlap days)
    "prices": ("daily_prices", "daily_prices_raw", "TaiwanStockPrice", 7),
    "dividends": ("dividends", "corporate_actions", "TaiwanStockDividendResult", 60),
    "capital_reductions": ("capital_reductions", "corporate_actions", "TaiwanStockCapitalReductionReferencePrice", 60),
    # fundamentals (Stage 6.2): statements can be restated / filed late -> long overlap window
    "income": ("income_statements", "financial_statements", "TaiwanStockFinancialStatements", 400),
    "balance": ("balance_sheets", "financial_statements", "TaiwanStockBalanceSheet", 400),
    "cashflow": ("cash_flows", "financial_statements", "TaiwanStockCashFlowsStatement", 400),
    "revenue": ("month_revenue", "monthly_revenue", "TaiwanStockMonthRevenue", 62),
    "dividend_policy": ("dividend_policy", "dividend_policy", "TaiwanStockDividend", 400),
    "per": ("valuation", "valuation_daily", "TaiwanStockPER", 7),
}
FUNDAMENTAL_TABLES = {"financial_statements", "monthly_revenue", "dividend_policy"}


TAIPEI = dt.timezone(dt.timedelta(hours=8))
SAFE_AFTER_HOUR = 18   # FinMind docs: TaiwanStockPrice updated Mon-Fri 17:30 Taipei time


def default_as_of(now: dt.datetime | None = None) -> str:
    """Last date whose daily data is final: today after 18:00 Taipei time, else yesterday.

    Before the vendor's documented update time, today's bar may be provisional
    (Stage 2.3 live test saw a same-day bar at 14:23 Taipei time).
    """
    now = (now or dt.datetime.now(dt.UTC)).astimezone(TAIPEI)
    day = now.date() if now.hour >= SAFE_AFTER_HOUR else now.date() - dt.timedelta(days=1)
    return day.isoformat()


def _d(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def _s(d: dt.date) -> str:
    return d.isoformat()


@dataclass
class RunSummary:
    dataset: str
    stocks: int = 0
    requests: int = 0
    skipped: int = 0
    success: int = 0
    empty: int = 0
    failed: int = 0
    gated: bool = False
    rows_inserted: int = 0
    rows_updated: int = 0
    failures: list[str] = field(default_factory=list)


class Downloader:
    def __init__(self, db: DatabaseManager, provider: DataProvider, raw_dir: Path | None = None,
                 as_of: str | None = None, clock: Callable[[], float] = time.monotonic,
                 fundamentals_cfg: dict | None = None):
        self.db, self.provider, self.raw_dir = db, provider, raw_dir
        self.as_of = as_of or default_as_of()
        self._clock = clock
        if fundamentals_cfg is None:
            from backend.config.strategy_config import load_config

            fundamentals_cfg = load_config()["fundamentals"]
        self.fcfg = fundamentals_cfg
        self._calendar = None

    def _with_availability(self, table: str, df: pd.DataFrame) -> pd.DataFrame:
        """Attach available_from / availability_basis (point-in-time rules, backend/data/normalizers/availability.py)."""
        from backend.data.normalizers import availability as av

        if self._calendar is None:
            self._calendar = av.Calendar(self.db.get_trading_days("1990-01-01", "2100-12-31", as_of="2100-12-31"))
        cal, c = self._calendar, self.fcfg
        if table == "financial_statements":
            pairs = [av.statement_available(p, cal, c) for p in df["period_end"]]
        elif table == "monthly_revenue":
            pairs = [av.revenue_available(m, t, cal, c) for m, t in zip(df["revenue_month"], df["create_time"])]
        else:
            pairs = [av.announcement_available(d, t, cal, c) for d, t in zip(df["announcement_date"], df["announcement_time"])]
        out = df.assign(available_from=[p[0] for p in pairs], availability_basis=[p[1] for p in pairs])
        dropped = out["available_from"].isna().sum()
        if dropped:      # beyond the known calendar: not usable yet; kept out until a later run
            log.info("%s: %d rows not yet available (date beyond the trading calendar)", table, dropped)
        return out[out["available_from"].notna()]

    # ---- raw archive -----------------------------------------------------------------
    def _archive(self, dataset: str, key: str, start: str, end: str, res: FetchResult) -> None:
        if self.raw_dir is None:
            return
        path = self.raw_dir / self.provider.name / dataset / f"{key}_{start}_{end}.json.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"dataset": dataset, "key": key, "start": start, "end": end, "downloaded_at": utc_now(),
                   "http_status": res.http_status, "data": res.raw}
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)

    def _stamp(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.assign(ingested_at=utc_now())

    # ---- reference data ----------------------------------------------------------------
    def sync_reference(self, start: str = "2013-01-01", run_id: str | None = None) -> dict[str, str]:
        """Security list, delistings, calendar, splits / par-value changes, benchmark indices."""
        run_id = run_id or f"reference-{utc_now()}"
        status: dict[str, str] = {}

        def step(name: str, fn: Callable[[], int]) -> None:
            try:
                n = fn()
                status[name] = f"ok ({n} rows)"
                self.db.log_download(name, "*", start, self.as_of, "success" if n else "empty", row_count=n)
            except DatasetNotAvailable as exc:
                status[name] = "gated"
                self.db.log_download(name, "*", start, self.as_of, "gated", error_message=str(exc)[:500])
            except (TransientError, ProviderError) as exc:
                status[name] = f"failed: {exc}"
                self.db.log_download(name, "*", start, self.as_of, "failed", error_message=str(exc)[:500])
                log.error("%s failed: %s", name, exc)

        def securities() -> int:
            lst, dl = self.provider.security_list(), self.provider.delisted_securities()
            self._archive("TaiwanStockInfo", "all", start, self.as_of, lst)
            self._archive("TaiwanStockDelisting", "all", start, self.as_of, dl)
            master, issues = build_security_master(lst.data, dl.data, self.provider.name, utc_now())
            res = self.db.upsert("security_master", master)
            if not issues.empty:
                self.db.insert("data_validation_log", issues.assign(run_id=run_id, created_at=utc_now()))
            log.info("security_master: %s; %d reconciliation issues", res, len(issues))
            return res.written + res.unchanged

        def calendar() -> int:
            cal = self.provider.trading_calendar(self.as_of)
            self._archive("TaiwanStockTradingDate", "all", start, self.as_of, cal)
            self.db.upsert("trading_calendar", self._stamp(cal.data))
            return len(cal.data)

        def market_actions() -> int:
            ca = self.provider.market_corporate_actions(start, self.as_of)
            self._archive("MarketCorporateActions", "all", start, self.as_of, ca)
            unmapped = ca.data.attrs.get("unmapped", [])
            if unmapped:
                self.db.insert("data_validation_log", [{
                    "run_id": run_id, "table_name": "corporate_actions", "stock_id": str(u.get("stock_id")),
                    "date": u.get("date"), "check_name": "unmapped_action_type", "severity": "warning",
                    "category": "unknown_type", "detail": json.dumps(u, ensure_ascii=False), "action": "flag",
                    "created_at": utc_now()} for u in unmapped])
            if not ca.data.empty:
                self.db.upsert("corporate_actions", self._stamp(ca.data))
            return len(ca.data)

        def benchmarks() -> int:
            n = 0
            for idx in ("TAIEX", "TPEx"):
                b = self.provider.benchmark_index(idx, start, self.as_of)
                self._archive("TaiwanStockTotalReturnIndex", idx, start, self.as_of, b)
                if not b.data.empty:
                    self.db.upsert("benchmark_index", self._stamp(b.data))
                n += len(b.data)
            return n

        step("security_master", securities)
        step("TaiwanStockTradingDate", calendar)
        step("market_corporate_actions", market_actions)
        step("TaiwanStockTotalReturnIndex", benchmarks)
        return status

    # ---- universe ----------------------------------------------------------------------
    def universe(self, start: str, markets: Sequence[str] = ("TWSE", "TPEX", "UNKNOWN")) -> list[str]:
        """Common stocks that were alive at any time in [start, as_of], including delisted ones."""
        df = self.db.read_sql(
            f"""SELECT stock_id FROM security_master
                WHERE security_type = 'common' AND exchange IN ({", ".join("?" for _ in markets)})
                  AND (delisting_date IS NULL OR delisting_date >= ?) ORDER BY stock_id""",
            (*markets, start))
        return df["stock_id"].tolist()

    # ---- planning ----------------------------------------------------------------------
    def plan(self, dataset: str, stock_id: str, start: str, end: str, force: bool = False) -> list[tuple[str, str]]:
        _, _, log_name, overlap = DATASETS[dataset]
        end = min(end, self.as_of)
        delist = self.db.read_sql("SELECT delisting_date FROM security_master WHERE stock_id = ?", (stock_id,))
        ddate = delist["delisting_date"].iloc[0] if len(delist) else None
        if ddate is not None and ddate < start:
            return []                                   # dead before the requested window
        if ddate is not None:
            end = min(end, ddate)
        if start > end:
            return []
        done = self.db.read_sql(
            "SELECT start_date, end_date FROM data_download_log WHERE dataset = ? AND stock_id = ? "
            "AND status IN ('success','empty')", (log_name, stock_id))
        if force or done.empty:
            return [(start, end)]
        cov_start, cov_end = done["start_date"].min(), done["end_date"].max()
        ranges: list[tuple[str, str]] = []
        if start < cov_start:
            ranges.append((start, _s(_d(cov_start) - dt.timedelta(days=1))))
        delisted_and_covered = ddate is not None and cov_end >= ddate
        if end > cov_end and not delisted_and_covered:
            ranges.append((max(start, _s(_d(cov_end) - dt.timedelta(days=overlap))), end))
        return ranges

    # ---- per-stock download ------------------------------------------------------------
    def download(self, dataset: str, stock_ids: Sequence[str], start: str, end: str | None = None,
                 force: bool = False, progress_every: int = 10) -> RunSummary:
        method, table, log_name, _ = DATASETS[dataset]
        end = end or self.as_of
        summary = RunSummary(dataset, stocks=len(stock_ids))
        t0 = self._clock()
        try:
            for i, sid in enumerate(stock_ids, 1):
                ranges = self.plan(dataset, sid, start, end, force)
                if not ranges:
                    summary.skipped += 1
                for s, e in ranges:
                    self._fetch_one(dataset, method, table, log_name, sid, s, e, summary)
                    if summary.gated:
                        return summary
                if i % progress_every == 0 or i == len(stock_ids):
                    rate = summary.requests / max(self._clock() - t0, 1e-9)
                    remaining = len(stock_ids) - i
                    eta = remaining / rate / 60 if rate > 0 and summary.requests else 0.0
                    log.info("[%s] %d/%d stocks | requests %d | ok %d empty %d failed %d skipped %d | ETA %.0f min",
                             dataset, i, len(stock_ids), summary.requests, summary.success, summary.empty,
                             summary.failed, summary.skipped, eta)
        except KeyboardInterrupt:
            log.warning("interrupted by user; completed work is saved, re-run the same command to resume")
            raise
        return summary

    def _fetch_one(self, dataset: str, method: str, table: str, log_name: str, sid: str, s: str, e: str,
                   summary: RunSummary) -> None:
        summary.requests += 1
        try:
            res: FetchResult = getattr(self.provider, method)(sid, s, e)
        except DatasetNotAvailable as exc:
            summary.gated = True
            self.db.log_download(log_name, sid, s, e, "gated", error_message=str(exc)[:500])
            log.error("%s is not available on this account tier: %s", log_name, exc)
            return
        except (TransientError, ProviderError) as exc:
            summary.failed += 1
            summary.failures.append(f"{sid} {s}..{e}: {exc}")
            self.db.log_download(log_name, sid, s, e, "failed", error_message=str(exc)[:500])
            log.error("%s %s %s..%s failed: %s", log_name, sid, s, e, exc)
            return
        self._archive(log_name, sid, s, e, res)
        if res.data.empty:
            summary.empty += 1
            self.db.log_download(log_name, sid, s, e, "empty", http_status=res.http_status)
            return
        if table == "daily_prices_raw":
            self._ensure_master(sid)
        data = self._with_availability(table, res.data) if table in FUNDAMENTAL_TABLES else res.data
        if data.empty:
            summary.empty += 1
            self.db.log_download(log_name, sid, s, e, "empty", http_status=res.http_status)
            return
        up = self.db.upsert(table, self._stamp(data))
        summary.rows_inserted += up.inserted
        summary.rows_updated += up.updated
        if table == "daily_prices_raw":
            self._update_price_span(sid)
        summary.success += 1
        self.db.log_download(log_name, sid, s, e, "success", row_count=len(res.data), http_status=res.http_status)

    def _ensure_master(self, sid: str) -> None:
        """Prices for a code unknown to both lists (e.g. 5326) still get a master row, flagged."""
        if self.db.read_sql("SELECT 1 FROM security_master WHERE stock_id = ?", (sid,)).empty:
            from backend.data.providers.finmind_provider import classify_security

            self.db.upsert("security_master", [{"stock_id": sid, "stock_name": None, "exchange": "UNKNOWN",
                                                "industry_category": None, "security_type": classify_security(sid),
                                                "delisting_date": None, "source": "downloader:auto",
                                                "updated_at": utc_now()}])
            self.db.insert("data_validation_log", [{
                "run_id": f"download-{utc_now()}", "table_name": "security_master", "stock_id": sid, "date": None,
                "check_name": "universe_reconciliation", "severity": "warning", "category": "not_in_any_list",
                "detail": "price data exists but code is in neither listing nor delisting table", "action": "flag",
                "created_at": utc_now()}])

    def _update_price_span(self, sid: str) -> None:
        """Derived, not vendor data: first/last date with a published price."""
        with self.db.transaction() as conn:
            conn.execute(
                """UPDATE security_master SET
                       first_price_date = (SELECT MIN(date) FROM daily_prices_raw WHERE stock_id = ? AND has_price = 1),
                       last_price_date  = (SELECT MAX(date) FROM daily_prices_raw WHERE stock_id = ? AND has_price = 1)
                   WHERE stock_id = ?""", (sid, sid, sid))
