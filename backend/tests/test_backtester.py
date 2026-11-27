"""Stage 4: metrics and the database-driven backtester. MOCK DATA (synthetic stock seed 3 / hand-made)."""
import json
import math

import pandas as pd
import pytest

from backend.backtesting import performance as pf
from backend.backtesting.backtester import Backtester
from backend.data.pipeline import DataPipeline
from backend.strategies.runner import SignalRunner

from .test_runner import AS_OF, loaded, stock3  # noqa: F401  (fixtures)


def test_wilson_interval_known_values():
    lo, hi = pf.wilson(50, 100)
    assert lo == pytest.approx(0.4038, abs=1e-4) and hi == pytest.approx(0.5962, abs=1e-4)
    lo, hi = pf.wilson(3, 5)                                   # few trades -> very wide interval
    assert hi - lo > 0.5


def test_equity_metrics_drawdown_and_cagr():
    eq = [100, 110, 99, 121, 121]
    d = pd.DataFrame({"date": [f"2024-01-0{i + 1}" for i in range(5)], "equity": eq, "exposure": 0.5})
    m = pf.equity_metrics(d)
    assert m["max_drawdown"] == pytest.approx(0.1) and m["total_return"] == pytest.approx(0.21)
    assert m["max_drawdown_duration_days"] == 1 and m["recovery_days_from_max_drawdown"] == 1


def test_trade_metrics_counts_breakeven_and_streaks():
    t = pd.DataFrame({"trade_id": range(6), "is_closed": 1, "exit_date": [f"2024-02-0{i + 1}" for i in range(6)],
                      "return_pct": [0.05, 0.02, -0.03, 0.0005, -0.01, -0.02], "pnl": [500, 200, -300, 5, -100, -200],
                      "r_multiple": [1.0, 0.4, -0.6, 0.0, -0.2, -0.4], "holding_days": 10, "costs": 10.0,
                      "invested": 10_000.0})
    m = pf.trade_metrics(t)
    assert (m["wins"], m["losses"], m["breakevens"]) == (2, 3, 1)
    assert m["win_rate"] == pytest.approx(2 / 6) and m["profit_factor"] == pytest.approx(705 / 600)
    assert m["max_consecutive_wins"] == 2 and m["max_consecutive_losses"] == 2


def test_split_segments_chronological_with_embargo():
    days = [f"d{i:04d}" for i in range(1000)]
    s = pf.split_segments(days, embargo=40)
    assert s["IS"] == ("d0000", "d0599") and s["VALIDATION"] == ("d0640", "d0799") and s["OOS"] == ("d0840", "d0999")


@pytest.fixture
def ready(loaded, cfg):
    stats = SignalRunner(loaded, cfg, AS_OF).run(["9999"])
    DataPipeline(loaded, AS_OF).validate(["9999"], __import__("pathlib").Path("/tmp") / "dq_test_reports")
    return loaded, stats["versions"]["include_today"]


def test_backtest_end_to_end_and_append_only(ready, cfg):
    db, vid = ready
    bt = Backtester(db, cfg)
    out = bt.run(strategy_version_id=vid, entry_version="A", stock_ids=["9999"], mode="single_stock",
                 start="2014-06-01", end=AS_OF)
    run = db.read_sql("SELECT * FROM backtest_runs WHERE run_id=?", (out["run_id"],)).iloc[0]
    assert run["mode"] == "single_stock" and run["universe_size"] == 1 and run["trailing_basis"] == "adjusted"
    cash = db.read_sql("SELECT SUM(amount) s FROM portfolio_cash WHERE run_id=?", (out["run_id"],)).s[0]
    last = db.read_sql("SELECT cash, equity, market_value FROM portfolio_daily WHERE run_id=? ORDER BY date DESC LIMIT 1",
                       (out["run_id"],)).iloc[0]
    assert cash == pytest.approx(last.cash)                       # ledger reconciles to the final cash
    assert last.equity == pytest.approx(last.cash + last.market_value)
    trades = db.read_sql("SELECT * FROM simulated_trades WHERE run_id=?", (out["run_id"],))
    assert len(trades) > 0 and (trades.entry_date > trades.signal_date).all()   # never filled on the signal day
    m = db.read_sql("SELECT segment, metric, value FROM performance_metrics WHERE run_id=?", (out["run_id"],))
    assert {"ALL", "IS", "VALIDATION", "OOS"} <= set(m.segment)
    out2 = bt.run(strategy_version_id=vid, entry_version="A", stock_ids=["9999"], mode="single_stock",
                  start="2014-06-01", end=AS_OF, cost_scenario="stress", slippage_end="high")
    assert out2["run_id"] != out["run_id"] and len(db.read_sql("SELECT * FROM backtest_runs")) == 2
    assert db.read_sql("SELECT slippage_bps FROM backtest_runs WHERE run_id=?", (out2["run_id"],)).iloc[0, 0] == 30.0


def test_fail_and_unverified_stocks_are_excluded(ready, cfg):
    db, vid = ready
    db.insert("data_quality_verdicts", [{"run_id": "dq-test", "stock_id": "9999", "verdict": "FAIL", "n_error": 1,
                                         "n_warning": 0, "n_info": 0, "reasons": "x",
                                         "created_at": "2099-01-01T00:00:00+00:00"}])
    with pytest.raises(ValueError, match="no usable stocks"):
        Backtester(db, cfg).run(strategy_version_id=vid, stock_ids=["9999"], start="2014-06-01", end=AS_OF)
    ids, excluded = Backtester(db, cfg).universe(["9999", "1234"])
    assert ids == [] and excluded == {"FAIL": ["9999"], "no_verdict": ["1234"]}
