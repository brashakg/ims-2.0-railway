"""BUG-031: _cash_sales_for_window queried created_at (a BSON Date) with an
ISO-STRING window -> Mongo type-bracketing never matched -> cash_sales always 0
-> false drawer variance. The window must include a datetime bound."""
import os
import sys
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers.finance import _cash_sales_for_window  # noqa: E402


class _CaptureColl:
    def __init__(self, orders):
        self.orders = orders
        self.last_match = None

    def find(self, match, proj=None):
        self.last_match = match
        return iter(self.orders)


class _DB:
    def __init__(self, orders):
        self._c = _CaptureColl(orders)

    def get_collection(self, name):
        return self._c


def test_cash_sales_sums_cash_and_window_is_datetime():
    orders = [
        {"payments": [{"method": "CASH", "amount": 500.0}, {"method": "UPI", "amount": 200.0}]},
        {"payments": [{"method": "CASH", "amount": -100.0}]},  # refund
        {"payments": [{"mode": "cash", "amount": 250.0}]},     # legacy `mode` alias
    ]
    db = _DB(orders)
    sales, refunds = _cash_sales_for_window(
        db, "S1", "2026-06-06T00:00:00", "2026-06-06T23:59:59"
    )
    assert sales == 750.0   # 500 + 250
    assert refunds == 100.0
    # The BUG-031 fix: the created_at window must include a real datetime bound
    # (not only an ISO string), so it matches BSON-Date created_at.
    ors = db._c.last_match["$or"]
    assert any(
        isinstance((c.get("created_at") or {}).get("$gte"), datetime) for c in ors
    ), f"expected a datetime $gte window, got {ors}"
    # ... and still a string window for any legacy ISO-string created_at.
    assert any(
        isinstance((c.get("created_at") or {}).get("$gte"), str) for c in ors
    )


def test_cash_sales_db_none_safe():
    assert _cash_sales_for_window(None, "S1", "2026-06-06", None) == (0.0, 0.0)
