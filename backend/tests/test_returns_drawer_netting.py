"""
IMS 2.0 - Day-End drawer nets money refunds from the returns collection
=======================================================================
THE MONEY LEAK: a money refund (returns.py create_return, RETURN branch) writes
ONLY a returns-collection doc -- POS never writes a negative payments[] row --
so the drawer expected (finance._cash_sales_for_window -> compute_expected_cash)
and the F23 blind Z-Read (tender_reconciliation.reconcile_window ->
eod_tally.compute_expected) never saw refunds: staff showed a false SHORT every
time cash was refunded.

THE FIX (reader-side only; returns.py / orders.py / POS untouched): both readers
net COMPLETED, non-historical returns docs, windowed on the RETURN's OWN
created_at (= the refund date -- the original sale's day-window would book the
refund into the WRONG day). CASH refunds reduce the expected drawer; UPI/CARD
refunds reduce those tenders' nets; a COLLECT-direction EXCHANGE (difference
collected at the till, recorded only as collect_amount on the return doc,
verified never payments-visible) is symmetric cash-IN.

Fake-coll pattern per test_transfer_cancel_allowlist.py; the faithful query
matcher mirrors test_e5_tender_recon.py so window/store filters are exercised
for real (string created_at vs datetime bound raises TypeError -> no match --
the same type-bracketing real Mongo applies).

No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers.finance import _cash_sales_for_window  # noqa: E402
from api.services import eod_tally  # noqa: E402
from api.services import tender_reconciliation as trec  # noqa: E402


# ============================================================================
# Faithful in-memory fake Mongo (query matching mirrors test_e5_tender_recon)
# ============================================================================


def _cmp_op(actual: Any, op: str, expected: Any) -> bool:
    if actual is None and op in ("$gt", "$gte", "$lt", "$lte"):
        return False
    try:
        if op == "$gt":
            return actual > expected
        if op == "$gte":
            return actual >= expected
        if op == "$lt":
            return actual < expected
        if op == "$lte":
            return actual <= expected
        if op == "$ne":
            return actual != expected
        if op == "$in":
            return actual in expected
    except TypeError:
        # str-vs-datetime comparison: real Mongo type-brackets (no match).
        return False
    return False


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for k, v in query.items():
        if k == "$or":
            if not any(_matches(doc, sub) for sub in v):
                return False
            continue
        actual = doc.get(k)
        if isinstance(v, dict) and any(str(kk).startswith("$") for kk in v):
            for op, expected in v.items():
                if not _cmp_op(actual, op, expected):
                    return False
            continue
        if actual != v:
            return False
    return True


class FakeCollection:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    def insert(self, doc: Dict[str, Any]) -> None:
        self.docs.append(dict(doc))

    def find(self, query=None, projection=None):
        return iter(
            [dict(d) for d in self.docs if _matches(d, query or {})]
        )

    def find_one(self, query=None, projection=None):
        for d in self.docs:
            if _matches(d, query or {}):
                return dict(d)
        return None


class FakeDB:
    def __init__(self):
        self._collections: Dict[str, FakeCollection] = {}

    def get_collection(self, name: str) -> FakeCollection:
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]


@pytest.fixture()
def db() -> FakeDB:
    return FakeDB()


@pytest.fixture(autouse=True)
def _pin_entity_resolver(monkeypatch):
    """reconcile_window resolves the tender map via the store->entity resolver;
    pin it so no test touches the real (absent) DB through E2's cache."""
    monkeypatch.setattr(
        "api.services.policy_engine._resolve_entity_id", lambda store_id: None
    )


# Yesterday / today windows (the key wrong-day scenario).
Y_START, Y_END = "2026-08-08T00:00:00", "2026-08-08T23:59:59"
T_START, T_END = "2026-08-09T00:00:00", "2026-08-09T23:59:59"


def _order(store: str, created: Any, method: str, amount: float) -> Dict[str, Any]:
    return {
        "store_id": store,
        "created_at": created,
        "payments": [{"method": method, "amount": amount}],
    }


def _return_doc(
    store: str,
    created_iso: str,
    rtype: str = "RETURN",
    refund_method: Optional[str] = "CASH",
    refund_amount: Optional[float] = None,
    credit_amount: Optional[float] = None,
    collect_amount: Optional[float] = None,
    settlement: Optional[Dict[str, Any]] = None,
    status: str = "COMPLETED",
    historical: Optional[bool] = None,
) -> Dict[str, Any]:
    """Mirrors the doc create_return persists (created_at = ISO STRING)."""
    doc: Dict[str, Any] = {
        "return_id": f"RET-{created_iso}",
        "store_id": store,
        "return_type": rtype,
        "refund_method": refund_method,
        "refund_amount": refund_amount,
        "credit_amount": credit_amount,
        "collect_amount": collect_amount,
        "settlement": settlement,
        "status": status,
        "created_at": created_iso,
    }
    if historical is not None:
        doc["historical"] = historical
    return doc


# ============================================================================
# (a) THE KEY SCENARIO: sale yesterday, CASH refund today -> the refund lands
#     in TODAY's window (the refund date), not yesterday's (the sale date).
# ============================================================================


def test_cash_refund_lands_in_refund_day_window_not_sale_day(db):
    # Sale yesterday (BSON-Date created_at), CASH 5000.
    db.get_collection("orders").insert(
        _order("S1", datetime(2026, 8, 8, 11, 0, 0), "CASH", 5000.0)
    )
    # Refund TODAY -- returns doc created_at is an ISO STRING.
    db.get_collection("returns").insert(
        _return_doc("S1", "2026-08-09T14:30:00.123456", refund_amount=1200.0)
    )

    today_sales, today_refunds = _cash_sales_for_window(db, "S1", T_START, T_END)
    assert today_refunds == 1200.0, "today's drawer must see today's refund"
    assert today_sales == 0.0

    y_sales, y_refunds = _cash_sales_for_window(db, "S1", Y_START, Y_END)
    assert y_sales == 5000.0
    assert y_refunds == 0.0, "the sale day must NOT absorb the later refund"


def test_expected_cash_close_math_nets_the_refund(db):
    """End-to-end drawer math: opening + sales - refunds via the pure service."""
    from api.services.cash_register import compute_expected_cash

    db.get_collection("orders").insert(
        _order("S1", datetime(2026, 8, 9, 10, 0, 0), "CASH", 3000.0)
    )
    db.get_collection("returns").insert(
        _return_doc("S1", "2026-08-09T15:00:00", refund_amount=500.0)
    )
    sales, refunds = _cash_sales_for_window(db, "S1", T_START, T_END)
    expected = compute_expected_cash(1000.0, sales, refunds, 0.0, 0.0)
    assert expected == 1000.0 + 3000.0 - 500.0  # no false SHORT


# ============================================================================
# (b) CREDIT_NOTE and EXCHANGE-credit do NOT reduce cash (store credit only).
# ============================================================================


def test_credit_note_and_exchange_credit_do_not_touch_cash(db):
    db.get_collection("returns").insert(
        _return_doc(
            "S1", "2026-08-09T10:00:00", rtype="CREDIT_NOTE",
            refund_method="STORE_CREDIT", credit_amount=900.0,
        )
    )
    db.get_collection("returns").insert(
        _return_doc(
            "S1", "2026-08-09T11:00:00", rtype="EXCHANGE",
            refund_method="CASH", credit_amount=400.0,
            settlement={"direction": "REFUND", "difference": 400.0},
        )
    )
    sales, refunds = _cash_sales_for_window(db, "S1", T_START, T_END)
    assert (sales, refunds) == (0.0, 0.0)

    recon = trec.reconcile_window(db, "S1", T_START, T_END, tender_map={})
    assert recon["by_mode"] == {}
    assert recon["total_net"] == 0.0


# ============================================================================
# (c)+(g) UPI refund reduces the UPI net, not CASH (per-mode netting).
# ============================================================================


def test_upi_refund_reduces_upi_net_not_cash(db):
    db.get_collection("orders").insert(
        _order("S1", datetime(2026, 8, 9, 10, 0, 0), "CASH", 1000.0)
    )
    db.get_collection("orders").insert(
        _order("S1", datetime(2026, 8, 9, 10, 5, 0), "UPI", 2000.0)
    )
    db.get_collection("returns").insert(
        _return_doc(
            "S1", "2026-08-09T12:00:00", refund_method="UPI", refund_amount=500.0
        )
    )
    recon = trec.reconcile_window(db, "S1", T_START, T_END, tender_map={})
    assert recon["by_mode"]["UPI"]["refunded"] == 500.0
    assert recon["by_mode"]["UPI"]["net"] == 1500.0
    assert recon["by_mode"]["CASH"]["net"] == 1000.0  # untouched
    assert recon["total_net"] == 2500.0

    # The CASH drawer reader must not see the UPI refund either.
    sales, refunds = _cash_sales_for_window(db, "S1", T_START, T_END)
    assert (sales, refunds) == (1000.0, 0.0)


def test_refund_method_alias_normalizes_gpay_to_upi(db):
    db.get_collection("returns").insert(
        _return_doc(
            "S1", "2026-08-09T12:00:00", refund_method="gpay", refund_amount=250.0
        )
    )
    recon = trec.reconcile_window(db, "S1", T_START, T_END, tender_map={})
    assert recon["by_mode"]["UPI"]["refunded"] == 250.0
    assert recon["by_mode"]["UPI"]["net"] == -250.0


def test_source_refund_method_books_under_unknown_never_cash(db):
    # _order_payment_method defaults to "SOURCE" when the original tender is
    # unresolvable; that canonicalizes to UNKNOWN and must NEVER fold into CASH.
    db.get_collection("returns").insert(
        _return_doc(
            "S1", "2026-08-09T12:00:00", refund_method="SOURCE",
            refund_amount=300.0,
        )
    )
    sales, refunds = _cash_sales_for_window(db, "S1", T_START, T_END)
    assert (sales, refunds) == (0.0, 0.0)
    recon = trec.reconcile_window(db, "S1", T_START, T_END, tender_map={})
    assert recon["by_mode"]["UNKNOWN"]["refunded"] == 300.0
    assert "CASH" not in recon["by_mode"]


# ============================================================================
# (d) historical:True (Shopify-era imports, settled outside the drawer).
# ============================================================================


def test_historical_returns_are_excluded_everywhere(db):
    db.get_collection("returns").insert(
        _return_doc(
            "S1", "2026-08-09T12:00:00", refund_amount=999.0, historical=True
        )
    )
    assert _cash_sales_for_window(db, "S1", T_START, T_END) == (0.0, 0.0)
    recon = trec.reconcile_window(db, "S1", T_START, T_END, tender_map={})
    assert recon["by_mode"] == {}


def test_non_completed_returns_are_excluded(db):
    db.get_collection("returns").insert(
        _return_doc("S1", "2026-08-09T12:00:00", refund_amount=999.0,
                    status="PENDING")
    )
    assert _cash_sales_for_window(db, "S1", T_START, T_END) == (0.0, 0.0)
    recon = trec.reconcile_window(db, "S1", T_START, T_END, tender_map={})
    assert recon["by_mode"] == {}


# ============================================================================
# (e) Store scoping: another store's refund never moves this drawer.
# ============================================================================


def test_store_scoping_excludes_other_stores_refunds(db):
    db.get_collection("returns").insert(
        _return_doc("S2", "2026-08-09T12:00:00", refund_amount=700.0)
    )
    assert _cash_sales_for_window(db, "S1", T_START, T_END) == (0.0, 0.0)
    recon = trec.reconcile_window(db, "S1", T_START, T_END, tender_map={})
    assert recon["by_mode"] == {}
    # ... while S2's own drawer sees it.
    s2_sales, s2_refunds = _cash_sales_for_window(db, "S2", T_START, T_END)
    assert s2_refunds == 700.0


# ============================================================================
# (f) String-date window matching (returns created_at is an ISO string).
# ============================================================================


def test_string_created_at_matches_string_window_bounds(db):
    inside = _return_doc("S1", "2026-08-09T00:00:01", refund_amount=100.0)
    before = _return_doc("S1", "2026-08-08T23:59:58", refund_amount=100.0)
    after = _return_doc("S1", "2026-08-10T00:00:01", refund_amount=100.0)
    for d in (inside, before, after):
        db.get_collection("returns").insert(d)
    sales, refunds = _cash_sales_for_window(db, "S1", T_START, T_END)
    assert refunds == 100.0  # only the inside doc


# ============================================================================
# (h) EXCHANGE collect-in: the till COLLECTS the price difference (recorded
#     only as collect_amount on the return doc -- verified never pushed into
#     orders.payments[]) -> symmetric cash-IN.
# ============================================================================


def test_exchange_cash_collect_adds_cash_in(db):
    db.get_collection("returns").insert(
        _return_doc(
            "S1", "2026-08-09T13:00:00", rtype="EXCHANGE",
            refund_method="CASH", collect_amount=800.0,
            settlement={"direction": "COLLECT", "difference": 800.0},
        )
    )
    sales, refunds = _cash_sales_for_window(db, "S1", T_START, T_END)
    assert sales == 800.0
    assert refunds == 0.0

    recon = trec.reconcile_window(db, "S1", T_START, T_END, tender_map={})
    assert recon["by_mode"]["CASH"]["collected"] == 800.0
    assert recon["by_mode"]["CASH"]["net"] == 800.0


def test_exchange_upi_collect_hits_upi_not_the_drawer(db):
    db.get_collection("returns").insert(
        _return_doc(
            "S1", "2026-08-09T13:00:00", rtype="EXCHANGE",
            refund_method="UPI", collect_amount=650.0,
            settlement={"direction": "COLLECT", "difference": 650.0},
        )
    )
    sales, refunds = _cash_sales_for_window(db, "S1", T_START, T_END)
    assert (sales, refunds) == (0.0, 0.0)  # drawer untouched
    recon = trec.reconcile_window(db, "S1", T_START, T_END, tender_map={})
    assert recon["by_mode"]["UPI"]["collected"] == 650.0
    assert recon["by_mode"]["UPI"]["net"] == 650.0


def test_exchange_even_direction_moves_nothing(db):
    db.get_collection("returns").insert(
        _return_doc(
            "S1", "2026-08-09T13:00:00", rtype="EXCHANGE",
            refund_method="CASH", collect_amount=None,
            settlement={"direction": "EVEN", "difference": 0.0},
        )
    )
    assert _cash_sales_for_window(db, "S1", T_START, T_END) == (0.0, 0.0)
    recon = trec.reconcile_window(db, "S1", T_START, T_END, tender_map={})
    assert recon["by_mode"] == {}


# ============================================================================
# F23 blind Z-Read: eod_tally.compute_expected consumes reconcile_window's
# CASH net -> the expected drawer figure now nets the refund.
# ============================================================================


def test_zread_expected_cash_nets_the_refund(db):
    db.get_collection("orders").insert(
        _order("S1", datetime(2026, 8, 9, 10, 0, 0), "CASH", 1000.0)
    )
    db.get_collection("returns").insert(
        _return_doc("S1", "2026-08-09T15:00:00", refund_amount=300.0)
    )
    exp = eod_tally.compute_expected(db, "S1", T_START, T_END, 10000, 0)
    assert exp["cash_sales_paisa"] == 70000  # (1000 - 300) rupees in paisa
    assert exp["expected_cash_paisa"] == 10000 + 70000
    # The by-mode payload surfaces the refund component for the UI.
    assert exp["by_mode"]["CASH"]["refunded"] == 300.0


# ============================================================================
# Fail-soft + count semantics.
# ============================================================================


def test_returns_scan_failure_never_zeroes_the_sales_figure(db):
    db.get_collection("orders").insert(
        _order("S1", datetime(2026, 8, 9, 10, 0, 0), "CASH", 2000.0)
    )

    class _Boom:
        def find(self, *a, **k):
            raise RuntimeError("returns collection unavailable")

    db._collections["returns"] = _Boom()  # type: ignore[assignment]
    sales, refunds = _cash_sales_for_window(db, "S1", T_START, T_END)
    assert sales == 2000.0  # orders figure survives
    assert refunds == 0.0
    recon = trec.reconcile_window(db, "S1", T_START, T_END, tender_map={})
    assert recon["by_mode"]["CASH"]["net"] == 2000.0


def test_count_stays_payment_row_count(db):
    db.get_collection("orders").insert(
        _order("S1", datetime(2026, 8, 9, 10, 0, 0), "CASH", 1000.0)
    )
    db.get_collection("returns").insert(
        _return_doc("S1", "2026-08-09T15:00:00", refund_amount=100.0)
    )
    recon = trec.reconcile_window(db, "S1", T_START, T_END, tender_map={})
    assert recon["by_mode"]["CASH"]["count"] == 1  # one payments[] row only
    assert recon["by_mode"]["CASH"]["net"] == 900.0
