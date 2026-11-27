"""SQLite schema (version 1).

Design rules
------------
* Dates are TEXT 'YYYY-MM-DD'; timestamps are TEXT ISO-8601 UTC. Both checked by CHECK.
* STRICT tables: SQLite enforces column types (no silent '12.5abc' strings).
* Every data row carries provenance: `source` and `ingested_at` (when we downloaded it).
* The database does NOT reject economically anomalous values (High < Low, negative
  volume, ...). Stage 2 requires anomalies to be classified and logged, not dropped,
  so they are accepted here and flagged by src/validation into `data_quality_issues`.
  Only structural errors (bad date format, unknown enum value, missing key) are rejected.
* Raw and adjusted prices live in separate tables. Execution code reads only
  `daily_prices_raw` / `price_limits`; signal code may read `daily_prices_adj`.
* Portability: plain TEXT/REAL/INTEGER types and `INSERT ... ON CONFLICT DO UPDATE`
  work on PostgreSQL too; the SQLite-only parts are `STRICT`, `GLOB` and PRAGMAs,
  isolated in this file so a PostgreSQL migration only rewrites the DDL.
"""
from __future__ import annotations

DATE_CHECK = "GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'"
TS_CHECK = "GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]T*'"


def _date(col: str, nullable: bool = False) -> str:
    null = "" if nullable else " NOT NULL"
    cond = f"{col} {DATE_CHECK}" if not nullable else f"{col} IS NULL OR {col} {DATE_CHECK}"
    return f"{col} TEXT{null} CHECK ({cond})"


def _ts(col: str) -> str:
    return f"{col} TEXT NOT NULL CHECK ({col} {TS_CHECK})"


SCHEMA_V1: list[str] = [
    # 1. security_master ---------------------------------------------------------
    f"""
    CREATE TABLE security_master (
        stock_id          TEXT NOT NULL PRIMARY KEY CHECK (length(stock_id) BETWEEN 4 AND 6),
        stock_name        TEXT,
        exchange          TEXT NOT NULL CHECK (exchange IN ('TWSE','TPEX','EMERGING','UNKNOWN')),
        industry_category TEXT,
        security_type     TEXT NOT NULL CHECK (security_type IN
                              ('common','etf','etn','preferred','tdr','warrant','other')),
        {_date('listing_date', True)},
        {_date('delisting_date', True)},
        listing_date_basis TEXT CHECK (listing_date_basis IN ('official','first_price_date') OR listing_date_basis IS NULL),
        {_date('first_price_date', True)},
        {_date('last_price_date', True)},
        source            TEXT NOT NULL,
        {_ts('updated_at')},
        CHECK (delisting_date IS NULL OR listing_date IS NULL OR delisting_date >= listing_date)
    ) STRICT
    """,
    "CREATE INDEX ix_security_master_exchange_type ON security_master (exchange, security_type)",
    # 2. daily_prices_raw ---------------------------------------------------------
    # OHLC are NULL on days without a published price (FinMind reports 0); has_price = 0 then.
    f"""
    CREATE TABLE daily_prices_raw (
        stock_id     TEXT NOT NULL REFERENCES security_master (stock_id),
        {_date('date')},
        open         REAL,
        high         REAL,
        low          REAL,
        close        REAL,
        volume       INTEGER,          -- shares (股)
        turnover     REAL,             -- NT$ traded value (成交金額)
        trade_count  INTEGER,          -- number of trades (成交筆數)
        has_price    INTEGER NOT NULL CHECK (has_price IN (0,1)),
        source       TEXT NOT NULL,
        {_ts('ingested_at')},
        PRIMARY KEY (stock_id, date)
    ) STRICT, WITHOUT ROWID
    """,
    "CREATE INDEX ix_daily_prices_raw_date ON daily_prices_raw (date)",
    # 3. daily_prices_adj ---------------------------------------------------------
    f"""
    CREATE TABLE daily_prices_adj (
        stock_id        TEXT NOT NULL REFERENCES security_master (stock_id),
        {_date('date')},
        adj_open        REAL,
        adj_high        REAL,
        adj_low         REAL,
        adj_close       REAL,
        adj_volume      REAL,
        price_factor    REAL NOT NULL CHECK (price_factor > 0),   -- adj = raw * price_factor
        volume_factor   REAL NOT NULL CHECK (volume_factor > 0),  -- adj_volume = volume * volume_factor
        method          TEXT NOT NULL,                            -- e.g. 'backward_total_return_v1'
        {_date('factor_as_of')},                                  -- last event date included
        {_ts('computed_at')},
        PRIMARY KEY (stock_id, date)
    ) STRICT, WITHOUT ROWID
    """,
    "CREATE INDEX ix_daily_prices_adj_date ON daily_prices_adj (date)",
    # 4. corporate_actions ----------------------------------------------------------
    f"""
    CREATE TABLE corporate_actions (
        stock_id                TEXT NOT NULL,
        {_date('ex_date')},
        action_type             TEXT NOT NULL CHECK (action_type IN
                                    ('cash_dividend','stock_dividend','cash_and_stock_dividend',
                                     'capital_reduction','split','reverse_split','par_value_change')),
        cash_dividend           REAL,
        stock_dividend          REAL,
        split_ratio             REAL,
        capital_reduction_ratio REAL,
        before_price            REAL,       -- close before the event
        reference_price         REAL,       -- official post-event reference price
        limit_up                REAL,       -- official limits on the event day
        limit_down              REAL,
        adjustment_factor       REAL CHECK (adjustment_factor IS NULL OR adjustment_factor > 0),
        raw_payload             TEXT NOT NULL CHECK (json_valid(raw_payload)),
        source                  TEXT NOT NULL,
        {_ts('ingested_at')},
        PRIMARY KEY (stock_id, ex_date, action_type)
    ) STRICT
    """,
    "CREATE INDEX ix_corporate_actions_ex_date ON corporate_actions (ex_date)",
    # 5. price_limits ---------------------------------------------------------------
    f"""
    CREATE TABLE price_limits (
        stock_id        TEXT NOT NULL,
        {_date('date')},
        reference_price REAL,
        limit_up        REAL,
        limit_down      REAL,
        has_limit       INTEGER NOT NULL CHECK (has_limit IN (0,1)),
        limit_pct       REAL,
        basis           TEXT NOT NULL CHECK (basis IN
                            ('computed','official_event','official_exchange','provider')),
        source          TEXT NOT NULL,
        {_ts('ingested_at')},
        PRIMARY KEY (stock_id, date)
    ) STRICT, WITHOUT ROWID
    """,
    # 6. trading_status -------------------------------------------------------------
    f"""
    CREATE TABLE trading_status (
        stock_id          TEXT NOT NULL,
        {_date('date')},
        status            TEXT NOT NULL CHECK (status IN
                              ('normal','no_price','suspended','disposition','full_delivery','delisted')),
        suspension_flag   INTEGER NOT NULL DEFAULT 0 CHECK (suspension_flag IN (0,1)),
        disposition_flag  INTEGER NOT NULL DEFAULT 0 CHECK (disposition_flag IN (0,1)),
        full_delivery_flag INTEGER NOT NULL DEFAULT 0 CHECK (full_delivery_flag IN (0,1)),
        delisting_flag    INTEGER NOT NULL DEFAULT 0 CHECK (delisting_flag IN (0,1)),
        basis             TEXT NOT NULL CHECK (basis IN ('official','inferred')),
        detail            TEXT,
        source            TEXT NOT NULL,
        {_ts('ingested_at')},
        PRIMARY KEY (stock_id, date)
    ) STRICT, WITHOUT ROWID
    """,
    # 7. industry_history ------------------------------------------------------------
    f"""
    CREATE TABLE industry_history (
        stock_id          TEXT NOT NULL,
        {_date('effective_date')},
        industry_category TEXT NOT NULL,
        point_in_time     INTEGER NOT NULL CHECK (point_in_time IN (0,1)),  -- 0 = current class back-filled
        source            TEXT NOT NULL,
        {_ts('ingested_at')},
        PRIMARY KEY (stock_id, effective_date)
    ) STRICT
    """,
    # 8. market_calendar --------------------------------------------------------------
    f"""
    CREATE TABLE market_calendar (
        {_date('date')},
        exchange       TEXT NOT NULL CHECK (exchange IN ('TWSE','TPEX')),
        is_trading_day INTEGER NOT NULL CHECK (is_trading_day IN (0,1)),
        session_type   TEXT NOT NULL CHECK (session_type IN ('regular','saturday_makeup','closed')),
        source         TEXT NOT NULL,
        {_ts('ingested_at')},
        PRIMARY KEY (date, exchange)
    ) STRICT, WITHOUT ROWID
    """,
    # 9. download_log -----------------------------------------------------------------
    # stock_id '*' = market-wide request. One row per (dataset, stock, range); retries update it.
    f"""
    CREATE TABLE download_log (
        dataset       TEXT NOT NULL,
        stock_id      TEXT NOT NULL,
        {_date('start_date')},
        {_date('end_date')},
        status        TEXT NOT NULL CHECK (status IN ('success','empty','failed','gated','partial')),
        row_count     INTEGER NOT NULL DEFAULT 0 CHECK (row_count >= 0),
        http_status   INTEGER,
        attempts      INTEGER NOT NULL DEFAULT 1 CHECK (attempts >= 1),
        error_message TEXT,
        {_ts('updated_at')},
        PRIMARY KEY (dataset, stock_id, start_date, end_date),
        CHECK (end_date >= start_date)
    ) STRICT
    """,
    "CREATE INDEX ix_download_log_status ON download_log (status)",
    # --- support tables ----------------------------------------------------------------
    # Every value an upsert overwrites is preserved here (provider revisions are never silent).
    f"""
    CREATE TABLE data_revisions (
        revision_id  INTEGER PRIMARY KEY,
        table_name   TEXT NOT NULL,
        row_key      TEXT NOT NULL CHECK (json_valid(row_key)),
        old_values   TEXT NOT NULL CHECK (json_valid(old_values)),
        new_values   TEXT NOT NULL CHECK (json_valid(new_values)),
        {_ts('revised_at')}
    ) STRICT
    """,
    "CREATE INDEX ix_data_revisions_table ON data_revisions (table_name, revised_at)",
    f"""
    CREATE TABLE data_quality_issues (
        issue_id    INTEGER PRIMARY KEY,
        run_id      TEXT NOT NULL,
        table_name  TEXT NOT NULL,
        stock_id    TEXT,
        {_date('date', True)},
        check_name  TEXT NOT NULL,
        severity    TEXT NOT NULL CHECK (severity IN ('info','warning','error')),
        category    TEXT NOT NULL,
        detail      TEXT,
        action      TEXT NOT NULL CHECK (action IN ('keep','flag','exclude')),
        {_ts('created_at')}
    ) STRICT
    """,
    "CREATE INDEX ix_dq_issues_run ON data_quality_issues (run_id, check_name)",
    "CREATE INDEX ix_dq_issues_stock ON data_quality_issues (stock_id, date)",
    f"""
    CREATE TABLE benchmark_index (
        index_id TEXT NOT NULL,
        {_date('date')},
        value    REAL NOT NULL,
        source   TEXT NOT NULL,
        {_ts('ingested_at')},
        PRIMARY KEY (index_id, date)
    ) STRICT, WITHOUT ROWID
    """,
]

# Natural keys used by DatabaseManager.upsert (must match the PRIMARY KEYs above).
TABLE_KEYS: dict[str, tuple[str, ...]] = {
    "security_master": ("stock_id",),
    "daily_prices_raw": ("stock_id", "date"),
    "daily_prices_adj": ("stock_id", "date"),
    "corporate_actions": ("stock_id", "ex_date", "action_type"),
    "price_limits": ("stock_id", "date"),
    "trading_status": ("stock_id", "date"),
    "industry_history": ("stock_id", "effective_date"),
    "market_calendar": ("date", "exchange"),
    "download_log": ("dataset", "stock_id", "start_date", "end_date"),
    "benchmark_index": ("index_id", "date"),
}

# Bookkeeping columns: a change in these alone is not a data revision.
BOOKKEEPING_COLUMNS = frozenset({"ingested_at", "updated_at", "computed_at"})

REQUIRED_TABLES = (
    "security_master", "daily_prices_raw", "daily_prices_adj", "corporate_actions", "price_limits",
    "trading_status", "industry_history", "market_calendar", "download_log",
)
