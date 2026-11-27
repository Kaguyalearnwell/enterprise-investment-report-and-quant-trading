"""Stage 2.3: provider normalisation, rate limiting, retry and resumable downloads.

The HTTP layer is replaced by FakeSession, which serves REAL FinMind responses
recorded in tests/fixtures/finmind/ (captured 2026-11-27) and filters them by the
requested date range like the live API does. No network access in these tests.
"""
import json
from pathlib import Path

import pandas as pd
import pytest
import requests

from src.data.downloader import Downloader
from src.data.security_master import build_security_master
from src.data_providers.base_provider import DatasetNotAvailable
from src.data_providers.finmind_provider import FinMindProvider, classify_security
from src.database.db_manager import DatabaseManager
from src.utils.retry import RateLimiter, TransientError, retry

FIX = Path(__file__).parent / "fixtures" / "finmind"
PRICE_FILES = {"2330": "price_2330.json", "3068": "price_3068.json", "2317": "price_2317_noprice.json",
               "9929": "price_9929_noprice.json"}


def fixture(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status
        self.text = json.dumps(body)

    def json(self):
        return self._body


class FakeSession:
    """Serves recorded responses; can inject failures: {call_index: exception or (status, body)}."""

    def __init__(self, failures=None):
        self.headers, self.calls, self.failures = {}, [], failures or {}

    def get(self, url, params, timeout):
        self.calls.append(dict(params))
        n = len(self.calls)
        if n in self.failures:
            f = self.failures[n]
            if isinstance(f, BaseException):
                raise f
            return FakeResponse(f[1], f[0])
        ds, sid = params["dataset"], params.get("data_id")
        if ds in ("TaiwanStockPriceAdj", "TaiwanStockPriceLimit"):
            return FakeResponse({"msg": "Your level is register. Please update your user level.", "status": 400}, 400)
        body = {
            "TaiwanStockInfo": lambda: fixture("info.json"),
            "TaiwanStockDelisting": lambda: fixture("delisting.json"),
            "TaiwanStockTradingDate": lambda: fixture("trading_date.json"),
            "TaiwanStockSplitPrice": lambda: fixture("split.json"),
            "TaiwanStockParValueChange": lambda: fixture("parvalue.json"),
            "TaiwanStockTotalReturnIndex": lambda: {"msg": "success", "status": 200, "data": [
                {"price": 100.0, "stock_id": sid, "date": "2024-06-10"}]},
            "TaiwanStockPrice": lambda: fixture(PRICE_FILES[sid]) if sid in PRICE_FILES else
            {"msg": "success", "status": 200, "data": []},
            "TaiwanStockDividendResult": lambda: fixture(f"dividend_{sid}.json") if (FIX / f"dividend_{sid}.json").exists()
            else {"msg": "success", "status": 200, "data": []},
            "TaiwanStockCapitalReductionReferencePrice": lambda: fixture("capred_2327.json"),
        }[ds]()
        s, e = params.get("start_date"), params.get("end_date")
        if s and ds not in ("TaiwanStockInfo", "TaiwanStockDelisting", "TaiwanStockTradingDate", "TaiwanStockSplitPrice"):
            body = {**body, "data": [r for r in body["data"] if s <= r["date"] <= (e or "9999-12-31")]}
        return FakeResponse(body)


def provider(session=None, **kw):
    lim = RateLimiter(10_000, 0.0, sleep=lambda s: None)
    return FinMindProvider(session=session or FakeSession(), limiter=lim, sleep=lambda s: None, **kw)


@pytest.fixture
def db(tmp_path):
    d = DatabaseManager(tmp_path / "db.sqlite")
    d.initialize()
    yield d
    d.close()


# ---- provider normalisation (real recorded responses) ---------------------------------------
def test_classify_security():
    assert [classify_security(x) for x in ["2330", "2881A", "0050", "00679B", "020000", "911602", "Semiconductor"]] == \
        ["common", "preferred", "etf", "etf", "etn", "tdr", "other"]


def test_prices_zero_means_no_price_and_volume_in_shares():
    df = provider().daily_prices("2317", "2025-07-28", "2025-08-01").data.set_index("date")
    assert df.loc["2025-07-30", "has_price"] == 0 and df.loc["2025-07-30", "close"] is None
    assert df.loc["2025-07-29", "close"] == 171.5 and df.loc["2025-07-29", "volume"] == 49613588


def test_volume_without_price_is_still_no_price():
    """9929 on 2025-07-31: 451 shares traded but no published price (FinMind docs example)."""
    df = provider().daily_prices("9929", "2025-07-29", "2025-08-01").data.set_index("date")
    assert df.loc["2025-07-31", "has_price"] == 0 and df.loc["2025-07-31", "volume"] == 451


def test_dividend_mapping_and_factor():
    df = provider().dividends("3068", "2010-01-01", "2018-12-31").data
    assert set(df.action_type) == {"cash_dividend", "cash_and_stock_dividend"}
    r = df[df.ex_date == "2011-08-17"].iloc[0]                    # 除息 4.36, before 54.8, ref 50.44
    assert r.cash_dividend == 4.36 and r.adjustment_factor == pytest.approx(50.44 / 54.8)
    both = df[df.action_type == "cash_and_stock_dividend"].iloc[0]
    assert pd.isna(both.cash_dividend) and pd.isna(both.stock_dividend)   # combined value is not split by guesswork


def test_market_actions_dedupe_par_value():
    df = provider().market_corporate_actions("2015-01-01", "2026-11-27").data
    assert not df.duplicated(["stock_id", "ex_date", "action_type"]).any()
    r = df[(df.stock_id == "0050") & (df.ex_date == "2025-06-18")].iloc[0]    # 0050 1-to-4 split
    assert r.action_type == "split" and r.split_ratio == pytest.approx(188.65 / 47.16)
    assert len(df.attrs["unmapped"]) == 1                                     # one row with empty type


def test_calendar_cut_at_as_of_and_saturday():
    df = provider().trading_calendar("2026-11-24").data
    assert df.date.max() == "2026-11-24"
    assert df.loc[df.date == "2016-01-30", "session_type"].unique().tolist() == ["saturday_makeup"]
    assert set(df.exchange) == {"TWSE", "TPEX"}


def test_security_list_dedupes_and_drops_index_names():
    sess = FakeSession()
    info = fixture("info.json")
    info["data"].append({"industry_category": "Index", "stock_id": "Semiconductor", "stock_name": "半導體類",
                         "type": "twse", "date": "2026-11-27"})
    orig = sess.get
    sess.get = lambda url, params, timeout: (FakeResponse(info) if params["dataset"] == "TaiwanStockInfo"
                                            else orig(url, params, timeout))
    df = provider(sess).security_list().data.set_index("stock_id")
    assert "Semiconductor" not in df.index and df.index.is_unique
    assert df.loc["2330", "industry_category"] == "半導體業"           # not the aggregate '電子工業'
    assert df.loc["006201", "exchange"] == "TPEX" and df.loc["006201", "in_current_snapshot"]
    assert not df.loc["1230", "in_current_snapshot"]


def test_security_master_reconciliation_rules():
    p = provider()
    master, issues = build_security_master(p.security_list().data, p.delisted_securities().data, "finmind",
                                           "2026-11-27T00:00:00+00:00")
    m = master.set_index("stock_id")
    assert m.loc["1230", "delisting_date"] == "2001-11-01"                 # stale listing row + delisting
    assert m.loc["3068", "exchange"] == "UNKNOWN" and m.loc["3068", "delisting_date"] == "2018-06-13"
    assert pd.isna(m.loc["2330", "delisting_date"])
    cats = dict(zip(issues.stock_id, issues.category))
    assert cats["3068"] == "delisted_exchange_unknown"
    assert "2330" not in cats


# ---- rate limiter / retry -----------------------------------------------------------------------
class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_rate_limiter_min_interval_and_hourly_cap():
    c = FakeClock()
    lim = RateLimiter(max_per_hour=3, min_interval=2.0, clock=c, sleep=c.sleep)
    stamps = []
    for _ in range(4):
        lim.acquire()
        stamps.append(c.t)
    assert stamps[:3] == [0.0, 2.0, 4.0]
    assert stamps[3] == pytest.approx(3600.0)        # 4th call waits for the first to leave the window


def test_retry_then_success_and_give_up():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise TransientError("boom")
        return "ok"

    assert retry(flaky, attempts=5, sleep=lambda s: None) == "ok" and len(calls) == 3
    with pytest.raises(TransientError):
        retry(lambda: (_ for _ in ()).throw(TransientError("x")), attempts=2, sleep=lambda s: None)


def test_http_402_pauses_then_retries():
    sess = FakeSession(failures={1: (402, {"msg": "Requests reach the upper limit. https://finmindtrade.com/",
                                           "status": 402})})
    paused = []
    p = provider(sess)
    p.limiter.penalize = lambda s: paused.append(s)
    df = p.daily_prices("2330", "2024-06-10", "2024-06-21").data
    assert len(df) == 9 and paused == [600.0] and len(sess.calls) == 2      # 2024-06-10 was a holiday


def test_network_error_retried():
    sess = FakeSession(failures={1: requests.ConnectionError("reset"), 2: requests.Timeout("slow")})
    assert len(provider(sess).daily_prices("2330", "2024-06-10", "2024-06-21").data) == 9
    assert len(sess.calls) == 3


def test_paid_dataset_raises_not_available_without_retry():
    sess = FakeSession()
    p = provider(sess)
    with pytest.raises(DatasetNotAvailable):
        p._request("TaiwanStockPriceAdj", data_id="2330")
    assert len(sess.calls) == 1


# ---- downloader: resume, skip, incremental, failure logging ------------------------------------------
def make_downloader(db, session=None, tmp=None, as_of="2026-11-27"):
    dl = Downloader(db, provider(session), raw_dir=tmp, as_of=as_of)
    dl.sync_reference("2013-01-01")
    return dl


def test_reference_sync(db, tmp_path):
    dl = make_downloader(db, tmp=tmp_path / "raw")
    counts = db.row_counts()
    assert counts["security_master"] > 10 and counts["market_calendar"] > 0 and counts["benchmark_index"] == 2
    assert counts["data_quality_issues"] >= 1
    assert (tmp_path / "raw" / "finmind" / "TaiwanStockInfo").exists()       # raw responses archived
    assert db.get_trading_days("2026-01-01", "2027-12-31", as_of="2026-11-27")[-1] <= "2026-11-27"


def test_download_then_rerun_skips_everything(db):
    sess = FakeSession()
    dl = make_downloader(db, sess)
    s1 = dl.download("prices", ["2330", "3068"], "2013-01-01")
    assert (s1.success, s1.failed) == (2, 0)
    n = len(sess.calls)
    s2 = dl.download("prices", ["2330", "3068"], "2013-01-01")          # same day: nothing to do
    assert len(sess.calls) == n and s2.skipped == 2


def test_next_day_update_refetches_overlap_window_only(db):
    sess = FakeSession()
    dl = make_downloader(db, sess)
    dl.download("prices", ["2330", "3068"], "2013-01-01")
    n = len(sess.calls)
    dl.as_of = "2026-11-30"                                             # next trading day
    s = dl.download("prices", ["2330", "3068"], "2013-01-01")
    new = sess.calls[n:]
    assert len(new) == 1 and s.skipped == 1                             # delisted 3068 never refetched
    assert (new[0]["data_id"], new[0]["start_date"], new[0]["end_date"]) == ("2330", "2026-11-20", "2026-11-30")


def test_delisted_stock_is_downloaded_and_span_recorded(db):
    dl = make_downloader(db)
    dl.download("prices", ["3068"], "2013-01-01")
    m = db.read_sql("SELECT * FROM security_master WHERE stock_id='3068'").iloc[0]
    assert m.last_price_date == "2018-06-06" and m.delisting_date == "2018-06-13"
    assert "3068" in dl.universe("2015-01-01")                    # delisted stocks stay in the universe


def test_interrupted_run_resumes(db):
    sess = FakeSession(failures={})
    dl = make_downloader(db, sess)
    n0 = len(sess.calls)
    sess.failures = {n0 + 2: KeyboardInterrupt()}                 # dies on the 2nd stock
    with pytest.raises(KeyboardInterrupt):
        dl.download("prices", ["2330", "2317", "3068"], "2013-01-01")
    assert db.completed_downloads("TaiwanStockPrice").stock_id.tolist() == ["2330"]
    s = dl.download("prices", ["2330", "2317", "3068"], "2013-01-01")
    assert sorted(db.completed_downloads("TaiwanStockPrice").stock_id) == ["2317", "2330", "3068"]
    assert s.failed == 0


def test_failed_request_is_logged_and_retried_next_run(db):
    sess = FakeSession()
    dl = make_downloader(db, sess)
    n0 = len(sess.calls)
    sess.failures = {n0 + k: requests.ConnectionError("down") for k in range(1, 6)}   # all 5 attempts fail
    s1 = dl.download("prices", ["2330"], "2013-01-01")
    assert s1.failed == 1
    assert db.read_sql("SELECT status, attempts FROM download_log WHERE dataset='TaiwanStockPrice'").iloc[0].tolist() == ["failed", 1]
    s2 = dl.download("prices", ["2330"], "2013-01-01")
    assert s2.success == 1
    row = db.read_sql("SELECT status, attempts FROM download_log WHERE dataset='TaiwanStockPrice'").iloc[0].tolist()
    assert row == ["success", 2]


def test_history_extended_backwards_fetches_only_head(db):
    sess = FakeSession()
    dl = make_downloader(db, sess)
    dl.download("prices", ["2330"], "2020-01-01")
    n = len(sess.calls)
    dl.download("prices", ["2330"], "2016-01-01", end="2026-11-27")
    new = sess.calls[n:]
    assert new[0]["start_date"] == "2016-01-01" and new[0]["end_date"] == "2019-12-31"


def test_unknown_code_gets_flagged_master_row(db):
    dl = make_downloader(db)
    db.conn.execute("DELETE FROM security_master WHERE stock_id = '2317'")
    dl.download("prices", ["2317"], "2025-07-01")
    m = db.read_sql("SELECT exchange, source FROM security_master WHERE stock_id='2317'").iloc[0].tolist()
    assert m == ["UNKNOWN", "downloader:auto"]
    assert "not_in_any_list" in db.read_sql("SELECT category FROM data_quality_issues").category.tolist()


def test_dividends_download(db):
    dl = make_downloader(db)
    s = dl.download("dividends", ["2330", "3068", "2317"], "2010-01-01")
    assert s.success == 2 and s.empty == 1      # 2317 has no fixture -> verified empty answer
    assert db.read_sql("SELECT COUNT(*) n FROM corporate_actions WHERE stock_id='3068'").n[0] == 8
