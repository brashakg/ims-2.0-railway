"""
IMS 2.0 - Returns GST-inclusive refund + restocking-fee tests
=============================================================
Regression cover for the P1 money bug (QA-confirmed 2026-05-28): a full-item
return on an order the customer PAID Rs 1,404 (Rs 1,190 net + Rs 214 GST,
GST-inclusive Indian MRP) refunded only Rs 1,190 - the GST was dropped, so the
customer was UNDER-refunded by ~15%.

The fix: refund = the GST-INCLUSIVE gross the customer paid, computed by
grossing the ORIGINAL order line's NET unit_price up by the rate it was billed
at. An optional restocking_fee (absolute Rs, for damaged / opened goods) is
deducted to get the net refund. gross_refund + restocking_fee + net_refund are
persisted on the return doc + the credit-note ledger row for an auditable
GSTR-1 reversal.

Two layers:
  1. Pure engine (returns_engine): gross-up, net_refund, gst_breakup math.
  2. Endpoint tests via FastAPI TestClient with monkeypatched fake repos - no
     live DB. The order carries gst_rate on its items so the handler recovers
     the authoritative rate.

NOTE: 1190 * 1.18 = 1404.20 exactly (GST 214.20). The QA ticket rounded this to
"1404 / 214"; the precise GST-inclusive figure the engine refunds is 1404.20.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import jwt  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import returns_engine as engine  # noqa: E402
from api.routers import returns as returns_router  # noqa: E402
from api.routers import auth as auth_mod  # noqa: E402

pytestmark = pytest.mark.asyncio


# ============================================================================
# 1. ENGINE - GST-inclusive gross + net refund + GST back-out
# ============================================================================


def test_engine_grosses_up_net_unit_price():
    # The QA case at the engine level: net 1190 @ 18% -> gross 1404.20.
    assert engine.gross_unit_price(1190, 18) == 1404.20
    val = engine.returned_value(
        [{"return_qty": 1, "unit_price": 1190, "gst_rate": 18}]
    )
    assert val == 1404.20


def test_engine_no_rate_is_unchanged():
    # gst_rate absent / 0 -> unit_price treated as already gross (legacy).
    assert engine.returned_value([{"return_qty": 1, "unit_price": 1500}]) == 1500.0
    assert engine.gross_unit_price(1500, 0) == 1500.0


def test_engine_partial_qty_proportional_gross():
    # 2 of a net-1190 @ 18% line -> 2 * 1404.20 = 2808.40.
    assert (
        engine.returned_value(
            [{"return_qty": 2, "unit_price": 1190, "gst_rate": 18}]
        )
        == 2808.40
    )


def test_engine_net_refund_deducts_fee():
    assert engine.net_refund(1404.20, 200) == 1204.20
    # Omitted fee -> full gross.
    assert engine.net_refund(1404.20) == 1404.20


def test_engine_net_refund_rejects_fee_over_gross():
    with pytest.raises(ValueError):
        engine.net_refund(1404.20, 2000)


def test_engine_gst_breakup_backs_tax_out():
    # GST is INSIDE the gross, not added on top: 1404.20 @ 18% -> 1190 + 214.20.
    bk = engine.gst_breakup(1404.20, 18)
    assert bk["taxable"] == 1190.0
    assert bk["tax"] == 214.20
    assert bk["gross"] == 1404.20


# ============================================================================
# 2. ENDPOINT tests (fake repos, no live DB)
# ============================================================================


class _FakeOrderRepo:
    def __init__(self, order=None):
        self._order = order

    def find_by_id(self, oid):
        if self._order and self._order.get("order_id") == oid:
            return self._order
        return None

    def find_by_order_number(self, num):
        if self._order and self._order.get("order_number") == num:
            return self._order
        return None


class _FakeCustomerRepo:
    def __init__(self):
        self.customers = {
            "CUST-1": {"customer_id": "CUST-1", "name": "Asha", "store_credit": 0.0}
        }
        self.updates = []

    def find_by_id(self, cid):
        return self.customers.get(cid)

    def update(self, cid, data):
        self.updates.append((cid, data))
        if cid in self.customers:
            self.customers[cid].update(data)
        return True


class _FakeResult:
    def __init__(self, matched=1):
        self.matched_count = matched
        self.modified_count = matched


class _FakeColl:
    """Generic in-memory collection for `returns` + `credit_note_ledger`."""

    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return _FakeResult(1)

    def find(self, query=None, projection=None):
        q = query or {}
        matched = [d for d in self.docs if all(d.get(k) == v for k, v in q.items())]
        return iter(matched)

    def find_one(self, query=None, projection=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in (query or {}).items()):
                out = dict(d)
                out.pop("_id", None)
                return out
        return None

    def count_documents(self, query=None):
        q = query or {}
        return sum(1 for d in self.docs if all(d.get(k) == v for k, v in q.items()))


def _staff_token(roles, store_id="BV-PUN-01", uid="u1"):
    return jwt.encode(
        {
            "sub": uid,
            "user_id": uid,
            "username": "tester",
            "roles": roles,
            "active_store_id": store_id,
            "exp": datetime.utcnow() + timedelta(hours=1),
        },
        auth_mod.SECRET_KEY,
        algorithm=auth_mod.ALGORITHM,
    )


# The QA order: one line, net unit_price 1190 billed at 18% GST. The customer
# paid 1404.20 gross (1190 taxable + 214.20 tax). item_id matches the return
# line's order_item_id so the handler recovers the authoritative gst_rate.
def _qa_order():
    return {
        "order_id": "ORD-BOK01",
        "order_number": "ORD-BOK01-2026-8E5731",
        "customer_id": "CUST-1",
        "customer_name": "Asha",
        "payment_method": "UPI",
        "store_id": "BV-PUN-01",
        "items": [
            {
                "item_id": "li1",
                "product_id": "PRD-1",
                "product_name": "Ray-Ban Sunglass",
                "sku": "RB-1",
                "quantity": 1,
                "unit_price": 1190,
                "gst_rate": 18.0,
                "item_total": 1190,
            }
        ],
    }


@pytest.fixture
def ctx(monkeypatch):
    """Wire the returns router with fake repos + collections + the QA order."""
    app = FastAPI()
    app.include_router(returns_router.router, prefix="/api/v1/returns")

    order_repo = _FakeOrderRepo(_qa_order())
    customer_repo = _FakeCustomerRepo()
    returns_coll = _FakeColl()
    ledger_coll = _FakeColl()

    class _FakeDB:
        is_connected = True

        def __init__(self):
            self.db = self

        def get_collection(self, name):
            return {
                "returns": returns_coll,
                "credit_note_ledger": ledger_coll,
            }.get(name, _FakeColl())

    fake_db = _FakeDB()

    monkeypatch.setattr(returns_router, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(
        returns_router, "get_customer_repository", lambda: customer_repo
    )
    monkeypatch.setattr(returns_router, "get_product_repository", lambda: None)
    # Stock repo absent -> restock records intent only (no live DB).
    monkeypatch.setattr(returns_router, "get_stock_repository", lambda: None)
    monkeypatch.setattr("api.dependencies.get_db", lambda: fake_db, raising=False)
    monkeypatch.setattr(
        "api.dependencies.get_audit_repository", lambda: None, raising=False
    )

    return {
        "client": TestClient(app),
        "order_repo": order_repo,
        "customer_repo": customer_repo,
        "returns_coll": returns_coll,
        "ledger_coll": ledger_coll,
    }


def _qa_payload(**over):
    """Full-item return on the QA order. unit_price is the NET line price; the
    handler recovers gst_rate=18 from the original order."""
    base = {
        "order_id": "ORD-BOK01",
        "order_number": "ORD-BOK01-2026-8E5731",
        "store_id": "BV-PUN-01",
        "return_type": "RETURN",
        "items": [
            {
                "order_item_id": "li1",
                "product_id": "PRD-1",
                "product_name": "Ray-Ban Sunglass",
                "sku": "RB-1",
                "return_qty": 1,
                "unit_price": 1190,
                "reason": "DEFECTIVE",
                "condition": "GOOD",
            }
        ],
    }
    base.update(over)
    return base


async def test_full_return_refunds_gst_inclusive_gross(ctx):
    """THE QA CASE: a full-item return must refund the GST-INCLUSIVE Rs 1404.20
    (1190 net + 214.20 GST), NOT the bare Rs 1190 net subtotal."""
    tok = _staff_token(["CASHIER"])
    r = ctx["client"].post(
        "/api/v1/returns",
        json=_qa_payload(),
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 201
    data = r.json()
    # Gross = what the customer paid; refund_amount = net (no fee) = gross.
    assert data["gross_refund"] == 1404.20
    assert data["returned_value"] == 1404.20
    assert data["refund_amount"] == 1404.20
    assert data["restocking_fee"] == 0.0
    assert data["net_refund"] == 1404.20
    # The GST back-out is auditable for the credit note / GSTR-1 reversal.
    assert data["gst_breakup"]["taxable"] == 1190.0
    assert data["gst_breakup"]["tax"] == 214.20
    # And the bug it replaces: it must NOT be the net 1190.
    assert data["refund_amount"] != 1190.0


async def test_partial_qty_refunds_proportional_gross(ctx):
    """A 1-of-2 return refunds the gross for the returned qty only."""
    tok = _staff_token(["CASHIER"])
    order = ctx["order_repo"]._order
    order["items"][0]["quantity"] = 2  # original sold 2 units
    payload = _qa_payload()
    payload["items"][0]["return_qty"] = 1  # return one of them
    r = ctx["client"].post(
        "/api/v1/returns",
        json=payload,
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 201
    data = r.json()
    # 1 * 1190 * 1.18 = 1404.20 (proportional to returned qty).
    assert data["gross_refund"] == 1404.20
    assert data["refund_amount"] == 1404.20


async def test_restocking_fee_deducts_from_gross(ctx):
    """Net refund = gross - restocking_fee for damaged / opened goods."""
    tok = _staff_token(["ADMIN"])
    payload = _qa_payload(restocking_fee=200)
    payload["items"][0]["condition"] = "OPENED"
    r = ctx["client"].post(
        "/api/v1/returns",
        json=payload,
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["gross_refund"] == 1404.20
    assert data["restocking_fee"] == 200.0
    # 1404.20 - 200 = 1204.20 net.
    assert data["net_refund"] == 1204.20
    assert data["refund_amount"] == 1204.20


async def test_restocking_fee_over_gross_is_422(ctx):
    """A restocking fee larger than the gross refund is rejected (422)."""
    tok = _staff_token(["ADMIN"])
    r = ctx["client"].post(
        "/api/v1/returns",
        json=_qa_payload(restocking_fee=99999),
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 422
    assert "restocking_fee" in r.json()["detail"]


async def test_restocking_fee_defaults_to_zero(ctx):
    """Omitting restocking_fee yields a full GST-inclusive refund."""
    tok = _staff_token(["CASHIER"])
    payload = _qa_payload()
    assert "restocking_fee" not in payload  # not sent
    r = ctx["client"].post(
        "/api/v1/returns",
        json=payload,
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["restocking_fee"] == 0.0
    assert data["net_refund"] == data["gross_refund"] == 1404.20


async def test_credit_note_ledger_records_gross_fee_net(ctx):
    """A CREDIT_NOTE with a restocking fee writes gross + fee + net onto the
    ledger row so the credit note / GSTR-1 reversal is fully auditable, and
    issues the NET amount as store credit."""
    tok = _staff_token(["STORE_MANAGER"])
    payload = _qa_payload(
        return_type="CREDIT_NOTE", customer_id="CUST-1", restocking_fee=204.20
    )
    r = ctx["client"].post(
        "/api/v1/returns",
        json=payload,
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 201
    data = r.json()
    # Net credit = 1404.20 - 204.20 = 1200.00.
    assert data["gross_refund"] == 1404.20
    assert data["restocking_fee"] == 204.20
    assert data["net_refund"] == 1200.0
    assert data["credit_amount"] == 1200.0
    # Ledger row carries the full gross / fee / net trail + ISSUED the net.
    assert len(ctx["ledger_coll"].docs) == 1
    entry = ctx["ledger_coll"].docs[0]
    assert entry["type"] == "ISSUED"
    assert entry["amount"] == 1200.0
    assert entry["gross_refund"] == 1404.20
    assert entry["restocking_fee"] == 204.20
    assert entry["net_refund"] == 1200.0
    assert entry["balance_after"] == 1200.0
    # Customer running balance bumped by the NET credit only.
    assert ctx["customer_repo"].customers["CUST-1"]["store_credit"] == 1200.0
    # The persisted return doc also records the gross / fee / net.
    rdoc = ctx["returns_coll"].docs[0]
    assert rdoc["gross_refund"] == 1404.20
    assert rdoc["restocking_fee"] == 204.20
    assert rdoc["net_refund"] == 1200.0


async def test_restocking_fee_rejected_on_exchange(ctx):
    """A restocking fee on an EXCHANGE is ambiguous (settled on the difference)
    and is rejected (422)."""
    tok = _staff_token(["ADMIN"])
    payload = _qa_payload(
        return_type="EXCHANGE",
        customer_id="CUST-1",
        restocking_fee=100,
        replacement_items=[
            {
                "product_id": "PRD-9",
                "name": "Oakley",
                "sku": "OK-9",
                "quantity": 1,
                "unit_price": 2000,
                "gst_rate": 18,
            }
        ],
    )
    r = ctx["client"].post(
        "/api/v1/returns",
        json=payload,
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 422
