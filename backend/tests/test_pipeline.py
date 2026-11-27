"""Stage 2 step 10: end-to-end pipeline on REAL recorded FinMind responses (fixtures), no network."""
import pandas as pd

from backend.data.collectors.downloader import Downloader
from backend.data.pipeline import DataPipeline, seed_source_registry
from backend.database.db_manager import DatabaseManager

from .test_downloader import FakeSession, provider


def test_pipeline_end_to_end(tmp_path):
    db = DatabaseManager(tmp_path / "p.sqlite")
    db.initialize()
    dl = Downloader(db, provider(FakeSession()), as_of="2026-11-27")
    dl.sync_reference("2013-01-01")
    # the recorded calendar fixture has no 2025-07 window; add those REAL trading days (2317 fixture dates)
    days = ["2025-07-28", "2025-07-29", "2025-07-30", "2025-07-31", "2025-08-01"]
    db.upsert("trading_calendar", [{"date": d, "exchange": x, "is_trading_day": 1, "session_type": "regular",
                                    "source": "test", "ingested_at": "2026-11-27T00:00:00+00:00"}
                                   for d in days for x in ("TWSE", "TPEX")])
    dl.download("prices", ["2330", "3068", "2317"], "2013-01-01")
    dl.download("dividends", ["2330", "3068"], "2010-01-01")
    pipe = DataPipeline(db, "2026-11-27")
    n = pipe.process(["2330", "3068", "2317"])
    counts = db.row_counts()
    assert n["stocks"] == 3 and counts["daily_prices_adj"] == counts["daily_prices_raw"]
    assert counts["weekly_prices"] > 0 and counts["price_limits"] == counts["daily_prices_raw"]
    # 2317's no-price day 2025-07-30 becomes an inferred one-day period
    sp = db.read_sql("SELECT * FROM suspension_history WHERE stock_id='2317'")
    assert sp[["start_date", "end_date", "basis"]].values.tolist() == [["2025-07-30", "2025-07-30", "inferred"]]
    # 2330 ex-dividend 2024-06-13: official event-day limits used, adjusted series continuous
    lim = db.read_sql("SELECT basis FROM price_limits WHERE stock_id='2330' AND date='2024-06-13'").basis[0]
    assert lim == "official_event"
    run_id, md, issues = pipe.validate(["2330", "3068", "2317"], tmp_path / "reports")
    assert md.exists() and "資料品質報告" in md.read_text() and "判定：" in md.read_text()
    v = db.latest_verdicts(["2330", "3068", "2317"])
    assert set(v.stock_id) == {"2330", "3068", "2317"} and set(v.verdict) <= {"PASS", "WARNING", "FAIL"}
    errors = issues[issues.severity == "error"]
    assert errors[errors.check_name.isin(["adjusted", "weekly", "ohlc_logic"])].empty, errors.to_string()
    # re-processing is idempotent
    pipe.process(["2330", "3068", "2317"])
    assert db.row_counts()["daily_prices_adj"] == counts["daily_prices_adj"]
    assert seed_source_registry(db) > 10
    v = pipe.snapshot_version("test")
    assert db.read_sql("SELECT * FROM data_versions WHERE version_id = ?", (v,)).content_hash[0]
    db.close()
