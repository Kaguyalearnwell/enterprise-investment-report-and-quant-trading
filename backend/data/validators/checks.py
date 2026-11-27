"""Pure data-quality checks. Each takes DataFrames and returns an issues DataFrame.

Rules (platform spec 6.6 / Stage 2 spec 7):
* Checks never modify or delete data. They classify and describe; the `action`
  column is a recommendation ('keep' = informational, 'flag' = usable but marked,
  'exclude' = should not be used by the backtest until resolved).
* Repeated consecutive issues (e.g. a 30-day gap) are collapsed into ONE issue
  with the run length in `detail`, so a report stays readable.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

ISSUE_COLUMNS = ["table_name", "stock_id", "date", "check_name", "severity", "category", "detail", "action"]
LIMIT_CHANGE_DATE = "2015-06-01"     # daily price limit 7% -> 10%
VWAP_TOLERANCE = 0.005
JUMP_TOLERANCE = 0.005
VOLUME_SPIKE_MULT = 50.0


def _issues(df: pd.DataFrame, table: str, check: str, severity: str, category: str, action: str,
            detail: pd.Series | str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=ISSUE_COLUMNS)
    out = pd.DataFrame({
        "table_name": table, "stock_id": df["stock_id"].to_numpy(),
        "date": df["date"].to_numpy() if "date" in df else None,
        "check_name": check, "severity": severity, "category": category,
        "detail": detail if isinstance(detail, str) else detail.to_numpy(), "action": action,
    })
    return out[ISSUE_COLUMNS]


def limit_pct(dates: pd.Series) -> pd.Series:
    return pd.Series(np.where(dates < LIMIT_CHANGE_DATE, 0.07, 0.10), index=dates.index)


# ---- daily_prices_raw ------------------------------------------------------------------------
def check_ohlc(p: pd.DataFrame) -> pd.DataFrame:
    """OHLC logic on days with a published price."""
    x = p[p["has_price"] == 1]
    out = []
    partial = x[x[["open", "high", "low", "close"]].isna().any(axis=1)]
    out.append(_issues(partial, "daily_prices_raw", "ohlc_logic", "error", "partial_ohlc", "exclude",
                       "has_price=1 but some of OHLC missing"))
    x = x.dropna(subset=["open", "high", "low", "close"])
    nonpos = x[(x[["open", "high", "low", "close"]] <= 0).any(axis=1)]
    out.append(_issues(nonpos, "daily_prices_raw", "ohlc_logic", "error", "nonpositive_price", "exclude",
                       "an OHLC value <= 0"))
    hi_bad = x[(x["high"] < x[["open", "close"]].max(axis=1)) | (x["high"] < x["low"])]
    out.append(_issues(hi_bad, "daily_prices_raw", "ohlc_logic", "error", "high_below_open_close_or_low", "exclude",
                       hi_bad.apply(lambda r: f"O={r.open} H={r.high} L={r.low} C={r.close}", axis=1)
                       if not hi_bad.empty else ""))
    lo_bad = x[x["low"] > x[["open", "close"]].min(axis=1)]
    out.append(_issues(lo_bad, "daily_prices_raw", "ohlc_logic", "error", "low_above_open_close", "exclude",
                       lo_bad.apply(lambda r: f"O={r.open} H={r.high} L={r.low} C={r.close}", axis=1)
                       if not lo_bad.empty else ""))
    return pd.concat(out, ignore_index=True)


def check_no_price_days(p: pd.DataFrame) -> pd.DataFrame:
    """Days with no published price are legitimate (suspension / no trade) but must be visible."""
    x = p[p["has_price"] == 0]
    with_vol = x[x["volume"].fillna(0) > 0]
    without = x[x["volume"].fillna(0) <= 0]
    return pd.concat([
        _issues(with_vol, "daily_prices_raw", "no_price_day", "info", "volume_without_price", "keep",
                with_vol["volume"].map(lambda v: f"volume={int(v)} shares but no published price")),
        _issues(without, "daily_prices_raw", "no_price_day", "info", "no_trade", "keep", "no trade, no price"),
    ], ignore_index=True)


def check_volume(p: pd.DataFrame) -> pd.DataFrame:
    neg = p[p["volume"] < 0]
    out = [_issues(neg, "daily_prices_raw", "volume", "error", "negative_volume", "exclude", "volume < 0")]
    x = p[(p["has_price"] == 1) & (p["volume"] > 0) & p["turnover"].notna()].copy()
    if not x.empty:
        vwap = x["turnover"] / x["volume"]
        bad = x[(vwap < x["low"] * (1 - VWAP_TOLERANCE)) | (vwap > x["high"] * (1 + VWAP_TOLERANCE))]
        out.append(_issues(bad, "daily_prices_raw", "volume", "warning", "vwap_outside_high_low", "flag",
                           (bad["turnover"] / bad["volume"]).round(4).astype(str)
                           .radd("turnover/volume=").str.cat(bad["low"].astype(str), sep=" low=")
                           .str.cat(bad["high"].astype(str), sep=" high=") if not bad.empty else ""))
    spikes = []
    for sid, g in p.sort_values("date").groupby("stock_id"):
        med = g["volume"].where(g["volume"] > 0).rolling(60, min_periods=20).median().shift(1)
        s = g[(g["volume"] > VOLUME_SPIKE_MULT * med)]
        if not s.empty:
            spikes.append(_issues(s, "daily_prices_raw", "volume", "info", "volume_spike", "keep",
                                  (s["volume"] / med[s.index]).round(1).map(lambda m: f"{m}x the 60-day median")))
    return pd.concat(out + spikes, ignore_index=True)


def check_duplicates(p: pd.DataFrame) -> pd.DataFrame:
    d = p[p.duplicated(["stock_id", "date"], keep=False)]
    return _issues(d, "daily_prices_raw", "duplicates", "error", "duplicate_stock_date", "exclude", "duplicate key")


def check_calendar(p: pd.DataFrame, trading_days: list[str], as_of: str) -> pd.DataFrame:
    """Rows on non-trading days, and trading days with no row between a stock's first and last row."""
    cal = pd.Index(sorted(trading_days))
    pos = {d: i for i, d in enumerate(cal)}
    out = []
    off = p[~p["date"].isin(cal)]
    out.append(_issues(off, "daily_prices_raw", "calendar", "error", "row_on_non_trading_day", "exclude",
                       "date is not in trading_calendar"))
    for sid, g in p[p["date"].isin(cal)].groupby("stock_id"):
        first, last = g["date"].min(), min(g["date"].max(), as_of)
        expected = cal[(cal >= first) & (cal <= last)]
        missing = sorted(set(expected) - set(g["date"]))
        if not missing:
            continue
        runs, cur = [], [missing[0]]
        for d in missing[1:]:
            if pos[d] == pos[cur[-1]] + 1:
                cur.append(d)
            else:
                runs.append(cur)
                cur = [d]
        runs.append(cur)
        rows = pd.DataFrame({"stock_id": sid, "date": [r[0] for r in runs]})
        detail = pd.Series([f"{len(r)} trading day(s) missing: {r[0]}..{r[-1]}" for r in runs])
        out.append(_issues(rows, "daily_prices_raw", "calendar", "warning", "missing_trading_days", "flag", detail))
    return pd.concat(out, ignore_index=True)


def check_returns(p: pd.DataFrame, actions: pd.DataFrame) -> pd.DataFrame:
    """Close-to-close moves beyond the daily limit that no corporate action explains."""
    x = p[p["has_price"] == 1].sort_values(["stock_id", "date"]).copy()
    x["prev_close"] = x.groupby("stock_id")["close"].shift(1)
    x["prev_date"] = x.groupby("stock_id")["date"].shift(1)
    x = x.dropna(subset=["prev_close"])
    ev = actions[["stock_id", "ex_date", "reference_price", "action_type"]].rename(columns={"ex_date": "date"}) \
        if not actions.empty else pd.DataFrame(columns=["stock_id", "date", "reference_price", "action_type"])
    # an event between the previous priced day and today explains the gap
    x = x.merge(ev.groupby(["stock_id", "date"], as_index=False).agg(
        reference_price=("reference_price", "last"), action_type=("action_type", lambda s: ",".join(sorted(s)))),
        on=["stock_id", "date"], how="left")
    base = x["reference_price"].where(x["reference_price"].notna() & (x["reference_price"] > 0), x["prev_close"])
    move = x["close"] / base - 1
    lim = limit_pct(x["date"]) + JUMP_TOLERANCE
    bad = x[move.abs() > lim].copy()
    if bad.empty:
        return pd.DataFrame(columns=ISSUE_COLUMNS)
    bad["move"] = move[bad.index]
    gap = bad["action_type"].isna()
    # a jump right after a long gap (suspension) is not an error, only informational
    det = bad.apply(lambda r: f"close {r.close} vs {'reference ' + str(r.reference_price) if pd.notna(r.action_type) else 'prev close ' + str(r.prev_close)} "
                              f"({r.move:+.1%}) prev priced day {r.prev_date}"
                              + (f"; event {r.action_type}" if pd.notna(r.action_type) else ""), axis=1)
    out = [
        _issues(bad[gap], "daily_prices_raw", "returns", "warning", "unexplained_jump", "flag", det[gap]),
        _issues(bad[~gap], "daily_prices_raw", "returns", "warning", "jump_vs_event_reference", "flag", det[~gap]),
    ]
    return pd.concat(out, ignore_index=True)


def check_stock_ids(master: pd.DataFrame) -> pd.DataFrame:
    """Code format must match the security type."""
    pat = {"common": r"[1-9]\d{3}", "preferred": r"[1-9]\d{3}[A-Z]", "etf": r"00\d{2,4}[A-Z]?",
           "etn": r"02\d{4}[A-Z]?", "tdr": r"91\d{4}"}
    bad = master[[not (t in pat and pd.Series([s]).str.fullmatch(pat[t]).iloc[0]) and t != "other"
                  for s, t in zip(master["stock_id"], master["security_type"])]]
    return _issues(bad.assign(date=None), "security_master", "stock_id", "warning", "code_format_mismatch", "flag",
                   bad["security_type"].map(lambda t: f"code format does not match security_type={t}"))


# ---- corporate actions --------------------------------------------------------------------------
def check_corporate_actions(actions: pd.DataFrame, p: pd.DataFrame, trading_days: list[str]) -> pd.DataFrame:
    if actions.empty:
        return pd.DataFrame(columns=ISSUE_COLUMNS)
    a = actions.rename(columns={"ex_date": "date"}).copy()
    out = []
    not_td = a[~a["date"].isin(set(trading_days))]
    out.append(_issues(not_td, "corporate_actions", "corporate_actions", "error", "ex_date_not_trading_day", "flag",
                       not_td["action_type"]))
    nof = a[a["adjustment_factor"].isna()]
    out.append(_issues(nof, "corporate_actions", "corporate_actions", "warning", "missing_adjustment_factor",
                       "flag", nof["action_type"]))
    # before_price should equal the last published close before the ex-date
    priced = p[p["has_price"] == 1][["stock_id", "date", "close"]].sort_values("date")
    have = a[a["stock_id"].isin(priced["stock_id"].unique()) & a["before_price"].notna()].sort_values("date")
    if not have.empty:
        left = have.assign(_d=pd.to_datetime(have["date"])).sort_values("_d")
        right = priced.assign(_pdt=pd.to_datetime(priced["date"])).rename(columns={"date": "_pd"}).sort_values("_pdt")
        m = pd.merge_asof(left, right, left_on="_d", right_on="_pdt", by="stock_id", allow_exact_matches=False)
        m = m.dropna(subset=["close"])
        diff = (m["before_price"] / m["close"] - 1).abs()
        bad = m[diff > 0.005]
        out.append(_issues(bad, "corporate_actions", "corporate_actions", "warning", "before_price_mismatch", "flag",
                           bad.apply(lambda r: f"{r.action_type}: before_price {r.before_price} vs last close "
                                               f"{r.close} on {r._pd}", axis=1) if not bad.empty else ""))
    return pd.concat(out, ignore_index=True)


def summarize(issues: pd.DataFrame) -> dict:
    if issues.empty:
        return {"total": 0, "by_check": {}, "by_severity": {}}
    return {
        "total": int(len(issues)),
        "by_severity": issues["severity"].value_counts().to_dict(),
        "by_check": json.loads(issues.groupby(["check_name", "category", "severity"]).size()
                               .rename("n").reset_index().to_json(orient="records", force_ascii=False)),
    }


# ---- adjusted prices ---------------------------------------------------------------------------
def check_adjusted(adj: pd.DataFrame, raw: pd.DataFrame, actions: pd.DataFrame) -> pd.DataFrame:
    """adj = raw * factor exactly; the factor may change only across an ex-date; continuity at each event."""
    if adj.empty:
        return pd.DataFrame(columns=ISSUE_COLUMNS)
    m = adj.merge(raw[["stock_id", "date", "close", "has_price"]], on=["stock_id", "date"], how="left")
    out = []
    priced = m[m["has_price"] == 1]
    ratio_bad = priced[~np.isclose(priced["adj_close"], priced["close"] * priced["price_factor"], rtol=1e-9)]
    out.append(_issues(ratio_bad, "daily_prices_adj", "adjusted", "error", "adj_not_raw_times_factor", "exclude",
                       "adj_close != close * price_factor"))
    ev = set(zip(actions["stock_id"], actions["ex_date"])) if not actions.empty else set()
    for sid, g in m.sort_values("date").groupby("stock_id"):
        chg = g[g["price_factor"].ne(g["price_factor"].shift(1)) & g["price_factor"].shift(1).notna()]
        # the factor drops back toward 1 on the first bar ON/AFTER an ex-date
        stray = chg[[not any((sid, d2) in ev for d2 in _between(g, d)) for d in chg["date"]]]
        out.append(_issues(stray, "daily_prices_adj", "adjusted", "error", "factor_change_without_event", "exclude",
                           "price_factor changes on a day with no corporate action"))
    return pd.concat(out, ignore_index=True)


def _between(g: pd.DataFrame, d: str) -> list[str]:
    """Calendar dates from the previous bar (exclusive) up to d (inclusive) - an ex-date may fall on a no-row day."""
    prev = g["date"][g["date"] < d].max()
    return [x for x in pd.date_range(prev, d).strftime("%Y-%m-%d") if x > prev]


# ---- price limits ------------------------------------------------------------------------------
def check_price_limits(raw: pd.DataFrame, limits: pd.DataFrame, tol: float = 1e-6) -> pd.DataFrame:
    """A traded price outside [limit_down, limit_up] means the limit (or the price) is wrong."""
    if limits.empty:
        return pd.DataFrame(columns=ISSUE_COLUMNS)
    m = raw[raw["has_price"] == 1].merge(limits[limits["has_limit"] == 1], on=["stock_id", "date"])
    bad = m[(m["high"] > m["limit_up"].astype(float) + tol) | (m["low"] < m["limit_down"].astype(float) - tol)]
    return _issues(bad, "price_limits", "price_limits", "warning", "price_outside_limits", "flag",
                   bad.apply(lambda r: f"H={r.high} L={r.low} vs limits {r.limit_down}..{r.limit_up} "
                                       f"(ref {r.reference_price}, {r.basis})", axis=1) if not bad.empty else "")


# ---- weekly bars ------------------------------------------------------------------------------
def check_weekly(stored: pd.DataFrame, rebuilt: pd.DataFrame) -> pd.DataFrame:
    """Stored weekly bars must equal a fresh aggregation of the stored daily bars."""
    if stored.empty and rebuilt.empty:
        return pd.DataFrame(columns=ISSUE_COLUMNS)
    k = ["stock_id", "week_key", "price_basis"]
    m = stored.merge(rebuilt, on=k, how="outer", suffixes=("", "_new"), indicator=True)
    cols = ["open", "high", "low", "close", "volume"]
    diff = m[(m["_merge"] != "both") | ~pd.concat(
        [pd.Series(np.isclose(m[c].astype(float), m[f"{c}_new"].astype(float), rtol=1e-12, equal_nan=True))
         for c in cols], axis=1).all(axis=1).to_numpy()]
    diff = diff.assign(date=diff["week_end"].fillna(diff.get("week_end_new")))
    return _issues(diff, "weekly_prices", "weekly", "error", "weekly_aggregation_mismatch", "exclude",
                   diff.apply(lambda r: f"week {r.week_key} {r.price_basis}: {r._merge}", axis=1)
                   if not diff.empty else "")
