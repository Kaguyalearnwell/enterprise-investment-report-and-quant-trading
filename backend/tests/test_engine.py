"""Stage 4: portfolio engine rules. MOCK DATA: every bar below is hand-written for one rule."""
import copy
import math

import pandas as pd
import pytest

from backend.backtesting.engine import Bar, PortfolioEngine
from backend.backtesting.execution import CostModel

DAYS = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2021-03-01", periods=80)]   # after odd lots started


def bar(o, h=None, l=None, c=None, pf=1.0, ma34=math.nan, atr=2.0, priced=True, lu=None, ld=None):
    h = h if h is not None else max(o, c if c is not None else o)
    l = l if l is not None else min(o, c if c is not None else o)
    c = c if c is not None else o
    return Bar(o, h, l, c, priced, pf, c * pf, ma34, atr, lu, ld)


def flat(price=100.0, n=len(DAYS), **kw):
    return {d: bar(price, price + 0.5, price - 0.5, price, **kw) for d in DAYS[:n]}


def signal(d, sid="9999", close=100.0, atr=2.0):
    return {d: [{"stock_id": sid, "signal_date": d, "close": close, "atr": atr, "turnover": 1e9}]}


def engine(cfg, basis="adjusted", **cost):
    return PortfolioEngine(cfg, CostModel(**({"slippage_bps": 0.0, "commission_discount": 0.6} | cost)), basis)


def run(cfg, bars, sigs, actions=None, meta=None, basis="adjusted", days=DAYS, **cost):
    meta = meta or {"9999": {"industry": "X", "name": "MOCK"}}
    return engine(cfg, basis, **cost).run(days, {"9999": bars} if "9999" not in bars else bars, sigs, actions or {}, meta)


def only_trade(res):
    assert len(res.trades) == 1, res.trades
    return res.trades[0]


def test_entry_next_open_and_initial_stop_touch(cfg):
    b = flat()
    b[DAYS[3]] = bar(100.0, 100.5, 94.9, 99.0)          # low 94.9 touches the stop 100 - 2.5*2 = 95
    res = run(cfg, b, signal(DAYS[1]))
    t = only_trade(res)
    assert t["entry_date"] == DAYS[2] and t["entry_price"] == 100.0 and t["initial_stop"] == 95.0
    assert t["exit_date"] == DAYS[3] and t["exit_reason"] == "STOP_INITIAL" and t["exit_price"] == 95.0
    # accounting identity: ledger sums to final cash; equity == cash when flat
    assert sum(x["amount"] for x in res.cash) == pytest.approx(res.daily[-1]["cash"])
    assert res.daily[-1]["equity"] == pytest.approx(1_000_000 + t["pnl"])
    # exact: 1,000 shares (10% cap) x 5 loss + buy fee 85.5 + sell fee 81.225 + 0.3% tax on 95,000 = -5,451.725
    assert t["pnl"] == pytest.approx(-5451.725)
    assert t["r_multiple"] == pytest.approx(-5451.725 / 5000)       # costs add ~0.09R to a stopped-out loss


def test_gap_through_stop_fills_at_open(cfg):
    b = flat()
    b[DAYS[3]] = bar(92.0, 93.0, 91.0, 92.5)
    t = only_trade(run(cfg, b, signal(DAYS[1])))
    assert t["exit_reason"] == "STOP_INITIAL_GAP" and t["exit_price"] == 92.0 and t["r_multiple"] < -1.5


def test_limit_up_open_and_chase_limit_not_filled(cfg):
    b = flat()
    b[DAYS[2]] = bar(110.0, 110.0, 110.0, 110.0, lu=110.0)
    res = run(cfg, b, signal(DAYS[1]))
    assert res.trades == [] and res.skipped["limit_up_open"] == 1
    b[DAYS[2]] = bar(103.0, 104.0, 102.0, 103.0, lu=110.0)               # > close 100 + 1 ATR (2)
    res = run(cfg, b, signal(DAYS[1]))
    assert res.trades == [] and res.skipped["gap_above_chase_limit"] == 1


def _ex_dividend_case():
    """Entry 100, stop 95. Cash dividend 6 on DAYS[5]: raw price drops ~6, adjusted series is continuous."""
    f = 94.5 / 100.5
    b = {d: bar(100.0, 100.5, 99.5, 100.5, pf=f) for d in DAYS[:5]}
    b.update({d: bar(94.5, 95.0, 94.0, 94.5, pf=1.0) for d in DAYS[5:20]})
    acts = {DAYS[5]: [{"stock_id": "9999", "action_type": "cash_dividend", "before_price": 100.5,
                       "reference_price": 94.5}]}
    return b, acts


def test_ex_dividend_does_not_trigger_stop_with_adjusted_basis(cfg):
    """User decision C1: tracking on adjusted prices avoids a false stop on the ex-dividend day."""
    b, acts = _ex_dividend_case()
    res = run(cfg, b, signal(DAYS[1]), acts, basis="adjusted", days=DAYS[:20])
    t = only_trade(res)
    assert t["exit_reason"] == "OPEN_AT_END" and t["dividends"] == pytest.approx(t["shares"] * 6.0)


def test_raw_basis_comparison_variant_is_stopped_by_the_dividend_gap(cfg):
    b, acts = _ex_dividend_case()
    t = only_trade(run(cfg, b, signal(DAYS[1]), acts, basis="raw", days=DAYS[:20]))
    assert t["exit_reason"] == "STOP_INITIAL_GAP" and t["exit_date"] == DAYS[5]
    assert t["dividends"] == pytest.approx(t["shares"] * 6.0)            # the dividend is still received


def test_split_rescales_shares_without_stopping(cfg):
    b = {d: bar(100.0, 100.5, 99.5, 100.0, pf=0.25) for d in DAYS[:5]}
    b.update({d: bar(25.0, 25.2, 24.9, 25.0, pf=1.0, atr=0.5) for d in DAYS[5:20]})
    acts = {DAYS[5]: [{"stock_id": "9999", "action_type": "split", "before_price": 100.0, "reference_price": 25.0}]}
    for basis in ("adjusted", "raw"):
        res = run(cfg, b, signal(DAYS[1]), acts, basis=basis, days=DAYS[:20])
        t = only_trade(res)
        assert t["exit_reason"] == "OPEN_AT_END", basis
        assert res.daily[-1]["market_value"] == pytest.approx(res.daily[4]["market_value"], rel=1e-9)


def test_trailing_stop_only_rises_and_exits(cfg):
    prices = [100, 100, 100, 104, 108, 112, 116, 120, 118, 114, 109]
    b = {d: bar(p, p + 0.5, p - 0.5, p) for d, p in zip(DAYS, prices)}
    res = run(cfg, b, signal(DAYS[1]), days=DAYS[:len(prices)])
    t = only_trade(res)
    stops = [r["stop_price"] for r in res.positions]
    assert all(b2 >= a2 - 1e-9 for a2, b2 in zip(stops, stops[1:]))          # never moves down
    assert t["exit_reason"].startswith("STOP_TRAIL") and t["exit_price"] == pytest.approx(114.0)   # 120 - 3*2


def test_trend_exit_after_two_closes_below_ma34(cfg):
    b = {d: bar(100.0, 100.5, 99.5, 100.0, ma34=99.0) for d in DAYS[:10]}
    b[DAYS[4]] = bar(100.0, 100.5, 98.4, 98.5, ma34=99.0)
    b[DAYS[5]] = bar(98.5, 99.0, 98.0, 98.2, ma34=99.0)
    t = only_trade(run(cfg, b, signal(DAYS[1]), days=DAYS[:10]))
    assert t["exit_reason"] == "TREND_MA34" and t["exit_date"] == DAYS[6]   # next open after the 2nd close


def test_time_stop_after_40_bars_without_1r(cfg):
    t = only_trade(run(cfg, flat(), signal(DAYS[1])))
    assert t["exit_reason"] == "TIME_STOP" and t["holding_days"] == 40
    assert t["exit_date"] == DAYS[2 + 40]                                    # entry day counts as bar 1


def test_limit_down_lock_defers_the_stop(cfg):
    b = flat()
    b[DAYS[3]] = bar(90.0, 90.0, 90.0, 90.0, ld=90.0)                       # locked limit-down: cannot sell
    b[DAYS[4]] = bar(88.0, 89.0, 87.0, 88.0, ld=81.0)
    res = run(cfg, b, signal(DAYS[1]))
    t = only_trade(res)
    assert res.skipped["stop_locked_limit_down"] == 1
    assert t["exit_date"] == DAYS[4] and t["exit_price"] == 88.0 and t["exit_reason"] == "STOP_INITIAL_DEFERRED"


def test_delisted_position_closed_at_last_price(cfg):
    b = flat(n=6)
    meta = {"9999": {"industry": "X", "name": "MOCK", "delisting_date": DAYS[8]}}
    t = only_trade(run(cfg, b, signal(DAYS[1]), meta=meta, days=DAYS[:12]))
    assert t["exit_reason"] == "DELISTED" and t["exit_date"] == DAYS[8] and t["exit_price"] == 100.0


def test_whole_lots_before_odd_lot_era_and_weight_cap(cfg):
    old = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2019-03-01", periods=10)]
    b = {d: bar(100.0, 100.5, 99.5, 100.0) for d in old}
    t = only_trade(run(cfg, b, signal(old[1]), days=old))
    assert t["shares"] == 1000                                               # 10% cap = 1,000 shares, whole lots
    b = {d: bar(1200.0, 1201.0, 1199.0, 1200.0, atr=20.0) for d in old}      # 1 lot = 1.2M > 10% of 1M
    res = run(cfg, b, signal(old[1], close=1200.0, atr=20.0), days=old)
    assert res.trades == [] and any(k.startswith("size_zero") for k in res.skipped)
    res = run(cfg, {d: bar(1200.0, 1201.0, 1199.0, 1200.0, atr=20.0) for d in DAYS[:10]},
              signal(DAYS[1], close=1200.0, atr=20.0), days=DAYS[:10])
    assert 0 < only_trade(res)["shares"] < 1000                              # odd lots after 2020-10-26


def test_no_lookahead_future_bars_do_not_change_the_past(cfg):
    b = flat()
    res = run(cfg, b, signal(DAYS[1]))
    k = 10
    b2 = copy.deepcopy(b)
    for d in DAYS[k + 1:]:
        b2[d] = bar(50.0, 51.0, 49.0, 50.0)
    res2 = run(cfg, b2, signal(DAYS[1]))
    assert res.daily[:k + 1] == res2.daily[:k + 1]


def test_meta_without_delisting_date_is_handled(cfg):
    """Regression (real data): a missing delisting date must not break the delisting check."""
    b = flat(n=6)
    res = run(cfg, b, signal(DAYS[1]), meta={"9999": {"industry": None, "name": None, "delisting_date": None}},
              days=DAYS[:12])
    assert only_trade(res)["exit_reason"] == "OPEN_AT_END"


def test_par_value_change_keeps_risk_and_r_in_entry_units(cfg):
    """Regression (real data, 2025): after a 10:1 par-value change open risk exploded to 28% of equity because
    the entry price stayed in old units while shares were rescaled. R must use entry values."""
    b = {d: bar(100.0, 100.5, 99.5, 100.0, pf=0.1) for d in DAYS[:5]}
    b.update({d: bar(10.0, 10.05, 9.95, 10.0, pf=1.0, atr=0.2) for d in DAYS[5:8]})
    b[DAYS[8]] = bar(9.4, 9.45, 9.3, 9.4, pf=1.0, atr=0.2)           # below the stop 9.5 (=95 in old units): gap
    acts = {DAYS[5]: [{"stock_id": "9999", "action_type": "par_value_change", "before_price": 100.0,
                       "reference_price": 10.0}]}
    res = run(cfg, b, signal(DAYS[1]), acts, days=DAYS[:10])
    t = only_trade(res)
    assert t["shares"] == 1000 and t["entry_price"] == 100.0           # reported in entry units
    assert max(r["open_risk"] / r["equity"] for r in res.daily) < 0.006  # ~0.5%, not 5x more
    assert t["r_multiple"] == pytest.approx(t["pnl"] / 5000.0)         # 1,000 shares x (100 - 95)


def test_ranking_does_not_depend_on_input_order(cfg):
    """Regression: identical signals delivered in a different order must give identical results."""
    bars = {sid: flat() for sid in ("1111", "2222", "3333", "4444")}
    meta = {sid: {"industry": sid, "name": sid} for sid in bars}
    sigs = [{"stock_id": sid, "signal_date": DAYS[1], "close": 100.0, "atr": 2.0, "turnover": 1e9} for sid in bars]
    cash_cfg = copy.deepcopy(cfg)
    cash_cfg["account"]["max_gross_exposure"] = 0.20          # room for exactly two 10% positions
    a = engine(cash_cfg).run(DAYS[:5], bars, {DAYS[1]: sigs}, {}, meta)
    b = engine(cash_cfg).run(DAYS[:5], bars, {DAYS[1]: list(reversed(sigs))}, {}, meta)
    assert [t["stock_id"] for t in a.trades] == [t["stock_id"] for t in b.trades]
    assert len(a.trades) == 2
