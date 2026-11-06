"""
IMS 2.0 - A CASHIER-only login finishes a till sale (Codex L2-POS-F01)
======================================================================
Both counter doors (/pos/counter General Counter, /pos/new Billing) are open
to CASHIER in the frontend, and "Complete sale" runs, in order:

    POST /orders/quote  -> POST /orders  -> POST /orders/{id}/payments
    -> (counter take-away) POST /orders/{id}/ready -> POST /orders/{id}/deliver

The CASHIER was refused at the first two (policy rows + in-function gate both
listed SALES_CASHIER but not CASHIER), so a cashier could never ring a sale.
Driven through the REAL app (RBAC middleware + route gates) with real JWTs
for each role, asserting the stored orders -- not just status codes.

What must NOT widen: CASHIER still cannot cancel a sale, its discount cap stays
0% (any discount goes to a manager), WORKSHOP_STAFF still never bills or takes
money (owner ruling 2026-11-01), and another shop's cashier is refused.
No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from tests.test_counter_sale_delivered import STORE, counter_env  # noqa: E402,F401

CASHIER_ID = "u-cashier-only"
ITEM = {
    "item_type": "WATCH",
    "product_id": "p-watch",
    "product_name": "Titan watch",
    "sku": "W-1",
    "quantity": 1,
    "unit_price": 1000.0,
    "discount_percent": 0,
    "category": "WATCH",
}


def _h(role, uid=CASHIER_ID, store=STORE, key=None):
    from api.routers.auth import create_access_token

    token = create_access_token(
        {
            "user_id": uid,
            "username": uid,
            "roles": [role],
            "store_ids": [store],
            "active_store_id": store,
        }
    )
    h = {"Authorization": f"Bearer {token}"}
    if key:
        h["Idempotency-Key"] = key
    return h


def _body(order_type="quick_sale", **item_over):
    return {
        "customer_id": "cust-counter",
        "order_type": order_type,
        "salesperson_id": CASHIER_ID,
        "salesperson_name": "Meena",
        "items": [dict(ITEM, **item_over)],
    }


def _quote(client, headers, **item_over):
    return client.post(
        "/api/v1/orders/quote",
        headers=headers,
        json={"items": [dict(ITEM, **item_over)], "cart_discount_percent": 0},
    )


def _orders(env):
    return list(env["orders"].find({}))


def _ledger(env):
    """Independent reconciliation: every order's stored money, by status."""
    rows = _orders(env)
    return {
        "count": len(rows),
        "by_status": sorted(r["status"] for r in rows),
        "paid": round(sum(float(r.get("amount_paid") or 0) for r in rows), 2),
        "legs": sum(len(r.get("payments") or []) for r in rows),
        "leg_total": round(
            sum(float(p.get("amount") or 0) for r in rows for p in (r.get("payments") or [])), 2
        ),
    }


# --------------------------------------------------------------------------- #
# 1. The whole counter sale, as a CASHIER-only login, with retries
# --------------------------------------------------------------------------- #
@pytest.fixture
def invoice_identity(monkeypatch, counter_env):
    """The shop's GSTIN identity and serial mint are tested on their own
    (test_gst_invoice_number_16, test_counter_roles_no_cost_anywhere); stubbed
    here so the invoice door answers on this suite's store-less fake DB."""
    from api.services import print_identity

    monkeypatch.setattr(print_identity, "assert_store_gstin", lambda *_a, **_k: "20AAAAA0000A1Z5")
    monkeypatch.setattr(
        counter_env["order_repo"], "next_invoice_number", lambda *_a, **_k: "TST01/2627/00001"
    )


@pytest.mark.usefixtures("invoice_identity")
@pytest.mark.parametrize("order_type", ["quick_sale", "full_sale"])
def test_cashier_finishes_a_sale_quote_to_delivered(client, counter_env, order_type):
    cashier = _h("CASHIER")
    q = _quote(client, cashier)
    assert q.status_code == 200, q.text
    payable = q.json()["grand_total"]

    key = f"bill-{order_type}"
    r = client.post("/api/v1/orders", headers=_h("CASHIER", key=key), json=_body(order_type))
    assert r.status_code == 201, r.text
    order_id = r.json()["order_id"]
    # Lost acknowledgement: the till re-presses the SAME bill key -> the SAME
    # order comes back, nothing new is written.
    again = client.post("/api/v1/orders", headers=_h("CASHIER", key=key), json=_body(order_type))
    assert again.status_code in (200, 201), again.text
    assert again.json()["order_id"] == order_id
    before_pay = _ledger(counter_env)
    assert before_pay == {"count": 1, "by_status": ["DRAFT"], "paid": 0.0, "legs": 0, "leg_total": 0.0}

    stored = counter_env["order_repo"].find_by_id(order_id)
    assert stored["grand_total"] == payable  # the quote IS the bill

    leg = {"method": "CASH", "amount": payable}
    leg_h = _h("CASHIER", key=f"{key}-leg-1")
    p = client.post(f"/api/v1/orders/{order_id}/payments", headers=leg_h, json=leg)
    assert p.status_code == 200, p.text
    # A re-sent leg (lost answer) records nothing twice.
    p2 = client.post(f"/api/v1/orders/{order_id}/payments", headers=leg_h, json=leg)
    assert p2.status_code == 200, p2.text
    assert _ledger(counter_env) == {
        "count": 1, "by_status": ["CONFIRMED"], "paid": payable, "legs": 1, "leg_total": payable,
    }

    assert client.post(f"/api/v1/orders/{order_id}/ready", headers=cashier).status_code == 200
    d = client.post(
        f"/api/v1/orders/{order_id}/deliver",
        headers=cashier,
        json={"handover": {"delivered_by_id": CASHIER_ID, "delivered_by_name": "Meena"}},
    )
    assert d.status_code == 200, d.text
    inv = client.get(f"/api/v1/orders/{order_id}/invoice", headers=cashier)
    assert inv.status_code == 200, inv.text
    assert inv.json()["invoiceNumber"] == "TST01/2627/00001"

    doc = counter_env["order_repo"].find_by_id(order_id)
    assert doc["status"] == "DELIVERED"
    assert doc["payment_status"] == "PAID"
    assert doc["balance_due"] == 0.0
    assert doc["store_id"] == STORE
    assert doc["handover_record"]["delivered_by_id"] == CASHIER_ID
    assert _ledger(counter_env) == {
        "count": 1, "by_status": ["DELIVERED"], "paid": payable, "legs": 1, "leg_total": payable,
    }


# --------------------------------------------------------------------------- #
# 2. Role matrix on the two doors that refused the cashier
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "role,allowed",
    [
        ("CASHIER", True),
        ("SALES_STAFF", True),
        ("STORE_MANAGER", True),
        ("ADMIN", True),
        ("SUPERADMIN", True),
        ("WORKSHOP_STAFF", False),  # never bills, never takes money (owner 2026-11-01)
        ("ACCOUNTANT", False),
        ("CATALOG_MANAGER", False),
    ],
)
def test_quote_and_create_role_matrix(client, counter_env, role, allowed):
    h = _h(role, uid=f"u-{role.lower()}")
    q = _quote(client, h)
    r = client.post("/api/v1/orders", headers=h, json=_body())
    if allowed:
        assert q.status_code == 200, q.text
        assert r.status_code == 201, r.text
        assert _ledger(counter_env)["count"] == 1
    else:
        assert q.status_code == 403, q.text
        assert r.status_code == 403, r.text
        assert _ledger(counter_env)["count"] == 0


def test_workshop_staff_still_cannot_take_money_on_the_cashiers_bill(client, counter_env):
    r = client.post("/api/v1/orders", headers=_h("CASHIER"), json=_body())
    order_id = r.json()["order_id"]
    p = client.post(
        f"/api/v1/orders/{order_id}/payments",
        headers=_h("WORKSHOP_STAFF", uid="u-ws"),
        json={"method": "CASH", "amount": 1000.0},
    )
    assert p.status_code == 403, p.text
    assert _ledger(counter_env)["legs"] == 0


# --------------------------------------------------------------------------- #
# 3. Nothing wider: no cancel, no discount power, no other shop
# --------------------------------------------------------------------------- #
def test_cashier_still_cannot_cancel_a_sale(client, counter_env):
    order_id = client.post("/api/v1/orders", headers=_h("CASHIER"), json=_body()).json()["order_id"]
    why = {"reason": "customer changed mind"}
    c = client.post(f"/api/v1/orders/{order_id}/cancel", headers=_h("CASHIER"), params=why)
    assert c.status_code == 403, c.text
    assert counter_env["order_repo"].find_by_id(order_id)["status"] == "DRAFT"
    # The store manager (same shop) still can.
    m = client.post(f"/api/v1/orders/{order_id}/cancel", headers=_h("STORE_MANAGER", uid="u-sm"), params=why)
    assert m.status_code == 200, m.text
    assert counter_env["order_repo"].find_by_id(order_id)["status"] == "CANCELLED"


def test_cashier_discount_still_needs_a_manager(client, counter_env):
    # CASHIER's role discount cap is 0%; the same 5% passes for SALES_STAFF (10%).
    r = client.post("/api/v1/orders", headers=_h("CASHIER"), json=_body(discount_percent=5, discount_reason="loyal customer"))
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "DISCOUNT_NEEDS_APPROVAL"
    assert r.json()["detail"]["max_percent"] == 0.0
    assert _ledger(counter_env)["count"] == 0
    s = client.post("/api/v1/orders", headers=_h("SALES_STAFF", uid="u-ss"), json=_body(discount_percent=5, discount_reason="loyal customer"))
    assert s.status_code == 201, s.text


def test_other_shops_cashier_cannot_finish_this_shops_bill(client, counter_env):
    order_id = client.post("/api/v1/orders", headers=_h("CASHIER"), json=_body()).json()["order_id"]
    other = _h("CASHIER", uid="u-other", store="BV-OTHER-99")
    p = client.post(f"/api/v1/orders/{order_id}/payments", headers=other, json={"method": "CASH", "amount": 1000.0})
    assert p.status_code in (403, 404), p.text
    assert client.post(f"/api/v1/orders/{order_id}/ready", headers=other).status_code in (403, 404)
    doc = counter_env["order_repo"].find_by_id(order_id)
    assert (doc["status"], doc.get("payments") or []) == ("DRAFT", [])
    # A bill started at this shop cannot be rung from the other shop's session.
    r = client.post("/api/v1/orders", headers=other, json=dict(_body(), bill_store_id=STORE))
    assert r.status_code == 409, r.text
    assert _ledger(counter_env)["count"] == 1


def test_policy_rows_match_the_in_function_gate():
    from api.routers import orders as om
    from api.services import rbac_policy

    for path in ("/api/v1/orders", "/api/v1/orders/quote"):
        row = rbac_policy.policy_for("POST", path)
        assert set(row["allowed"]) == set(om.SALE_ROLES), path
