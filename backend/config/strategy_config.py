"""Load the TOML research configuration."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = Path(__file__).resolve().parent / "strategy_default.toml"


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict[str, Any]:
    with open(path, "rb") as fh:
        return tomllib.load(fh)
