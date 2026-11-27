"""Stage 2 step 9: weekly bars. Uses the Phase 1 synthetic calendar (MOCK DATA)."""
import pandas as pd

from backend.data.normalizers.weekly_prices import build_weekly
from backend.data.validators import checks
from backend.indicators.weekly import resample_weekly
from backend.strategies.fib_bb_weekly import ADJ_COLS

NOW = "2026-11-27T00:00:00+00:00"


def daily_from_stock(stock: pd.DataFrame) -> pd.DataFrame:
    d = stock.copy()
    d["stock_id"], d["date"], d["has_price"] = "9999", d.index.strftime("%Y-%m-%d"), 1
    return d.reset_index(drop=True)


def test_matches_phase1_weekly_aggregation(stock):
    d = daily_from_stock(stock)
    days = d["date"].tolist()
    w = build_weekly(d, days, as_of="2017-12-29", computed_at=NOW)
    ref = resample_weekly(stock, ADJ_COLS)
    adj = w[w.price_basis == "adj"].set_index("week_key")
    assert (adj["close"] == ref["close"].reindex(adj.index)).all()
    assert (adj["high"] == ref["high"].reindex(adj.index)).all()


def test_week_end_is_last_trading_day_and_complete_flag(stock):
    d = daily_from_stock(stock)
    days = d["date"].tolist()
    w = build_weekly(d, days, as_of="2016-06-15", computed_at=NOW)        # a Wednesday
    raw = w[w.price_basis == "raw"].set_index("week_key")
    assert raw.loc[201623, "week_end"] == "2016-06-09"                     # Friday 2016-06-10 closed
    assert raw.loc[201624, "complete"] == 0 and raw.loc[201623, "complete"] == 1
    assert w["week_end"].max() <= "2016-06-15"


def test_no_price_days_excluded(stock):
    d = daily_from_stock(stock)
    i = d.index[d.date == "2016-06-08"][0]
    d.loc[i, ["open", "high", "low", "close", "adj_open", "adj_high", "adj_low", "adj_close"]] = None
    d.loc[i, "has_price"] = 0
    w = build_weekly(d, d["date"].tolist(), "2017-12-29", NOW).set_index(["week_key", "price_basis"])
    assert w.loc[(201623, "raw"), "trading_days"] == 3


def test_check_weekly_detects_mismatch(stock):
    d = daily_from_stock(stock)
    w = build_weekly(d, d["date"].tolist(), "2017-12-29", NOW)
    assert checks.check_weekly(w, w.copy()).empty
    bad = w.copy()
    bad.loc[5, "close"] += 1
    assert checks.check_weekly(bad, w).category.tolist() == ["weekly_aggregation_mismatch"]
