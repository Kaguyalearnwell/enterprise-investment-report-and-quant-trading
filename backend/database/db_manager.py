"""SQLite access layer: connection setup, revision-tracked upserts, typed readers.

Only this module issues SQL. The rest of the system talks to it with pandas
DataFrames, so the backtester never depends on the database engine.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import math
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from backend.database.migrations import apply_migrations, current_version
from backend.database.schema import BOOKKEEPING_COLUMNS, TABLE_KEYS

log = logging.getLogger(__name__)


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class UpsertResult:
    inserted: int
    updated: int
    unchanged: int

    @property
    def written(self) -> int:
        return self.inserted + self.updated


def _norm(v: Any) -> Any:
    """Normalise a cell for storage/comparison: NaN/NaT/pd.NA -> None, numpy -> python."""
    if v is None or v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        v = v.item()
        if isinstance(v, float) and math.isnan(v):
            return None
    if isinstance(v, (pd.Timestamp, dt.date)) and not isinstance(v, dt.datetime):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, bool):
        return int(v)
    return v


class DatabaseManager:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, isolation_level=None)   # explicit transactions only
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self._columns: dict[str, list[str]] = {}

    # ---- lifecycle ---------------------------------------------------------------
    def initialize(self) -> list[int]:
        applied = apply_migrations(self.conn)
        self._columns.clear()
        return applied

    @property
    def schema_version(self) -> int:
        return current_version(self.conn)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> DatabaseManager:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    # ---- introspection -------------------------------------------------------------
    def tables(self) -> list[str]:
        q = "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        return [r[0] for r in self.conn.execute(q)]

    def columns(self, table: str) -> list[str]:
        if table not in self._columns:
            if table not in self.tables():
                raise KeyError(f"unknown table {table!r}")
            self._columns[table] = [r[1] for r in self.conn.execute(f'PRAGMA table_info("{table}")')]
        return self._columns[table]

    # ---- writes --------------------------------------------------------------------
    def upsert(self, table: str, rows: pd.DataFrame | Sequence[dict[str, Any]], track_revisions: bool = True) -> UpsertResult:
        """Insert new rows, update changed rows, skip identical rows.

        Every overwritten value is copied to `data_revisions` first (unless
        track_revisions=False, used only for bookkeeping tables such as data_download_log).
        Runs as one transaction: either the whole batch is written or nothing is.
        """
        if table not in TABLE_KEYS:
            raise KeyError(f"{table!r} has no upsert key")
        records = rows.to_dict("records") if isinstance(rows, pd.DataFrame) else list(rows)
        if not records:
            return UpsertResult(0, 0, 0)
        keys = TABLE_KEYS[table]
        known = set(self.columns(table))
        cols = list(records[0].keys())
        # Every row must carry the same columns: silently dropping a column, or
        # writing NULL over an existing value, would both corrupt data.
        bad = next((i for i, r in enumerate(records) if set(r.keys()) != set(cols)), None)
        if bad is not None:
            raise ValueError(f"row {bad} of {table} batch has different columns than row 0")
        unknown = [c for c in cols if c not in known]
        if unknown:
            raise ValueError(f"columns not in {table}: {unknown}")
        missing_keys = [k for k in keys if k not in cols]
        if missing_keys:
            raise ValueError(f"{table} rows must include key columns {missing_keys}")
        norm = [{c: _norm(r.get(c)) for c in cols} for r in records]
        seen: set[tuple] = set()
        for r in norm:
            k = tuple(r[c] for c in keys)
            if k in seen:
                raise ValueError(f"duplicate key in batch for {table}: {k}")
            seen.add(k)

        compare = [c for c in cols if c not in keys and c not in BOOKKEEPING_COLUMNS]
        with self.transaction() as conn:
            existing = self._fetch_existing(conn, table, keys, cols, norm)
            to_write: list[dict] = []
            revisions: list[tuple] = []
            inserted = updated = unchanged = 0
            now = utc_now()
            for r in norm:
                k = tuple(r[c] for c in keys)
                old = existing.get(k)
                if old is None:
                    inserted += 1
                    to_write.append(r)
                    continue
                diff = [c for c in compare if old[c] != r[c]]
                if not diff:
                    unchanged += 1
                    continue
                updated += 1
                to_write.append(r)
                if track_revisions:
                    revisions.append((
                        table, json.dumps(dict(zip(keys, k)), ensure_ascii=False),
                        json.dumps({c: old[c] for c in diff}, ensure_ascii=False),
                        json.dumps({c: r[c] for c in diff}, ensure_ascii=False), now,
                    ))
            if to_write:
                col_sql = ", ".join(f'"{c}"' for c in cols)
                ph = ", ".join("?" for _ in cols)
                non_key = [c for c in cols if c not in keys]
                action = (
                    "DO UPDATE SET " + ", ".join(f'"{c}" = excluded."{c}"' for c in non_key) if non_key else "DO NOTHING"
                )
                sql = (f'INSERT INTO "{table}" ({col_sql}) VALUES ({ph}) '
                       f'ON CONFLICT ({", ".join(keys)}) {action}')
                conn.executemany(sql, [tuple(r[c] for c in cols) for r in to_write])
            if revisions:
                conn.executemany(
                    "INSERT INTO data_revisions (table_name, row_key, old_values, new_values, revised_at) VALUES (?,?,?,?,?)",
                    revisions,
                )
        res = UpsertResult(inserted, updated, unchanged)
        log.debug("upsert %s: %s", table, res)
        return res

    def _fetch_existing(self, conn: sqlite3.Connection, table: str, keys: tuple[str, ...], cols: list[str],
                        rows: list[dict]) -> dict[tuple, dict]:
        conn.execute("DROP TABLE IF EXISTS temp._upsert_keys")
        conn.execute(f"CREATE TEMP TABLE _upsert_keys ({', '.join(keys)})")
        conn.executemany(f"INSERT INTO temp._upsert_keys VALUES ({', '.join('?' for _ in keys)})",
                         [tuple(r[k] for k in keys) for r in rows])
        col_sql = ", ".join(f't."{c}"' for c in cols)
        on = " AND ".join(f't."{k}" = k."{k}"' for k in keys)
        cur = conn.execute(f'SELECT {col_sql} FROM "{table}" t JOIN temp._upsert_keys k ON {on}')
        out = {}
        for vals in cur:
            d = dict(zip(cols, vals))
            out[tuple(d[k] for k in keys)] = d
        conn.execute("DROP TABLE temp._upsert_keys")
        return out

    def insert(self, table: str, rows: pd.DataFrame | Sequence[dict[str, Any]]) -> int:
        """Plain append for log-style tables without a natural key (data_validation_log)."""
        records = rows.to_dict("records") if isinstance(rows, pd.DataFrame) else list(rows)
        if not records:
            return 0
        cols = list(records[0].keys())
        known = set(self.columns(table))
        if any(c not in known for c in cols):
            raise ValueError(f"columns not in {table}: {[c for c in cols if c not in known]}")
        sql = f'INSERT INTO "{table}" ({", ".join(cols)}) VALUES ({", ".join("?" for _ in cols)})'
        with self.transaction() as conn:
            conn.executemany(sql, [tuple(_norm(r.get(c)) for c in cols) for r in records])
        return len(records)

    # ---- download log (resume support) ----------------------------------------------
    def log_download(self, dataset: str, stock_id: str, start_date: str, end_date: str, status: str,
                     row_count: int = 0, http_status: int | None = None, error_message: str | None = None) -> None:
        prev = self.conn.execute(
            "SELECT attempts FROM data_download_log WHERE dataset=? AND stock_id=? AND start_date=? AND end_date=?",
            (dataset, stock_id, start_date, end_date),
        ).fetchone()
        self.upsert("data_download_log", [{
            "dataset": dataset, "stock_id": stock_id, "start_date": start_date, "end_date": end_date,
            "status": status, "row_count": row_count, "http_status": http_status,
            "attempts": (prev[0] + 1) if prev else 1, "error_message": error_message, "updated_at": utc_now(),
        }], track_revisions=False)

    def completed_downloads(self, dataset: str) -> pd.DataFrame:
        """Requests that need no retry: 'success' and 'empty' (a verified empty answer)."""
        return self.read_sql(
            "SELECT stock_id, start_date, end_date, status, row_count FROM data_download_log "
            "WHERE dataset = ? AND status IN ('success','empty')", (dataset,))

    # ---- reads ---------------------------------------------------------------------
    def read_sql(self, sql: str, params: Sequence[Any] = ()) -> pd.DataFrame:
        cur = self.conn.execute(sql, tuple(params))
        cols = [d[0] for d in cur.description] if cur.description else []
        return pd.DataFrame(cur.fetchall(), columns=cols)

    def get_security_master(self, exchanges: Sequence[str] | None = None, security_type: str | None = None) -> pd.DataFrame:
        sql, params = "SELECT * FROM security_master WHERE 1=1", []
        if exchanges:
            sql += f" AND exchange IN ({', '.join('?' for _ in exchanges)})"
            params += list(exchanges)
        if security_type:
            sql += " AND security_type = ?"
            params.append(security_type)
        return self.read_sql(sql + " ORDER BY stock_id", params)

    def get_universe(self, as_of: str, security_type: str = "common") -> pd.DataFrame:
        """Point-in-time universe: listed on or before `as_of` and not delisted before it.

        Uses only dates; a security whose listing date is unknown falls back to its
        first price date. Never uses today's membership to decide a past date.
        Exchange 'UNKNOWN' (delisted stocks found only in the delisting table) is
        included on purpose: dropping them would re-introduce survivorship bias.
        """
        return self.read_sql(
            """SELECT * FROM security_master
               WHERE security_type = ? AND exchange IN ('TWSE','TPEX','UNKNOWN')
                 AND COALESCE(listing_date, first_price_date) <= ?
                 AND (delisting_date IS NULL OR delisting_date > ?)
               ORDER BY stock_id""",
            (security_type, as_of, as_of),
        )

    def get_daily_prices_raw(self, stock_ids: Sequence[str] | None, start: str, end: str) -> pd.DataFrame:
        return self._get_prices("daily_prices_raw", stock_ids, start, end)

    def get_daily_prices_adj(self, stock_ids: Sequence[str] | None, start: str, end: str) -> pd.DataFrame:
        return self._get_prices("daily_prices_adj", stock_ids, start, end)

    def _get_prices(self, table: str, stock_ids: Sequence[str] | None, start: str, end: str) -> pd.DataFrame:
        sql, params = f'SELECT * FROM "{table}" WHERE date BETWEEN ? AND ?', [start, end]
        if stock_ids:
            sql += f" AND stock_id IN ({', '.join('?' for _ in stock_ids)})"
            params += list(stock_ids)
        return self.read_sql(sql + " ORDER BY stock_id, date", params)

    def get_trading_days(self, start: str, end: str, as_of: str, exchange: str = "TWSE") -> list[str]:
        """Trading days in [start, end], never beyond `as_of` (the provider publishes future calendars)."""
        df = self.read_sql(
            "SELECT date FROM trading_calendar WHERE exchange = ? AND is_trading_day = 1 "
            "AND date BETWEEN ? AND ? AND date <= ? ORDER BY date",
            (exchange, start, end, as_of),
        )
        return df["date"].tolist()

    def row_counts(self) -> dict[str, int]:
        return {t: int(self.conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]) for t in self.tables()}

    def latest_verdicts(self, stock_ids: Sequence[str] | None = None) -> pd.DataFrame:
        """Most recent PASS/WARNING/FAIL verdict per stock (latest data-quality run that covered it)."""
        df = self.read_sql(
            """SELECT v.stock_id, v.verdict, v.reasons, v.run_id, v.created_at FROM data_quality_verdicts v
               JOIN (SELECT stock_id, MAX(created_at) m FROM data_quality_verdicts GROUP BY stock_id) x
                 ON x.stock_id = v.stock_id AND x.m = v.created_at""")
        df = df.drop_duplicates("stock_id", keep="last")
        return df[df["stock_id"].isin(stock_ids)] if stock_ids else df
