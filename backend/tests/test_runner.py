"""Stage 3: signal runner through the database. MOCK DATA: Phase 1 synthetic stock, seed 3
(seed 3 is used because it produces E-version entries; seed 0 has none)."""
import copy

import pandas as pd
import pytest

from backend.data.pipeline import DataPipeline
from backend.database.db_manager import DatabaseManager
from backend.strategies.fib_bb_weekly import generate
from backend.strategies.runner import SignalRunner, daily_screen, explain

from .conftest import make_stock

NOW = "2026-11-27T00:00:00+00:00"
AS_OF = "2017-12-29"


@pytest.fixture
def stock3():
    return make_stock(3)


@pytest.fixture
def loaded(tmp_path, stock3):
    stock = stock3
    db = DatabaseManager(tmp_path / "s.sqlite")
    db.initialize()
    days = stock.index.strftime("%Y-%m-%d")
    db.upsert("security_master", [{"stock_id": "9999", "stock_name": "MOCK", "exchange": "TWSE",
                                   "industry_category": "測試", "security_type": "common", "source": "mock",
                                   "updated_at": NOW}])
    db.upsert("trading_calendar", [{"date": d, "exchange": x, "is_trading_day": 1, "session_type": "regular",
                                    "source": "mock", "ingested_at": NOW} for d in days for x in ("TWSE", "TPEX")])
    raw = pd.DataFrame({"stock_id": "9999", "date": days, "open": stock["open"].to_numpy(),
                        "high": stock["high"].to_numpy(), "low": stock["low"].to_numpy(),
                        "close": stock["close"].to_numpy(), "volume": stock["volume"].round().astype(int).to_numpy(),
                        "turnover": (stock["close"] * stock["volume"]).to_numpy(), "trade_count": 1, "has_price": 1,
                        "source": "mock", "ingested_at": NOW})
    db.upsert("daily_prices_raw", raw)
    DataPipeline(db, AS_OF).process(["9999"])           # no corporate actions: adjusted == raw
    yield db
    db.close()


def test_runner_matches_direct_strategy_and_records_as_of(loaded, cfg, stock3):
    stock = stock3
    r = SignalRunner(loaded, cfg, AS_OF)
    stats = r.run(["9999"])
    vid = stats["versions"]["include_today"]
    sig = loaded.read_sql("SELECT * FROM strategy_signals_readable WHERE strategy_version_id=? ORDER BY date", (vid,))
    direct = r.load_input("9999")
    ref = generate(direct, cfg)                          # same input, called directly
    ref_e = ref.loc[pd.to_datetime(sig.date), "entry_E"].astype(int).tolist()
    assert sig["entry_E"].astype(int).tolist() == ref_e
    assert sum(ref["entry_E"]) == stats["entries"]["include_today"]["E"] > 0
    first = sig.iloc[0]
    assert first.signal_as_of == first.date + "T13:30:00+08:00"
    days = list(stock.index.strftime("%Y-%m-%d"))
    assert first.earliest_entry_date == days[days.index(first.date) + 1]
    assert pd.isna(sig.iloc[-1].earliest_entry_date)    # last day: next trading day not yet known


def test_two_volume_versions_and_immutability(loaded, cfg):
    r = SignalRunner(loaded, cfg, AS_OF)
    v1 = r.run(["9999"])["versions"]
    assert len(set(v1.values())) == 2
    assert r.run(["9999"])["versions"] == v1                          # same params -> same ids, no duplicates
    assert len(loaded.read_sql("SELECT * FROM strategy_versions")) == 2
    c2 = copy.deepcopy(cfg)
    c2["bollinger"]["squeeze_pct"] = 15.0
    v2 = SignalRunner(loaded, c2, AS_OF).run(["9999"])["versions"]
    assert set(v2.values()).isdisjoint(v1.values())                   # changed params -> new versions kept beside old
    assert len(loaded.read_sql("SELECT * FROM strategy_versions")) == 4


def test_explain_and_daily_screen(loaded, cfg):
    stats = SignalRunner(loaded, cfg, AS_OF).run(["9999"])
    vid = stats["versions"]["include_today"]
    d = loaded.read_sql("SELECT date FROM strategy_signals WHERE strategy_version_id=? AND (entries & 16) > 0 LIMIT 1",
                        (vid,)).date[0]
    ex = explain(loaded, "9999", d, vid)
    assert ex["available"] and ex["entries"]["E"]
    assert all(c["passed"] for c in ex["conditions"] if c["name"] in ("daily_trend", "breakout", "weekly_trend"))
    scr = daily_screen(loaded, d, vid, "E")
    assert scr.stock_id.tolist() == ["9999"]
    row = scr.iloc[0]
    assert row.reference_initial_stop == pytest.approx(row.close_raw - 2.5 * row.atr14_raw)
    assert not explain(loaded, "9999", "2013-01-03", vid)["available"]    # warm-up period
