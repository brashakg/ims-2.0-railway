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


def _inclusive_order(payments, qty=1, customer_id="CUST-1"):
    return {
        "order_id": "ORD-INC-1",
        "order_number": "ORD-INC-1-2026",
        "customer_id": customer_id,
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

    def _build(payments, qty=1, catalog_price=_CATALOG_PRICE,
               customer_id="CUST-1", ledger_down=False):
        order = _inclusive_order(payments, qty=qty, customer_id=customer_id)
        orders_coll = _ClaimingOrdersColl(order)
        repo = _RepoWithColl(orders_coll)
        returns_coll = _QueryColl()
        ledger_coll = _QueryColl()
        if ledger_down:
            def _boom(_doc):
                raise RuntimeError("credit_note_ledger insert failed")
            ledger_coll.insert_one = _boom  # type: ignore[assignment]
        # ONE customer repo per build so a store-credit balance bump is
        # observable (a fresh repo per lookup silently swallowed it).
        customer_repo = _FakeCustomerRepo()

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
            returns_router, "get_customer_repository", lambda: customer_repo
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
            "customers": customer_repo,
            "ledger": ledger_coll,
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
        json=_exchange_payload("CASH", client_price=_BILLED_GROSS),
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
    """ROUND-5 MUST-FIX 1 + 2. A STORE_CREDIT leg used to pay the customer
    NOTHING: 201, _issue_store_credit calls 0, credit_note_ledger empty, while
    refund_amount was stamped 5900 -- the customer surrendered Rs 5,900 of goods,
    took Rs 3,000 in notes, and the missing Rs 2,900 existed nowhere in IMS (and
    the cumulative cap then blocked the corrective refund forever).

    The old test asserted only 201 + the cash leg, so it was GREEN while the
    customer was short. It now proves the money was actually issued."""
    c = _voucher_ctx(ctx)
    before = c["customers"].customers["CUST-1"]["store_credit"]
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
    body = r.json()
    assert body["drawer_auto_netted"] is True

    # ONLY the cash leg reaches the drawer; store credit never does.
    _, refunds = _cash_sales_for_window(
        c["db"], _STORE, "2000-01-01T00:00:00", "2999-01-01T00:00:00"
    )
    assert refunds == 3000.00

    # THE CUSTOMER IS ACTUALLY PAID: balance rose by EXACTLY the STORE_CREDIT leg.
    after = c["customers"].customers["CUST-1"]["store_credit"]
    assert round(after - before, 2) == 2900.00, (before, after)

    # ... and a real ISSUED ledger row backs it, carrying the GST split so the
    # GSTR-1 CDNR reports the true output-tax reversal.
    issued = [d for d in c["ledger"].docs if d.get("type") == "ISSUED"]
    assert len(issued) == 1, c["ledger"].docs
    row = issued[0]
    assert row["amount"] == 2900.00
    assert row["ref"] == body["return_id"]
    assert row.get("taxable") is not None and row.get("tax") is not None
    # GST is pro-rated to the credited portion, not the whole refund.
    assert round(row["taxable"] + row["tax"], 2) == 2900.00
    assert row.get("gst_rate") == 18.0

    # The response surfaces what was issued.
    assert body["credit_amount"] == 2900.00
    assert body["credit_entry"] is not None


def test_store_credit_refund_is_reflected_on_the_return_doc(ctx):
    c = _voucher_ctx(ctx)
    c["client"].post(
        "/api/v1/returns",
        json=_payload(
            refund_tenders=[
                {"method": "CASH", "amount": 3000.00},
                {"method": "STORE_CREDIT", "amount": 2900.00},
            ]
        ),
        headers=_HDR,
    )
    doc = c["returns"].docs[0]
    assert doc["credit_amount"] == 2900.00
    assert doc["credit_entry"] is not None
    # refund_amount stays the FULL net (the cumulative cap is on total value
    # returned), but the credited portion is now explicit and backed by a row.
    assert doc["refund_amount"] == 5900.00


def test_all_store_credit_refund_issues_the_whole_amount(ctx):
    """A sale paid ENTIRELY on a voucher refunds entirely as store credit."""
    c = ctx([{"method": "GIFT_VOUCHER", "amount": _BILLED_GROSS}])
    before = c["customers"].customers["CUST-1"]["store_credit"]
    r = c["client"].post(
        "/api/v1/returns",
        json=_payload(
            refund_tenders=[{"method": "STORE_CREDIT", "amount": _BILLED_GROSS}]
        ),
        headers=_HDR,
    )
    assert r.status_code in (200, 201), r.text
    after = c["customers"].customers["CUST-1"]["store_credit"]
    assert round(after - before, 2) == _BILLED_GROSS
    _, refunds = _cash_sales_for_window(
        c["db"], _STORE, "2000-01-01T00:00:00", "2999-01-01T00:00:00"
    )
    assert refunds == 0.0  # nothing left the drawer


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
    """ROUND-4 harm, ROUND-5 answer. A typed Rs 59,000 produced a Rs 53,100
    COLLECT. The catalog price is now a CEILING, so an above-catalog price is
    REFUSED outright -- the cashier sees the error instead of a silently
    different number, and the drawer is never moved by a typed figure."""
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}])
    r = c["client"].post(
        "/api/v1/returns",
        json=_exchange_payload("CASH", client_price=59000.00),
        headers=_HDR,
    )
    assert r.status_code == 400, r.text
    sales, _ = _cash_sales_for_window(
        c["db"], _STORE, "2000-01-01T00:00:00", "2999-01-01T00:00:00"
    )
    assert sales == 0.0  # neither Rs 53,100 nor Rs 1,100 reached the drawer


def test_exchange_quote_echoes_catalog_priced_lines(ctx):
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}])
    q = c["client"].post(
        "/api/v1/returns/quote",
        json=_exchange_payload("CASH", client_price=_CATALOG_PRICE),
        headers=_HDR,
    ).json()
    assert q["replacement_items_priced"][0]["unit_price"] == _CATALOG_PRICE
    assert q["replacement_items_priced"][0]["price_source"] == "CATALOG"
    assert q["replacement_total"] == _CATALOG_PRICE


# ===========================================================================
# ROUND-5 MUST-FIX 4 (verdict): cash_in_shortfall must require a pool that
# ACTUALLY EXISTS. On a Shopify-paid order (no captured tenders, no
# non-refundable pool) it came back True with non_refundable_tenders {}, so the
# till printed an impossible "refund that portion as STORE_CREDIT" AND the
# !cashInShortfall gate suppressed the ONE correct advisory on exactly the
# orders it was written for.
# ===========================================================================


def test_unverifiable_order_does_not_claim_a_nonexistent_voucher_pool(ctx):
    c = ctx([])  # Shopify-paid / imported: no captured payments at all
    c["order"]["amount_paid"] = _BILLED_GROSS
    q = c["client"].post("/api/v1/returns/quote", json=_payload(), headers=_HDR).json()
    assert q["captured_tenders"] == {}
    assert q["non_refundable_tenders"] == {}
    # There is no voucher portion to steer the cashier towards.
    assert q["cash_in_shortfall"] is False
    # ... but the order genuinely cannot be verified, so the escape advisory
    # (the one that says "record it as cash paid out") must still fire.
    assert q["tenders_unverifiable"] is True


def test_part_voucher_order_still_reports_the_shortfall(ctx):
    """The guard must not disarm the REAL case it was written for."""
    c = _voucher_ctx(ctx)
    q = c["client"].post("/api/v1/returns/quote", json=_payload(), headers=_HDR).json()
    assert q["non_refundable_tenders"] == {"GIFT_VOUCHER": 2900.00}
    assert q["cash_in_shortfall"] is True


# ===========================================================================
# ROUND-5 MUST-FIX 5 (verdict): replacement QUANTITY was an unverified client
# number multiplied straight into a drawer figure. Measured before the fix:
# quantity 50 -> collect_amount 344100.0 (a Rs 343,000 phantom shortage from
# one fat-finger); 1e6 -> 6,999,994,100.0; 2.5 accepted.
# ===========================================================================


def _qty_payload(qty, collect_method="CASH", client_price=_CATALOG_PRICE):
    body = _payload(
        return_type="EXCHANGE",
        replacement_items=[
            {
                "product_id": "PRD-2",
                "name": "Replacement Frame",
                "sku": "RB-2",
                "quantity": qty,
                "unit_price": client_price,
            }
        ],
    )
    body["collect_method"] = collect_method
    return body


def test_fat_finger_replacement_quantity_is_refused(ctx):
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}])
    r = c["client"].post("/api/v1/returns", json=_qty_payload(50), headers=_HDR)
    assert r.status_code == 400, r.text
    assert c["returns"].docs == []
    assert _returned_qty(c["order"]) == 0.0
    sales, _ = _cash_sales_for_window(
        c["db"], _STORE, "2000-01-01T00:00:00", "2999-01-01T00:00:00"
    )
    assert sales == 0.0  # no Rs 344,100 phantom reached the drawer


def test_absurd_replacement_quantity_is_refused(ctx):
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}])
    r = c["client"].post("/api/v1/returns", json=_qty_payload(1_000_000), headers=_HDR)
    assert r.status_code == 400, r.text


def test_fractional_replacement_quantity_is_refused(ctx):
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}])
    r = c["client"].post("/api/v1/returns", json=_qty_payload(2.5), headers=_HDR)
    assert r.status_code == 400, r.text
    assert "whole" in r.text.lower() or "quantity" in r.text.lower()


def test_zero_replacement_quantity_is_refused(ctx):
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}])
    r = c["client"].post("/api/v1/returns", json=_qty_payload(0), headers=_HDR)
    assert r.status_code == 400, r.text


def test_quantity_within_the_line_cap_is_accepted(ctx):
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}])
    r = c["client"].post("/api/v1/returns", json=_qty_payload(2), headers=_HDR)
    assert r.status_code in (200, 201), r.text
    # 2 x 7000 - 5900 = 8100 collected.
    assert r.json()["collect_amount"] == 8100.00


# ===========================================================================
# ROUND-5 MUST-FIX 6 (verdict): the catalog price must be a CEILING, not an
# override. Optical retail discounts routinely; a Rs 5,900 frame sold at 15%
# off and swapped for the identical SKU manufactured a Rs 885 COLLECT the
# customer never owed, with no representable honest payload.
# ===========================================================================


def test_negotiated_price_below_catalog_is_honoured(ctx):
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}])
    r = c["client"].post(
        "/api/v1/returns",
        json=_qty_payload(1, client_price=6500.00),
        headers=_HDR,
    )
    assert r.status_code in (200, 201), r.text
    # 6500 - 5900 = 600, NOT the catalog-derived 1100.
    assert r.json()["collect_amount"] == 600.00
    assert r.json()["settlement"]["replacement_total"] == 6500.00


def test_price_above_catalog_is_refused(ctx):
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}])
    r = c["client"].post(
        "/api/v1/returns",
        json=_qty_payload(1, client_price=59000.00),
        headers=_HDR,
    )
    assert r.status_code == 400, r.text
    assert c["returns"].docs == []
    sales, _ = _cash_sales_for_window(
        c["db"], _STORE, "2000-01-01T00:00:00", "2999-01-01T00:00:00"
    )
    assert sales == 0.0


def test_quote_echoes_a_server_computed_replacement_total(ctx):
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}])
    q = c["client"].post(
        "/api/v1/returns/quote",
        json=_qty_payload(2, client_price=6500.00),
        headers=_HDR,
    ).json()
    assert q["replacement_total"] == 13000.00
    assert q["replacement_items_priced"][0]["price_source"] == "NEGOTIATED"
    assert q["replacement_items_priced"][0]["quantity"] == 2


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


# ===========================================================================
# ROUND-6. The chair's pattern: every round-5 control was correct where it was
# applied and absent at its sibling / opposite side. These tests pin the OTHER
# side of each one.
# ===========================================================================


def _multi_line_exchange(n_lines, qty_each, collect_method="CASH",
                         unit_price=_CATALOG_PRICE):
    body = _payload(
        return_type="EXCHANGE",
        replacement_items=[
            {
                "product_id": "PRD-2",
                "name": "Replacement Frame",
                "sku": "RB-2",
                "quantity": qty_each,
                "unit_price": unit_price,
            }
            for _ in range(n_lines)
        ],
    )
    body["collect_method"] = collect_method
    return body


def _expected_paisa(db):
    from api.services import eod_tally

    return eod_tally.compute_expected(
        db, _STORE, "2000-01-01T00:00:00", "2999-01-01T00:00:00", 0, 0
    )["expected_cash_paisa"]


def _voucher_ctx_walkin(ctx):
    return ctx(
        [{"method": "CASH", "amount": 3000.00},
         {"method": "GIFT_VOUCHER", "amount": 2900.00}],
        customer_id=None,
    )


# --- MUST-FIX 1: the qty bound was PER LINE; the ORDER total was unbounded ---


def test_many_lines_each_under_the_per_line_cap_are_refused(ctx):
    """50 lines x qty 1: every line passes the per-line cap of 20, but the
    exchange collects Rs 344,100 -- and this PR routes collect_amount into the
    REAL Day-End drawer, so that is a Rs 344,100 phantom shortage."""
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}])
    before = _expected_paisa(c["db"])
    r = c["client"].post(
        "/api/v1/returns", json=_multi_line_exchange(50, 1), headers=_HDR
    )
    assert r.status_code == 400, r.text
    assert c["returns"].docs == []
    assert _returned_qty(c["order"]) == 0.0
    # THE POINT: the closing drawer figure is untouched by the rejected call.
    assert _expected_paisa(c["db"]) == before == 0


def test_worst_case_lines_times_qty_is_refused(ctx):
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}])
    before = _expected_paisa(c["db"])
    r = c["client"].post(
        "/api/v1/returns", json=_multi_line_exchange(20, 20), headers=_HDR
    )
    assert r.status_code == 400, r.text
    assert _expected_paisa(c["db"]) == before == 0


def test_duplicate_lines_of_the_same_sku_are_summed_against_the_cap(ctx):
    """The realistic harm: repeated clicks on the same frame. The per-ORDER
    total is what must be capped, not each row."""
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}])
    r = c["client"].post(
        "/api/v1/returns", json=_multi_line_exchange(11, 2), headers=_HDR
    )
    assert r.status_code == 400, r.text  # 22 units > the 20-unit order cap


def test_a_legitimate_multi_line_exchange_still_completes(ctx):
    """The cap must not break an honest two-item swap."""
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}])
    r = c["client"].post(
        "/api/v1/returns", json=_multi_line_exchange(2, 1), headers=_HDR
    )
    assert r.status_code in (200, 201), r.text
    assert r.json()["collect_amount"] == round(2 * _CATALOG_PRICE - _BILLED_GROSS, 2)


# --- MUST-FIX 2: the price CEILING had no FLOOR, and a low price flips the
#     settlement direction into REFUND, minting store credit invisibly. ---


def _typed_price_exchange(price, collect_method="CASH"):
    return _qty_payload(1, collect_method=collect_method, client_price=price)


def test_typed_price_cannot_flip_collect_into_refund(ctx):
    """catalog 7000, goods 5900 -> an honest exchange COLLECTS 1100. A typed
    0.01 turned it into Rs 5,899.99 of POS-redeemable store credit while the
    customer kept a Rs 7,000 frame -- and the drawer never moved, so Day-End
    could not flag it."""
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}])
    before = c["customers"].customers["CUST-1"]["store_credit"]
    r = c["client"].post(
        "/api/v1/returns", json=_typed_price_exchange(0.01), headers=_HDR
    )
    assert r.status_code == 400, r.text
    assert c["returns"].docs == []
    assert c["ledger"].docs == []
    assert c["customers"].customers["CUST-1"]["store_credit"] == before
    assert _returned_qty(c["order"]) == 0.0


def test_deep_discount_below_the_floor_is_refused(ctx):
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}])
    r = c["client"].post(
        "/api/v1/returns", json=_typed_price_exchange(100.0), headers=_HDR
    )
    assert r.status_code == 400, r.text
    assert c["ledger"].docs == []


def test_one_rupee_price_is_refused(ctx):
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}])
    r = c["client"].post(
        "/api/v1/returns", json=_typed_price_exchange(1.0), headers=_HDR
    )
    assert r.status_code == 400, r.text


def test_the_legitimate_negotiated_discount_still_works(ctx):
    """MF6's real case must survive: a modest negotiated discount is honoured."""
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}])
    r = c["client"].post(
        "/api/v1/returns", json=_typed_price_exchange(6500.0), headers=_HDR
    )
    assert r.status_code in (200, 201), r.text
    assert r.json()["collect_amount"] == 600.00
    assert r.json()["credit_amount"] is None


# --- MUST-FIX 3: the pro-rated GST split mixed bases -> zero, then NEGATIVE tax


@pytest.mark.parametrize("fee", [0.0, 900.0, 1500.0])
def test_store_credit_gst_split_is_honest_at_every_restocking_fee(ctx, fee):
    """The old assertion (taxable + tax == leg) was a TAUTOLOGY: the code
    DEFINED tax = leg - taxable. These pin both halves independently against an
    inclusive-GST split of the CREDITED amount."""
    c = _voucher_ctx(ctx)
    net = round(_BILLED_GROSS - fee, 2)
    cash_leg = min(3000.00, net)
    credit_leg = round(net - cash_leg, 2)
    if credit_leg <= 0:
        pytest.skip("fee leaves no non-drawer portion")
    r = c["client"].post(
        "/api/v1/returns",
        json=_payload(
            restocking_fee=fee,
            refund_tenders=[
                {"method": "CASH", "amount": cash_leg},
                {"method": "STORE_CREDIT", "amount": credit_leg},
            ],
        ),
        headers=_HDR,
    )
    assert r.status_code in (200, 201), r.text
    row = [d for d in c["ledger"].docs if d.get("type") == "ISSUED"][0]
    # INDEPENDENT recomputation: back 18% out of the CREDITED amount.
    expect_taxable = round(credit_leg / 1.18, 2)
    expect_tax = round(credit_leg - expect_taxable, 2)
    assert row["taxable"] == expect_taxable, (fee, row)
    assert row["tax"] == expect_tax, (fee, row)
    # Arithmetic sanity the tautology could never catch.
    assert row["tax"] > 0, (fee, row)
    assert row["taxable"] <= credit_leg, (fee, row)


# --- MUST-FIX 4: fail-loud reached 1 of 3 credit-issuing branches ---


def _credit_note_payload():
    return _payload(return_type="CREDIT_NOTE")


def _exchange_refund_payload():
    """Replacement CHEAPER than the returned goods -> REFUND direction."""
    return _payload(
        return_type="EXCHANGE",
        replacement_items=[
            {
                "product_id": "PRD-2",
                "name": "Replacement Frame",
                "sku": "RB-2",
                "quantity": 1,
                "unit_price": 1000.00,
            }
        ],
    )


@pytest.mark.parametrize("kind", ["RETURN", "CREDIT_NOTE", "EXCHANGE_REFUND"])
def test_no_branch_claims_credit_without_a_ledger_row_walkin(ctx, kind):
    """A walk-in (no customer record) is routine counter behaviour and makes
    _issue_store_credit return None. Round 5 fail-louded the RETURN branch only:
    CREDIT_NOTE and EXCHANGE-REFUND returned 201 claiming credit that had no
    ledger row, AND burned the returnable quantity."""
    if kind == "EXCHANGE_REFUND":
        c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}],
                catalog_price=1000.00, customer_id=None)
        body = _exchange_refund_payload()
    elif kind == "CREDIT_NOTE":
        c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}], customer_id=None)
        body = _credit_note_payload()
    else:
        c = _voucher_ctx_walkin(ctx)
        body = _payload(refund_tenders=[
            {"method": "CASH", "amount": 3000.00},
            {"method": "STORE_CREDIT", "amount": 2900.00},
        ])
    r = c["client"].post("/api/v1/returns", json=body, headers=_HDR)
    assert r.status_code >= 400, (kind, r.status_code, r.text)
    assert c["ledger"].docs == [], kind
    assert _returned_qty(c["order"]) == 0.0, kind          # RELEASED, not burned
    assert c["returns"].docs == [], kind


@pytest.mark.parametrize("kind", ["RETURN", "CREDIT_NOTE", "EXCHANGE_REFUND"])
def test_no_branch_claims_credit_when_the_ledger_is_down(ctx, kind):
    if kind == "EXCHANGE_REFUND":
        c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}],
                catalog_price=1000.00, ledger_down=True)
        body = _exchange_refund_payload()
    elif kind == "CREDIT_NOTE":
        c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}], ledger_down=True)
        body = _credit_note_payload()
    else:
        c = ctx([{"method": "CASH", "amount": 3000.00},
                 {"method": "GIFT_VOUCHER", "amount": 2900.00}], ledger_down=True)
        body = _payload(refund_tenders=[
            {"method": "CASH", "amount": 3000.00},
            {"method": "STORE_CREDIT", "amount": 2900.00},
        ])
    r = c["client"].post("/api/v1/returns", json=body, headers=_HDR)
    assert r.status_code >= 400, (kind, r.status_code, r.text)
    assert _returned_qty(c["order"]) == 0.0, kind
    assert c["returns"].docs == [], kind


# --- MUST-FIX 5: a walk-in part-voucher return must have SOME path to 201 ---


def test_walkin_quote_does_not_offer_store_credit(ctx):
    """Offering a tender the server will then 503 on is a dead end whose error
    names an impossible remedy."""
    c = _voucher_ctx_walkin(ctx)
    q = c["client"].post("/api/v1/returns/quote", json=_payload(), headers=_HDR).json()
    assert "STORE_CREDIT" not in q["refundable_by_tender"], q
    # ... and the till is told a complete split is impossible, so it offers the
    # un-netted escape instead of disabling the button.
    assert q["tenders_unverifiable"] is True


def test_walkin_part_voucher_refund_has_a_completable_path(ctx):
    c = _voucher_ctx_walkin(ctx)
    # The FE-buildable escape: no refund_tenders -> recorded, netted nowhere.
    r = c["client"].post("/api/v1/returns", json=_payload(), headers=_HDR)
    assert r.status_code in (200, 201), r.text
    assert r.json()["drawer_auto_netted"] is False
    assert r.json()["credit_amount"] is None


# --- MUST-FIX 6(b): gateway + counter cash must raise an advisory ---


def test_gateway_plus_counter_cash_is_flagged_unverifiable(ctx):
    """SHOPIFY 2000 + counter CASH 3900 is a real supported shape. Cash-in
    cannot cover the Rs 5,900 net, there is no voucher pool, and BOTH advisories
    used to stay silent -- the cashier hands back Rs 5,900 and records nothing."""
    c = ctx([{"method": "SHOPIFY_GATEWAY", "amount": 2000.00},
             {"method": "CASH", "amount": 3900.00}])
    q = c["client"].post("/api/v1/returns/quote", json=_payload(), headers=_HDR).json()
    assert q["non_refundable_tenders"] == {}
    # No voucher pool -> the STORE_CREDIT instruction must NOT be shown ...
    assert q["cash_in_shortfall"] is False
    # ... but the refund is NOT fully tenderable, so the escape advisory must.
    assert q["tenders_unverifiable"] is True


def test_collect_ceiling_fires_even_when_unit_count_is_legal(ctx):
    """20 lines x qty 1 = 20 units, WITHIN the unit cap -- but the difference is
    Rs 134,100. The output ceiling is the backstop that makes any future factor
    of the drawer figure safe by default, so it must be reachable on its own."""
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}])
    before = _expected_paisa(c["db"])
    r = c["client"].post(
        "/api/v1/returns", json=_multi_line_exchange(20, 1), headers=_HDR
    )
    assert r.status_code == 400, r.text
    assert "collect" in r.text.lower()
    assert _expected_paisa(c["db"]) == before == 0


def test_price_above_the_floor_that_still_flips_direction_is_refused(ctx):
    """catalog 7000 -> floor 3500. A typed 4000 clears the floor but is BELOW
    the Rs 5,900 returned value, so it still flips COLLECT into a REFUND that
    mints store credit. The floor alone does not catch this."""
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}])
    before = c["customers"].customers["CUST-1"]["store_credit"]
    r = c["client"].post(
        "/api/v1/returns", json=_typed_price_exchange(4000.0), headers=_HDR
    )
    assert r.status_code == 400, r.text
    assert c["ledger"].docs == []
    assert c["customers"].customers["CUST-1"]["store_credit"] == before


def test_a_genuine_downgrade_exchange_still_refunds(ctx):
    """The direction-flip guard must NOT block an honest downgrade: when the
    CATALOG price is itself below the returned value, a REFUND is correct."""
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}], catalog_price=4000.00)
    r = c["client"].post(
        "/api/v1/returns", json=_typed_price_exchange(4000.0), headers=_HDR
    )
    assert r.status_code in (200, 201), r.text
    assert r.json()["settlement"]["direction"] == "REFUND"
    assert r.json()["credit_amount"] == 1900.00


def test_unit_cap_fires_when_the_collect_ceiling_would_not(ctx):
    """ISOLATES the per-ORDER unit cap. A cheap catalog item keeps the collected
    difference well under the Rs 100,000 output ceiling, so only the unit cap
    can reject 21 units. Without this the two controls masked each other and a
    mutant that removed the unit cap still passed."""
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}], catalog_price=1000.00)
    before = _expected_paisa(c["db"])
    r = c["client"].post(
        "/api/v1/returns",
        json=_multi_line_exchange(21, 1, unit_price=1000.00),
        headers=_HDR
    )
    assert r.status_code == 400, r.text
    assert "units" in r.text.lower()
    # 21 x 1000 - 5900 = 15,100 -- far below the collect ceiling.
    assert _expected_paisa(c["db"]) == before == 0


def test_price_floor_fires_when_the_direction_guard_would_not(ctx):
    """ISOLATES the negotiated-price FLOOR. With catalog 20,000 the floor is
    10,000; a typed 9,000 is below the floor but STILL above the Rs 5,900
    returned value, so the settlement stays COLLECT and the direction-flip guard
    never fires. Only the floor can reject it."""
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}], catalog_price=20000.00)
    r = c["client"].post(
        "/api/v1/returns", json=_typed_price_exchange(9000.0), headers=_HDR
    )
    assert r.status_code == 400, r.text
    assert "below the catalog price" in r.text.lower()
    assert c["returns"].docs == []


# ===========================================================================
# ROUND-7. Same sibling/other-side question run over round 6's OWN diff:
#   - _issue_credit_or_fail gained a guard on the RETURN branch and none on its
#     CREDIT_NOTE sibling ten lines below        -> MF1
#   - the new hard-fail path releases the qty claim, but the loyalty reversal
#     that ran BEFORE it is never undone         -> MF4
#   - the FE dedupe fixed one replacement-line producer; its sibling
#     (addBlankReplacement) builds a line the server must refuse -> MF2
#   - the price CEILING is exact; the client sends a ROUNDED price -> MF3
# ===========================================================================


@pytest.mark.parametrize("fee", [0.00, 2950.00, 5899.99, 5900.00])
def test_credit_note_completes_at_every_restocking_fee(ctx, fee):
    """MF1. CREDIT_NOTE called _issue_credit_or_fail UNCONDITIONALLY, and
    _issue_store_credit returns None when the amount is <= 0. A fee equal to the
    gross (permitted by the FE, whose max IS the gross) therefore 503'd with
    'the credit ledger could not be written - retry shortly' against a HEALTHY
    ledger: 'take the goods back, issue nothing' became un-recordable and the
    message sent the cashier into an infinite retry."""
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}])
    r = c["client"].post(
        "/api/v1/returns",
        json=_payload(return_type="CREDIT_NOTE", restocking_fee=fee),
        headers=_HDR,
    )
    assert r.status_code in (200, 201), (fee, r.status_code, r.text)
    expected_net = round(_BILLED_GROSS - fee, 2)
    assert r.json()["net_refund"] == expected_net
    issued = [d["amount"] for d in c["ledger"].docs if d.get("type") == "ISSUED"]
    if expected_net > 0:
        assert issued == [expected_net], (fee, c["ledger"].docs)
    else:
        assert issued == [], (fee, c["ledger"].docs)   # nothing to issue
    # The goods came back in EVERY case -- the claim must stand, not be released.
    assert _returned_qty(c["order"]) == 1.0, fee


@pytest.mark.parametrize("fee", [0.00, 5900.00])
def test_quote_and_post_agree_on_a_full_fee_credit_note(ctx, fee):
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}])
    body = _payload(return_type="CREDIT_NOTE", restocking_fee=fee)
    q = c["client"].post("/api/v1/returns/quote", json=body, headers=_HDR)
    assert q.status_code == 200, q.text
    r = c["client"].post("/api/v1/returns", json=body, headers=_HDR)
    assert r.status_code in (200, 201), r.text
    assert q.json()["net_refund"] == r.json()["net_refund"]


# --- MF4: the hard-fail path must not orphan the loyalty reversal ---


@pytest.mark.parametrize("kind", ["CREDIT_NOTE", "EXCHANGE_REFUND"])
def test_credit_failure_leaves_no_orphaned_loyalty_reversal(ctx, kind):
    """The loyalty reversal is keyed on return_id and each retry mints a NEW
    one, so an un-undone reversal re-claws real value on every attempt with no
    return doc to reconcile against. The hard-failing step must run FIRST."""
    if kind == "EXCHANGE_REFUND":
        c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}],
                catalog_price=1000.00, ledger_down=True)
        body = _exchange_refund_payload()
    else:
        c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}], ledger_down=True)
        body = _credit_note_payload()
    r = c["client"].post("/api/v1/returns", json=body, headers=_HDR)
    assert r.status_code >= 400, (kind, r.text)
    assert c["returns"].docs == [], kind
    assert _returned_qty(c["order"]) == 0.0, kind
    assert c["loyalty"] == [], (kind, c["loyalty"])


@pytest.mark.parametrize("kind", ["CREDIT_NOTE", "EXCHANGE_REFUND"])
def test_repeated_credit_failures_never_accumulate_loyalty_reversals(ctx, kind):
    if kind == "EXCHANGE_REFUND":
        c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}],
                catalog_price=1000.00, ledger_down=True)
        body = _exchange_refund_payload()
    else:
        c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}], ledger_down=True)
        body = _credit_note_payload()
    for _ in range(2):
        assert c["client"].post(
            "/api/v1/returns", json=body, headers=_HDR
        ).status_code >= 400
    assert c["loyalty"] == [], (kind, c["loyalty"])
    assert len({call[0] for call in c["loyalty"]}) == 0


def test_successful_return_still_reverses_loyalty(ctx):
    """The reorder must not silently drop the reversal on the happy path."""
    c = ctx([{"method": "CASH", "amount": _BILLED_GROSS}])
    r = c["client"].post(
        "/api/v1/returns",
        json=_payload(refund_tenders=[{"method": "CASH", "amount": _BILLED_GROSS}]),
        headers=_HDR,
    )
    assert r.status_code in (200, 201), r.text
    assert len(c["loyalty"]) == 1, c["loyalty"]


# --- MF3: the exact catalog price must be accepted (the FE must send it) ---


def test_exact_catalog_price_with_paise_is_accepted(ctx):
    """catalog 4241.50. The FE used to Math.round the picked price to 4242 and
    the server's exact ceiling refused it -- the cashier picked from search,
    typed nothing, and was told the price was above catalog."""
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}], catalog_price=4241.50)
    r = c["client"].post(
        "/api/v1/returns",
        json=_qty_payload(2, collect_method="CASH", client_price=4241.50),
        headers=_HDR,
    )
    assert r.status_code in (200, 201), r.text
    assert r.json()["settlement"]["replacement_total"] == 8483.00


def test_a_rounded_up_price_is_still_refused(ctx):
    """The server stays strict; it is the CLIENT that must stop rounding."""
    c = ctx([{"method": "CARD", "amount": _BILLED_GROSS}], catalog_price=4241.50)
    r = c["client"].post(
        "/api/v1/returns",
        json=_qty_payload(2, collect_method="CASH", client_price=4242.00),
        headers=_HDR,
    )
    assert r.status_code == 400, r.text
    assert "above the catalog price" in r.text.lower()
