import numpy as np
import pandas as pd

from src.indicators import bars_since_true_within, bollinger, percent_rank, rma, rolling_std, sma, true_range, wilder_atr


def test_std_is_population_std():
    s = pd.Series(np.arange(30, dtype=float) ** 1.3)
    got = rolling_std(s, 20).iloc[-1]
    assert np.isclose(got, np.std(s.iloc[-20:].to_numpy(), ddof=0))
    assert not np.isclose(got, s.iloc[-20:].std())  # pandas default ddof=1 differs


def test_bollinger_bandwidth_identity():
    s = pd.Series(100 + np.sin(np.arange(60)) * 3)
    bb = bollinger(s, 20, 2.0)
    sd = rolling_std(s, 20)
    assert np.allclose((4 * sd / sma(s, 20)).dropna(), bb["bbw"].dropna())


def test_rma_seed_and_recursion():
    s = pd.Series([np.nan, 1.0, 2, 3, 4, 5, 6])
    r = rma(s, 3)
    assert r.iloc[:3].isna().all()
    assert r.iloc[3] == 2.0                       # SMA of 1,2,3
    assert np.isclose(r.iloc[4], 2.0 + (4 - 2.0) / 3)


def test_atr_matches_reference_loop(stock):
    h, l, c = stock["high"], stock["low"], stock["close"]
    atr = wilder_atr(h, l, c, 14).to_numpy()
    tr = [h.iloc[0] - l.iloc[0]] + [
        max(h.iloc[i] - l.iloc[i], abs(h.iloc[i] - c.iloc[i - 1]), abs(l.iloc[i] - c.iloc[i - 1])) for i in range(1, len(c))
    ]
    ref = [np.nan] * 13 + [np.mean(tr[:14])]
    for x in tr[14:]:
        ref.append(ref[-1] + (x - ref[-1]) / 14)
    assert np.allclose(atr, ref, equal_nan=True)
    assert np.allclose(true_range(h, l, c).iloc[1:], tr[1:])


def test_percent_rank_excludes_current_bar():
    # A new all-time low must rank 0%. If the current bar were inside the window
    # (as with rolling(n).rank(pct=True)) it would compare <= itself and rank 25%.
    s = pd.Series([4.0, 3, 2, 1, 0])
    pr = percent_rank(s, 4)
    assert pr.iloc[:4].isna().all()
    assert pr.iloc[4] == 0.0


def test_percent_rank_window_definition():
    s = pd.Series([5.0, 1, 2, 3, 4, 3])
    pr = percent_rank(s, 4)
    # bar 4: previous 4 = bars 0..3 = [5,1,2,3]; values <= 4 -> 3 of 4
    # bar 5: previous 4 = bars 1..4 = [1,2,3,4]; values <= 3 -> 3 of 4
    assert pr.iloc[4] == 75.0 and pr.iloc[5] == 75.0


def test_percent_rank_brute_force(stock):
    s = stock["close"].pct_change().abs()
    n = 120
    pr = percent_rank(s, n)
    ref = pd.Series(np.nan, index=s.index)
    x = s.to_numpy()
    for t in range(n, len(x)):
        w = x[t - n : t]
        if not (np.isnan(w).any() or np.isnan(x[t])):
            ref.iloc[t] = (w <= x[t]).sum() * 100 / n
    assert np.allclose(pr, ref, equal_nan=True)


def test_recent_window_includes_current():
    c = pd.Series([False, False, True, False, False, False, False, False, False])
    r = bars_since_true_within(c, 6)
    assert list(r.iloc[5:]) == [True, True, True, False]
