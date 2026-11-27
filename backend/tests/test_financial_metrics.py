"""Stage 7.1 financial metrics on REAL 2330 statements 2023-2024 (FinMind, recorded 2026-11-27).

Reference values are TSMC's published 2024 figures: revenue NT$2,894.3bn, operating cash flow
NT$1,826.2bn, gross margin 56.1%.
"""
import json
from pathlib import Path

import pandas as pd
import pytest

from backend.data.collectors.downloader import Downloader
from backend.database.db_manager import DatabaseManager
from backend.research.financial_metrics import metrics_as_of, quarterly

from .test_downloader import FakeResponse, FakeSession, provider

FIX = Path(__file__).parent / "fixtures" / "finmind"
NOW = "2026-11-27T00:00:00+00:00"
FILES = {"TaiwanStockFinancialStatements": "income_2330_full.json", "TaiwanStockBalanceSheet": "balance_2330.json",
         "TaiwanStockCashFlowsStatement": "cashflow_2330.json"}


class StatementSession(FakeSession):
    def get(self, url, params, timeout):
        if params["dataset"] in FILES:
            self.calls.append(dict(params))
            return FakeResponse(json.loads((FIX / FILES[params["dataset"]]).read_text()))
        return super().get(url, params, timeout)


@pytest.fixture
def db(tmp_path):
    d = DatabaseManager(tmp_path / "m.sqlite")
    d.initialize()
    days = [x.strftime("%Y-%m-%d") for x in pd.bdate_range("2023-01-02", "2026-11-27")]
    d.upsert("trading_calendar", [{"date": x, "exchange": "TWSE", "is_trading_day": 1, "session_type": "regular",
                                   "source": "test", "ingested_at": NOW} for x in days])
    d.upsert("security_master", [{"stock_id": "2330", "exchange": "TWSE", "security_type": "common",
                                  "source": "test", "updated_at": NOW}])
    dl = Downloader(d, provider(StatementSession()), as_of="2026-11-27")
    for ds in ("income", "balance", "cashflow"):
        assert dl.download(ds, ["2330"], "2023-01-01").success == 1
    yield d
    d.close()


def test_cash_flow_ytd_converted_to_single_quarters(db):
    q = quarterly(db, "2330", "2026-11-27")
    cfo = q["q_CashFlowsFromOperatingActivities"] / 1e9
    assert cfo["2024-03-31"] == pytest.approx(436.3, abs=0.1)
    assert cfo["2024-06-30"] == pytest.approx(814.0 - 436.3, abs=0.1)        # YTD(Q2) - YTD(Q1)
    assert cfo.loc["2024-03-31":"2024-12-31"].sum() == pytest.approx(1826.2, abs=0.1)


def test_ttm_metrics_match_published_2024_figures(db):
    m = metrics_as_of(db, "2330", "2025-04-01")
    assert m["latest_period"] == "2024-12-31"
    assert m["revenue_ttm"] / 1e9 == pytest.approx(2894.3, abs=0.5)
    assert m["cfo_ttm"] / 1e9 == pytest.approx(1826.2, abs=0.5)
    assert m["gross_margin"] == pytest.approx(0.561, abs=0.002)
    assert 0.3 < m["revenue_yoy"] < 0.4                                         # 2024 growth vs 2023
    assert m["completeness"] > 0.9


def test_point_in_time_annual_report_not_visible_before_deadline(db):
    assert metrics_as_of(db, "2330", "2025-03-31")["latest_period"] == "2024-09-30"
    assert metrics_as_of(db, "2330", "2025-04-01")["latest_period"] == "2024-12-31"
