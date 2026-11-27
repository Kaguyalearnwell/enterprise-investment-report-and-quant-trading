"""Stage 4: execution rules. Prices are illustrative (MOCK) except where marked."""
import math

import pytest

from backend.backtesting.execution import (CostModel, entry_check, is_locked, lot_shares, round_to_tick,
                                           size_position, stop_fill)


def test_round_to_tick_direction():
    assert round_to_tick(593.2, "buy") == 594.0 and round_to_tick(593.8, "sell") == 593.0   # tick 1 at 500-1000
    assert round_to_tick(45.61, "buy") == 45.65 and round_to_tick(45.64, "sell") == 45.6    # tick 0.05
    assert round_to_tick(9.995, "buy") == 10.0                                              # crosses into 0.05 band


def test_costs_min_commission_and_tax():
    c = CostModel(commission_discount=0.6)
    assert c.commission(100_000) == pytest.approx(85.5)
    assert c.commission(10_000) == 20.0                   # 8.55 < NT$20 minimum
    assert c.tax(100_000) == pytest.approx(300.0)          # 0.3% on sells (not the 0.15% day-trade rate)


def test_slippage_and_odd_lot_extra():
    c = CostModel(slippage_bps=15)
    assert c.fill_price(100.0, "buy") == 100.5            # 100.15 -> tick 0.5 up
    assert c.fill_price(100.0, "sell") == 99.8            # 99.85 -> tick 0.1 down (below 100)
    assert c.fill_price(593.0, "buy", odd_lot=True) == 595.0   # 25 bps -> 594.48 -> 595


def test_lots_before_and_after_odd_lot_trading():
    assert lot_shares(1999.7, "2019-05-02", "2020-10-26") == 1000
    assert lot_shares(999, "2019-05-02", "2020-10-26") == 0
    assert lot_shares(1999.7, "2021-05-03", "2020-10-26") == 1999


def test_entry_check():
    assert entry_check(110.0, 110.0, 100.0, 3.0, 1.0) == "limit_up_open"
    assert entry_check(104.0, 110.0, 100.0, 3.0, 1.0) == "gap_above_chase_limit"    # > close + 1 ATR
    assert entry_check(102.5, 110.0, 100.0, 3.0, 1.0) is None


def test_stop_gap_touch_locked():
    assert stop_fill(95.0, 99.0, 94.0, 96.0, 90.0) == ("gap", 95.0)       # open below the stop
    assert stop_fill(99.0, 100.0, 95.5, 96.0, 90.0) == ("touch", 96.0)
    assert stop_fill(99.0, 100.0, 97.0, 96.0, 90.0) is None
    kind, px = stop_fill(90.0, 90.0, 90.0, 96.0, 90.0)
    assert kind == "locked" and math.isnan(px)                            # limit-down all day: cannot sell
    assert is_locked(90.0, 90.0, 90.0) and not is_locked(91.0, 90.0, 90.0)


def test_sizing_caps():
    c = CostModel()
    kw = dict(equity=1_000_000, cash=1_000_000, market_value=0, industry_value=0, open_risk=0, risk_pct=0.01,
              max_weight=0.10, max_industry=0.25, max_exposure=0.90, max_open_risk=0.05, cost=c)
    # Phase 1 §3 #1: price 100, ATR 3 -> stop 92.5; risk budget wants 1,333 shares but 10% caps at 1,000
    n, why = size_position(price=100.0, stop=92.5, **kw)
    assert why == "max_weight" and n == pytest.approx(1000)
    n, why = size_position(price=100.0, stop=80.0, **kw)                 # wide stop: risk budget binds
    assert why == "risk_budget" and n == pytest.approx(500)
    n, why = size_position(price=100.0, stop=92.5, **{**kw, "open_risk": 48_000})
    assert why == "max_open_risk" and n == pytest.approx(2000 / 7.5)
    n, why = size_position(price=100.0, stop=92.5, **{**kw, "market_value": 880_000})
    assert why == "max_exposure" and n == pytest.approx(200)
    n, why = size_position(price=100.0, stop=92.5, **{**kw, "cash": 30_000})
    assert why == "cash" and n < 300
