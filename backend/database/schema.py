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


# =====================================================================================
# Schema v2 (platform spec section 6.3): rename to spec names, add data-side tables and views.
# Strategy / backtest tables are added in v3 together with the code that writes them.
# =====================================================================================
SCHEMA_V2: list[str] = [
    # --- renames to the platform spec names ------------------------------------------
    "ALTER TABLE market_calendar RENAME TO trading_calendar",
    "ALTER TABLE download_log RENAME TO data_download_log",
    "ALTER TABLE data_quality_issues RENAME TO data_validation_log",
    "ALTER TABLE industry_history RENAME TO industry_classification",
    # --- security_history: validity periods of name / exchange / type (SCD type 2) -----
    f"""
    CREATE TABLE security_history (
        stock_id      TEXT NOT NULL,
        {_date('valid_from')},
        {_date('valid_to', True)},
        stock_name    TEXT,
        exchange      TEXT NOT NULL CHECK (exchange IN ('TWSE','TPEX','EMERGING','UNKNOWN')),
        security_type TEXT,
        evidence      TEXT NOT NULL,      -- e.g. 'tpex_monthly_snapshot'
        source        TEXT NOT NULL,
        {_ts('ingested_at')},
        PRIMARY KEY (stock_id, valid_from, exchange),
        CHECK (valid_to IS NULL OR valid_to >= valid_from)
    ) STRICT
    """,
    # --- weekly_prices: built from daily bars; week_end = actual last trading day -------
    f"""
    CREATE TABLE weekly_prices (
        stock_id     TEXT NOT NULL,
        week_key     INTEGER NOT NULL CHECK (week_key BETWEEN 199001 AND 209953),   -- ISO year*100 + week
        price_basis  TEXT NOT NULL CHECK (price_basis IN ('raw','adj')),
        {_date('week_start')},           -- first trading day of the week
        {_date('week_end')},             -- last trading day of the week (not necessarily Friday)
        open REAL, high REAL, low REAL, close REAL,
        volume       REAL,
        trading_days INTEGER NOT NULL CHECK (trading_days BETWEEN 1 AND 7),
        complete     INTEGER NOT NULL CHECK (complete IN (0,1)),   -- 1 only when the week is over at build time
        {_date('built_as_of')},
        {_ts('computed_at')},
        PRIMARY KEY (stock_id, week_key, price_basis)
    ) STRICT, WITHOUT ROWID
    """,
    "CREATE INDEX ix_weekly_prices_week_end ON weekly_prices (week_end)",
    # --- period tables -----------------------------------------------------------------
    f"""
    CREATE TABLE suspension_history (
        stock_id   TEXT NOT NULL,
        {_date('start_date')},
        {_date('end_date')},
        trading_days INTEGER NOT NULL CHECK (trading_days >= 1),
        basis      TEXT NOT NULL CHECK (basis IN ('official','inferred')),
        reason     TEXT,
        source     TEXT NOT NULL,
        {_ts('ingested_at')},
        PRIMARY KEY (stock_id, start_date, basis),
        CHECK (end_date >= start_date)
    ) STRICT
    """,
    f"""
    CREATE TABLE disposition_history (
        stock_id   TEXT NOT NULL,
        {_date('start_date')},
        {_date('end_date')},
        measure    TEXT,
        condition  TEXT,
        {_date('announced_date', True)},
        source     TEXT NOT NULL,
        {_ts('ingested_at')},
        PRIMARY KEY (stock_id, start_date),
        CHECK (end_date >= start_date)
    ) STRICT
    """,
    # --- data management -----------------------------------------------------------------
    f"""
    CREATE TABLE data_source_registry (
        source_id          TEXT NOT NULL PRIMARY KEY,
        name               TEXT NOT NULL,
        provider           TEXT NOT NULL,
        access_method      TEXT NOT NULL,
        endpoint           TEXT,
        tier_required      TEXT,
        {_date('coverage_start', True)},
        coverage_note      TEXT,
        fields             TEXT CHECK (fields IS NULL OR json_valid(fields)),
        usage_limits       TEXT,
        update_frequency   TEXT,
        completeness_notes TEXT,
        verification       TEXT NOT NULL,     -- how the facts above were verified
        {_date('verified_on')},
        target_table       TEXT,
        {_ts('updated_at')}
    ) STRICT
    """,
    f"""
    CREATE TABLE data_versions (
        version_id      INTEGER PRIMARY KEY,
        {_ts('created_at')},
        {_date('as_of')},
        description     TEXT NOT NULL,
        row_counts      TEXT NOT NULL CHECK (json_valid(row_counts)),
        last_revision_id INTEGER,
        content_hash    TEXT NOT NULL
    ) STRICT
    """,
    # --- optional chip-data module (manual import only; never part of the baseline) -----------
    f"""
    CREATE TABLE chip_import_log (
        import_id     INTEGER PRIMARY KEY,
        file_name     TEXT NOT NULL,
        file_sha256   TEXT NOT NULL UNIQUE,
        source        TEXT NOT NULL,
        field_mapping TEXT NOT NULL CHECK (json_valid(field_mapping)),
        rows_imported INTEGER NOT NULL DEFAULT 0,
        status        TEXT NOT NULL CHECK (status IN ('imported','rejected','partial')),
        notes         TEXT,
        {_ts('imported_at')}
    ) STRICT
    """,
    f"""
    CREATE TABLE chip_daily (
        stock_id       TEXT NOT NULL,
        {_date('date')},
        metric         TEXT NOT NULL,     -- e.g. 'foreign_net_buy_shares'
        value          REAL,
        {_date('available_from')},        -- first trading day the value may be used (default t+1)
        published_at   TEXT,
        import_id      INTEGER NOT NULL REFERENCES chip_import_log (import_id),
        PRIMARY KEY (stock_id, date, metric)
    ) STRICT, WITHOUT ROWID
    """,
    f"""
    CREATE TABLE chip_broker (
        stock_id       TEXT NOT NULL,
        {_date('date')},
        broker_id      TEXT NOT NULL,
        broker_name    TEXT,
        buy_shares     REAL,
        sell_shares    REAL,
        {_date('available_from')},
        import_id      INTEGER NOT NULL REFERENCES chip_import_log (import_id),
        PRIMARY KEY (stock_id, date, broker_id)
    ) STRICT, WITHOUT ROWID
    """,
    f"""
    CREATE TABLE chip_margin (
        stock_id        TEXT NOT NULL,
        {_date('date')},
        margin_balance  REAL,
        short_balance   REAL,
        {_date('available_from')},
        source          TEXT NOT NULL,
        import_id       INTEGER REFERENCES chip_import_log (import_id),
        PRIMARY KEY (stock_id, date)
    ) STRICT, WITHOUT ROWID
    """,
    # --- views with the spec names (one fact, one place) -----------------------------------
    """
    CREATE VIEW daily_prices AS
    SELECT r.stock_id, r.date, r.open, r.high, r.low, r.close, r.volume, r.turnover, r.trade_count, r.has_price,
           a.adj_open, a.adj_high, a.adj_low, a.adj_close, a.adj_volume, a.price_factor, a.method AS adj_method
    FROM daily_prices_raw r LEFT JOIN daily_prices_adj a ON a.stock_id = r.stock_id AND a.date = r.date
    """,
    """
    CREATE VIEW dividend_history AS SELECT * FROM corporate_actions
    WHERE action_type IN ('cash_dividend','stock_dividend','cash_and_stock_dividend')
    """,
    """
    CREATE VIEW stock_split_history AS SELECT * FROM corporate_actions
    WHERE action_type IN ('split','reverse_split','par_value_change')
    """,
    """
    CREATE VIEW capital_reduction_history AS SELECT * FROM corporate_actions WHERE action_type = 'capital_reduction'
    """,
]

TABLE_KEYS.update({
    "trading_calendar": ("date", "exchange"),
    "data_download_log": ("dataset", "stock_id", "start_date", "end_date"),
    "industry_classification": ("stock_id", "effective_date"),
    "security_history": ("stock_id", "valid_from", "exchange"),
    "weekly_prices": ("stock_id", "week_key", "price_basis"),
    "suspension_history": ("stock_id", "start_date", "basis"),
    "disposition_history": ("stock_id", "start_date"),
    "data_source_registry": ("source_id",),
    "chip_daily": ("stock_id", "date", "metric"),
    "chip_broker": ("stock_id", "date", "broker_id"),
    "chip_margin": ("stock_id", "date"),
})
for _old in ("market_calendar", "download_log", "industry_history"):
    TABLE_KEYS.pop(_old, None)

REQUIRED_TABLES = (
    "security_master", "security_history", "industry_classification",
    "daily_prices_raw", "daily_prices_adj", "weekly_prices", "trading_calendar",
    "corporate_actions", "trading_status", "price_limits", "suspension_history", "disposition_history",
    "data_download_log", "data_validation_log", "data_source_registry", "data_versions",
    "chip_daily", "chip_broker", "chip_margin", "chip_import_log",
)
REQUIRED_VIEWS = ("daily_prices", "dividend_history", "stock_split_history", "capital_reduction_history")


# =====================================================================================
# Schema v3: listing evidence found in the Stage 2 acceptance run (emerging-board history).
# SQLite cannot change a CHECK constraint in place and security_master is referenced by
# foreign keys, so instead of rebuilding the table a new column `listing_basis` with the
# extended value set supersedes `listing_date_basis` (kept for compatibility, copied over).
# =====================================================================================
SCHEMA_V3: list[str] = [
    f"ALTER TABLE security_master ADD COLUMN emerging_exit_date TEXT CHECK (emerging_exit_date IS NULL OR "
    f"emerging_exit_date {DATE_CHECK})",
    "ALTER TABLE security_master ADD COLUMN listing_basis TEXT CHECK (listing_basis IS NULL OR listing_basis IN "
    "('official','emerging_exit','inferred_from_prices','tpex_first_seen','first_price_date'))",
    "UPDATE security_master SET listing_basis = listing_date_basis WHERE listing_date_basis IS NOT NULL",
]


# =====================================================================================
# Schema v4 (Stage 3): strategy versions, technical indicators, strategy signals.
# Condition and entry flags are stored as bitmasks to keep the full-market table small
# (about 7 million rows per strategy version); the *_readable view expands them.
# =====================================================================================
CONDITION_BITS = {"daily_trend": 1, "squeeze": 2, "recent_squeeze": 4, "breakout": 8, "volume_confirm_incl": 16,
                  "volume_confirm_prior": 32, "weekly_trend": 64, "liquid": 128, "eligible": 256,
                  "volume_confirm": 512}
ENTRY_BITS = {"A": 1, "B": 2, "C": 4, "D": 8, "E": 16, "S0": 32}

SCHEMA_V4: list[str] = [
    f"""
    CREATE TABLE strategy_versions (
        strategy_version_id TEXT NOT NULL PRIMARY KEY,      -- sha256 of params, first 16 hex
        name                TEXT NOT NULL,
        params              TEXT NOT NULL CHECK (json_valid(params)),
        indicator_version   TEXT NOT NULL,
        code_version        TEXT NOT NULL,
        description         TEXT,
        {_ts('created_at')}
    ) STRICT
    """,
    f"""
    CREATE TABLE technical_indicators (
        stock_id          TEXT NOT NULL,
        {_date('date')},
        indicator_version TEXT NOT NULL,
        close_raw REAL, close_adj REAL, volume_adj REAL,
        ma13 REAL, ma34 REAL, ma55 REAL, slope55 REAL,
        bb_mid REAL, bb_upper REAL, bb_lower REAL, bbw REAL, bbw_pct REAL,
        atr14_raw REAL,
        volume_ma20_incl REAL, volume_ma20_prior REAL, turnover_ma20 REAL,
        w_ma13 REAL, w_ma34 REAL, w_ma55 REAL,
        {_date('weekly_source_week_end', True)},
        {_ts('computed_at')},
        PRIMARY KEY (stock_id, date, indicator_version)
    ) STRICT, WITHOUT ROWID
    """,
    f"""
    CREATE TABLE strategy_signals (
        stock_id            TEXT NOT NULL,
        {_date('date')},                                   -- signal day t
        strategy_version_id TEXT NOT NULL REFERENCES strategy_versions (strategy_version_id),
        signal_as_of        TEXT NOT NULL,                 -- t at the 13:30 Taipei close: when the signal is known
        {_date('earliest_entry_date', True)},              -- next trading day (NULL if not yet in the calendar)
        conditions          INTEGER NOT NULL,              -- bitmask, see CONDITION_BITS
        entries             INTEGER NOT NULL,              -- bitmask, see ENTRY_BITS
        {_ts('computed_at')},
        PRIMARY KEY (stock_id, date, strategy_version_id)
    ) STRICT, WITHOUT ROWID
    """,
    "CREATE INDEX ix_strategy_signals_date ON strategy_signals (strategy_version_id, date, entries)",
    "CREATE VIEW strategy_signals_readable AS SELECT stock_id, date, strategy_version_id, signal_as_of, "
    "earliest_entry_date, "
    + ", ".join(f"(conditions & {b}) > 0 AS {n}" for n, b in CONDITION_BITS.items()) + ", "
    + ", ".join(f"(entries & {b}) > 0 AS entry_{n}" for n, b in ENTRY_BITS.items())
    + " FROM strategy_signals",
]

TABLE_KEYS.update({
    "strategy_versions": ("strategy_version_id",),
    "technical_indicators": ("stock_id", "date", "indicator_version"),
    "strategy_signals": ("stock_id", "date", "strategy_version_id"),
})
REQUIRED_TABLES = REQUIRED_TABLES + ("strategy_versions", "technical_indicators", "strategy_signals")
