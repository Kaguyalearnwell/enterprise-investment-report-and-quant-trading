"""Dashboard API (read-only) on the MOCK database: synthetic stock seed 3 with signals and a backtest."""
import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.backtesting.backtester import Backtester
from backend.data.pipeline import DataPipeline
from backend.strategies.runner import SignalRunner

from .test_runner import AS_OF, loaded, stock3  # noqa: F401


@pytest.fixture
def client(loaded, cfg, tmp_path):
    vid = SignalRunner(loaded, cfg, AS_OF).run(["9999"])["versions"]["include_today"]
    DataPipeline(loaded, AS_OF).validate(["9999"], tmp_path)
    run = Backtester(loaded, cfg).run(strategy_version_id=vid, entry_version="A", stock_ids=["9999"],
                                      mode="single_stock", start="2014-06-01", end=AS_OF)
    path = loaded.path
    loaded.close()
    return TestClient(create_app(path)), run["run_id"], vid


def test_overview_and_signals(client):
    c, _, _ = client
    o = c.get("/api/overview").json()
    assert o["stocks_with_prices"] == 1 and o["last_signal_date"] == AS_OF and "下單" in o["notice"]
    s = c.get("/api/signals", params={"version": "A"}).json()
    assert s["date"] == AS_OF and isinstance(s["rows"], list)
    assert c.get("/api/signals", params={"version": "Z"}).status_code == 422


def test_stock_detail_explain_search(client):
    c, _, vid = client
    st = c.get("/api/stocks/9999").json()
    assert st["strategy_version_id"] == vid and len(st["bars"]) > 1000 and st["verdict"]["verdict"] in ("PASS", "WARNING")
    assert c.get("/api/stocks/0000").status_code == 404
    d = next(b["date"] for b in st["bars"] if b["entries"])
    ex = c.get("/api/stocks/9999/explain", params={"date": d}).json()
    assert ex["available"] and len(ex["conditions"]) == 7
    assert c.get("/api/stocks/search", params={"q": "99"}).json()[0]["stock_id"] == "9999"


def test_backtest_is_sealed_after_validation_by_default(client):
    c, run_id, _ = client
    b = c.get(f"/api/backtests/{run_id}").json()
    assert b["sealed"] and b["daily"][-1]["date"] <= b["segments"]["VALIDATION"][1] < b["segments"]["OOS"][0]
    assert "OOS" not in b["metrics"] and "ALL" not in b["metrics"]
    assert all(t["entry_date"] <= b["visible_until"] for t in b["trades"])
    u = c.get(f"/api/backtests/{run_id}", params={"unseal": True}).json()
    assert "OOS" in u["metrics"] and u["daily"][-1]["date"] > b["daily"][-1]["date"]
    oos_day = u["daily"][-1]["date"]
    assert c.get(f"/api/backtests/{run_id}/positions", params={"date": oos_day}).status_code == 403


def test_api_is_read_only(client):
    c, _, _ = client
    for url in ("/api/overview", "/api/signals", "/api/stocks/9999", "/api/backtests", "/api/experiments",
                "/api/data-quality"):
        assert c.get(url).status_code == 200, url
    assert c.post("/api/overview").status_code == 405                    # no write methods exist


def test_export_csv_and_xlsx(client):
    c, _, _ = client
    r = c.get("/api/signals/export", params={"version": "A", "fmt": "csv"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    r = c.get("/api/signals/export", params={"version": "A", "fmt": "xlsx"})
    assert r.status_code == 200 and r.content[:2] == b"PK"                # xlsx is a zip file


def test_sealed_year_metrics_never_straddle_the_cut(client):
    c, run_id, _ = client
    b = c.get(f"/api/backtests/{run_id}").json()
    years = [k[5:] for k in b["metrics"] if k.startswith("YEAR:")]
    assert years and all(f"{y}-12-31" <= b["visible_until"] for y in years)
