"""Emerging-board detection and listing dates (found in the Stage 2 acceptance run on real data)."""
import pandas as pd

from backend.data.normalizers.listing import emerging_prefix_end, resolve_listing
from backend.data.normalizers.price_limits import compute_limits
from backend.data.validators import checks

NOW = "2026-11-27T00:00:00+00:00"


def bars(rows, sid="6488"):
    return pd.DataFrame([{"stock_id": sid, "date": d, "open": o, "high": h, "low": l, "close": c, "has_price": 1}
                         for d, o, h, l, c in rows])


# REAL first bars of 6488 環球晶 from FinMind (emerging board, open = previous day's average)
REAL_6488 = [("2014-10-28", 0.0, 101.0, 68.8, 90.35), ("2014-10-29", 92.04, 90.4, 86.0, 87.0),
             ("2014-10-30", 88.36, 86.0, 85.0, 85.5), ("2014-10-31", 85.37, 82.6, 78.09, 79.6)]


def test_single_bad_bar_at_start_is_not_an_emerging_period():
    raw = bars([("2013-01-02", 105.0, 101.0, 99.0, 100.0)] +
               [(f"2013-01-{d:02d}", 100.0, 101.0, 99.0, 100.0) for d in range(3, 30)])
    assert emerging_prefix_end(raw) is None


def test_real_6488_emerging_prefix_detected():
    listed = [(f"2015-09-{d:02d}", 70.0, 71.0, 69.0, 70.5) for d in (24, 25)]   # MOCK listed bars after it
    raw = bars(REAL_6488 + listed)
    assert emerging_prefix_end(raw) == "2014-10-31"
    res = resolve_listing(raw, None, tpex_first_seen="2015-10-01")
    assert res == {"listing_date": "2015-09-24", "listing_basis": "inferred_from_prices", "emerging_to": "2014-10-31"}


def test_normal_stock_has_no_emerging_prefix():
    raw = bars([(f"2016-01-{d:02d}", 100.0, 101.0, 99.0, 100.0) for d in range(4, 30)] +
               [("2016-02-01", 120.0, 101.0, 99.0, 100.0)])   # one bad bar late in history (MOCK): not a prefix
    assert emerging_prefix_end(raw) is None
    assert resolve_listing(raw, None, None)["listing_date"] is None


def test_emerging_exit_row_wins_when_later():
    raw = bars([("2024-09-20", 50.0, 51.0, 49.0, 50.0), ("2024-09-25", 50.0, 51.0, 49.0, 50.0),
                ("2024-09-26", 52.0, 53.0, 51.0, 52.0)], sid="1294")
    res = resolve_listing(raw, "2024-09-25", None)
    assert (res["listing_date"], res["listing_basis"]) == ("2024-09-26", "emerging_exit")


def test_limits_free_before_listing_and_first_five_days():
    days = [f"2015-09-{d:02d}" for d in (21, 22, 23, 24, 25, 28, 29, 30)]
    raw = pd.DataFrame({"stock_id": "6488", "date": days, "close": 70.0, "has_price": 1})
    lim = compute_limits(raw, pd.DataFrame(), NOW, listing_date="2015-09-23").set_index("date")["has_limit"]
    assert lim.tolist() == [0, 0, 0, 0, 0, 0, 0, 1]      # 2 emerging + 5 new-listing days without limit


def test_reclassify_pre_listing_and_pre_event_halt():
    issues = pd.DataFrame([
        {"table_name": "daily_prices_raw", "stock_id": "6488", "date": "2014-10-29", "check_name": "ohlc_logic",
         "severity": "error", "category": "high_below_open_close_or_low", "detail": "x", "action": "exclude"},
        {"table_name": "daily_prices_raw", "stock_id": "6488", "date": "2016-01-04", "check_name": "ohlc_logic",
         "severity": "error", "category": "high_below_open_close_or_low", "detail": "x", "action": "exclude"},
        {"table_name": "daily_prices_raw", "stock_id": "8070", "date": "2020-08-06", "check_name": "calendar",
         "severity": "warning", "category": "missing_trading_days",
         "detail": "2 trading day(s) missing: 2020-08-06..2020-08-07", "action": "flag"},
    ])
    master = pd.DataFrame({"stock_id": ["6488", "8070"], "listing_date": ["2015-09-24", None]})
    acts = pd.DataFrame([{"stock_id": "8070", "ex_date": "2020-08-17", "action_type": "par_value_change"}])
    days = ["2020-08-06", "2020-08-07", "2020-08-10", "2020-08-11", "2020-08-12", "2020-08-13", "2020-08-14", "2020-08-17"]
    out = checks.reclassify(issues, master, acts, days)
    assert out.category.tolist() == ["pre_listing:high_below_open_close_or_low", "high_below_open_close_or_low",
                                     "pre_event_halt"]
    assert out.severity.tolist() == ["info", "error", "info"]      # a real error after listing stays an error
