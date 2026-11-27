"""Experiment registry: every experiment and every trial is written down, good or bad."""
from __future__ import annotations

import datetime as dt
import json
import math
import uuid

from backend.database.db_manager import DatabaseManager, utc_now


def clean(x):
    """JSON-safe copy: NaN/inf -> None, numpy scalars -> python."""
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if hasattr(x, "item") and not isinstance(x, (str, bytes)):
        x = x.item()
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    return x


class Registry:
    def __init__(self, db: DatabaseManager):
        self.db = db
        self._n: dict[str, int] = {}

    def start(self, name: str, kind: str, design: dict, segment: str, hypothesis: str | None = None,
              strategy_version_id: str | None = None, preregistration: str = "docs/stage5_preregistration.md") -> str:
        eid = f"exp-{name}-{dt.datetime.now(dt.UTC).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:4]}"
        dv = self.db.read_sql("SELECT MAX(version_id) v FROM data_versions").v[0]
        self.db.insert("experiment_registry", [{
            "experiment_id": eid, "name": name, "kind": kind, "hypothesis": hypothesis,
            "design": json.dumps(clean(design), ensure_ascii=False), "segment": segment,
            "strategy_version_id": strategy_version_id, "data_version_id": None if dv is None else int(dv),
            "preregistration": preregistration, "created_at": utc_now(), "status": "running", "result_summary": None}])
        self._n[eid] = 0
        return eid

    def trial(self, eid: str, label: str, params: dict, segment: str, metrics: dict, run_id: str | None = None) -> int:
        self._n[eid] = self._n.get(eid, 0) + 1
        self.db.insert("experiment_trials", [{
            "experiment_id": eid, "trial_no": self._n[eid], "label": label,
            "params": json.dumps(clean(params), ensure_ascii=False), "segment": segment,
            "metrics": json.dumps(clean(metrics), ensure_ascii=False), "run_id": run_id, "created_at": utc_now()}])
        return self._n[eid]

    def finish(self, eid: str, summary: dict, status: str = "completed") -> None:
        # the registry row is the experiment's header: its result is filled in once, at the end
        with self.db.transaction() as conn:
            conn.execute("UPDATE experiment_registry SET status = ?, result_summary = ? WHERE experiment_id = ?",
                         (status, json.dumps(clean(summary), ensure_ascii=False), eid))

    def total_trials(self) -> int:
        return int(self.db.read_sql("SELECT COUNT(*) n FROM experiment_trials").n[0])
