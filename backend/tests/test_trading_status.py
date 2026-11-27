"""Stage 2 step 7: inferred trading status. MOCK DATA."""
import pandas as pd

from backend.data.normalizers.trading_status import infer_status

DAYS = ["2025-07-28", "2025-07-29", "2025-07-30", "2025-07-31", "2025-08-01", "2025-08-04", "2025-08-05"]
NOW = "2026-11-27T00:00:00+00:00"


def test_periods_from_no_price_and_missing_rows():
    raw = pd.DataFrame({"stock_id": "9999", "date": ["2025-07-28", "2025-07-29", "2025-07-30", "2025-08-04"],
                        "has_price": [1, 0, 1, 1]})       # 07-31 and 08-01 have no row at all
    st, sp = infer_status(raw, DAYS, None, NOW)
    assert st.set_index("date")["detail"].to_dict() == {"2025-07-29": "no_published_price",
                                                         "2025-07-31": "missing_row", "2025-08-01": "missing_row"}
    assert sp[["start_date", "end_date", "trading_days"]].values.tolist() == [
        ["2025-07-29", "2025-07-29", 1], ["2025-07-31", "2025-08-01", 2]]
    assert set(sp["basis"]) == {"inferred"}


def test_days_after_last_price_are_not_suspension_and_delisting_recorded():
    raw = pd.DataFrame({"stock_id": "9999", "date": ["2025-07-28", "2025-07-29"], "has_price": [1, 1]})
    st, sp = infer_status(raw, DAYS, "2025-08-05", NOW)
    assert sp.empty
    assert st[["date", "status", "delisting_flag"]].values.tolist() == [["2025-08-05", "delisted", 1]]
