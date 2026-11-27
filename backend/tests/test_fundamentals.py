"""Stage 6.2 fundamentals: REAL FinMind responses recorded 2026-11-27 (fixtures), no network."""
import json
from pathlib import Path

import pandas as pd
import pytest

from backend.data.collectors.downloader import Downloader
from backend.database.db_manager import DatabaseManager

from .test_downloader import FakeResponse, FakeSession, provider

FIX = Path(__file__).parent / "fixtures" / "finmind"
NOW = "2026-11-27T00:00:00+00:00"


class FundSession(FakeSession):
    MAP = {"TaiwanStockFinancialStatements": "income_2330.json", "TaiwanStockMonthRevenue": "revenue_2330.json",
           "TaiwanStockDividend": "dividend_policy_2330.json", "TaiwanStockPER": "per_2330.json"}

    def get(self, url, params, timeout):
        if params["dataset"] in self.MAP:
            self.calls.append(dict(params))
            body = json.loads((FIX / self.MAP[params["dataset"]]).read_text())
            s, e = params.get("start_date", ""), params.get("end_date") or "9999"
            return FakeResponse({**body, "data": [r for r in body["data"] if s <= r["date"] <= e]})
        return super().get(url, params, timeout)


def test_statement_normalisation():
    df = provider(FundSession()).income_statements("2330", "2023-01-01", "2024-12-31").data
    assert set(df.statement) == {"income"} and set(df.period_end) == {"2023-12-31", "2024-03-31"}
    eps = df[(df.item == "EPS") & (df.period_end == "2024-03-31")].value.iloc[0]
    assert eps == pytest.approx(8.7, abs=0.5)                  # REAL: 2330 Q1-2024 EPS 8.70


def test_revenue_month_mapping():
    df = provider(FundSession()).month_revenue("2330", "2024-01-01", "2026-11-27").data
    first = df.iloc[0]
    assert first.revenue_month == "2023-12"                     # FinMind date 2024-01-01 holds December 2023


def test_dividend_policy_filters_on_announcement_not_date():
    p = provider(FundSession())
    df = p.dividend_policy("2330", "2020-01-01", "2026-09-10").data
    assert (df.announcement_date <= "2026-09-10").all()
    raw = json.loads((FIX / "dividend_policy_2330.json").read_text())["data"]
    future = [r for r in raw if r["date"] > "2026-09-10" and r["AnnouncementDate"] <= "2026-09-10"]
    assert future, "fixture should contain an announced dividend whose `date` lies later"
    assert any(df.announcement_date == future[0]["AnnouncementDate"][:10])   # kept although `date` is later
    r = df.iloc[0]
    assert r.cash_dividend > 0 and r.stock_dividend == 0          # 2330: cash only


def test_download_with_availability_and_point_in_time_read(tmp_path):
    db = DatabaseManager(tmp_path / "f.sqlite")
    db.initialize()
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2023-12-01", "2026-11-27")]
    db.upsert("trading_calendar", [{"date": d, "exchange": "TWSE", "is_trading_day": 1, "session_type": "regular",
                                    "source": "test", "ingested_at": NOW} for d in days])
    db.upsert("security_master", [{"stock_id": "2330", "exchange": "TWSE", "security_type": "common",
                                   "source": "test", "updated_at": NOW}])
    dl = Downloader(db, provider(FundSession()), as_of="2026-11-27")
    for ds in ("income", "revenue", "dividend_policy", "per"):
        s = dl.download(ds, ["2330"], "2020-01-01")
        assert s.success == 1 and s.failed == 0, ds
    q1 = db.read_sql("SELECT DISTINCT available_from, availability_basis FROM financial_statements "
                     "WHERE period_end='2024-03-31'").iloc[0].tolist()
    assert q1 == ["2024-05-16", "statutory_deadline"]          # deadline 05-15 -> next trading day
    assert db.statements_as_of("2330", "2024-05-15").period_end.max() == "2023-12-31"
    assert db.statements_as_of("2330", "2024-05-16").period_end.max() == "2024-03-31"
    rv = db.read_sql("SELECT * FROM monthly_revenue WHERE revenue_month='2026-06'").iloc[0]
    assert (rv.available_from, rv.availability_basis) == ("2026-07-14", "create_time")   # collected 07-13
    old = db.read_sql("SELECT * FROM monthly_revenue WHERE revenue_month='2024-01'").iloc[0]
    assert (old.available_from, old.availability_basis) == ("2024-02-21", "fallback_rule")
    assert len(db.read_sql("SELECT * FROM valuation_daily")) == 7
    db.close()
