"""Data Provider Interface.

The rest of the system depends only on this interface and on the column layout
of the database tables, never on a vendor. Replacing FinMind with TEJ (or any
source with full delisted history) means writing one new subclass; the
downloader, database and backtester stay unchanged.

Every method returns a pandas DataFrame whose columns are exactly the target
table's columns (without `ingested_at`, which the downloader stamps). Providers
must NOT fill gaps, forward-fill prices or drop anomalies: they translate the
vendor's answer faithfully and mark what is missing.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import pandas as pd


class ProviderError(Exception):
    """Non-retryable error (bad request, schema change, ...)."""


class DatasetNotAvailable(ProviderError):
    """The account tier does not include this dataset (e.g. FinMind 'Your level is free')."""


class RateLimitExceeded(Exception):
    """Server-side quota exceeded; caller should pause and retry."""


@dataclass
class FetchResult:
    """A provider answer plus the raw vendor payload kept for provenance."""

    data: pd.DataFrame
    raw: list[dict[str, Any]] = field(default_factory=list)
    http_status: int | None = None
    source: str = ""


class DataProvider(ABC):
    name: str = "base"

    # reference data (whole market, one request each)
    @abstractmethod
    def security_list(self) -> FetchResult:
        """Current listings -> security_master columns."""

    @abstractmethod
    def delisted_securities(self) -> FetchResult:
        """Historical delistings -> security_master columns (delisting_date set)."""

    @abstractmethod
    def trading_calendar(self, as_of: str) -> FetchResult:
        """Trading days up to `as_of` (never later) -> trading_calendar columns."""

    @abstractmethod
    def market_corporate_actions(self, start: str, end: str) -> FetchResult:
        """Whole-market actions available in one request (splits, par value changes)."""

    @abstractmethod
    def benchmark_index(self, index_id: str, start: str, end: str) -> FetchResult:
        """Benchmark index levels -> benchmark_index columns."""

    # per-security data
    @abstractmethod
    def daily_prices(self, stock_id: str, start: str, end: str) -> FetchResult:
        """Unadjusted daily OHLCV -> daily_prices_raw columns."""

    @abstractmethod
    def dividends(self, stock_id: str, start: str, end: str) -> FetchResult:
        """Ex-rights / ex-dividend events -> corporate_actions columns."""

    @abstractmethod
    def capital_reductions(self, stock_id: str, start: str, end: str) -> FetchResult:
        """Capital reductions -> corporate_actions columns."""
