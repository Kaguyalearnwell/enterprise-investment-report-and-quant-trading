"""FinMind provider (free / register tier), verified in Stage 2.1.

Translation rules (all from Stage 2.1 evidence or FinMind docs):
* Trading_Volume is in SHARES; Trading_money is NT$ value; Trading_turnover is the trade count.
* open/max/min/close == 0 means "no published price that day" -> stored as NULL, has_price = 0.
* HTTP 402 "Requests reach the upper limit" -> RateLimitExceeded.
* "Your level is free/register" -> DatasetNotAvailable (paid dataset).
* TaiwanStockTradingDate includes FUTURE dates -> cut at `as_of`.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import pandas as pd
import requests

from backend.config.data_sources import FINMIND_API_URL, FINMIND_MIN_INTERVAL_SECONDS, FINMIND_REQUESTS_PER_HOUR, NOT_INDUSTRY_CATEGORY

from backend.core.retry import RateLimiter, TransientError, retry
from backend.data.providers.base_provider import DataProvider, DatasetNotAvailable, FetchResult, ProviderError, RateLimitExceeded

log = logging.getLogger(__name__)
SOURCE = "finmind"
EXCHANGE_MAP = {"twse": "TWSE", "tpex": "TPEX", "emerging": "EMERGING"}


def classify_security(stock_id: str) -> str:
    """Security type from the TWSE/TPEx code format."""
    if re.fullmatch(r"[1-9]\d{3}", stock_id):
        return "common"
    if re.fullmatch(r"[1-9]\d{3}[A-Z]", stock_id):
        return "preferred"
    if re.fullmatch(r"00\d{2,4}[A-Z]?", stock_id):
        return "etf"
    if re.fullmatch(r"02\d{4}[A-Z]?", stock_id):
        return "etn"
    if re.fullmatch(r"91\d{4}", stock_id):
        return "tdr"
    return "other"


def _pick_industry(values: pd.Series) -> str | None:
    vals = sorted({v for v in values.dropna() if v})
    real = [v for v in vals if v not in NOT_INDUSTRY_CATEGORY]
    chosen = real or vals
    return chosen[0] if chosen else None


def _f(v: Any) -> float | None:
    if v is None or v == "":
        return None
    x = float(v)
    return None if x == -1.0 else x   # -1 is FinMind's missing-value sentinel (e.g. ExrightReferencePrice)


def _ratio(num: Any, den: Any) -> float | None:
    n, d = _f(num), _f(den)
    return n / d if n and d and n > 0 and d > 0 else None


class FinMindProvider(DataProvider):
    name = SOURCE

    def __init__(self, token: str = "", session: requests.Session | None = None, limiter: RateLimiter | None = None,
                 timeout: float = 60.0, attempts: int = 5, rate_limit_pause: float = 600.0, sleep=None):
        import time

        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", "taiwan-quant-system/0.2 (research)")
        if token:
            # In the cloud session the proxy replaces this placeholder with the real token.
            self.session.headers["Authorization"] = f"Bearer {token}"
        tier = "register" if token else "anonymous"
        self.limiter = limiter or RateLimiter(FINMIND_REQUESTS_PER_HOUR[tier], FINMIND_MIN_INTERVAL_SECONDS)
        self.timeout, self.attempts, self.rate_limit_pause = timeout, attempts, rate_limit_pause
        self._sleep = sleep or time.sleep

    # ---- HTTP ------------------------------------------------------------------------
    def _request(self, dataset: str, **params: str) -> tuple[list[dict], int]:
        def once() -> tuple[list[dict], int]:
            self.limiter.acquire()
            try:
                r = self.session.get(FINMIND_API_URL, params={"dataset": dataset, **params}, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                raise TransientError(f"network: {exc}") from exc
            try:
                body = r.json()
            except ValueError:
                if r.status_code >= 500:
                    raise TransientError(f"HTTP {r.status_code} non-JSON body")
                raise ProviderError(f"HTTP {r.status_code} non-JSON body: {r.text[:200]}")
            msg = str(body.get("msg", ""))
            if r.status_code == 402 or "upper limit" in msg:
                raise RateLimitExceeded(msg)
            if "level is" in msg:
                raise DatasetNotAvailable(f"{dataset}: {msg}")
            if r.status_code >= 500:
                raise TransientError(f"HTTP {r.status_code}: {msg}")
            if r.status_code != 200 or body.get("status") != 200 or "data" not in body:
                raise ProviderError(f"{dataset} HTTP {r.status_code}: {msg}")
            return body["data"], r.status_code

        def with_rate_limit() -> tuple[list[dict], int]:
            try:
                return once()
            except RateLimitExceeded as exc:
                self.limiter.penalize(self.rate_limit_pause)
                raise TransientError(f"rate limited: {exc}") from exc

        return retry(with_rate_limit, attempts=self.attempts, sleep=self._sleep)

    # ---- reference data ------------------------------------------------------------------
    def security_list(self) -> FetchResult:
        raw, status = self._request("TaiwanStockInfo")
        df = pd.DataFrame(raw)
        if df.empty:
            return FetchResult(df, raw, status, SOURCE)
        # TaiwanStockInfo also lists index names as stock_id ('Semiconductor', 'IronSteel', ...).
        # They are not securities: keep only exchange code formats (4-6 chars, leading digit).
        is_code = df["stock_id"].astype(str).str.fullmatch(r"[0-9][0-9A-Z]{3,5}")
        dropped = sorted(df.loc[~is_code, "stock_id"].astype(str).unique())
        if dropped:
            log.info("TaiwanStockInfo: dropped %d non-security ids (index names), e.g. %s", len(dropped), dropped[:3])
        df = df[is_code]
        snapshot = df["date"].max()
        rows = []
        for sid, g in df.groupby("stock_id"):
            last = g[g["date"] == g["date"].max()]
            # FinMind docs: a stock that moved from the emerging board keeps an 'emerging' row whose
            # date is the day it left the board.
            em = g.loc[g["type"] == "emerging", "date"]
            left_emerging = em.max() if not em.empty and (g["type"] != "emerging").any() else None
            rows.append({
                "stock_id": sid,
                "stock_name": last["stock_name"].iloc[0],
                "exchange": EXCHANGE_MAP.get(last["type"].iloc[0], "UNKNOWN"),
                "industry_category": _pick_industry(last["industry_category"]),
                "security_type": classify_security(sid),
                "info_last_seen": g["date"].max(),
                "in_current_snapshot": bool(g["date"].max() == snapshot),
                "emerging_exit_date": left_emerging,
            })
        out = pd.DataFrame(rows)
        out.attrs["snapshot_date"] = snapshot
        out.attrs["dropped_non_security_ids"] = dropped
        return FetchResult(out, raw, status, SOURCE)

    def delisted_securities(self) -> FetchResult:
        raw, status = self._request("TaiwanStockDelisting")
        df = pd.DataFrame(raw)
        if not df.empty:
            df = df.rename(columns={"date": "delisting_date"})[["stock_id", "stock_name", "delisting_date"]]
            df["security_type"] = df["stock_id"].map(classify_security)
        return FetchResult(df, raw, status, SOURCE)

    def trading_calendar(self, as_of: str) -> FetchResult:
        raw, status = self._request("TaiwanStockTradingDate")
        d = pd.to_datetime(pd.DataFrame(raw)["date"])
        d = d[d <= pd.Timestamp(as_of)]
        cal = pd.DataFrame({
            "date": d.dt.strftime("%Y-%m-%d"),
            "is_trading_day": 1,
            "session_type": ["saturday_makeup" if wd >= 5 else "regular" for wd in d.dt.weekday],
            "source": SOURCE,
        })
        # TWSE and TPEx share one calendar.
        out = pd.concat([cal.assign(exchange="TWSE"), cal.assign(exchange="TPEX")], ignore_index=True)
        return FetchResult(out, raw, status, SOURCE)

    def market_corporate_actions(self, start: str, end: str) -> FetchResult:
        split_raw, s1 = self._request("TaiwanStockSplitPrice")
        par_raw, _ = self._request("TaiwanStockParValueChange", start_date=start, end_date=end)
        rows, unmapped = [], []
        type_map = {"分割": "split", "反分割": "reverse_split", "面額變更": "par_value_change"}
        for r in split_raw:
            at = type_map.get(r.get("type", ""))
            if at is None:
                unmapped.append(r)
                continue
            rows.append(self._action(r, r["date"], at, before=r.get("before_price"), ref=r.get("after_price"),
                                     up=r.get("max_price"), down=r.get("min_price"),
                                     split_ratio=_ratio(r.get("before_price"), r.get("after_price")),
                                     dataset="TaiwanStockSplitPrice"))
        for r in par_raw:
            rows.append(self._action(r, r["date"], "par_value_change", before=r.get("before_close"),
                                     ref=r.get("after_ref_close"), up=r.get("after_ref_max"), down=r.get("after_ref_min"),
                                     split_ratio=_ratio(r.get("before_close"), r.get("after_ref_close")),
                                     dataset="TaiwanStockParValueChange"))
        df = pd.DataFrame(rows)
        if not df.empty:
            df = df[(df["ex_date"] >= start) & (df["ex_date"] <= end)]
            # A par-value change can appear in both datasets: keep one row, prefer the dedicated table.
            df["_pref"] = df["raw_payload"].str.contains('"dataset": "TaiwanStockParValueChange"').astype(int)
            df = (df.sort_values("_pref").drop_duplicates(["stock_id", "ex_date", "action_type"], keep="last")
                    .drop(columns="_pref").reset_index(drop=True))
        res = FetchResult(df, split_raw + par_raw, s1, SOURCE)
        res.data.attrs["unmapped"] = unmapped
        return res

    def benchmark_index(self, index_id: str, start: str, end: str) -> FetchResult:
        raw, status = self._request("TaiwanStockTotalReturnIndex", data_id=index_id, start_date=start, end_date=end)
        df = pd.DataFrame(raw)
        if not df.empty:
            df = pd.DataFrame({"index_id": df["stock_id"], "date": df["date"], "value": df["price"].astype(float),
                               "source": SOURCE})
        return FetchResult(df, raw, status, SOURCE)

    # ---- per security --------------------------------------------------------------------
    def daily_prices(self, stock_id: str, start: str, end: str) -> FetchResult:
        raw, status = self._request("TaiwanStockPrice", data_id=stock_id, start_date=start, end_date=end)
        df = pd.DataFrame(raw)
        if df.empty:
            return FetchResult(df, raw, status, SOURCE)
        ohlc = df[["open", "max", "min", "close"]].astype(float)
        no_price = (ohlc == 0).all(axis=1) | (ohlc["close"] == 0)
        out = pd.DataFrame({
            "stock_id": df["stock_id"].astype(str),
            "date": df["date"],
            "open": ohlc["open"].mask(no_price),
            "high": ohlc["max"].mask(no_price),
            "low": ohlc["min"].mask(no_price),
            "close": ohlc["close"].mask(no_price),
            "volume": df["Trading_Volume"].astype("int64"),
            "turnover": df["Trading_money"].astype(float),
            "trade_count": df["Trading_turnover"].astype("int64"),
            "has_price": (~no_price).astype(int),
            "source": SOURCE,
        })
        out = out.astype(object).where(out.notna(), None)
        return FetchResult(out, raw, status, SOURCE)

    def dividends(self, stock_id: str, start: str, end: str) -> FetchResult:
        raw, status = self._request("TaiwanStockDividendResult", data_id=stock_id, start_date=start, end_date=end)
        rows = []
        for r in raw:
            kind = str(r.get("stock_or_cache_dividend", ""))
            has_stock, has_cash = "權" in kind, "息" in kind
            at = ("cash_and_stock_dividend" if has_stock and has_cash else
                  "stock_dividend" if has_stock else "cash_dividend")
            value = _f(r.get("stock_and_cache_dividend"))
            rows.append(self._action(
                r, r["date"], at, before=r.get("before_price"), ref=r.get("reference_price"),
                up=r.get("max_price"), down=r.get("min_price"),
                # The vendor gives one combined value; it is split only when the event is pure cash or pure stock.
                cash=value if at == "cash_dividend" else None,
                stock=value if at == "stock_dividend" else None,
                dataset="TaiwanStockDividendResult"))
        return FetchResult(pd.DataFrame(rows), raw, status, SOURCE)

    def capital_reductions(self, stock_id: str, start: str, end: str) -> FetchResult:
        raw, status = self._request("TaiwanStockCapitalReductionReferencePrice", data_id=stock_id,
                                    start_date=start, end_date=end)
        rows = [self._action(r, r["date"], "capital_reduction", before=r.get("ClosingPriceonTheLastTradingDay"),
                             ref=r.get("PostReductionReferencePrice"), up=r.get("LimitUp"), down=r.get("LimitDown"),
                             dataset="TaiwanStockCapitalReductionReferencePrice")
                for r in raw if start <= r["date"] <= end]
        return FetchResult(pd.DataFrame(rows), raw, status, SOURCE)

    @staticmethod
    def _action(r: dict, ex_date: str, action_type: str, *, before: Any, ref: Any, up: Any, down: Any,
                dataset: str, cash: float | None = None, stock: float | None = None,
                split_ratio: float | None = None) -> dict:
        return {
            "stock_id": str(r["stock_id"]), "ex_date": ex_date, "action_type": action_type,
            "cash_dividend": cash, "stock_dividend": stock, "split_ratio": split_ratio,
            "capital_reduction_ratio": None,
            "before_price": _f(before), "reference_price": _f(ref), "limit_up": _f(up), "limit_down": _f(down),
            "adjustment_factor": _ratio(ref, before),
            "raw_payload": json.dumps({"dataset": dataset, **r}, ensure_ascii=False),
            "source": SOURCE,
        }


# ---- fundamentals (Stage 6.2; datasets verified in Stage 6.1) --------------------------------------
STATEMENT_DATASETS = {"income": "TaiwanStockFinancialStatements", "balance": "TaiwanStockBalanceSheet",
                      "cashflow": "TaiwanStockCashFlowsStatement"}


def _statements(self, stock_id: str, start: str, end: str, statement: str) -> FetchResult:
    raw, status = self._request(STATEMENT_DATASETS[statement], data_id=stock_id, start_date=start, end_date=end)
    df = pd.DataFrame(raw)
    if df.empty:
        return FetchResult(df, raw, status, SOURCE)
    out = pd.DataFrame({"stock_id": df["stock_id"].astype(str), "period_end": df["date"], "statement": statement,
                        "item": df["type"], "value": pd.to_numeric(df["value"], errors="coerce"),
                        "origin_name": df.get("origin_name"), "source": SOURCE})
    out = out.drop_duplicates(["stock_id", "period_end", "statement", "item"], keep="last")
    return FetchResult(out.astype(object).where(out.notna(), None), raw, status, SOURCE)


def _month_revenue(self, stock_id: str, start: str, end: str) -> FetchResult:
    raw, status = self._request("TaiwanStockMonthRevenue", data_id=stock_id, start_date=start, end_date=end)
    df = pd.DataFrame(raw)
    if df.empty:
        return FetchResult(df, raw, status, SOURCE)
    out = pd.DataFrame({"stock_id": df["stock_id"].astype(str),
                        "revenue_month": [f"{int(y)}-{int(m):02d}" for y, m in zip(df["revenue_year"], df["revenue_month"])],
                        "revenue": pd.to_numeric(df["revenue"], errors="coerce"),
                        "create_time": df.get("create_time", ""), "source": SOURCE})
    return FetchResult(out.drop_duplicates(["stock_id", "revenue_month"], keep="last"), raw, status, SOURCE)


def _dividend_policy(self, stock_id: str, start: str, end: str) -> FetchResult:
    """`date` in this dataset can lie in the future (Stage 6.1): request without an end date and keep rows
    whose ANNOUNCEMENT date is on or before `end`."""
    raw, status = self._request("TaiwanStockDividend", data_id=stock_id, start_date=start)
    rows = []
    for r in raw:
        ad = (r.get("AnnouncementDate") or "")[:10]
        if not ad or ad > end:
            continue
        num = lambda k: float(r.get(k) or 0.0)  # noqa: E731
        ex = lambda k: (r.get(k) or "")[:10] or None  # noqa: E731
        rows.append({"stock_id": str(r["stock_id"]), "announcement_date": ad,
                     "announcement_time": r.get("AnnouncementTime") or None, "period_label": str(r.get("year") or ""),
                     "cash_dividend": num("CashEarningsDistribution") + num("CashStatutorySurplus"),
                     "stock_dividend": num("StockEarningsDistribution") + num("StockStatutorySurplus"),
                     "cash_ex_date": ex("CashExDividendTradingDate"), "stock_ex_date": ex("StockExDividendTradingDate"),
                     "cash_payment_date": ex("CashDividendPaymentDate"),
                     "raw_payload": json.dumps(r, ensure_ascii=False), "source": SOURCE})
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.drop_duplicates(["stock_id", "announcement_date", "period_label"], keep="last")
    return FetchResult(df, raw, status, SOURCE)


def _valuation(self, stock_id: str, start: str, end: str) -> FetchResult:
    raw, status = self._request("TaiwanStockPER", data_id=stock_id, start_date=start, end_date=end)
    df = pd.DataFrame(raw)
    if df.empty:
        return FetchResult(df, raw, status, SOURCE)
    out = pd.DataFrame({"stock_id": df["stock_id"].astype(str), "date": df["date"],
                        "per": pd.to_numeric(df["PER"], errors="coerce"), "pbr": pd.to_numeric(df["PBR"], errors="coerce"),
                        "dividend_yield": pd.to_numeric(df["dividend_yield"], errors="coerce"), "source": SOURCE})
    return FetchResult(out.astype(object).where(out.notna(), None), raw, status, SOURCE)


FinMindProvider.income_statements = lambda self, s, a, b: _statements(self, s, a, b, "income")
FinMindProvider.balance_sheets = lambda self, s, a, b: _statements(self, s, a, b, "balance")
FinMindProvider.cash_flows = lambda self, s, a, b: _statements(self, s, a, b, "cashflow")
FinMindProvider.month_revenue = _month_revenue
FinMindProvider.dividend_policy = _dividend_policy
FinMindProvider.valuation = _valuation
