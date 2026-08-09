"""
IMS 2.0 - ENDPOINT-LEVEL tests for refund-tender capture (money panel round 3)
=============================================================================
Round 2's writer tests called the private helpers in isolation, so NOTHING drove
``create_return``. That is exactly why two P0s shipped green:

  * P0-1 the till prefilled a GST-grossed-up amount (Rs 6,962 for a Rs 5,900
    inclusive-priced line) that the server's paise-exact balance check then
    400'd -- every ordinary counter sale, un-returnable;
  * P0-3 the new tender validators raised 400 AFTER the atomic returnable-qty
    claim and the loyalty reversal, so every rejected split permanently burned
    the customer's returnable quantity and clawed back their points with no
    return doc written ("returnable quantity 0" on retry).

These tests drive the REAL endpoint over a realistic INCLUSIVE-priced order
(taxable 5000.00 + tax 900.00 = Rs 5,900.00 billed at 18%) through FastAPI's
TestClient, with a fake orders collection that implements the SAME atomic
positional claim production uses -- so returned_qty and loyalty are observable
before and after a rejection.

No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from api.routers import returns as returns_router  # noqa: E402
from api.routers.finance import _cash_sales_for_window  # noqa: E402
from tests.test_returns_gst_refund import (  # noqa: E402
    _FakeColl,
    _FakeCustomerRepo,
    _staff_token,
)

_HDR = {"Authorization": f"Bearer {_staff_token(['ADMIN'])}"}
_STORE = "BV-PUN-01"

# The realistic INCLUSIVE-priced order the chair used: one line billed
# Rs 5,900.00 all-in at 18% GST (taxable 5000.00 + tax 900.00).
_TAXABLE = 5000.00
_TAX = 900.00
_BILLED_GROSS = 5900.00


def _inclusive_order(payments, qty=1):
    return {
        "order_id": "ORD-INC-1",
        "order_number": "ORD-INC-1-2026",
        "customer_id": "CUST-1",
        "customer_name": "Asha",
        "store_id": _STORE,
        "status": "DELIVERED",
        "amount_paid": sum(p["amount"] for p in payments),
        "payments": payments,
        "items": [
            {
                "item_id": "li1",
                "product_id": "PRD-1",
                "product_name": "Ray-Ban Sunglass",
                "sku": "RB-1",
                "quantity": qty,
                "unit_price": _TAXABLE / qty,
                "gst_rate": 18.0,
                "taxable_value": _TAXABLE,
                "tax_amount": _TAX,
                "item_total": _BILLED_GROSS,
            }
        ],
    }


class _QueryColl(_FakeColl):
    """`returns` collection fake that understands the operators the Day-End
    drawer reader actually uses ($or / $ne / $gte / $lte), so the endpoint tests
    can drive the REAL reader over the REAL persisted doc."""

    @staticmethod
    def _match(doc, query):
        for k, v in (query or {}).items():
            if k == "$or":
                if not any(_QueryColl._match(doc, sub) for sub in v):
                    return False
                continue
            actual = doc.get(k)
            if isinstance(v, dict) and any(str(kk).startswith("$") for kk in v):
                for op, exp in v.items():
                    try:
                        if op == "$ne" and actual == exp:
                            return False
                        if op == "$gte" and not (actual is not None and actual >= exp):
                            return False
                        if op == "$lte" and not (actual is not None and actual <= exp):
                            return False
                    except TypeError:
                        return False  # Mongo type-bracketing
                continue
            if actual != v:
                return False
        return True

    def find(self, query=None, projection=None):
        return iter([dict(d) for d in self.docs if self._match(d, query or {})])

    def find_one(self, query=None, projection=None):
        for d in self.docs:
            if self._match(d, query or {}):
                out = dict(d)
                out.pop("_id", None)
                return out
        return None


class _ClaimingOrdersColl:
    """Fake `orders` collection implementing the SAME atomic positional claim as
    production (`items.$.returned_qty` guarded by an $elemMatch cap), so a test
    can assert the returnable quantity is UNTOUCHED after a rejected return."""

    def __init__(self, order):
        self.order = order

    # -- reads used by _resolve_order / repo bridge --------------------------
    def find_one(self, query=None, projection=None):
        if (query or {}).get("order_id") == self.order.get("order_id"):
            return dict(self.order)
        return None

    def find(self, query=None, projection=None):
        """The Day-End drawer reader scans orders for CASH payments before it
        scans returns. Match on store_id only (the tests use a wide window)."""
        q = query or {}
        if q.get("store_id") and q["store_id"] != self.order.get("store_id"):
            return iter([])
        return iter([dict(self.order)])

    def find_one_and_update(self, match, update, return_document=None, **_kw):
        if match.get("order_id") != self.order.get("order_id"):
            return None
        elem = (match.get("items") or {}).get("$elemMatch") or {}
        inc = (update.get("$inc") or {}).get("items.$.returned_qty", 0)
        for line in self.order["items"]:
            if elem.get("item_id") and line.get("item_id") != elem["item_id"]:
                continue
            if elem.get("product_id") and line.get("product_id") != elem["product_id"]:
                continue
            returned = float(line.get("returned_qty") or 0)
            # Mirror the production guard: claimable only when the units already
            # returned leave room ($or of <=cap / absent / null), else no-match.
            ors = elem.get("$or")
            if ors is not None:
                cap = None
                for clause in ors:
                    rq = clause.get("returned_qty")
                    if isinstance(rq, dict) and "$lte" in rq:
                        cap = rq["$lte"]
                if cap is not None and returned > cap:
                    return None
            elif isinstance(elem.get("returned_qty"), dict):
                return None  # the impossible-match branch (cap < 0)
            line["returned_qty"] = returned + inc
            return dict(self.order)
        return None


class _RepoWithColl:
    def __init__(self, coll):
        self.collection = coll
        self._order = coll.order

    def find_by_id(self, oid):
        return self._order if self._order.get("order_id") == oid else None

    def find_by_order_number(self, num):
        return self._order if self._order.get("order_number") == num else None


# Catalogued replacement product. The EXCHANGE price is resolved from HERE, not
# from the client payload -- a typed price is a cash-drawer input via the
# COLLECT, and a fat-finger manufactured a Rs 53,100 phantom shortage.
_CATALOG_PRICE = 7000.00


class _FakeProductRepo:
    def __init__(self, price=_CATALOG_PRICE):
        self.price = price

    def find_by_id(self, pid):
        if pid == "PRD-2":
            return {"product_id": "PRD-2", "name": "Replacement Frame",
                    "sku": "RB-2", "offer_price": self.price}
        return None

    def find_by_sku(self, sku):
        if sku == "RB-2":
            return self.find_by_id("PRD-2")
        return None


@pytest.fixture()
def ctx(monkeypatch):
    """Wire the REAL returns router over fakes; returns a builder so each test
    picks the source order's tender composition."""

    def _build(payments, qty=1, catalog_price=_CATALOG_PRICE):
        order = _inclusive_order(payments, qty=qty)
        orders_coll = _ClaimingOrdersColl(order)
        repo = _RepoWithColl(orders_coll)
        returns_coll = _QueryColl()
        ledger_coll = _FakeColl()

        class _FakeDB:
            is_connected = True

            def __init__(self):
                self.db = self

            def get_collection(self, name):
                return {
                    "returns": returns_coll,
                    "credit_note_ledger": ledger_coll,
                    "orders": orders_coll,
                }.get(name, _FakeColl())

        fake_db = _FakeDB()
        loyalty_calls = []

        def _fake_reverse(return_id, order_id, customer_id):
            loyalty_calls.append((return_id, order_id, customer_id))
            return {"ok": True, "earned_clawed": 10, "redeemed_restored": 0}

        monkeypatch.setattr(returns_router, "get_order_repository", lambda: repo)
        monkeypatch.setattr(
            returns_router, "get_customer_repository", lambda: _FakeCustomerRepo()
        )
        monkeypatch.setattr(
            returns_router, "get_product_repository",
            lambda: _FakeProductRepo(catalog_price),
        )
        monkeypatch.setattr(returns_router, "get_stock_repository", lambda: None)
        monkeypatch.setattr("api.dependencies.get_db", lambda: fake_db, raising=False)
        monkeypatch.setattr(
            "api.dependencies.get_audit_repository", lambda: None, raising=False
        )
        monkeypatch.setattr(
            "api.routers.loyalty.reverse_for_return", _fake_reverse, raising=False
        )

        app = FastAPI()
        app.include_router(returns_router.router, prefix="/api/v1/returns")
        return {
            "client": TestClient(app),
            "order": order,
            "returns": returns_coll,
            "db": fake_db,
            "loyalty": loyalty_calls,
        }

    return _build


def _payload(**over):
    body = {
        "order_id": "ORD-INC-1",
        "store_id": _STORE,
        "return_type": "RETURN",
        "items": [
            {
                "order_item_id": "li1",
                "product_id": "PRD-1",
                "product_name": "Ray-Ban Sunglass",
                "sku": "RB-1",
                "return_qty": 1,
                "unit_price": _TAXABLE,
                "gst_rate": 18.0,
                "condition": "GOOD",
            }
        ],
    }
    body.update(over)
    return body


# ===========================================================================
# P0-1: the amount the till must prefill IS the server's net_refund.
# ===========================================================================


def test_quote_returns_the_billed_gross_not_a_grossed_up_price(ctx):
    c = ctx([{"method": "CASH", "amount": _BILLED_GROSS}])
    r = c["client"].post("/api/v1/returns/quote", json=_payload(), headers=_HDR)
    assert r.status_code == 200, r.text
    q = r.json()
    # The customer paid Rs 5,900.00 all-in; that is the refund.
    assert q["gross_refund"] == _BILLED_GROSS
    assert q["net_refund"] == _BILLED_GROSS
    # The OLD frontend formula (unit_price * (1 + gst/100)) produced this --
    # a Rs 1,062 phantom that made the tender split un-balanceable.
    assert round(_BILLED_GROSS * 1.18, 2) == 6962.00
    assert q["net_refund"] != 6962.00


def test_fe_billed_unit_gross_formula_equals_server_net_refund(ctx):
    """The FE derives its display figure as (taxable_value + tax_amount)/qty --
    the SAME formula the server uses. They must agree to the paisa."""
    c = ctx([{"method": "CASH", "amount": _BILLED_GROSS}])
    line = c["order"]["items"][0]
    fe_unit_gross = round(
        (line["taxable_value"] + line["tax_amount"]) / line["quantity"], 2
    )
    fe_total = round(fe_unit_gross * 1, 2)
    q = c["client"].post("/api/v1/returns/quote", json=_payload(), headers=_HDR).json()
    assert fe_total == q["net_refund"] == _BILLED_GROSS


def test_quote_echoes_captured_tenders_for_the_picker(ctx):
    c = ctx([
        {"method": "UPI", "amount": 2000.00},
        {"method": "CASH", "amount": 3900.00},
    ])
    q = c["client"].post("/api/v1/returns/quote", json=_payload(), headers=_HDR).json()
    assert q["captured_tenders"] == {"UPI": 2000.00, "CASH": 3900.00}
    assert q["refundable_by_tender"] == {"UPI": 2000.00, "CASH": 3900.00}
    assert q["tenders_unverifiable"] is False


# ===========================================================================
# (a) A normal RETURN completes and the drawer nets it correctly.
# ===========================================================================


def test_cash_return_completes_and_nets_the_drawer(ctx):
    c = ctx([{"method": "CASH", "amount": _BILLED_GROSS}])
    r = c["client"].post(
        "/api/v1/returns",
        json=_payload(refund_tenders=[{"method": "CASH", "amount": _BILLED_GROSS}]),
        headers=_HDR,
    )
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body["net_refund"] == _BILLED_GROSS
    assert body["drawer_auto_netted"] is True

    doc = c["returns"].docs[0]
    assert doc["refund_tenders"] == [{"method": "CASH", "amount": _BILLED_GROSS}]
    assert doc["drawer_auto_netted"] is True

    # ... and the Day-End drawer reader SEES it (the whole point of the PR).
    sales, refunds = _cash_sales_for_window(
        c["db"], _STORE, "2000-01-01T00:00:00", "2999-01-01T00:00:00"
    )
    assert refunds == _BILLED_GROSS


def test_split_tender_return_nets_only_the_cash_leg(ctx):
    """The round-1 regression, end to end: a CASH+CARD sale refunded to both
    tenders must cut ONLY the cash leg from the drawer."""
    c = ctx([
        {"method": "CASH", "amount": 2000.00},
        {"method": "CARD", "amount": 3900.00},
    ])
    r = c["client"].post(
        "/api/v1/returns",
        json=_payload(
            refund_tenders=[
                {"method": "CASH", "amount": 2000.00},
                {"method": "CARD", "amount": 3900.00},
            ]
        ),
        headers=_HDR,
    )
    assert r.status_code in (200, 201), r.text
    sales, refunds = _cash_sales_for_window(
        c["db"], _STORE, "2000-01-01T00:00:00", "2999-01-01T00:00:00"
    )
    assert refunds == 2000.00  # NOT the whole Rs 5,900


# ===========================================================================
# (c) A FORGED tender is refused by the BACKEND (a UI rule is not a control).
# ===========================================================================


def test_cash_refund_on_a_card_only_sale_is_refused(ctx):
    """The cash-skim path: POST a CASH leg against a 100%-CARD sale."""
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}])
    r = c["client"].post(
        "/api/v1/returns",
        json=_payload(refund_tenders=[{"method": "CASH", "amount": _BILLED_GROSS}]),
        headers=_HDR,
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["reason"] == "TENDER_MISMATCH"
    assert c["returns"].docs == []  # nothing recorded
    # ... and the drawer was never moved.
    _, refunds = _cash_sales_for_window(
        c["db"], _STORE, "2000-01-01T00:00:00", "2999-01-01T00:00:00"
    )
    assert refunds == 0.0


def test_cash_leg_exceeding_what_cash_collected_is_refused(ctx):
    c = ctx([
        {"method": "CASH", "amount": 1000.00},
        {"method": "CARD", "amount": 4900.00},
    ])
    r = c["client"].post(
        "/api/v1/returns",
        json=_payload(
            refund_tenders=[
                {"method": "CASH", "amount": 5000.00},
                {"method": "CARD", "amount": 900.00},
            ]
        ),
        headers=_HDR,
    )
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["reason"] == "TENDER_MISMATCH"
    assert detail["captured_on_tender"] == 1000.00


def test_order_without_captured_payments_records_but_never_auto_nets(ctx):
    """Unverifiable (legacy / imported) order: the refund is NOT blocked, but it
    is downgraded to UNKNOWN so no drawer figure moves on an unverified claim."""
    c = ctx([])
    c["order"]["amount_paid"] = _BILLED_GROSS  # legacy: paid, but no payments[]
    r = c["client"].post(
        "/api/v1/returns",
        json=_payload(refund_tenders=[{"method": "CASH", "amount": _BILLED_GROSS}]),
        headers=_HDR,
    )
    assert r.status_code in (200, 201), r.text
    doc = c["returns"].docs[0]
    assert doc["refund_tenders"] is None
    assert doc["drawer_auto_netted"] is False
    _, refunds = _cash_sales_for_window(
        c["db"], _STORE, "2000-01-01T00:00:00", "2999-01-01T00:00:00"
    )
    assert refunds == 0.0


# ===========================================================================
# (b) P0-3 REGRESSION: a rejected return must leave returned_qty AND loyalty
#     untouched, and must stay retryable.
# ===========================================================================


def _returned_qty(order):
    return float(order["items"][0].get("returned_qty") or 0)


def test_unbalanced_split_leaves_returnable_qty_and_loyalty_untouched(ctx):
    c = ctx([{"method": "CASH", "amount": _BILLED_GROSS}])
    before = _returned_qty(c["order"])
    assert before == 0.0

    r = c["client"].post(
        "/api/v1/returns",
        # Split does not sum to the net refund -> must reject.
        json=_payload(refund_tenders=[{"method": "CASH", "amount": 1000.00}]),
        headers=_HDR,
    )
    assert r.status_code == 400, r.text

    # THE REGRESSION ASSERTIONS: nothing was burned.
    assert _returned_qty(c["order"]) == before == 0.0
    assert c["loyalty"] == []          # no points clawed back
    assert c["returns"].docs == []     # no return doc written


def test_rejected_forged_tender_leaves_returnable_qty_untouched(ctx):
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}])
    r = c["client"].post(
        "/api/v1/returns",
        json=_payload(refund_tenders=[{"method": "CASH", "amount": _BILLED_GROSS}]),
        headers=_HDR,
    )
    assert r.status_code == 422
    assert _returned_qty(c["order"]) == 0.0
    assert c["loyalty"] == []


def test_retry_after_a_rejection_succeeds(ctx):
    """The user-visible consequence of P0-3: after a rejected split the cashier
    could never retry ("returnable quantity 0"). Fixing the ordering restores
    the retry."""
    c = ctx([{"method": "CASH", "amount": _BILLED_GROSS}])
    bad = c["client"].post(
        "/api/v1/returns",
        json=_payload(refund_tenders=[{"method": "CASH", "amount": 1.00}]),
        headers=_HDR,
    )
    assert bad.status_code == 400
    good = c["client"].post(
        "/api/v1/returns",
        json=_payload(refund_tenders=[{"method": "CASH", "amount": _BILLED_GROSS}]),
        headers=_HDR,
    )
    assert good.status_code in (200, 201), good.text
    assert _returned_qty(c["order"]) == 1.0  # claimed exactly once


def test_unknown_tender_code_is_rejected_pre_claim(ctx):
    c = ctx([{"method": "CASH", "amount": _BILLED_GROSS}])
    r = c["client"].post(
        "/api/v1/returns",
        json=_payload(refund_tenders=[{"method": "BITCOIN", "amount": _BILLED_GROSS}]),
        headers=_HDR,
    )
    assert r.status_code == 400
    assert _returned_qty(c["order"]) == 0.0
    assert c["loyalty"] == []


# ===========================================================================
# (d) EXCHANGE-COLLECT completes and keys the cash-in off collect_method.
# ===========================================================================


def _exchange_payload(collect_method=None, client_price=7000.00,
                      product_id="PRD-2", sku="RB-2"):
    """`client_price` is what the till TYPES. The server must IGNORE it and
    price the line from the catalog."""
    body = _payload(
        return_type="EXCHANGE",
        replacement_items=[
            {
                "product_id": product_id,
                "name": "Replacement Frame",
                "sku": sku,
                "quantity": 1,
                "unit_price": client_price,
            }
        ],
    )
    if collect_method is not None:
        body["collect_method"] = collect_method
    return body


def test_exchange_collect_completes_and_adds_cash_in(ctx):
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}])
    r = c["client"].post(
        "/api/v1/returns", json=_exchange_payload("CASH"), headers=_HDR
    )
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body["settlement"]["direction"] == "COLLECT"
    assert body["collect_method"] == "CASH"
    assert body["drawer_auto_netted"] is True

    expected_collect = round(_CATALOG_PRICE - _BILLED_GROSS, 2)
    sales, refunds = _cash_sales_for_window(
        c["db"], _STORE, "2000-01-01T00:00:00", "2999-01-01T00:00:00"
    )
    assert sales == expected_collect  # Rs 1,100 cash IN
    assert refunds == 0.0


def test_exchange_collect_without_method_nets_nowhere(ctx):
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}])
    r = c["client"].post("/api/v1/returns", json=_exchange_payload(), headers=_HDR)
    assert r.status_code in (200, 201), r.text
    assert r.json()["collect_method"] is None
    assert r.json()["drawer_auto_netted"] is False
    sales, refunds = _cash_sales_for_window(
        c["db"], _STORE, "2000-01-01T00:00:00", "2999-01-01T00:00:00"
    )
    assert (sales, refunds) == (0.0, 0.0)


def test_exchange_even_direction_moves_no_money(ctx):
    """Restored coverage for the `if direction != COLLECT: continue` branch.
    The CATALOG price equals the returned value, so the swap is even."""
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}],
            catalog_price=_BILLED_GROSS)
    r = c["client"].post(
        "/api/v1/returns",
        json=_exchange_payload("CASH"),
        headers=_HDR,
    )
    assert r.status_code in (200, 201), r.text
    assert r.json()["settlement"]["direction"] == "EVEN"
    sales, refunds = _cash_sales_for_window(
        c["db"], _STORE, "2000-01-01T00:00:00", "2999-01-01T00:00:00"
    )
    assert (sales, refunds) == (0.0, 0.0)


def test_exchange_bad_collect_method_rejected_pre_claim(ctx):
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}])
    r = c["client"].post(
        "/api/v1/returns", json=_exchange_payload("CRYPTO"), headers=_HDR
    )
    assert r.status_code == 400
    assert _returned_qty(c["order"]) == 0.0
    assert c["loyalty"] == []


# ===========================================================================
# Cumulative per-tender cap across TWO returns.
# ===========================================================================


def test_second_refund_cannot_exceed_the_tender_remaining(ctx):
    c = ctx([
        {"method": "CASH", "amount": 3000.00},
        {"method": "CARD", "amount": 2900.00},
    ], qty=2)
    # First return: 1 unit (Rs 2,950) all to CASH -> leaves Rs 50 of CASH.
    first = c["client"].post(
        "/api/v1/returns",
        json=_payload(refund_tenders=[{"method": "CASH", "amount": 2950.00}]),
        headers=_HDR,
    )
    assert first.status_code in (200, 201), first.text
    # Second return of the other unit, again all to CASH -> exceeds remaining.
    second = c["client"].post(
        "/api/v1/returns",
        json=_payload(refund_tenders=[{"method": "CASH", "amount": 2950.00}]),
        headers=_HDR,
    )
    assert second.status_code == 422, second.text
    detail = second.json()["detail"]
    assert detail["reason"] == "TENDER_MISMATCH"
    assert detail["already_refunded_on_tender"] == 2950.00


# ===========================================================================
# The FE and BE balance tolerances must AGREE (a Re 1.00 client tolerance
# showed a green "balanced" split that the server then 400'd).
# ===========================================================================


# ===========================================================================
# ROUND-3 MUST-FIX 1 + 5: MULTI-LEG SAME-TENDER. The cap used to be applied
# PER LEG, so N legs of one tender each got the FULL allowance and the request
# was never summed. The chair executed a Rs 2,900 cash skim through this hole:
# every one of the 18 previous tests used one leg per tender, which is exactly
# why it shipped green.
# ===========================================================================


def test_two_same_tender_legs_each_under_cap_are_refused(ctx):
    """THE SKIM. Order took CASH 3000 + UPI 2900. Two {CASH, 2950} legs are each
    individually under the Rs 3,000 cash allowance, but together they claim
    Rs 5,900 of cash the till never took -- the drawer would drop Rs 2,900 below
    reality and the cashier could pocket the notes and still close BALANCED."""
    c = ctx([
        {"method": "CASH", "amount": 3000.00},
        {"method": "UPI", "amount": 2900.00},
    ])
    r = c["client"].post(
        "/api/v1/returns",
        json=_payload(
            refund_tenders=[
                {"method": "CASH", "amount": 2950.00},
                {"method": "CASH", "amount": 2950.00},
            ]
        ),
        headers=_HDR,
    )
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["reason"] == "TENDER_MISMATCH"
    # The AGGREGATE is what gets reported, not one leg.
    assert detail["requested_amount"] == 5900.00
    assert detail["captured_on_tender"] == 3000.00
    # Nothing recorded, nothing burned, drawer untouched.
    assert c["returns"].docs == []
    assert _returned_qty(c["order"]) == 0.0
    _, refunds = _cash_sales_for_window(
        c["db"], _STORE, "2000-01-01T00:00:00", "2999-01-01T00:00:00"
    )
    assert refunds == 0.0


def test_three_same_tender_legs_are_summed(ctx):
    c = ctx([
        {"method": "CASH", "amount": 3000.00},
        {"method": "UPI", "amount": 2900.00},
    ])
    r = c["client"].post(
        "/api/v1/returns",
        json=_payload(
            refund_tenders=[
                {"method": "CASH", "amount": 1966.67},
                {"method": "CASH", "amount": 1966.67},
                {"method": "CASH", "amount": 1966.66},
            ]
        ),
        headers=_HDR,
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["requested_amount"] == 5900.00


def test_legal_multi_leg_split_is_accepted_and_folded(ctx):
    """A legitimate split across DIFFERENT tenders still works, and same-tender
    rows are FOLDED into one canonical leg so the readers see the true total."""
    c = ctx([
        {"method": "CASH", "amount": 3000.00},
        {"method": "UPI", "amount": 2900.00},
    ])
    r = c["client"].post(
        "/api/v1/returns",
        json=_payload(
            refund_tenders=[
                {"method": "CASH", "amount": 1500.00},
                {"method": "CASH", "amount": 1500.00},   # same tender, split rows
                {"method": "UPI", "amount": 2900.00},
            ]
        ),
        headers=_HDR,
    )
    assert r.status_code in (200, 201), r.text
    doc = c["returns"].docs[0]
    assert doc["refund_tenders"] == [
        {"method": "CASH", "amount": 3000.00},
        {"method": "UPI", "amount": 2900.00},
    ]
    _, refunds = _cash_sales_for_window(
        c["db"], _STORE, "2000-01-01T00:00:00", "2999-01-01T00:00:00"
    )
    assert refunds == 3000.00  # exactly the cash the till actually took


# ===========================================================================
# ROUND-3 MUST-FIX 3: a part-voucher sale must be REFUNDABLE. Before the fix
# every payload a cashier could build was rejected and the only escape was a
# second CASH row -- straight into the skim above.
# ===========================================================================


def _voucher_ctx(ctx):
    return ctx([
        {"method": "CASH", "amount": 3000.00},
        {"method": "GIFT_VOUCHER", "amount": 2900.00},
    ])


def test_part_voucher_sale_quote_flags_the_shortfall(ctx):
    c = _voucher_ctx(ctx)
    q = c["client"].post("/api/v1/returns/quote", json=_payload(), headers=_HDR).json()
    assert q["captured_tenders"] == {"CASH": 3000.00}
    assert q["non_refundable_tenders"] == {"GIFT_VOUCHER": 2900.00}
    # STORE_CREDIT is offered for exactly the voucher portion.
    assert q["refundable_by_tender"]["STORE_CREDIT"] == 2900.00
    assert q["cash_in_shortfall"] is True
    # ... and the escape banner condition is on.
    assert q["tenders_unverifiable"] is True


def test_part_voucher_refund_completes_via_store_credit(ctx):
    c = _voucher_ctx(ctx)
    r = c["client"].post(
        "/api/v1/returns",
        json=_payload(
            refund_tenders=[
                {"method": "CASH", "amount": 3000.00},
                {"method": "STORE_CREDIT", "amount": 2900.00},
            ]
        ),
        headers=_HDR,
    )
    assert r.status_code in (200, 201), r.text
    assert r.json()["drawer_auto_netted"] is True
    # ONLY the cash leg reaches the drawer; store credit never does.
    _, refunds = _cash_sales_for_window(
        c["db"], _STORE, "2000-01-01T00:00:00", "2999-01-01T00:00:00"
    )
    assert refunds == 3000.00


def test_store_credit_leg_cannot_exceed_the_non_refundable_pool(ctx):
    c = _voucher_ctx(ctx)
    r = c["client"].post(
        "/api/v1/returns",
        json=_payload(
            refund_tenders=[
                {"method": "CASH", "amount": 100.00},
                {"method": "STORE_CREDIT", "amount": 5800.00},
            ]
        ),
        headers=_HDR,
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["requested_method"] == "STORE_CREDIT"


def test_part_voucher_cash_only_split_still_refused(ctx):
    """The skim route out of the old dead end stays closed."""
    c = _voucher_ctx(ctx)
    r = c["client"].post(
        "/api/v1/returns",
        json=_payload(
            refund_tenders=[
                {"method": "CASH", "amount": 3000.00},
                {"method": "CASH", "amount": 2900.00},
            ]
        ),
        headers=_HDR,
    )
    assert r.status_code == 422, r.text
    assert _returned_qty(c["order"]) == 0.0


# ===========================================================================
# ROUND-3 MUST-FIX 2: the EXCHANGE replacement price is SERVER-resolved.
# A client-typed Rs 59,000 produced a Rs 53,100 COLLECT and moved the drawer.
# ===========================================================================


def test_exchange_ignores_a_fat_finger_client_price(ctx):
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}])
    r = c["client"].post(
        "/api/v1/returns",
        json=_exchange_payload("CASH", client_price=59000.00),
        headers=_HDR,
    )
    assert r.status_code in (200, 201), r.text
    # Settlement uses the CATALOG price (7000), not the typed 59000.
    assert r.json()["settlement"]["difference"] == round(
        _CATALOG_PRICE - _BILLED_GROSS, 2
    )
    sales, _ = _cash_sales_for_window(
        c["db"], _STORE, "2000-01-01T00:00:00", "2999-01-01T00:00:00"
    )
    assert sales == round(_CATALOG_PRICE - _BILLED_GROSS, 2)  # Rs 1,100, not 53,100


def test_exchange_quote_echoes_catalog_priced_lines(ctx):
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}])
    q = c["client"].post(
        "/api/v1/returns/quote",
        json=_exchange_payload("CASH", client_price=59000.00),
        headers=_HDR,
    ).json()
    assert q["replacement_items_priced"][0]["unit_price"] == _CATALOG_PRICE
    assert q["replacement_items_priced"][0]["price_source"] == "CATALOG"


def test_exchange_with_uncatalogued_replacement_is_rejected_pre_claim(ctx):
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}])
    r = c["client"].post(
        "/api/v1/returns",
        json=_exchange_payload("CASH", product_id="NOT-IN-CATALOG", sku="NOPE"),
        headers=_HDR,
    )
    assert r.status_code == 400, r.text
    assert "catalog" in r.text.lower()
    assert _returned_qty(c["order"]) == 0.0
    assert c["loyalty"] == []


def test_backend_balance_tolerance_is_one_paisa(ctx):
    """0.01 off is accepted (rounding); 0.02 off is rejected. The FE uses the
    same 0.01 constant (TENDER_BALANCE_EPSILON in ReturnsPage.tsx)."""
    c = ctx([{"method": "CASH", "amount": _BILLED_GROSS}])
    ok = c["client"].post(
        "/api/v1/returns",
        json=_payload(
            refund_tenders=[{"method": "CASH", "amount": _BILLED_GROSS - 0.01}]
        ),
        headers=_HDR,
    )
    assert ok.status_code in (200, 201), ok.text

    c2 = ctx([{"method": "CASH", "amount": _BILLED_GROSS}])
    bad = c2["client"].post(
        "/api/v1/returns",
        json=_payload(
            refund_tenders=[{"method": "CASH", "amount": _BILLED_GROSS - 0.50}]
        ),
        headers=_HDR,
    )
    assert bad.status_code == 400, bad.text
