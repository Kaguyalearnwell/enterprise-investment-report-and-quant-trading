"""Compute indicators and strategy signals from the database (Stage 3).

Input rules (all point-in-time):
* only bars with a published price (has_price = 1) up to `as_of`;
* only bars ON OR AFTER the stock's listing date: emerging-board bars (興櫃, open = previous
  day's average, no limit) are excluded from every indicator (Stage 2 finding);
* signals use adjusted prices; liquidity and ATR-for-stops are expressed in raw prices.

Every signal row records `signal_as_of` (close of day t, 13:30 Taipei) and the earliest
simulated entry date (next trading day). Strategy parameters are frozen into an immutable
`strategy_versions` row; any change produces a new version id.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
from collections.abc import Sequence

import numpy as np
import pandas as pd

from backend.database.db_manager import DatabaseManager, utc_now
from backend.database.schema import CONDITION_BITS, ENTRY_BITS
from backend.strategies.fib_bb_weekly import VERSIONS, compute_features, entry_signals

log = logging.getLogger(__name__)
CODE_VERSION = "fib_bb_weekly/2"   # /2: percent_rank decides ties by value (relative tolerance 1e-9)
CLOSE_TIME = "T13:30:00+08:00"
INDICATOR_SECTIONS = ("trend", "bollinger", "weekly")


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:16]


def indicator_version(cfg: dict) -> str:
    keys = {s: cfg[s] for s in INDICATOR_SECTIONS}
    keys["breakout"] = {k: v for k, v in cfg["breakout"].items() if k != "volume_ma_mode"}
    keys["atr_period"] = cfg["risk"]["atr_period"]
    return _hash({"code": CODE_VERSION, **keys})


def strategy_params(cfg: dict, volume_mode: str) -> dict:
    c = copy.deepcopy(cfg)
    c["breakout"]["volume_ma_mode"] = volume_mode
    return {"code": CODE_VERSION, "trend": c["trend"], "bollinger": c["bollinger"], "breakout": c["breakout"],
            "weekly": c["weekly"], "risk": c["risk"], "versions": {k: list(v) for k, v in VERSIONS.items()}}


class SignalRunner:
    def __init__(self, db: DatabaseManager, cfg: dict, as_of: str):
        self.db, self.cfg, self.as_of = db, cfg, as_of
        self.ind_version = indicator_version(cfg)

    # ---- versions ---------------------------------------------------------------------
    def register_version(self, volume_mode: str, name: str | None = None) -> str:
        params = strategy_params(self.cfg, volume_mode)
        vid = _hash(params)
        if self.db.read_sql("SELECT 1 FROM strategy_versions WHERE strategy_version_id = ?", (vid,)).empty:
            self.db.upsert("strategy_versions", [{
                "strategy_version_id": vid, "name": name or f"baseline/{volume_mode}",
                "params": json.dumps(params, sort_keys=True), "indicator_version": self.ind_version,
                "code_version": CODE_VERSION, "description": f"Fib MA + BBW squeeze breakout + weekly; volume {volume_mode}",
                "created_at": utc_now()}])
        return vid

    # ---- input -------------------------------------------------------------------------
    def load_input(self, sid: str) -> pd.DataFrame:
        listing = self.db.read_sql("SELECT listing_date FROM security_master WHERE stock_id = ?", (sid,))
        start = listing["listing_date"].iloc[0] if len(listing) and pd.notna(listing["listing_date"].iloc[0]) else "1990-01-01"
        d = self.db.read_sql(
            """SELECT r.date, r.open, r.high, r.low, r.close, r.volume, a.adj_open, a.adj_high, a.adj_low,
                      a.adj_close, a.adj_volume
               FROM daily_prices_raw r JOIN daily_prices_adj a ON a.stock_id = r.stock_id AND a.date = r.date
               WHERE r.stock_id = ? AND r.has_price = 1 AND r.date >= ? AND r.date <= ? ORDER BY r.date""",
            (sid, start, self.as_of))
        if d.empty:
            return d
        d.index = pd.DatetimeIndex(d.pop("date"))
        d["tradable"] = True
        return d.astype(float).assign(tradable=True)

    # ---- run ---------------------------------------------------------------------------
    def run(self, stock_ids: Sequence[str], volume_modes: Sequence[str] = ("include_today", "prior_20")) -> dict:
        versions = {m: self.register_version(m) for m in volume_modes}
        days = self.db.get_trading_days("1990-01-01", self.as_of, as_of=self.as_of)
        nxt = dict(zip(days[:-1], days[1:]))
        stats = {"stocks": 0, "indicator_rows": 0, "signal_rows": 0, "entries": {m: {} for m in volume_modes}}
        for i, sid in enumerate(stock_ids, 1):
            df = self.load_input(sid)
            if len(df) < 2:
                continue
            f = compute_features(df, self.cfg)
            now = utc_now()
            self._write_indicators(sid, df, f, now)
            for mode, vid in versions.items():
                g = f.copy()
                col = "volume_confirm_incl" if mode == "include_today" else "volume_confirm_prior"
                g["volume_confirm"] = f[col]
                e = entry_signals(g)
                n = self._write_signals(sid, vid, g, e, nxt, now)
                stats["signal_rows"] += n
                for v in ENTRY_BITS:
                    stats["entries"][mode][v] = stats["entries"][mode].get(v, 0) + int(e[f"entry_{v}"].sum())
            stats["stocks"] += 1
            stats["indicator_rows"] += len(f)
            if i % 50 == 0:
                log.info("signals: %d/%d stocks", i, len(stock_ids))
        stats["versions"] = versions
        return stats

    def _write_indicators(self, sid: str, df: pd.DataFrame, f: pd.DataFrame, now: str) -> None:
        out = pd.DataFrame({
            "stock_id": sid, "date": f.index.strftime("%Y-%m-%d"), "indicator_version": self.ind_version,
            "close_raw": df["close"], "close_adj": df["adj_close"], "volume_adj": df["adj_volume"],
            "ma13": f["ma_fast"], "ma34": f["ma_mid"], "ma55": f["ma_slow"], "slope55": f["slope_slow"],
            "bb_mid": f["bb_mid"], "bb_upper": f["bb_upper"], "bb_lower": f["bb_lower"], "bbw": f["bbw"],
            "bbw_pct": f["bbw_pct"], "atr14_raw": f["atr_raw"], "volume_ma20_incl": f["volume_ma_incl"],
            "volume_ma20_prior": f["volume_ma_prior"], "turnover_ma20": f["turnover_ma"],
            "w_ma13": f["w_ma_fast"], "w_ma34": f["w_ma_mid"], "w_ma55": f["w_ma_slow"],
            "weekly_source_week_end": [None if pd.isna(x) else pd.Timestamp(x).strftime("%Y-%m-%d")
                                       for x in f["weekly_source_week_end"]],
            "computed_at": now,
        })
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM technical_indicators WHERE stock_id = ? AND indicator_version = ?",
                         (sid, self.ind_version))
        self.db.upsert("technical_indicators", out.replace({np.nan: None}), track_revisions=False)

    def _write_signals(self, sid: str, vid: str, f: pd.DataFrame, e: pd.DataFrame, nxt: dict, now: str) -> int:
        warm = f[["ma_slow", "slope_slow", "bbw_pct", "atr_raw", "turnover_ma"]].notna().all(axis=1) & f["weekly_valid"]
        cond = np.zeros(len(f), dtype=np.int64)
        for name, bit in CONDITION_BITS.items():
            cond |= np.where(f[name].fillna(False).astype(bool).to_numpy(), bit, 0)
        ent = np.zeros(len(f), dtype=np.int64)
        for v, bit in ENTRY_BITS.items():
            ent |= np.where(e[f"entry_{v}"].to_numpy(), bit, 0)
        dates = f.index.strftime("%Y-%m-%d")
        rows = pd.DataFrame({"stock_id": sid, "date": dates, "strategy_version_id": vid,
                             "signal_as_of": [d + CLOSE_TIME for d in dates],
                             "earliest_entry_date": [nxt.get(d) for d in dates],
                             "conditions": cond, "entries": ent, "computed_at": now})[warm.to_numpy()]
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM strategy_signals WHERE stock_id = ? AND strategy_version_id = ?", (sid, vid))
        if not rows.empty:
            self.db.upsert("strategy_signals", rows, track_revisions=False)
        return len(rows)


# ---- read helpers for the Dashboard / CLI -------------------------------------------------------
EXPLAIN_RULES = [
    ("daily_trend", "日線多頭排列：MA13 > MA34 > MA55 且 MA55 斜率 > 0", ("ma13", "ma34", "ma55", "slope55")),
    ("recent_squeeze", "最近 6 日內 BBW 百分位 ≤ 20%", ("bbw_pct",)),
    ("breakout", "收盤（還原）> 布林上軌", ("close_adj", "bb_upper")),
    ("volume_confirm", "成交量 > 1.5 × 20 日均量（依版本的均量定義）", ("volume_adj", "volume_ma20_incl", "volume_ma20_prior")),
    ("weekly_trend", "上一個已完成週 K：週 MA13 > MA34 > MA55 且 MA55 上升", ("w_ma13", "w_ma34", "w_ma55", "weekly_source_week_end")),
    ("liquid", "20 日平均成交金額（原始價）≥ NT$20,000,000", ("turnover_ma20",)),
    ("eligible", "共同可交易條件：暖機完成 + 流動性 + 已上市櫃", ()),
]


def explain(db: DatabaseManager, stock_id: str, date: str, strategy_version_id: str) -> dict:
    """Why a stock did / did not signal on a date: each condition, its inputs, and the result."""
    sig = db.read_sql("SELECT * FROM strategy_signals WHERE stock_id=? AND date=? AND strategy_version_id=?",
                      (stock_id, date, strategy_version_id))
    ver = db.read_sql("SELECT indicator_version FROM strategy_versions WHERE strategy_version_id=?",
                      (strategy_version_id,))
    if ver.empty:
        raise KeyError(f"unknown strategy version {strategy_version_id}")
    ind = db.read_sql("SELECT * FROM technical_indicators WHERE stock_id=? AND date=? AND indicator_version=?",
                      (stock_id, date, ver["indicator_version"].iloc[0]))
    if sig.empty or ind.empty:
        return {"stock_id": stock_id, "date": date, "available": False,
                "reason": "no signal row: not a priced trading day after listing, or indicator warm-up not complete"}
    c, e = int(sig["conditions"].iloc[0]), int(sig["entries"].iloc[0])
    row = ind.iloc[0]
    return {
        "stock_id": stock_id, "date": date, "available": True, "strategy_version_id": strategy_version_id,
        "signal_as_of": sig["signal_as_of"].iloc[0], "earliest_entry_date": sig["earliest_entry_date"].iloc[0],
        "conditions": [{"name": n, "rule": rule, "passed": bool(c & CONDITION_BITS[n]),
                        "inputs": {k: (None if pd.isna(row[k]) else row[k]) for k in keys}}
                       for n, rule, keys in EXPLAIN_RULES],
        "entries": {v: bool(e & b) for v, b in ENTRY_BITS.items()},
        "note": "訊號在 t 日收盤後才成立；最早只能在 earliest_entry_date 開盤模擬進場。這是研究訊號，不是成交。",
    }


def daily_screen(db: DatabaseManager, date: str, strategy_version_id: str, version: str = "E",
                 stop_atr_mult: float = 2.5) -> pd.DataFrame:
    """Stocks whose entry `version` fired on `date` (research signals, NOT simulated fills)."""
    bit = ENTRY_BITS[version]
    ivers = db.read_sql("SELECT indicator_version FROM strategy_versions WHERE strategy_version_id=?",
                        (strategy_version_id,))["indicator_version"].iloc[0]
    df = db.read_sql(
        f"""SELECT s.stock_id, m.stock_name, m.industry_category, m.exchange, s.date AS signal_date,
                   s.earliest_entry_date, i.close_raw, i.turnover_ma20, i.ma13, i.ma34, i.ma55, i.bbw_pct,
                   i.volume_adj, i.volume_ma20_incl, i.volume_ma20_prior, i.atr14_raw, s.conditions
            FROM strategy_signals s
            JOIN technical_indicators i ON i.stock_id = s.stock_id AND i.date = s.date AND i.indicator_version = ?
            LEFT JOIN security_master m ON m.stock_id = s.stock_id
            WHERE s.strategy_version_id = ? AND s.date = ? AND (s.entries & {bit}) > 0
            ORDER BY i.turnover_ma20 DESC""", (ivers, strategy_version_id, date))
    if df.empty:
        return df
    df["volume_ratio_incl"] = df["volume_adj"] / df["volume_ma20_incl"]
    df["volume_ratio_prior"] = df["volume_adj"] / df["volume_ma20_prior"]
    df["weekly_trend"] = (df["conditions"] & CONDITION_BITS["weekly_trend"]) > 0
    df["reference_price"] = df["close_raw"]            # reference only: the simulated fill is the NEXT open
    df["reference_initial_stop"] = df["close_raw"] - stop_atr_mult * df["atr14_raw"]
    df["reference_risk_per_share"] = stop_atr_mult * df["atr14_raw"]
    return df.drop(columns=["conditions"])
