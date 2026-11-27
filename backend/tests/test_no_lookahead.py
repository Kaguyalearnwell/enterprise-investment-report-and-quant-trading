"""Causality tests: anything known at the close of day t must not change when
data AFTER day t is altered. This is the repaint / look-ahead test that also
guards the weekly alignment (the rest of the current week is "future")."""
import numpy as np
import pandas as pd
import pytest

from backend.strategies.fib_bb_weekly import ADJ_COLS, generate
from backend.indicators.weekly import resample_weekly, weekly_filter, weekly_trend, week_key

PRICE = ["open", "high", "low", "close", "adj_open", "adj_high", "adj_low", "adj_close"]


def perturb_after(df: pd.DataFrame, t: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    out = df.copy()
    k = len(df) - t - 1
    shock = np.exp(np.cumsum(rng.normal(0, 0.05, k)))
    for c in PRICE:
        out.iloc[t + 1 :, out.columns.get_loc(c)] = out[c].iloc[t + 1 :].to_numpy() * shock
    for c in ["volume", "adj_volume"]:
        out.iloc[t + 1 :, out.columns.get_loc(c)] = out[c].iloc[t + 1 :].to_numpy() * rng.uniform(0.1, 10, k)
    return out


def frames_equal_upto(a: pd.DataFrame, b: pd.DataFrame, t: int) -> bool:
    pd.testing.assert_frame_equal(a.iloc[: t + 1], b.iloc[: t + 1])
    return True


@pytest.mark.parametrize("seed", range(6))
def test_full_feature_set_is_causal(stock, cfg, seed):
    rng = np.random.default_rng(100 + seed)
    t = int(rng.integers(400, len(stock) - 50))
    base = generate(stock, cfg)
    alt = generate(perturb_after(stock, t, seed), cfg)
    assert frames_equal_upto(base, alt, t)


def test_weekly_uses_only_previous_completed_week(stock, cfg):
    """Cut on a Wednesday: Mon-Wed of that week must not depend on Thu/Fri."""
    weds = [i for i, d in enumerate(stock.index) if d.weekday() == 2 and i > 600]
    for t in weds[:20]:
        base = weekly_filter(stock, cfg["weekly"], ADJ_COLS)
        alt = weekly_filter(perturb_after(stock, t, t), cfg["weekly"], ADJ_COLS)
        assert frames_equal_upto(base, alt, t)


def current_week_alignment(daily: pd.DataFrame, cfg) -> pd.Series:
    """A deliberately WRONG alignment (uses the still-forming current week)."""
    wt = weekly_trend(resample_weekly(daily, ADJ_COLS), cfg["ma_fast"], cfg["ma_mid"], cfg["ma_slow"])
    return pd.Series(wt["w_ma_fast"].reindex(week_key(daily.index).to_numpy()).to_numpy(), index=daily.index)


def test_perturbation_test_detects_repainting(stock, cfg):
    """Power check: the same test must FAIL for a repainting implementation."""
    weds = [i for i, d in enumerate(stock.index) if d.weekday() == 2 and i > 600][:10]
    leaked = 0
    for t in weds:
        a = current_week_alignment(stock, cfg["weekly"]).iloc[: t + 1]
        b = current_week_alignment(perturb_after(stock, t, t), cfg["weekly"]).iloc[: t + 1]
        leaked += not np.allclose(a, b, equal_nan=True)
    assert leaked == len(weds)


def test_weekly_alignment_with_holidays(stock, cfg):
    w = weekly_filter(stock, cfg["weekly"], ADJ_COLS)
    wk = resample_weekly(stock, ADJ_COLS)
    # Whole ISO week 2016-W06 closed: the next week must source from 2016-W05.
    d = stock.index[stock.index >= "2016-02-15"][0]
    assert w.loc[d, "source_week_end"] == wk.loc[201605, "week_end"]
    # Week whose Friday is closed: its weekly bar ends on Thursday, and the
    # following Monday sees that Thursday-ended bar.
    assert wk.loc[201623, "week_end"] == pd.Timestamp("2016-06-09")
    assert w.loc[pd.Timestamp("2016-06-13"), "source_week_end"] == pd.Timestamp("2016-06-09")
    # Every daily bar sources from a week that ended strictly before it.
    src = w["source_week_end"].dropna()
    assert (src.index > src).all()


def test_signals_scale_invariant(stock, cfg):
    """Back-adjusted histories get rescaled by later corporate actions; signals must not move."""
    scaled = stock.copy()
    for c in ["adj_open", "adj_high", "adj_low", "adj_close"]:
        scaled[c] *= 0.37
    a, b = generate(stock, cfg), generate(scaled, cfg)
    bool_cols = [c for c in a.columns if a[c].dtype == bool]
    pd.testing.assert_frame_equal(a[bool_cols], b[bool_cols])
    assert np.allclose(a["atr_raw"], b["atr_raw"], equal_nan=True)
