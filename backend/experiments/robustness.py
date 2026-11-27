"""Parameter grid, plateau selection, walk-forward, PBO, DSR, bootstrap, Monte Carlo, market regimes.

The 243-combination signal grid and the 9-combination exit grid are fixed in
docs/stage5_preregistration.md (Appendix A) BEFORE running. Signals for each combination
are computed in memory from inputs truncated at the last Validation day, so no OOS
information can enter the grid, the selection or the walk-forward.
"""
from __future__ import annotations

import copy
import itertools
import math
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from backend.experiments import stats
from backend.experiments.context import ExperimentContext
from backend.strategies.fib_bb_weekly import VERSIONS, compute_features, entry_signals

GRID = {
    "ma_set": [(10, 28, 45), (13, 34, 55), (16, 40, 65)],
    "num_std": [1.5, 2.0, 2.5],
    "squeeze_pct": [10.0, 20.0, 30.0],
    "squeeze_recent_bars": [3, 6, 10],
    "volume_mult": [1.2, 1.5, 2.0],
}
EXIT_GRID = {"stop_atr_mult": [2.0, 2.5, 3.0], "trail_atr_mult": [2.0, 3.0, 4.0]}
BASELINE = {"ma_set": (13, 34, 55), "num_std": 2.0, "squeeze_pct": 20.0, "squeeze_recent_bars": 6, "volume_mult": 1.5}


def combos() -> list[dict]:
    keys = list(GRID)
    return [dict(zip(keys, v)) for v in itertools.product(*[GRID[k] for k in keys])]


def apply(cfg: dict, combo: dict) -> dict:
    c = copy.deepcopy(cfg)
    c["trend"]["ma_fast"], c["trend"]["ma_mid"], c["trend"]["ma_slow"] = combo["ma_set"]
    c["bollinger"]["num_std"] = combo["num_std"]
    c["bollinger"]["squeeze_pct"] = combo["squeeze_pct"]
    c["bollinger"]["squeeze_recent_bars"] = combo["squeeze_recent_bars"]
    c["breakout"]["volume_mult"] = combo["volume_mult"]
    return c


def key(combo: dict) -> tuple:
    return tuple(combo[k] for k in GRID)


# ---- parallel signal computation ---------------------------------------------------------------
_INPUTS: dict[str, pd.DataFrame] = {}
_CFG: dict = {}
_WINDOW: tuple[str, str] = ("", "")


def _init(inputs, cfg, window):
    global _INPUTS, _CFG, _WINDOW
    _INPUTS, _CFG, _WINDOW = inputs, cfg, window


def _signals_for_combo(combo: dict) -> dict:
    cfg = apply(_CFG, combo)
    out: dict[str, list[dict]] = {}
    a, b = _WINDOW
    for sid, df in _INPUTS.items():
        if len(df) < 2:
            continue
        f = compute_features(df, cfg)
        e = entry_signals(f, {"E": VERSIONS["E"]})["entry_E"]
        idx = f.index[e.to_numpy()]
        for ts in idx:
            d = ts.strftime("%Y-%m-%d")
            if not (a <= d <= b):
                continue
            atr = f.at[ts, "atr_raw"]
            if atr != atr:   # NaN
                continue
            out.setdefault(d, []).append({"stock_id": sid, "signal_date": d, "close": float(df.at[ts, "close"]),
                                          "atr": float(atr), "turnover": float(f.at[ts, "turnover_ma"])})
    return out


def grid_signals(inputs: dict[str, pd.DataFrame], cfg: dict, window: tuple[str, str], workers: int = 4,
                 limit: int | None = None) -> dict:
    cs = combos()
    if limit:                                   # pipeline test only: the baseline plus the first combinations
        cs = [BASELINE] + [c for c in cs if key(c) != key(BASELINE)][: limit - 1]
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(inputs, cfg, window)) as ex:
        sigs = list(ex.map(_signals_for_combo, cs, chunksize=4))
    return {key(c): (c, s) for c, s in zip(cs, sigs)}


# ---- plateau selection ------------------------------------------------------------------------------
def neighbours(k: tuple) -> list[tuple]:
    keys = list(GRID)
    out = []
    for i, name in enumerate(keys):
        vals = GRID[name]
        j = vals.index(k[i])
        for jj in (j - 1, j + 1):
            if 0 <= jj < len(vals):
                n = list(k)
                n[i] = vals[jj]
                out.append(tuple(n))
    return out


def plateau_scores(score: dict[tuple, float]) -> dict[tuple, float]:
    out = {}
    for k, v in score.items():
        vals = [v] + [score[n] for n in neighbours(k) if n in score]
        vals = [x for x in vals if x is not None and not math.isnan(x)]
        out[k] = float(np.mean(vals)) if vals else math.nan
    return out


def select_plateau(score: dict[tuple, float]) -> tuple:
    ps = plateau_scores(score)
    return max((k for k in ps if not math.isnan(ps[k])), key=lambda k: ps[k])


# ---- walk-forward (inside IS + Validation only) ------------------------------------------------------------
def walk_forward(returns: dict[tuple, pd.Series], start: str, end: str, train_years: int = 3) -> list[dict]:
    y0, y1 = int(start[:4]), int(end[:4])
    out = []
    for y in range(y0, y1 - train_years + 1):
        tr = (f"{y}-01-01", f"{y + train_years - 1}-12-31")
        te = (f"{y + train_years}-01-01", min(f"{y + train_years}-12-31", end))
        if te[0] > end:
            break
        score = {k: stats.sharpe(r[(r.index >= tr[0]) & (r.index <= tr[1])].to_numpy()) for k, r in returns.items()}
        sel = select_plateau(score)
        rs = returns[sel]
        test = rs[(rs.index >= te[0]) & (rs.index <= te[1])].to_numpy()
        base = returns[key(BASELINE)]
        out.append({"train": tr, "test": te, "selected": sel, "train_sharpe": score[sel],
                    "test_sharpe": stats.sharpe(test) if len(test) > 2 else math.nan,
                    "test_return": float(np.prod(1 + test) - 1) if len(test) else math.nan,
                    "baseline_test_sharpe": stats.sharpe(base[(base.index >= te[0]) & (base.index <= te[1])].to_numpy())})
    return out


# ---- market regimes (research-default definition, pre-registered) -------------------------------------------
def regimes(index_values: pd.Series) -> pd.DataFrame:
    """Per date: trend regime (bull / bear / sideways) and volatility regime (high / low / normal), causal."""
    s = index_values.sort_index().astype(float)
    ma = s.rolling(200, min_periods=200).mean()
    ret200 = s / s.shift(200) - 1
    vol = s.pct_change().rolling(20, min_periods=20).std() * math.sqrt(252)
    vol_pct = vol.expanding(min_periods=250).apply(lambda x: (x[:-1] <= x[-1]).mean() if len(x) > 1 else np.nan,
                                                   raw=True)
    trend = np.where((s > ma) & (ret200 > 0.10), "bull", np.where((s < ma) & (ret200 < -0.10), "bear", "sideways"))
    trend = np.where(ma.isna(), None, trend)
    volr = np.where(vol_pct >= 0.70, "high_vol", np.where(vol_pct <= 0.30, "low_vol", "normal_vol"))
    volr = np.where(pd.isna(vol_pct), None, volr)
    return pd.DataFrame({"trend": trend, "volatility": volr}, index=s.index)


def daily_returns(daily: pd.DataFrame) -> pd.Series:
    d = daily.set_index("date")["equity"].astype(float)
    return d.pct_change().dropna()


def mdd_from_returns(r: np.ndarray) -> float:
    eq = np.cumprod(1 + np.asarray(r, float))
    peak = np.maximum.accumulate(np.r_[1.0, eq])[1:]
    return float(np.max(1 - eq / peak)) if len(eq) else 0.0


__all__ = ["GRID", "EXIT_GRID", "BASELINE", "combos", "apply", "key", "grid_signals", "plateau_scores",
           "select_plateau", "walk_forward", "regimes", "daily_returns", "mdd_from_returns", "ExperimentContext"]
