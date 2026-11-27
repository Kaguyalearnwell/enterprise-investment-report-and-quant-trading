"""Stage 5 robustness tools (MOCK data)."""
import math

import numpy as np
import pandas as pd
import pytest

from backend.experiments import robustness as rb

from .test_experiment_context import ctx  # noqa: F401
from .test_runner import loaded, stock3  # noqa: F401


def test_grid_size_and_neighbours():
    assert len(rb.combos()) == 243
    k = rb.key(rb.BASELINE)                    # all middle values -> 2 neighbours per parameter
    assert len(rb.neighbours(k)) == 10
    corner = tuple(v[0] for v in rb.GRID.values())
    assert len(rb.neighbours(corner)) == 5


def test_plateau_beats_isolated_spike():
    keys = [rb.key(c) for c in rb.combos()]
    score = {k: 0.0 for k in keys}
    spike = keys[0]                             # a corner with a lucky high score, neighbours poor
    score[spike] = 5.0
    centre = rb.key(rb.BASELINE)                 # a broad plateau
    score[centre] = 1.0
    for n in rb.neighbours(centre):
        score[n] = 1.0
    assert rb.select_plateau(score) == centre


def test_regimes_are_causal():
    idx = pd.bdate_range("2010-01-01", periods=900)
    rng = np.random.default_rng(0)
    s = pd.Series(10000 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, len(idx)))), index=idx)
    a = rb.regimes(s)
    s2 = s.copy()
    s2.iloc[700:] *= 0.5
    b = rb.regimes(s2)
    pd.testing.assert_frame_equal(a.iloc[:700], b.iloc[:700])
    assert set(a["trend"].dropna()) <= {"bull", "bear", "sideways"}


def test_walk_forward_windows_and_selection():
    idx = pd.bdate_range("2015-01-01", "2021-12-31").strftime("%Y-%m-%d")
    rng = np.random.default_rng(1)
    rets = {rb.key(c): pd.Series(rng.normal(0, 0.01, len(idx)), index=idx) for c in rb.combos()[:30]}
    rets[rb.key(rb.BASELINE)] = pd.Series(rng.normal(0, 0.01, len(idx)), index=idx)
    wf = rb.walk_forward(rets, "2015-01-01", "2021-12-31")
    assert [w["test"][0][:4] for w in wf] == ["2018", "2019", "2020", "2021"]
    assert all(w["train"][1] < w["test"][0] for w in wf)        # test always after train


def test_grid_baseline_reproduces_stored_E_signals(ctx):
    c, _ = ctx
    from backend.strategies.fib_bb_weekly import VERSIONS

    rb._init(c.inputs(), c.cfg, (c.start, c.end))
    mine = rb._signals_for_combo(rb.BASELINE)
    ref = c.signals_for(VERSIONS["E"])
    assert sorted(mine) == sorted(ref)
    assert all(math.isclose(mine[d][0]["atr"], ref[d][0]["atr"]) for d in mine)
