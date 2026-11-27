"""Command-line entry point.

Examples
--------
    python main.py init-db
    python main.py download reference                     # stock lists, calendar, splits, benchmarks
    python main.py download prices --stocks 2330 2317     # specific stocks
    python main.py download prices --all --limit 10       # first 10 stocks of the universe
    python main.py download all                           # reference + prices + dividends, whole universe
    python main.py status

All downloads are incremental and resumable: re-run the same command after an
interruption and it continues where it stopped.
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys

from backend.config.settings import get_settings
from backend.data.collectors.downloader import DATASETS, Downloader
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
    d.add_argument("--end", default=None, help="default: today")
    d.add_argument("--force", action="store_true", help="re-download even if already covered")
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

    if not s.finmind_token:
        log.warning("FINMIND_TOKEN not set: anonymous limit 300 requests/hour")
    dl = Downloader(db, FinMindProvider(token=s.finmind_token), raw_dir=s.raw_dir)
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
