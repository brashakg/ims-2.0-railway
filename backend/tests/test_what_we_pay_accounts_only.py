"""What we pay is for the owner and the accounts roles only (owner ruling
2026-10-12).

  1. EVERYTHING WE PAY -- purchase prices (PO line prices and totals, the
     receiving cockpit, the last price paid, a vendor's purchase history, a
     vendor return's price, a vendor RMA's credit and write-off, a receipt read
     through a barcode) and product cost (cost price, landed cost, margin,
     stock value at cost, profit, COGS by category) -- reaches SUPERADMIN / ADMIN / ACCOUNTANT
     only. AREA_MANAGER, STORE_MANAGER and CATALOG_MANAGER see MRP and selling
     price, never cost: their raw JSON carries no cost or purchase-price key
     and no cost figure under any name (one walker, ``_leaks``).
  2. A manager still raises and sends a PO, by quantity: the server prices
     each line from the last price paid to that vendor (the one last-paid
     rule), else the catalogue cost; a price he types is not taken; a line
     nobody can price stays unpriced (never 0) and sending it raises a task
     for accounts; its goods are still received onto the shelf and the bill
     brings the cost. Accounts keep their typed price.
  3. Every read follows cost_mask: widen its rule and the managers see cost
     again everywhere, so no read keeps a copy of it.

Driven over HTTP with a token per role against one shop whose every cost and
purchase price is COST (or a figure worked out from it).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from test_counter_roles_no_cost_anywhere import (  # noqa: E402,F401
    BLIND,
    COST,
    COST_PAISE,
    READS as SHOP_READS,
    STORE,
    _Conn,
    _cost_keys,
    _figures_in,
    _headers,
    shop,
)

MANAGERS = ("AREA_MANAGER", "STORE_MANAGER", "CATALOG_MANAGER")
ACCOUNTS = ("SUPERADMIN", "ADMIN", "ACCOUNTANT")
QTY = 2
SUBTOTAL = round(COST * QTY, 2)
TAX = round(SUBTOTAL * 0.12, 2)
TOTAL = round(SUBTOTAL + TAX, 2)
# A purchase document's money keys: generic names (on an ORDER unit_price is
# the selling price), so the walker is told them for purchase reads only.
# Deliberately NOT read from cost_mask: a key dropped from the mask must
# still be caught here.
_PRICE_KEYS = (
    "unit_price", "estimated_unit_price", "subtotal", "tax", "tax_amount",
    "total_amount", "gst_summary", "line_tax", "cgst", "sgst", "igst",
    "spend", "total_spend", "totals",
)


GRN_BARCODE = "2900000000024"
# A vendor RMA's money keys (none says "cost").
_RMA_MONEY = (
    "expected_credit_paise", "received_credit_paise", "variance_paise",
    "written_off_paise", "received_paise", "credit_notes",
    "expected_credit_rupees", "received_credit_rupees", "variance_rupees",
)


def _filled(body):
    """`body` without empty containers: an empty {"costs": {}} carries no money."""
    if isinstance(body, dict):
        out = {k: _filled(v) for k, v in body.items()}
        return {k: v for k, v in out.items() if v not in ({}, [])}
    if isinstance(body, list):
        return [_filled(v) for v in body]
    return body


def _leaks(body, extra=()):
    """Every cost / price key in `body` plus every cost figure in it."""
    figures = [
        f for f in (str(SUBTOTAL), str(TAX), str(TOTAL)) if f in json.dumps(body, default=str)
    ]
    return _cost_keys(_filled(body), extra) + _figures_in(body) + figures


@pytest.fixture
def buying(shop, monkeypatch):
    """The shop plus what it bought: vendor V1, a sent PO at COST, an accepted
    receipt whose unit was received at COST, a priced vendor return, a vendor
    RMA credited and written off at COST, a sold order line at cost, and an
    accountant at the shop."""
    from api.routers import vendor_returns as vr_mod
    from api.routers import vendor_rma as rma_mod

    # vendor_returns and vendor_rma bound get_db at import; point them here too.
    monkeypatch.setattr(vr_mod, "get_db", lambda: _Conn(shop))
    monkeypatch.setattr(rma_mod, "get_db", lambda: _Conn(shop))
    # The products ride the provisional door, so the send gate's catalogue
    # completeness check (not this ruling) stays out of the way.
    shop.products.update_many({}, {"$set": {"provisional": True}})
    now = datetime.now()
    shop.vendors.insert_one(
        {"vendor_id": "V1", "trade_name": "Acme Optics", "legal_name": "Acme",
         "gstin": "27AAPFU0939F1ZV", "is_active": True}
    )
    line = {
        "product_id": "P1", "product_name": "Aviator", "sku": "SKU-P1",
        "quantity": QTY, "ordered_qty": QTY, "received_qty": 0,
        "unit_price": COST, "tax_rate": 12, "line_tax": TAX,
        "cgst": round(TAX / 2, 2), "sgst": round(TAX / 2, 2), "igst": 0,
        "line_status": "OPEN",
    }
    shop.purchase_orders.insert_one(
        {
            "po_id": "PO1", "po_number": "PO-1", "vendor_id": "V1",
            "vendor_name": "Acme Optics", "delivery_store_id": STORE,
            "status": "SENT", "items": [line], "subtotal": SUBTOTAL,
            "tax_amount": TAX, "total_amount": TOTAL,
            "gst_summary": {"cgst": TAX / 2, "sgst": TAX / 2, "igst": 0, "tax": TAX},
            "created_at": now.isoformat(), "sent_at": now.isoformat(),
        }
    )
    shop.grns.insert_one(
        {
            "grn_id": "G1", "grn_number": "GRN-1", "po_id": "PO-OLD",
            "vendor_id": "V1", "store_id": STORE, "status": "ACCEPTED",
            "accepted_at": (now - timedelta(days=3)).isoformat(),
            "created_at": (now - timedelta(days=3)).isoformat(),
            "items": [{"product_id": "P1", "received_qty": 1, "accepted_qty": 1,
                       "unit_price": COST}],
            "total_received": 1, "total_accepted": 1,
        }
    )
    shop.stock_units.insert_one(
        {"stock_id": "U-GRN", "barcode": GRN_BARCODE, "product_id": "P1", "store_id": STORE,
         "status": "SOLD", "quantity": 0, "source_type": "GRN", "source_id": "G1",
         "unit_cost": COST, "cost_price": COST, "created_at": now}
    )
    shop.vendor_returns.insert_one(
        {"return_id": "VR1", "vendor_id": "V1", "vendor_name": "Acme Optics",
         "store_id": STORE, "status": "created", "return_type": "defective",
         "items": [{"product_id": "P1", "product_name": "Aviator", "quantity": 1,
                    "reason": "broken", "unit_price": COST}],
         "total_value": COST, "created_at": now.isoformat()}
    )
    # Two pairs back to V1 at COST each, credited COST, the rest written off.
    shop.vendor_rmas.insert_one(
        {"rma_id": "RMA1", "vendor_id": "V1", "vendor_name": "Acme Optics",
         "store_id": STORE, "status": "CLOSED", "vendor_rma_number": "VRMA-1",
         "lines": [{"product_id": "P1", "product_name": "Aviator", "quantity": QTY,
                    "reason": "DEFECTIVE", "unit_cost_paise": COST_PAISE,
                    "line_expected_paise": QTY * COST_PAISE}],
         "expected_credit_paise": QTY * COST_PAISE, "received_credit_paise": COST_PAISE,
         "variance_paise": COST_PAISE, "written_off_paise": COST_PAISE,
         "credit_notes": [{"credit_note_number": "CN-1", "received_paise": COST_PAISE}],
         "created_at": now,
         "status_history": [
             {"status": "CREDIT_RECEIVED", "at": now, "by": "u-acct",
              "notes": f"credit note CN-1 for {COST_PAISE} paise"},
             {"status": "CLOSED", "at": now, "by": "u-acct",
              "notes": f"closed; wrote off {COST_PAISE} paise"},
         ]}
    )
    shop.orders.insert_one(
        {"order_id": "O4", "order_number": "BV-O4", "store_id": STORE,
         "customer_id": "C1", "status": "DELIVERED", "grand_total": 9999.0,
         "created_at": now,
         "items": [{"item_id": "I4", "product_id": "P1", "product_name": "Aviator",
                    "category": "FRAME", "quantity": 1, "unit_price": 9999.0,
                    "item_total": 9999.0, "cost_price": COST}]}
    )
    shop.users.insert_one(
        {"user_id": "u-acct", "username": "acct", "roles": ["ACCOUNTANT"],
         "store_ids": [STORE], "is_active": True}
    )
    shop.promo_applications.insert_one({
        "order_id": "O4", "store_id": STORE, "applied_at": now.isoformat(),
        "total_discount_given": 100, "estimated_cogs": COST,
        "net_margin_after_promo": 9999 - COST - 100, "cogs_is_estimated": True,
        "applied_promos": [{"promo_id": "PROMO1", "promo_name": "Frame offer", "discount_given": 100}],
    })
    return shop


from tests.ist_business_day import business_day

_DAY = business_day(datetime.now())
_WINDOW = f"from_date={_DAY}&to_date={_DAY}"
# (path, response-specific value-at-cost keys) -- purchase reads.
PURCHASE_READS = [
    ("/api/v1/vendors/purchase-orders", _PRICE_KEYS),
    ("/api/v1/vendors/purchase-orders/PO1", _PRICE_KEYS),
    ("/api/v1/vendors/goods-receipt/cockpit?vendor_id=V1", _PRICE_KEYS),
    ("/api/v1/vendors/last-cost?vendor_id=V1&product_ids=P1", _PRICE_KEYS),
    ("/api/v1/vendors/grn", _PRICE_KEYS),
    ("/api/v1/vendors/grn/G1", _PRICE_KEYS),
    ("/api/v1/vendors/V1/purchase-history", _PRICE_KEYS),
    ("/api/v1/vendor-returns", _PRICE_KEYS + ("total_value",)),
    ("/api/v1/vendor-returns/VR1", _PRICE_KEYS + ("total_value",)),
    ("/api/v1/vendor-rma", _RMA_MONEY),
    ("/api/v1/vendor-rma/RMA1", _RMA_MONEY),
    # The unit's receipt, read through its barcode (open to every role).
    (f"/api/v1/inventory/barcode/{GRN_BARCODE}/trace", _PRICE_KEYS),
]
# Reports and dashboards worked out from cost.
REPORT_READS = [
    (f"/api/v1/analytics/enterprise-kpis?store_id={STORE}", ("gross_margin_percent", "gross_profit")),
    (f"/api/v1/reports/promotions?store_id={STORE}", ("net_margin_impact", "estimated_cogs", "net_margin_after_promo")),
    (f"/api/v1/reports/inventory/valuation?store_id={STORE}", ("value", "total")),
    (f"/api/v1/reports/profit/by-category?store_id={STORE}&{_WINDOW}",
     ("cost", "profit", "total_profit")),
    (f"/api/v1/reports/profit/by-store?{_WINDOW}", ("cost", "profit", "total_profit")),
    (f"/api/v1/reports/finance/expense-vs-revenue?store_id={STORE}&{_WINDOW}",
     ("cost", "profit")),
    (f"/api/v1/analytics/inventory-intelligence?store_id={STORE}", ("value", "total_value")),
    (f"/api/v1/workshop/spoilage-analytics?store_id={STORE}", ("spoilage_cost_total_paise",)),
    # The Finance dashboard's P&L by category (open to the shop managers).
    (f"/api/v1/finance/pnl/by-category?{_WINDOW}", ("gross_profit", "total_gross_profit")),
]
SHOP = [(path, extra) for _m, path, extra in [*SHOP_READS, BLIND]]
ALL_READS = PURCHASE_READS + REPORT_READS + SHOP


def _get(client, role, path):
    return client.get(path, headers=_headers(role))


# ---------------------------------------------------------------------------
# 1. The managers read no cost and no price paid, anywhere; accounts do
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("role", MANAGERS)
@pytest.mark.parametrize("path,extra", ALL_READS)
def test_managers_read_no_cost_and_no_price_paid(client, buying, role, path, extra):
    r = _get(client, role, path)
    # A refused read shows nothing; the buyers must reach the purchase reads.
    assert r.status_code in (200, 403), (role, path, r.status_code, r.text[:300])
    if r.status_code == 403:
        assert role == "CATALOG_MANAGER" or (path, extra) in REPORT_READS, (role, path)
        return
    assert not _leaks(r.json(), extra), (role, path, _leaks(r.json(), extra))


@pytest.mark.parametrize("role", ("STORE_MANAGER", "AREA_MANAGER"))
@pytest.mark.parametrize("path,extra", PURCHASE_READS)
def test_buyers_still_open_every_purchase_read(client, buying, role, path, extra):
    # The positive half of the test above: a buyer is not refused the screen,
    # only its prices -- so "no leak" is not passing on a 403.
    r = _get(client, role, path)
    assert r.status_code == 200, (role, path, r.text[:300])


@pytest.mark.parametrize("role", ACCOUNTS)
@pytest.mark.parametrize("path,extra", ALL_READS)
def test_accounts_still_read_what_we_pay(client, buying, role, path, extra):
    # The positive control that keeps the managers' "no leak" from passing on
    # an empty shop. The accountant has no workshop / blind-count screen, and
    # no receiving cockpit (receiving is managers only, ruling 2026-09-28).
    r = _get(client, role, path)
    if role == "ACCOUNTANT" and r.status_code == 403:
        assert any(k in path for k in ("spoilage", "blind-count", "goods-receipt/cockpit")), path
        return
    assert r.status_code == 200, (role, path, r.text[:300])
    assert _leaks(r.json(), extra), (role, path, r.json())


# ---------------------------------------------------------------------------
# 2. A manager's PO: the server prices it, the reply hides it
# ---------------------------------------------------------------------------
def _po(client, role, *lines):
    body = {
        "vendor_id": "V1",
        "delivery_store_id": STORE,
        "items": [
            {"product_id": pid, "product_name": pid, "quantity": QTY, **extra}
            for pid, extra in lines
        ],
    }
    r = client.post("/api/v1/vendors/purchase-orders", json=body, headers=_headers(role))
    assert r.status_code == 201, r.text
    return r.json()


def _stored(db, po_id):
    return {it["product_id"]: it.get("unit_price") for it in db.purchase_orders.find_one({"po_id": po_id})["items"]}


@pytest.fixture
def catalogue(buying):
    for pid, cost in (("P2", 777.0), ("P3", None)):
        buying.products.insert_one(
            {"product_id": pid, "sku": f"SKU-{pid}", "name": pid, "category": "FRAME",
             "brand": "Ray-Ban", "mrp": 9999.0, "offer_price": 9999.0, "gst_rate": 12,
             "hsn_code": "900311", "cost_price": cost, "is_active": True,
             "attributes": {"brand_name": "Ray-Ban", "model_no": f"M-{pid}", "colour_code": "BLK"},
             "provisional": True}
        )
    # P1's catalogue cost is not what was last paid for it (COST, receipt G1),
    # so a PO that took the catalogue cost over the last price paid shows.
    buying.products.update_one({"product_id": "P1"}, {"$set": {
        "cost_price": 1234.5, "hsn_code": "900311",
        "attributes": {"brand_name": "Ray-Ban", "model_no": "M-P1", "colour_code": "BLK"},
    }})
    return buying


@pytest.mark.parametrize("role", ("STORE_MANAGER", "AREA_MANAGER"))
def test_a_managers_po_is_priced_on_the_server_and_hides_it(client, catalogue, role):
    reply = _po(
        client, role,
        ("P1", {"unit_price": 1.0}),  # typed: not taken; last paid to V1 wins
        ("P2", {}),                   # no history: the catalogue cost
        ("P3", {"unit_price": 5.0}),  # nothing known: unpriced, never 0
    )
    assert _stored(catalogue, reply["po_id"]) == {"P1": COST, "P2": 777.0, "P3": None}
    assert not _leaks(reply, _PRICE_KEYS), reply
    assert reply["po_number"]
    detail = _get(client, role, f"/api/v1/vendors/purchase-orders/{reply['po_id']}").json()
    assert [it["quantity"] for it in detail["items"]] == [QTY] * 3
    assert not _leaks(detail, _PRICE_KEYS), detail


def test_accounts_keep_the_price_they_type_and_blanks_are_filled(client, catalogue):
    reply = _po(client, "ACCOUNTANT", ("P1", {"unit_price": 500.0}), ("P2", {}), ("P3", {}))
    assert _stored(catalogue, reply["po_id"]) == {"P1": 500.0, "P2": 777.0, "P3": None}
    assert reply["total_amount"] > 0


def _price_tasks(db):
    return list(db.tasks.find({"source_ref": {"$regex": "^po-price:"}}))


def test_sending_an_unpriced_po_raises_a_task_for_accounts(client, catalogue):
    po_id = _po(client, "STORE_MANAGER", ("P3", {}))["po_id"]
    assert _price_tasks(catalogue) == []  # a draft is not sent yet
    r = client.post(f"/api/v1/vendors/purchase-orders/{po_id}/send", headers=_headers("STORE_MANAGER"))
    assert r.status_code == 200, r.text
    (task,) = _price_tasks(catalogue)
    assert task["source_ref"] == f"po-price:{po_id}"
    assert task["assigned_to"] == "u-acct"
    assert task["store_id"] == STORE
    assert "P3" in task["description"] and "Purchase Invoices" in task["description"]


@pytest.mark.parametrize("taxable", [None, 0])
@pytest.mark.parametrize("consolidated_dc", [False, True])
def test_an_unpriced_line_is_received_and_billed_its_cost_comes_with_the_bill(client, catalogue, taxable, consolidated_dc):
    # P4 is catalogued but for its cost (catalogue managers have no cost box,
    # nothing was paid to V1 for it). A manager orders it by quantity and sends
    # it, the goods arrive and are accepted: they go on the shelf without a
    # cost, and the receipt can be booked in Purchase Invoices, which is where
    # the task sent to accounts says the price is entered.
    catalogue.products.insert_one(
        {"product_id": "P4", "sku": "SKU-P4", "name": "P4", "category": "FRAME",
         "brand": "Ray-Ban", "mrp": 9999.0, "offer_price": 9999.0, "gst_rate": 12,
         "hsn_code": "900311", "is_active": True,
         "attributes": {"brand_name": "Ray-Ban", "model_no": "M-P4", "colour_code": "BLK"}}
    )
    po_id = _po(client, "STORE_MANAGER", ("P4", {}))["po_id"]
    assert _stored(catalogue, po_id) == {"P4": None}
    sent = client.post(f"/api/v1/vendors/purchase-orders/{po_id}/send", headers=_headers("STORE_MANAGER"))
    assert sent.status_code == 200, sent.text
    catalogue.grns.insert_one(
        {"grn_id": "G4", "grn_number": "GRN-4", "po_id": po_id, "vendor_id": "V1",
         "store_id": STORE, "status": "PENDING", "vendor_invoice_no": "INV-4",
         "items": [{"product_id": "P4", "received_qty": QTY, "accepted_qty": QTY,
                    "tallied": True}]}
    )
    r = client.post("/api/v1/vendors/grn/G4/accept", headers=_headers("STORE_MANAGER"))
    assert r.status_code == 200, r.text
    assert (r.json()["grn_status"], r.json()["units_added"]) == ("ACCEPTED", QTY), r.json()
    units = list(catalogue.stock_units.find({"source_id": "G4"}))
    assert [u["status"] for u in units] == ["AVAILABLE"] * QTY
    assert not any(u.get("cost_price") for u in units)  # no made-up cost
    # Our company, so the bill knows which GSTIN it books on.
    catalogue.entities.insert_one({"entity_id": "E1", "name": "Better Vision", "gstins": [{"gstin": "27AABCB1234C1ZE"}]})
    catalogue.stores.insert_one({"store_id": STORE, "entity_id": "E1", "gstin": "27AABCB1234C1ZE", "state_code": "27"})
    bill = client.get(
        "/api/v1/vendors/purchase-invoices/from-grn/G4", headers=_headers("ACCOUNTANT")
    )
    assert bill.status_code == 200, bill.text
    assert bill.json()["lines"][0]["unit_price"] is None
    body = {
        "vendor_id": "V1", "invoice_number": "UNPRICED-4", "invoice_date": _DAY,
        "po_id": po_id, "grn_id": "G4",
        "lines": [{"product_id": "P4", "description": "P4", "qty": QTY,
                   "unit_price": 0, "gst_rate": 12}],
    }
    if taxable is not None:
        body["lines"][0]["taxable"] = taxable
    if consolidated_dc:
        # The same received goods can be billed through the DC consolidation
        # door, whose request has no header PO or single GRN to inspect.
        catalogue.grns.update_one({"grn_id": "G4"}, {"$set": {"grn_subtype": "DELIVERY_CHALLAN"}})
        body.pop("po_id")
        body.pop("grn_id")
        body["linked_dc_ids"] = ["G4"]
    for path in ("/preview", ""):
        refused = client.post("/api/v1/vendors/purchase-invoices" + path,
                              json=body, headers=_headers("ACCOUNTANT"))
        assert refused.status_code == 422, refused.text
        assert refused.json()["detail"]["code"] == "INVOICE_PRICE_REQUIRED"
    assert catalogue.vendor_bills.count_documents({"invoice_number": "UNPRICED-4"}) == 0
    assert catalogue.products.find_one({"product_id": "P4"}).get("cost_price") is None


def test_sending_a_priced_po_raises_no_task(client, catalogue):
    po_id = _po(client, "STORE_MANAGER", ("P1", {}), ("P2", {}))["po_id"]
    r = client.post(f"/api/v1/vendors/purchase-orders/{po_id}/send", headers=_headers("STORE_MANAGER"))
    assert r.status_code == 200, r.text
    assert _price_tasks(catalogue) == []


@pytest.mark.parametrize("path", ["", "/timeline"])
def test_legacy_po_price_history_is_hidden_without_changing_the_record(client, buying, path):
    history = [{"kind": "edited", "label": "Edited", "at": "2026-10-01",
                "detail": "Aviator: quantity 1 -> 2; Aviator: cost Rs 4321.77 -> Rs 4500"}]
    buying.purchase_orders.update_one({"po_id": "PO1"}, {"$set": {"history": history}})
    manager = client.get(f"/api/v1/vendors/purchase-orders/PO1{path}", headers=_headers("STORE_MANAGER"))
    assert manager.status_code == 200, manager.text
    assert "quantity 1 -> 2" in manager.text
    assert "4321.77" not in manager.text and "4500" not in manager.text
    accounts = client.get(f"/api/v1/vendors/purchase-orders/PO1{path}", headers=_headers("ACCOUNTANT"))
    assert accounts.status_code == 200 and "cost Rs 4321.77 -> Rs 4500" in accounts.text
    assert buying.purchase_orders.find_one({"po_id": "PO1"})["history"] == history


# ---------------------------------------------------------------------------
# 3. ONE rule: widen cost_mask and every read above moves with it
# ---------------------------------------------------------------------------
def test_every_read_follows_the_one_cost_rule(client, buying, monkeypatch):
    from api.services import cost_mask

    for ctx in ("default", "product", "purchase", "payables"):
        monkeypatch.setitem(cost_mask._CONTEXT_ROLES, ctx, {"STORE_MANAGER"})
    for path, extra in ALL_READS:
        r = _get(client, "STORE_MANAGER", path)
        assert r.status_code == 200, (path, r.text[:300])
        assert _leaks(r.json(), extra), path
    # ...and the area manager, not widened, still sees none of it.
    r = _get(client, "AREA_MANAGER", "/api/v1/vendors/purchase-orders/PO1")
    assert not _leaks(r.json(), _PRICE_KEYS)


def test_the_rule_is_the_owner_ruling():
    from api.services.cost_mask import COST_VISIBLE_ROLES, can_see_cost
    from api.services import rbac_policy as rbac

    assert COST_VISIBLE_ROLES == set(ACCOUNTS)
    for ctx in ("default", "product", "purchase", "payables"):
        assert {r for r in rbac.ALL_ROLES if can_see_cost({"roles": [r]}, ctx)} == set(ACCOUNTS), ctx


_COST_CELL = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "frontend", "src",
    "components", "common", "CostCell.tsx",
)


@pytest.mark.skipif(not os.path.exists(_COST_CELL), reason="frontend/ not shipped here")
def test_the_frontend_reads_one_list_and_it_is_this_rule():
    import re

    from api.services.cost_mask import COST_VISIBLE_ROLES

    with open(_COST_CELL, encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(r"export const COST_ROLES[^=]*=\s*\[([^\]]*)\]", src)
    assert m, "CostCell.tsx no longer declares COST_ROLES"
    assert set(re.findall(r"'([A-Z_]+)'", m.group(1))) == COST_VISIBLE_ROLES
    # No second list: the old names are gone from the whole frontend.
    root = os.path.dirname(os.path.dirname(os.path.dirname(_COST_CELL)))
    stale = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.endswith((".ts", ".tsx")):
                with open(os.path.join(dirpath, name), encoding="utf-8") as fh:
                    if re.search(r"\b(PRODUCT_COST_ROLES|PURCHASE_PRICE_ROLES|PAYABLES_ROLES)\b", fh.read()):
                        stale.append(name)
    assert not stale, stale


@pytest.mark.parametrize("role,sees", (("STORE_MANAGER", False), ("ACCOUNTANT", True)))
def test_a_vendor_rma_run_end_to_end_reads_its_money_back_to_accounts_only(client, buying, role, sees):
    # Raise two pairs at COST, authorize, dispatch, take a credit note for one
    # pair, try to close (refused: a variance is still owed), then write it
    # off. Every reply, the refusal and the RMA read afterwards: the money (in
    # keys, or in the words a step writes into the history) is the accounts'.
    def post(path, body, actor=role):
        return client.post(f"/api/v1/vendor-rma{path}", json=body, headers=_headers(actor))

    def read_for_role(rid):
        return client.get(f"/api/v1/vendor-rma/{rid}", headers=_headers(role))

    made = post("", {"vendor_id": "V1", "vendor_name": "Acme Optics", "store_id": STORE,
                     "lines": [{"product_id": "P1", "product_name": "Aviator",
                                "quantity": QTY, "reason": "DEFECTIVE", "unit_cost": COST}]}, "ACCOUNTANT")
    assert made.status_code == 201, made.text
    rid = made.json()["rma_id"]
    steps = [read_for_role(rid)]
    for path, body in (
        ("/authorize", {"vendor_rma_number": "VRMA-2"}),
        ("/dispatch", {"carrier": "Delhivery", "awb": "AWB-2"}),
        ("/credit-note", {"credit_note_number": "CN-2", "received_amount": COST}),
    ):
        actor = "ACCOUNTANT" if path == "/credit-note" else role
        if actor != role:
            denied = post(f"/{rid}{path}", body)
            assert denied.status_code == 403, denied.text
        changed = post(f"/{rid}{path}", body, actor)
        assert changed.status_code == 200, (path, changed.text)
        steps.append(read_for_role(rid))
    refused = post(f"/{rid}/close", {})
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["error"] == "variance_outstanding"
    if not sees:
        denied = post(f"/{rid}/close", {"write_off_variance": True})
        assert denied.status_code == 403, denied.text
    closed = post(f"/{rid}/close", {"write_off_variance": True}, "ACCOUNTANT")
    assert (closed.status_code, closed.json()["status"]) == (200, "CLOSED"), closed.text
    read = client.get(f"/api/v1/vendor-rma/{rid}", headers=_headers(role)).json()
    assert [s["status"] for s in read["status_history"]] == [
        "DRAFT", "AUTHORIZED", "DISPATCHED", "CREDIT_RECEIVED", "CLOSED"]
    assert [ln["quantity"] for ln in read["lines"]] == [QTY]
    closed = read_for_role(rid)
    money = (steps[0], steps[3], refused, closed)
    if sees:
        assert all(_leaks(r.json(), _RMA_MONEY) for r in money), [r.json() for r in money]
        assert _leaks(read, _RMA_MONEY)
    else:
        assert not any(_leaks(r.json(), _RMA_MONEY) for r in steps + [refused, closed]), [
            r.json() for r in steps + [refused, closed]]
        assert not _leaks(read, _RMA_MONEY), read


@pytest.mark.parametrize("role", ("SALES_STAFF", "CASHIER", "WORKSHOP_STAFF"))
def test_a_counter_role_reads_no_receipt_price_through_a_barcode(client, buying, role):
    r = _get(client, role, f"/api/v1/inventory/barcode/{GRN_BARCODE}/trace")
    assert r.status_code == 200, r.text
    assert r.json()["purchase"][0]["grn_id"] == "G1"  # the receipt, not its prices
    assert not _leaks(r.json(), _PRICE_KEYS), r.json()


@pytest.mark.parametrize("role,sees", (("STORE_MANAGER", False), ("ACCOUNTANT", True)))
def test_moving_a_return_on_reads_its_price_back_to_accounts_only(client, buying, role, sees):
    # A manager may move an already priced return on, but no
    # manager reads what we paid back (owner ruling 2026-10-12).
    r = client.patch(
        "/api/v1/vendor-returns/VR1/status", json={"status": "approved"}, headers=_headers(role)
    )
    assert r.status_code == 200, r.text
    assert r.json()["return"]["status"] == "approved"
    assert bool(_leaks(r.json(), _PRICE_KEYS + ("total_value",))) is sees, r.json()


@pytest.mark.parametrize("role", ("STORE_MANAGER", "AREA_MANAGER"))
def test_return_status_door_cannot_bypass_accounts_price_rule(client, buying, role):
    buying.vendor_returns.update_one({"return_id": "VR1"}, {"$set": {
        "items.0.unit_price": None, "total_value": None,
    }})
    body = {"status": "approved", "unit_prices": [999]}
    moved = client.patch("/api/v1/vendor-returns/VR1/status", json=body, headers=_headers(role))
    assert moved.status_code == 200, moved.text
    unchanged = buying.vendor_returns.find_one({"return_id": "VR1"})
    assert unchanged["status"] == "approved" and unchanged["items"][0]["unit_price"] is None
    denied = client.put("/api/v1/vendor-returns/VR1/prices", json={"unit_prices": [999]}, headers=_headers(role))
    assert denied.status_code == 403, denied.text
    accepted = client.put("/api/v1/vendor-returns/VR1/prices", json={"unit_prices": [999]}, headers=_headers("ACCOUNTANT"))
    assert accepted.status_code == 200, accepted.text
    assert buying.vendor_returns.find_one({"return_id": "VR1"})["items"][0]["unit_price"] == 999


@pytest.mark.parametrize("role", ("STORE_MANAGER", "AREA_MANAGER"))
def test_managers_cannot_issue_supplier_credit(client, buying, role):
    buying.vendor_returns.update_one({"return_id": "VR1"}, {"$set": {"status": "received_by_vendor"}})
    denied = client.patch("/api/v1/vendor-returns/VR1/status", json={"status": "credit_issued"}, headers=_headers(role))
    assert denied.status_code == 403, denied.text
    assert buying.vendor_returns.find_one({"return_id": "VR1"})["status"] == "received_by_vendor"


# ---------------------------------------------------------------------------
# 4. Writes answer through the same mask as the reads (panel round 2)
# ---------------------------------------------------------------------------
_NOTE_MONEY = ("rate_paise", "taxable_paise", "totals", "totals_rupees")


@pytest.fixture
def notes(buying, monkeypatch):
    """The shop plus our company, so a debit note knows which GSTIN it is
    issued on (rtv_debit_notes bound get_db at import)."""
    from api.routers import rtv_debit_notes as dn_mod

    monkeypatch.setattr(dn_mod, "get_db", lambda: _Conn(buying))
    buying.entities.insert_one({"entity_id": "E1", "name": "Better Vision", "gstins": [{"gstin": "27AABCB1234C1ZE"}]})
    buying.stores.insert_one({"store_id": STORE, "entity_id": "E1", "gstin": "27AABCB1234C1ZE", "state_code": "27"})
    return buying


@pytest.mark.parametrize("role,sees", (
    ("STORE_MANAGER", False), ("AREA_MANAGER", False), ("ACCOUNTANT", True), ("ADMIN", True)))
def test_issuing_a_debit_note_reads_its_money_back_to_accounts_only(client, notes, role, sees):
    # Issuing is idempotent: the second post returns the note already issued,
    # so an unmasked reply would read any note's money.
    body = {"source_type": "vendor_return", "rtv_id": "VR1"}
    first = client.post("/api/v1/rtv-debit-notes/issue", json=body, headers=_headers(role))
    again = client.post("/api/v1/rtv-debit-notes/issue", json=body, headers=_headers(role))
    if not sees:
        assert (first.status_code, again.status_code) == (403, 403), first.text
        assert notes.rtv_debit_notes.count_documents({}) == 0
        return
    assert (first.status_code, again.status_code) == (201, 201), first.text
    assert again.json()["idempotent"] is True
    for r in (first, again):
        note = r.json()["debit_note"]
        assert note["debit_note_number"] and note["lines"][0]["qty"] == 1
        assert bool(_leaks(r.json(), _NOTE_MONEY)) is sees, r.json()
        assert ("27AAPFU0939F1ZV" in r.text) is sees  # the supplier's GSTIN


@pytest.mark.parametrize("role,status", (
    ("STORE_MANAGER", 403), ("AREA_MANAGER", 403), ("ACCOUNTANT", 200), ("ADMIN", 200)))
def test_the_debit_note_tally_export_is_for_accounts_only(client, notes, role, status):
    issued = client.post(
        "/api/v1/rtv-debit-notes/issue", json={"rtv_id": "VR1"}, headers=_headers("ACCOUNTANT")
    )
    assert issued.status_code == 201, issued.text
    dn = issued.json()["debit_note"]["debit_note_id"]
    r = client.get(f"/api/v1/rtv-debit-notes/{dn}/tally", headers=_headers(role))
    assert r.status_code == status, r.text[:300]
    assert (str(COST) in r.text) is (status == 200)


@pytest.mark.parametrize("role,sees", (("STORE_MANAGER", False), ("AREA_MANAGER", False), ("ADMIN", True)))
def test_the_quarantine_queue_and_its_writes_show_cost_to_accounts_only(client, buying, role, sees):
    h = _headers(role)
    put = client.patch("/api/v1/inventory/stock/U1/quarantine", json={"reason": "DEFECTIVE"}, headers=h)
    assert put.status_code == 200, put.text
    assert put.json()["stock_unit"]["status"] == "QUARANTINED"
    queue = _get(client, role, "/api/v1/inventory/stock/quarantined")
    assert queue.status_code == 200, queue.text
    assert [it["stock_id"] for it in queue.json()["items"]] == ["U1"]
    lift = client.patch(
        "/api/v1/inventory/stock/U1/lift-quarantine",
        json={"lift_reason": "not defective after all"}, headers=h,
    )
    assert lift.status_code == 200, lift.text
    for r in (put, queue, lift):
        assert bool(_leaks(r.json())) is sees, r.json()


@pytest.mark.parametrize("role,sees", (("STORE_MANAGER", False), ("AREA_MANAGER", False), ("ADMIN", True)))
def test_locking_the_day_end_count_shows_its_value_to_accounts_only(client, buying, role, sees):
    # P1 counted at 0 with units on hand: the lock reveals a shortage at cost.
    h = _headers(role)
    opened = client.post("/api/v1/blind-count/open", json={"store_id": STORE}, headers=h)
    assert opened.status_code == 200, opened.text
    sid = opened.json()["session_id"]
    sub = client.post(
        f"/api/v1/blind-count/{sid}/submit",
        json={"counts": [{"product_id": "P1", "counted_qty": 0}]}, headers=h,
    )
    assert sub.status_code == 200, sub.text
    locked = client.post(f"/api/v1/blind-count/{sid}/lock", headers=h)
    assert locked.status_code == 200, locked.text
    assert locked.json()["status"].upper() == "LOCKED"
    assert any(it.get("variance_units") for it in locked.json()["items"]), locked.json()
    assert bool(_leaks(locked.json())) is sees, locked.json()


@pytest.mark.parametrize("role,sees", (("CATALOG_MANAGER", False), ("ADMIN", True)))
def test_writing_a_lens_line_echoes_its_cost_to_accounts_only(client, buying, role, sees):
    for enum_id, items in (("brands", ["Essilor", "Zeiss"]), ("series", ["Crizal", "Clear"])):
        buying.lens_enum_config.insert_one({"enum_id": enum_id, "items": items})
    buying.lens_catalog.update_one({"lens_line_id": "LL1"}, {"$set": {"mrp": 9999.0}})
    h = _headers(role)
    replies = [
        client.patch("/api/v1/lens-catalog/LL1", json={}, headers=h),
        client.patch("/api/v1/lens-catalog/LL1", json={"notes": "re-checked"}, headers=h),
        client.post(
            "/api/v1/lens-catalog",
            json={"brand": "Zeiss", "series": "Clear", "index": 1.6, "material": "POLY",
                  "lens_type": "SV", "coating": "AR", "mrp": 9999.0, "cost_price": COST},
            headers=h,
        ),
    ]
    for r in replies:
        assert r.status_code in (200, 201), r.text
        assert r.json()["lens_line"]["brand"]
        assert bool(_leaks(r.json())) is sees, r.json()


@pytest.mark.parametrize("role", ("STORE_MANAGER", "CATALOG_MANAGER", "ADMIN"))
def test_a_collection_rule_cannot_search_on_cost(client, buying, role):
    # A rule over the cost (or the text of a nested object that holds it) is a
    # cost oracle: "cost > X" binary-searched recovers it to the paisa. It
    # matches nothing, for every role; a real rule still matches.
    buying.products.update_one(
        {"product_id": "P1"}, {"$set": {"pricing": {"cost_price": COST, "offer_price": 9999.0}}}
    )

    def count(rules):
        r = client.post("/api/v1/collections/preview", json={"rules": rules}, headers=_headers(role))
        assert r.status_code == 200, r.text
        return r.json()["match_count"]

    assert count([{"field": "brand", "relation": "EQUALS", "value": "Ray-Ban"}]) >= 1
    for field in ("cost_price", "Cost Price", "landed_cost"):
        for value in ("0", "4321", "4322"):
            assert count([{"field": field, "relation": "GREATER_THAN", "value": value}]) == 0, field
            assert count([{"field": field, "relation": "LESS_THAN", "value": value}]) == 0, field
    assert count([{"field": "pricing", "relation": "CONTAINS", "value": "4321"}]) == 0


# ---------------------------------------------------------------------------
# 5. One price rule for every PO door; a line nobody priced is not a 0
# ---------------------------------------------------------------------------
def test_an_unpriced_line_billed_at_its_price_is_no_price_exception():
    # None (create_po) or 0.0 (a CL / forecast draft) is "no PO price": the
    # bill's price stands; it is not "differs from PO price 0 by 1e9%".
    from api.services.purchase_match import three_way_match

    def match(po_price):
        return three_way_match(
            {"items": [{"product_id": "P4", "quantity": 2, "unit_price": po_price}]},
            {"items": [{"product_id": "P4", "accepted_qty": 2}]},
            [{"product_id": "P4", "qty": 2, "unit_price": 600.0}],
        )

    for unpriced in (None, 0.0):
        res = match(unpriced)
        assert res["match_status"] == "MATCHED", (unpriced, res["exceptions"])
        assert res["lines"][0]["po_unit_price"] is None
    # ...while a priced line still has its price checked.
    assert match(500.0)["match_status"] != "MATCHED"


@pytest.mark.parametrize("unpriced", (None, 0.0))
def test_sending_a_draft_with_an_unpriced_line_raises_the_accounts_task(client, catalogue, unpriced):
    # A CL / forecast draft stored 0.0 where create_po stores None: both are
    # "nobody priced it" and both raise the task at send.
    catalogue.purchase_orders.insert_one(
        {"po_id": "PO-CL", "po_number": "PO-CL-1", "vendor_id": "V1", "vendor_name": "Acme Optics",
         "delivery_store_id": STORE, "status": "DRAFT", "source": "cl_po_generator",
         "items": [{"product_id": "P2", "product_name": "P2", "quantity": 1, "ordered_qty": 1,
                    "unit_price": unpriced, "line_status": "OPEN"}]}
    )
    r = client.post("/api/v1/vendors/purchase-orders/PO-CL/send", headers=_headers("STORE_MANAGER"))
    assert r.status_code == 200, r.text
    assert [t["source_ref"] for t in _price_tasks(catalogue)] == ["po-price:PO-CL"]


def test_the_forecast_drafts_price_their_lines_by_the_one_rule(client, catalogue):
    # P1 was last paid COST to V1 (its catalogue says 1234.5); P3 has no price
    # anywhere. The forecast door stores what create_po stores: COST and None.
    catalogue.products.update_many(
        {"product_id": {"$in": ["P1", "P3"]}}, {"$set": {"preferred_vendor_id": "V1"}}
    )
    catalogue.orders.insert_one(
        {"order_id": "O9", "store_id": STORE, "status": "DELIVERED", "created_at": datetime.now(),
         "items": [{"product_id": "P3", "product_name": "P3", "quantity": 5}]}
    )
    r = client.post(
        "/api/v1/vendors/purchase-orders/from-forecast",
        json={"store_id": STORE}, headers=_headers("STORE_MANAGER"),
    )
    assert r.status_code == 201, r.text
    assert not _leaks(r.json(), _PRICE_KEYS + ("estimated_unit_price",)), r.json()
    (po,) = list(catalogue.purchase_orders.find({"source": "demand_forecast"}))
    assert {it["product_id"]: it["unit_price"] for it in po["items"]} == {"P1": COST, "P3": None}


@pytest.mark.parametrize("role", ("ADMIN", "STORE_MANAGER", "AREA_MANAGER"))
def test_the_cl_drafts_price_their_lines_by_the_one_rule(client, catalogue, monkeypatch, role):
    from api.routers import cl_po

    needs = [
        {"product_id": "P1", "sph": -1.0, "qty": 2, "description": "Aviator", "unit_price": 1234.5},
        {"product_id": "LL9", "sph": -2.0, "qty": 1, "description": "Unknown lens", "unit_price": 0.0},
    ]
    monkeypatch.setattr(cl_po, "_read_gap_planner_needs", lambda *_a, **_k: needs)
    monkeypatch.setattr(cl_po, "_make_vendor_resolver", lambda _db: (lambda _need: "V1"))
    r = client.post(
        "/api/v1/cl-po/generate", json={"store_id": STORE, "source": "gap-planner", "dry_run": True},
        headers=_headers(role),
    )
    assert r.status_code == 200, r.text
    (group,) = r.json()["groups"]
    if role == "ADMIN":
        assert {ln["product_id"]: ln["unit_price"] for ln in group["lines"]} == {"P1": COST, "LL9": None}
    else:
        assert not _leaks(r.json(), _PRICE_KEYS), r.json()
        assert {ln["product_id"] for ln in group["lines"]} == {"P1", "LL9"}


@pytest.mark.parametrize("role,told", (("STORE_MANAGER", False), ("ACCOUNTANT", True)))
def test_only_the_roles_that_price_are_told_a_cost_was_recorded(client, buying, role, told):
    # P1 has no catalogue cost; the server prices it at the last price paid and
    # records that as its cost. A manager typed no rate: he is not told so.
    buying.products.update_one({"product_id": "P1"}, {"$unset": {"cost_price": ""}})
    reply = _po(client, role, ("P1", {}))
    assert bool(reply["cost_filled"]) is told, reply
    assert buying.products.find_one({"product_id": "P1"})["cost_price"] == COST


@pytest.mark.parametrize("suffix", ("", "/stores"))
@pytest.mark.parametrize("role", ("STORE_MANAGER", "AREA_MANAGER", "CATALOG_MANAGER", "ADMIN"))
def test_collection_insight_routes_mask_computed_cost(client, buying, monkeypatch, suffix, role):
    from api.routers import collections_insights as mod
    monkeypatch.setattr(mod, "_get_db", lambda: buying)
    monkeypatch.setattr(mod, "_load_collection_or_404", lambda *_a: {"id": "COST-COL", "title": "Frames"})
    block = {"units_on_hand": 2, "stock_value": COST, "margin_30d": 52.5, "revenue_30d": 9999}
    monkeypatch.setattr(mod._svc, "kpis", lambda *_a, **_k: dict(block))
    monkeypatch.setattr(mod._svc, "store_breakdown", lambda *_a, **_k: [{"store_id": STORE, **block}])
    reply = client.get("/api/v1/collections/COST-COL/insights" + suffix, headers=_headers(role))
    assert reply.status_code == 200, reply.text
    data = reply.json()["stores"][0] if suffix else reply.json()
    assert data["units_on_hand"] == 2 and data["revenue_30d"] == 9999
    if role == "ADMIN":
        assert data["stock_value"] == COST and data["margin_30d"] == 52.5
    else:
        assert "stock_value" not in data and "margin_30d" not in data
