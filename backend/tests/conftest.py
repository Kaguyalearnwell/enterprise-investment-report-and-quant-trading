"""Synthetic market data for Phase 1 tests.

This is NOT market data. It is a regime-switching random walk on a calendar
with Taiwan-style holes (a closed Lunar New Year week, closed Fridays) and an
annual cash dividend, used only to test causality and definitions.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]   # repository root
sys.path.insert(0, str(ROOT))

from backend.config.strategy_config import load_config  # noqa: E402


def make_calendar(start="2013-01-01", end="2017-12-31") -> pd.DatetimeIndex:
    days = pd.bdate_range(start, end)
    closed = set(pd.bdate_range("2014-01-27", "2014-02-04"))        # whole week + (LNY-like)
    closed |= set(pd.bdate_range("2016-02-08", "2016-02-12"))       # whole ISO week
    closed |= {pd.Timestamp(d) for d in ["2015-04-03", "2016-06-10", "2017-10-06", "2013-12-27"]}  # Fridays
    return pd.DatetimeIndex([d for d in days if d not in closed])


def make_stock(seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = make_calendar()
    n = len(idx)
    regime = np.repeat(rng.choice([-1.0, 0.0, 1.0], size=n // 60 + 1), 60)[:n]
    vol = np.repeat(rng.choice([0.008, 0.02], size=n // 40 + 1), 40)[:n]
    ret = 0.0012 * regime + vol * rng.standard_normal(n)
    raw_close = 50 * np.exp(np.cumsum(ret))

    # annual cash dividend: raw price drops by D on ex-date, adj factor applied before it
    ex_dates = [i for i, d in enumerate(idx) if d.month == 7 and d.day <= 7 and (i == 0 or idx[i - 1].month == 6)]
    factor = np.ones(n)
    for i in ex_dates:
        d = 0.03 * raw_close[i - 1]
        raw_close[i:] -= d
        factor[:i] *= (raw_close[i - 1] - d) / raw_close[i - 1]
    raw_close = np.maximum(raw_close, 1.0)

    spread = np.abs(rng.standard_normal(n)) * vol * raw_close
    raw_open = raw_close * (1 + 0.3 * vol * rng.standard_normal(n))
    raw_high = np.maximum(raw_open, raw_close) + spread
    raw_low = np.minimum(raw_open, raw_close) - spread
    volume = rng.lognormal(mean=13.5, sigma=0.6, size=n) * (1 + 2 * (rng.random(n) < 0.05))

    df = pd.DataFrame(
        {"open": raw_open, "high": raw_high, "low": raw_low, "close": raw_close, "volume": volume},
        index=idx,
    )
    for c in ["open", "high", "low", "close"]:
        df[f"adj_{c}"] = df[c] * factor
    df["adj_volume"] = df["volume"]
    df["tradable"] = True
    return df


@pytest.fixture(scope="session")
def cfg():
    return load_config()


@pytest.fixture(scope="session")
def stock():
    return make_stock(0)
