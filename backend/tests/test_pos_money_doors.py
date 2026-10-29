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

        def get_collection(self, name):  # the real connection wrapper has it too
            return db.get_collection(name)

    conn = _Conn()
    conn.db = db
    monkeypatch.setattr(om, "get_order_repository", lambda: orders)
    monkeypatch.setattr(om, "get_stock_repository", lambda: None)
    from database.repositories.workshop_repository import WorkshopJobRepository

    monkeypatch.setattr(
        "api.dependencies.get_workshop_repository",
        lambda: WorkshopJobRepository(db.get_collection("workshop_jobs")),
    )
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
    assert "needs a manager's approval" in detail["message"]
    assert detail["running_total"] == pytest.approx(2808.4)
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
    db.get_collection("orders").insert_one(_order("ORD-BOK01"))
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
    assert _stored(till)["balance_due"] == 0.0  # nothing is owed on a cancelled order


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
def test_only_gst_returns_keep_an_invoice_cancelled_behind_its_whole_credit_note():
    # Round-3 review K2: the books (default rule) drop EVERY cancelled order;
    # the GST-document view (GSTR-1 / 3B, which also file credit notes) keeps
    # only an invoice the refund-advance door reversed with a whole-invoice note.
    from api.utils.real_sale import is_real_sale, real_sale_match
    from tests.strict_fakes import matches

    reversed_ = {"order_id": "A", "status": "CANCELLED", "invoice_number": "BOK01/2627/00009",
                 "advance_refund_status": "COMPLETED", "advance_refund_credit_note": "BOK01/C2627/0001"}
    db = StrictDB()
    db.seed("orders", [
        reversed_,
        {**reversed_, "order_id": "B", "status": "CANCELED"},
        # an unpaid online COD cancel with an invoice number: no credit note
        {"order_id": "C", "status": "CANCELLED", "invoice_number": "BOK01/2627/00010",
         "channel": "ONLINE", "shopify_order_id": "SH-9"},
        # the refund is not COMPLETED yet
        {**reversed_, "order_id": "D", "advance_refund_status": None},
        {"order_id": "E", "status": "cancelled"},
        {"order_id": "F", "status": "CONFIRMED"},
    ])
    rows = list(db.get_collection("orders").find({}))
    gst = real_sale_match(db, gst_documents=True)
    assert sorted(o["order_id"] for o in rows if matches(o, gst)) == ["A", "B", "F"]
    assert [o["order_id"] for o in rows if is_real_sale(o, gst_documents=True)] == ["A", "B", "F"]
    books = real_sale_match(db)
    assert "A" not in [o["order_id"] for o in rows if matches(o, books)]
    assert is_real_sale(reversed_) is False


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

    _advance_order(till, advance_refund_id="RET-ADV-1", advance_refund_record="RET-ADV-1", status="READY")
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


# --- M1 / M2 (round 3): a refund part-written is finished, never rolled back --
def _invoiced_voucher_order(till):
    till["db"].get_collection("customers").insert_one({"customer_id": "CUST-1", "name": "Asha", "store_credit": 50.0})
    till["db"].get_collection("orders").insert_one(
        _order(invoice_number="BOK01/2627/00009", items=[dict(_LINE)], amount_paid=1000.0,
               payment_status="PARTIAL", grand_total=5000.0,
               payments=[{"payment_id": "p1", "method": "GIFT_VOUCHER", "amount": 1000.0}])
    )


@pytest.mark.usefixtures("gst_numbers")
def test_a_failed_store_credit_is_finished_by_the_next_press_with_the_same_note(till, monkeypatch):
    _invoiced_voucher_order(till)
    real_credit = returns_router._credit_back_advance
    monkeypatch.setattr(returns_router, "_credit_back_advance", lambda *a, **k: None)
    client = _advance_client()
    r1 = client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r1.status_code == 503 and "press" in r1.text.lower(), r1.text
    pending = till["db"].get_collection("returns").find_one({"order_id": "ORD-1"})
    assert pending["status"] == "PENDING" and pending["credit_note_number"] == "TST01/C2627/0001"
    assert _stored(till)["advance_refund_credit_note"] == "TST01/C2627/0001"  # stamped on the claim
    assert _ledger(till).count_documents({}) == 0

    monkeypatch.setattr(returns_router, "_credit_back_advance", real_credit)
    r2 = client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r2.status_code == 201, r2.text
    done = till["db"].get_collection("returns").find_one({"order_id": "ORD-1"})
    assert done["status"] == "COMPLETED" and done["credit_note_number"] == "TST01/C2627/0001"
    assert [x["delta"] for x in _ledger(till).find({})] == [1000.0]
    assert _stored(till)["status"] == "CANCELLED"


@pytest.mark.usefixtures("gst_numbers")
def test_a_crash_after_the_credit_rolls_forward_without_a_second_credit(till, monkeypatch):
    # The press crashed after writing the store credit but before COMPLETED:
    # the customer may already have spent it, so the next press finishes.
    _invoiced_voucher_order(till)
    coll = till["db"].get_collection("returns")
    real_update = coll.update_one
    crashed = {"n": 0}

    def _crash_on_complete(flt, upd, *a, **k):
        if (upd.get("$set") or {}).get("status") == "COMPLETED" and not crashed["n"]:
            crashed["n"] = 1
            raise RuntimeError("process died")
        return real_update(flt, upd, *a, **k)

    monkeypatch.setattr(coll, "update_one", _crash_on_complete)
    client = _advance_client()
    assert client.post("/api/v1/returns/advance-refund", json=_ADV_BODY,
                       headers=_hdr("ACCOUNTANT")).status_code == 503
    assert [x["delta"] for x in _ledger(till).find({})] == [1000.0]
    r2 = client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r2.status_code == 201, r2.text
    assert [x["delta"] for x in _ledger(till).find({})] == [1000.0]  # credited once
    assert till["db"].get_collection("customers").find_one({"customer_id": "CUST-1"})["store_credit"] == 1050.0
    assert coll.find_one({"order_id": "ORD-1"})["status"] == "COMPLETED"
    assert _stored(till)["status"] == "CANCELLED"


def test_the_store_credit_row_is_written_once_per_refund(till):
    order = _order(customer_id="CUST-1", advance_refund_id="RET-X")  # the press holds the claim
    till["db"].get_collection("orders").insert_one(order)
    till["db"].get_collection("customers").insert_one({"customer_id": "CUST-1", "store_credit": 0.0})
    a = returns_router._credit_back_advance(order, 700.0, "RET-X", _user("ACCOUNTANT"))
    b = returns_router._credit_back_advance(order, 700.0, "RET-X", _user("ACCOUNTANT"))
    assert a["entry_id"] == b["entry_id"] == "RET-X:credit"
    assert _ledger(till).count_documents({}) == 1


@pytest.mark.usefixtures("gst_numbers")
def test_two_presses_numbering_at_once_write_one_number_off(till, monkeypatch):
    audits = []

    class _Audit:
        def create(self, row):
            audits.append(row)

    monkeypatch.setattr("api.dependencies.get_audit_repository", lambda: _Audit())
    _advance_order(till, invoice_number="BOK01/2627/00009", items=[dict(_LINE)])
    real_mint = returns_router.next_document_number

    def _other_press_wins(*a, **k):
        n = real_mint(*a, **k)
        till["db"].get_collection("orders").update_one(
            {"order_id": "ORD-1"}, {"$set": {"advance_refund_credit_note": "TST01/C2627/0900"}})
        return n

    monkeypatch.setattr(returns_router, "next_document_number", _other_press_wins)
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 201, r.text
    assert till["db"].get_collection("returns").find_one({"order_id": "ORD-1"})["credit_note_number"] == "TST01/C2627/0900"
    unused = [a for a in audits if a.get("action") == "CREDIT_NOTE_NUMBER_UNUSED"]
    assert [u["details"]["unused_credit_note_number"] for u in unused] == ["TST01/C2627/0001"]


def _stale(till, *, minutes_ago):
    from datetime import datetime, timedelta, timezone

    at = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    till["db"].get_collection("orders").insert_one(
        _order(amount_paid=1000.0, payment_status="PARTIAL", advance_refund_id="RET-STALE",
               advance_refund_at=at,
               payments=[{"payment_id": "p2", "method": "CASH", "amount": 1000.0}])
    )


def test_a_claim_with_nothing_written_is_freed_then_refunded_once(till):
    _stale(till, minutes_ago=10)
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 201, r.text
    assert till["db"].get_collection("returns").count_documents({"order_id": "ORD-1"}) == 1
    assert _stored(till)["status"] == "CANCELLED"


def test_a_press_still_in_flight_is_not_undone(till):
    _stale(till, minutes_ago=0)
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 409, r.text
    assert till["db"].get_collection("returns").count_documents({}) == 0
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


# =========================================================================== #
# Round-3 review (head 7fe62571)
# =========================================================================== #
# --- K1: no handover of a cancelled / refunded order, at any door ------------
def _refunded_ready(till):
    """A fully PAID order the refund-advance door has claimed: payment_status
    alone still reads PAID, which is how it slipped past the handover gate."""
    till["db"].get_collection("orders").insert_one(_order(
        status="READY", payment_status="PAID", amount_paid=5000.0, balance_due=0.0, grand_total=5000.0,
        payments=[{"payment_id": "p1", "method": "CASH", "amount": 5000.0}], advance_refund_id="RET-ADV-1",
        advance_refund_record="RET-ADV-1"))


@pytest.mark.parametrize("status,extra", [("READY", {"advance_refund_id": "RET-ADV-1"}),
                                          ("CANCELLED", {}), ("CANCELED", {})])
def test_the_handover_gate_refuses_a_refunded_or_cancelled_order(status, extra):
    from api.services.delivery_gate import assert_handover_payment

    order = _order(status=status, payment_status="PAID", amount_paid=1000.0, balance_due=0.0, **extra)
    with pytest.raises(HTTPException) as exc:
        assert_handover_payment(order, approval_token=None, current_user=_user("ADMIN"))
    assert exc.value.status_code == 409


def test_workshop_status_and_pickup_scan_cannot_hand_over_a_refunded_order(till, monkeypatch):
    # workshop PUT status and the PICKUP scan both run this one gate.
    from api.routers.workshop import gates

    monkeypatch.setattr(gates, "get_order_repository", lambda: till["orders"])
    _refunded_ready(till)
    with pytest.raises(HTTPException) as exc:
        gates.gate_job_handover_payment({"job_id": "J1", "order_id": "ORD-1"}, _user("WORKSHOP_STAFF"), None)
    assert exc.value.status_code == 409


@pytest.mark.parametrize("method", ["Prepaid", "COD"])
@pytest.mark.asyncio
async def test_shipping_cannot_dispatch_a_refunded_order(till, monkeypatch, method):
    from api.routers import shipping

    _refunded_ready(till)
    monkeypatch.setattr(shipping, "_resolve_order", lambda oid: _stored(till, oid))
    body = shipping.ShipmentCreate(order_id="ORD-1", address=shipping.ShipAddress(payment_method=method))
    with pytest.raises(HTTPException) as exc:
        await shipping.book_shipment(body, _user("ADMIN"))
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_counter_deliver_of_a_refunded_paid_order_is_refused(till):
    from api.routers.orders import deliver_order

    _refunded_ready(till)
    with pytest.raises(HTTPException) as exc:
        await deliver_order("ORD-1", None, _user("STORE_MANAGER"))
    assert exc.value.status_code == 409
    assert _stored(till)["status"] == "READY"


def test_the_deliver_claim_refuses_an_order_an_advance_refund_claimed(till):
    from api.routers.orders.release import _claim_order_status

    _refunded_ready(till)
    assert _claim_order_status(till["orders"], "ORD-1", "DELIVERED", ("READY",), "u1") is False
    assert _stored(till)["status"] == "READY"


def test_cancel_closes_the_open_workshop_job(till):
    _advance_order(till)
    jobs = till["db"].get_collection("workshop_jobs")
    jobs.insert_one({"job_id": "J1", "order_id": "ORD-1", "status": "READY"})
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 201, r.text
    assert jobs.find_one({"job_id": "J1"})["status"] == "CANCELLED"


def test_no_advance_refund_once_any_job_was_handed_over(till):
    # Round-5 review F1: a job DELIVERED never moves the order (it stays
    # READY), so the door read the order alone and paid the advance back for
    # goods the customer already has.
    _advance_order(till, status="READY")
    jobs = till["db"].get_collection("workshop_jobs")
    jobs.insert_one({"job_id": "J1", "order_id": "ORD-1", "status": "READY"})
    jobs.insert_one({"job_id": "J0", "order_id": "ORD-1", "status": "DELIVERED"})
    client = _advance_client()
    r = client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 409, r.text
    assert "handed over" in r.text
    assert till["db"].get_collection("returns").count_documents({}) == 0
    order = _stored(till)
    assert order["status"] == "READY" and order.get("advance_refund_id") is None
    assert jobs.find_one({"job_id": "J1"})["status"] == "READY"
    plan = client.get("/api/v1/returns/advance-refund/ORD-1", headers=_hdr("ACCOUNTANT")).json()
    assert plan["ok"] is False and "handed over" in plan["reason"]


# --- R1: a refund claim landing mid-payment voids the payment ----------------
@pytest.mark.asyncio
async def test_a_refund_claim_landing_mid_payment_refuses_the_payment(till, monkeypatch):
    till["db"].get_collection("orders").insert_one(_order())
    coll = till["db"].get_collection("orders")
    real_update = coll.update_one
    landed = {"n": 0}

    def _claim_lands_first(flt, upd, *a, **k):
        if "$push" in upd and not landed["n"]:
            landed["n"] = 1
            real_update({"order_id": "ORD-1"}, {"$set": {"advance_refund_id": "RET-ADV-RACE"}})
        return real_update(flt, upd, *a, **k)

    monkeypatch.setattr(coll, "update_one", _claim_lands_first)
    with pytest.raises(HTTPException) as exc:
        await add_payment("ORD-1", PaymentCreate(method="CASH", amount=100.0), _user("CASHIER"))
    assert exc.value.status_code == 409
    assert _stored(till)["payments"] == [] and _stored(till)["amount_paid"] == 0.0


# --- K2: the books drop the cancelled order; nothing is owed on it -----------
@pytest.mark.usefixtures("gst_numbers")
def test_books_drop_the_refunded_cancelled_order_and_nothing_is_owed(till):
    from api.routers.analytics import _EXCLUDED_ORDER_STATUSES
    from api.utils.real_sale import real_sale_match
    from tests.strict_fakes import matches

    _advance_order(till, invoice_number="BOK01/2627/00009", items=[dict(_LINE)])
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 201, r.text
    doc = _stored(till)
    assert doc["status"] == "CANCELLED" and doc["balance_due"] == 0.0 and doc["payment_status"] == "REFUNDED"
    books = real_sale_match(till["db"])  # P&L, receivables, cash-flow, Tally, ticker
    assert not matches(doc, books)
    assert "CANCELLED" in _EXCLUDED_ORDER_STATUSES  # analytics


@pytest.mark.usefixtures("gst_numbers")
def test_an_online_cancelled_unpaid_invoiced_order_stays_out_of_gstr1(till, monkeypatch):
    import api.routers.reports as rep_mod
    from datetime import datetime, timezone

    till["db"].seed("stores", [{"store_id": STORE, "gstin": "27AAACR0000A1ZZ", "store_name": "Pune",
                                "state": "Maharashtra"}])
    line = dict(_LINE, hsn_code="900311", item_total=5000.0)
    till["db"].get_collection("orders").insert_one(_order(
        status="CANCELLED", channel="ONLINE", shopify_order_id="SH-9", invoice_number="BOK01/2627/00010",
        items=[line], tax_amount=238.10, created_at=datetime.now(timezone.utc).replace(tzinfo=None)))
    monkeypatch.setattr(rep_mod, "_get_raw_db", lambda: till["db"])
    from api.utils.ist import ist_today

    rep = rep_mod._compute_gstr1(ist_today().strftime("%Y-%m"), STORE)
    assert rep["b2cs"] == [] and rep["cdnr"] == []


# =========================================================================== #
# Round-4 review (head 9a8e5880): crash injection
# =========================================================================== #
def _crash_once(monkeypatch, obj, name, when):
    """Make obj.name raise the FIRST time `when(*args)` is true."""
    real = getattr(obj, name)
    hit = {"n": 0}

    def _wrapped(*a, **k):
        if not hit["n"] and when(*a, **k):
            hit["n"] = 1
            raise RuntimeError("process died here")
        return real(*a, **k)

    monkeypatch.setattr(obj, name, _wrapped)
    return hit


# --- F1: the order stamp comes before COMPLETED, and a re-press heals it ----
@pytest.mark.usefixtures("gst_numbers")
def test_a_crash_at_the_order_stamp_is_healed_by_the_next_press(till, monkeypatch):
    _advance_order(till, invoice_number="BOK01/2627/00009", items=[dict(_LINE)])
    hit = _crash_once(monkeypatch, till["orders"], "update_if",
                      lambda oid, exp, data, *a, **k: "advance_refund_status" in data)
    client = _advance_client()
    r1 = client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert hit["n"] == 1 and r1.status_code == 503, r1.text
    doc = till["db"].get_collection("returns").find_one({"order_id": "ORD-1"})
    # The note is NOT filed (record not COMPLETED) while the invoice is not kept.
    assert doc["status"] == "PENDING"
    assert _stored(till).get("advance_refund_status") is None
    r2 = client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r2.status_code == 201, r2.text
    assert till["db"].get_collection("returns").find_one({"order_id": "ORD-1"})["status"] == "COMPLETED"
    order = _stored(till)
    assert order["advance_refund_status"] == "COMPLETED" and order["status"] == "CANCELLED"
    assert order["advance_refund_credit_note"] == doc["credit_note_number"]


@pytest.mark.usefixtures("gst_numbers")
def test_a_completed_record_without_the_order_stamp_is_finished(till):
    # A record already COMPLETED whose order stamp never landed (older
    # sequence): the re-press runs the finish instead of only the cancel.
    _advance_order(till, invoice_number="BOK01/2627/00009", items=[dict(_LINE)],
                   advance_refund_id="RET-OLD", advance_refund_credit_note="TST01/C2627/0007")
    till["db"].get_collection("returns").insert_one({
        "return_id": "RET-OLD", "order_id": "ORD-1", "store_id": STORE, "status": "COMPLETED",
        "invoice_number": "BOK01/2627/00009", "credit_note_number": "TST01/C2627/0007",
        "net_refund": 1000.0, "refund_tenders": [{"method": "CASH", "amount": 1000.0}]})
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 201, r.text
    order = _stored(till)
    assert order["advance_refund_status"] == "COMPLETED" and order["status"] == "CANCELLED"


# --- F2: the balance moves once, atomically, even across a crash -------------
def test_a_crash_at_the_balance_write_is_finished_once(till, monkeypatch):
    till["db"].get_collection("customers").insert_one({"customer_id": "CUST-1", "name": "Asha", "store_credit": 50.0})
    till["db"].get_collection("orders").insert_one(
        _order(amount_paid=1000.0, payment_status="PARTIAL",
               payments=[{"payment_id": "p1", "method": "STORE_CREDIT", "amount": 1000.0}]))
    customers = till["db"].get_collection("customers")
    hit = _crash_once(monkeypatch, customers, "update_one", lambda flt, upd, *a, **k: "$inc" in upd)
    client = _advance_client()
    r1 = client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert hit["n"] == 1 and r1.status_code == 503, r1.text
    row = _ledger(till).find_one({})
    assert row["applied"] is False
    assert customers.find_one({"customer_id": "CUST-1"})["store_credit"] == 50.0
    r2 = client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r2.status_code == 201, r2.text
    assert customers.find_one({"customer_id": "CUST-1"})["store_credit"] == 1050.0
    assert _ledger(till).find_one({})["applied"] is True
    # Applying the same row again moves nothing.
    returns_router._credit_back_advance(_stored(till), 1000.0, row["ref"], _user("ACCOUNTANT"))
    assert customers.find_one({"customer_id": "CUST-1"})["store_credit"] == 1050.0


def test_the_balance_moves_by_inc_not_by_overwriting_a_read(till):
    # A redeem lands between the row's read and the balance write: an $inc
    # keeps it; a $set of the earlier read would erase it.
    till["db"].get_collection("customers").insert_one({"customer_id": "CUST-1", "store_credit": 500.0})
    orig_find = till["db"].get_collection("customers").find_one

    redeemed = {"n": 0}

    def _redeem_meanwhile(flt=None, *a, **k):
        doc = orig_find(flt, *a, **k)
        if flt == {"customer_id": "CUST-1"} and not redeemed["n"]:
            redeemed["n"] = 1
            till["db"].get_collection("customers").update_one(
                {"customer_id": "CUST-1"}, {"$inc": {"store_credit": -200.0}})
        return doc

    till["db"].get_collection("customers").find_one = _redeem_meanwhile
    till["db"].get_collection("orders").insert_one(_order(advance_refund_id="RET-Y"))
    returns_router._credit_back_advance(_order(), 700.0, "RET-Y", _user("ACCOUNTANT"))
    till["db"].get_collection("customers").find_one = orig_find
    assert till["db"].get_collection("customers").find_one({"customer_id": "CUST-1"})["store_credit"] == 1000.0


# --- S2: a crash between numbering and the stamp leaves no silent gap ---------
@pytest.mark.usefixtures("gst_numbers")
def test_a_crash_after_numbering_is_written_off_on_the_next_press(till, monkeypatch):
    audits = []

    class _Audit:
        def create(self, row):
            audits.append(row)

    monkeypatch.setattr("api.dependencies.get_audit_repository", lambda: _Audit())
    _advance_order(till, invoice_number="BOK01/2627/00009", items=[dict(_LINE)])
    hit = _crash_once(monkeypatch, till["orders"], "update_if",
                      lambda oid, exp, data, *a, **k: bool(data.get("advance_refund_credit_note")))
    client = _advance_client()
    assert client.post("/api/v1/returns/advance-refund", json=_ADV_BODY,
                       headers=_hdr("ACCOUNTANT")).status_code == 503
    assert hit["n"] == 1
    # The crashed press's marker is old by the time anyone presses again.
    till["db"].get_collection("orders").update_one(
        {"order_id": "ORD-1"}, {"$set": {"advance_refund_cn_minting_at": "2026-10-01T10:00:00+00:00"}})
    r2 = client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r2.status_code == 201, r2.text
    unused = [a for a in audits if a.get("action") == "CREDIT_NOTE_NUMBER_UNUSED"]
    # Round 8: the crashed press recorded its serial, so it is written off BY NAME.
    assert [u["details"]["unused_credit_note_number"] for u in unused] == ["TST01/C2627/0001"]
    assert till["db"].get_collection("returns").find_one({"order_id": "ORD-1"})["credit_note_number"] == "TST01/C2627/0002"


# --- S3: a stale claim whose record appears late is finished, not freed -------
def test_a_stale_claim_with_a_late_record_is_finished_not_freed(till, monkeypatch):
    from datetime import datetime, timedelta, timezone

    at = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    till["db"].get_collection("orders").insert_one(
        _order(amount_paid=1000.0, payment_status="PARTIAL", advance_refund_id="RET-SLOW", advance_refund_at=at,
               payments=[{"payment_id": "p1", "method": "CASH", "amount": 1000.0}]))
    rcoll = till["db"].get_collection("returns")
    rcoll.insert_one({"return_id": "RET-SLOW", "order_id": "ORD-1", "store_id": STORE, "status": "PENDING",
                      "net_refund": 1000.0, "refund_tenders": [{"method": "CASH", "amount": 1000.0}]})
    real = rcoll.find_one
    seen = {"n": 0}

    def _first_look_misses(flt=None, *a, **k):
        if flt == {"return_id": "RET-SLOW"} and not seen["n"]:
            seen["n"] = 1
            return None
        return real(flt, *a, **k)

    monkeypatch.setattr(rcoll, "find_one", _first_look_misses)
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 201, r.text
    assert real({"return_id": "RET-SLOW"})["status"] == "COMPLETED"
    assert rcoll.count_documents({"order_id": "ORD-1"}) == 1
    assert _stored(till)["status"] == "CANCELLED"


# --- S4: a refund claim landing between the job gate and the job write --------
@pytest.mark.asyncio
async def test_workshop_handover_is_undone_when_a_refund_lands_mid_write(till, monkeypatch):
    from api.routers.workshop import gates, status as wstatus
    from api.routers.workshop.models import StatusBody
    from database.repositories.workshop_repository import WorkshopJobRepository

    till["db"].get_collection("orders").insert_one(_order(
        status="READY", payment_status="PAID", amount_paid=1000.0, balance_due=0.0,
        payments=[{"payment_id": "p1", "method": "CASH", "amount": 1000.0}]))
    jobs = till["db"].get_collection("workshop_jobs")
    jobs.insert_one({"job_id": "J1", "order_id": "ORD-1", "store_id": STORE, "status": "READY", "qc_passed": True})
    wrepo = WorkshopJobRepository(jobs)
    real_update = wrepo.update_status

    def _refund_lands_first(*a, **k):
        till["db"].get_collection("orders").update_one(
            {"order_id": "ORD-1"}, {"$set": {"advance_refund_id": "RET-RACE"}})
        return real_update(*a, **k)

    monkeypatch.setattr(wrepo, "update_status", _refund_lands_first)
    monkeypatch.setattr(wstatus, "get_workshop_repository", lambda: wrepo)
    monkeypatch.setattr(gates, "get_order_repository", lambda: till["orders"])
    with pytest.raises(HTTPException) as exc:
        await wstatus.update_job_status("J1", StatusBody(status="DELIVERED"), None, None, _user("STORE_MANAGER"))
    assert exc.value.status_code == 409
    assert jobs.find_one({"job_id": "J1"})["status"] == "READY"


def test_the_pickup_scan_undo_helper_puts_the_job_back(till, monkeypatch):
    from api.routers.workshop import gates
    from database.repositories.workshop_repository import WorkshopJobRepository

    monkeypatch.setattr(gates, "get_order_repository", lambda: till["orders"])
    till["db"].get_collection("orders").insert_one(_order(status="READY", advance_refund_id="RET-RACE"))
    jobs = till["db"].get_collection("workshop_jobs")
    jobs.insert_one({"job_id": "J2", "order_id": "ORD-1", "status": "DELIVERED"})
    assert gates.undo_handover_if_order_refunded(WorkshopJobRepository(jobs), {"job_id": "J2", "order_id": "ORD-1"}, "READY")
    assert jobs.find_one({"job_id": "J2"})["status"] == "READY"


# --- S1: the GST cross-check adds the cancelled-behind-a-note invoice to Books --
def test_crosscheck_books_carry_invoices_cancelled_behind_a_whole_credit_note(till):
    from api.routers.finance import gst_crosscheck as xr
    from api.services import gst_crosscheck as xc
    from datetime import datetime

    till["db"].get_collection("orders").insert_one(_order(
        status="CANCELLED", invoice_number="BOK01/2627/00009", grand_total=5000.0, tax_amount=238.10,
        advance_refund_status="COMPLETED", advance_refund_credit_note="TST01/C2627/0001",
        created_at=datetime(2026, 10, 10)))
    till["db"].get_collection("orders").insert_one(_order(
        "ORD-2", status="CANCELLED", invoice_number="BOK01/2627/00010", grand_total=900.0, tax_amount=42.86,
        created_at=datetime(2026, 10, 10)))  # no credit note: stays out
    cbn = xr._cancelled_behind_credit_note(
        till["db"], [STORE], datetime(2026, 10, 1), datetime(2026, 11, 1), {}, {})
    assert cbn["count"] == 1 and cbn["tax"] == pytest.approx(238.10) and cbn["taxableValue"] == pytest.approx(4761.90)
    out = xc.build_crosscheck(
        {"totalTaxableValue": 4761.90, "totalTax": 238.10},
        {"outwardTaxableValue": 0.0, "outwardTax": 0.0},
        {"sales_taxable": 0.0, "sales_tax": 0.0, "cancelled_behind_credit_note": cbn},
        {"taxable": 0.0, "tax": 0.0, "cgst": 0.0, "sgst": 0.0, "igst": 0.0},
    )
    row = next(c for c in out["comparisons"] if c["metric"] == "Total output GST")
    vals = row.get("values") or row.get("sources") or {}
    assert vals["Books (orders)"] == pytest.approx(vals["GSTR-1"]) == pytest.approx(238.10)
    assert out["cancelled_behind_credit_note"]["count"] == 1


def test_a_crash_after_the_balance_moved_never_moves_it_twice(till):
    # Died after the $inc but before the row was marked applied: the guard on
    # the customer doc (store_credit_refs) stops a second $inc.
    till["db"].get_collection("customers").insert_one(
        {"customer_id": "CUST-1", "store_credit": 1050.0, "store_credit_refs": ["RET-Z:credit"]})
    _ledger(till).insert_one({"_id": "RET-Z:credit", "entry_id": "RET-Z:credit", "customer_id": "CUST-1",
                              "type": "ADJUSTED", "amount": 1000.0, "delta": 1000.0, "ref": "RET-Z",
                              "applied": False})
    till["db"].get_collection("orders").insert_one(_order(advance_refund_id="RET-Z"))
    out = returns_router._credit_back_advance(_order(), 1000.0, "RET-Z", _user("ACCOUNTANT"))
    assert out is not None and out["applied"] is True
    assert till["db"].get_collection("customers").find_one({"customer_id": "CUST-1"})["store_credit"] == 1050.0


@pytest.mark.asyncio
async def test_pickup_scan_is_undone_when_a_refund_lands_mid_write(till, monkeypatch):
    from api.routers import labels
    from api.routers.workshop import gates
    from database.repositories.workshop_repository import WorkshopJobRepository

    till["db"].get_collection("orders").insert_one(_order(
        status="READY", payment_status="PAID", amount_paid=1000.0, balance_due=0.0,
        payments=[{"payment_id": "p1", "method": "CASH", "amount": 1000.0}]))
    jobs = till["db"].get_collection("workshop_jobs")
    jobs.insert_one({"job_id": "J3", "job_number": "J3", "order_id": "ORD-1", "store_id": STORE,
                     "status": "READY", "qc_passed": True})
    wrepo = WorkshopJobRepository(jobs)
    real_update = wrepo.update_status

    def _refund_lands_first(*a, **k):
        till["db"].get_collection("orders").update_one(
            {"order_id": "ORD-1"}, {"$set": {"advance_refund_id": "RET-RACE"}})
        return real_update(*a, **k)

    monkeypatch.setattr(wrepo, "update_status", _refund_lands_first)
    monkeypatch.setattr(labels, "get_workshop_repository", lambda: wrepo)
    monkeypatch.setattr(gates, "get_order_repository", lambda: till["orders"])
    out = await labels.scan_advance("J3", labels.ScanAdvanceBody(scanned_code="J3", station="PICKUP"),
                                    _user("STORE_MANAGER"))
    assert out["ok"] is False and out["reason"] == "ORDER_REFUNDED", out
    assert jobs.find_one({"job_id": "J3"})["status"] == "READY"



# =========================================================================== #
# Round-5 review (head 5e9ae0be)
# =========================================================================== #
@pytest.mark.asyncio
async def test_a_pickup_scan_then_an_advance_refund_is_refused(till, monkeypatch):
    # A 12,000 order paid up front; pair 1 goes out through the pickup scan
    # (the scan's money gate wants nothing owed), pair 2 is still in the lab.
    from api.routers import labels
    from api.routers.workshop import gates
    from database.repositories.workshop_repository import WorkshopJobRepository

    till["db"].get_collection("orders").insert_one(_order(
        status="READY", grand_total=12000.0, amount_paid=12000.0, balance_due=0.0, payment_status="PAID",
        payments=[{"payment_id": "p1", "method": "CASH", "amount": 12000.0}]))
    jobs = till["db"].get_collection("workshop_jobs")
    jobs.insert_one({"job_id": "P1", "job_number": "P1", "order_id": "ORD-1", "store_id": STORE,
                     "status": "READY", "qc_passed": True})
    jobs.insert_one({"job_id": "P2", "job_number": "P2", "order_id": "ORD-1", "store_id": STORE,
                     "status": "IN_PROGRESS"})
    wrepo = WorkshopJobRepository(jobs)
    monkeypatch.setattr(labels, "get_workshop_repository", lambda: wrepo)
    monkeypatch.setattr(gates, "get_order_repository", lambda: till["orders"])
    out = await labels.scan_advance("P1", labels.ScanAdvanceBody(scanned_code="P1", station="PICKUP"),
                                    _user("STORE_MANAGER"))
    assert out["ok"] is True, out
    assert _stored(till)["status"] == "READY"  # the job going out never moves the order

    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 409, r.text
    assert till["db"].get_collection("returns").count_documents({}) == 0
    assert _stored(till)["status"] == "READY"


def test_the_handed_over_check_runs_after_the_claim_too(till, monkeypatch):
    # A pickup landing between the door's first look and its claim.
    _advance_order(till, status="READY")
    jobs = till["db"].get_collection("workshop_jobs")
    jobs.insert_one({"job_id": "J1", "order_id": "ORD-1", "status": "READY"})
    real_update_if = till["orders"].update_if

    def _pickup_meanwhile(oid, expected, data, *a, **k):
        ok = real_update_if(oid, expected, data, *a, **k)
        if "advance_refund_at" in data and data.get("advance_refund_id"):
            jobs.update_one({"job_id": "J1"}, {"$set": {"status": "DELIVERED"}})
        return ok

    monkeypatch.setattr(till["orders"], "update_if", _pickup_meanwhile)
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 409, r.text
    assert till["db"].get_collection("returns").count_documents({}) == 0
    assert _stored(till).get("advance_refund_id") is None


def _race_a_credit_into(till, monkeypatch, amount):
    """Between the door's balance read and its write, another door adds
    `amount` with an atomic $inc (the advance refund's)."""
    customers = till["db"].get_collection("customers")
    real = customers.find_one
    hit = {"n": 0}

    def _find(flt=None, *a, **k):
        doc = real(flt, *a, **k)
        if not hit["n"] and flt == {"customer_id": "CUST-1"}:
            hit["n"] = 1
            customers.update_one({"customer_id": "CUST-1"}, {"$inc": {"store_credit": amount}})
        return doc

    monkeypatch.setattr(customers, "find_one", _find)


def test_a_manual_store_credit_issue_never_overwrites_a_concurrent_credit(till, monkeypatch):
    from api.routers import customers as cust_router

    till["db"].get_collection("customers").insert_one({"customer_id": "CUST-1", "store_id": STORE, "store_credit": 500.0})
    monkeypatch.setattr(cust_router, "get_customer_repository",
                        lambda: CustomerRepository(till["db"].get_collection("customers")))
    monkeypatch.setattr(cust_router, "_ledger_coll", lambda: till["db"].get_collection("credit_note_ledger"))
    monkeypatch.setattr(cust_router, "_scoped_customer_or_404",
                        lambda cid, user, write=False: till["db"].get_collection("customers").find_one({"customer_id": cid}))
    _race_a_credit_into(till, monkeypatch, 1000.0)
    cust_router._post_credit_entry("CUST-1", "ISSUED", cust_router.StoreCreditEntryRequest(amount=200.0, reason="goodwill"),
                                   _user("ADMIN"))
    assert till["db"].get_collection("customers").find_one({"customer_id": "CUST-1"})["store_credit"] == 1700.0


def test_a_return_credit_note_never_overwrites_a_concurrent_credit(till, monkeypatch):
    till["db"].get_collection("customers").insert_one({"customer_id": "CUST-1", "store_credit": 500.0})
    monkeypatch.setattr(returns_router, "next_document_number", lambda *a, **k: "TST01/C2627/0042")
    _race_a_credit_into(till, monkeypatch, 1000.0)
    entry = returns_router._issue_store_credit("CUST-1", 200.0, reason="return", ref="RET-Q",
                                               current_user=_user("ADMIN"), store_id=STORE)
    assert entry is not None
    assert till["db"].get_collection("customers").find_one({"customer_id": "CUST-1"})["store_credit"] == 1700.0


@pytest.mark.usefixtures("gst_numbers")
def test_a_numbering_refusal_leaves_no_phantom_write_off(till, monkeypatch):
    from database.repositories.order_repository import InvoiceNumberError

    audits = []

    class _Audit:
        def create(self, row):
            audits.append(row)

    monkeypatch.setattr("api.dependencies.get_audit_repository", lambda: _Audit())
    _advance_order(till, invoice_number="BOK01/2627/00009", items=[dict(_LINE)])
    real_mint = returns_router.next_document_number
    calls = {"n": 0}

    def _refuse_once(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise InvoiceNumberError("no counter yet")
        return real_mint(*a, **k)

    monkeypatch.setattr(returns_router, "next_document_number", _refuse_once)
    client = _advance_client()
    assert client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT")).status_code == 400
    assert _stored(till).get("advance_refund_cn_minting_at") is None
    assert client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT")).status_code == 201
    assert [a for a in audits if a.get("action") == "CREDIT_NOTE_NUMBER_UNUSED"] == []


def test_an_orphan_pending_record_does_not_block_the_next_refund(till):
    # A press freed after 2 minutes whose record landed late (and wrote nothing
    # else: its finish refuses without the claim) is voided, not a wall.
    _advance_order(till)
    rcoll = till["db"].get_collection("returns")
    rcoll.insert_one({"return_id": "RET-ORPHAN", "order_id": "ORD-1", "store_id": STORE, "status": "PENDING",
                      "advance_refund": True, "net_refund": 1000.0, "refund_tenders": [],
                      "created_at": "2026-10-01T10:00:00+00:00"})
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 201, r.text
    assert rcoll.find_one({"return_id": "RET-ORPHAN"})["status"] == "VOIDED"
    assert rcoll.count_documents({"order_id": "ORD-1", "status": "COMPLETED"}) == 1


def test_an_undone_handover_clears_the_pickup_record(till, monkeypatch):
    from api.routers.workshop import gates
    from database.repositories.workshop_repository import WorkshopJobRepository

    monkeypatch.setattr(gates, "get_order_repository", lambda: till["orders"])
    till["db"].get_collection("orders").insert_one(_order(status="READY", advance_refund_id="RET-RACE"))
    jobs = till["db"].get_collection("workshop_jobs")
    jobs.insert_one({"job_id": "J5", "order_id": "ORD-1", "status": "DELIVERED", "delivered_at": "2026-10-24",
                     "picked_up_by_name": "Ravi", "picked_up_by_phone": "9800000000"})
    gates.undo_handover_if_order_refunded(WorkshopJobRepository(jobs), {"job_id": "J5", "order_id": "ORD-1"}, "READY")
    job = jobs.find_one({"job_id": "J5"})
    assert job["status"] == "READY"
    assert "delivered_at" not in job and "picked_up_by_name" not in job and "picked_up_by_phone" not in job



def test_a_young_pending_record_of_another_press_is_never_voided(till):
    # A press that claimed a moment ago is still writing its own record.
    from api.utils.event_time import utc_now_iso

    _advance_order(till)
    rcoll = till["db"].get_collection("returns")
    rcoll.insert_one({"return_id": "RET-NOW", "order_id": "ORD-1", "store_id": STORE, "status": "PENDING",
                      "advance_refund": True, "net_refund": 1000.0, "refund_tenders": [],
                      "created_at": utc_now_iso()})
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 400, r.text
    assert rcoll.find_one({"return_id": "RET-NOW"})["status"] == "PENDING"


# =========================================================================== #
# Round-6 review (head 70e624a2)
# =========================================================================== #
def _old_pending(till, **over):
    rec = {"return_id": "RET-A", "order_id": "ORD-1", "store_id": STORE, "status": "PENDING",
           "advance_refund": True, "net_refund": 1000.0, "refund_tenders": [],
           "credit_amount": 1000.0, "created_at": "2026-10-01T10:00:00+00:00"}
    rec.update(over)
    till["db"].get_collection("returns").insert_one(rec)


def test_a_stale_record_whose_credit_was_applied_is_never_voided(till):
    # Press A stalled, B freed its claim, A then credited Rs 1,000: voiding A's
    # record and refunding again would pay Rs 2,000 for a Rs 1,000 advance.
    till["db"].get_collection("customers").insert_one({"customer_id": "CUST-1", "store_credit": 1000.0})
    till["db"].get_collection("orders").insert_one(
        _order(amount_paid=1000.0, payment_status="PARTIAL",
               payments=[{"payment_id": "p1", "method": "STORE_CREDIT", "amount": 1000.0}]))
    _old_pending(till)
    _ledger(till).insert_one({"_id": "RET-A:credit", "entry_id": "RET-A:credit", "customer_id": "CUST-1",
                              "type": "ADJUSTED", "delta": 1000.0, "ref": "RET-A", "applied": True})
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 400 and "Ask an admin" in r.text, r.text
    assert till["db"].get_collection("returns").find_one({"return_id": "RET-A"})["status"] == "PENDING"
    assert _ledger(till).count_documents({}) == 1
    assert till["db"].get_collection("customers").find_one({"customer_id": "CUST-1"})["store_credit"] == 1000.0


def test_a_stale_record_with_a_credit_note_number_is_never_voided(till):
    _advance_order(till)
    _old_pending(till, credit_amount=None, credit_note_number="TST01/C2627/0003")
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 400 and "Ask an admin" in r.text, r.text
    assert till["db"].get_collection("returns").find_one({"return_id": "RET-A"})["status"] == "PENDING"


def test_a_freed_claim_cannot_credit(till):
    till["db"].get_collection("customers").insert_one({"customer_id": "CUST-1", "store_credit": 0.0})
    till["db"].get_collection("orders").insert_one(_order(advance_refund_id="RET-OTHER"))
    out = returns_router._credit_back_advance(_stored(till), 1000.0, "RET-A", _user("ACCOUNTANT"))
    assert out is None
    assert _ledger(till).count_documents({}) == 0
    assert till["db"].get_collection("customers").find_one({"customer_id": "CUST-1"})["store_credit"] == 0.0


def test_a_customer_without_a_store_credit_field_gets_just_the_delta(till):
    # One rule: no seeding from the ledger (it double counted). A customer
    # with no field AND no ledger history simply starts at 0 + the delta;
    # one WITH history is refused until the backfill sets the field (below).
    from api.routers.customers import apply_store_credit_delta

    customers = till["db"].get_collection("customers")
    customers.insert_one({"customer_id": "CUST-1"})
    repo = CustomerRepository(customers)
    assert apply_store_credit_delta(repo, "CUST-1", 200.0) is True
    assert customers.find_one({"customer_id": "CUST-1"})["store_credit"] == 200.0


def test_a_null_store_credit_is_treated_as_missing(till):
    from api.routers.customers import apply_store_credit_delta

    customers = till["db"].get_collection("customers")
    customers.insert_one({"customer_id": "CUST-1", "store_credit": None})
    assert apply_store_credit_delta(CustomerRepository(customers), "CUST-1", 200.0) is True
    assert customers.find_one({"customer_id": "CUST-1"})["store_credit"] == 200.0


def test_the_advance_credit_uses_the_one_balance_rule(till):
    # Missing field: the advance refund's guarded $inc goes the same way.
    till["db"].get_collection("customers").insert_one({"customer_id": "CUST-1", "store_credit": None})
    till["db"].get_collection("orders").insert_one(_order(advance_refund_id="RET-B"))
    out = returns_router._credit_back_advance(_stored(till), 700.0, "RET-B", _user("ACCOUNTANT"))
    assert out is not None and out["applied"] is True
    assert till["db"].get_collection("customers").find_one({"customer_id": "CUST-1"})["store_credit"] == 700.0
    returns_router._credit_back_advance(_stored(till), 700.0, "RET-B", _user("ACCOUNTANT"))
    assert till["db"].get_collection("customers").find_one({"customer_id": "CUST-1"})["store_credit"] == 700.0


def test_backfill_sets_the_field_from_the_ledger_dry_run_first(tmp_path):
    from scripts import backfill_store_credit_field as bf

    db = StrictDB()
    db.seed("customers", [{"customer_id": "C1"}, {"customer_id": "C2", "store_credit": None},
                          {"customer_id": "C3", "store_credit": 50.0}, {"customer_id": "C4"}])
    db.seed("credit_note_ledger", [{"customer_id": "C1", "delta": 300.0}, {"customer_id": "C1", "delta": -100.0},
                                   {"customer_id": "C2", "delta": 80.0}, {"customer_id": "C3", "delta": 999.0}])
    out = bf.plan(db)
    assert sorted(out["sets"]) == [("C1", 200.0), ("C2", 80.0), ("C4", 0.0)]
    assert "store_credit" not in db.get_collection("customers").find_one({"customer_id": "C1"})
    assert bf.apply(db, out["sets"], str(tmp_path / "b.json")) == 3
    cust = db.get_collection("customers")
    assert cust.find_one({"customer_id": "C1"})["store_credit"] == 200.0
    assert cust.find_one({"customer_id": "C3"})["store_credit"] == 50.0
    assert bf.plan(db)["sets"] == []


def test_a_freed_claim_cannot_apply_its_unapplied_credit_row(till):
    # Press A wrote its credit row while it held the claim, then stalled; the
    # claim was freed before the balance moved: the balance must not move now.
    till["db"].get_collection("customers").insert_one({"customer_id": "CUST-1", "store_credit": 0.0})
    till["db"].get_collection("orders").insert_one(_order(advance_refund_id="RET-OTHER"))
    _ledger(till).insert_one({"_id": "RET-A:credit", "entry_id": "RET-A:credit", "customer_id": "CUST-1",
                              "type": "ADJUSTED", "delta": 1000.0, "ref": "RET-A", "applied": False})
    assert returns_router._credit_back_advance(_stored(till), 1000.0, "RET-A", _user("ACCOUNTANT")) is None
    assert till["db"].get_collection("customers").find_one({"customer_id": "CUST-1"})["store_credit"] == 0.0


# =========================================================================== #
# Round-7 review (head 22778d95)
# =========================================================================== #
def _ship(till, monkeypatch, **doc):
    from api.routers import shipping

    coll = till["db"].get_collection("shipments")
    monkeypatch.setattr(shipping, "_shipments_coll", lambda: coll)
    if doc:
        coll.insert_one(doc)
    return coll


# --- 1: a courier booking is a handover -----------------------------------------
def test_no_advance_refund_once_a_courier_shipment_is_booked(till, monkeypatch):
    _advance_order(till, status="READY")
    _ship(till, monkeypatch, shipment_id="SH-1", order_id="ORD-1", status="BOOKED")
    client = _advance_client()
    r = client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 409 and "handed over" in r.text, r.text
    assert till["db"].get_collection("returns").count_documents({}) == 0
    plan = client.get("/api/v1/returns/advance-refund/ORD-1", headers=_hdr("ACCOUNTANT")).json()
    assert plan["ok"] is False and "handed over" in plan["reason"]


def test_a_dead_shipment_does_not_block_the_refund(till, monkeypatch):
    _advance_order(till, status="READY")
    _ship(till, monkeypatch, shipment_id="SH-0", order_id="ORD-1", status="CANCELLED")
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 201, r.text


def test_a_booking_landing_after_the_claim_is_caught(till, monkeypatch):
    _advance_order(till, status="READY")
    ships = _ship(till, monkeypatch)
    real_update_if = till["orders"].update_if

    def _booked_meanwhile(oid, expected, data, *a, **k):
        ok = real_update_if(oid, expected, data, *a, **k)
        if data.get("advance_refund_id") and "advance_refund_at" in data:
            ships.insert_one({"shipment_id": "SH-2", "order_id": "ORD-1", "status": "BOOKED"})
        return ok

    monkeypatch.setattr(till["orders"], "update_if", _booked_meanwhile)
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 409, r.text
    assert till["db"].get_collection("returns").count_documents({}) == 0
    assert _stored(till).get("advance_refund_id") is None


# --- 2: no tax invoice on a refunded order; the claim pins the invoice state -----
def test_no_invoice_is_minted_on_an_advance_refunded_order(till):
    from database.repositories.order_repository import InvoiceNumberError, order_mint_problem

    assert "refund" in order_mint_problem({"status": "CONFIRMED", "advance_refund_id": "RET-1"})
    till["db"].get_collection("orders").insert_one(_order(advance_refund_id="RET-1"))
    with pytest.raises(InvoiceNumberError):
        till["orders"].set_invoice("ORD-1", "BOK01/2627/00077")
    assert not _stored(till).get("invoice_number")


def test_an_invoice_minted_between_the_look_and_the_claim_stops_the_refund(till, monkeypatch):
    _advance_order(till)
    real_find = till["orders"].find_by_id
    hit = {"n": 0}

    def _invoiced_meanwhile(oid):
        doc = real_find(oid)
        if not hit["n"]:
            hit["n"] = 1
            till["db"].get_collection("orders").update_one(
                {"order_id": oid}, {"$set": {"invoice_number": "BOK01/2627/00078"}})
        return doc

    monkeypatch.setattr(till["orders"], "find_by_id", _invoiced_meanwhile)
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 409, r.text
    assert till["db"].get_collection("returns").count_documents({}) == 0


# --- 3 (reviewer probe): the backfill never sums an unapplied advance credit ------
def test_probe_backfill_never_doubles_a_stranded_advance_credit(till, tmp_path):
    from scripts import backfill_store_credit_field as bf

    db = till["db"]
    db.get_collection("customers").insert_one({"customer_id": "CUST-1"})  # legacy: no field
    db.get_collection("orders").insert_one(_order(advance_refund_id="RET-A"))
    _ledger(till).insert_one({"_id": "RET-A:credit", "entry_id": "RET-A:credit", "customer_id": "CUST-1",
                              "type": "ADJUSTED", "delta": 1000.0, "ref": "RET-A", "applied": False,
                              "source": "ADVANCE_REFUND"})
    out = bf.plan(db)
    # Round 8: never skipped (that locked the customer out); the unapplied row
    # is left out of the sum and applied once by its own press.
    assert out["sets"] == [("CUST-1", 0.0)] and out["unapplied_left"] == ["CUST-1"]
    bf.apply(db, out["sets"], str(tmp_path / "b.json"))
    # The next press applies its own row once: 1,000, never 2,000.
    assert returns_router._credit_back_advance(_stored(till), 1000.0, "RET-A", _user("ACCOUNTANT")) is not None
    assert db.get_collection("customers").find_one({"customer_id": "CUST-1"})["store_credit"] == 1000.0


# --- 4: a skipped backfill can never silently drop a legacy balance --------------
def test_a_missing_field_with_ledger_history_is_refused_loudly(till):
    from api.routers.customers import apply_store_credit_delta

    customers = till["db"].get_collection("customers")
    customers.insert_one({"customer_id": "CUST-1"})
    _ledger(till).insert_one({"customer_id": "CUST-1", "type": "ISSUED", "delta": 300.0})
    assert apply_store_credit_delta(CustomerRepository(customers), "CUST-1", 200.0) is False
    assert "store_credit" not in customers.find_one({"customer_id": "CUST-1"})


def test_the_manual_door_refuses_before_writing_when_the_backfill_was_skipped(till, monkeypatch):
    from api.routers import customers as cust_router

    till["db"].get_collection("customers").insert_one({"customer_id": "CUST-1", "store_id": STORE})
    _ledger(till).insert_one({"customer_id": "CUST-1", "type": "ISSUED", "delta": 300.0})
    monkeypatch.setattr(cust_router, "get_customer_repository",
                        lambda: CustomerRepository(till["db"].get_collection("customers")))
    monkeypatch.setattr(cust_router, "_ledger_coll", lambda: _ledger(till))
    monkeypatch.setattr(cust_router, "_scoped_customer_or_404",
                        lambda cid, user, write=False: till["db"].get_collection("customers").find_one({"customer_id": cid}))
    with pytest.raises(HTTPException) as exc:
        cust_router._post_credit_entry("CUST-1", "ISSUED", cust_router.StoreCreditEntryRequest(amount=200.0, reason="goodwill"),
                                       _user("ADMIN"))
    assert exc.value.status_code == 503
    assert _ledger(till).count_documents({}) == 1  # nothing new written


def test_a_return_credit_note_refuses_before_writing_when_the_backfill_was_skipped(till, monkeypatch):
    till["db"].get_collection("customers").insert_one({"customer_id": "CUST-1"})
    _ledger(till).insert_one({"customer_id": "CUST-1", "type": "ISSUED", "delta": 300.0})
    monkeypatch.setattr(returns_router, "next_document_number", lambda *a, **k: "TST01/C2627/0042")
    out = returns_router._issue_store_credit("CUST-1", 200.0, reason="return", ref="RET-Q",
                                             current_user=_user("ADMIN"), store_id=STORE)
    assert out is None
    assert _ledger(till).count_documents({}) == 1


# --- 10: the return credit note's audit balance reads THE balance rule ----------
def test_a_return_credit_note_audits_the_authoritative_balance(till, monkeypatch):
    # customer.store_credit is authoritative (500); the ledger alone sums 300.
    till["db"].get_collection("customers").insert_one({"customer_id": "CUST-1", "store_credit": 500.0})
    _ledger(till).insert_one({"customer_id": "CUST-1", "type": "ISSUED", "delta": 300.0})
    monkeypatch.setattr(returns_router, "next_document_number", lambda *a, **k: "TST01/C2627/0043")
    out = returns_router._issue_store_credit("CUST-1", 200.0, reason="return", ref="RET-R",
                                             current_user=_user("ADMIN"), store_id=STORE)
    assert out["balance_after"] == 700.0
    assert till["db"].get_collection("customers").find_one({"customer_id": "CUST-1"})["store_credit"] == 700.0


# --- 5: two returns at once cannot dodge the Rs 2,000 rule ------------------------
def test_two_returns_at_once_cannot_dodge_the_2000_rule(till, monkeypatch):
    # Rs 1,800 + Rs 1,800 on one bill, the second fired while the first is
    # still deciding: the order's refunded total is reserved BEFORE the gate.
    line = {"item_id": "li1", "product_id": "P1", "product_name": "Frame", "sku": "F1", "quantity": 2,
            "unit_price": 1525.42, "gst_rate": 18.0, "taxable_value": 3050.85, "tax_amount": 549.15}
    till["db"].get_collection("orders").insert_one(_order(
        status="DELIVERED", grand_total=3600.0, amount_paid=3600.0, balance_due=0.0, payment_status="PAID",
        items=[line], payments=[{"payment_id": "p1", "method": "CASH", "amount": 3600.0}]))
    monkeypatch.setattr(returns_router, "get_stock_repository", lambda: None)
    monkeypatch.setattr(returns_router, "get_product_repository", lambda: None)
    monkeypatch.setattr(returns_router, "next_document_number", lambda *a, **k: f"TST01/C2627/{len(results) + 1:04d}")
    # The per-line unit claim (positional array update) is not what is under
    # test here -- the order's refunded TOTAL is; let the unit claim pass.
    monkeypatch.setattr(returns_router, "_claim_returnable_qty", lambda *a, **k: True)
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: None)
    payload = {"order_id": "ORD-1", "return_type": "RETURN", "items": [{
        "order_item_id": "li1", "product_id": "P1", "product_name": "Frame", "sku": "F1",
        "return_qty": 1, "unit_price": 1525.42, "reason": "DEFECTIVE", "condition": "GOOD"}]}
    client = _advance_client()
    real_gate = returns_router._gate_refund_approval_matrix
    results = []

    def _second_fires_mid_gate(*a, **k):
        if not results:
            results.append("first-deciding")
            results.append(client.post("/api/v1/returns", json=payload, headers=_hdr("CASHIER")))
        return real_gate(*a, **k)

    monkeypatch.setattr(returns_router, "_gate_refund_approval_matrix", _second_fires_mid_gate)
    first = client.post("/api/v1/returns", json=payload, headers=_hdr("CASHIER"))
    second = results[1]
    assert first.status_code == 201, first.text
    assert second.status_code == 403, second.text
    assert second.json()["detail"]["reason"] == "REFUND_APPROVAL_REQUIRED"
    assert till["db"].get_collection("returns").count_documents({"status": "COMPLETED"}) == 1
    # The refused one released its hold; the recorded one's doc counts it now.
    holds = _stored(till)["refund_holds"]
    assert list(holds) == [till["db"].get_collection("returns").find_one({"status": "COMPLETED"})["return_id"]]


# --- 6: freeing a stale claim and a press stamping its record cannot interleave ----
def test_a_press_whose_claim_was_freed_stops_before_any_money(till, monkeypatch):
    till["db"].get_collection("customers").insert_one({"customer_id": "CUST-1", "store_credit": 0.0})
    till["db"].get_collection("orders").insert_one(
        _order(amount_paid=1000.0, payment_status="PARTIAL",
               payments=[{"payment_id": "p1", "method": "STORE_CREDIT", "amount": 1000.0}]))
    rcoll = till["db"].get_collection("returns")
    real_insert = rcoll.insert_one

    def _freed_after_insert(doc, *a, **k):
        out = real_insert(doc, *a, **k)
        till["db"].get_collection("orders").update_one(
            {"order_id": "ORD-1"}, {"$set": {"advance_refund_id": None}})  # another press freed the claim
        return out

    monkeypatch.setattr(rcoll, "insert_one", _freed_after_insert)
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 409, r.text
    assert _ledger(till).count_documents({}) == 0
    assert till["db"].get_collection("customers").find_one({"customer_id": "CUST-1"})["store_credit"] == 0.0


def test_a_free_loses_to_a_press_that_stamped_its_record(till, monkeypatch):
    from datetime import datetime, timedelta, timezone

    at = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    till["db"].get_collection("orders").insert_one(
        _order(amount_paid=1000.0, payment_status="PARTIAL", advance_refund_id="RET-A", advance_refund_at=at,
               payments=[{"payment_id": "p1", "method": "CASH", "amount": 1000.0}]))
    rcoll = till["db"].get_collection("returns")
    real_find = rcoll.find_one
    looks = {"n": 0}

    def _a_writes_between_the_look_and_the_free(flt=None, *a, **k):
        if flt == {"return_id": "RET-A"}:
            looks["n"] += 1
            if looks["n"] == 2:  # B's second look: A lands its record + stamp right after
                rcoll.insert_one({"return_id": "RET-A", "order_id": "ORD-1", "store_id": STORE,
                                  "status": "PENDING", "advance_refund": True, "net_refund": 1000.0,
                                  "refund_tenders": [{"method": "CASH", "amount": 1000.0}],
                                  "created_at": at})
                till["db"].get_collection("orders").update_one(
                    {"order_id": "ORD-1"}, {"$set": {"advance_refund_record": "RET-A"}})
                return None
        return real_find(flt, *a, **k)

    monkeypatch.setattr(rcoll, "find_one", _a_writes_between_the_look_and_the_free)
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 201, r.text
    assert real_find({"return_id": "RET-A"})["status"] == "COMPLETED"
    assert rcoll.count_documents({"order_id": "ORD-1"}) == 1  # refunded once, A's record


# --- 7: the refund belongs to the day it completed --------------------------------
@pytest.mark.usefixtures("gst_numbers")
def test_a_refund_finished_later_is_dated_when_it_completed(till, monkeypatch):
    _invoiced_voucher_order(till)
    real_credit = returns_router._credit_back_advance
    monkeypatch.setattr(returns_router, "_credit_back_advance", lambda *a, **k: None)
    client = _advance_client()
    assert client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT")).status_code == 503
    rcoll = till["db"].get_collection("returns")
    rcoll.update_one({"order_id": "ORD-1"}, {"$set": {"created_at": "2026-10-01T10:00:00+00:00",
                                                       "claimed_at": "2026-10-01T10:00:00+00:00"}})  # claimed last week
    monkeypatch.setattr(returns_router, "_credit_back_advance", real_credit)
    assert client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT")).status_code == 201
    doc = rcoll.find_one({"order_id": "ORD-1"})
    assert doc["claimed_at"] == "2026-10-01T10:00:00+00:00"
    assert doc["created_at"] == doc["completed_at"] and doc["created_at"] > "2026-10-02"


# --- 8: never write a number off while another press is numbering ---------------
@pytest.mark.usefixtures("gst_numbers")
def test_no_write_off_while_another_press_is_numbering(till, monkeypatch):
    from api.utils.event_time import utc_now_iso

    audits = []

    class _Audit:
        def create(self, row):
            audits.append(row)

    monkeypatch.setattr("api.dependencies.get_audit_repository", lambda: _Audit())
    _advance_order(till, invoice_number="BOK01/2627/00009", items=[dict(_LINE)],
                   advance_refund_id="RET-B", advance_refund_cn_minting_at=utc_now_iso(),
                   advance_refund_record="RET-B")
    till["db"].get_collection("returns").insert_one({
        "return_id": "RET-B", "order_id": "ORD-1", "store_id": STORE, "status": "PENDING",
        "advance_refund": True, "invoice_number": "BOK01/2627/00009", "net_refund": 1000.0,
        "refund_tenders": [{"method": "CASH", "amount": 1000.0}], "created_at": utc_now_iso()})
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 409, r.text
    assert [a for a in audits if a.get("action") == "CREDIT_NOTE_NUMBER_UNUSED"] == []


def test_a_return_credit_note_fails_loudly_when_the_balance_does_not_move(till, monkeypatch):
    # No customer doc for the balance to move on: the row must not be passed
    # off as issued credit.
    monkeypatch.setattr(returns_router, "next_document_number", lambda *a, **k: "TST01/C2627/0044")
    out = returns_router._issue_store_credit("CUST-GONE", 200.0, reason="return", ref="RET-S",
                                             current_user=_user("ADMIN"), store_id=STORE)
    assert out is None


def test_a_press_stamps_its_record_so_a_stale_free_cannot_take_its_claim(till, monkeypatch):
    # Press A records + stamps, then dies before finishing; its record is not
    # visible to B's looks (a lagging read). B must NOT free A's claim.
    _advance_order(till)
    real_finish = returns_router._finish_advance_refund

    def _dies(*a, **k):
        raise RuntimeError("press A died")

    monkeypatch.setattr(returns_router, "_finish_advance_refund", _dies)
    client = _advance_client()
    with pytest.raises(RuntimeError):
        client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    monkeypatch.setattr(returns_router, "_finish_advance_refund", real_finish)
    rid = _stored(till)["advance_refund_id"]
    till["db"].get_collection("orders").update_one(
        {"order_id": "ORD-1"}, {"$set": {"advance_refund_at": "2026-10-01T10:00:00+00:00"}})
    rcoll = till["db"].get_collection("returns")
    real_find = rcoll.find_one
    monkeypatch.setattr(rcoll, "find_one", lambda flt=None, *a, **k: None if flt == {"return_id": rid} else real_find(flt, *a, **k))
    r = client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 409, r.text
    assert _stored(till)["advance_refund_id"] == rid
    assert rcoll.count_documents({"order_id": "ORD-1"}) == 1
