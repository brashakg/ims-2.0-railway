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
    return shop


_DAY = datetime.now().date().isoformat()
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
    # an empty shop. The accountant has no workshop / blind-count screen.
    r = _get(client, role, path)
    if role == "ACCOUNTANT" and r.status_code == 403:
        assert "spoilage" in path or "blind-count" in path, path
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
             "provisional": True}
        )
    # P1's catalogue cost is not what was last paid for it (COST, receipt G1),
    # so a PO that took the catalogue cost over the last price paid shows.
    buying.products.update_one({"product_id": "P1"}, {"$set": {"cost_price": 1234.5}})
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


def test_an_unpriced_line_is_received_and_billed_its_cost_comes_with_the_bill(client, catalogue):
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


def test_sending_a_priced_po_raises_no_task(client, catalogue):
    po_id = _po(client, "STORE_MANAGER", ("P1", {}), ("P2", {}))["po_id"]
    r = client.post(f"/api/v1/vendors/purchase-orders/{po_id}/send", headers=_headers("STORE_MANAGER"))
    assert r.status_code == 200, r.text
    assert _price_tasks(catalogue) == []


# ---------------------------------------------------------------------------
# 3. ONE rule: widen cost_mask and every read above moves with it
# ---------------------------------------------------------------------------
def test_every_read_follows_the_one_cost_rule(client, buying, monkeypatch):
    from api.services import cost_mask

    for ctx in ("product", "purchase"):
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
    def post(path, body):
        return client.post(f"/api/v1/vendor-rma{path}", json=body, headers=_headers(role))

    made = post("", {"vendor_id": "V1", "vendor_name": "Acme Optics", "store_id": STORE,
                     "lines": [{"product_id": "P1", "product_name": "Aviator",
                                "quantity": QTY, "reason": "DEFECTIVE", "unit_cost": COST}]})
    assert made.status_code == 201, made.text
    rid = made.json()["rma_id"]
    steps = [made]
    for path, body in (
        ("/authorize", {"vendor_rma_number": "VRMA-2"}),
        ("/dispatch", {"carrier": "Delhivery", "awb": "AWB-2"}),
        ("/credit-note", {"credit_note_number": "CN-2", "received_amount": COST}),
    ):
        steps.append(post(f"/{rid}{path}", body))
        assert steps[-1].status_code == 200, (path, steps[-1].text)
    refused = post(f"/{rid}/close", {})
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["error"] == "variance_outstanding"
    closed = post(f"/{rid}/close", {"write_off_variance": True})
    assert (closed.status_code, closed.json()["status"]) == (200, "CLOSED"), closed.text
    read = client.get(f"/api/v1/vendor-rma/{rid}", headers=_headers(role)).json()
    assert [s["status"] for s in read["status_history"]] == [
        "DRAFT", "AUTHORIZED", "DISPATCHED", "CREDIT_RECEIVED", "CLOSED"]
    assert [ln["quantity"] for ln in read["lines"]] == [QTY]
    money = (made, steps[3], refused, closed)
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
    # A manager may move a return on (and type its price at approval) but no
    # manager reads what we paid back (owner ruling 2026-10-12).
    r = client.patch(
        "/api/v1/vendor-returns/VR1/status", json={"status": "approved"}, headers=_headers(role)
    )
    assert r.status_code == 200, r.text
    assert r.json()["return"]["status"] == "approved"
    assert bool(_leaks(r.json(), _PRICE_KEYS + ("total_value",))) is sees, r.json()
