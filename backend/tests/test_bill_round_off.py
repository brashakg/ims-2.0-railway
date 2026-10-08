"""
IMS 2.0 - Bill round off to the nearest rupee (owner ruling 2026-10-08)
=======================================================================
Every till bill is rounded ONCE, on its final payable total, after GST, to the
nearest rupee (50 paise and above up, below down) and carries a separate
"Round off" line. The round off is NOT taxable value and NOT tax: every line's
taxable value, the CGST/SGST/IGST and the GSTR-1 / GSTR-3B figures stay exactly
what they were. The order stores the rounded total in `grand_total` and the
adjustment in `round_off`; payments, refunds, prints and reports read that one
field instead of re-rounding.

Rupee anchors used below (all 5% frames unless named):
  1000.49 -> pays 1000, round off -0.49   (taxable 952.85, GST 47.64)
  1000.50 -> pays 1001, round off +0.50
  1000.51 -> pays 1001, round off +0.49
  999.99 frame + 500.50 sunglass = 1500.49 -> pays 1500, round off -0.49
       (frame 952.37 + 47.62 @5%, sunglass 424.15 + 76.35 @18%)
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from tests.test_orders_gst_recompute import (  # noqa: E402,F401  (fixture import)
    _frame_item,
    _post_order,
    _sunglass_item,
    patched_orders,
)
from tests.test_superadmin_order_edit import (  # noqa: E402,F401  (fixture import)
    _edit_item,
    _seed_order,
    wired,
)


def _saved(patched_orders, order_id):
    return next(
        d
        for d in patched_orders["order_repo"].collection.docs
        if d.get("order_id") == order_id
    )


# ============================================================================
# 1. THE rule, and the one engine every till total comes from
# ============================================================================


@pytest.mark.parametrize(
    "total, payable, round_off",
    [
        (1000.49, 1000.0, -0.49),
        (1000.50, 1001.0, 0.50),
        (1000.51, 1001.0, 0.49),
        (1000.00, 1000.0, 0.0),
        (0.49, 0.0, -0.49),
        (0.0, 0.0, 0.0),
    ],
)
def test_round_bill_is_nearest_rupee_half_up(total, payable, round_off):
    from api.routers.orders import round_bill

    assert round_bill(total) == (payable, round_off)


def test_engine_rounds_only_the_payable_and_keeps_every_tax_figure():
    """Mixed-GST bill: per-line taxable + GST and the order taxable + GST are
    exactly what the engine produced before the round off existed; only the
    payable moved, and the round off accounts for the whole gap."""
    from api.routers.orders import _compute_per_category_gst

    items = [
        {"item_total": 999.99, "category": "FRAME", "item_type": "FRAME"},
        {"item_total": 500.50, "category": "SUNGLASSES", "item_type": "SUNGLASSES"},
    ]
    out = _compute_per_category_gst(items, 0)
    assert (items[0]["taxable_value"], items[0]["tax_amount"]) == (952.37, 47.62)
    assert (items[1]["taxable_value"], items[1]["tax_amount"]) == (424.15, 76.35)
    assert out["taxable"] == 1376.52
    assert out["tax"] == 123.97
    assert out["grand_total"] == 1500.0
    assert out["round_off"] == -0.49
    assert round(out["taxable"] + out["tax"] + out["round_off"], 2) == out["grand_total"]


# ============================================================================
# 2. The till: create, add a line, remove a line
# ============================================================================


@pytest.mark.parametrize(
    "price, payable, round_off",
    [(1000.49, 1000.0, -0.49), (1000.50, 1001.0, 0.50), (1000.51, 1001.0, 0.49),
     (1000.00, 1000.0, 0.0)],
)
def test_till_bill_stores_rounded_total_and_round_off(
    client, auth_headers, patched_orders, price, payable, round_off
):
    resp = _post_order(client, auth_headers, [_frame_item(price)])
    assert resp.status_code in (200, 201), resp.text
    assert resp.json()["grand_total"] == payable
    saved = _saved(patched_orders, resp.json()["order_id"])
    assert saved["grand_total"] == payable
    assert saved["round_off"] == round_off
    assert saved["balance_due"] == payable


def test_mixed_gst_bill_taxes_unchanged_only_payable_rounded(
    client, auth_headers, patched_orders
):
    resp = _post_order(
        client, auth_headers, [_frame_item(999.99), _sunglass_item(500.50)]
    )
    assert resp.status_code in (200, 201), resp.text
    saved = _saved(patched_orders, resp.json()["order_id"])
    assert saved["tax_amount"] == 123.97
    lines = {i["category"]: i for i in saved["items"]}
    assert (lines["FRAME"]["taxable_value"], lines["FRAME"]["tax_amount"]) == (952.37, 47.62)
    assert (lines["SUNGLASSES"]["taxable_value"], lines["SUNGLASSES"]["tax_amount"]) == (424.15, 76.35)
    assert saved["grand_total"] == 1500.0
    assert saved["round_off"] == -0.49


def test_adding_and_removing_a_draft_line_re_rounds_the_bill(
    client, auth_headers, patched_orders
):
    resp = _post_order(client, auth_headers, [_frame_item(1000.00)])
    order_id = resp.json()["order_id"]
    r = client.post(
        f"/api/v1/orders/{order_id}/items",
        json={
            "product_id": "custom-sg-ro",
            "item_type": "SUNGLASSES",
            "category": "SUNGLASSES",
            "quantity": 1,
            "unit_price": 500.50,
            "discount_percent": 0,
        },
        headers=auth_headers,
    )
    assert r.status_code in (200, 201), r.text
    saved = _saved(patched_orders, order_id)
    assert (saved["grand_total"], saved["round_off"], saved["balance_due"]) == (1501.0, 0.5, 1501.0)

    sg_id = next(i["item_id"] for i in saved["items"] if i["item_type"] == "SUNGLASSES")
    r = client.delete(f"/api/v1/orders/{order_id}/items/{sg_id}", headers=auth_headers)
    assert r.status_code in (200, 204), r.text
    saved = _saved(patched_orders, order_id)
    assert (saved["grand_total"], saved["round_off"], saved["balance_due"]) == (1000.0, 0.0, 1000.0)


def _quote(client, headers, items, bill_pct=0.0):
    return client.post(
        "/api/v1/orders/quote",
        json={"items": items, "cart_discount_percent": bill_pct},
        headers=headers,
    )


def _disc_frame(price, line_pct):
    return dict(_frame_item(price), discount_percent=line_pct,
                discount_reason="regular customer")


def test_till_quote_of_the_rs_265_cart_is_255(client, auth_headers, patched_orders):
    """A Rs 265 frame, 1.5% line and 2.5% bill discount: 254.499375 before
    rounding, billed 254.50 -> Rs 255. A till rounding its own per-line paise
    reached 254.49 and quoted Rs 254, a rupee short of the order it created."""
    q = _quote(client, auth_headers, [_disc_frame(265.0, 1.5)], 2.5)
    assert q.status_code == 200, q.text
    assert (q.json()["grand_total"], q.json()["round_off"]) == (255.0, 0.5)


@pytest.mark.parametrize(
    "price, line_pct, bill_pct",
    [(265.0, 1.5, 2.5), (1000.49, 0.0, 0.0), (999.0, 7.5, 5.0), (1234.0, 3.5, 7.5),
     (1999.0, 0.5, 10.0)],
)
def test_till_quote_is_the_bill_create_makes(
    client, auth_headers, patched_orders, price, line_pct, bill_pct
):
    """ONE pricing for the till: the quote it collects against and the order
    it then creates carry the same payable, round off, GST and discount."""
    item = _disc_frame(price, line_pct)
    q = _quote(client, auth_headers, [item], bill_pct)
    assert q.status_code == 200, q.text
    extra = (
        {"cart_discount_percent": bill_pct, "cart_discount_reason": "festival offer"}
        if bill_pct else {}
    )
    resp = _post_order(client, auth_headers, [item], **extra)
    assert resp.status_code in (200, 201), resp.text
    saved = _saved(patched_orders, resp.json()["order_id"])
    quoted = q.json()
    assert (quoted["grand_total"], quoted["round_off"]) == (
        saved["grand_total"], saved["round_off"]
    )
    assert (quoted["tax"], quoted["total_discount"]) == (
        saved["tax_amount"], saved["total_discount"]
    )


def test_till_quote_is_for_roles_that_bill(client, patched_orders):
    from tests.test_returns_gst_refund import _staff_token

    r = _quote(client, {"Authorization": f"Bearer {_staff_token(['ACCOUNTANT'])}"},
               [_frame_item(1000.5)])
    assert r.status_code == 403, r.text


def test_order_view_carries_round_off_for_the_screens():
    from api.routers.orders import order_to_frontend

    out = order_to_frontend({"order_id": "o1", "grand_total": 1001.0, "round_off": 0.5})
    assert out["grandTotal"] == 1001.0
    assert out["roundOff"] == 0.5


# ============================================================================
# 3. Payments use the rounded figure: split tender and a credit sale
# ============================================================================


def _pay(client, auth_headers, order_id, method, amount):
    return client.post(
        f"/api/v1/orders/{order_id}/payments",
        json={"method": method, "amount": amount},
        headers=auth_headers,
    )


def test_split_payment_settles_the_rounded_total(client, auth_headers, patched_orders):
    order_id = _post_order(client, auth_headers, [_frame_item(1000.50)]).json()["order_id"]
    assert _pay(client, auth_headers, order_id, "UPI", 500).status_code in (200, 201)
    assert _pay(client, auth_headers, order_id, "CASH", 501).status_code in (200, 201)
    saved = _saved(patched_orders, order_id)
    assert saved["amount_paid"] == 1001.0
    assert saved["balance_due"] == 0.0
    assert saved["payment_status"] == "PAID"


def test_unrounded_amount_does_not_settle_a_rounded_up_bill(
    client, auth_headers, patched_orders
):
    order_id = _post_order(client, auth_headers, [_frame_item(1000.50)]).json()["order_id"]
    assert _pay(client, auth_headers, order_id, "CASH", 1000.50).status_code in (200, 201)
    saved = _saved(patched_orders, order_id)
    assert saved["payment_status"] == "PARTIAL"
    assert saved["balance_due"] == 0.5


def test_rounded_down_bill_refuses_the_unrounded_amount(
    client, auth_headers, patched_orders
):
    """1000.49 pays 1000: taking 1000.49 is an over-tender the server refuses."""
    order_id = _post_order(client, auth_headers, [_frame_item(1000.49)]).json()["order_id"]
    r = _pay(client, auth_headers, order_id, "CASH", 1000.49)
    assert r.status_code == 400, r.text
    assert _pay(client, auth_headers, order_id, "CASH", 1000).status_code in (200, 201)
    assert _saved(patched_orders, order_id)["payment_status"] == "PAID"


def test_credit_sale_owes_the_rounded_total(client, auth_headers, patched_orders):
    order_id = _post_order(client, auth_headers, [_frame_item(1000.51)]).json()["order_id"]
    r = _pay(client, auth_headers, order_id, "CREDIT", 1001)
    assert r.status_code in (200, 201), r.text
    saved = _saved(patched_orders, order_id)
    assert saved["payment_status"] == "CREDIT"
    assert saved["balance_due"] == 1001.0


# ============================================================================
# 4. SUPERADMIN edit re-rounds; its correction note keeps round off out of tax
# ============================================================================


def test_superadmin_recompute_re_rounds_the_bill():
    from api.routers.orders import _compute_per_category_gst
    from api.services.order_superadmin_edit import recompute_totals

    gst = recompute_totals(
        [{"item_total": 1000.51, "category": "FRAME"}], 0, _compute_per_category_gst
    )
    assert (gst["grand_total"], gst["round_off"]) == (1001.0, 0.49)


def test_superadmin_edit_persists_the_new_round_off(client, auth_headers, wired):
    """The edit door must store the NEW round off with the new total, or every
    GST reader backs a stale adjustment out of the new bill."""
    _seed_order(wired["order_repo"], grand_total=1000.0, round_off=-0.49)
    r = client.put(
        "/api/v1/orders/ord-16/superadmin-edit",
        json={"reason": "price typed wrong at the counter",
              "items": [_edit_item(unit_price=1200.50)]},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    saved = wired["order_repo"].find_by_id("ord-16")
    assert (saved["grand_total"], saved["round_off"]) == (1201.0, 0.5)


def test_correction_note_taxable_excludes_the_round_off_change():
    from api.services.order_superadmin_edit import (
        build_credit_note_doc,
        compute_invoice_delta,
    )

    before = {"grand_total": 1000.0, "round_off": -0.49, "tax_amount": 47.64}
    after = {"grand_total": 1201.0, "round_off": 0.50, "tax_amount": 57.17}
    delta = compute_invoice_delta(before, after)
    note = build_credit_note_doc(order={}, delta=delta, reason="r", user_id="u")
    # taxable moved 1143.33 - 952.85 = 190.48; GST 9.53; round off 0.99.
    assert note["amount"] == 201.0
    assert note["tax_amount"] == 9.53
    assert note["taxable_amount"] == 190.48


def _seed_rounded_up_invoice(order_repo):
    """An invoiced 1200.50 frame bill: paid 1201, round off +0.50."""
    line = dict(_edit_item(unit_price=1200.50), item_total=1200.50, gst_rate=5.0,
                taxable_value=1143.33, tax_amount=57.17)
    _seed_order(order_repo, items=[line], subtotal=1200.50, tax_amount=57.17,
                grand_total=1201.0, round_off=0.5, amount_paid=1201.0,
                invoice_number="INV/BOK-01/26-27/0901")


def test_credit_note_door_keeps_the_round_off_change_out_of_taxable(
    client, auth_headers, wired
):
    """The CREDIT direction through the real door: 1201 (+0.50) corrected to
    1000 (-0.49). Taxable moved 1143.33 -> 952.85 = 190.48; the 0.99 round-off
    change is part of the 201 note but never of its taxable value."""
    _seed_rounded_up_invoice(wired["order_repo"])
    r = client.put(
        "/api/v1/orders/ord-16/superadmin-invoice-change",
        json={"mode": "CREDIT_NOTE", "reason": "overcharged",
              "items": [_edit_item(unit_price=1000.49)]},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    note = next(
        d for d in wired["db"].get_collection("credit_note_ledger").docs
        if d.get("note_type") == "CREDIT_NOTE"
    )
    assert (note["amount"], note["tax_amount"], note["taxable_amount"]) == (
        201.0, 9.53, 190.48
    )
    # The original invoice and its round off stay as issued.
    saved = wired["order_repo"].find_by_id("ord-16")
    assert (saved["grand_total"], saved["round_off"]) == (1201.0, 0.5)


def test_revised_invoice_stores_the_new_round_off(client, auth_headers, wired):
    """A revised invoice carries its OWN round off next to its new total, or
    every GST reader and the reprint use the original bill's figure."""
    _seed_rounded_up_invoice(wired["order_repo"])
    r = client.put(
        "/api/v1/orders/ord-16/superadmin-invoice-change",
        json={"mode": "REVISED_INVOICE", "reason": "wrong price",
              "items": [_edit_item(unit_price=1000.49)]},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    saved = wired["order_repo"].find_by_id("ord-16")
    assert (saved["grand_total"], saved["round_off"]) == (1000.0, -0.49)


# ============================================================================
# 5. One rule: the dead round-off settings keys are gone
# ============================================================================


def test_system_settings_no_longer_offer_round_off_keys(client, auth_headers):
    r = client.get("/api/v1/admin/system/settings", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert "round_off_enabled" not in r.json()
    assert "round_off_paise" not in r.json()
