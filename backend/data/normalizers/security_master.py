"""Build security_master rows by reconciling the listing table with the delisting table.

Stage 2.1/2.3 evidence: FinMind's TaiwanStockInfo is NOT a current-listings table.
It keeps long-delisted stocks (e.g. 1230, delisted 2001) with arbitrary update dates.
Only rows dated on the latest snapshot date are listed today. Therefore:

  delisting table has the stock, not in today's snapshot -> delisted on that date
  delisting table has the stock, AND in today's snapshot  -> CONFLICT (relisting or code reuse?)
  only in the delisting table                             -> delisted, exchange UNKNOWN
  stale listing row, no delisting record                  -> status UNKNOWN (Stage 2.5 audit)

Nothing is silently resolved: every non-trivial case is returned as a data-quality issue.
"""
from __future__ import annotations

import pandas as pd

MASTER_COLUMNS = ["stock_id", "stock_name", "exchange", "industry_category", "security_type", "delisting_date",
                  "source", "updated_at"]


def build_security_master(listing: pd.DataFrame, delisted: pd.DataFrame, source: str,
                          updated_at: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (security_master rows, issues). Issues use data_quality_issues columns minus run/created."""
    lst = listing.set_index("stock_id") if not listing.empty else pd.DataFrame()
    dl = delisted.set_index("stock_id") if not delisted.empty else pd.DataFrame()
    rows, issues = [], []
    for sid in sorted(set(lst.index) | set(dl.index)):
        in_l, in_d = sid in lst.index, sid in dl.index
        row = {"stock_id": sid, "source": source, "updated_at": updated_at, "delisting_date": None}
        if in_l:
            r = lst.loc[sid]
            row.update(stock_name=r["stock_name"], exchange=r["exchange"], industry_category=r["industry_category"],
                       security_type=r["security_type"])
        else:
            r = dl.loc[sid]
            row.update(stock_name=r["stock_name"], exchange="UNKNOWN", industry_category=None,
                       security_type=r["security_type"])
        current = bool(in_l and lst.loc[sid, "in_current_snapshot"])
        if in_d:
            ddate = dl.loc[sid, "delisting_date"]
            if current:
                issues.append(_issue(sid, ddate, "relisted_or_code_reuse",
                                     f"in delisting table ({ddate}) but also in today's listing snapshot; "
                                     "treated as listed, delisting date NOT applied"))
            else:
                row["delisting_date"] = ddate
                if not in_l:
                    issues.append(_issue(sid, ddate, "delisted_exchange_unknown",
                                         "only in delisting table; exchange unknown until Stage 2.5 audit", "info"))
        elif in_l and not current:
            issues.append(_issue(sid, None, "stale_listing_without_delisting",
                                 f"last seen in listing table {lst.loc[sid, 'info_last_seen']}, "
                                 "no delisting record; status unknown"))
        rows.append(row)
    return pd.DataFrame(rows, columns=MASTER_COLUMNS), pd.DataFrame(issues)


def _issue(stock_id: str, date: str | None, category: str, detail: str, severity: str = "warning") -> dict:
    return {"table_name": "security_master", "stock_id": stock_id, "date": date,
            "check_name": "universe_reconciliation", "severity": severity, "category": category,
            "detail": detail, "action": "flag"}
