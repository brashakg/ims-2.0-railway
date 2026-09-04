"""
IMS 2.0 - COD collects the BALANCE, not the bill (shipping money fix)
======================================================================
PLAN_STATUS 4g finding: a cash-on-delivery booking told Shiprocket
``sub_total = grand_total`` - the whole bill - when the customer had paid a
deposit at the counter and owed only the balance. The courier then asked for
the deposit a second time at the door.

Shiprocket's create-adhoc body has NO separate COD-collectable field:
``sub_total`` (we send none of the shipping / giftwrap / transaction /
discount add-ons) IS the order total the courier collects on a COD parcel.
So a COD booking must put the order's SERVER-side ``balance_due`` there, and
refuse when there is nothing to collect or the balance is above the bill.

DISCRIMINATING POWER was MEASURED: the pre-fix copies of
services/shiprocket.py, services/delivery_gate.py and routers/shipping.py
were restored and this file re-run; the per-test outcome is recorded in the
PR. Fakes carry REAL money fields; no assertion reads comment text.

Refusals are asserted on the STABLE `detail["code"]`, never on the author's
own error prose - a reworded message is not a behaviour change, and a test
that fails when it is reworded teaches nothing (review round 1, finding 9).
The adversarial probes that found the round-1 bugs live alongside this file
in test_cod_probe_review.py.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
os.environ["DISPATCH_MODE"] = "off"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio  # noqa: E402

from fastapi import HTTPException  # noqa: E402

from api.services import shiprocket  # noqa: E402
from tests.test_delivery_money_gate import (  # noqa: E402
    FakeOrderRepo,
    _order,
    _ship_client,
    _ship_token,
)


def _boom(*_a, **_k):
    raise AssertionError("network must NOT be called in this test")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(shiprocket.httpx, "AsyncClient", _boom)
    monkeypatch.delenv("SHIPROCKET_EMAIL", raising=False)
    monkeypatch.delenv("SHIPROCKET_PASSWORD", raising=False)


def _run(coro):
    return asyncio.run(coro)


def _partly_paid(**extra):
    """Rs 3,000 bill, Rs 1,000 paid at the counter, Rs 2,000 still owed -
    the exact case the courier over-collected on."""
    order = {
        "order_id": "ORD-COD-1",
        "order_number": "INV-7001",
        "customer_name": "Asha",
        "customer_phone": "9876543210",
        "store_id": "BV-TEST-01",
        "grand_total": 3000.0,
        "amount_paid": 1000.0,
        "balance_due": 2000.0,
        "payment_status": "PARTIAL",
        "created_at": datetime(2026, 9, 1, 10, 0, 0),
        "items": [
            {"product_name": "Frame", "sku": "F-1", "quantity": 1, "item_total": 2000},
            {"product_name": "Lens", "sku": "L-1", "quantity": 1, "item_total": 1000},
        ],
    }
    order.update(extra)
    return order


_ADDR = {
    "address": "12 MG Road",
    "city": "Ranchi",
    "state": "Jharkhand",
    "pincode": "834001",
}


# ============================================================================
# 1. Payload builder (pure) - the field the courier collects on
# ============================================================================


def test_cod_payload_collects_balance_not_bill():
    p = shiprocket.build_shipment_payload(
        _partly_paid(), {**_ADDR, "payment_method": "COD"}
    )
    assert p["payment_method"] == "COD"
    assert p["sub_total"] == 2000.0  # what is OWED, not the Rs 3,000 bill
    # The line items still describe the goods in the box at their value.
    assert sum(i["selling_price"] for i in p["order_items"]) == 3000.0


def test_prepaid_payload_declares_full_bill():
    """Prepaid is untouched: nothing is collected, sub_total is the order
    value. (Regression guard - passes before and after the fix.)"""
    p = shiprocket.build_shipment_payload(_partly_paid(), dict(_ADDR))
    assert p["payment_method"] == "Prepaid"
    assert p["sub_total"] == 3000.0


def test_cod_payload_normalises_method_spelling():
    """A lower-case 'cod' from a client used to be forwarded verbatim while
    the router treated it as COD - now one predicate decides both."""
    p = shiprocket.build_shipment_payload(
        _partly_paid(), {**_ADDR, "payment_method": " cod "}
    )
    assert p["payment_method"] == "COD"
    assert p["sub_total"] == 2000.0


def test_cod_payload_refuses_fully_paid():
    with pytest.raises(HTTPException) as exc:
        shiprocket.build_shipment_payload(
            _partly_paid(amount_paid=3000.0, balance_due=0.0, payment_status="PAID"),
            {**_ADDR, "payment_method": "COD"},
        )
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "COD_NOTHING_TO_COLLECT"


def test_cod_payload_refuses_balance_above_bill():
    with pytest.raises(HTTPException) as exc:
        shiprocket.build_shipment_payload(
            _partly_paid(balance_due=5000.0),
            {**_ADDR, "payment_method": "COD"},
        )
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "COD_BALANCE_EXCEEDS_BILL"


# ============================================================================
# 2. SIMULATED bookings expose the courier body (no carrier needed)
# ============================================================================


def test_simulated_booking_carries_cod_amount(monkeypatch):
    monkeypatch.setattr(shiprocket, "dispatch_mode", lambda: "off")
    res = _run(
        shiprocket.create_shipment(
            _partly_paid(), {**_ADDR, "payment_method": "COD"}, db=None
        )
    )
    assert res.status == "SIMULATED"
    assert res.raw["payload"]["payment_method"] == "COD"
    assert res.raw["payload"]["sub_total"] == 2000.0


def test_simulated_without_creds_carries_payload_too(monkeypatch):
    """The second SIMULATED branch (live mode, creds unset) is the one
    production actually sits on today - it must expose the body as well."""
    monkeypatch.setattr(shiprocket, "dispatch_mode", lambda: "live")
    res = _run(
        shiprocket.create_shipment(
            _partly_paid(), {**_ADDR, "payment_method": "COD"}, db=None
        )
    )
    assert res.status == "SIMULATED"
    assert res.raw["payload"]["sub_total"] == 2000.0


# ============================================================================
# 3. The booking door (POST /shipments) - server figure, refusals, persistence
# ============================================================================


def _cod_body(order_id="ORD-C1", **extra):
    body = {"order_id": order_id, "address": {"payment_method": "COD"}}
    body.update(extra)
    return body


def test_book_cod_persists_balance_as_cod_amount(monkeypatch):
    """Rs 3,000 bill, Rs 1,000 paid: the courier is told Rs 2,000, and that
    figure is what the shipment doc records. Client-supplied amounts riding
    on the request (top-level or inside address) are ignored - the ORDER is
    the only source."""
    client, coll = _ship_client(
        monkeypatch,
        _order(order_id="ORD-C1", grand_total=3000.0, amount_paid=1000.0,
               balance_due=2000.0),
    )
    body = _cod_body(cod_amount=50, sub_total=50)
    body["address"]["cod_amount"] = 50
    body["address"]["sub_total"] = 50
    resp = client.post(
        "/api/v1/shipping/shipments",
        json=body,
        headers={"Authorization": f"Bearer {_ship_token(['CASHIER'])}"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["payment_method"] == "COD"
    assert data["cod_amount"] == 2000.0
    (doc,) = coll.docs
    assert doc["payment_method"] == "COD"
    assert doc["cod_amount"] == 2000.0


def test_book_cod_unpaid_web_order_collects_whole_bill(monkeypatch):
    """Nothing paid upstream -> the balance IS the bill; COD still collects
    it all (the web-COD import case the exemption exists for)."""
    client, coll = _ship_client(
        monkeypatch,
        _order(order_id="ORD-C1", payment_status="UNPAID",
               grand_total=18000.0, balance_due=18000.0),
    )
    resp = client.post(
        "/api/v1/shipping/shipments",
        json=_cod_body(),
        headers={"Authorization": f"Bearer {_ship_token(['CASHIER'])}"},
    )
    assert resp.status_code == 201, resp.text
    assert coll.docs[0]["cod_amount"] == 18000.0


def test_book_cod_refused_when_fully_paid(monkeypatch):
    client, coll = _ship_client(
        monkeypatch,
        _order(order_id="ORD-C1", payment_status="PAID", grand_total=3000.0,
               amount_paid=3000.0, balance_due=0.0),
    )
    resp = client.post(
        "/api/v1/shipping/shipments",
        json=_cod_body(),
        headers={"Authorization": f"Bearer {_ship_token(['STORE_MANAGER'])}"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "COD_NOTHING_TO_COLLECT"
    assert coll.docs == []  # nothing booked, nothing persisted


def test_book_cod_refused_when_balance_exceeds_bill(monkeypatch):
    client, coll = _ship_client(
        monkeypatch,
        _order(order_id="ORD-C1", grand_total=3000.0, balance_due=5000.0),
    )
    resp = client.post(
        "/api/v1/shipping/shipments",
        json=_cod_body(),
        headers={"Authorization": f"Bearer {_ship_token(['STORE_MANAGER'])}"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "COD_BALANCE_EXCEEDS_BILL"
    assert coll.docs == []


def test_book_cod_refused_when_order_unresolvable(monkeypatch):
    """A Prepaid booking of an unloadable order still books 'with request
    data only' (fail-soft, unchanged). COD cannot: the collectable amount
    lives on the order and nowhere else."""
    client, coll = _ship_client(monkeypatch, None)
    assert FakeOrderRepo(None).find_by_id("ORD-C1") is None  # fixture sanity
    resp = client.post(
        "/api/v1/shipping/shipments",
        json=_cod_body(),
        headers={"Authorization": f"Bearer {_ship_token(['STORE_MANAGER'])}"},
    )
    assert resp.status_code == 400, resp.text
    assert coll.docs == []


@pytest.mark.parametrize(
    "spelling", ["COD", " cod ", "cod", "Cod", None, "Prepaid", "prepaid", ""]
)
def test_persisted_shipment_matches_the_carrier_body(monkeypatch, spelling):
    """Adopted from the review (probe H1). Whatever spelling arrives, the
    shipment doc IMS keeps and the body the courier is handed must agree -
    that agreement is the whole point of one shared payment_kind(). A blank
    or absent method keeps the historical default, Prepaid."""
    seen = []
    real = shiprocket.create_shipment

    async def _capture(order, address, db=None, **kw):
        res = await real(order, address, db=db, **kw)
        seen.append(res.raw.get("payload"))
        return res

    monkeypatch.setattr(shiprocket, "create_shipment", _capture)
    client, coll = _ship_client(
        monkeypatch,
        _order(order_id="ORD-C1", grand_total=3000.0, amount_paid=1000.0,
               balance_due=2000.0),
    )
    address = {} if spelling is None else {"payment_method": spelling}
    resp = client.post(
        "/api/v1/shipping/shipments",
        json={"order_id": "ORD-C1", "address": address},
        headers={"Authorization": f"Bearer {_ship_token(['STORE_MANAGER'])}"},
    )
    assert resp.status_code == 201, resp.text
    (doc,) = coll.docs
    (payload,) = seen
    assert doc["payment_method"] == payload["payment_method"]
    if doc["payment_method"] == "COD":
        assert doc["cod_amount"] == payload["sub_total"] == 2000.0
    else:
        assert doc["cod_amount"] == 0.0 and payload["sub_total"] == 3000.0


def test_book_prepaid_records_zero_cod_amount(monkeypatch):
    client, coll = _ship_client(
        monkeypatch,
        _order(order_id="ORD-C1", payment_status="PAID", grand_total=3000.0,
               amount_paid=3000.0, balance_due=0.0),
    )
    resp = client.post(
        "/api/v1/shipping/shipments",
        json={"order_id": "ORD-C1"},
        headers={"Authorization": f"Bearer {_ship_token(['CASHIER'])}"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["payment_method"] == "Prepaid"
    assert coll.docs[0]["cod_amount"] == 0.0
