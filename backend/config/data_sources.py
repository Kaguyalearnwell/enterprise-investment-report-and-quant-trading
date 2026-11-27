"""Registry of data sources, as VERIFIED in Stage 2.1 (docs/stage2_1_data_sources.md).

Only facts confirmed by live calls (reports/stage2_1_data_source_verification.json)
or by FinMind's official documentation are recorded here.
"""
from __future__ import annotations

from dataclasses import dataclass

FINMIND_API_URL = "https://api.finmindtrade.com/api/v4/data"
FINMIND_REQUESTS_PER_HOUR = {"anonymous": 300, "register": 600}   # docs: quickstart page
FINMIND_MIN_INTERVAL_SECONDS = 2.0   # Stage 2.1: bursts < 1 s apart briefly returned empty data

TPEX_DAILY_QUOTES_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/otc"


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    free_tier: bool              # usable on FinMind free / register level
    per_stock_only: bool         # free tier must pass data_id (no whole-market query)
    fields: tuple[str, ...]
    coverage_start: str | None   # from official docs; None = not documented
    target_table: str


FINMIND_DATASETS: dict[str, DatasetSpec] = {
    d.name: d
    for d in [
        DatasetSpec("TaiwanStockInfo", True, False,
                    ("industry_category", "stock_id", "stock_name", "type", "date"), None, "security_master"),
        DatasetSpec("TaiwanStockDelisting", True, False, ("date", "stock_id", "stock_name"), "2001-01-01", "security_master"),
        DatasetSpec("TaiwanStockTradingDate", True, False, ("date",), "1999-01-05", "trading_calendar"),
        DatasetSpec("TaiwanStockPrice", True, True,
                    ("date", "stock_id", "Trading_Volume", "Trading_money", "open", "max", "min", "close", "spread",
                     "Trading_turnover"), "1994-10-01", "daily_prices_raw"),
        DatasetSpec("TaiwanStockDividendResult", True, True,
                    ("date", "stock_id", "before_price", "after_price", "stock_and_cache_dividend",
                     "stock_or_cache_dividend", "max_price", "min_price", "open_price", "reference_price"),
                    "2003-05-01", "corporate_actions"),
        DatasetSpec("TaiwanStockCapitalReductionReferencePrice", True, True,
                    ("date", "stock_id", "ClosingPriceonTheLastTradingDay", "PostReductionReferencePrice", "LimitUp",
                     "LimitDown", "OpeningReferencePrice", "ExrightReferencePrice", "ReasonforCapitalReduction"),
                    "2011-01-01", "corporate_actions"),
        DatasetSpec("TaiwanStockSplitPrice", True, False,
                    ("date", "stock_id", "type", "before_price", "after_price", "max_price", "min_price", "open_price"),
                    None, "corporate_actions"),
        DatasetSpec("TaiwanStockParValueChange", True, False,
                    ("date", "stock_id", "stock_name", "before_close", "after_ref_close", "after_ref_max",
                     "after_ref_min", "after_ref_open"), "2020-01-01", "corporate_actions"),
        DatasetSpec("TaiwanStockTotalReturnIndex", True, True, ("price", "stock_id", "date"), None, "benchmark_index"),
        # Paid (Backer / Sponsor) -- verified to return "Your level is free/register"
        DatasetSpec("TaiwanStockPriceAdj", False, True, (), "1994-10-01", "daily_prices_adj"),
        DatasetSpec("TaiwanStockPriceLimit", False, True,
                    ("date", "stock_id", "reference_price", "limit_up", "limit_down"), "2000-01-01", "price_limits"),
        DatasetSpec("TaiwanStockSuspended", False, False,
                    ("date", "stock_id", "suspension_time", "resumption_date", "resumption_time"), "2011-10-06",
                    "trading_status"),
        DatasetSpec("TaiwanStockDispositionSecuritiesPeriod", False, True,
                    ("date", "stock_id", "stock_name", "disposition_cnt", "condition", "measure", "period_start",
                     "period_end"), None, "trading_status"),
    ]
}

# TaiwanStockInfo.industry_category values that are not industries (FinMind SDK source comment).
NOT_INDUSTRY_CATEGORY = ("電子工業", "化學生技醫療", "創新板股票", "創新版股票")
