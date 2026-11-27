import pandas as pd

from backend.strategies.fib_bb_weekly import VERSIONS, generate


def test_entry_E_matches_spec_formula(stock, cfg):
    f = generate(stock, cfg)
    state = (
        f["eligible"]
        & f["daily_trend"]
        & f["weekly_trend"]
        & f["recent_squeeze"]
        & (stock["adj_close"] > f["bb_upper"])
        & (stock["adj_volume"] > 1.5 * f["volume_ma"])
        & (f["turnover_ma"] >= 20_000_000)
    )
    expected = state & ~state.shift(1, fill_value=False)
    pd.testing.assert_series_equal(f["entry_E"], expected, check_names=False)


def test_versions_are_nested_states(stock, cfg):
    f = generate(stock, cfg)
    order = ["A", "B", "C", "D", "E"]
    states = {}
    for v in order:
        s = f["eligible"].copy()
        for c in VERSIONS[v]:
            s &= f[c]
        states[v] = s
    for lo, hi in zip(order[1:], order[:-1]):
        assert not (states[lo] & ~states[hi]).any(), f"{lo} state must imply {hi} state"


def test_no_signal_during_warmup(stock, cfg):
    f = generate(stock, cfg)
    entries = f.filter(like="entry_")
    assert not entries[~f["eligible"]].any().any()
    # Weekly MA55 + slope needs 56 weekly bars + 1 week shift -> > 1 year of warm-up.
    first_eligible = f.index[f["eligible"]].min()
    assert first_eligible >= stock.index[0] + pd.Timedelta(days=365)


def test_untradable_days_never_signal(stock, cfg):
    df = stock.copy()
    df.loc["2016-01-01":"2016-12-31", "tradable"] = False
    f = generate(df, cfg)
    assert not f.loc["2016", f.columns.str.startswith("entry_")].any().any()


def test_liquidity_uses_raw_turnover(stock, cfg):
    f = generate(stock, cfg)
    ref = (stock["close"] * stock["volume"]).rolling(20).mean()
    pd.testing.assert_series_equal(f["turnover_ma"], ref, check_names=False)


def test_volume_modes(stock, cfg):
    """Prompt 04 §8.2: 'include_today' vs 'prior_20' (previous 20 complete bars)."""
    import copy

    f = generate(stock, cfg)
    v = stock["adj_volume"]
    assert (f["volume_ma_prior"] == f["volume_ma_incl"].shift(1)).iloc[25:].all()
    assert f["volume_confirm"].equals(f["volume_confirm_incl"])           # default mode
    c2 = copy.deepcopy(cfg)
    c2["breakout"]["volume_ma_mode"] = "prior_20"
    g = generate(stock, c2)
    assert g["volume_confirm"].equals(g["volume_confirm_prior"])
    ref = v > 1.5 * v.rolling(20).mean().shift(1)
    pd.testing.assert_series_equal(g["volume_confirm_prior"], ref, check_names=False)
    # the two definitions really differ on this data (otherwise the comparison would be pointless)
    assert (f["volume_confirm_incl"] != f["volume_confirm_prior"]).any()
    c3 = copy.deepcopy(cfg)
    c3["breakout"]["volume_ma_mode"] = "bogus"
    import pytest
    with pytest.raises(ValueError):
        generate(stock, c3)
