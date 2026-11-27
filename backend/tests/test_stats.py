"""Stage 5 statistical tools: known-answer tests (MOCK / simulated data)."""
import math

import numpy as np
import pytest

from backend.experiments import stats


def test_phi_and_mutual_information_known_values():
    x = np.array([1, 1, 0, 0, 1, 0, 1, 0], bool)
    assert stats.phi(x, x) == pytest.approx(1.0) and stats.phi(x, ~x) == pytest.approx(-1.0)
    assert stats.mutual_information(x, x) == pytest.approx(1.0)               # H(fair coin) = 1 bit
    x2 = np.array([1, 1, 1, 1, 0, 0, 0, 0], bool)
    y2 = np.array([1, 1, 0, 0, 1, 1, 0, 0], bool)                            # 2/2/2/2 table: independent
    assert stats.phi(x2, y2) == pytest.approx(0.0) and stats.mutual_information(x2, y2) == pytest.approx(0.0)
    a, b, c, d = 30, 10, 20, 40                                                # 2x2 table
    xx = np.r_[np.ones(a + b, bool), np.zeros(c + d, bool)]
    yy = np.r_[np.ones(a, bool), np.zeros(b, bool), np.ones(c, bool), np.zeros(d, bool)]
    assert stats.phi(xx, yy) == pytest.approx((a * d - b * c) / math.sqrt(40 * 60 * 50 * 50))


def test_bootstrap_ci_covers_mean_and_paired_difference():
    rng = np.random.default_rng(1)
    r = rng.normal(0.001, 0.01, 1500)
    dist = stats.bootstrap([r], lambda x: x.mean(), n_boot=500, seed=2)
    lo, hi = np.percentile(dist, [2.5, 97.5])
    assert lo < 0.001 < hi
    diff = stats.bootstrap([r, r], lambda a, b: a.mean() - b.mean(), n_boot=50, seed=3)
    assert np.allclose(diff, 0.0)                                              # paired: identical series


def test_mc_reshuffle_preserves_total_and_orders_quantiles():
    out = stats.mc_reshuffle([100, -50, -50, 200, -100, 30], 1000.0, n_sims=500)
    assert 0 < out["mdd_p50"] <= out["mdd_p95"] <= out["mdd_p99"] < 1
    assert out["losing_streak_p95"] <= 3


def test_psr_and_dsr():
    assert stats.probabilistic_sharpe(0.0, 0.0, 1000, 0.0, 3.0) == pytest.approx(0.5)
    rng = np.random.default_rng(4)
    good = rng.normal(0.002, 0.01, 2000)                                       # annual SR ~ 3
    one = stats.deflated_sharpe(good, [0.2])
    many = stats.deflated_sharpe(good, list(rng.normal(0, 0.05, 1000)))
    assert one["dsr"] > 0.99 and many["dsr"] < one["dsr"]                      # more trials -> lower DSR
    assert stats.expected_max_sharpe(0.0025, 1000) > stats.expected_max_sharpe(0.0025, 10) > 0


def test_pbo_noise_vs_real_skill():
    rng = np.random.default_rng(5)
    noise = rng.normal(0, 0.01, (1600, 20))
    assert 0.3 < stats.pbo_cscv(noise, 16)["pbo"] < 0.7                       # pure noise -> about 0.5
    skill = noise.copy()
    skill[:, 0] += 0.004                                                       # one trial truly better
    assert stats.pbo_cscv(skill, 16)["pbo"] < 0.05


def test_holm():
    out = stats.holm({"a": 0.001, "b": 0.02, "c": 0.04})
    assert [out[k]["reject_null"] for k in "abc"] == [True, True, True]
    out = stats.holm({"a": 0.001, "b": 0.03, "c": 0.04})
    assert [out[k]["reject_null"] for k in "abc"] == [True, False, False]     # step-down stops at b
