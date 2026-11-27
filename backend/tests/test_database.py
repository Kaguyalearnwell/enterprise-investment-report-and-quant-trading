"""Stage 2.2: schema, constraints, migrations and the read/write interface.

These tests use small hand-written rows (not market data) in a temporary database.
"""
import json
import math
import sqlite3

import pandas as pd
import pytest

from backend.config.settings import get_settings
from backend.database.db_manager import DatabaseManager, utc_now
from backend.database.migrations import MIGRATIONS, Migration, MigrationError, apply_migrations
from backend.database.schema import REQUIRED_TABLES, REQUIRED_VIEWS

NOW = "2026-11-27T00:00:00+00:00"


@pytest.fixture
def db(tmp_path):
    d = DatabaseManager(tmp_path / "t.sqlite")
    d.initialize()
    base = {"stock_name": None, "exchange": "TWSE", "security_type": "common", "listing_date": None,
            "listing_date_basis": None, "first_price_date": None, "delisting_date": None,
            "source": "test", "updated_at": NOW}
    d.upsert("security_master", [
        {**base, "stock_id": "2330", "stock_name": "台積電", "listing_date": "1994-09-05", "listing_date_basis": "official"},
        {**base, "stock_id": "3068", "stock_name": "美磊", "exchange": "TPEX", "first_price_date": "2012-01-02",
         "listing_date_basis": "first_price_date", "delisting_date": "2018-06-13"},
        {**base, "stock_id": "6669", "stock_name": "緯穎", "listing_date": "2019-11-13", "listing_date_basis": "official"},
    ])
    yield d
    d.close()


def price(stock_id="2330", date="2024-01-02", close=593.0, **kw):
    row = {"stock_id": stock_id, "date": date, "open": 590.0, "high": 593.0, "low": 589.0, "close": close,
           "volume": 27997826, "turnover": 16549619798.0, "trade_count": 20667, "has_price": 1,
           "source": "test", "ingested_at": NOW}
    row.update(kw)
    return row


# ---- schema & migrations -------------------------------------------------------------
def test_all_required_tables_and_views_exist(db):
    assert set(REQUIRED_TABLES) <= set(db.tables())
    views = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
    assert set(REQUIRED_VIEWS) <= views
    assert db.schema_version == 6


def test_migrations_are_idempotent(db):
    assert db.initialize() == []
    assert db.schema_version == 6


def test_upgrade_v1_to_v2_keeps_data(tmp_path):
    """A database created by the v1 code (e.g. an earlier download) must upgrade in place."""
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path, isolation_level=None)
    apply_migrations(conn, MIGRATIONS[:1])
    conn.execute("INSERT INTO download_log (dataset, stock_id, start_date, end_date, status, updated_at) "
                 "VALUES ('TaiwanStockPrice','2330','2013-01-01','2026-11-26','success',?)", (NOW,))
    conn.execute("INSERT INTO market_calendar VALUES ('2026-11-26','TWSE',1,'regular','t',?)", (NOW,))
    conn.close()
    d = DatabaseManager(path)
    assert d.initialize() == [2, 3, 4, 5, 6]
    assert d.completed_downloads("TaiwanStockPrice").stock_id.tolist() == ["2330"]
    assert d.get_trading_days("2026-01-01", "2026-12-31", as_of="2026-11-27") == ["2026-11-26"]
    assert "download_log" not in d.tables() and "market_calendar" not in d.tables()
    d.close()


def test_spec_views(db):
    db.upsert("daily_prices_raw", [price()])
    db.upsert("daily_prices_adj", [{"stock_id": "2330", "date": "2024-01-02", "adj_open": 580.0, "adj_high": 583.0,
                                    "adj_low": 579.0, "adj_close": 583.0, "adj_volume": 27997826.0,
                                    "price_factor": 583 / 593, "volume_factor": 1.0, "method": "test",
                                    "factor_as_of": "2026-09-16", "computed_at": NOW}])
    row = db.read_sql("SELECT close, adj_close FROM daily_prices WHERE stock_id='2330'").iloc[0]
    assert (row.close, row.adj_close) == (593.0, 583.0)
    ca = {"stock_id": "2330", "ex_date": "2024-06-13", "action_type": "cash_dividend", "cash_dividend": 4.0,
          "raw_payload": "{}", "source": "t", "ingested_at": NOW}
    db.upsert("corporate_actions", [ca, {**ca, "ex_date": "2025-06-18", "action_type": "split", "cash_dividend": None}])
    assert db.read_sql("SELECT COUNT(*) n FROM dividend_history").n[0] == 1
    assert db.read_sql("SELECT COUNT(*) n FROM stock_split_history").n[0] == 1


def test_edited_migration_is_refused(db):
    tampered = (Migration(1, MIGRATIONS[0].description, MIGRATIONS[0].statements + ("SELECT 1",)),)
    with pytest.raises(MigrationError):
        apply_migrations(db.conn, tampered)


def test_indexes_exist(db):
    idx = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"ix_daily_prices_raw_date", "ix_corporate_actions_ex_date", "ix_download_log_status"} <= idx


def test_pragmas(db):
    assert db.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


# ---- constraints -----------------------------------------------------------------------
def test_primary_key_rejects_duplicates(db):
    db.upsert("daily_prices_raw", [price()])
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute("INSERT INTO daily_prices_raw (stock_id,date,has_price,source,ingested_at) "
                        "VALUES ('2330','2024-01-02',1,'x',?)", (NOW,))


def test_duplicate_key_in_batch_is_rejected(db):
    with pytest.raises(ValueError, match="duplicate key"):
        db.upsert("daily_prices_raw", [price(), price(close=1.0)])


@pytest.mark.parametrize("bad", [
    {"date": "2024/01/02"},          # wrong date format
    {"has_price": 2},                # flag outside {0,1}
    {"close": "593 元"},             # STRICT typing: text in a REAL column
])
def test_structural_errors_are_rejected(db, bad):
    with pytest.raises(sqlite3.IntegrityError):
        db.upsert("daily_prices_raw", [price(**bad)])


def test_enum_checks(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.upsert("security_master", [{"stock_id": "9999", "exchange": "NYSE", "security_type": "common",
                                       "source": "t", "updated_at": NOW}])


def test_foreign_key_and_atomic_batch(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.upsert("daily_prices_raw", [price(date="2024-01-03"), price(stock_id="0000")])
    # the valid row in the same batch must not have been written
    assert db.row_counts()["daily_prices_raw"] == 0


def test_anomalous_values_are_stored_not_rejected(db):
    """Economic anomalies are flagged later by validation, never silently dropped at insert."""
    db.upsert("daily_prices_raw", [price(high=580.0, low=600.0, volume=-5)])
    got = db.get_daily_prices_raw(["2330"], "2024-01-01", "2024-01-31").iloc[0]
    assert got.high == 580.0 and got.low == 600.0 and got.volume == -5


def test_heterogeneous_batch_rejected(db):
    """Regression: columns present only in later rows used to be dropped silently."""
    rows = [price(), {**price(date="2024-01-03"), "trade_count": None}]
    rows[0].pop("trade_count")
    with pytest.raises(ValueError, match="different columns"):
        db.upsert("daily_prices_raw", rows)


def test_unknown_column_rejected(db):
    with pytest.raises(ValueError, match="columns not in"):
        db.upsert("daily_prices_raw", [price(adj_close=1.0)])


# ---- upsert semantics & revisions -------------------------------------------------------
def test_upsert_insert_unchanged_update_and_revision(db):
    r1 = db.upsert("daily_prices_raw", [price(), price(date="2024-01-03", close=578.0)])
    assert (r1.inserted, r1.updated, r1.unchanged) == (2, 0, 0)

    r2 = db.upsert("daily_prices_raw", [price(ingested_at=utc_now())])   # only bookkeeping changed
    assert (r2.inserted, r2.updated, r2.unchanged) == (0, 0, 1)

    r3 = db.upsert("daily_prices_raw", [price(close=594.0)])             # provider revised the close
    assert (r3.inserted, r3.updated, r3.unchanged) == (0, 1, 0)
    rev = db.read_sql("SELECT * FROM data_revisions")
    assert len(rev) == 1
    assert json.loads(rev.row_key[0]) == {"stock_id": "2330", "date": "2024-01-02"}
    assert json.loads(rev.old_values[0]) == {"close": 593.0}
    assert json.loads(rev.new_values[0]) == {"close": 594.0}
    assert db.get_daily_prices_raw(["2330"], "2024-01-02", "2024-01-02").close[0] == 594.0


def test_nan_is_stored_as_null(db):
    db.upsert("daily_prices_raw", pd.DataFrame([price(open=math.nan, high=math.nan, low=math.nan, close=math.nan,
                                                      has_price=0)]))
    row = db.conn.execute("SELECT open, close, has_price FROM daily_prices_raw").fetchone()
    assert row == (None, None, 0)


# ---- download log / resume --------------------------------------------------------------
def test_download_log_attempts_and_resume(db):
    db.log_download("TaiwanStockPrice", "2330", "2013-01-01", "2026-11-27", "failed", http_status=500, error_message="x")
    assert db.completed_downloads("TaiwanStockPrice").empty
    db.log_download("TaiwanStockPrice", "2330", "2013-01-01", "2026-11-27", "success", row_count=3393)
    log = db.read_sql("SELECT * FROM data_download_log")
    assert len(log) == 1 and log.attempts[0] == 2 and log.status[0] == "success"
    assert db.completed_downloads("TaiwanStockPrice").stock_id.tolist() == ["2330"]


# ---- point-in-time readers ---------------------------------------------------------------
def test_trading_days_never_beyond_as_of(db):
    db.upsert("trading_calendar", [{"date": d, "exchange": "TWSE", "is_trading_day": 1, "session_type": "regular",
                                   "source": "t", "ingested_at": NOW}
                                  for d in ["2026-11-26", "2026-11-27", "2026-11-30", "2027-01-04"]])
    assert db.get_trading_days("2026-01-01", "2027-12-31", as_of="2026-11-27") == ["2026-11-26", "2026-11-27"]


def test_universe_is_point_in_time(db):
    assert db.get_universe("2017-06-30").stock_id.tolist() == ["2330", "3068"]   # 3068 alive, 6669 not listed yet
    assert db.get_universe("2018-06-13").stock_id.tolist() == ["2330"]           # delisted that day
    assert db.get_universe("2020-01-02").stock_id.tolist() == ["2330", "6669"]


def test_corporate_action_payload_must_be_json(db):
    row = {"stock_id": "2330", "ex_date": "2024-06-13", "action_type": "cash_dividend", "cash_dividend": 4.0,
           "before_price": 975.0, "reference_price": 971.0, "adjustment_factor": 971 / 975,
           "raw_payload": "not json", "source": "t", "ingested_at": NOW}
    with pytest.raises(sqlite3.IntegrityError):
        db.upsert("corporate_actions", [row])
    row["raw_payload"] = json.dumps({"before_price": 975.0})
    assert db.upsert("corporate_actions", [row]).inserted == 1


# ---- settings ------------------------------------------------------------------------------
def test_token_never_in_repr(monkeypatch, tmp_path):
    monkeypatch.setenv("FINMIND_TOKEN", "secret-value-123")
    s = get_settings(env_file=None)
    assert s.finmind_token == "secret-value-123"
    assert "secret-value-123" not in repr(s)
