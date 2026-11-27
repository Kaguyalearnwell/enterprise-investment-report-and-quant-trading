"""Command-line entry point.

Examples
--------
    python -m backend.cli init-db
    python -m backend.cli download reference                     # stock lists, calendar, splits, benchmarks
    python -m backend.cli download prices --stocks 2330 2317     # specific stocks
    python -m backend.cli download prices --all --limit 10       # first 10 stocks of the universe
    python -m backend.cli download all                           # reference + prices + dividends, whole universe
    python -m backend.cli status
    python -m backend.cli process --stocks 2330          # adjusted prices, limits, status, weekly bars
    python -m backend.cli validate --stocks 2330         # data-quality report -> reports/data_quality/
    python -m backend.cli audit-survivorship             # TPEx monthly snapshots -> reports/survivorship/
    python -m backend.cli registry                       # seed data_source_registry

All downloads are incremental and resumable: re-run the same command after an
interruption and it continues where it stopped.
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys

from backend.config.settings import get_settings
from backend.data.collectors.downloader import DATASETS, Downloader, default_as_of
from backend.data.pipeline import DataPipeline, seed_source_registry
from backend.data.providers.official_provider import TPExProvider
from backend.data.validators.survivorship_audit import SurvivorshipAudit, write_survivorship_report
from backend.config.strategy_config import load_config
from backend.strategies.runner import SignalRunner, daily_screen, explain
from backend.backtesting.backtester import Backtester
from backend.data.providers.finmind_provider import FinMindProvider
from backend.database.db_manager import DatabaseManager
from backend.core.logger import setup_logging

DEFAULT_START = "2013-01-01"   # weekly MA55 needs ~57 weeks of warm-up before a 2015 backtest start
log = logging.getLogger("main")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Taiwan quant system: data management")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db", help="create / migrate the SQLite database")
    sub.add_parser("status", help="row counts and download log summary")

    d = sub.add_parser("download", help="download data (incremental, resumable)")
    d.add_argument("what", choices=["reference", "prices", "dividends", "capital-reductions", "all"])
    g = d.add_mutually_exclusive_group()
    g.add_argument("--stocks", nargs="+", help="stock ids")
    g.add_argument("--all", action="store_true", help="whole universe from security_master")
    d.add_argument("--market", nargs="+", choices=["TWSE", "TPEX", "UNKNOWN"], default=["TWSE", "TPEX", "UNKNOWN"])
    d.add_argument("--limit", type=int, help="only the first N stocks (testing)")
    d.add_argument("--start", default=DEFAULT_START)
    d.add_argument("--end", default=None, help="default: last final trading day (today after 18:00 Taipei, else yesterday)")
    d.add_argument("--force", action="store_true", help="re-download even if already covered")

    for name, hlp in [("process", "build adjusted prices, limits, status, weekly bars"),
                      ("validate", "run data-quality checks and write a report")]:
        x = sub.add_parser(name, help=hlp)
        gx = x.add_mutually_exclusive_group(required=True)
        gx.add_argument("--stocks", nargs="+")
        gx.add_argument("--all", action="store_true")
    a = sub.add_parser("audit-survivorship", help="compare the universe with official TPEx monthly snapshots")
    a.add_argument("--start", default=DEFAULT_START)
    a.add_argument("--end", default=None)
    sg = sub.add_parser("signals", help="strategy indicators and signals")
    ssub = sg.add_subparsers(dest="signals_cmd", required=True)
    sc = ssub.add_parser("compute", help="compute indicators + signals (both volume definitions)")
    gsc = sc.add_mutually_exclusive_group(required=True)
    gsc.add_argument("--stocks", nargs="+")
    gsc.add_argument("--all", action="store_true")
    sl = ssub.add_parser("list", help="research signals on a date (NOT simulated fills)")
    sl.add_argument("--date", default=None, help="default: latest signal date")
    sl.add_argument("--version", default="E", choices=["A", "B", "C", "D", "E", "S0"])
    sl.add_argument("--volume-mode", default="include_today", choices=["include_today", "prior_20"])
    sl.add_argument("--csv", default=None, help="also write the list to this CSV file")
    se = ssub.add_parser("explain", help="why a stock did / did not signal on a date")
    se.add_argument("stock")
    se.add_argument("date")
    se.add_argument("--volume-mode", default="include_today", choices=["include_today", "prior_20"])
    bt = sub.add_parser("backtest", help="historical simulation (no real orders)")
    bsub = bt.add_subparsers(dest="bt_cmd", required=True)
    br = bsub.add_parser("run", help="run a backtest; every run is stored with a new run id")
    gbr = br.add_mutually_exclusive_group(required=True)
    gbr.add_argument("--stocks", nargs="+")
    gbr.add_argument("--all", action="store_true")
    br.add_argument("--mode", choices=["portfolio", "single_stock"], default="portfolio")
    br.add_argument("--version", default="E", choices=["A", "B", "C", "D", "E", "S0"])
    br.add_argument("--volume-mode", default="include_today", choices=["include_today", "prior_20"])
    br.add_argument("--cost", default="base", choices=["optimistic", "base", "stress"])
    br.add_argument("--slippage", default="low", choices=["low", "high"], help="end of the scenario's slippage range")
    br.add_argument("--basis", default=None, choices=["adjusted", "raw"], help="trailing-stop basis (default: config)")
    br.add_argument("--start", default="2015-01-01")
    br.add_argument("--end", default=None)
    bsub.add_parser("list", help="list stored backtest runs")
    bs = bsub.add_parser("show", help="metrics of one run (In-Sample only unless --unseal)")
    bs.add_argument("run_id")
    bs.add_argument("--unseal", action="store_true", help="also show VALIDATION / OOS (see Stage 4 report)")
    sub.add_parser("registry", help="seed / refresh data_source_registry")
    v = sub.add_parser("version", help="record a data version fingerprint")
    v.add_argument("description")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    s = get_settings()
    setup_logging(log_file=s.reports_dir / "logs" / f"{dt.date.today().isoformat()}.log")
    db = DatabaseManager(s.db_path)
    db.initialize()
    if args.cmd == "init-db":
        log.info("database ready at %s (schema v%d)", s.db_path, db.schema_version)
        return 0
    if args.cmd == "status":
        for t, n in db.row_counts().items():
            print(f"{t:24s} {n:>12,d}")
        print(db.read_sql("SELECT dataset, status, COUNT(*) AS n, SUM(row_count) AS rows FROM data_download_log "
                          "GROUP BY dataset, status ORDER BY dataset, status").to_string(index=False))
        return 0

    as_of = default_as_of()
    if args.cmd in ("process", "validate"):
        pipe = DataPipeline(db, as_of)
        ids = args.stocks or db.read_sql("SELECT DISTINCT stock_id FROM daily_prices_raw ORDER BY 1").stock_id.tolist()
        if args.cmd == "process":
            log.info("processed: %s", pipe.process(ids))
        else:
            run_id, md, issues = pipe.validate(None if args.all else ids, s.reports_dir / "data_quality")
            log.info("validation %s: %d issues -> %s", run_id, len(issues), md)
        return 0
    if args.cmd == "audit-survivorship":
        res = SurvivorshipAudit(db, TPExProvider(), raw_dir=s.raw_dir).run(args.start, args.end or as_of)
        md = write_survivorship_report(res, s.reports_dir / "survivorship")
        log.info("survivorship audit: added %d, exchange resolved %d -> %s", res["added_to_master"],
                 res["exchange_resolved_to_tpex"], md)
        return 0
    if args.cmd == "signals":
        runner = SignalRunner(db, load_config(), as_of)
        if args.signals_cmd == "compute":
            ids = args.stocks or db.read_sql("SELECT DISTINCT stock_id FROM daily_prices_adj ORDER BY 1").stock_id.tolist()
            st = runner.run(ids)
            log.info("signals: %d stocks, %d indicator rows, %d signal rows; versions %s; entries %s",
                     st["stocks"], st["indicator_rows"], st["signal_rows"], st["versions"], st["entries"])
            return 0
        vid = runner.register_version(args.volume_mode)
        if args.signals_cmd == "explain":
            import json as _json
            print(_json.dumps(explain(db, args.stock, args.date, vid), ensure_ascii=False, indent=2, default=str))
            return 0
        date = args.date or db.read_sql("SELECT MAX(date) d FROM strategy_signals WHERE strategy_version_id=?",
                                        (vid,)).d[0]
        df = daily_screen(db, date, vid, args.version)
        print(f"research signals {args.version} on {date} (volume {args.volume_mode}, version {vid}): {len(df)} stocks")
        print("注意：這是研究訊號，不是成交；模擬進場最早為下一交易日開盤。")
        if not df.empty:
            print(df[["stock_id", "stock_name", "industry_category", "close_raw", "turnover_ma20", "bbw_pct",
                      "volume_ratio_incl", "atr14_raw", "reference_initial_stop", "earliest_entry_date"]]
                  .to_string(index=False))
            if args.csv:
                df.to_csv(args.csv, index=False, encoding="utf-8-sig")
        return 0
    if args.cmd == "backtest":
        cfg = load_config()
        if args.bt_cmd == "run":
            vid = SignalRunner(db, cfg, as_of).register_version(args.volume_mode)
            out = Backtester(db, cfg).run(strategy_version_id=vid, entry_version=args.version,
                                          stock_ids=None if args.all else args.stocks, mode=args.mode,
                                          start=args.start, end=args.end, cost_scenario=args.cost,
                                          slippage_end=args.slippage, trailing_basis=args.basis)
            m = out["metrics"].get("IS", {})
            print(f"run {out['run_id']} ({out['status']}), universe {out['universe']}, excluded {out['excluded']}")
            print("In-Sample:", {k: round(m[k], 4) for k in ("trades", "win_rate", "profit_factor", "cagr",
                                                              "max_drawdown", "sharpe", "expectancy_r") if k in m})
            print("VALIDATION / OOS metrics are stored but sealed until Stage 5 (backtest show --unseal).")
            return 0
        if args.bt_cmd == "list":
            print(db.read_sql("SELECT run_id, created_at, mode, entry_version, cost_scenario, slippage_bps, "
                              "trailing_basis, universe_size, status FROM backtest_runs ORDER BY created_at").to_string(index=False))
            return 0
        segs = ("ALL", "IS", "VALIDATION", "OOS") if args.unseal else ("IS",)
        m = db.read_sql("SELECT segment, metric, value FROM performance_metrics WHERE run_id = ?", (args.run_id,))
        piv = m[m.segment.isin(segs)].pivot(index="metric", columns="segment", values="value")
        print(piv.to_string())
        return 0
    if args.cmd == "registry":
        log.info("data_source_registry rows written: %d", seed_source_registry(db))
        return 0
    if args.cmd == "version":
        log.info("data version %d recorded", DataPipeline(db, as_of).snapshot_version(args.description))
        return 0

    if not s.finmind_token:
        log.warning("FINMIND_TOKEN not set: anonymous limit 300 requests/hour")
    dl = Downloader(db, FinMindProvider(token=s.finmind_token), raw_dir=s.raw_dir, as_of=as_of)
    what = args.what
    if what in ("reference", "all"):
        for k, v in dl.sync_reference(args.start).items():
            log.info("reference %-28s %s", k, v)
        if what == "reference":
            return 0

    if what == "all" or args.all:
        stocks = dl.universe(args.start, args.market)
    elif args.stocks:
        stocks = args.stocks
    else:
        log.error("give --stocks or --all")
        return 2
    if args.limit:
        stocks = stocks[: args.limit]
    datasets = ["prices", "dividends"] if what == "all" else [what.replace("-", "_")]
    rc = 0
    for ds in datasets:
        assert ds in DATASETS
        log.info("downloading %s for %d stocks, %s .. %s", ds, len(stocks), args.start, args.end or "today")
        summ = dl.download(ds, stocks, args.start, args.end, force=args.force)
        log.info("done %s: %s", ds, {k: v for k, v in vars(summ).items() if k != "failures"})
        for f in summ.failures[:20]:
            log.warning("failed: %s", f)
        if summ.failed or summ.gated:
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
