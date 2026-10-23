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
from database.repositories.customer_repository import CustomerRepository  # noqa: E402
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
    monkeypatch.setattr(
        returns_router, "get_customer_repository",
        lambda: CustomerRepository(db.get_collection("customers")),
    )
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
        ("ACCOUNTANT", True),  # owner 2026-10-23: accounts collect credit bills
        ("WORKSHOP_STAFF", False),
        ("CATALOG_MANAGER", False),
        ("DESIGN_MANAGER", False),
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


@pytest.mark.asyncio
async def test_accountant_cannot_record_a_loyalty_tender(till):
    # Owner 2026-10-23: ACCOUNTANT records money, never points -- even when a
    # real burn sits on the bill, and the burn stays unclaimed for the till.
    till["db"].get_collection("orders").insert_one(_order())
    till["db"].get_collection("loyalty_accounts").insert_one(
        {"customer_id": "CUST-1", "balance_points": 500, "lifetime_earned": 500, "tier": "BRONZE"}
    )
    burned = await L.redeem(L.RedeemRequest(customer_id="CUST-1", order_id="ORD-1", points=300), _user("CASHIER"))
    with pytest.raises(HTTPException) as exc:
        await add_payment("ORD-1", PaymentCreate(method="LOYALTY", amount=300.0), _user("ACCOUNTANT"))
    assert exc.value.status_code == 403
    assert _stored(till)["payments"] == []
    row = till["db"].get_collection("loyalty_transactions").find_one({"txn_id": burned["txn_id"]})
    assert row.get("tender_payment_id") is None
    # The till can still use that burn.
    await add_payment("ORD-1", PaymentCreate(method="LOYALTY", amount=300.0), _user("CASHIER"))
    assert _stored(till)["amount_paid"] == 300.0


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


_ADV_BODY = {"order_id": "ORD-1", "reason": "customer cancelled the order", "cash_paid_from": "STORE_DRAWER"}
_LINE = {"item_id": "li1", "product_id": "P1", "product_name": "Frame", "sku": "F1", "quantity": 1,
         "unit_price": 4761.90, "gst_rate": 5.0, "taxable_value": 4761.90, "tax_amount": 238.10}


def _ledger(till):
    return till["db"].get_collection("credit_note_ledger")


@pytest.mark.parametrize(
    "role", ["CASHIER", "SALES_STAFF", "STORE_MANAGER", "OPTOMETRIST", "WORKSHOP_STAFF"]
)
def test_advance_refund_is_accounts_and_admin_only(till, role):
    # Owner 2026-10-23: ACCOUNTANT / ADMIN / SUPERADMIN only -- not store managers.
    _advance_order(till)
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr(role))
    assert r.status_code == 403, r.text
    assert till["db"].get_collection("returns").count_documents({}) == 0
    assert _stored(till)["status"] == "CONFIRMED"


def test_advance_refund_has_its_own_capability_key():
    from api.services import capabilities as C

    assert C.capability_for("POST", "/api/v1/returns/advance-refund") == "returns:advance-refund"
    assert C.PARENT_CAPABILITY["returns:advance-refund"] == "returns:write"
    # ACCOUNTANT never joins the counter-returns grant union.
    assert "ACCOUNTANT" not in C._CAP_ROLE_HINT["returns:write"]
    row = rbac_policy.policy_for("POST", "/api/v1/returns/advance-refund")
    assert set(row["allowed"]) == set(returns_router._ADVANCE_REFUND_ROLES)


@pytest.mark.parametrize("role", ["ACCOUNTANT", "ADMIN"])
def test_accounts_refunds_the_advance_to_its_tenders_and_cancels(till, role):
    _advance_order(till)
    client = _advance_client()
    r = client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr(role))
    assert r.status_code == 201, r.text
    ret = till["db"].get_collection("returns").find_one({"order_id": "ORD-1"})
    assert ret["status"] == "COMPLETED" and ret["return_type"] == "RETURN"
    assert ret["net_refund"] == 1000.0
    assert sorted((t["method"], t["amount"]) for t in ret["refund_tenders"]) == [("CASH", 600.0), ("UPI", 400.0)]
    assert ret["gst_breakup"] == {"taxable": 0.0, "tax": 0.0}  # no invoice, no tax reversed
    assert ret["credit_note_number"] is None
    assert _ledger(till).count_documents({}) == 0
    assert _stored(till)["status"] == "CANCELLED"
    assert r.json()["order_status"] == "CANCELLED"

    # A second press refunds nothing more.
    r2 = client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr(role))
    assert r2.status_code == 400, r2.text
    assert till["db"].get_collection("returns").count_documents({"order_id": "ORD-1"}) == 1


@pytest.mark.usefixtures("gst_numbers")
def test_invoiced_part_advance_gets_a_whole_invoice_credit_note_and_only_the_advance_back(till):
    # Owner 2026-10-23: Rs 5,000 invoice, Rs 1,000 paid -> a credit note for the
    # WHOLE invoice (all its tax reversed), Rs 1,000 refunded, order cancelled.
    _advance_order(till, invoice_number="BOK01/2627/00009", items=[dict(_LINE)])
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 201, r.text
    ret = till["db"].get_collection("returns").find_one({"order_id": "ORD-1"})
    assert ret["credit_note_number"] == "TST01/C2627/0001"
    assert ret["gst_breakup"]["tax"] == pytest.approx(238.10)
    assert ret["gst_breakup"]["taxable"] == pytest.approx(4761.90)
    assert ret["credit_note_value"] == pytest.approx(5000.0)
    assert ret["net_refund"] == 1000.0 and ret["refund_amount"] == 1000.0
    assert sum(t["amount"] for t in ret["refund_tenders"]) == 1000.0
    assert [it["return_qty"] for it in ret["items"]] == [1]
    assert all(it["restock"] is False for it in ret["items"])  # the cancel restocks, not the note
    assert _stored(till)["status"] == "CANCELLED"


@pytest.mark.parametrize("method", ["STORE_CREDIT", "GIFT_VOUCHER", "EMI"])
def test_store_credit_voucher_or_emi_advance_comes_back_as_store_credit(till, method):
    # Owner 2026-10-23: ALWAYS as store credit to the bill's own customer.
    till["db"].get_collection("customers").insert_one({"customer_id": "CUST-1", "name": "Asha", "store_credit": 50.0})
    till["db"].get_collection("orders").insert_one(
        _order(amount_paid=1000.0, balance_due=4000.0, grand_total=5000.0, payment_status="PARTIAL",
               payments=[{"payment_id": "p1", "method": method, "amount": 700.0},
                         {"payment_id": "p2", "method": "CASH", "amount": 300.0}])
    )
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 201, r.text
    rows = list(_ledger(till).find({}))
    assert len(rows) == 1
    row = rows[0]
    assert row["customer_id"] == "CUST-1" and row["type"] == "ADJUSTED"  # money back, not a 2nd credit note
    assert row["delta"] == 700.0 and row["balance_after"] == 750.0
    assert row["order_id"] == "ORD-1" and row["created_by"] == "u1"
    cust = till["db"].get_collection("customers").find_one({"customer_id": "CUST-1"})
    assert cust["store_credit"] == 750.0
    ret = till["db"].get_collection("returns").find_one({"order_id": "ORD-1"})
    assert sorted((t["method"], t["amount"]) for t in ret["refund_tenders"]) == [("CASH", 300.0), ("STORE_CREDIT", 700.0)]
    assert _stored(till)["status"] == "CANCELLED"


def test_store_credit_advance_on_a_walk_in_is_refused(till):
    till["db"].get_collection("orders").insert_one(
        _order(customer_id="walkin-1", amount_paid=500.0, payment_status="PARTIAL",
               payments=[{"payment_id": "p1", "method": "GIFT_VOUCHER", "amount": 500.0}])
    )
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ADMIN"))
    assert r.status_code == 400, r.text
    assert _ledger(till).count_documents({}) == 0
    assert till["db"].get_collection("returns").count_documents({}) == 0
    assert _stored(till)["status"] == "CONFIRMED"


def test_a_failed_cancel_is_finished_by_pressing_again(till, monkeypatch):
    from api.routers.orders import cancel as cancel_mod

    _advance_order(till)
    real = cancel_mod.cancel_order_core

    async def _fails_once(*_a, **_k):
        raise HTTPException(status_code=500, detail="Could not cancel the order -- please retry.")

    monkeypatch.setattr(cancel_mod, "cancel_order_core", _fails_once)
    client = _advance_client()
    r = client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 201 and r.json()["cancel_error"], r.text
    assert _stored(till)["status"] == "CONFIRMED"

    monkeypatch.setattr(cancel_mod, "cancel_order_core", real)
    r2 = client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r2.status_code == 201, r2.text
    assert _stored(till)["status"] == "CANCELLED"
    assert till["db"].get_collection("returns").count_documents({"order_id": "ORD-1"}) == 1  # refunded once


def test_preview_names_the_amount_and_method_or_the_reason(till):
    _advance_order(till)
    client = _advance_client()
    ok = client.get("/api/v1/returns/advance-refund/ORD-1", headers=_hdr("ACCOUNTANT")).json()
    assert ok["ok"] is True and ok["total"] == 1000.0
    assert sorted((t["method"], t["amount"]) for t in ok["refund_tenders"]) == [("CASH", 600.0), ("UPI", 400.0)]
    till["db"].get_collection("orders").insert_one(_order("ORD-2"))
    no = client.get("/api/v1/returns/advance-refund/ORD-2", headers=_hdr("ACCOUNTANT")).json()
    assert no["ok"] is False and "No money was taken" in no["reason"]
    assert client.get("/api/v1/returns/advance-refund/ORD-1", headers=_hdr("STORE_MANAGER")).status_code == 403


@pytest.mark.asyncio
async def test_after_a_full_refund_only_accounts_or_admin_may_cancel(till):
    _advance_order(till)
    till["db"].get_collection("returns").insert_one(
        {"return_id": "RET-X", "order_id": "ORD-1", "status": "COMPLETED", "net_refund": 1000.0}
    )
    for role in ("SALES_STAFF", "STORE_MANAGER"):
        with pytest.raises(HTTPException) as exc:
            await cancel_order("ORD-1", reason="customer cancelled the order", current_user=_user(role))
        assert exc.value.status_code == 403
    assert _stored(till)["status"] == "CONFIRMED"
    out = await cancel_order("ORD-1", reason="customer cancelled the order", current_user=_user("ADMIN"))
    assert out["status"] == "CANCELLED"


# =========================================================================== #
# Money review of #1264 (2026-10-23)
# =========================================================================== #
@pytest.mark.usefixtures("gst_numbers")
def test_split_refunds_cannot_dodge_the_2000_rule(monkeypatch):
    # Rs 2,808 bill returned one unit at a time: Rs 1,404 alone is under the
    # line, but the second return takes the order's refunds past Rs 2,000.
    client, returns = _ctx(monkeypatch, qty=2)
    r1 = client.post("/api/v1/returns", json=_qa_payload(), headers=_hdr("CASHIER"))
    assert r1.status_code == 201, r1.text
    r2 = client.post("/api/v1/returns", json=_qa_payload(), headers=_hdr("CASHIER"))
    assert r2.status_code == 403, r2.text
    assert r2.json()["detail"]["reason"] == "REFUND_APPROVAL_REQUIRED"
    assert returns.count_documents({}) == 1


def _stored_matrix(monkeypatch, matrix):
    from api.services import policy_engine

    def _get(key, scope=None, *, default=None):
        return matrix if key == ram.MATRIX_KEY else True

    monkeypatch.setattr(policy_engine, "get_policy", _get)


def test_an_old_shape_saved_matrix_is_ignored(monkeypatch):
    legacy = {
        "currency": "INR",
        "role_floor_paise": {"CASHIER": 0, "SALES_STAFF": 0, "STORE_MANAGER": 500000,
                             "AREA_MANAGER": 2000000, "ADMIN": None, "SUPERADMIN": None},
        "bands": [{"max_paise": 100000, "tier": 0}, {"max_paise": 500000, "tier": 1},
                  {"max_paise": 2000000, "tier": 2}, {"max_paise": None, "tier": 3}],
        "reason_bump": {"DEFECTIVE": 0, "CHANGE_OF_MIND": 0, "PRICE_MATCH": 1, "GOODWILL": 1},
        "max_tier": 3,
    }
    _stored_matrix(monkeypatch, legacy)
    # The owner rule, not the old bands: Rs 1,500 by a cashier needs nobody,
    # Rs 2,500 needs a store manager (not an admin), a manager is never gated.
    assert ram.required_tier_for_refund(150000, "GOODWILL", "CASHIER") is None
    assert ram.required_tier_for_refund(250000, "GOODWILL", "CASHIER") == "auto"
    assert ram.required_tier_for_refund(900000, "DEFECTIVE", "STORE_MANAGER") is None


def test_a_saved_matrix_can_never_loosen_the_owner_rule(monkeypatch):
    _stored_matrix(monkeypatch, {"bands": [{"max_paise": None, "tier": 0}], "max_tier": 3})
    assert ram.required_tier_for_refund(250000, "DEFECTIVE", "CASHIER") == "auto"


def test_advance_claim_refuses_when_a_payment_lands_mid_refund(till, monkeypatch):
    _advance_order(till)
    orders = till["orders"]
    real_find = orders.find_by_id

    def _read_then_payment_lands(oid):
        doc = real_find(oid)
        till["db"].get_collection("orders").update_one(
            {"order_id": oid}, {"$set": {"amount_paid": 1500.0}})
        return doc

    monkeypatch.setattr(orders, "find_by_id", _read_then_payment_lands)
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 409, r.text
    assert till["db"].get_collection("returns").count_documents({}) == 0
    assert _stored(till).get("advance_refund_id") is None


def test_two_presses_at_once_refund_once(till, monkeypatch):
    # The other press won the claim: this one records nothing.
    _advance_order(till)
    monkeypatch.setattr(till["orders"], "update_if", lambda *a, **k: False)
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ADMIN"))
    assert r.status_code == 409, r.text
    assert till["db"].get_collection("returns").count_documents({}) == 0
    assert _ledger(till).count_documents({}) == 0
    assert _stored(till)["status"] == "CONFIRMED"


def test_advance_door_refuses_when_a_counter_return_claimed_units_first(till):
    # A counter return reserved units (its doc not written yet): the door
    # sees the reservation after its own claim and backs out.
    _advance_order(till, items=[dict(_LINE, returned_qty=1)])
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ADMIN"))
    assert r.status_code == 409, r.text
    assert till["db"].get_collection("returns").count_documents({}) == 0
    assert _stored(till).get("advance_refund_id") is None


@pytest.mark.usefixtures("gst_numbers")
def test_counter_return_refused_while_an_advance_refund_holds_the_order(monkeypatch):
    client, returns = _ctx(monkeypatch, advance_refund_id="RET-ADV-1")
    r = client.post("/api/v1/returns", json=_qa_payload(), headers=_hdr("STORE_MANAGER"))
    assert r.status_code == 409, r.text
    assert returns.count_documents({}) == 0


def test_cancel_crash_after_refund_says_press_again(till, monkeypatch):
    from api.routers.orders import cancel as cancel_mod

    _advance_order(till)

    async def _boom(*_a, **_k):
        raise RuntimeError("mongo blip")

    monkeypatch.setattr(cancel_mod, "cancel_order_core", _boom)
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["refund_amount"] == 1000.0 and body["cancel_error"]
    assert "press" in body["message"].lower()
    assert till["db"].get_collection("returns").count_documents({"order_id": "ORD-1"}) == 1


@pytest.mark.asyncio
async def test_loyalty_claim_is_released_when_the_payment_write_fails(till, monkeypatch):
    till["db"].get_collection("orders").insert_one(_order())
    till["db"].get_collection("loyalty_accounts").insert_one(
        {"customer_id": "CUST-1", "balance_points": 500, "lifetime_earned": 500, "tier": "BRONZE"}
    )
    burned = await L.redeem(L.RedeemRequest(customer_id="CUST-1", order_id="ORD-1", points=300), _user("CASHIER"))
    monkeypatch.setattr(till["orders"], "add_payment", lambda *a, **k: False)
    with pytest.raises(HTTPException):
        await add_payment("ORD-1", PaymentCreate(method="LOYALTY", amount=300.0), _user("CASHIER"))
    row = till["db"].get_collection("loyalty_transactions").find_one({"txn_id": burned["txn_id"]})
    assert row.get("tender_payment_id") is None


@pytest.mark.asyncio
async def test_same_key_loyalty_twin_replays_instead_of_refusing(till):
    # Two identical requests (same Idempotency-Key) racing: the first claimed
    # the burn but its payment row is not on the order yet.
    till["db"].get_collection("orders").insert_one(_order())
    till["db"].get_collection("loyalty_accounts").insert_one(
        {"customer_id": "CUST-1", "balance_points": 500, "lifetime_earned": 500, "tier": "BRONZE"}
    )
    burned = await L.redeem(L.RedeemRequest(customer_id="CUST-1", order_id="ORD-1", points=300), _user("CASHIER"))
    till["db"].get_collection("loyalty_transactions").update_one(
        {"txn_id": burned["txn_id"]},
        {"$set": {"tender_payment_id": "pay-twin", "tender_idempotency_key": "idem-L"}},
    )
    out = await add_payment(
        "ORD-1", PaymentCreate(method="LOYALTY", amount=300.0), _user("CASHIER"), idempotency_key="idem-L"
    )
    assert out["payment_id"] == "pay-twin" and out["_idempotent_replay"] is True
    assert _stored(till)["payments"] == []  # the twin records it, not this request


def test_backfill_links_old_redemptions_to_their_payment_only_by_reference(tmp_path):
    from scripts import backfill_loyalty_tender_claims as bf

    db = StrictDB()
    db.seed("orders", [
        _order("ORD-1", payments=[{"payment_id": "p1", "method": "LOYALTY", "amount": 300.0,
                                   "reference": "300pts txn T-1"}]),
        _order("ORD-2", payments=[{"payment_id": "p2", "method": "LOYALTY", "amount": 200.0,
                                   "reference": "no txn named"}]),
    ])
    db.seed("loyalty_transactions", [
        {"txn_id": "T-1", "order_id": "ORD-1", "customer_id": "CUST-1", "type": "REDEEM", "rupee_value": 300.0},
        {"txn_id": "T-2", "order_id": "ORD-2", "customer_id": "CUST-1", "type": "REDEEM", "rupee_value": 200.0},
        {"txn_id": "T-3", "order_id": "ORD-1", "customer_id": "CUST-1", "type": "REDEEM", "rupee_value": 300.0},
    ])
    out = bf.plan(db)
    assert out["links"] == [("T-1", "p1")] and out["unmatched"] == 2
    # Dry run wrote nothing.
    assert db.get_collection("loyalty_transactions").find_one({"txn_id": "T-1"}).get("tender_payment_id") is None
    backup = tmp_path / "b.json"
    assert bf.apply(db, out["links"], str(backup)) == 1
    rows = db.get_collection("loyalty_transactions")
    assert rows.find_one({"txn_id": "T-1"})["tender_payment_id"] == "p1"
    assert rows.find_one({"txn_id": "T-2"}).get("tender_payment_id") is None
    assert rows.find_one({"txn_id": "T-3"}).get("tender_payment_id") is None
    assert "T-1" in backup.read_text()
    assert bf.plan(db)["links"] == []  # idempotent


# =========================================================================== #
# Fail closed: an unreadable refund history refuses the refund (owner rule)
# =========================================================================== #
class _ReadFails:
    def find(self, *_a, **_k):
        raise RuntimeError("returns read failed")


def test_strict_prior_refunds_refuses_on_a_read_error(monkeypatch):
    monkeypatch.setattr(returns_router, "_returns_coll", lambda: _ReadFails())
    with pytest.raises(HTTPException) as exc:
        returns_router._sum_prior_refunds("ORD-1", strict=True)
    assert exc.value.status_code == 503
    assert "Could not check earlier refunds on this bill - try again" in str(exc.value.detail)
    # Other callers keep the old fail-soft reading.
    assert returns_router._sum_prior_refunds("ORD-1") == 0.0


@pytest.mark.usefixtures("gst_numbers")
def test_return_refused_when_earlier_refunds_cannot_be_read(monkeypatch):
    # The line-quantity read succeeds; the read of the bill's earlier refunds
    # (the Rs 2,000 rule and the over-refund cap) fails -> refused, nothing kept.
    client, returns = _ctx(monkeypatch)
    real_find = returns.find
    calls = {"n": 0}

    def _find(*a, **k):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("returns read failed")
        return real_find(*a, **k)

    monkeypatch.setattr(returns, "find", _find)
    r = client.post("/api/v1/returns", json=_qa_payload(), headers=_hdr("CASHIER"))
    assert r.status_code == 503, r.text
    assert "Could not check earlier refunds on this bill - try again" in r.text
    assert returns.count_documents({}) == 0


# =========================================================================== #
# Round-2 money review (head dd003bd3)
# =========================================================================== #
def test_a_cancelled_order_with_an_invoice_is_still_a_sale():
    # B1: its credit note does the reversal; dropping the invoice as well
    # reversed the tax twice.
    from api.utils.real_sale import is_real_sale, real_sale_match
    from tests.strict_fakes import matches

    db = StrictDB()
    db.seed("orders", [
        {"order_id": "A", "status": "CANCELLED", "invoice_number": "BOK01/2627/00009"},
        {"order_id": "B", "status": "CANCELED", "invoice_number": "BOK01/2627/00010"},
        {"order_id": "C", "status": "CANCELLED", "invoice_number": None},
        {"order_id": "D", "status": "cancelled"},
        {"order_id": "E", "status": "CONFIRMED"},
    ])
    flt = real_sale_match(db)
    rows = list(db.get_collection("orders").find({}))
    assert sorted(o["order_id"] for o in rows if matches(o, flt)) == ["A", "B", "E"]
    assert [o["order_id"] for o in rows if is_real_sale(o)] == ["A", "B", "E"]


def _gst_flow(till):
    """Invoice Rs 5,000 (tax 238.10), Rs 1,000 advance, then the advance door:
    whole-invoice credit note, order cancelled. Same month for both."""
    from datetime import datetime, timezone

    till["db"].seed("stores", [{"store_id": STORE, "gstin": "27AAACR0000A1ZZ", "store_name": "Pune",
                                "state": "Maharashtra"}])
    till["db"].seed("customers", [{"customer_id": "CUST-1", "name": "Asha", "state": "Maharashtra"}])
    line = dict(_LINE, hsn_code="900311", item_total=5000.0)
    _advance_order(till, invoice_number="BOK01/2627/00009", items=[line], tax_amount=238.10,
                   created_at=datetime.now(timezone.utc).replace(tzinfo=None))
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 201, r.text
    assert _stored(till)["status"] == "CANCELLED"
    from api.utils.ist import ist_today
    return ist_today().strftime("%Y-%m")


@pytest.mark.usefixtures("gst_numbers")
def test_gstr1_files_the_invoice_and_its_whole_credit_note_once(till, monkeypatch):
    import api.routers.reports as rep_mod

    month = _gst_flow(till)
    monkeypatch.setattr(rep_mod, "_get_raw_db", lambda: till["db"])
    rep = rep_mod._compute_gstr1(month, STORE)
    sales_tax = round(sum(b.get("cgst", 0) + b.get("sgst", 0) + b.get("igst", 0) for b in rep["b2cs"]), 2)
    cn_tax = round(sum(c["taxValue"] for c in rep["cdnr"]), 2)
    assert sales_tax == pytest.approx(238.10)   # the invoice is still filed
    assert cn_tax == pytest.approx(238.10)      # its credit note reverses it, once
    assert len(rep["cdnr"]) == 1


@pytest.mark.usefixtures("gst_numbers")
def test_gstr3b_nets_the_invoice_and_its_credit_note_to_zero(till, monkeypatch):
    import api.routers.reports as rep_mod

    month = _gst_flow(till)
    monkeypatch.setattr(rep_mod, "_get_raw_db", lambda: till["db"])
    rep = rep_mod._compute_gstr3b(month, STORE)
    t = rep["outwardTaxableSupplies"]
    assert round(t["centralTax"] + t["stateTax"] + t["integratedTax"], 2) == pytest.approx(0.0, abs=0.01)
    assert rep["outwardTaxableValue"] == pytest.approx(0.0, abs=0.01)
    # ...because the invoice's 238.10 met its credit note's 238.10 -- not
    # because the invoice vanished and the note rolls forward to next month.
    cn = rep["creditNotes"]
    assert round(cn["centralTax"] + cn["stateTax"] + cn["integratedTax"], 2) == pytest.approx(238.10)
    carry = rep.get("creditNoteCarryForward") or {}
    assert round(sum(float(v or 0) for k, v in carry.items() if k != "taxableValue"), 2) == 0.0


# --- B2: a refunded order takes no more money and is never handed over ------
@pytest.mark.asyncio
async def test_no_payment_on_an_order_whose_advance_was_refunded(till):
    _advance_order(till, advance_refund_id="RET-ADV-1")
    with pytest.raises(HTTPException) as exc:
        await add_payment("ORD-1", PaymentCreate(method="CASH", amount=100.0), _user("CASHIER"))
    assert exc.value.status_code == 409
    assert len(_stored(till)["payments"]) == 2 and _stored(till)["amount_paid"] == 1000.0


@pytest.mark.asyncio
async def test_no_handover_of_an_order_whose_advance_was_refunded(till):
    from api.routers.orders import deliver_order, deliver_with_payment
    from api.routers.orders.delivery import DeliverWithPaymentRequest

    _advance_order(till, advance_refund_id="RET-ADV-1", status="READY")
    with pytest.raises(HTTPException) as exc:
        await deliver_order("ORD-1", None, _user("STORE_MANAGER"))
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc2:
        await deliver_with_payment(
            "ORD-1", DeliverWithPaymentRequest(payment=PaymentCreate(method="CASH", amount=100.0)),
            _user("STORE_MANAGER"))
    assert exc2.value.status_code == 409
    doc = _stored(till)
    assert doc["status"] == "READY" and doc["amount_paid"] == 1000.0


@pytest.mark.asyncio
async def test_cancel_guard_reads_earlier_refunds_strictly(till, monkeypatch):
    _advance_order(till)
    monkeypatch.setattr(returns_router, "_returns_coll", lambda: _ReadFails())
    with pytest.raises(HTTPException) as exc:
        await cancel_order("ORD-1", reason="customer cancelled the order", current_user=_user("ADMIN"))
    assert exc.value.status_code == 503
    assert "Could not check earlier refunds" in str(exc.value.detail)
    assert _stored(till)["status"] == "CONFIRMED"


def test_preview_offers_finish_cancel_after_a_failed_cancel(till, monkeypatch):
    from api.routers.orders import cancel as cancel_mod

    _advance_order(till)

    async def _fails(*_a, **_k):
        raise HTTPException(status_code=500, detail="Could not cancel the order -- please retry.")

    monkeypatch.setattr(cancel_mod, "cancel_order_core", _fails)
    client = _advance_client()
    assert client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT")).status_code == 201
    plan = client.get("/api/v1/returns/advance-refund/ORD-1", headers=_hdr("ACCOUNTANT")).json()
    assert plan["ok"] is True and plan["action"] == "finish_cancel"


# --- M1: no burned credit-note numbers --------------------------------------
@pytest.mark.usefixtures("gst_numbers")
def test_no_credit_note_number_is_taken_when_the_store_credit_fails(till, monkeypatch):
    minted = []
    real_mint = returns_router.next_document_number

    def _spy(*a, **k):
        n = real_mint(*a, **k)
        minted.append(n)
        return n

    monkeypatch.setattr(returns_router, "next_document_number", _spy)
    monkeypatch.setattr(returns_router, "_credit_back_advance", lambda *a, **k: None)
    till["db"].get_collection("orders").insert_one(
        _order(invoice_number="BOK01/2627/00009", items=[dict(_LINE)], amount_paid=1000.0,
               payment_status="PARTIAL", grand_total=5000.0,
               payments=[{"payment_id": "p1", "method": "GIFT_VOUCHER", "amount": 1000.0}])
    )
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 503, r.text
    assert minted == []
    assert till["db"].get_collection("returns").count_documents({}) == 0


@pytest.mark.usefixtures("gst_numbers")
def test_a_number_burned_by_a_failed_save_is_recorded_as_unused(till, monkeypatch):
    audits = []

    class _Audit:
        def create(self, row):
            audits.append(row)

    monkeypatch.setattr("api.dependencies.get_audit_repository", lambda: _Audit())
    _advance_order(till, invoice_number="BOK01/2627/00009", items=[dict(_LINE)])
    rcoll = till["db"].get_collection("returns")

    def _boom(_doc):
        raise RuntimeError("insert failed")

    monkeypatch.setattr(rcoll, "insert_one", _boom)
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 503, r.text
    unused = [a for a in audits if a.get("action") == "CREDIT_NOTE_NUMBER_UNUSED"]
    assert len(unused) == 1 and unused[0]["details"]["unused_credit_note_number"] == "TST01/C2627/0001"
    assert _stored(till).get("advance_refund_id") is None


# --- M2: a crash between the claim and the record is undone on the next press
def _stale(till, *, minutes_ago):
    from datetime import datetime, timedelta, timezone

    at = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    till["db"].get_collection("customers").insert_one({"customer_id": "CUST-1", "name": "Asha", "store_credit": 750.0})
    till["db"].get_collection("orders").insert_one(
        _order(amount_paid=1000.0, payment_status="PARTIAL", advance_refund_id="RET-STALE",
               advance_refund_at=at,
               payments=[{"payment_id": "p1", "method": "STORE_CREDIT", "amount": 700.0},
                         {"payment_id": "p2", "method": "CASH", "amount": 300.0}])
    )
    _ledger(till).insert_one({"entry_id": "e1", "customer_id": "CUST-1", "type": "ADJUSTED", "amount": 700.0,
                              "delta": 700.0, "balance_after": 750.0, "ref": "RET-STALE",
                              "order_id": "ORD-1", "source": "ADVANCE_REFUND"})


def test_a_crashed_press_is_undone_then_refunded_once(till):
    _stale(till, minutes_ago=10)
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 201, r.text
    rows = list(_ledger(till).find({"customer_id": "CUST-1"}))
    assert sorted(x["delta"] for x in rows) == [-700.0, 700.0, 700.0]  # stale, its reversal, the real one
    assert till["db"].get_collection("customers").find_one({"customer_id": "CUST-1"})["store_credit"] == 750.0
    assert till["db"].get_collection("returns").count_documents({"order_id": "ORD-1"}) == 1
    assert _stored(till)["status"] == "CANCELLED"


def test_a_press_still_in_flight_is_not_undone(till):
    _stale(till, minutes_ago=0)
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 409, r.text
    assert _ledger(till).count_documents({}) == 1
    assert _stored(till)["advance_refund_id"] == "RET-STALE"


# --- M3: cash paid from the shop drawer vs from HQ ---------------------------
def test_drawer_cash_refund_carries_a_cash_count_block(till):
    _advance_order(till)
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 201, r.text
    ret = till["db"].get_collection("returns").find_one({"order_id": "ORD-1"})
    cash = [t for t in ret["refund_tenders"] if t["method"] == "CASH"][0]
    assert cash["cash_count"]["state"] == "NOT_CAPTURED"
    assert ret["cash_paid_from"] == "STORE_DRAWER" and ret["drawer_auto_netted"] is True


def test_hq_cash_refund_never_touches_the_shop_drawer(till):
    _advance_order(till)
    body = {**_ADV_BODY, "cash_paid_from": "HQ"}
    r = _advance_client().post("/api/v1/returns/advance-refund", json=body, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 201, r.text
    ret = till["db"].get_collection("returns").find_one({"order_id": "ORD-1"})
    assert [t["method"] for t in ret["refund_tenders"]] == ["UPI"]
    assert ret["paid_outside_drawer"] == [{"method": "CASH", "amount": 600.0, "paid_from": "HQ"}]
    assert ret["cash_paid_from"] == "HQ"
    assert ret["net_refund"] == 1000.0


def test_cash_refund_must_say_where_the_cash_comes_from(till):
    _advance_order(till)
    body = {k: v for k, v in _ADV_BODY.items() if k != "cash_paid_from"}
    r = _advance_client().post("/api/v1/returns/advance-refund", json=body, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 400, r.text
    assert till["db"].get_collection("returns").count_documents({}) == 0


# --- create_return's re-check after reserving units --------------------------
@pytest.mark.usefixtures("gst_numbers")
def test_counter_return_backs_out_when_an_advance_refund_claims_mid_way(monkeypatch):
    client, returns = _ctx(monkeypatch)
    order = returns_router.get_order_repository()._order
    real_claim = returns_router._claim_returnable_qty
    released = []

    def _claim_then_door_claims(*a, **k):
        ok = real_claim(*a, **k)
        order["advance_refund_id"] = "RET-ADV-RACE"  # the door won the order meanwhile
        return ok

    monkeypatch.setattr(returns_router, "_claim_returnable_qty", _claim_then_door_claims)
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: released.append(a))
    r = client.post("/api/v1/returns", json=_qa_payload(), headers=_hdr("STORE_MANAGER"))
    assert r.status_code == 409, r.text
    assert returns.count_documents({}) == 0
    assert len(released) == 1
