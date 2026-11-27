"""Versioned, checksummed schema migrations.

Each migration is applied once inside a transaction and recorded in
`schema_migrations` with a SHA-256 checksum of its statements. If an already
applied migration is later edited, `apply_migrations` refuses to run, because
the live database would no longer match the code. Schema changes are made by
appending a new migration, never by editing an old one.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import sqlite3
from dataclasses import dataclass

from backend.database.schema import SCHEMA_V1, SCHEMA_V2, SCHEMA_V3, SCHEMA_V4, SCHEMA_V5, SCHEMA_V6, SCHEMA_V7

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Migration:
    version: int
    description: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        normalized = "\n".join(" ".join(s.split()) for s in self.statements)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "initial schema: 9 core tables + revisions, quality issues, benchmark index", tuple(SCHEMA_V1)),
    Migration(2, "platform spec names; security_history, weekly_prices, suspension/disposition history, "
                 "data_source_registry, data_versions, chip tables; spec views", tuple(SCHEMA_V2)),
    Migration(3, "security_master: emerging_exit_date, listing_basis (emerging-board evidence)", tuple(SCHEMA_V3)),
    Migration(4, "strategy_versions, technical_indicators, strategy_signals (+ readable view)", tuple(SCHEMA_V4)),
    Migration(5, "backtests (runs, trades, daily portfolio, positions, cash, metrics) + data-quality verdicts",
              tuple(SCHEMA_V5)),
    Migration(6, "experiment_registry, experiment_trials", tuple(SCHEMA_V6)),
    Migration(7, "fundamentals: financial_statements, monthly_revenue, dividend_policy, valuation_daily", tuple(SCHEMA_V7)),
)


class MigrationError(RuntimeError):
    pass


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
               version INTEGER PRIMARY KEY, description TEXT NOT NULL,
               checksum TEXT NOT NULL, applied_at TEXT NOT NULL) STRICT"""
    )


def current_version(conn: sqlite3.Connection) -> int:
    _ensure_table(conn)
    row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
    return int(row[0])


def apply_migrations(conn: sqlite3.Connection, migrations: tuple[Migration, ...] = MIGRATIONS) -> list[int]:
    """Apply pending migrations; return the versions applied in this call."""
    _ensure_table(conn)
    applied = {v: c for v, c in conn.execute("SELECT version, checksum FROM schema_migrations")}
    for m in migrations:
        if m.version in applied and applied[m.version] != m.checksum:
            raise MigrationError(
                f"migration {m.version} was edited after being applied (checksum mismatch); add a new migration instead"
            )
    done: list[int] = []
    for m in sorted(migrations, key=lambda x: x.version):
        if m.version in applied:
            continue
        try:
            conn.execute("BEGIN")
            for stmt in m.statements:
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO schema_migrations (version, description, checksum, applied_at) VALUES (?,?,?,?)",
                (m.version, m.description, m.checksum, dt.datetime.now(dt.UTC).isoformat(timespec="seconds")),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        log.info("applied migration %d: %s", m.version, m.description)
        done.append(m.version)
    return done
