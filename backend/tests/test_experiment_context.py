"""Stage 5: experiment context on the MOCK synthetic stock (seed 3)."""
from pathlib import Path

import numpy as np
import pytest

from backend.data.pipeline import DataPipeline
from backend.database.schema import ENTRY_BITS
from backend.experiments.context import ExperimentContext
from backend.strategies.fib_bb_weekly import VERSIONS
from backend.strategies.runner import SignalRunner

from .test_runner import AS_OF, loaded, stock3  # noqa: F401


@pytest.fixture
def ctx(loaded, cfg, tmp_path):
    vid = SignalRunner(loaded, cfg, AS_OF).run(["9999"])["versions"]["include_today"]
    DataPipeline(loaded, AS_OF).validate(["9999"], tmp_path)
    return ExperimentContext(loaded, cfg, vid, ["9999"], start="2014-06-01", full_end=AS_OF), vid


def test_combination_signals_reproduce_stored_entries(ctx):
    c, vid = ctx
    for v, conds in VERSIONS.items():
        mine = sorted(d for d in c.signals_for(conds))
        stored = c.db.read_sql(f"SELECT date FROM strategy_signals WHERE strategy_version_id=? AND (entries & "
                               f"{ENTRY_BITS[v]}) > 0 AND date BETWEEN ? AND ? ORDER BY date", (vid, c.start, c.end)).date.tolist()
        assert mine == stored, v


def test_oos_is_never_simulated(ctx):
    c, _ = ctx
    assert c.days[-1] == c.segments["VALIDATION"][1] < c.segments["OOS"][0]
    trades, daily = c.simulate(c.signals_for(VERSIONS["A"]))
    assert daily["date"].max() <= c.segments["VALIDATION"][1]
    m = c.metrics(trades, daily)
    assert set(m) == {"IS", "VALIDATION"}


def test_random_like_matches_dates_and_counts(ctx):
    c, _ = ctx
    tmpl = c.signals_for(VERSIONS["B"])
    pool = c.eligible_pool()
    r = c.random_like(tmpl, pool, np.random.default_rng(0))
    assert set(r) <= set(tmpl) and all(len(r[d]) == min(len(tmpl[d]), len(pool[d])) for d in r)
    assert all(s["stock_id"] in {p["stock_id"] for p in pool[d]} for d in r for s in r[d])
