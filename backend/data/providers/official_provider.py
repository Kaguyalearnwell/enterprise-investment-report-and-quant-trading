"""Official exchange data (TPEx). Verified endpoint: Stage 2.1.

* TPEx daily quotes (上櫃股票每日收盤行情): all TPEx securities for one date, including
  issued shares and the NEXT day's official limit-up / limit-down prices.
* TWSE: the official site blocks the cloud environment used for development
  (Stage 2.1 §3.8). It is deliberately NOT implemented here because it could not be
  verified; run TWSE checks from the user's own computer in a later stage.
"""
from __future__ import annotations

import datetime as dt
import logging
import time

import pandas as pd
import requests

from backend.config.data_sources import TPEX_DAILY_QUOTES_URL
from backend.core.retry import TransientError, retry

log = logging.getLogger(__name__)


def _num(s: str) -> float | None:
    s = str(s).replace(",", "").strip()
    if s in ("", "---", "--", "除權息", "除息", "除權"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


class TPExProvider:
    name = "tpex"

    def __init__(self, session: requests.Session | None = None, min_interval: float = 3.0, sleep=time.sleep):
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", "Mozilla/5.0 (taiwan-quant-research)")
        self.min_interval, self._sleep, self._last = min_interval, sleep, 0.0

    def daily_quotes(self, date: dt.date) -> pd.DataFrame:
        """All TPEx quotes for `date`; empty DataFrame on a non-trading day."""
        def once() -> dict:
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last = time.monotonic()
            try:
                r = self.session.get(TPEX_DAILY_QUOTES_URL, timeout=60,
                                     params={"date": date.strftime("%Y/%m/%d"), "type": "EW", "response": "json"})
            except (requests.ConnectionError, requests.Timeout) as exc:
                raise TransientError(str(exc)) from exc
            if r.status_code >= 500:
                raise TransientError(f"HTTP {r.status_code}")
            return r.json()

        body = retry(once, attempts=4, sleep=self._sleep)
        tables = body.get("tables") or []
        if not tables or not tables[0].get("data"):
            return pd.DataFrame()
        t = tables[0]
        df = pd.DataFrame(t["data"], columns=[f.strip() for f in t["fields"]])
        out = pd.DataFrame({
            "date": date.isoformat(),
            "stock_id": df["代號"].astype(str).str.strip(),
            "stock_name": df["名稱"].astype(str).str.strip(),
            "close": df["收盤"].map(_num),
            "volume": df["成交股數"].map(_num),
            "issued_shares": df["發行股數"].map(_num) if "發行股數" in df else None,
            "next_limit_up": df["次日漲停價"].map(_num) if "次日漲停價" in df else None,
            "next_limit_down": df["次日跌停價"].map(_num) if "次日跌停價" in df else None,
        })
        return out
