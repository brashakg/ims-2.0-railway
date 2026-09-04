"""
IMS 2.0 - COD booking: the adversarial review of PR #1094, kept as tests
========================================================================
Round-1 review of "courier COD collects balance_due": 39 probes, each
asserting the CORRECT shop-day behaviour. 12 FAILED - those were the
findings - and 27 held. Every one is kept: the F* probes now pin the FIXED
behaviour, the H* ones stay as the attacks that held.

Findings, in the order they appear below:
  F0  the only Book-shipment button never sent payment_method, so a web COD
      order could not be shipped from the product at all (fixed in the UI -
      OrderShippingCard.tsx + its vitest; the backend half is pinned here).
  F1  an unrecognised spelling ("Cash on Delivery") was coerced to Prepaid.
  F2  a missing balance_due read as Rs 0 here and as the whole bill at the
      counter - contradictory refusals on one order.
  F3  a CANCELLED order (stock already released) was bookable COD.
  F4  re-booking inserted a second shipment: the balance collected twice.
  F5  no store scope on the booking door.
  F6  a stored "2,000.00" was a 500, not a 400.
  F7  create_shipment promised never to raise, then raised.
"""

from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
os.environ["DISPATCH_MODE"] = "off"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio  # noqa: E402

from fastapi import HTTPException  # noqa: E402

from api.routers import shipping as shipping_mod  # noqa: E402
from api.services import shiprocket  # noqa: E402
from api.services.delivery_gate import cod_collectable  # noqa: E402
from tests.test_delivery_money_gate import (  # noqa: E402
    _order,
    _ship_client,
    _ship_token,
)

H = lambda roles, **kw: {"Authorization": f"Bearer {_ship_token(roles, **kw)}"}  # noqa: E731
URL = "/api/v1/shipping/shipments"


def _partial(**extra):
    base = dict(order_id="ORD-P1", payment_status="PARTIAL", grand_total=3000.0,
                amount_paid=1000.0, balance_due=2000.0)
    base.update(extra)
    return _order(**base)


def _capture_payload(monkeypatch):
    """Record the exact body the carrier would receive (SIMULATED raw payload)."""
    seen = []
    real = shiprocket.create_shipment

    async def wrapped(order, address, db=None, **kw):
        res = await real(order, address, db=db, **kw)
        seen.append(res.raw.get("payload"))
        return res

    monkeypatch.setattr(shiprocket, "create_shipment", wrapped)
    return seen


# ============================================================================
# F0 (FIXED). The only Book-shipment button never sent payment_method, so the
#     web COD order the exemption exists for could not be shipped from the
#     product at all: {order_id, store_id} means Prepaid, and Prepaid on an
#     UNPAID order is a hard 400. OrderShippingCard.tsx now asks COD/Prepaid
#     and defaults to COD exactly here; that half is pinned in
#     frontend/src/components/orders/__tests__/OrderShippingCard.test.tsx.
#     This is the backend half: the body the fixed card sends must book.
# ============================================================================


def test_F0_web_cod_order_books_the_whole_bill(monkeypatch):
    """A Shopify COD order imports UNPAID with balance == bill. Booked the way
    the card now books it, the courier is told to collect the bill."""
    client, coll = _ship_client(
        monkeypatch,
        _order(order_id="ORD-W1", payment_status="UNPAID", grand_total=18000.0,
               balance_due=18000.0, source="shopify", shopify_order_id="123"),
    )
    r = client.post(URL, json={"order_id": "ORD-W1", "store_id": "BV-TEST-01",
                               "address": {"payment_method": "COD"}},
                    headers=H(["STORE_MANAGER"]))
    assert r.status_code == 201, r.text
    assert coll.docs[0]["cod_amount"] == 18000.0


def test_F0b_default_prepaid_still_refuses_the_unpaid_order(monkeypatch):
    """And the reason the card had to be fixed rather than the default
    flipped: an omitted method still means Prepaid, and Prepaid on an order
    with nothing paid ships goods with nothing collected at either end."""
    client, coll = _ship_client(
        monkeypatch,
        _order(order_id="ORD-W1", payment_status="UNPAID", grand_total=18000.0,
               balance_due=18000.0),
    )
    r = client.post(URL, json={"order_id": "ORD-W1"}, headers=H(["STORE_MANAGER"]))
    assert r.status_code == 400, r.text
    assert coll.docs == []


# ============================================================================
# F1 (FIXED). An unrecognised payment_method spelling was COERCED to Prepaid.
#     Manager + part-paid order -> the goods shipped and nothing was
#     collected. shiprocket.payment_kind now refuses it 400.
# ============================================================================


@pytest.mark.parametrize("spelling", ["Cash on Delivery", "cash_on_delivery", "C.O.D", "COD_"])
def test_F1_misspelt_cod_must_not_become_prepaid(monkeypatch, spelling):
    client, coll = _ship_client(monkeypatch, _partial())
    r = client.post(URL, json={"order_id": "ORD-P1", "address": {"payment_method": spelling}},
                    headers=H(["STORE_MANAGER"]))
    assert r.status_code == 400, (r.status_code, r.json())
    assert r.json()["detail"]["code"] == "UNKNOWN_PAYMENT_METHOD"
    assert coll.docs == []


def test_F1c_payment_kind_is_the_only_reading():
    """One helper, not a second copy: the router gate and the carrier payload
    both go through it, so no spelling can be COD at one and Prepaid at the
    other."""
    assert not hasattr(shiprocket, "is_cod")
    assert shiprocket.payment_kind(" cod ") == "COD"
    assert shiprocket.payment_kind("PREPAID") == "Prepaid"
    assert shiprocket.payment_kind(None) == shiprocket.payment_kind("") == "Prepaid"
    with pytest.raises(HTTPException) as e:
        shiprocket.payment_kind("Cash on Delivery")
    assert e.value.status_code == 400


# ============================================================================
# F2 (FIXED). Missing balance_due: cod_collectable read it as 0 (paid) while
#     counter add_payment door (orders.py:4813) reads it as grand_total (unpaid).
#     Such an order is refused BOTH ways with contradictory messages.
# ============================================================================


def test_F2_missing_balance_due_unpaid_order(monkeypatch):
    doc = _order(order_id="ORD-L1", payment_status="UNPAID", grand_total=3000.0,
                 amount_paid=0.0)
    doc.pop("balance_due")
    client, coll = _ship_client(monkeypatch, doc)
    cod = client.post(URL, json={"order_id": "ORD-L1", "address": {"payment_method": "COD"}},
                      headers=H(["STORE_MANAGER"]))
    pre = client.post(URL, json={"order_id": "ORD-L1"}, headers=H(["STORE_MANAGER"]))
    # Record the contradiction for the report, then assert the sane reading.
    print("COD ->", cod.status_code, cod.json().get("detail"))
    print("Prepaid ->", pre.status_code, pre.json().get("detail"))
    assert cod.status_code == 201, "an UNPAID order with no balance_due is owed grand_total"
    assert coll.docs[0]["cod_amount"] == 3000.0
    # The counter door reads the SAME helper, so the two doors cannot
    # disagree about what this order owes any more.
    from api.services.delivery_gate import order_balance_due

    assert order_balance_due(doc) == 3000.0


# ============================================================================
# F3 (FIXED). A CANCELLED order (stock already released) could be booked COD.
# ============================================================================


def test_F3_cancelled_order_not_bookable(monkeypatch):
    client, coll = _ship_client(
        monkeypatch, _partial(status="CANCELLED"))
    r = client.post(URL, json={"order_id": "ORD-P1", "address": {"payment_method": "COD"}},
                    headers=H(["STORE_MANAGER"]))
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "ORDER_NOT_SHIPPABLE"
    assert coll.docs == []


# ============================================================================
# F4 (FIXED). Re-booking the same order (retry after a courier no-show, split
#     parcel) inserted a SECOND COD collection of the same balance.
# ============================================================================


def test_F4_second_cod_booking_double_collects(monkeypatch):
    client, coll = _ship_client(monkeypatch, _partial())
    body = {"order_id": "ORD-P1", "address": {"payment_method": "COD"}}
    r1 = client.post(URL, json=body, headers=H(["STORE_MANAGER"]))
    assert r1.status_code == 201
    r2 = client.post(URL, json=body, headers=H(["STORE_MANAGER"]))
    assert r2.status_code == 409, r2.text
    detail = r2.json()["detail"]
    assert detail["code"] == "SHIPMENT_ALREADY_BOOKED"
    # It NAMES the parcel already out, so the shop can go look for it.
    assert detail["shipment_id"] == r1.json()["shipment_id"]
    assert sum(d["cod_amount"] for d in coll.docs) == 2000.0


def test_F4b_rebook_is_a_typed_out_act(monkeypatch):
    """The retry is legitimate when the first parcel is not coming - but only
    when the caller says so explicitly."""
    client, coll = _ship_client(monkeypatch, _partial())
    body = {"order_id": "ORD-P1", "address": {"payment_method": "COD"}}
    assert client.post(URL, json=body, headers=H(["STORE_MANAGER"])).status_code == 201
    r2 = client.post(URL, json={**body, "rebook": True}, headers=H(["STORE_MANAGER"]))
    assert r2.status_code == 201, r2.text
    assert len(coll.docs) == 2


def test_F4c_a_failed_booking_never_blocks_a_retry(monkeypatch):
    """A FAILED booking never reached a courier, so it is not a live parcel."""
    client, coll = _ship_client(monkeypatch, _partial())
    coll.docs.append({"shipment_id": "SHP-OLD", "order_id": "ORD-P1",
                      "status": "FAILED", "cod_amount": 0.0})
    r = client.post(URL, json={"order_id": "ORD-P1", "address": {"payment_method": "COD"}},
                    headers=H(["STORE_MANAGER"]))
    assert r.status_code == 201, r.text


# ============================================================================
# F5 (FIXED). Store scope: a CASHIER of another store could book COD for this
#     store's order (main had no validate_store_access here either).
# ============================================================================


def test_F5_other_store_cashier_cannot_book(monkeypatch):
    client, coll = _ship_client(monkeypatch, _partial())  # store BV-TEST-01
    r = client.post(URL, json={"order_id": "ORD-P1", "address": {"payment_method": "COD"}},
                    headers=H(["CASHIER"], store_id="BV-TEST-02"))
    assert r.status_code == 403, r.text
    assert coll.docs == []


def test_F5b_admin_keeps_cross_store_reach(monkeypatch):
    """validate_store_access already encodes the HQ carve-out - booking must
    not narrow it."""
    client, coll = _ship_client(monkeypatch, _partial())
    r = client.post(URL, json={"order_id": "ORD-P1", "address": {"payment_method": "COD"}},
                    headers=H(["ADMIN"], store_id="BV-TEST-02"))
    assert r.status_code == 201, r.text


# ============================================================================
# F6 (FIXED). A non-numeric legacy string was a ValueError -> 500, not a 400.
# ============================================================================


def test_F6_formatted_string_balance_is_400_not_500():
    with pytest.raises(HTTPException) as e:
        cod_collectable({"grand_total": 3000.0, "balance_due": "2,000.00"})
    assert e.value.status_code == 400
    assert e.value.detail["code"] == "AMOUNT_NOT_A_NUMBER"


def test_F6b_formatted_string_balance_through_the_door(monkeypatch):
    client, coll = _ship_client(monkeypatch, _partial(balance_due="2,000.00"))
    r = client.post(URL, json={"order_id": "ORD-P1", "address": {"payment_method": "COD"}},
                    headers=H(["STORE_MANAGER"]))
    assert r.status_code == 400, r.text
    assert coll.docs == []


# ============================================================================
# F7 (FIXED). create_shipment()'s docstring says NEVER raises; the new balance
#     check raised straight through it. The router still pre-validates and
#     answers 400 - this is the unattended caller's safety net.
# ============================================================================


@pytest.mark.parametrize("overrides,address", [
    (dict(amount_paid=3000.0, balance_due=0.0, payment_status="PAID"),
     {"payment_method": "COD"}),
    ({}, {"payment_method": "Cash on Delivery"}),
    (dict(balance_due="2,000.00"), {"payment_method": "COD"}),
])
def test_F7_create_shipment_never_raises(monkeypatch, overrides, address):
    monkeypatch.setattr(shiprocket, "dispatch_mode", lambda: "off")
    res = asyncio.run(
        shiprocket.create_shipment(_partial(**overrides), address, db=None)
    )
    assert isinstance(res, shiprocket.ShipResult)
    assert res.status == "FAILED" and res.ok is False
    assert res.error


# ============================================================================
# HOLD probes (expected to PASS)
# ============================================================================


@pytest.mark.parametrize("spelling", ["COD", " cod ", "cod", "Cod", None, "Prepaid", "prepaid", ""])
def test_H1_router_doc_and_carrier_payload_agree(monkeypatch, spelling):
    seen = _capture_payload(monkeypatch)
    client, coll = _ship_client(monkeypatch, _partial())
    addr = {} if spelling is None else {"payment_method": spelling}
    r = client.post(URL, json={"order_id": "ORD-P1", "address": addr},
                    headers=H(["STORE_MANAGER"]))
    assert r.status_code == 201, r.text
    (doc,) = coll.docs
    (payload,) = seen
    assert doc["payment_method"] == payload["payment_method"]
    if doc["payment_method"] == "COD":
        assert doc["cod_amount"] == payload["sub_total"] == 2000.0
    else:
        assert doc["cod_amount"] == 0.0 and payload["sub_total"] == 3000.0


@pytest.mark.parametrize(
    "balance,expect",
    [
        (0.01, "refuse"), (0.011, 0.01), (0.02, 0.02), (0.1 + 0.2, 0.3),
        ("2000.00", 2000.0), (2000, 2000.0), (2000.999, 2001.0),
        (-0.0, "refuse"), (-1000.0, "refuse"), (3000.0, 3000.0), (3000.01, 3000.01),
        (3000.02, "refuse"), (None, "refuse"), ("", "refuse"),
    ],
)
def test_H3_paise_edges(balance, expect):
    order = {"grand_total": 3000.0, "balance_due": balance}
    if expect == "refuse":
        with pytest.raises(HTTPException) as e:
            cod_collectable(order)
        assert e.value.status_code == 400
    else:
        assert cod_collectable(order) == expect


def test_H3b_negative_zero_message():
    with pytest.raises(HTTPException) as e:
        cod_collectable({"grand_total": 3000.0, "balance_due": -0.0})
    print("DETAIL:", e.value.detail)


def test_H4_prepaid_regression(monkeypatch):
    seen = _capture_payload(monkeypatch)
    # manager, part-paid, Prepaid -> credit decision, collects nothing (same as main)
    client, coll = _ship_client(monkeypatch, _partial())
    r = client.post(URL, json={"order_id": "ORD-P1"}, headers=H(["STORE_MANAGER"]))
    assert r.status_code == 201 and seen[-1]["sub_total"] == 3000.0
    assert seen[-1]["payment_method"] == "Prepaid"
    # cashier, part-paid, Prepaid -> 403 ; UNPAID -> 400 for everyone
    r = client.post(URL, json={"order_id": "ORD-P1"}, headers=H(["CASHIER"]))
    assert r.status_code == 403
    client, coll = _ship_client(monkeypatch, _partial(payment_status="UNPAID", amount_paid=0.0,
                                                      balance_due=3000.0))
    r = client.post(URL, json={"order_id": "ORD-P1"}, headers=H(["STORE_MANAGER"]))
    assert r.status_code == 400
    # grand_total missing -> subtotal fallback unchanged on Prepaid
    p = shiprocket.build_shipment_payload({"subtotal": 500.0, "items": []}, {})
    assert p["sub_total"] == 500.0 and p["payment_method"] == "Prepaid"


def test_H5_booking_reads_balance_fresh(monkeypatch):
    """A payment recorded between two bookings changes what the 2nd collects."""
    order = _partial()

    class Repo:
        def find_by_id(self, oid):
            return dict(order) if oid == order["order_id"] else None

    client, coll = _ship_client(monkeypatch, None)
    monkeypatch.setattr(shipping_mod, "get_order_repository", lambda: Repo())
    # rebook: the F4 fix refuses a silent second booking, and what this probe
    # is about is which BALANCE the next booking reads.
    body = {"order_id": "ORD-P1", "address": {"payment_method": "COD"}, "rebook": True}
    assert client.post(URL, json=body, headers=H(["STORE_MANAGER"])).json()["cod_amount"] == 2000.0
    order.update(amount_paid=2500.0, balance_due=500.0)
    assert client.post(URL, json=body, headers=H(["STORE_MANAGER"])).json()["cod_amount"] == 500.0
    order.update(amount_paid=3000.0, balance_due=0.0, payment_status="PAID")
    assert client.post(URL, json=body, headers=H(["STORE_MANAGER"])).status_code == 400


def test_H7_request_amounts_and_order_fields_cannot_steer(monkeypatch):
    """Client-supplied money anywhere in the body is ignored; only the DB order."""
    seen = _capture_payload(monkeypatch)
    client, coll = _ship_client(monkeypatch, _partial())
    body = {"order_id": "ORD-P1", "balance_due": 1, "grand_total": 1, "cod_amount": 1,
            "address": {"payment_method": "COD", "sub_total": 1, "balance_due": 1}}
    r = client.post(URL, json=body, headers=H(["CASHIER"]))
    assert r.status_code == 201 and seen[0]["sub_total"] == 2000.0


def test_F1b_differential_main_vs_branch_on_unknown_spelling():
    """DIFFERENTIAL (FIXED): exec main's shiprocket.py and compare the carrier
    body. main forwarded the raw string (a carrier-side reject at worst); the
    first cut of this PR coerced any non-'COD' string to Prepaid, which the
    carrier happily accepts and collects nothing on. The branch now refuses
    the booking outright."""
    import subprocess
    import types

    src = subprocess.run(
        ["git", "show", "origin/main:backend/api/services/shiprocket.py"],
        capture_output=True, text=True, check=True,
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ).stdout.replace("from ..utils.ist", "from api.utils.ist")
    main_mod = types.ModuleType("shiprocket_main_probe")
    sys.modules["shiprocket_main_probe"] = main_mod  # dataclass needs it registered
    exec(compile(src, "shiprocket_main", "exec"), main_mod.__dict__)

    order = _partial()
    for spelling in ("Cash on Delivery", "cash_on_delivery"):
        addr = {"pincode": "834001", "payment_method": spelling}
        # pylint: disable=no-member -- main_mod is exec'd at runtime
        on_main = main_mod.build_shipment_payload(order, addr)
        assert on_main["payment_method"] == spelling      # verbatim on main
        with pytest.raises(HTTPException) as exc:
            shiprocket.build_shipment_payload(order, addr)
        print(f"{spelling!r}: main={on_main['payment_method']!r}/"
              f"{on_main['sub_total']}  branch=400/{exc.value.detail['code']}")
        assert exc.value.status_code == 400


def test_H8_credit_tender_order_ships_cod_for_cash_balance():
    """payment_status CREDIT (pay-later promise): amount_paid excludes the
    promise, so the courier collects the real cash balance."""
    assert cod_collectable({"grand_total": 3000.0, "amount_paid": 0.0,
                            "balance_due": 3000.0, "payment_status": "CREDIT"}) == 3000.0
