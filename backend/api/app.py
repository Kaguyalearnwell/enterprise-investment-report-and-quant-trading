"""Read-only research API for the Dashboard (FastAPI).

* No endpoint writes research data, and nothing here can place orders: the system has no broker access.
* OOS protection extends to the API: backtest curves, trades and metrics are cut at the end of the
  Validation segment unless `unseal=true` is passed explicitly (Stage 5 pre-registration).
* Run:  uvicorn backend.api.app:app --port 8000     (database path from TQS_DB_PATH / .env)
"""
from __future__ import annotations

import io
import json
import math
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from backend.backtesting.performance import split_segments
from backend.data.collectors.downloader import default_as_of
from backend.config.settings import PROJECT_ROOT, get_settings
from backend.config.strategy_config import load_config
from backend.database.db_manager import DatabaseManager
from backend.database.schema import CONDITION_BITS, ENTRY_BITS
from backend.research.financial_metrics import metrics_as_of
from backend.strategies.runner import _hash, daily_screen, explain, strategy_params

FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


def _clean(x):
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_clean(v) for v in x]
    if hasattr(x, "item") and not isinstance(x, (str, bytes)):
        x = x.item()
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    return x


def records(df: pd.DataFrame) -> list[dict]:
    return _clean(df.to_dict("records"))


def create_app(db_path: str | Path | None = None) -> FastAPI:
    path = Path(db_path) if db_path else get_settings().db_path
    cfg = load_config()
    app = FastAPI(title="Taiwan Quant Research (read-only)", version="0.9")

    def db() -> DatabaseManager:
        if not path.exists():
            raise HTTPException(503, f"database not found at {path}; run `python -m backend.cli init-db`")
        return DatabaseManager(path)

    def version_id(d: DatabaseManager, volume_mode: str) -> str:
        """Strategy version id computed from the parameters (read-only: never registers anything)."""
        return _hash(strategy_params(cfg, volume_mode))

    # ---- overview ------------------------------------------------------------------------------
    @app.get("/api/health")
    def health():
        return {"status": "ok", "database": str(path), "exists": path.exists()}

    @app.get("/api/overview")
    def overview(volume_mode: str = "include_today"):
        d = db()
        q = lambda s, p=(): d.read_sql(s, p)  # noqa: E731
        last_price = q("SELECT MAX(date) m FROM daily_prices_raw").m[0]
        last_sig = q("SELECT MAX(date) m FROM strategy_signals").m[0]
        vid = version_id(d, volume_mode) if last_sig else None
        per_version = {}
        if vid:
            for v, bit in ENTRY_BITS.items():
                per_version[v] = int(q(f"SELECT COUNT(*) n FROM strategy_signals WHERE strategy_version_id=? AND date=? "
                                       f"AND (entries & {bit}) > 0", (vid, last_sig)).n[0])
        eligible = int(q(f"SELECT COUNT(*) n FROM strategy_signals WHERE strategy_version_id=? AND date=? AND "
                         f"(conditions & {CONDITION_BITS['eligible']}) > 0", (vid, last_sig)).n[0]) if vid else 0
        verdicts = q("SELECT verdict, COUNT(*) n FROM (SELECT stock_id, verdict, MAX(created_at) FROM data_quality_verdicts "
                     "GROUP BY stock_id) GROUP BY verdict")
        by_industry = []
        if vid:
            by_industry = records(q(f"""SELECT COALESCE(m.industry_category, '未分類') industry, COUNT(*) n
                                       FROM strategy_signals s LEFT JOIN security_master m USING(stock_id)
                                       WHERE s.strategy_version_id=? AND s.date=? AND (s.entries & {ENTRY_BITS['E']}) > 0
                                       GROUP BY 1 ORDER BY 2 DESC""", (vid, last_sig)))
        bench = q("SELECT date, value FROM benchmark_index WHERE index_id='TAIEX' ORDER BY date DESC LIMIT 260")
        trend = None
        if len(bench) >= 200:
            b = bench.iloc[::-1]["value"].astype(float)
            trend = {"close": float(b.iloc[-1]), "ma200": float(b.iloc[-200:].mean()),
                     "above_ma200": bool(b.iloc[-1] > b.iloc[-200:].mean()),
                     "vol20_annual": float(b.pct_change().iloc[-20:].std() * math.sqrt(252))}
        out = {
            "securities": int(q("SELECT COUNT(*) n FROM security_master").n[0]),
            "common_listed": int(q("SELECT COUNT(*) n FROM security_master WHERE security_type='common' AND "
                                   "exchange IN ('TWSE','TPEX') AND delisting_date IS NULL").n[0]),
            "stocks_with_prices": int(q("SELECT COUNT(DISTINCT stock_id) n FROM daily_prices_raw").n[0]),
            "last_price_date": last_price, "last_signal_date": last_sig, "eligible_today": eligible,
            "signals_today": per_version, "signals_by_industry_E": by_industry, "market": trend,
            "data_quality_verdicts": dict(zip(verdicts.verdict, verdicts.n.astype(int))),
            "last_download": q("SELECT MAX(updated_at) m FROM data_download_log").m[0],
            "failed_downloads": int(q("SELECT COUNT(*) n FROM data_download_log WHERE status='failed'").n[0]),
            "notice": "研究與模擬用途；不提供任何下單功能。",
        }
        d.close()
        return _clean(out)

    # ---- signals ---------------------------------------------------------------------------------
    def _screen(date: str | None, version: str, volume_mode: str) -> tuple[str, pd.DataFrame]:
        d = db()
        vid = version_id(d, volume_mode)
        date = date or d.read_sql("SELECT MAX(date) m FROM strategy_signals WHERE strategy_version_id=?", (vid,)).m[0]
        df = daily_screen(d, date, vid, version, cfg["risk"]["stop_atr_mult"]) if date else pd.DataFrame()
        d.close()
        return date, df

    @app.get("/api/signals")
    def signals(date: str | None = None, version: str = Query("E", pattern="^(A|B|C|D|E|S0)$"),
                volume_mode: str = Query("include_today", pattern="^(include_today|prior_20)$")):
        date, df = _screen(date, version, volume_mode)
        return {"date": date, "version": version, "volume_mode": volume_mode, "rows": records(df),
                "notice": "研究訊號，不是成交；模擬進場最早為 earliest_entry_date 開盤。"}

    @app.get("/api/signals/export")
    def signals_export(date: str | None = None, version: str = "E", volume_mode: str = "include_today",
                       fmt: str = Query("csv", pattern="^(csv|xlsx)$")):
        date, df = _screen(date, version, volume_mode)
        name = f"signals_{version}_{date}.{fmt}"
        if fmt == "csv":
            return Response(df.to_csv(index=False).encode("utf-8-sig"), media_type="text/csv",
                            headers={"Content-Disposition": f'attachment; filename="{name}"'})
        buf = io.BytesIO()
        df.to_excel(buf, index=False)
        return Response(buf.getvalue(), headers={"Content-Disposition": f'attachment; filename="{name}"'},
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # ---- stocks ----------------------------------------------------------------------------------
    @app.get("/api/stocks/search")
    def search(q: str = Query(..., min_length=1)):
        d = db()
        like = f"%{q}%"
        df = d.read_sql("SELECT stock_id, stock_name, exchange, industry_category, security_type, listing_date, "
                        "delisting_date FROM security_master WHERE stock_id LIKE ? OR stock_name LIKE ? "
                        "ORDER BY security_type='common' DESC, stock_id LIMIT 30", (like, like))
        d.close()
        return records(df)

    @app.get("/api/stocks/{stock_id}")
    def stock(stock_id: str, start: str = "2013-01-01", end: str = "2100-12-31",
              volume_mode: str = "include_today"):
        d = db()
        m = d.read_sql("SELECT * FROM security_master WHERE stock_id=?", (stock_id,))
        if m.empty:
            d.close()
            raise HTTPException(404, f"unknown stock {stock_id}")
        vid = version_id(d, volume_mode)
        iv = d.read_sql("SELECT indicator_version FROM strategy_versions WHERE strategy_version_id=?", (vid,)).iloc[0, 0]
        bars = d.read_sql("""SELECT r.date, r.open, r.high, r.low, r.close, r.volume, r.has_price, a.adj_close,
                                    i.ma13, i.ma34, i.ma55, i.bb_upper, i.bb_mid, i.bb_lower, i.bbw_pct, i.atr14_raw,
                                    s.conditions, s.entries
                             FROM daily_prices_raw r
                             LEFT JOIN daily_prices_adj a ON a.stock_id=r.stock_id AND a.date=r.date
                             LEFT JOIN technical_indicators i ON i.stock_id=r.stock_id AND i.date=r.date AND i.indicator_version=?
                             LEFT JOIN strategy_signals s ON s.stock_id=r.stock_id AND s.date=r.date AND s.strategy_version_id=?
                             WHERE r.stock_id=? AND r.date BETWEEN ? AND ? ORDER BY r.date""",
                          (iv, vid, stock_id, start, end))
        weekly = d.read_sql("SELECT week_key, week_start, week_end, open, high, low, close, volume, complete "
                            "FROM weekly_prices WHERE stock_id=? AND price_basis='raw' AND week_end BETWEEN ? AND ? "
                            "ORDER BY week_key", (stock_id, start, end))
        actions = d.read_sql("SELECT ex_date, action_type, before_price, reference_price FROM corporate_actions "
                             "WHERE stock_id=? ORDER BY ex_date", (stock_id,))
        verdict = d.latest_verdicts([stock_id])
        d.close()
        return _clean({"master": records(m)[0], "bars": records(bars), "weekly": records(weekly),
                       "corporate_actions": records(actions), "verdict": records(verdict)[0] if len(verdict) else None,
                       "strategy_version_id": vid})

    @app.get("/api/stocks/{stock_id}/explain")
    def stock_explain(stock_id: str, date: str, volume_mode: str = "include_today"):
        d = db()
        out = explain(d, stock_id, date, version_id(d, volume_mode))
        d.close()
        return _clean(out)

    @app.get("/api/stocks/{stock_id}/fundamentals")
    def fundamentals(stock_id: str, as_of: str | None = None):
        d = db()
        as_of = as_of or default_as_of()
        out = metrics_as_of(d, stock_id, as_of)
        rev = d.revenue_as_of(stock_id, as_of).tail(36)
        d.close()
        return _clean({"metrics": out, "monthly_revenue": records(rev),
                       "notice": "只使用 as_of 當日已可取得的財報（法定申報期限推定）。"})

    # ---- backtests -----------------------------------------------------------------------------------
    @app.get("/api/backtests")
    def backtests():
        d = db()
        df = d.read_sql("SELECT run_id, created_at, mode, entry_version, cost_scenario, slippage_bps, trailing_basis, "
                        "universe_size, start_date, end_date, status, code_version FROM backtest_runs ORDER BY created_at DESC")
        d.close()
        return records(df)

    def _cut(d: DatabaseManager, run_id: str, unseal: bool) -> tuple[str, dict]:
        days = d.read_sql("SELECT date FROM portfolio_daily WHERE run_id=? ORDER BY date", (run_id,)).date.tolist()
        if not days:
            raise HTTPException(404, f"unknown run {run_id}")
        sp = cfg["split"]
        seg = split_segments(days, (sp["in_sample"], sp["validation"], sp["out_of_sample"]), sp["embargo_bars"])
        return ("2100-12-31" if unseal else seg["VALIDATION"][1]), seg

    @app.get("/api/backtests/{run_id}")
    def backtest(run_id: str, unseal: bool = False):
        d = db()
        cut, seg = _cut(d, run_id, unseal)
        run = d.read_sql("SELECT * FROM backtest_runs WHERE run_id=?", (run_id,))
        daily = d.read_sql("SELECT date, equity, cash, market_value, exposure, open_risk, n_positions FROM portfolio_daily "
                           "WHERE run_id=? AND date <= ? ORDER BY date", (run_id, cut))
        trades = d.read_sql("SELECT * FROM simulated_trades WHERE run_id=? AND entry_date <= ? ORDER BY entry_date",
                            (run_id, cut))
        visible = ("IS", "VALIDATION") if not unseal else ("ALL", "IS", "VALIDATION", "OOS")
        m = d.read_sql("SELECT segment, metric, value FROM performance_metrics WHERE run_id=?", (run_id,))
        m = m[m.segment.isin(visible) | (m.segment.str.startswith("YEAR:") & ((m.segment.str[5:] + "-12-31") <= cut))]
        # A calendar year straddling the seal (e.g. YEAR:2024 when Validation ends 2024-07) contains OOS
        # days, so only years that end on or before the cut are shown.
        metrics: dict = {}
        for r in m.itertuples(index=False):
            metrics.setdefault(r.segment, {})[r.metric] = r.value
        d.close()
        run_rec = records(run.drop(columns=["params"]))[0] if len(run) else None
        return _clean({"run": run_rec, "segments": seg, "sealed": not unseal, "visible_until": cut,
                       "daily": records(daily), "trades": records(trades), "metrics": metrics})

    @app.get("/api/backtests/{run_id}/positions")
    def positions(run_id: str, date: str, unseal: bool = False):
        d = db()
        cut, _ = _cut(d, run_id, unseal)
        if date > cut:
            d.close()
            raise HTTPException(403, "date is in the sealed OOS segment")
        p = d.read_sql("""SELECT p.*, m.stock_name, m.industry_category FROM portfolio_positions p
                          LEFT JOIN security_master m USING(stock_id) WHERE p.run_id=? AND p.date=?""", (run_id, date))
        day = d.read_sql("SELECT * FROM portfolio_daily WHERE run_id=? AND date=?", (run_id, date))
        d.close()
        return _clean({"date": date, "portfolio": records(day)[0] if len(day) else None, "positions": records(p)})

    # ---- experiments / data quality ------------------------------------------------------------------
    @app.get("/api/experiments")
    def experiments():
        d = db()
        df = d.read_sql("SELECT experiment_id, name, kind, hypothesis, segment, created_at, status FROM experiment_registry "
                        "ORDER BY created_at DESC")
        d.close()
        return records(df)

    @app.get("/api/experiments/{experiment_id}")
    def experiment(experiment_id: str):
        d = db()
        e = d.read_sql("SELECT * FROM experiment_registry WHERE experiment_id=?", (experiment_id,))
        if e.empty:
            d.close()
            raise HTTPException(404, "unknown experiment")
        t = d.read_sql("SELECT trial_no, label, params, segment, metrics FROM experiment_trials WHERE experiment_id=? "
                       "ORDER BY trial_no", (experiment_id,))
        d.close()
        rec = records(e)[0]
        for k in ("design", "result_summary"):
            rec[k] = json.loads(rec[k]) if rec.get(k) else None
        trials = [{**r, "params": json.loads(r["params"]), "metrics": json.loads(r["metrics"])} for r in records(t)]
        return _clean({"experiment": rec, "trials": trials})

    @app.get("/api/data-quality")
    def data_quality():
        d = db()
        v = d.latest_verdicts()
        run = d.read_sql("SELECT MAX(run_id) r FROM data_validation_log WHERE run_id LIKE 'dq-%'").r[0]
        issues = d.read_sql("SELECT check_name, category, severity, COUNT(*) n FROM data_validation_log WHERE run_id=? "
                            "GROUP BY 1,2,3 ORDER BY 4 DESC", (run,)) if run else pd.DataFrame()
        top = d.read_sql("SELECT stock_id, date, check_name, category, severity, detail, action FROM data_validation_log "
                         "WHERE run_id=? AND severity IN ('error','warning') ORDER BY severity, stock_id LIMIT 200",
                         (run,)) if run else pd.DataFrame()
        failed = d.read_sql("SELECT dataset, stock_id, start_date, end_date, attempts, error_message, updated_at "
                            "FROM data_download_log WHERE status IN ('failed','gated') ORDER BY updated_at DESC LIMIT 100")
        sources = d.read_sql("SELECT source_id, name, provider, tier_required, coverage_start, usage_limits, "
                             "completeness_notes, verification, verified_on FROM data_source_registry ORDER BY source_id")
        d.close()
        surv = sorted((PROJECT_ROOT / "reports" / "survivorship").glob("*.json"))
        survivorship = json.loads(surv[-1].read_text()) if surv else None
        if survivorship:
            survivorship.pop("monthly", None)
        return _clean({"verdicts": records(v), "latest_run": run, "issue_summary": records(issues),
                       "issues": records(top), "failed_downloads": records(failed), "sources": records(sources),
                       "survivorship": survivorship,
                       "limitations": ["上市（TWSE）股票未經官方比對，不能宣稱已消除倖存者偏誤",
                                       "處置股、全額交割股：免費資料沒有", "歷史產業分類：只有目前分類"]})

    # ---- frontend --------------------------------------------------------------------------------------
    if FRONTEND_DIST.exists():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse({"detail": "not found"}, status_code=404)
            return FileResponse(FRONTEND_DIST / "index.html")

    return app


app = create_app()
