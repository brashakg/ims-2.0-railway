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
"""
from __future__ import annotations

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
