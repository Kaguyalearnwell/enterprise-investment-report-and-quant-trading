"""Stage 2 step 8: survivorship audit logic with a fake TPEx provider. MOCK snapshots."""
import pandas as pd

from backend.data.validators.survivorship_audit import SurvivorshipAudit, month_first_trading_days
from backend.database.db_manager import DatabaseManager

NOW = "2026-11-27T00:00:00+00:00"


class FakeTPEx:
    def __init__(self, snaps):
        self.snaps, self.calls = snaps, 0

    def daily_quotes(self, date):
        self.calls += 1
        rows = self.snaps.get(date.isoformat(), [])
        return pd.DataFrame([{"stock_id": s, "stock_name": f"n{s}"} for s in rows])


def test_month_first_trading_days():
    assert month_first_trading_days(["2016-01-04", "2016-01-05", "2016-02-01", "2016-02-02"], "2016-01-01",
                                    "2016-12-31") == ["2016-01-04", "2016-02-01"]


def test_audit_adds_missing_and_resolves_exchange(tmp_path):
    db = DatabaseManager(tmp_path / "a.sqlite")
    db.initialize()
    days = ["2014-01-02", "2014-02-05", "2014-03-03"]
    db.upsert("trading_calendar", [{"date": d, "exchange": "TPEX", "is_trading_day": 1, "session_type": "regular",
                                    "source": "t", "ingested_at": NOW} for d in days] +
              [{"date": d, "exchange": "TWSE", "is_trading_day": 1, "session_type": "regular", "source": "t",
                "ingested_at": NOW} for d in days])
    base = {"stock_name": None, "industry_category": None, "security_type": "common", "source": "finmind",
            "updated_at": NOW}
    db.upsert("security_master", [
        {**base, "stock_id": "3068", "exchange": "UNKNOWN", "delisting_date": "2014-03-20"},   # seen 03-03 -> TPEX
        {**base, "stock_id": "4444", "exchange": "UNKNOWN", "delisting_date": "2016-01-01"},   # seen long before
        {**base, "stock_id": "6488", "exchange": "TPEX", "delisting_date": None},
    ])
    fake = FakeTPEx({"2014-01-02": ["3068", "4444", "6488", "5326", "006201"],
                     "2014-02-05": ["3068", "6488", "5326"], "2014-03-03": ["3068", "6488"]})
    res = SurvivorshipAudit(db, fake, raw_dir=tmp_path / "raw").run("2014-01-01", "2014-03-31")
    m = db.get_security_master().set_index("stock_id")
    assert res["added_ids"] == ["5326"] and m.loc["5326", "source"] == "tpex_snapshot"
    assert m.loc["3068", "exchange"] == "TPEX" and m.loc["4444", "exchange"] == "UNKNOWN"
    assert "006201" not in m.index                                  # ETFs are not common stocks
    h = db.read_sql("SELECT * FROM security_history WHERE stock_id='5326'").iloc[0]
    assert (h.valid_from, h.valid_to) == ("2014-01-02", "2014-02-05")
    assert len(db.read_sql("SELECT * FROM data_revisions WHERE table_name='security_master'")) == 1   # 3068 change
    # second run uses the raw cache: no new requests
    n = fake.calls
    SurvivorshipAudit(db, fake, raw_dir=tmp_path / "raw").run("2014-01-01", "2014-03-31")
    assert fake.calls == n
    db.close()
