"""Stage 2 step 5: backward adjustment. MOCK DATA except where marked REAL."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.data.normalizers.adjustments import adjust_prices, event_factors
from backend.data.validators import checks

NOW = "2026-11-27T00:00:00+00:00"


def raw(dates, closes, sid="9999", vol=1000):
    return pd.DataFrame({"stock_id": sid, "date": dates, "open": closes, "high": closes, "low": closes,
                         "close": closes, "volume": vol, "has_price": 1})


def act(ex, typ, before, ref, sid="9999"):
    return {"stock_id": sid, "ex_date": ex, "action_type": typ, "before_price": before, "reference_price": ref,
            "adjustment_factor": ref / before}


def test_cash_dividend_continuity_and_latest_bar_unchanged():
    r = raw(["2024-06-11", "2024-06-12", "2024-06-13", "2024-06-14"], [100.0, 100.0, 96.0, 97.0])
    a = pd.DataFrame([act("2024-06-13", "cash_dividend", 100.0, 96.0)])
    adj = adjust_prices(r, a, NOW).set_index("date")
    assert adj.loc["2024-06-12", "adj_close"] == pytest.approx(96.0)          # continuous across ex-date
    assert adj.loc["2024-06-14", "adj_close"] == 97.0 and adj.loc["2024-06-14", "price_factor"] == 1.0
    assert (adj["volume_factor"] == 1.0).all()                                 # cash dividend: no share change


def test_split_scales_volume_and_multiple_events_compound():
    r = raw(["2025-06-16", "2025-06-17", "2025-06-18", "2025-06-19", "2025-06-20"], [190.0, 188.0, 47.0, 47.5, 47.0])
    a = pd.DataFrame([act("2025-06-18", "split", 188.0, 47.0), act("2025-06-20", "cash_dividend", 47.5, 47.0)])
    adj = adjust_prices(r, a, NOW).set_index("date")
    f_div = 47.0 / 47.5
    assert adj.loc["2025-06-16", "price_factor"] == pytest.approx(0.25 * f_div)
    assert adj.loc["2025-06-16", "adj_volume"] == pytest.approx(4000.0)       # 1:4 split -> 4x earlier volume
    assert adj.loc["2025-06-19", "price_factor"] == pytest.approx(f_div)


def test_no_price_days_stay_null():
    r = raw(["2024-06-11", "2024-06-12"], [100.0, 100.0])
    r.loc[1, ["open", "high", "low", "close"]] = None
    r.loc[1, "has_price"] = 0
    adj = adjust_prices(r, pd.DataFrame(), NOW)
    assert np.isnan(adj.iloc[1]["adj_close"]) and adj.iloc[1]["price_factor"] == 1.0


def test_volume_unknown_for_capital_reduction():
    ev = event_factors(pd.DataFrame([act("2014-10-09", "capital_reduction", 22.05, 49.82)]))
    assert not ev.iloc[0]["volume_exact"] and ev.iloc[0]["f_volume"] == 1.0


def test_check_adjusted_catches_corruption():
    r = raw(["2024-06-11", "2024-06-12", "2024-06-13"], [100.0, 100.0, 96.0])
    a = pd.DataFrame([act("2024-06-13", "cash_dividend", 100.0, 96.0)])
    adj = adjust_prices(r, a, NOW)
    assert checks.check_adjusted(adj, r, a).empty
    bad = adj.copy()
    bad.loc[0, "adj_close"] = 1.0
    bad.loc[1, "price_factor"] = 0.5
    bad.loc[1, "adj_close"] = 50.0
    assert set(checks.check_adjusted(bad, r, a).category) == {"adj_not_raw_times_factor",
                                                              "factor_change_without_event"}


def test_real_2330_dividend_record():
    """REAL: 2330 ex-dividend results from FinMind (fixture captured 2026-11-27)."""
    rows = json.loads((Path(__file__).parent / "fixtures/finmind/dividend_2330.json").read_text())["data"]
    a = pd.DataFrame([act(r["date"], "cash_dividend", r["before_price"], r["reference_price"], "2330") for r in rows])
    ev = event_factors(a)
    assert (ev["f_price"] < 1).all() and (ev["f_price"] > 0.99).all()       # small quarterly cash dividends
