"""
An online order's OWN persisted place of supply is what its invoice, GSTR-1 row
and Tally row print (money panel 2026-09-28, pre-existing finding).

The online ingest stamps ``place_of_supply`` + ``interstate`` from the buyer's
DELIVERY state, and GSTR-1/3B file the IGST head off that persisted flag. The
invoice door (JSON + PDF), GSTR-1's place-of-supply column and the Tally B2B
list re-derived the place of supply from the CUSTOMER doc -- for a returning
buyer that is the state of their FIRST delivery (the mapper never overwrites
it). Case: ships from Bokaro (JH, 20) to Maharashtra (27), customer doc says 20
-> the order files IGST at 27 while the invoice printed CGST+SGST at 20 and
GSTR-1 filed an IGST row with the supplier's own state as place of supply.

A POS order persists no place of supply, so its rule (customer state) is
pinned unchanged here too. mongomock only; no network.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test_x")

mongomock = pytest.importorskip("mongomock")

_ITEMS = [{"product_id": "P-RB", "quantity": 1, "gst_rate": 12.0,
           "taxable_value": 892.86, "tax_amount": 107.14, "item_total": 1000.0}]


def _order(order_id, **extra):
    return {
        "order_id": order_id, "_id": order_id, "order_number": order_id,
        "invoice_number": f"INV/BV-BOK-01/26-27/{order_id}", "store_id": "BV-BOK-01",
        "customer_id": "C-1", "customer_name": "Ravi", "status": "CONFIRMED",
        "created_at": datetime(2026, 9, 15, 6, 0), "items": _ITEMS,
        "grand_total": 1000.0, "tax_amount": 107.14, **extra,
    }


@pytest.fixture
def db(monkeypatch):
    db = mongomock.MongoClient()["ims_pos"]
    db.stores.insert_one({"store_id": "BV-BOK-01", "store_name": "Bokaro", "state_code": "20",
                          "state": "20", "gstin": "20AAAAA0000A1Z5", "is_active": True})
    # A returning buyer: the customer doc still carries their FIRST delivery state.
    db.customers.insert_one({"customer_id": "C-1", "name": "Ravi", "state": "20",
                             "customer_type": "B2B", "billing_address": {"state_code": "20"}})
    # Online: delivered to Maharashtra, filed IGST at 27.
    db.orders.insert_one(_order("ONL-1", channel="ONLINE", place_of_supply="27",
                                place_of_supply_assumed=False, interstate=True))
    # POS: no persisted place of supply -> the customer's state decides.
    db.orders.insert_one(_order("POS-1"))

    import api.dependencies as deps
    from api.routers.orders import invoices as inv_mod
    from api.routers.reports import gstr1 as gstr1_mod
    from database.repositories.customer_repository import CustomerRepository
    from database.repositories.order_repository import OrderRepository

    class _Stores:
        def find_by_id(self, store_id):
            return db.stores.find_one({"store_id": store_id}, {"_id": 0})

    monkeypatch.setattr(deps, "get_store_repository", lambda: _Stores())
    monkeypatch.setattr(inv_mod, "get_order_repository", lambda: OrderRepository(db.orders))
    monkeypatch.setattr(inv_mod, "get_customer_repository", lambda: CustomerRepository(db.customers))
    monkeypatch.setattr(gstr1_mod, "_get_raw_db", lambda: db)
    return db


def _invoice(order_id):
    from api.routers.orders import invoices as inv_mod

    payload, _order_doc, _cust = inv_mod._assemble_invoice(order_id, {"roles": ["SUPERADMIN"]})
    return payload


def test_the_invoice_prints_the_orders_own_place_of_supply(db):
    online = _invoice("ONL-1")
    assert online["placeOfSupply"] == "27" and online["interstate"] is True
    assert online["taxTotals"]["igst"] == 107.14 and online["taxTotals"]["cgst"] == 0

    pos = _invoice("POS-1")  # unchanged: the customer's state, intra-state
    assert pos["placeOfSupply"] == "20" and pos["interstate"] is False


def test_gstr1_files_the_orders_own_place_of_supply(db):
    from api.routers.reports import gstr1 as gstr1_mod

    rows = gstr1_mod._compute_gstr1("2026-09", "BV-BOK-01")["b2cs"]
    by_pos = {(r["placeOfSupply"], r["igst"] > 0) for r in rows}
    assert by_pos == {("27", True), ("20", False)}, rows


def test_tally_lists_the_orders_own_place_of_supply(db):
    from api.routers.finance.tally import _b2b_invoices

    rows = {r["order_id"]: r for r in _b2b_invoices(db, store_id="BV-BOK-01")}
    assert rows["ONL-1"]["place_of_supply"] == "27" and rows["ONL-1"]["igst"] > 0
    assert rows["POS-1"]["place_of_supply"] == "20" and rows["POS-1"]["igst"] == 0
