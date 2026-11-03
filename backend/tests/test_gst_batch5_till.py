"""
GST batch 5 -- the till / order path (owner ruling 26, 2026-11-03)
==================================================================
(a) GSTR-1, GSTR-3B and Table 13 file the TAX INVOICE in the month of its
    own date (invoice_date, stamped at the mint), on that date; a revised invoice is refused once the original's month is over
    (that change goes on a credit / debit note).
(b) The buyer GSTIN is frozen on the order in the minting write; GSTR-1 files
    B2B / B2C (and the notes' CDNR) on that stamp, never on the customer
    record read later. A malformed GSTIN refuses the mint.
(c) Cancel vs mint: a number never lands on a cancelled order, and a cancel
    never lands on an invoice minted after the cancel door read the order.

Revert map: (a) -> test_gstr1_files_the_invoice_date*, test_an_invoice_minted_next_month_*,
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


def test_an_invoice_minted_next_month_files_in_its_own_month(monkeypatch):
    """Order taken 28 June, invoice printed 02 July 00:30 IST: July's GSTR-1,
    Table 13 and GSTR-3B file it on 02 July -- June's never see it.
    REVERT CHECK: the created_at window files it in June (in no return once
    June is filed)."""
    from api.routers.reports import gstr3b as r3b

    cust = {"customer_id": "C1", "name": "Acme", "gstin": GSTIN_BUYER, "state": "Jharkhand"}
    db = _gst_db(_sale(created_at=datetime(2026, 6, 28, 6, 0),
                       invoice_date=datetime(2026, 7, 1, 19, 0)), cust)
    monkeypatch.setattr(r, "_get_raw_db", lambda: db)
    monkeypatch.setattr(r3b, "_get_raw_db", lambda: db, raising=False)
    june, july = r._compute_gstr1(JUNE, "S1"), r._compute_gstr1("2026-07", "S1")
    assert (june["b2b"], june["docsIssued"]) == ([], [])
    assert [b["invoiceDate"] for b in july["b2b"]] == ["2026-07-02"]
    assert [(d["from"], d["total"]) for d in july["docsIssued"]] == [("BOK02/2627/00001", 1)]
    assert r3b._compute_gstr3b(JUNE, "S1")["outwardTaxableValue"] == 0
    assert r3b._compute_gstr3b("2026-07", "S1")["outwardTaxableValue"] == 100.0


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


def test_door_mints_a_placeholder_gstin_as_a_consumer_bill(door, monkeypatch):
    """A legacy import's 'NA' is no GSTIN: a B2C invoice stamped '', never a
    refused one. REVERT CHECK: the strict check refuses it (400)."""
    client, db = door
    _customers(monkeypatch, {"C1": {"customer_id": "C1", "gstin": "NA"}})
    db.get_collection("orders").insert_one(_order("O-NA", customer_id="C1"))
    r1 = client.get("/orders/O-NA/invoice")
    assert r1.status_code == 200, r1.text
    assert db.get_collection("orders").find_one({"order_id": "O-NA"})["customer_gstin"] == ""


def test_door_refuses_when_the_customer_cannot_be_read(door, monkeypatch):
    """A failed customer read never freezes a B2B sale as a consumer one."""
    from api.routers import orders as orders_module

    class _Down:
        def find_by_id(self, cid):
            raise RuntimeError("db down")

    monkeypatch.setattr(orders_module, "get_customer_repository", lambda: _Down())
    client, db = door
    db.get_collection("orders").insert_one(_order("O-DOWN", customer_id="C1"))
    r1 = client.get("/orders/O-DOWN/invoice")
    assert r1.status_code == 400 and "could not be read" in r1.json()["detail"]
    stored = db.get_collection("orders").find_one({"order_id": "O-DOWN"})
    assert not stored.get("invoice_number") and "customer_gstin" not in stored


def test_counter_sale_to_an_out_of_state_buyer_is_the_shops_state(door, monkeypatch):
    """CA 2026-10-23: the buyer takes the goods at the Jharkhand shop --
    CGST+SGST, place of supply 20, stamped at the mint; a website order keeps
    its own. REVERT CHECK: without the stamp the Maharashtra buyer prints IGST."""
    client, db = door
    _customers(monkeypatch, {"C1": {"customer_id": "C1", "billing_address": {"state": "Maharashtra"}}})
    db.get_collection("orders").insert_one(_order("O-CTR", customer_id="C1"))
    body = client.get("/orders/O-CTR/invoice").json()
    assert (body["interstate"], body["placeOfSupply"]) == (False, "20")
    stored = db.get_collection("orders").find_one({"order_id": "O-CTR"})
    assert (stored["interstate"], stored["place_of_supply"]) == (False, "20")
    db.get_collection("orders").insert_one(_order("O-WEB", customer_id="C1", channel="ONLINE"))
    assert client.get("/orders/O-WEB/invoice").json()["interstate"] is True
    assert "interstate" not in db.get_collection("orders").find_one({"order_id": "O-WEB"})
    # ...and GSTR-1 files the stamp: an intra-state B2CS row for state 20.
    gdb = _gst_db(_sale(customer_gstin="", interstate=False, place_of_supply="20"),
                  {"customer_id": "C1", "name": "P", "state": "Maharashtra"})
    [row] = _gstr1(monkeypatch, gdb)["b2csFiled"]
    assert (row["supplyType"], row["placeOfSupplyCode"], row["igst"]) == ("INTRA", "20", 0.0)


def test_returns_file_the_stamped_gstins_state_not_the_edited_record(monkeypatch):
    """Minted to a Jharkhand GSTIN; the record now holds a Maharashtra one.
    GSTR-1 files intra-state on the stamp, and GSTR-3B files the same heads.
    REVERT CHECK: _order_supply on the live record files IGST in both."""
    from api.routers.reports import gstr3b as r3b

    db = _gst_db(_sale(customer_gstin=GSTIN_BUYER),
                 {"customer_id": "C1", "name": "Acme", "gstin": "27AAACB1234C1Z5",
                  "state": "Maharashtra"})
    monkeypatch.setattr(r3b, "_get_raw_db", lambda: db, raising=False)
    (row,) = _gstr1(monkeypatch, db)["b2b"]
    assert (row["placeOfSupplyCode"], row["igst"], row["customerGSTIN"]) == ("20", 0.0, GSTIN_BUYER)
    out = r3b._compute_gstr3b(JUNE, "S1")["outwardTaxableSupplies"]
    assert (out["integratedTax"], out["centralTax"], out["stateTax"]) == (
        row["igst"], row["cgst"], row["sgst"])


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



def test_gstr3b_table_3_2_reads_the_stamped_gstin(monkeypatch):
    """An inter-state sale minted to a registered buyer whose record lost its
    GSTIN later: B2B on GSTR-1, so never in GSTR-3B Table 3.2 either."""
    from api.routers.reports import gstr3b as r3b

    sale = _sale(customer_gstin="27AAACR5055K1Z5")
    db = _gst_db(sale, {"customer_id": "C1", "name": "Acme", "gstin": "", "state": "Maharashtra"})
    monkeypatch.setattr(r, "_get_raw_db", lambda: db)
    monkeypatch.setattr(r3b, "_get_raw_db", lambda: db, raising=False)
    assert [b["customerGSTIN"] for b in r._compute_gstr1(JUNE, "S1")["b2b"]] == ["27AAACR5055K1Z5"]
    assert r3b._compute_gstr3b(JUNE, "S1")["interStateUnregistered"] == []


# ---------------------------------------------------------------------------
# (b) the notes file on their invoice's stamped buyer, never the record now
# ---------------------------------------------------------------------------

from tests.test_gst_batch2_credit_notes import (  # noqa: E402
    _cash_return, _debit_note, _superadmin_credit_note)
from tests.test_gst_batch3_per_gstin import _sale as _b3_sale, gstins  # noqa: E402,F401
from tests.test_receipt_without_po import MONTH, STORE, world  # noqa: E402,F401
from api.routers.reports import gstr1 as r1  # noqa: E402

_NOTES = {
    "return": lambda db, c: _cash_return(db, "RET-B5-1", "O-N", c, 200.0, 10.0),
    "register": lambda db, c: _superadmin_credit_note(db, "BOK/C2627/0051", "O-N", c, 200.0, 10.0),
    "debit": lambda db, c: _debit_note(db, "BOK/D2627/0051", "O-N", c, 100.0, 5.0),
}


@pytest.mark.parametrize("note", sorted(_NOTES))
@pytest.mark.parametrize("live,stamp,section", [
    ("C-MH", "27AAACR5055K1Z5", "CDNR"),   # minted B2B; the record lost its GSTIN
    ("C-MH-B2B", "", "B2CS"),              # minted B2C; a GSTIN added later
])
def test_a_note_files_on_its_invoices_stamped_gstin(gstins, note, live, stamp, section):
    """REVERT CHECK: each note path reading the live customer GSTIN files the
    other section (and the CDNR buyer GSTIN drops)."""
    _b3_sale(gstins, STORE, "O-N", live, 2100.0)
    gstins.orders.update_one({"order_id": "O-N"}, {"$set": {"customer_gstin": stamp}})
    _NOTES[note](gstins, live)
    [cn] = r1._compute_gstr1(MONTH, STORE)["cdnr"]
    assert (cn["noteSection"], cn["customerGSTIN"]) == (section, stamp)


def test_the_books_split_on_the_stamped_gstin(monkeypatch):
    """Tally / finance GST cards and the credit-note head ask _order_supply
    too: a sale minted to a Jharkhand GSTIN at a Jharkhand shop stays intra
    after the record moves to Maharashtra.
    REVERT CHECK: without the overlay in _order_supply it books IGST."""
    from api.routers.finance._shared import _order_is_interstate

    order = {"store_id": "S1", "customer_id": "C1", "customer_gstin": GSTIN_BUYER}
    assert _order_is_interstate(order, {"S1": {"state_code": "20"}},
                                {"C1": {"gstin": "27AAACB1234C1Z5", "state": "Maharashtra"}}) is False


# ---------------------------------------------------------------------------
# Review round: the books / Tally side asks the SAME sale month and buyer
# ---------------------------------------------------------------------------

_ADMIN = {"user_id": "u-admin", "username": "admin", "roles": ["SUPERADMIN"],
          "active_role": "SUPERADMIN"}


def _next_month_sale(**extra):
    """Order taken 28 June, invoice printed 02 July 00:30 IST."""
    return _sale(created_at=datetime(2026, 6, 28, 6, 0),
                 invoice_date=datetime(2026, 7, 1, 19, 0), **extra)


def test_cross_check_matches_an_invoice_minted_next_month(monkeypatch):
    """The books and Tally legs file the invoice in its own month, as GSTR-1
    / 3B do: every cross-check row is MATCH in June AND July.
    REVERT CHECK: the created_at window in _books_and_tally_for_stores puts
    the books in June and GSTR-1 in July -- four MISMATCH rows each month."""
    from api.routers.finance.gst_crosscheck import _run_gst_cross_check

    cust = {"customer_id": "C1", "name": "Acme", "gstin": GSTIN_BUYER, "state": "Jharkhand"}
    db = _gst_db(_next_month_sale(), cust)
    db.stores.update_one({"store_id": "S1"}, {"$set": {"entity_id": "E1"}})
    monkeypatch.setattr(r, "_get_raw_db", lambda: db)
    for m in (6, 7):
        rows = _run_gst_cross_check(db, m, 2026, None)["comparisons"]
        assert [c["metric"] for c in rows if c["status"] == "MISMATCH"] == [], m


def _tally(monkeypatch, db):
    from api.routers.finance import tally

    monkeypatch.setattr(tally, "_get_db", lambda: db)
    return tally


def test_tally_files_an_invoice_minted_next_month_in_its_own_month(monkeypatch):
    """The B2B book and the sales JV select and date the invoice on 02 July.
    REVERT CHECK: the created_at window lists / exports it in June, dated 28 June."""
    import agents.nexus_providers as nexus

    tally = _tally(monkeypatch, _gst_db(_next_month_sale(), {
        "customer_id": "C1", "name": "Acme", "gstin": GSTIN_BUYER, "state": "Jharkhand"}))
    assert tally._b2b_invoices(tally._get_db(), from_date="2026-06-01", to_date="2026-06-30") == []
    rows = tally._b2b_invoices(tally._get_db(), from_date="2026-07-01", to_date="2026-07-31")
    assert [row["date"] for row in rows] == ["2026-07-02"]
    seen = []
    monkeypatch.setattr(nexus, "tally_build_day_voucher_xml",
                        lambda orders, meta: seen.append(list(orders)) or "<x/>")
    for frm, to in (("2026-06-01", "2026-06-30"), ("2026-07-01", "2026-07-31")):
        asyncio.run(tally.get_tally_sales_jv(from_date=frm, to_date=to, store_id="S1",
                                             entity_id=None, current_user=_ADMIN))
    june, july = seen
    assert (june, [o["order_id"] for o in july]) == ([], ["O1"])
    assert nexus.ist_date_str_from_stored(july[0]["created_at"]) == "2026-07-02"


@pytest.mark.parametrize("stamp,live,b2b", [
    (GSTIN_BUYER, "", True),    # minted B2B; the record lost its GSTIN
    ("", GSTIN_BUYER, False),   # minted a consumer bill; a GSTIN added later
])
def test_tally_b2b_reads_the_stamped_gstin(monkeypatch, stamp, live, b2b):
    """The B2B worklist and its export classify on the GSTIN frozen at the
    mint, as GSTR-1 files it.
    REVERT CHECK: dropping _invoice_customer in _b2b_invoices / _b2b_fetch_orders
    flips each case (list or export)."""
    tally = _tally(monkeypatch, _gst_db(_sale(customer_gstin=stamp), {
        "customer_id": "C1", "name": "Acme", "gstin": live, "state": "Jharkhand"}))
    db = tally._get_db()
    assert [row["order_id"] for row in tally._b2b_invoices(db)] == (["O1"] if b2b else [])
    assert [o["order_id"] for o in tally._b2b_fetch_orders(db, ["O1"])] == (
        ["BOK02/2627/00001"] if b2b else [])


@pytest.mark.parametrize("gstin", ["00AAPFU0939F1ZV", "40AAPFU0939F1ZV"])
def test_customer_form_refuses_a_gstin_with_no_state(gstin):
    """The right shape with no Indian state code never reaches a customer
    record (the mint would refuse every invoice for him).
    REVERT CHECK: the shape-only regex saves it."""
    from pydantic import ValidationError
    from api.routers.customers import CustomerCreate, CustomerUpdate, _check_gstin

    with pytest.raises(ValueError):
        _check_gstin(gstin)
    with pytest.raises(ValidationError):
        CustomerUpdate(gstin=gstin)
    assert _check_gstin(GSTIN_BUYER) == GSTIN_BUYER
    assert CustomerCreate  # the create model shares _check_gstin


def test_tally_b2b_export_dates_the_voucher_on_the_invoice(monkeypatch):
    """The B2B export's voucher <DATE> is the tax invoice's own day, 02 July.
    REVERT CHECK: without o['created_at'] = _sale_filed_at(o) in
    _b2b_fetch_orders the voucher is dated 28 June."""
    import agents.nexus_providers as nexus

    tally = _tally(monkeypatch, _gst_db(_next_month_sale(customer_gstin=GSTIN_BUYER), {
        "customer_id": "C1", "name": "Acme", "gstin": GSTIN_BUYER, "state": "Jharkhand"}))
    [o] = tally._b2b_fetch_orders(tally._get_db(), ["O1"])
    assert nexus.ist_date_str_from_stored(o["created_at"]) == "2026-07-02"


def test_gst_registration_refuses_a_stray_sale_in_its_invoice_month(monkeypatch):
    """A sale booked to a shop that does not exist, taken 28 June and invoiced
    02 July, is July's stray (409), never June's.
    REVERT CHECK: the created_at window in gst_by_registration's stray count
    refuses June and passes July."""
    from api.routers.finance.gst_crosscheck import gst_by_registration

    cust = {"customer_id": "C1", "name": "Acme", "gstin": GSTIN_BUYER, "state": "Jharkhand"}
    db = _gst_db(_next_month_sale(store_id="S-GONE"), cust)
    db.stores.update_one({"store_id": "S1"}, {"$set": {"entity_id": "E1"}})
    monkeypatch.setattr(r, "_get_raw_db", lambda: db)
    gst_by_registration(db, 6, 2026, None)
    with pytest.raises(HTTPException) as exc:
        gst_by_registration(db, 7, 2026, None)
    assert exc.value.status_code == 409


def test_cross_check_adds_back_a_refunded_invoice_in_its_invoice_month(monkeypatch):
    """Taken 28 June, invoiced 02 July, advance refunded and cancelled behind
    its credit note in July: the books add it back in July, as GSTR-1 files
    it -- every row MATCH both months.
    REVERT CHECK: the created_at window in _cancelled_behind_credit_note adds
    it back in June (MISMATCH June and July)."""
    from api.routers.finance.gst_crosscheck import _run_gst_cross_check

    cust = {"customer_id": "C1", "name": "Acme", "gstin": GSTIN_BUYER, "state": "Jharkhand"}
    db = _gst_db(_next_month_sale(status="CANCELLED", advance_refund_status="COMPLETED",
                                  advance_refund_credit_note="BOK/C2627/0001",
                                  cancelled_at=datetime(2026, 7, 5, 6, 0)), cust)
    db.stores.update_one({"store_id": "S1"}, {"$set": {"entity_id": "E1"}})
    monkeypatch.setattr(r, "_get_raw_db", lambda: db)
    for m, added in ((6, 0), (7, 1)):
        out = _run_gst_cross_check(db, m, 2026, None)
        assert out["cancelled_behind_credit_note"]["count"] == added, m
        assert [c["metric"] for c in out["comparisons"] if c["status"] == "MISMATCH"] == [], m
