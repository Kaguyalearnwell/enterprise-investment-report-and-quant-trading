"""Point-in-time financial metrics (Prompt 04 §4) from FinMind statements.

Only statement rows with available_from <= as_of are used, so a metric "as of" a date never
uses a report that was not yet published (statutory-deadline rule, Stage 6.1/6.2).

Statement conventions (verified on real 2330 data, Stage 6.2):
  income   - single-quarter values           -> TTM = sum of the last 4 quarters
  cashflow - year-to-date cumulative values  -> single quarter = YTD(q) - YTD(q-1) within the fiscal year
  balance  - balances at period end          -> averages use (latest + 4 quarters earlier) / 2
Financial-sector statements (banks, insurers, financial holdings) use different line items;
metrics that need missing items are left empty (never estimated) and lower the completeness score.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from backend.database.db_manager import DatabaseManager

METRICS = ["revenue_ttm", "revenue_yoy", "gross_margin", "operating_margin", "net_margin", "net_income_parent_ttm",
           "roe", "roa", "cfo_ttm", "capex_ttm", "fcf_ttm", "fcf_margin", "fcf_to_net_income", "capex_to_revenue",
           "debt_ratio", "current_ratio", "quick_ratio", "net_cash", "receivables_growth_minus_revenue_growth",
           "inventory_growth_minus_revenue_growth", "cfo_to_net_income"]


def quarterly(db: DatabaseManager, stock_id: str, as_of: str) -> pd.DataFrame:
    """One row per period_end with single-quarter flows and period-end balances (point in time)."""
    st = db.statements_as_of(stock_id, as_of)
    if st.empty:
        return pd.DataFrame()
    wide = st.pivot_table(index="period_end", columns=["statement", "item"], values="value", aggfunc="last")
    wide = wide.sort_index()
    out = pd.DataFrame(index=wide.index)

    def col(statement, item):
        return wide[(statement, item)] if (statement, item) in wide.columns else pd.Series(np.nan, index=wide.index)

    for item in ("Revenue", "GrossProfit", "OperatingIncome", "IncomeAfterTaxes", "EquityAttributableToOwnersOfParent"):
        out[f"q_{item}"] = col("income", item)
    for item in ("CashFlowsFromOperatingActivities", "PropertyAndPlantAndEquipment"):
        ytd = col("cashflow", item)
        year = pd.Series(ytd.index.str[:4], index=ytd.index)
        prev = ytd.groupby(year).shift(1)
        first_q = ytd.index.str[5:7] == "03"
        out[f"q_{item}"] = np.where(first_q, ytd, ytd - prev)        # NaN when the previous quarter is missing
    for item in ("TotalAssets", "Liabilities", "CurrentAssets", "CurrentLiabilities", "Inventories", "AccountsReceivableNet",
                 "CashAndCashEquivalents", "LongtermBorrowings", "BondsPayable", "ShorttermBorrowings",
                 "LongtermLiabilitiesCurrentPortion", "EquityAttributableToOwnersOfParent"):
        out[f"b_{item}"] = col("balance", item)
    return out


def _ttm(s: pd.Series) -> float:
    last4 = s.iloc[-4:]
    return float(last4.sum()) if len(last4) == 4 and last4.notna().all() else math.nan


def _div(a: float, b: float) -> float:
    return a / b if b and not math.isnan(a) and not math.isnan(b) and b != 0 else math.nan


def metrics_as_of(db: DatabaseManager, stock_id: str, as_of: str) -> dict:
    q = quarterly(db, stock_id, as_of)
    out = {"stock_id": stock_id, "as_of": as_of, "latest_period": None}
    if len(q) < 4:
        return {**out, **{m: math.nan for m in METRICS}, "completeness": 0.0, "note": "fewer than 4 published quarters"}
    # consecutive quarters only: the last 8 period ends must be 3 months apart
    out["latest_period"] = q.index[-1]
    cur, prev = q.iloc[-4:], q.iloc[-8:-4] if len(q) >= 8 else None
    rev, rev_prev = _ttm(cur["q_Revenue"]), _ttm(prev["q_Revenue"]) if prev is not None else math.nan
    ni_parent = _ttm(cur["q_EquityAttributableToOwnersOfParent"])
    ni = _ttm(cur["q_IncomeAfterTaxes"])
    cfo = _ttm(cur["q_CashFlowsFromOperatingActivities"])
    capex = _ttm(cur["q_PropertyAndPlantAndEquipment"])            # acquisitions are negative cash flows
    last, year_ago = q.iloc[-1], q.iloc[-5] if len(q) >= 5 else None
    avg = lambda c: (last[c] + year_ago[c]) / 2 if year_ago is not None else math.nan  # noqa: E731
    debt = sum(x for x in (last.get("b_LongtermBorrowings"), last.get("b_BondsPayable"), last.get("b_ShorttermBorrowings"),
                           last.get("b_LongtermLiabilitiesCurrentPortion")) if x is not None and not math.isnan(x))
    growth = lambda c: _div(last[c], year_ago[c]) - 1 if year_ago is not None else math.nan  # noqa: E731
    rev_yoy = _div(rev, rev_prev) - 1
    m = {
        "revenue_ttm": rev, "revenue_yoy": rev_yoy,
        "gross_margin": _div(_ttm(cur["q_GrossProfit"]), rev), "operating_margin": _div(_ttm(cur["q_OperatingIncome"]), rev),
        "net_margin": _div(ni, rev), "net_income_parent_ttm": ni_parent,
        "roe": _div(ni_parent, avg("b_EquityAttributableToOwnersOfParent")), "roa": _div(ni, avg("b_TotalAssets")),
        "cfo_ttm": cfo, "capex_ttm": -capex if not math.isnan(capex) else math.nan,
        "fcf_ttm": cfo + capex if not (math.isnan(cfo) or math.isnan(capex)) else math.nan,
        "debt_ratio": _div(last["b_Liabilities"], last["b_TotalAssets"]),
        "current_ratio": _div(last["b_CurrentAssets"], last["b_CurrentLiabilities"]),
        "quick_ratio": _div(last["b_CurrentAssets"] - (0 if math.isnan(last["b_Inventories"]) else last["b_Inventories"]),
                            last["b_CurrentLiabilities"]),
        "net_cash": last["b_CashAndCashEquivalents"] - debt if not math.isnan(last["b_CashAndCashEquivalents"]) else math.nan,
        "receivables_growth_minus_revenue_growth": growth("b_AccountsReceivableNet") - rev_yoy,
        "inventory_growth_minus_revenue_growth": growth("b_Inventories") - rev_yoy,
        "cfo_to_net_income": _div(cfo, ni),
    }
    m["fcf_margin"] = _div(m["fcf_ttm"], rev)
    m["fcf_to_net_income"] = _div(m["fcf_ttm"], ni)
    m["capex_to_revenue"] = _div(m["capex_ttm"], rev)
    vals = [m[k] for k in METRICS]
    out.update(m)
    out["completeness"] = float(np.mean([not (isinstance(v, float) and math.isnan(v)) for v in vals]))
    return out
