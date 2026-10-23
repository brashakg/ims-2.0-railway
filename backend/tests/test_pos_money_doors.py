"""
IMS 2.0 - POS money doors (owner-approved POS fix, 2026-10-23)
==============================================================
Four verified holes, each pinned through the REAL route over strict fakes
(tests/strict_fakes.py raises on any Mongo feature it cannot emulate), asserting
the stored order / ledger / return rows -- never just a status code:

  1. LOYALTY tender on trust: add_payment recorded a LOYALTY leg with no points
     behind it, and the door was open to every logged-in role.
  2. A return's store credit could go to ANY customer_id in the body.
  3. Refunds of any size by cashier / sales staff; the approval matrix was dark
     and failed OPEN. Owner ruling 10-23: above Rs 2,000 needs a manager.
  4. A paid (or invoiced) order could be plain-cancelled, keeping the advance
     with no refund door.

No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import loyalty as L  # noqa: E402
from api.routers import orders as om  # noqa: E402
from api.routers import returns as returns_router  # noqa: E402
from api.routers.orders import PaymentCreate, add_payment, cancel_order  # noqa: E402
from api.services import rbac_policy  # noqa: E402
from api.services import refund_approval_matrix as ram  # noqa: E402
from database.repositories.loyalty_repository import (  # noqa: E402
    LoyaltyAccountRepository,
    LoyaltyTransactionRepository,
)
from database.repositories.order_repository import OrderRepository  # noqa: E402
from tests.strict_fakes import StrictDB  # noqa: E402

STORE = "BV-PUN-01"


def _user(role, uid="U1"):
    return {"user_id": uid, "username": uid, "roles": [role], "active_store_id": STORE}


def _order(order_id="ORD-1", grand_total=1000.0, **over):
    doc = {
        "order_id": order_id,
        "order_number": order_id,
        "store_id": STORE,
        "customer_id": "CUST-1",
        "customer_name": "Asha",
        "status": "CONFIRMED",
        "grand_total": grand_total,
        "amount_paid": 0.0,
        "balance_due": grand_total,
        "payment_status": "UNPAID",
        "payments": [],
        "items": [],
        "created_at": "2026-10-20T10:00:00",
    }
    doc.update(over)
    return doc


@pytest.fixture()
def till(monkeypatch):
    """One StrictDB behind the order, loyalty and returns doors."""
    db = StrictDB()
    orders = OrderRepository(db.get_collection("orders"))
    accounts = LoyaltyAccountRepository(db.get_collection("loyalty_accounts"))
    txns = LoyaltyTransactionRepository(db.get_collection("loyalty_transactions"))

    class _Conn:
        is_connected = True

    conn = _Conn()
    conn.db = db
    monkeypatch.setattr(om, "get_order_repository", lambda: orders)
    monkeypatch.setattr(om, "get_stock_repository", lambda: None)
    monkeypatch.setattr("api.dependencies.get_loyalty_transaction_repository", lambda: txns)
    monkeypatch.setattr(L, "get_loyalty_transaction_repository", lambda: txns)
    monkeypatch.setattr(L, "get_loyalty_account_repository", lambda: accounts)
    monkeypatch.setattr(L, "get_order_repository", lambda: orders)
    monkeypatch.setattr(L.loyalty_otp, "redeem_otp_required", lambda *_a, **_k: False)
    monkeypatch.setattr(L, "get_audit_repository", lambda: None)
    monkeypatch.setattr(returns_router, "get_order_repository", lambda: orders)
    monkeypatch.setattr("api.dependencies.get_db", lambda: conn, raising=False)
    monkeypatch.setattr("api.dependencies.get_audit_repository", lambda: None, raising=False)
    from api.services import audit_alerts, lens_stock_hook

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(audit_alerts, "alert_order_cancelled", _noop)
    monkeypatch.setattr(lens_stock_hook, "release_for_cancel", _noop)
    return {"db": db, "orders": orders, "accounts": accounts}


def _stored(till, order_id="ORD-1"):
    return till["db"].get_collection("orders").find_one({"order_id": order_id})


# =========================================================================== #
# 1. LOYALTY tender must be backed by real, unused points for THIS bill
# =========================================================================== #
@pytest.mark.asyncio
async def test_loyalty_tender_with_no_points_redeemed_is_refused(till):
    till["db"].get_collection("orders").insert_one(_order())
    with pytest.raises(HTTPException) as exc:
        await add_payment("ORD-1", PaymentCreate(method="LOYALTY", amount=300.0), _user("CASHIER"))
    assert exc.value.status_code == 400
    assert "Not enough loyalty points" in exc.value.detail
    doc = _stored(till)
    assert doc["payments"] == [] and doc["amount_paid"] == 0.0 and doc["balance_due"] == 1000.0


@pytest.mark.asyncio
async def test_customer_short_of_points_cannot_pay_by_loyalty(till):
    till["db"].get_collection("orders").insert_one(_order())
    till["db"].get_collection("loyalty_accounts").insert_one(
        {"customer_id": "CUST-1", "balance_points": 120, "lifetime_earned": 120, "tier": "BRONZE"}
    )
    with pytest.raises(HTTPException):
        await L.redeem(L.RedeemRequest(customer_id="CUST-1", order_id="ORD-1", points=300), _user("CASHIER"))
    with pytest.raises(HTTPException) as exc:
        await add_payment("ORD-1", PaymentCreate(method="LOYALTY", amount=300.0), _user("CASHIER"))
    assert exc.value.status_code == 400
    assert till["accounts"].find_by_id("CUST-1")["balance_points"] == 120
    assert _stored(till)["amount_paid"] == 0.0


@pytest.mark.asyncio
async def test_real_redeem_backs_exactly_one_loyalty_tender(till):
    till["db"].get_collection("orders").insert_one(_order())
    till["db"].get_collection("loyalty_accounts").insert_one(
        {"customer_id": "CUST-1", "balance_points": 500, "lifetime_earned": 500, "tier": "BRONZE"}
    )
    burned = await L.redeem(
        L.RedeemRequest(customer_id="CUST-1", order_id="ORD-1", points=300), _user("CASHIER")
    )
    assert burned["rupee_value"] == 300.0
    assert till["accounts"].find_by_id("CUST-1")["balance_points"] == 200

    res = await add_payment("ORD-1", PaymentCreate(method="LOYALTY", amount=300.0), _user("CASHIER"))
    doc = _stored(till)
    assert doc["amount_paid"] == 300.0 and doc["balance_due"] == 700.0
    assert doc["payments"][0]["loyalty_txn_id"] == burned["txn_id"]
    row = till["db"].get_collection("loyalty_transactions").find_one({"txn_id": burned["txn_id"]})
    assert row["tender_payment_id"] == res["payment_id"]

    # The same burn cannot back a second leg (replay / double tap).
    with pytest.raises(HTTPException) as exc:
        await add_payment("ORD-1", PaymentCreate(method="LOYALTY", amount=300.0), _user("CASHIER"))
    assert exc.value.status_code == 400
    assert _stored(till)["amount_paid"] == 300.0


@pytest.mark.asyncio
async def test_a_burn_on_another_order_cannot_pay_this_one(till):
    coll = till["db"].get_collection("orders")
    coll.insert_one(_order("ORD-1"))
    coll.insert_one(_order("ORD-2"))
    till["db"].get_collection("loyalty_accounts").insert_one(
        {"customer_id": "CUST-1", "balance_points": 500, "lifetime_earned": 500, "tier": "BRONZE"}
    )
    await L.redeem(L.RedeemRequest(customer_id="CUST-1", order_id="ORD-2", points=300), _user("CASHIER"))
    with pytest.raises(HTTPException):
        await add_payment("ORD-1", PaymentCreate(method="LOYALTY", amount=300.0), _user("CASHIER"))
    assert _stored(till, "ORD-1")["amount_paid"] == 0.0


@pytest.mark.parametrize(
    "role,allowed",
    [
        ("CASHIER", True),
        ("SALES_STAFF", True),
        ("STORE_MANAGER", True),
        ("OPTOMETRIST", True),
        ("WORKSHOP_STAFF", False),
        ("ACCOUNTANT", False),
        ("CATALOG_MANAGER", False),
    ],
)
@pytest.mark.asyncio
async def test_payment_role_matrix(till, role, allowed):
    till["db"].get_collection("orders").insert_one(_order())
    pay = PaymentCreate(method="CASH", amount=100.0)
    if allowed:
        await add_payment("ORD-1", pay, _user(role))
        assert _stored(till)["amount_paid"] == 100.0
    else:
        with pytest.raises(HTTPException) as exc:
            await add_payment("ORD-1", pay, _user(role))
        assert exc.value.status_code == 403
        assert _stored(till)["payments"] == []


def test_payment_policy_row_matches_the_in_function_gate():
    row = rbac_policy.policy_for("POST", "/api/v1/orders/ORD-1/payments")
    assert set(row["allowed"]) == set(om.PAYMENT_ROLES)


# =========================================================================== #
# 2 + 3. Returns: credit to the bill's customer only; > Rs 2,000 needs a manager
# =========================================================================== #
from tests.test_returns_refund_cap import _ctx  # noqa: E402
from tests.test_returns_gst_refund import _qa_payload, _staff_token  # noqa: E402


def _hdr(role):
    return {"Authorization": f"Bearer {_staff_token([role])}"}


def _two_units():
    # Two units of the QA line (net 1190 @ 18%) = Rs 2,808.40 gross: above Rs 2,000.
    p = _qa_payload()
    p["items"][0]["return_qty"] = 2
    return p


@pytest.mark.usefixtures("gst_numbers")
def test_store_credit_cannot_go_to_another_customer(monkeypatch):
    client, returns = _ctx(monkeypatch, amount_paid=2000.0)
    ledger = returns_router._ledger_coll()
    r = client.post(
        "/api/v1/returns",
        json=_qa_payload(return_type="CREDIT_NOTE", customer_id="CUST-OTHER"),
        headers=_hdr("STORE_MANAGER"),
    )
    assert r.status_code == 400, r.text
    assert "customer on the original bill" in r.text
    assert returns.count_documents({}) == 0
    assert ledger.count_documents({}) == 0


@pytest.mark.usefixtures("gst_numbers")
def test_store_credit_goes_to_the_bills_customer(monkeypatch):
    client, returns = _ctx(monkeypatch, amount_paid=2000.0)
    ledger = returns_router._ledger_coll()
    r = client.post(
        "/api/v1/returns",
        json=_qa_payload(return_type="CREDIT_NOTE", customer_id="CUST-1"),
        headers=_hdr("STORE_MANAGER"),
    )
    assert r.status_code == 201, r.text
    assert [d["customer_id"] for d in ledger.docs] == ["CUST-1"]
    assert returns.docs[0]["customer_id"] == "CUST-1"


@pytest.mark.usefixtures("gst_numbers")
@pytest.mark.parametrize("role", ["CASHIER", "SALES_STAFF"])
def test_refund_above_2000_by_sales_or_cashier_needs_a_manager(monkeypatch, role):
    client, returns = _ctx(monkeypatch, qty=2)
    r = client.post("/api/v1/returns", json=_two_units(), headers=_hdr(role))
    assert r.status_code == 403, r.text
    detail = r.json()["detail"]
    assert detail["reason"] == "REFUND_APPROVAL_REQUIRED"
    assert detail["required_tier"] == "auto"
    assert "Rs 2,000" in detail["message"]
    assert returns.count_documents({}) == 0


@pytest.mark.usefixtures("gst_numbers")
@pytest.mark.parametrize("role", ["STORE_MANAGER", "ADMIN"])
def test_refund_above_2000_by_a_manager_goes_through(monkeypatch, role):
    client, returns = _ctx(monkeypatch, qty=2)
    r = client.post("/api/v1/returns", json=_two_units(), headers=_hdr(role))
    assert r.status_code == 201, r.text
    assert returns.docs[0]["net_refund"] == pytest.approx(2808.4)


@pytest.mark.usefixtures("gst_numbers")
def test_refund_at_or_below_2000_by_a_cashier_is_unchanged(monkeypatch):
    client, returns = _ctx(monkeypatch)
    r = client.post("/api/v1/returns", json=_qa_payload(), headers=_hdr("CASHIER"))
    assert r.status_code == 201, r.text
    assert returns.docs[0]["net_refund"] == pytest.approx(1404.2)


@pytest.mark.usefixtures("gst_numbers")
def test_refund_gate_fails_closed_on_a_resolver_error(monkeypatch):
    client, returns = _ctx(monkeypatch)

    def _boom(*_a, **_k):
        raise RuntimeError("matrix unreadable")

    monkeypatch.setattr(ram, "required_tier", _boom)
    r = client.post("/api/v1/returns", json=_qa_payload(), headers=_hdr("CASHIER"))
    assert r.status_code == 503, r.text
    assert returns.count_documents({}) == 0


@pytest.mark.usefixtures("gst_numbers")
def test_unreadable_policy_still_gates(monkeypatch):
    # The switch and the stored matrix cannot be read: the gate stays ON with
    # the owner's code default -- never a free pass.
    from api.services import policy_engine

    def _boom(*_a, **_k):
        raise RuntimeError("policy store down")

    monkeypatch.setattr(policy_engine, "get_policy", _boom)
    client, returns = _ctx(monkeypatch, qty=2)
    r = client.post("/api/v1/returns", json=_two_units(), headers=_hdr("CASHIER"))
    assert r.status_code == 403, r.text
    assert returns.count_documents({}) == 0


def test_refund_request_routes_to_a_store_manager():
    # E4's generic refund bands would send a Rs 2,808 request to ADMIN; the
    # matrix action is a STORE_MANAGER's to approve (owner ruling).
    from api.services.approvals import ApprovalEngine

    db = StrictDB()
    res = ApprovalEngine(db=db).request(
        action_type="REFUND_APPROVAL_MATRIX",
        requested_by="U-cash",
        requested_by_roles=["CASHIER"],
        store_id=STORE,
        amount=2808.4,
        context={"order_id": "ORD-BOK01"},
    )
    assert res["ok"], res
    stored = db.get_collection("approval_requests").find_one({"request_id": res["request_id"]})
    assert stored["required_tier"] == "auto"
    assert "STORE_MANAGER" in stored["required_roles"]


# =========================================================================== #
# 4. Cancel never swallows money; the advance has a refund door
# =========================================================================== #
@pytest.mark.asyncio
async def test_unpaid_order_cancels_as_before(till):
    till["db"].get_collection("orders").insert_one(_order())
    out = await cancel_order("ORD-1", reason="customer changed mind", current_user=_user("SALES_STAFF"))
    assert out["status"] == "CANCELLED"
    assert _stored(till)["status"] == "CANCELLED"


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["SALES_STAFF", "STORE_MANAGER"])
async def test_paid_order_cannot_be_plain_cancelled(till, role):
    till["db"].get_collection("orders").insert_one(
        _order(amount_paid=1000.0, balance_due=4000.0, grand_total=5000.0,
               payments=[{"payment_id": "p1", "method": "CASH", "amount": 1000.0}])
    )
    with pytest.raises(HTTPException) as exc:
        await cancel_order("ORD-1", reason="customer changed mind", current_user=_user(role))
    assert exc.value.status_code == 400
    assert "Rs 1,000.00 was paid" in exc.value.detail
    assert _stored(till)["status"] == "CONFIRMED"


@pytest.mark.asyncio
async def test_invoiced_order_cannot_be_cancelled(till):
    till["db"].get_collection("orders").insert_one(_order(invoice_number="BOK01/2627/00007"))
    with pytest.raises(HTTPException) as exc:
        await cancel_order("ORD-1", reason="customer changed mind", current_user=_user("STORE_MANAGER"))
    assert exc.value.status_code == 400
    assert "BOK01/2627/00007" in exc.value.detail
    assert _stored(till)["status"] == "CONFIRMED"


@pytest.mark.asyncio
async def test_online_order_cancel_is_unchanged(till):
    # Shopify took the money and refunds it through Refund reviews: the
    # counter's money gate does not apply to an online order.
    till["db"].get_collection("orders").insert_one(
        _order(channel="ONLINE", shopify_order_id="SH-1", invoice_number="BOK01/2627/00011",
               amount_paid=1000.0, payment_status="PAID")
    )
    out = await cancel_order("ORD-1", reason="customer changed mind", current_user=_user("STORE_MANAGER"))
    assert out["status"] == "CANCELLED"


def _advance_client():
    app = FastAPI()
    app.include_router(returns_router.router, prefix="/api/v1/returns")
    return TestClient(app)


def _advance_order(till, **over):
    till["db"].get_collection("orders").insert_one(
        _order(amount_paid=1000.0, balance_due=4000.0, grand_total=5000.0, payment_status="PARTIAL",
               payments=[{"payment_id": "p1", "method": "CASH", "amount": 600.0},
                         {"payment_id": "p2", "method": "UPI", "amount": 400.0}], **over)
    )


@pytest.mark.parametrize("role", ["CASHIER", "SALES_STAFF", "OPTOMETRIST", "WORKSHOP_STAFF"])
def test_advance_refund_is_manager_only(till, role):
    _advance_order(till)
    r = _advance_client().post(
        "/api/v1/returns/advance-refund",
        json={"order_id": "ORD-1", "reason": "customer cancelled the order"},
        headers=_hdr(role),
    )
    assert r.status_code == 403, r.text
    assert till["db"].get_collection("returns").count_documents({}) == 0
    assert _stored(till)["status"] == "CONFIRMED"


def test_manager_refunds_the_advance_to_its_tenders_and_cancels(till):
    _advance_order(till)
    client = _advance_client()
    body = {"order_id": "ORD-1", "reason": "customer cancelled the order"}
    r = client.post("/api/v1/returns/advance-refund", json=body, headers=_hdr("STORE_MANAGER"))
    assert r.status_code == 201, r.text
    ret = till["db"].get_collection("returns").find_one({"order_id": "ORD-1"})
    assert ret["status"] == "COMPLETED" and ret["return_type"] == "RETURN"
    assert ret["net_refund"] == 1000.0
    assert sorted((t["method"], t["amount"]) for t in ret["refund_tenders"]) == [("CASH", 600.0), ("UPI", 400.0)]
    assert ret["gst_breakup"] == {"taxable": 0.0, "tax": 0.0}  # no invoice, no tax reversed
    assert _stored(till)["status"] == "CANCELLED"
    assert r.json()["order_status"] == "CANCELLED"

    # A second press refunds nothing more.
    r2 = client.post("/api/v1/returns/advance-refund", json=body, headers=_hdr("STORE_MANAGER"))
    assert r2.status_code == 400, r2.text
    assert till["db"].get_collection("returns").count_documents({"order_id": "ORD-1"}) == 1


def test_advance_refund_refuses_an_invoiced_order(till):
    _advance_order(till, invoice_number="BOK01/2627/00009")
    r = _advance_client().post(
        "/api/v1/returns/advance-refund",
        json={"order_id": "ORD-1", "reason": "customer cancelled the order"},
        headers=_hdr("STORE_MANAGER"),
    )
    assert r.status_code == 400, r.text
    assert "credit note" in r.text
    assert till["db"].get_collection("returns").count_documents({}) == 0
    assert _stored(till)["status"] == "CONFIRMED"


@pytest.mark.asyncio
async def test_after_a_full_refund_only_a_manager_may_cancel(till):
    _advance_order(till)
    till["db"].get_collection("returns").insert_one(
        {"return_id": "RET-X", "order_id": "ORD-1", "status": "COMPLETED", "net_refund": 1000.0}
    )
    with pytest.raises(HTTPException) as exc:
        await cancel_order("ORD-1", reason="customer cancelled the order", current_user=_user("SALES_STAFF"))
    assert exc.value.status_code == 403
    assert _stored(till)["status"] == "CONFIRMED"
    out = await cancel_order("ORD-1", reason="customer cancelled the order", current_user=_user("STORE_MANAGER"))
    assert out["status"] == "CANCELLED"
