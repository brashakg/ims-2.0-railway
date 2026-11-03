"""
GST batch 5 -- the till / order path (owner ruling 26, 2026-11-03)
==================================================================
(a) GSTR-1 files the TAX INVOICE: its own date (invoice_date, stamped at the
    mint) when it falls in the month, an invoice dated after the month is
    flagged; a revised invoice is refused once the original's month is over
    (that change goes on a credit / debit note).
(b) The buyer GSTIN is frozen on the order in the minting write; GSTR-1 files
    B2B / B2C (and the notes' CDNR) on that stamp, never on the customer
    record read later. A malformed GSTIN refuses the mint.
(c) Cancel vs mint: a number never lands on a cancelled order, and a cancel
    never lands on an invoice minted after the cancel door read the order.

Revert map: (a) -> test_gstr1_files_the_invoice_date*, test_gstr1_flags_*,
test_superadmin_order_edit::test_revised_invoice_refused_after_the_originals_month;
(b) -> test_door_stamps_*, test_gstr1_files_the_stamped_gstin*,
test_door_refuses_a_bad_gstin; (c) -> test_set_invoice_never_numbers_*,
test_cancel_loses_to_an_invoice_*.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import api.routers.reports as r  # noqa: E402
from api.routers.orders import cancel as cancel_module  # noqa: E402
from api.routers.orders.release import _claim_order_for_cancel  # noqa: E402
from database.repositories.order_repository import InvoiceNumberError  # noqa: E402
from strict_fakes import StrictDB  # noqa: E402
from test_gst_invoice_number_16 import _order, _world, door  # noqa: E402,F401

GSTIN_SHOP = "20AAACR0000A1ZZ"
GSTIN_BUYER = "20AAACB1234C1Z5"
JUNE = "2026-06"


def _gst_db(order, customer):
    db = StrictDB()
    db.seed("stores", [{"store_id": "S1", "store_code": "BV-BOK-02", "gstin": GSTIN_SHOP,
                        "store_name": "Bokaro", "state": "Jharkhand"}])
    db.seed("customers", [customer])
    db.seed("orders", [order])
    return db


def _sale(**extra):
    return {"order_id": "O1", "order_number": "O1", "invoice_number": "BOK02/2627/00001",
            "store_id": "S1", "status": "COMPLETED", "created_at": datetime(2026, 6, 2, 6, 0),
            "grand_total": 105.0, "tax_amount": 5.0, "customer_id": "C1",
            "items": [{"gst_rate": 5, "taxable_value": 100.0, "tax_amount": 5.0,
                       "hsn_code": "9004", "item_total": 105.0, "quantity": 1}], **extra}


def _gstr1(monkeypatch, db):
    monkeypatch.setattr(r, "_get_raw_db", lambda: db)
    return r._compute_gstr1(JUNE, "S1")


# ---------------------------------------------------------------------------
# (a) the invoice's own date
# ---------------------------------------------------------------------------


def test_gstr1_files_the_invoice_date_not_the_order_date(monkeypatch):
    """Order booked 2 June, invoice minted 10 June: the row files 10 June."""
    cust = {"customer_id": "C1", "name": "Acme", "gstin": GSTIN_BUYER, "state": "Jharkhand"}
    db = _gst_db(_sale(invoice_date=datetime(2026, 6, 10, 6, 0)), cust)
    (row,) = _gstr1(monkeypatch, db)["b2b"]
    assert row["invoiceDate"] == "2026-06-10"


def test_gstr1_flags_an_invoice_dated_after_the_month(monkeypatch):
    cust = {"customer_id": "C1", "name": "Acme", "gstin": GSTIN_BUYER, "state": "Jharkhand"}
    db = _gst_db(_sale(invoice_date=datetime(2026, 7, 3, 6, 0)), cust)
    data = _gstr1(monkeypatch, db)
    assert data["b2b"][0]["invoiceDate"] == "2026-06-02"
    assert any("after this month" in i["issue"] for i in data["validation"]["issues"])


# ---------------------------------------------------------------------------
# (b) the buyer GSTIN frozen at the mint
# ---------------------------------------------------------------------------


def test_gstr1_files_the_stamped_gstin_not_the_customer_record(monkeypatch):
    """Minted B2B; the customer's GSTIN was cleared later -- still B2B."""
    db = _gst_db(_sale(customer_gstin=GSTIN_BUYER),
                 {"customer_id": "C1", "name": "Acme", "gstin": "", "state": "Jharkhand"})
    data = _gstr1(monkeypatch, db)
    assert [b["customerGSTIN"] for b in data["b2b"]] == [GSTIN_BUYER]


def test_gstr1_files_the_stamped_consumer_bill_as_b2c(monkeypatch):
    """Minted as a consumer bill; a GSTIN added to the customer later never
    turns the filed invoice into B2B."""
    db = _gst_db(_sale(customer_gstin=""),
                 {"customer_id": "C1", "name": "Acme", "gstin": GSTIN_BUYER, "state": "Jharkhand"})
    data = _gstr1(monkeypatch, db)
    assert data["b2b"] == [] and data["b2cs"]


def _customers(monkeypatch, docs):
    from api.routers import orders as orders_module

    class _CustomerRepo:
        def find_by_id(self, cid):
            return docs.get(cid)

    monkeypatch.setattr(orders_module, "get_customer_repository", lambda: _CustomerRepo())


def test_door_stamps_the_buyer_gstin_in_the_minting_write(door, monkeypatch):
    client, db = door
    _customers(monkeypatch, {"C1": {"customer_id": "C1", "gstin": GSTIN_BUYER.lower()}})
    db.get_collection("orders").insert_one(_order("O-B2B", customer_id="C1"))
    r1 = client.get("/orders/O-B2B/invoice")
    assert r1.status_code == 200, r1.text
    stored = db.get_collection("orders").find_one({"order_id": "O-B2B"})
    assert stored["customer_gstin"] == GSTIN_BUYER
    assert r1.json()["customerGstin"] == GSTIN_BUYER
    # The customer's GSTIN changes later: a reprint shows what was issued.
    _customers(monkeypatch, {"C1": {"customer_id": "C1", "gstin": ""}})
    assert client.get("/orders/O-B2B/invoice").json()["customerGstin"] == GSTIN_BUYER


def test_door_refuses_a_bad_gstin(door, monkeypatch):
    client, db = door
    _customers(monkeypatch, {"C1": {"customer_id": "C1", "gstin": "XXAAACB1234C1Z5"}})
    db.get_collection("orders").insert_one(_order("O-BAD", customer_id="C1"))
    r1 = client.get("/orders/O-BAD/invoice")
    assert r1.status_code == 400 and "not a valid GSTIN" in r1.json()["detail"]
    assert not db.get_collection("orders").find_one({"order_id": "O-BAD"}).get("invoice_number")


# ---------------------------------------------------------------------------
# (c) cancel vs mint
# ---------------------------------------------------------------------------


def test_set_invoice_never_numbers_a_cancelled_order():
    """The door read the order live; a cancel landed before the write: no
    number on the order (the serial is a gap Table 13 files cancelled)."""
    repo, db = _world()
    db.get_collection("orders").insert_one(_order("O-C", status="CANCELLED"))
    with pytest.raises(InvoiceNumberError, match="cancelled"):
        repo.set_invoice("O-C", "BOK02/2627/00009")
    assert not db.get_collection("orders").find_one({"order_id": "O-C"}).get("invoice_number")


def test_cancel_claim_loses_to_an_invoice_minted_after_the_read():
    repo, db = _world()
    db.get_collection("orders").insert_one(_order("O-R", invoice_number="BOK02/2627/00001"))
    assert _claim_order_for_cancel(repo, "O-R", "r" * 10, {"user_id": "u"}, invoice=None) is None
    assert db.get_collection("orders").find_one({"order_id": "O-R"})["status"] == "CONFIRMED"


def test_cancel_loses_to_an_invoice_minted_mid_cancel(monkeypatch):
    """The cancel door read the order numberless; the invoice door minted
    before the claim: 409 naming the invoice, order still a sale."""
    repo, db = _world()
    db.get_collection("orders").insert_one(_order("O-M"))
    stale = dict(db.get_collection("orders").find_one({"order_id": "O-M"}))
    real_find = repo.find_by_id
    calls = {"n": 0}

    def _find(oid):
        calls["n"] += 1
        if calls["n"] == 1:
            db.get_collection("orders").update_one(
                {"order_id": oid}, {"$set": {"invoice_number": "BOK02/2627/00002"}})
            return stale
        return real_find(oid)

    monkeypatch.setattr(repo, "find_by_id", _find)
    monkeypatch.setattr(cancel_module, "get_order_repository", lambda: repo)
    monkeypatch.setattr(cancel_module, "validate_store_access", lambda sid, user: sid)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(cancel_module.cancel_order_core("O-M", "r" * 10, {"user_id": "u", "roles": ["ADMIN"]}))
    assert exc.value.status_code == 409 and "BOK02/2627/00002" in exc.value.detail
    assert db.get_collection("orders").find_one({"order_id": "O-M"})["status"] == "CONFIRMED"

