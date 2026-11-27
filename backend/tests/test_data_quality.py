"""Stage 2 step 4: data-quality checks.

MOCK DATA: every price row below is hand-written to trigger one specific check.
They are not market data and are never written to a research database.
"""
import pandas as pd

from backend.data.validators import checks

DAYS = ["2024-06-11", "2024-06-12", "2024-06-13", "2024-06-14", "2024-06-17", "2024-06-18", "2024-06-19"]


def row(date, o=100.0, h=101.0, l=99.0, c=100.0, v=1000, t=None, has=1, sid="9999"):
    return {"stock_id": sid, "date": date, "open": o, "high": h, "low": l, "close": c, "volume": v,
            "turnover": t if t is not None else (c * v if c is not None else 0.0), "trade_count": 1, "has_price": has}


def cats(df):
    return sorted(df["category"].tolist())


def test_ohlc_logic():
    p = pd.DataFrame([row(DAYS[0]), row(DAYS[1], h=98.0), row(DAYS[2], l=100.5), row(DAYS[3], o=0.0),
                      row(DAYS[4], c=None)])
    # open=0 breaks two rules at once (price <= 0 AND low above open); both must be reported
    assert cats(checks.check_ohlc(p)) == ["high_below_open_close_or_low", "low_above_open_close",
                                          "low_above_open_close", "nonpositive_price", "partial_ohlc"]


def test_no_price_days_are_info_not_errors():
    p = pd.DataFrame([row(DAYS[0], None, None, None, None, v=0, has=0),
                      row(DAYS[1], None, None, None, None, v=451, t=6223, has=0)])
    out = checks.check_no_price_days(p)
    assert cats(out) == ["no_trade", "volume_without_price"] and set(out.severity) == {"info"}
    assert checks.check_ohlc(p).empty                        # has_price=0 rows are not OHLC errors


def test_vwap_outside_range_and_negative_volume():
    p = pd.DataFrame([row(DAYS[0], t=100.0 * 1000), row(DAYS[1], t=150.0 * 1000), row(DAYS[2], v=-5)])
    assert set(cats(checks.check_volume(p))) == {"vwap_outside_high_low", "negative_volume"}


def test_duplicates():
    p = pd.DataFrame([row(DAYS[0]), row(DAYS[0], c=100.5)])
    assert len(checks.check_duplicates(p)) == 2


def test_missing_days_collapsed_into_runs_and_off_calendar_rows():
    p = pd.DataFrame([row(DAYS[0]), row(DAYS[3]), row(DAYS[6]), row("2024-06-15")])   # 06-15 is a Saturday
    out = checks.check_calendar(p, DAYS, as_of="2024-06-19")
    miss = out[out.category == "missing_trading_days"]
    assert miss.detail.tolist() == ["2 trading day(s) missing: 2024-06-12..2024-06-13",
                                    "2 trading day(s) missing: 2024-06-17..2024-06-18"]
    assert out[out.category == "row_on_non_trading_day"].date.tolist() == ["2024-06-15"]


def test_jump_explained_by_event_reference_is_not_flagged():
    p = pd.DataFrame([row(DAYS[0], c=100.0), row(DAYS[1], o=80.0, h=81.0, l=79.0, c=80.0)])
    ev = pd.DataFrame([{"stock_id": "9999", "ex_date": DAYS[1], "reference_price": 79.5, "action_type": "split"}])
    assert checks.check_returns(p, ev).empty
    out = checks.check_returns(p, ev.iloc[0:0])
    assert cats(out) == ["unexplained_jump"]


def test_limit_7pct_before_2015_06_01():
    p = pd.DataFrame([row("2015-05-28", c=100.0), row("2015-05-29", c=108.0, h=108.0),
                      row("2015-06-01", c=116.0, h=116.0)])
    out = checks.check_returns(p, pd.DataFrame())
    assert out.date.tolist() == ["2015-05-29"]               # +8% > 7% limit; +7.4% after the change is fine


def test_before_price_mismatch():
    p = pd.DataFrame([row(DAYS[0], c=100.0), row(DAYS[1], c=96.0, l=95.0)])
    good = {"stock_id": "9999", "ex_date": DAYS[1], "action_type": "cash_dividend", "before_price": 100.0,
            "reference_price": 96.0, "adjustment_factor": 0.96}
    assert checks.check_corporate_actions(pd.DataFrame([good]), p, DAYS).empty
    bad = {**good, "before_price": 110.0}
    assert cats(checks.check_corporate_actions(pd.DataFrame([bad]), p, DAYS)) == ["before_price_mismatch"]


def test_stock_id_format():
    m = pd.DataFrame({"stock_id": ["2330", "23300", "0050"], "security_type": ["common", "common", "etf"]})
    assert checks.check_stock_ids(m).stock_id.tolist() == ["23300"]
