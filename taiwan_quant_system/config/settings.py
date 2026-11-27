"""Runtime settings: paths and secrets come from environment variables, never from code.

Environment variables (see ../.env.example):
    TQS_DATA_DIR     root data directory            (default: <project>/data)
    TQS_DB_PATH      SQLite database file            (default: <data>/database/taiwan_stocks.sqlite)
    FINMIND_TOKEN    FinMind API token               (optional; in the cloud session the network
                                                      proxy injects it, so use the placeholder
                                                      value "proxy-injected" there)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader (KEY=VALUE lines). Existing environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_path: Path
    raw_dir: Path
    processed_dir: Path
    reports_dir: Path
    finmind_token: str = field(repr=False)   # never printed


def get_settings(env_file: Path | None = PROJECT_ROOT / ".env") -> Settings:
    if env_file is not None:
        _load_dotenv(env_file)
    data_dir = Path(os.environ.get("TQS_DATA_DIR", PROJECT_ROOT / "data"))
    db_path = Path(os.environ.get("TQS_DB_PATH", data_dir / "database" / "taiwan_stocks.sqlite"))
    return Settings(
        data_dir=data_dir,
        db_path=db_path,
        raw_dir=data_dir / "raw",
        processed_dir=data_dir / "processed",
        reports_dir=PROJECT_ROOT / "reports",
        finmind_token=os.environ.get("FINMIND_TOKEN", ""),
    )
