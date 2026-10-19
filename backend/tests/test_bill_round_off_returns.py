"""
IMS 2.0 - Bill round off on returns (owner ruling 2026-10-08). A FULL return
refunds what was paid -- the rounded bill; a PARTIAL return refunds its lines at
their real value and does not re-round the original, so the return that brings
back the last unit settles the round off. The credit note's taxable value and
tax are the lines' own; the round off is neither.
"""

from __future__ import annotations

import os
import sys

import pytest

# The doors under test number their GST documents (a return's credit note,
# a debit note): these fakes hold no store records, so a stub mint numbers
# them (conftest.gst_numbers). The mint is tested in test_gst_document_numbers.
pytestmark = pytest.mark.usefixtures("gst_numbers")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")


# ============================================================================
# Returns: a full return gives back what was paid, a partial one its lines
# ============================================================================


def _ro_order(prices, *, amount_paid=None):
    """A delivered till order whose lines carry the engine's own stamps."""
    from api.routers.orders import _compute_per_category_gst

    items = [
        {"item_id": f"li{i}", "product_id": f"PRD-{i}", "product_name": f"Frame {i}",
         "sku": f"SKU-{i}", "item_type": "FRAME", "category": "FRAME",
         "quantity": 1, "unit_price": p, "item_total": p}
        for i, p in enumerate(prices, start=1)
    ]
    gst = _compute_per_category_gst(items, 0)
    return {
        "order_id": "ORD-RO1", "order_number": "ORD-RO1-N", "customer_id": "CUST-1",
        "customer_name": "Asha", "payment_method": "UPI", "store_id": "BV-PUN-01",
        "status": "DELIVERED", "items": items, "tax_amount": gst["tax"],
        "grand_total": gst["grand_total"], "round_off": gst["round_off"],
        "amount_paid": gst["grand_total"] if amount_paid is None else amount_paid,
    }


def _returns_client(monkeypatch, order):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.routers import returns as returns_router
    from tests.test_returns_gst_refund import _FakeColl, _FakeCustomerRepo, _FakeOrderRepo

    returns_coll, ledger_coll = _FakeColl(), _FakeColl()

    class _FakeDB:
        is_connected = True

        def __init__(self):
            self.db = self

        def get_collection(self, name):
            return {"returns": returns_coll, "credit_note_ledger": ledger_coll}.get(
                name, _FakeColl()
            )

    fake_db = _FakeDB()
    monkeypatch.setattr(returns_router, "get_order_repository", lambda: _FakeOrderRepo(order))
    monkeypatch.setattr(returns_router, "get_customer_repository", lambda: _FakeCustomerRepo())
    monkeypatch.setattr(returns_router, "get_product_repository", lambda: None)
    monkeypatch.setattr(returns_router, "get_stock_repository", lambda: None)
    monkeypatch.setattr("api.dependencies.get_db", lambda: fake_db, raising=False)
    monkeypatch.setattr("api.dependencies.get_audit_repository", lambda: None, raising=False)
    app = FastAPI()
    app.include_router(returns_router.router, prefix="/api/v1/returns")
    return TestClient(app), returns_coll, ledger_coll


def _return_body(line_ids, return_type="RETURN"):
    return {
        "order_id": "ORD-RO1",
        "store_id": "BV-PUN-01",
        "return_type": return_type,
        "items": [
            {"order_item_id": f"li{i}", "product_id": f"PRD-{i}", "return_qty": 1,
             "unit_price": 0, "reason": "DEFECTIVE", "condition": "GOOD"}
            for i in line_ids
        ],
    }


def _hdr():
    from tests.test_returns_gst_refund import _staff_token

    return {"Authorization": f"Bearer {_staff_token(['ADMIN'])}"}


@pytest.mark.parametrize(
    "prices, paid", [([500.25, 500.24], 1000.0), ([500.25, 500.25], 1001.0)]
)
def test_full_return_refunds_what_was_paid(monkeypatch, prices, paid):
    order = _ro_order(prices)
    client, returns_coll, _ = _returns_client(monkeypatch, order)
    q = client.post("/api/v1/returns/quote", json=_return_body([1, 2]), headers=_hdr())
    assert q.status_code == 200, q.text
    assert q.json()["net_refund"] == paid
    r = client.post("/api/v1/returns", json=_return_body([1, 2]), headers=_hdr())
    assert r.status_code in (200, 201), r.text
    doc = returns_coll.docs[-1]
    assert doc["net_refund"] == paid
    assert doc["refund_amount"] == paid
    # The GST reversal is the lines' own tax, not the round off.
    assert doc["gst_breakup"]["tax"] == round(
        sum(i["tax_amount"] for i in order["items"]), 2
    )


def test_partial_return_refunds_lines_and_the_last_one_settles_the_round_off(
    monkeypatch,
):
    order = _ro_order([500.25, 500.24])  # pays 1000, round off -0.49
    client, returns_coll, _ = _returns_client(monkeypatch, order)
    r1 = client.post("/api/v1/returns", json=_return_body([1]), headers=_hdr())
    assert r1.status_code in (200, 201), r1.text
    assert returns_coll.docs[-1]["net_refund"] == 500.25  # its real value
    r2 = client.post("/api/v1/returns", json=_return_body([2]), headers=_hdr())
    assert r2.status_code in (200, 201), r2.text
    assert returns_coll.docs[-1]["net_refund"] == 499.75  # 500.24 - 0.49
    assert round(sum(d["net_refund"] for d in returns_coll.docs), 2) == 1000.0


def test_full_credit_note_keeps_round_off_out_of_taxable_and_tax(monkeypatch):
    order = _ro_order([500.25, 500.24])
    client, _, ledger_coll = _returns_client(monkeypatch, order)
    r = client.post(
        "/api/v1/returns", json=_return_body([1, 2], "CREDIT_NOTE"), headers=_hdr()
    )
    assert r.status_code in (200, 201), r.text
    entry = ledger_coll.docs[-1]
    assert entry["net_refund"] == 1000.0
    assert entry["tax"] == 47.64
    assert entry["taxable"] == 952.85




@pytest.mark.parametrize(
    "prices, first_refund",
    [
        # A frame and a free case: the bill rounds DOWN by more than the kept
        # line is worth, so the frame alone refunds what was paid, not 1000.49.
        ([1000.49, 0.0], 1000.0),
        ([1000.20, 0.10], 1000.0),  # bill 1000.30 -> 1000
    ],
)
def test_partial_return_never_refunds_more_than_was_paid(
    monkeypatch, prices, first_refund
):
    order = _ro_order(prices)
    assert order["grand_total"] == 1000.0 and order["round_off"] < 0
    client, returns_coll, ledger_coll = _returns_client(monkeypatch, order)
    q = client.post("/api/v1/returns/quote", json=_return_body([1]), headers=_hdr())
    assert q.status_code == 200, q.text
    assert q.json()["net_refund"] == first_refund
    r1 = client.post("/api/v1/returns", json=_return_body([1]), headers=_hdr())
    assert r1.status_code in (200, 201), r1.text  # was 400 at the amount-paid cap
    assert returns_coll.docs[-1]["net_refund"] == first_refund
    # The rest of the bill comes back for what is left of it, never below 0.
    r2 = client.post("/api/v1/returns", json=_return_body([2]), headers=_hdr())
    assert r2.status_code in (200, 201), r2.text
    assert round(sum(d["net_refund"] for d in returns_coll.docs), 2) == 1000.0
    assert all(d["net_refund"] >= 0 for d in returns_coll.docs)


def test_capped_partial_credit_note_keeps_the_lines_taxable_value(monkeypatch):
    order = _ro_order([1000.49, 0.0])
    frame = order["items"][0]
    client, _, ledger_coll = _returns_client(monkeypatch, order)
    r = client.post(
        "/api/v1/returns", json=_return_body([1], "CREDIT_NOTE"), headers=_hdr()
    )
    assert r.status_code in (200, 201), r.text
    entry = ledger_coll.docs[-1]
    assert entry["net_refund"] == 1000.0
    # The frame's own tax and taxable value; the -0.49 is neither.
    assert entry["tax"] == frame["tax_amount"]
    assert entry["taxable"] == frame["taxable_value"]


def test_rounded_up_bill_returned_one_unit_at_a_time_refunds_what_was_paid(
    monkeypatch,
):
    """The closing piece settles the round off counting the units EARLIER
    returns brought back. 3 x Rs 333.50 = 1000.50 is billed Rs 1001 (+0.50):
    returned a unit at a time it refunds 333.50, 333.50, then 334.00. Counting
    only this return's units, the last piece refunded 333.50 and the customer
    was 50 paise short of what was paid."""
    order = _ro_order([333.5])
    line = order["items"][0]
    line.update(quantity=3, item_total=1000.5)
    from api.routers.orders import _compute_per_category_gst

    gst = _compute_per_category_gst(order["items"], 0)
    order.update(tax_amount=gst["tax"], grand_total=gst["grand_total"],
                 round_off=gst["round_off"], amount_paid=gst["grand_total"])
    assert (order["grand_total"], order["round_off"]) == (1001.0, 0.5)
    client, returns_coll, _ = _returns_client(monkeypatch, order)
    for _ in range(3):
        r = client.post("/api/v1/returns", json=_return_body([1]), headers=_hdr())
        assert r.status_code in (200, 201), r.text
    assert [d["net_refund"] for d in returns_coll.docs] == [333.5, 333.5, 334.0]


def test_restocking_fee_is_kept_when_a_later_return_settles_the_round_off(
    monkeypatch,
):
    """What is left of the bill is measured on what earlier returns gave back
    BEFORE their fee. Bill 500.25 + 500.24 = 1000.49, paid Rs 1000: line 1
    comes back with a Rs 50 restocking fee (450.25), then line 2 (499.75) --
    Rs 950 in all, the fee kept. Measured on the net refund instead, the
    closing return paid 549.75 and handed the Rs 50 fee back."""
    order = _ro_order([500.25, 500.24])
    client, returns_coll, _ = _returns_client(monkeypatch, order)
    r1 = client.post(
        "/api/v1/returns", json=dict(_return_body([1]), restocking_fee=50),
        headers=_hdr(),
    )
    assert r1.status_code in (200, 201), r1.text
    r2 = client.post("/api/v1/returns", json=_return_body([2]), headers=_hdr())
    assert r2.status_code in (200, 201), r2.text
    assert [d["net_refund"] for d in returns_coll.docs] == [450.25, 499.75]
