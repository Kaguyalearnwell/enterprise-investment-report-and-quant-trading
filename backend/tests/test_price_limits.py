"""Stage 2 step 6: price limits. Values in test_real_tpex_official_limits are REAL (TPEx 2016-01-04)."""
import pandas as pd
import pytest

from backend.data.normalizers.price_limits import compute_limits, limit_prices, tick_size
from backend.data.validators import checks

NOW = "2026-11-27T00:00:00+00:00"


@pytest.mark.parametrize("p,t", [(9.99, "0.01"), (10, "0.05"), (49.95, "0.05"), (50, "0.1"), (99.9, "0.1"),
                                 (100, "0.5"), (499.5, "0.5"), (500, "1"), (999, "1"), (1000, "5"), (2385, "5")])
def test_tick_size(p, t):
    assert str(tick_size(p)) == t


def test_limits_round_inward_to_tick():
    assert limit_prices(593.0, "2024-01-03")[:2] == (652.0, 534.0)       # 652.3 -> 652; 533.7 -> 534
    assert limit_prices(45.6, "2024-01-03")[:2] == (50.1, 41.05)         # 50.16 at tick 0.1; 41.04 -> 41.05
    assert limit_prices(100.0, "2015-05-29")[:2] == (107.0, 93.0)        # 7% before 2015-06-01


def test_real_tpex_official_limits():
    """REAL: TPEx 2016-01-04 close 133.00 for 1256 -> official next-day limits 146.00 / 120.00 (Stage 2.1)."""
    up, down, _ = limit_prices(133.0, "2016-01-05")
    assert (up, down) == (146.0, 120.0)


def test_compute_limits_uses_official_event_values_and_last_published_close():
    raw = pd.DataFrame({"stock_id": "9999", "date": ["2024-06-11", "2024-06-12", "2024-06-13", "2024-06-14"],
                        "close": [100.0, None, 96.0, 97.0], "has_price": [1, 0, 1, 1]})
    act = pd.DataFrame([{"ex_date": "2024-06-13", "reference_price": 96.0, "limit_up": 105.5, "limit_down": 86.5}])
    lim = compute_limits(raw, act, NOW).set_index("date")
    assert lim.loc["2024-06-11", "has_limit"] == 0                        # no earlier close
    assert lim.loc["2024-06-12", "reference_price"] == 100.0
    assert lim.loc["2024-06-13", "basis"] == "official_event" and lim.loc["2024-06-13", "limit_up"] == 105.5
    assert lim.loc["2024-06-14", "reference_price"] == 96.0 and lim.loc["2024-06-14", "limit_up"] == 105.5


def test_check_price_outside_limits():
    raw = pd.DataFrame({"stock_id": "9999", "date": ["2024-06-12"], "high": [111.0], "low": [99.0],
                        "close": [110.0], "has_price": [1]})
    lim = pd.DataFrame({"stock_id": "9999", "date": ["2024-06-12"], "reference_price": [100.0],
                        "limit_up": [110.0], "limit_down": [90.0], "has_limit": [1], "basis": ["computed"]})
    assert checks.check_price_limits(raw, lim).category.tolist() == ["price_outside_limits"]
