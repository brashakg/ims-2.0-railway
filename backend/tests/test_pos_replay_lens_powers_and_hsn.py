"""Audit L2-POS-F03 + L1-F38 (POS, 2026-11-05).

L2-POS-F03: the bill fingerprint that decides "same bill -> replay" ignored a
lens line's powers, so the same Idempotency-Key with a changed sph returned the
FIRST order as if the changed bill had been saved. A changed power / axis /
lens option is now another bill (409 IDEMPOTENCY_BODY_MISMATCH); an identical
retry still replays exactly once.

L1-F38: a new POS line carried no HSN, so GSTR-1 silently filed it under 9004,
and an accepted master HSN/rate edit was ignored by the next sale. A new line
now freezes the live master HSN (create, add-item, quote), the GST engine
rates it by the product master's own HSN rule, and the export flags a line
with no HSN instead of filing it silently.

Codex 11-06 round 2: a bill saved under the old formula replays when the
retry is unchanged, judged against the saved order's own lens lines.

Codex 11-06 round 3: that judgement keeps each power on its whole financial
line, and a keyed bill with no fingerprint at all is judged the same way.

Owner ruling 2026-11-07 (round 4): a keyed bill with no fingerprint fails
closed. Every saved line and bill field is compared, not only the lens lines.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from tests.test_order_rx_validation import _item, _seed_rx, rx_orders  # noqa: E402,F401


def _seed(rx, *, pid, category, item_type, hsn=None, gst_rate=None, sku=None, mrp=2000.0):
    from api.routers import orders as orders_module
    from database.repositories.product_repository import ProductRepository

    repo = ProductRepository(rx["db"].get_collection("products"))
    repo.create({
        "product_id": pid, "name": pid, "sku": sku or pid, "category": category,
        "item_type": item_type, "mrp": mrp, "cost_price": 10.0,
        "discount_category": "MASS", "is_active": True,
        "hsn_code": hsn, "gst_rate": gst_rate,
    })
    rx["monkeypatch"].setattr(orders_module, "get_product_repository", lambda: repo)
    return repo


def _orders(rx):
    return rx["db"].get_collection("orders").docs


def _post(client, headers, items, key=None):
    h = dict(headers)
    if key:
        h["Idempotency-Key"] = key
    return client.post("/api/v1/orders", json={"customer_id": "cust-x", "items": items}, headers=h)


def _lens(rxid, **over):
    it = _item("LENS-P", item_type="LENS", category="OPTICAL_LENS", sph=0.0, cyl=-0.5, axis=90,
               add=None, prescription_id=rxid, unit_price=2000.0)
    it["lens_details"] = {"type": "SV", "material": "1.56", "coatings": ["ARC"]}
    it.update(over)
    return it


# =========================================================================== #
# L2-POS-F03 -- unit: each clinical field is part of the bill's identity
# =========================================================================== #
def _fp(**over):
    from api.routers.orders.create import order_body_fingerprint
    from api.routers.orders.models import OrderCreate

    line = {"item_type": "LENS", "product_id": "LENS-P", "quantity": 2, "unit_price": 2000.0,
            "prescription_id": "rx-1", "sph": -1.0, "cyl": -0.5, "axis": 90, "add": 1.0,
            "lens_options": {"tint": "grey"}, "lens_details": {"type": "PAL", "material": "1.6"}}
    line.update(over)
    frame = {"item_type": "FRAME", "product_id": "FR-1", "quantity": 1, "unit_price": 1000.0}
    return order_body_fingerprint(OrderCreate(customer_id="c", items=[line, frame]))


@pytest.mark.parametrize("field,value", [
    ("sph", -1.25), ("cyl", -0.75), ("axis", 180), ("add", 1.25),
    ("lens_options", {"tint": "brown"}), ("lens_details", {"type": "PAL", "material": "1.67"}),
    ("lens_details", None), ("sph", None),
])
def test_a_changed_clinical_field_is_another_bill(field, value):
    assert _fp(**{field: value}) != _fp()


def test_an_equivalent_representation_is_the_same_bill():
    from api.routers.orders.create import order_body_fingerprint
    from api.routers.orders.models import OrderCreate

    # -1 vs -1.0, dict key order, and line order are the same bill.
    assert _fp(sph=-1, lens_details={"material": "1.6", "type": "PAL"}) == _fp()
    a = {"item_type": "FRAME", "product_id": "FR-1", "quantity": 1, "unit_price": 1000.0}
    b = {"item_type": "LENS", "product_id": "L", "quantity": 2, "unit_price": 500.0, "sph": -1.0}
    assert (order_body_fingerprint(OrderCreate(customer_id="c", items=[a, b]))
            == order_body_fingerprint(OrderCreate(customer_id="c", items=[b, a])))


def test_a_line_without_lens_data_hashes_exactly_as_before():
    """A draft saved before this change still replays: a frame bill's
    fingerprint is the pre-change formula, byte for byte."""
    from api.routers.orders.create import order_body_fingerprint
    from api.routers.orders.models import OrderCreate

    order = OrderCreate(customer_id="c", items=[{"item_type": "FRAME", "product_id": "FR-1", "quantity": 1,
                                                 "unit_price": 1000.0}])
    old = {"customer_id": "c", "patient_id": "", "order_type": order.order_type or "",
           "lines": [["FRAME", "FR-1", "", 1, 1000.0, 0.0, "", ""]],
           "cart_discount_percent": 0.0, "cart_discount_amount": 0.0}
    assert order_body_fingerprint(order) == hashlib.sha256(
        json.dumps(old, sort_keys=True).encode("utf-8")).hexdigest()


# =========================================================================== #
# L2-POS-F03 -- the real door: POST /orders under one key
# =========================================================================== #
def test_a_changed_lens_power_under_the_same_key_is_refused(client, staff_headers, rx_orders):
    _seed(rx_orders, pid="LENS-P", category="OPTICAL_LENS", item_type="LENS", hsn="900150")
    rxid = _seed_rx(rx_orders, prescription_id="rx-f03")
    r1 = _post(client, staff_headers, [_lens(rxid)], key="K-F03")
    assert r1.status_code in (200, 201), r1.text
    for change in ({"sph": -1.0}, {"cyl": -0.75}, {"axis": 180},
                   {"lens_details": {"type": "SV", "material": "1.67", "coatings": ["ARC"]}}):
        r2 = _post(client, staff_headers, [_lens(rxid, **change)], key="K-F03")
        assert r2.status_code == 409, (change, r2.text)
        assert r2.json()["detail"]["code"] == "IDEMPOTENCY_BODY_MISMATCH"
    # Reconciliation: ONE order, still the first bill's powers.
    docs = _orders(rx_orders)
    assert len(docs) == 1
    line = docs[0]["items"][0]
    assert (line["sph"], line["cyl"], line["axis"]) == (0.0, -0.5, 90)
    assert line["lens_details"]["material"] == "1.56"


def test_an_identical_lens_retry_replays_exactly_once(client, auth_headers, staff_headers, rx_orders):
    _seed(rx_orders, pid="LENS-P", category="OPTICAL_LENS", item_type="LENS", hsn="900150")
    rxid = _seed_rx(rx_orders, prescription_id="rx-f03b")
    r1 = _post(client, staff_headers, [_lens(rxid)], key="K-F03B")
    r2 = _post(client, staff_headers, [_lens(rxid, sph=-0.0)], key="K-F03B")
    assert r1.status_code in (200, 201) and r2.status_code in (200, 201), r2.text
    assert r2.json()["order_id"] == r1.json()["order_id"]
    # HQ (SUPERADMIN) in the same shop: its own key, its own bill.
    r3 = _post(client, auth_headers, [_lens(rxid)], key="K-F03-HQ")
    assert r3.status_code in (200, 201), r3.text
    assert len(_orders(rx_orders)) == 2


def test_a_lost_insert_ack_with_changed_powers_is_refused(client, staff_headers, rx_orders, monkeypatch):
    """The other replay door: the pre-check misses (a racing first press) and
    the unique key index refuses the insert -> the twin is judged by the SAME
    fingerprint, so changed powers are refused, not replayed."""
    from api.routers import orders as orders_module

    _seed(rx_orders, pid="LENS-P", category="OPTICAL_LENS", item_type="LENS", hsn="900150")
    rxid = _seed_rx(rx_orders, prescription_id="rx-f03c")
    r1 = _post(client, staff_headers, [_lens(rxid)], key="K-F03C")
    assert r1.status_code in (200, 201), r1.text
    repo = orders_module.get_order_repository()
    real_find_one = repo.find_one
    calls = {"n": 0}

    def _find_one(q, *a, **k):
        if "idempotency_key" in q and calls["n"] == 0:
            calls["n"] += 1
            return None  # the racing press's pre-check missed
        return real_find_one(q, *a, **k)

    def _dup(*a, **k):
        raise Exception("E11000 duplicate key error index: store_id_1_idempotency_key_1")

    monkeypatch.setattr(repo, "find_one", _find_one)
    monkeypatch.setattr(repo, "create_unique", _dup)
    r2 = _post(client, staff_headers, [_lens(rxid, sph=-1.0)], key="K-F03C")
    assert r2.status_code == 409, r2.text
    assert r2.json()["detail"]["code"] == "IDEMPOTENCY_BODY_MISMATCH"
    calls["n"] = 0
    r3 = _post(client, staff_headers, [_lens(rxid)], key="K-F03C")
    assert r3.status_code in (200, 201) and r3.json()["order_id"] == r1.json()["order_id"], r3.text
    assert len(_orders(rx_orders)) == 1


# =========================================================================== #
# L2-POS-F03 round 2 -- a bill saved BEFORE lens data joined the fingerprint
# =========================================================================== #
def _old_format_fp(items):
    """The formula a pre-deploy order was saved with, copied literally: the
    production code must keep recognising hashes already in the database."""
    from api.routers.orders.models import OrderCreate

    order = OrderCreate(customer_id="cust-x", items=items)
    lines = sorted(
        [str(i.item_type or "").upper(), str(i.product_id or ""), str(i.stock_id or ""), int(i.quantity or 0),
         round(float(i.unit_price or 0), 2), round(float(i.discount_percent or 0), 4), str(i.prescription_id or ""),
         str(i.lens_line_id or "")]
        for i in order.items)
    body = {"customer_id": "cust-x", "patient_id": "", "order_type": order.order_type or "", "lines": lines,
            "cart_discount_percent": 0.0, "cart_discount_amount": 0.0}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


def _old_bill(client, headers, rx, key, items):
    """A saved bill exactly as a pre-deploy press left it. Returns its id."""
    r = _post(client, headers, items, key=key)
    assert r.status_code in (200, 201), r.text
    docs = _orders(rx)
    assert len(docs) == 1
    docs[0]["body_fingerprint"] = _old_format_fp(items)
    return r.json()["order_id"]


def _lost_ack(monkeypatch):
    """The other replay door: the pre-check misses and the unique key index
    refuses the insert, so the twin answers."""
    from api.routers import orders as orders_module

    repo = orders_module.get_order_repository()
    real_find_one = repo.find_one
    missed = []

    def _find_one(q, *a, **k):
        if "idempotency_key" in q and not missed:
            missed.append(1)
            return None
        return real_find_one(q, *a, **k)

    def _dup(*a, **k):
        raise Exception("E11000 duplicate key error index: store_id_1_idempotency_key_1")

    monkeypatch.setattr(repo, "find_one", _find_one)
    monkeypatch.setattr(repo, "create_unique", _dup)


@pytest.mark.parametrize("door", ["pre-check", "lost-ack"])
def test_an_old_format_unchanged_lens_retry_replays_with_no_effect(client, staff_headers, rx_orders, monkeypatch, door):
    _seed(rx_orders, pid="LENS-P", category="OPTICAL_LENS", item_type="LENS", hsn="900150")
    rxid = _seed_rx(rx_orders, prescription_id="rx-old")
    oid = _old_bill(client, staff_headers, rx_orders, "K-OLD", [_lens(rxid)])
    before = copy.deepcopy(_orders(rx_orders))
    if door == "lost-ack":
        _lost_ack(monkeypatch)
    r = _post(client, staff_headers, [_lens(rxid)], key="K-OLD")
    assert r.status_code in (200, 201), r.text
    assert r.json()["order_id"] == oid
    assert _orders(rx_orders) == before  # the saved order is untouched


@pytest.mark.parametrize("door", ["pre-check", "lost-ack"])
@pytest.mark.parametrize("change", [
    {"sph": -1.0}, {"cyl": -0.75}, {"axis": 180}, {"add": 1.25}, {"lens_options": {"tint": "grey"}},
    {"lens_details": {"type": "SV", "material": "1.67", "coatings": ["ARC"]}},
    {"lens_details": None},
])
def test_an_old_format_key_refuses_changed_clinical_intent(client, staff_headers, rx_orders, monkeypatch, door, change):
    _seed(rx_orders, pid="LENS-P", category="OPTICAL_LENS", item_type="LENS", hsn="900150")
    rxid = _seed_rx(rx_orders, prescription_id="rx-old2")
    _old_bill(client, staff_headers, rx_orders, "K-OLD2", [_lens(rxid)])
    before = copy.deepcopy(_orders(rx_orders))
    if door == "lost-ack":
        _lost_ack(monkeypatch)
    r = _post(client, staff_headers, [_lens(rxid, **change)], key="K-OLD2")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "IDEMPOTENCY_BODY_MISMATCH"
    assert _orders(rx_orders) == before


def test_an_old_format_key_refuses_a_retry_that_drops_the_powers(client, staff_headers, rx_orders):
    """Without its lens data the changed body hashes EXACTLY like the old
    saved one, so the hash alone calls it the same bill."""
    _seed(rx_orders, pid="LENS-P", category="OPTICAL_LENS", item_type="LENS", hsn="900150")
    rxid = _seed_rx(rx_orders, prescription_id="rx-old3")
    _old_bill(client, staff_headers, rx_orders, "K-OLD3", [_lens(rxid)])
    bare = _lens(rxid)
    for field in ("sph", "cyl", "axis", "lens_details"):
        bare.pop(field)
    r = _post(client, staff_headers, [bare], key="K-OLD3")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "IDEMPOTENCY_BODY_MISMATCH"


def test_an_old_format_key_refuses_powers_swapped_between_two_lenses(client, staff_headers, rx_orders):
    """Both eyes' powers are still on the bill, on the wrong lenses."""
    repo = _seed(rx_orders, pid="LENS-P", category="OPTICAL_LENS", item_type="LENS", hsn="900150")
    repo.create({"product_id": "LENS-Q", "name": "LENS-Q", "sku": "LENS-Q", "category": "OPTICAL_LENS",
                 "item_type": "LENS", "mrp": 2000.0, "cost_price": 10.0, "discount_category": "MASS",
                 "is_active": True, "hsn_code": "900150"})
    rxid = _seed_rx(rx_orders, prescription_id="rx-old4")
    left = _lens(rxid, sph=-1.0)
    right = _lens(rxid, product_id="LENS-Q", product_name="LENS-Q", sph=-2.0)
    _old_bill(client, staff_headers, rx_orders, "K-OLD4", [left, right])
    r = _post(client, staff_headers, [{**left, "sph": -2.0}, {**right, "sph": -1.0}], key="K-OLD4")
    assert r.status_code == 409, r.text
    # The same two lenses listed the other way round are the same bill.
    r = _post(client, staff_headers, [right, left], key="K-OLD4")
    assert r.status_code in (200, 201), r.text


def test_an_old_format_retry_by_sku_alias_replays(client, staff_headers, rx_orders):
    """The saved line holds the canonical product; the retry names its SKU."""
    _seed(rx_orders, pid="LENS-P", category="OPTICAL_LENS", item_type="LENS", hsn="900150", sku="LENS-SKU")
    rxid = _seed_rx(rx_orders, prescription_id="rx-old5")
    line = _lens(rxid, product_id="LENS-SKU")
    oid = _old_bill(client, staff_headers, rx_orders, "K-OLD5", [line])
    assert _orders(rx_orders)[0]["items"][0]["product_id"] == "LENS-P"
    r = _post(client, staff_headers, [line], key="K-OLD5")
    assert r.status_code in (200, 201) and r.json()["order_id"] == oid, r.text
    assert _post(client, staff_headers, [{**line, "sph": -1.0}], key="K-OLD5").status_code == 409


def test_a_frame_bill_keeps_replaying_after_a_later_edit(client, staff_headers, rx_orders):
    """No lens intent on either side: the saved lines hold nothing to compare,
    so an order edited after the first press replays as it always did."""
    _seed(rx_orders, pid="FR-9", category="FRAME", item_type="FRAME", hsn="900311")
    line = _item("FR-9", item_type="FRAME", category="FRAME")
    r1 = _post(client, staff_headers, [line], key="K-FR9")
    assert r1.status_code in (200, 201), r1.text
    _orders(rx_orders)[0]["items"][0]["quantity"] = 3
    r2 = _post(client, staff_headers, [line], key="K-FR9")
    assert r2.status_code in (200, 201) and r2.json()["order_id"] == r1.json()["order_id"], r2.text


# =========================================================================== #
# L2-POS-F03 round 3 -- each power stays on ITS OWN financial line
# =========================================================================== #
def _twin_lines(rxid, **each):
    """One lens, one Rx, one quantity: the two lines differ only in money and power."""
    dear = _lens(rxid, unit_price=2000.0, sph=0.0, **each)
    cheap = _lens(rxid, unit_price=1900.0, sph=-1.0, discount_reason="loyal customer", **each)
    return dear, cheap


@pytest.mark.parametrize("door", ["pre-check", "lost-ack"])
def test_an_old_format_key_refuses_powers_swapped_between_two_prices_of_one_lens(
        client, staff_headers, rx_orders, monkeypatch, door):
    """Codex round 3: both powers are still on the bill and the old hash sees
    the same prices, yet the Rs 2000 line now grinds the Rs 1900 line's power."""
    _seed(rx_orders, pid="LENS-P", category="OPTICAL_LENS", item_type="LENS", hsn="900150")
    rxid = _seed_rx(rx_orders, prescription_id="rx-twin")
    dear, cheap = _twin_lines(rxid)
    _old_bill(client, staff_headers, rx_orders, "K-TWIN", [dear, cheap])
    before = copy.deepcopy(_orders(rx_orders))
    if door == "lost-ack":
        _lost_ack(monkeypatch)
    r = _post(client, staff_headers, [{**dear, "sph": -1.0}, {**cheap, "sph": 0.0}], key="K-TWIN")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "IDEMPOTENCY_BODY_MISMATCH"
    assert _orders(rx_orders) == before


def test_an_old_format_key_refuses_powers_swapped_between_two_discounts_of_one_lens(client, staff_headers, rx_orders):
    _seed(rx_orders, pid="LENS-P", category="OPTICAL_LENS", item_type="LENS", hsn="900150")
    rxid = _seed_rx(rx_orders, prescription_id="rx-twin-d")
    full = _lens(rxid, sph=0.0)
    off = _lens(rxid, sph=-1.0, discount_percent=5, discount_reason="loyal customer")
    _old_bill(client, staff_headers, rx_orders, "K-TWIN-D", [full, off])
    r = _post(client, staff_headers, [{**full, "sph": -1.0}, {**off, "sph": 0.0}], key="K-TWIN-D")
    assert r.status_code == 409, r.text


@pytest.mark.parametrize("door", ["pre-check", "lost-ack"])
def test_an_old_format_two_price_bill_replays_unchanged_and_with_whole_lines_reordered(
        client, staff_headers, rx_orders, monkeypatch, door):
    _seed(rx_orders, pid="LENS-P", category="OPTICAL_LENS", item_type="LENS", hsn="900150", sku="LENS-SKU")
    rxid = _seed_rx(rx_orders, prescription_id="rx-twin2")
    dear, cheap = _twin_lines(rxid, product_id="LENS-SKU")
    oid = _old_bill(client, staff_headers, rx_orders, "K-TWIN2", [dear, cheap])
    before = copy.deepcopy(_orders(rx_orders))
    if door == "lost-ack":
        _lost_ack(monkeypatch)
    r = _post(client, staff_headers, [dear, cheap], key="K-TWIN2")
    assert r.status_code in (200, 201) and r.json()["order_id"] == oid, r.text
    r = _post(client, staff_headers, [cheap, dear], key="K-TWIN2")  # SKU alias, whole lines swapped
    assert r.status_code in (200, 201) and r.json()["order_id"] == oid, r.text
    assert _orders(rx_orders) == before


# =========================================================================== #
# L2-POS-F03 round 3 -- a keyed bill saved before any fingerprint existed: its
# own saved lines are the only evidence, and nothing is back-filled onto it
# =========================================================================== #
def _no_fp_bill(client, headers, rx, key, items):
    r = _post(client, headers, items, key=key)
    assert r.status_code in (200, 201), r.text
    docs = _orders(rx)
    assert len(docs) == 1
    docs[0].pop("body_fingerprint")
    return r.json()["order_id"]


@pytest.mark.parametrize("door", ["pre-check", "lost-ack"])
@pytest.mark.parametrize("change", [
    {"sph": -1.0}, {"cyl": -0.75}, {"axis": 180}, {"add": 1.25}, {"lens_options": {"tint": "grey"}},
    {"lens_details": {"type": "SV", "material": "1.67", "coatings": ["ARC"]}},
    {"lens_details": None}, {"sph": None, "cyl": None, "axis": None, "lens_details": None},
    {"prescription_id": "rx-other"}, {"lens_line_id": "LL-other"},
])
def test_a_no_fingerprint_key_refuses_changed_clinical_intent(client, staff_headers, rx_orders, monkeypatch, door, change):
    _seed(rx_orders, pid="LENS-P", category="OPTICAL_LENS", item_type="LENS", hsn="900150")
    rxid = _seed_rx(rx_orders, prescription_id="rx-nofp")
    _seed_rx(rx_orders, prescription_id="rx-other")
    _no_fp_bill(client, staff_headers, rx_orders, "K-NOFP", [_lens(rxid)])
    before = copy.deepcopy(_orders(rx_orders))
    if door == "lost-ack":
        _lost_ack(monkeypatch)
    r = _post(client, staff_headers, [_lens(rxid, **change)], key="K-NOFP")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "IDEMPOTENCY_BODY_MISMATCH"
    assert _orders(rx_orders) == before  # no fingerprint invented, nothing rewritten


@pytest.mark.parametrize("door", ["pre-check", "lost-ack"])
def test_a_no_fingerprint_unchanged_lens_retry_replays_with_no_effect(client, staff_headers, rx_orders, monkeypatch, door):
    _seed(rx_orders, pid="LENS-P", category="OPTICAL_LENS", item_type="LENS", hsn="900150", sku="LENS-SKU")
    rxid = _seed_rx(rx_orders, prescription_id="rx-nofp2")
    dear, cheap = _twin_lines(rxid, product_id="LENS-SKU")
    oid = _no_fp_bill(client, staff_headers, rx_orders, "K-NOFP2", [dear, cheap])
    before = copy.deepcopy(_orders(rx_orders))
    if door == "lost-ack":
        _lost_ack(monkeypatch)
    for items in ([dear, cheap], [cheap, dear]):
        r = _post(client, staff_headers, items, key="K-NOFP2")
        assert r.status_code in (200, 201) and r.json()["order_id"] == oid, r.text
    assert _orders(rx_orders) == before


def test_a_no_fingerprint_key_refuses_powers_swapped_between_two_prices(client, staff_headers, rx_orders):
    _seed(rx_orders, pid="LENS-P", category="OPTICAL_LENS", item_type="LENS", hsn="900150")
    rxid = _seed_rx(rx_orders, prescription_id="rx-nofp3")
    dear, cheap = _twin_lines(rxid)
    _no_fp_bill(client, staff_headers, rx_orders, "K-NOFP3", [dear, cheap])
    r = _post(client, staff_headers, [{**dear, "sph": -1.0}, {**cheap, "sph": 0.0}], key="K-NOFP3")
    assert r.status_code == 409, r.text


def test_a_no_fingerprint_key_refuses_a_lens_added_to_a_bill_that_had_none(client, staff_headers, rx_orders):
    _seed(rx_orders, pid="FR-9", category="FRAME", item_type="FRAME", hsn="900311")
    rxid = _seed_rx(rx_orders, prescription_id="rx-nofp4")
    line = _item("FR-9", item_type="FRAME", category="FRAME")
    _no_fp_bill(client, staff_headers, rx_orders, "K-NOFP4", [line])
    r = _post(client, staff_headers, [{**line, "sph": -1.0, "prescription_id": rxid}], key="K-NOFP4")
    assert r.status_code == 409, r.text


@pytest.mark.parametrize("field,value", [("customer_id", "cust-other"), ("patient_id", "patient-other")])
def test_a_no_fingerprint_key_refuses_a_lens_bill_for_someone_else(client, staff_headers, rx_orders, field, value):
    """Same powers, another person's eyes: the customer and the billed member
    are on the saved order, so they are compared when the bill has lens intent."""
    _seed(rx_orders, pid="LENS-P", category="OPTICAL_LENS", item_type="LENS", hsn="900150")
    rxid = _seed_rx(rx_orders, prescription_id="rx-nofp5")
    oid = _no_fp_bill(client, staff_headers, rx_orders, "K-NOFP5", [_lens(rxid)])
    h = {**staff_headers, "Idempotency-Key": "K-NOFP5"}
    same = {"customer_id": "cust-x", "items": [_lens(rxid)]}
    r = client.post("/api/v1/orders", json=same, headers=h)
    assert r.status_code in (200, 201) and r.json()["order_id"] == oid, r.text
    saved_patient = _orders(rx_orders)[0]["patient_id"]
    r = client.post("/api/v1/orders", json={**same, "patient_id": saved_patient}, headers=h)
    assert r.status_code in (200, 201) and r.json()["order_id"] == oid, r.text
    r = client.post("/api/v1/orders", json={**same, field: value}, headers=h)
    assert r.status_code == 409, r.text


def test_an_rx_only_line_is_lens_intent_when_no_hash_has_seen_the_rx(client, staff_headers, rx_orders):
    _seed(rx_orders, pid="FR-9", category="FRAME", item_type="FRAME", hsn="900311")
    rxid = _seed_rx(rx_orders, prescription_id="rx-nofp7")
    other = _seed_rx(rx_orders, prescription_id="rx-nofp8")
    line = _item("FR-9", item_type="FRAME", category="FRAME", prescription_id=rxid)
    oid = _no_fp_bill(client, staff_headers, rx_orders, "K-NOFP7", [line])
    r = _post(client, staff_headers, [line], key="K-NOFP7")
    assert r.status_code in (200, 201) and r.json()["order_id"] == oid, r.text
    assert _post(client, staff_headers, [{**line, "prescription_id": other}], key="K-NOFP7").status_code == 409


def test_a_hashed_rx_only_line_keeps_replaying_after_a_later_edit(client, staff_headers, rx_orders):
    """A hash has seen this Rx, so the saved line is not re-judged."""
    _seed(rx_orders, pid="FR-9", category="FRAME", item_type="FRAME", hsn="900311")
    rxid = _seed_rx(rx_orders, prescription_id="rx-nofp9")
    line = _item("FR-9", item_type="FRAME", category="FRAME", prescription_id=rxid)
    r1 = _post(client, staff_headers, [line], key="K-NOFP9")
    assert r1.status_code in (200, 201), r1.text
    _orders(rx_orders)[0]["items"][0]["quantity"] = 3
    r2 = _post(client, staff_headers, [line], key="K-NOFP9")
    assert r2.status_code in (200, 201) and r2.json()["order_id"] == r1.json()["order_id"], r2.text


def test_a_no_fingerprint_frame_bill_replays_only_as_saved(client, staff_headers, rx_orders):
    """Owner ruling 2026-11-07 (fail closed): with no hash, the saved bill's own
    fields are the evidence. The unchanged retry replays; a changed price is
    refused and points to the Orders screen. Nothing moves either way."""
    _seed(rx_orders, pid="FR-9", category="FRAME", item_type="FRAME", hsn="900311")
    line = _item("FR-9", item_type="FRAME", category="FRAME")
    oid = _no_fp_bill(client, staff_headers, rx_orders, "K-NOFP6", [line])
    before = copy.deepcopy(_orders(rx_orders))
    r = _post(client, staff_headers, [line], key="K-NOFP6")
    assert r.status_code in (200, 201) and r.json()["order_id"] == oid, r.text
    r = _post(client, staff_headers, [{**line, "unit_price": 1900.0, "discount_reason": "loyal customer"}], key="K-NOFP6")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "IDEMPOTENCY_BODY_MISMATCH"
    assert "Orders screen" in r.json()["detail"]["message"]
    assert _orders(rx_orders) == before


def _two_line_bill(rx, price=2000.0):
    _seed(rx, pid="FR-8", category="FRAME", item_type="FRAME", hsn="900311")
    _seed(rx, pid="FR-9", category="FRAME", item_type="FRAME", hsn="900311", sku="FR-SKU")
    return [_item("FR-9", item_type="FRAME", category="FRAME", unit_price=price),
            _item("custom-fit", item_type="SERVICE", category="SERVICES", unit_price=50.0)]


@pytest.mark.parametrize("door", ["pre-check", "lost-ack"])
@pytest.mark.parametrize("change", [
    {"unit_price": 1900.0, "discount_reason": "loyal customer"}, {"quantity": 2},
    {"discount_percent": 5.0, "discount_reason": "changed"}, {"stock_id": "UNIT-OTHER"}, {"item_type": "SUNGLASS"},
    {"product_id": "FR-8"},
], ids=["price", "quantity", "discount", "unit", "type", "product"])
def test_a_no_fingerprint_bill_refuses_a_changed_non_lens_line(client, staff_headers, rx_orders, monkeypatch, door, change):
    lines = _two_line_bill(rx_orders)
    _no_fp_bill(client, staff_headers, rx_orders, "K-NF-L", lines)
    before = copy.deepcopy(_orders(rx_orders))
    if door == "lost-ack":
        _lost_ack(monkeypatch)
    r = _post(client, staff_headers, [{**lines[0], **change}, lines[1]], key="K-NF-L")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "IDEMPOTENCY_BODY_MISMATCH"
    assert _orders(rx_orders) == before


def test_a_no_fingerprint_bill_refuses_a_line_added_or_dropped(client, staff_headers, rx_orders):
    lines = _two_line_bill(rx_orders)
    _no_fp_bill(client, staff_headers, rx_orders, "K-NF-N", lines)
    before = copy.deepcopy(_orders(rx_orders))
    for items in (lines[:1], lines + [{**lines[1], "product_id": "custom-more"}]):
        assert _post(client, staff_headers, items, key="K-NF-N").status_code == 409
    assert _orders(rx_orders) == before


@pytest.mark.parametrize("change", [
    {"order_type": "prescription_order"}, {"cart_discount_percent": 5.0, "cart_discount_reason": "changed"},
    {"cart_discount_amount": 10.0}, {"customer_id": "cust-other"}, {"customer_id": ""},
    {"patient_id": "patient-other"},
], ids=["type", "bill-percent", "bill-amount", "customer", "customer-dropped", "member"])
def test_a_no_fingerprint_bill_refuses_a_changed_bill_field(client, staff_headers, rx_orders, change):
    lines = _two_line_bill(rx_orders)
    h = {**staff_headers, "Idempotency-Key": "K-NF-B"}
    body = {"customer_id": "cust-x", "order_type": "quick_sale", "items": lines}
    r = client.post("/api/v1/orders", json=body, headers=h)
    assert r.status_code in (200, 201), r.text
    _orders(rx_orders)[0].pop("body_fingerprint")
    before = copy.deepcopy(_orders(rx_orders))
    r = client.post("/api/v1/orders", json={**body, **change}, headers=h)
    assert r.status_code == 409, r.text
    assert _orders(rx_orders) == before


@pytest.mark.parametrize("sent, status", [("cust-x", 409), ("", 201)], ids=["names-one", "blank-too"])
def test_a_no_fingerprint_bill_saved_without_a_customer_matches_only_a_blank(client, staff_headers, rx_orders, sent, status):
    """The customer is stored as sent, so a true retry always matches exactly:
    a blank on either side is a different customer, never a pass."""
    lines = _two_line_bill(rx_orders)
    h = {**staff_headers, "Idempotency-Key": "K-NF-C"}
    body = {"customer_id": "cust-x", "order_type": "quick_sale", "items": lines}
    r = client.post("/api/v1/orders", json=body, headers=h)
    assert r.status_code in (200, 201), r.text
    doc = _orders(rx_orders)[0]
    doc.pop("body_fingerprint")
    doc["customer_id"] = None
    before = copy.deepcopy(_orders(rx_orders))
    r = client.post("/api/v1/orders", json={**body, "customer_id": sent}, headers=h)
    assert r.status_code == status, r.text
    assert _orders(rx_orders) == before


@pytest.mark.parametrize("change", [
    {"items": 1}, {"order_type": "quick_sale"}, {"cart_discount_percent": 6.0}, {"cart_discount_amount": 80.0},
], ids=["non-lens-line", "type", "bill-percent", "bill-amount"])
def test_a_no_fingerprint_lens_bill_also_weighs_what_is_not_lens(client, staff_headers, rx_orders, change):
    """Round-4 gap: only the lens lines and the people were compared."""
    _seed(rx_orders, pid="LENS-P", category="OPTICAL_LENS", item_type="LENS", hsn="900150")
    rxid = _seed_rx(rx_orders, prescription_id="rx-nf-m")
    lens = _lens(rxid)
    extra = _item("custom-fit", item_type="SERVICE", category="SERVICES", unit_price=33.33)
    h = {**staff_headers, "Idempotency-Key": "K-NF-M"}
    body = {"customer_id": "cust-x", "order_type": "prescription_order", "items": [lens, extra],
            "cart_discount_percent": 5.0, "cart_discount_reason": "good customer", "cart_discount_amount": 101.67}
    r = client.post("/api/v1/orders", json=body, headers=h)
    assert r.status_code in (200, 201), r.text
    oid = r.json()["order_id"]
    _orders(rx_orders)[0].pop("body_fingerprint")
    before = copy.deepcopy(_orders(rx_orders))
    for same in (body, {**body, "items": [extra, lens]}, {**body, "cart_discount_amount": 0}):
        r = client.post("/api/v1/orders", json=same, headers=h)
        assert r.status_code in (200, 201) and r.json()["order_id"] == oid, r.text
    changed = {**body, **change}
    if "items" in change:
        changed["items"] = [lens, {**extra, "unit_price": 34.0}]
    r = client.post("/api/v1/orders", json=changed, headers=h)
    assert r.status_code == 409, r.text
    assert _orders(rx_orders) == before


def test_a_no_fingerprint_bill_edited_since_refuses_the_old_request(client, staff_headers, rx_orders):
    """The saved bill is the only evidence, so a later edit makes the old press
    a different bill: refused, sent to Orders, nothing rewritten."""
    lines = _two_line_bill(rx_orders)
    _no_fp_bill(client, staff_headers, rx_orders, "K-NF-E", lines)
    _orders(rx_orders)[0]["items"][0]["quantity"] = 3
    before = copy.deepcopy(_orders(rx_orders))
    assert _post(client, staff_headers, lines, key="K-NF-E").status_code == 409
    assert _orders(rx_orders) == before


def test_a_no_fingerprint_bill_replays_a_reorder_and_a_raw_saved_sku(client, staff_headers, rx_orders):
    """Identical retries still replay: whole lines in another order, and a bill
    that saved the SKU it was sent (nothing rewrites that saved line)."""
    lines = _two_line_bill(rx_orders)
    oid = _no_fp_bill(client, staff_headers, rx_orders, "K-NF-R", lines)
    assert _orders(rx_orders)[0]["items"][0]["product_id"] == "FR-9"
    _orders(rx_orders)[0]["items"][0]["product_id"] = "FR-SKU"
    before = copy.deepcopy(_orders(rx_orders))
    for items in (lines, list(reversed(lines)), [{**lines[0], "product_id": "FR-SKU"}, lines[1]]):
        r = _post(client, staff_headers, items, key="K-NF-R")
        assert r.status_code in (200, 201) and r.json()["order_id"] == oid, r.text
    assert _orders(rx_orders) == before


# =========================================================================== #
# L1-F38 -- the sale freezes the master HSN; the next sale follows an edit
# =========================================================================== #
def _acc(pid="ACC-1", price=118.0):
    return _item(pid, item_type="ACCESSORY", category="ACCESSORIES", unit_price=price)


def test_a_sale_carries_the_master_hsn_and_follows_an_accepted_edit(client, staff_headers, rx_orders):
    repo = _seed(rx_orders, pid="ACC-1", category="ACCESSORIES", item_type="ACCESSORIES",
                 hsn="392690", gst_rate=18.0, mrp=118.0)
    r1 = _post(client, staff_headers, [_acc()])
    assert r1.status_code in (200, 201), r1.text
    first = dict(_orders(rx_orders)[0]["items"][0])
    assert first["hsn_code"] == "392690" and first["gst_rate"] == 18.0
    assert (first["taxable_value"], first["tax_amount"]) == (100.0, 18.0)

    # The accepted master edit (products.py derives the rate from the HSN).
    repo.collection.update_one({"product_id": "ACC-1"}, {"$set": {"hsn_code": "900311", "gst_rate": 5.0}})
    r2 = _post(client, staff_headers, [_acc()])
    assert r2.status_code in (200, 201), r2.text
    docs = _orders(rx_orders)
    nxt = [d for d in docs if d["order_id"] == r2.json()["order_id"]][0]["items"][0]
    assert nxt["hsn_code"] == "900311" and nxt["gst_rate"] == 5.0
    # Reconciliation: the earlier sale is NOT rewritten; both totals = price.
    old = [d for d in docs if d["order_id"] == r1.json()["order_id"]][0]
    assert old["items"][0]["hsn_code"] == "392690" and old["items"][0]["tax_amount"] == 18.0
    for d in docs:
        assert d["grand_total"] == 118.0
        assert round(d["tax_amount"] + sum(i["taxable_value"] for i in d["items"]), 2) == 118.0


def test_a_sale_by_sku_alias_carries_the_master_hsn(client, auth_headers, rx_orders):
    _seed(rx_orders, pid="ACC-2", sku="SKU-ACC-2", category="ACCESSORIES", item_type="ACCESSORIES",
          hsn="392690", gst_rate=18.0, mrp=118.0)
    r = _post(client, auth_headers, [_acc("SKU-ACC-2")])
    assert r.status_code in (200, 201), r.text
    line = _orders(rx_orders)[0]["items"][0]
    assert line["product_id"] == "ACC-2" and line["hsn_code"] == "392690"


def test_virtual_lens_and_service_lines_carry_their_item_type_hsn(client, staff_headers, rx_orders):
    _seed(rx_orders, pid="UNUSED", category="FRAME", item_type="FRAME", hsn="900311")
    rxid = _seed_rx(rx_orders, prescription_id="rx-virt")
    lens = _item("lens-123", item_type="LENS", category="RX_LENSES", prescription_id=rxid, unit_price=1050.0)
    svc = _item("custom-fit", item_type="SERVICE", category="SERVICES", unit_price=118.0)
    r = _post(client, staff_headers, [lens, svc])
    assert r.status_code in (200, 201), r.text
    by_pid = {i["product_id"]: i for i in _orders(rx_orders)[0]["items"]}
    assert (by_pid["lens-123"]["hsn_code"], by_pid["lens-123"]["gst_rate"]) == ("900150", 5.0)
    assert (by_pid["custom-fit"]["hsn_code"], by_pid["custom-fit"]["gst_rate"]) == ("998599", 18.0)


def test_add_item_and_quote_stamp_the_same_hsn_as_create(client, staff_headers, rx_orders):
    _seed(rx_orders, pid="ACC-3", category="ACCESSORIES", item_type="ACCESSORIES", hsn="900311", gst_rate=5.0,
          mrp=105.0)
    q = client.post("/api/v1/orders/quote", json={"items": [_acc("ACC-3", 105.0)]}, headers=staff_headers)
    assert q.status_code == 200, q.text
    r = _post(client, staff_headers, [_item("custom-fit", item_type="SERVICE", category="SERVICES",
                                            unit_price=118.0)])
    assert r.status_code in (200, 201), r.text
    oid = r.json()["order_id"]
    a = client.post(f"/api/v1/orders/{oid}/items", json=_acc("ACC-3", 105.0), headers=staff_headers)
    assert a.status_code in (200, 201), a.text
    added = [i for i in _orders(rx_orders)[0]["items"] if i["product_id"] == "ACC-3"][0]
    assert added["hsn_code"] == "900311" and added["gst_rate"] == 5.0
    assert (added["taxable_value"], added["tax_amount"]) == (100.0, 5.0)
    # The quote prices the line exactly as the sale bills it.
    assert (q.json()["taxable"], q.json()["tax"]) == (100.0, 5.0)


# =========================================================================== #
# L1-F38 -- the export never files a missing HSN silently
# =========================================================================== #
def test_the_export_keeps_a_stamped_hsn_and_flags_a_missing_one():
    from api.routers.reports.gst_base import _order_rate_lines

    order = {"order_id": "o1", "invoice_number": "INV-1"}
    stamped = [{"hsn_code": "392690", "gst_rate": 18.0, "taxable_value": 100.0, "tax_amount": 18.0, "quantity": 1}]
    issues: list = []
    assert _order_rate_lines(stamped, 100.0, 18.0, order, issues) == [(18, "392690", 100.0, 18.0)]
    assert issues == []

    bare = [{"gst_rate": 18.0, "taxable_value": 100.0, "tax_amount": 18.0, "quantity": 1}]
    issues = []
    _order_rate_lines(bare, 100.0, 18.0, order, issues)
    assert len(issues) == 1 and "no HSN" in issues[0]["issue"] and issues[0]["invoice"] == "INV-1"

    issues = []  # no usable line at all: the order-totals line is flagged too
    _order_rate_lines([], 100.0, 18.0, {**order, "tax_rate": 18}, issues)
    assert any("HSN" in i["issue"] for i in issues)
