"""Statistical tools for Stage 5. Each function has a known-answer unit test.

References
----------
* Stationary bootstrap: Politis & Romano (1994).
* Probabilistic / Deflated Sharpe Ratio: Bailey & Lopez de Prado (2012, 2014).
* Probability of Backtest Overfitting via CSCV: Bailey, Borwein, Lopez de Prado & Zhu (2017).
* Holm (1979) step-down multiple-testing correction.
"""
from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Sequence

import numpy as np
from scipy import stats as st

EULER = 0.5772156649015329


# ---- dependence between conditions --------------------------------------------------------------
def contingency(x: np.ndarray, y: np.ndarray) -> tuple[int, int, int, int]:
    x, y = np.asarray(x, bool), np.asarray(y, bool)
    return int((x & y).sum()), int((x & ~y).sum()), int((~x & y).sum()), int((~x & ~y).sum())


def phi(x: np.ndarray, y: np.ndarray) -> float:
    a, b, c, d = contingency(x, y)
    den = math.sqrt((a + b) * (c + d) * (a + c) * (b + d))
    return (a * d - b * c) / den if den > 0 else math.nan


def mutual_information(x: np.ndarray, y: np.ndarray) -> float:
    """Mutual information in bits between two binary variables."""
    a, b, c, d = contingency(x, y)
    n = a + b + c + d
    mi = 0.0
    for nxy, nx, ny in ((a, a + b, a + c), (b, a + b, b + d), (c, c + d, a + c), (d, c + d, b + d)):
        if nxy > 0:
            mi += nxy / n * math.log2(nxy * n / (nx * ny))
    return mi


# ---- resampling ------------------------------------------------------------------------------------
def stationary_bootstrap_indices(n: int, mean_block: float, rng: np.random.Generator) -> np.ndarray:
    p = 1.0 / mean_block
    idx = np.empty(n, dtype=np.int64)
    idx[0] = rng.integers(n)
    jumps = rng.random(n) < p
    starts = rng.integers(n, size=n)
    for i in range(1, n):
        idx[i] = starts[i] if jumps[i] else (idx[i - 1] + 1) % n
    return idx


def bootstrap(series: Sequence[np.ndarray], stat: Callable[..., float], n_boot: int = 2000, mean_block: float = 20.0,
              seed: int = 0) -> np.ndarray:
    """Stationary block bootstrap of a statistic of one or several ALIGNED series (paired resampling)."""
    arrs = [np.asarray(s, float) for s in series]
    n = len(arrs[0])
    rng = np.random.default_rng(seed)
    out = np.empty(n_boot)
    for b in range(n_boot):
        idx = stationary_bootstrap_indices(n, mean_block, rng)
        out[b] = stat(*[a[idx] for a in arrs])
    return out


def sharpe(r: np.ndarray, periods: int = 252) -> float:
    r = np.asarray(r, float)
    sd = r.std(ddof=1)
    return float(r.mean() / sd * math.sqrt(periods)) if sd > 0 else math.nan


def mc_reshuffle(pnl: Sequence[float], initial: float, n_sims: int = 5000, seed: int = 0) -> dict:
    """Trade-order Monte Carlo: max drawdown and longest losing streak distributions."""
    p = np.asarray(pnl, float)
    rng = np.random.default_rng(seed)
    mdd, streak = np.empty(n_sims), np.empty(n_sims)
    for k in range(n_sims):
        x = rng.permutation(p)
        eq = initial + np.cumsum(x)
        peak = np.maximum.accumulate(np.r_[initial, eq])[1:]
        mdd[k] = float(np.max(1 - eq / peak)) if len(eq) else 0.0
        best = cur = 0
        for v in x:
            cur = cur + 1 if v < 0 else 0
            best = max(best, cur)
        streak[k] = best
    return {"mdd_p50": float(np.percentile(mdd, 50)), "mdd_p95": float(np.percentile(mdd, 95)),
            "mdd_p99": float(np.percentile(mdd, 99)), "losing_streak_p50": float(np.percentile(streak, 50)),
            "losing_streak_p95": float(np.percentile(streak, 95))}


# ---- Sharpe ratio inference ---------------------------------------------------------------------------
def probabilistic_sharpe(sr: float, sr_benchmark: float, n_obs: int, skew: float, kurt: float) -> float:
    """PSR: probability that the true (per-period) Sharpe exceeds sr_benchmark. kurt is NON-excess kurtosis."""
    den = math.sqrt(max(1 - skew * sr + (kurt - 1) / 4 * sr * sr, 1e-12))
    return float(st.norm.cdf((sr - sr_benchmark) * math.sqrt(n_obs - 1) / den))


def expected_max_sharpe(sr_variance: float, n_trials: int) -> float:
    """Expected maximum of n_trials per-period Sharpe ratios under the null of zero skill."""
    if n_trials <= 1:
        return 0.0
    return math.sqrt(sr_variance) * ((1 - EULER) * st.norm.ppf(1 - 1 / n_trials)
                                     + EULER * st.norm.ppf(1 - 1 / (n_trials * math.e)))


def deflated_sharpe(returns: np.ndarray, trial_sharpes: Sequence[float]) -> dict:
    """DSR of the selected strategy's per-period returns given the per-period Sharpe ratios of ALL trials."""
    r = np.asarray(returns, float)
    sr = r.mean() / r.std(ddof=1)
    trials = np.asarray([s for s in trial_sharpes if not math.isnan(s)], float)
    var = float(trials.var(ddof=1)) if len(trials) > 1 else 0.0
    sr0 = expected_max_sharpe(var, len(trials))
    skew, kurt = float(st.skew(r)), float(st.kurtosis(r, fisher=False))
    return {"sharpe_per_period": float(sr), "sharpe_annual": float(sr * math.sqrt(252)), "n_trials": int(len(trials)),
            "trial_sharpe_variance": var, "sr0_per_period": float(sr0), "skew": skew, "kurtosis": kurt,
            "n_obs": int(len(r)), "dsr": probabilistic_sharpe(sr, sr0, len(r), skew, kurt),
            "psr_vs_zero": probabilistic_sharpe(sr, 0.0, len(r), skew, kurt)}


# ---- Probability of Backtest Overfitting (CSCV) ----------------------------------------------------------
def pbo_cscv(returns: np.ndarray, n_blocks: int = 16) -> dict:
    """returns: T x N matrix (rows = periods, columns = trials). Performance metric: Sharpe ratio."""
    m = np.asarray(returns, float)
    t, n = m.shape
    if n < 2:
        raise ValueError("PBO needs at least two trials")
    usable = (t // n_blocks) * n_blocks
    blocks = m[:usable].reshape(n_blocks, usable // n_blocks, n)
    s1, s2, cnt = blocks.sum(axis=1), (blocks ** 2).sum(axis=1), blocks.shape[1]

    def sr(idx):
        k = len(idx) * cnt
        mu = s1[list(idx)].sum(axis=0) / k
        var = (s2[list(idx)].sum(axis=0) - k * mu * mu) / (k - 1)
        return mu / np.sqrt(np.maximum(var, 1e-18))

    logits, oos_of_best, degr = [], [], []
    all_idx = set(range(n_blocks))
    for comb in itertools.combinations(range(n_blocks), n_blocks // 2):
        is_sr = sr(comb)
        oos_sr = sr(tuple(sorted(all_idx - set(comb))))
        best = int(np.argmax(is_sr))
        rank = (oos_sr < oos_sr[best]).sum() + 0.5 * ((oos_sr == oos_sr[best]).sum() - 1) + 1   # 1..n
        w = rank / (n + 1)
        logits.append(math.log(w / (1 - w)))
        oos_of_best.append(float(oos_sr[best]))
        degr.append((float(is_sr[best]), float(oos_sr[best])))
    lg = np.asarray(logits)
    return {"pbo": float((lg <= 0).mean()), "n_combinations": len(lg), "n_trials": n, "n_blocks": n_blocks,
            "logit_mean": float(lg.mean()), "logit_quantiles": [float(q) for q in np.percentile(lg, [5, 25, 50, 75, 95])],
            "prob_oos_loss_of_best": float((np.asarray(oos_of_best) < 0).mean()),
            "is_vs_oos_slope": float(np.polyfit([d[0] for d in degr], [d[1] for d in degr], 1)[0])}


# ---- multiple testing ----------------------------------------------------------------------------------
def holm(pvalues: dict[str, float], alpha: float = 0.05) -> dict[str, dict]:
    items = sorted(pvalues.items(), key=lambda kv: kv[1])
    m = len(items)
    out, stop = {}, False
    for i, (k, p) in enumerate(items):
        thr = alpha / (m - i)
        reject = (not stop) and p <= thr
        stop = stop or not reject
        out[k] = {"p": p, "threshold": thr, "reject_null": reject}
    return out
