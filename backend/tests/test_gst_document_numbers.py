"""
GST document numbers fit 16 characters (owner / CA rulings 2026-10-18)
=====================================================================
ONE mint (order_repository.next_document_number) numbers every GST document a
shop issues, each from the shop's own series under its ONE shop code:

  tax invoice                    BOK02/2627/00001   (PR #1231)
  inter-GSTIN transfer invoice   BOK02/T2627/0001   series T
  debit note                     BOK02/D2627/0001   series D
  credit note                    BOK02/C2627/0001   series C

Revert-proof map (revert the named piece -> the named test goes red):
  * the lettered format -> test_each_series_is_16_chars
  * one counter per series -> test_series_are_independent
  * one code per shop across series -> test_series_code_is_one_per_shop
  * the DuplicateKeyError retry / its reason -> test_duplicate_key_*
  * ship mints the T number, across GSTINs only -> test_inter_gstin_ship_mints_*,
    test_ship_refused_when_the_sender_cannot_number_it,
    test_same_gstin_transfer_stays_on_a_challan_not_in_gstr1
  * the mirror bill carries it -> test_inter_gstin_transfer_in_gstr1_b2b,
    test_a_move_shipped_before_numbering_is_numbered_at_complete
  * the invoice frozen at ship -> test_reprint_and_books_keep_the_lines_frozen_at_ship,
    test_a_short_receipt_books_at_the_invoiced_rate
  * the printed date on the row -> test_month_boundary_row_carries_the_ship_date
  * GSTR-1 checks transfer numbers -> test_gstr1_flags_an_old_transfer_number
  * the short-receipt warning -> test_short_receipt_is_flagged_against_the_issued_invoice
  * the e-way flag -> test_eway_flag_boundary_is_strictly_over_50000
  * the credit-note doors -> test_store_credit_note_*,
    test_credit_note_number_comes_from_the_sale_shop
  * no truncation in CDNR -> test_cdnr_files_the_full_number
  Elsewhere: the RTV debit-note door in test_f20_rtv_debit_note.py; the admin
  C/D notes and the refused-revision audit row in test_superadmin_order_edit.py.

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest backend/tests/test_gst_document_numbers.py -q
"""

from __future__ import annotations

import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from pymongo.errors import DuplicateKeyError  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strict_fakes import StrictDB  # noqa: E402
from api.utils.ist import IST  # noqa: E402
from database.repositories.order_repository import (  # noqa: E402
    InvoiceNumberError,
    OrderRepository,
    gst_invoice_number_problem,
    next_document_number,
)

MAY_2026 = datetime(2026, 5, 1, 11, 0, tzinfo=IST)
LETTERED_RE = re.compile(r"^[A-Z][A-Z0-9]{4}/[TDC]\d{4}/\d{4}$")

STORES = [
    {"store_id": "BV-BOK-02", "store_code": "BV-BOK-02"},
    {"store_id": "BV-DHN-02", "store_code": "BV-DHN-02"},
    {"store_id": "pune-uuid", "store_code": "BV-PUN-01"},
]


def _db(stores=STORES):
    db = StrictDB()
    db.seed("stores", [dict(s) for s in stores])
    return db


# ---------------------------------------------------------------------------
# The shared mint: format, independence, one code per shop, FY, concurrency
# ---------------------------------------------------------------------------


def test_each_series_is_16_chars():
    db = _db()
    for series, want in (("T", "BOK02/T2627/0001"), ("D", "BOK02/D2627/0001"),
                         ("C", "BOK02/C2627/0001")):
        n = next_document_number(db, "BV-BOK-02", series, MAY_2026)
        assert n == want and len(n) == 16 and LETTERED_RE.match(n)
        assert gst_invoice_number_problem(n) is None


def test_series_are_independent():
    """Each series counts on its own: a transfer invoice never takes a tax
    invoice's serial, and the tax invoice keeps its 5-digit format."""
    db = _db()
    orders = db.get_collection("orders")
    orders.database = db
    repo = OrderRepository(orders)
    assert repo.next_invoice_number("BV-BOK-02", MAY_2026) == "BOK02/2627/00001"
    assert next_document_number(db, "BV-BOK-02", "T", MAY_2026) == "BOK02/T2627/0001"
    assert next_document_number(db, "BV-BOK-02", "T", MAY_2026) == "BOK02/T2627/0002"
    assert repo.next_invoice_number("BV-BOK-02", MAY_2026) == "BOK02/2627/00002"
    assert next_document_number(db, "BV-BOK-02", "C", MAY_2026) == "BOK02/C2627/0001"
    ids = sorted(d["_id"] for d in db.get_collection("counters").docs)
    assert ids == ["gstdoc-C:BOK02:2026", "gstdoc-T:BOK02:2026", "invoice:BOK02:2026"]


def test_series_code_is_one_per_shop():
    """The shop code belongs to one shop per FY across ALL its series: a code
    another shop used for transfer invoices stays theirs, and a shop that
    issued debit notes under one code cannot invoice under a second."""
    db = _db()
    assert next_document_number(db, "BV-BOK-02", "T", MAY_2026) == "BOK02/T2627/0001"
    # Bokaro moves to BKS02 behind the form's back; Pune takes BOK02.
    stores = db.get_collection("stores")
    stores.update_one({"store_id": "BV-BOK-02"}, {"$set": {"invoice_code": "BKS02"}})
    stores.update_one({"store_id": "pune-uuid"}, {"$set": {"invoice_code": "BOK02"}})
    with pytest.raises(InvoiceNumberError, match="already used this financial year by store BV-BOK-02"):
        next_document_number(db, "pune-uuid", "", MAY_2026)
    with pytest.raises(InvoiceNumberError, match="already issued invoices as BOK02"):
        next_document_number(db, "BV-BOK-02", "D", MAY_2026)


def test_fy_rollover_in_ist():
    db = _db()
    last = datetime(2027, 3, 31, 18, 29, 59, tzinfo=timezone.utc)  # 23:59:59 IST
    first = datetime(2027, 3, 31, 18, 30, tzinfo=timezone.utc)  # 00:00 IST 1 Apr
    assert next_document_number(db, "BV-BOK-02", "C", last) == "BOK02/C2627/0001"
    assert next_document_number(db, "BV-BOK-02", "C", first) == "BOK02/C2728/0001"
    assert next_document_number(db, "BV-BOK-02", "C", last) == "BOK02/C2627/0002"


def test_serial_10000_is_refused_not_truncated():
    db = _db()
    db.get_collection("counters").insert_one(
        {"_id": "gstdoc-D:BOK02:2026", "seq": 9998, "store_id": "BV-BOK-02", "fy": 2026}
    )
    assert next_document_number(db, "BV-BOK-02", "D", MAY_2026) == "BOK02/D2627/9999"
    with pytest.raises(InvoiceNumberError, match="17 characters"):
        next_document_number(db, "BV-BOK-02", "D", MAY_2026)


def test_unknown_series_is_a_programming_error():
    with pytest.raises(ValueError):
        next_document_number(_db(), "BV-BOK-02", "X", MAY_2026)


def test_concurrent_mints_one_number_each():
    """16 threads x 300 transfer-invoice mints: the serial's only source is
    the counter's atomic $inc (a lock here, as Mongo serialises one doc)."""
    db = _db()
    counters = db.get_collection("counters")
    lock = threading.Lock()
    raw = counters.find_one_and_update

    def atomic(*a, **k):
        with lock:
            return raw(*a, **k)

    counters.find_one_and_update = atomic
    with ThreadPoolExecutor(max_workers=16) as pool:
        nums = list(pool.map(
            lambda _i: next_document_number(db, "BV-BOK-02", "T", MAY_2026), range(300)
        ))
    assert len(set(nums)) == 300
    assert sorted(int(n[-4:]) for n in nums) == list(range(1, 301))


def test_db_less_mint_is_refused():
    with pytest.raises(InvoiceNumberError, match="counter unavailable"):
        next_document_number(None, "BV-BOK-02", "C", MAY_2026)


# ---------------------------------------------------------------------------
# #1231 review: a duplicate key on the counter upsert
# ---------------------------------------------------------------------------


def _race_once(counters, racer_doc):
    """The first claim loses a race: the racer's series doc lands, then the
    upsert raises DuplicateKeyError (what Mongo does when the filter's extra
    fields keep it from matching the doc another worker just inserted)."""
    raw = counters.find_one_and_update
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            counters.insert_one(dict(racer_doc))
            raise DuplicateKeyError("E11000 duplicate key error")
        flt = a[0] if a else k.get("filter")
        if racer_doc["store_id"] != flt.get("store_id"):
            raise DuplicateKeyError("E11000 duplicate key error")
        return raw(*a, **k)

    counters.find_one_and_update = flaky
    return calls


def test_duplicate_key_from_a_racing_mint_of_this_shop_is_retried():
    db = _db()
    counters = db.get_collection("counters")
    calls = _race_once(counters, {"_id": "gstdoc-T:BOK02:2026", "seq": 1,
                                  "store_id": "BV-BOK-02", "fy": 2026})
    # The racer took 0001; the retry $incs the same doc.
    assert next_document_number(db, "BV-BOK-02", "T", MAY_2026) == "BOK02/T2627/0002"
    assert calls["n"] == 2


def test_duplicate_key_from_another_shop_names_it():
    db = _db()
    counters = db.get_collection("counters")
    calls = _race_once(counters, {"_id": "invoice:BOK02:2026", "seq": 1,
                                  "store_id": "pune-uuid", "store_code": "BV-PUN-01",
                                  "fy": 2026})
    with pytest.raises(InvoiceNumberError,
                       match="already used this financial year by store BV-PUN-01"):
        next_document_number(db, "BV-BOK-02", "", MAY_2026)
    assert calls["n"] == 2  # one retry, then the real reason


# ===========================================================================
# 1. Inter-GSTIN stock transfer = a TAX INVOICE from the sender's T series
# ===========================================================================

mongomock = pytest.importorskip("mongomock")

GSTIN_Z_JH = "20AAACZ1111A1ZH"  # company Z, Jharkhand (both Dhanbad shops)
GSTIN_Z_MH = "27AAACZ1111A1Z3"  # company Z, Maharashtra (Pune)
GSTIN_Y_JH = "20AAACY2222B1ZA"  # company Y, Jharkhand (Bokaro)
UNIT_COST = 1850.0


def _shop(sid, code, name, entity, gstin, state, pin):
    return {
        "store_id": sid, "store_name": name, "name": name, "entity_id": entity,
        "gstin": gstin, "state": state, "state_code": gstin[:2], "address": f"{name} Road",
        "city": name.split()[-1], "pincode": pin, "store_type": "PHYSICAL",
        "invoice_code": code,
    }


SHOPS = [
    _shop("ST-DHN-1", "DHN01", "Hirapur Dhanbad", "ENT-Z", GSTIN_Z_JH, "Jharkhand", "826001"),
    _shop("ST-DHN-2", "DHN02", "Bank More Dhanbad", "ENT-Z", GSTIN_Z_JH, "Jharkhand", "826009"),
    _shop("ST-BOK-1", "BOK01", "Sector 4 Bokaro", "ENT-Y", GSTIN_Y_JH, "Jharkhand", "827004"),
    _shop("ST-PUN-1", "PUN01", "FC Road Pune", "ENT-Z", GSTIN_Z_MH, "Maharashtra", "411004"),
]
ADMIN = {"user_id": "u-admin", "username": "admin", "roles": ["ADMIN"],
         "store_ids": [], "active_store_id": None}
ACCOUNTS = {"user_id": "u-acc", "username": "acc", "roles": ["ACCOUNTANT"],
            "store_ids": ["ST-DHN-1"], "active_store_id": "ST-DHN-1"}


class _Conn:
    is_connected = True

    def __init__(self, db):
        self.db = db

    def __getattr__(self, name):
        return self.db[name]


@pytest.fixture
def world(monkeypatch):
    from api import dependencies as deps
    from api.routers import reports, transfers
    from database.repositories import order_repository as repo_module

    handle = mongomock.MongoClient()["ims_gstnum"]
    handle["entities"].insert_many([
        {"entity_id": "ENT-Z", "legal_name": "Zed Opticals Pvt Ltd", "gstins": [
            {"gstin": GSTIN_Z_JH, "state_code": "20", "is_primary": True},
            {"gstin": GSTIN_Z_MH, "state_code": "27"}]},
        {"entity_id": "ENT-Y", "legal_name": "Wye Vision LLP",
         "gstins": [{"gstin": GSTIN_Y_JH, "state_code": "20"}]},
    ])
    handle["stores"].insert_many([dict(s) for s in SHOPS])
    handle["products"].insert_one({
        "product_id": "P-1", "sku": "FR-1", "name": "Carrera Frame", "category": "FRAME",
        "hsn_code": "900311", "cost_price": 2000.0,
    })
    handle["stock_units"].insert_many([
        {"stock_id": f"SU-{n}", "product_id": "P-1", "store_id": "ST-DHN-1",
         "status": "AVAILABLE", "barcode": f"BVQX0{n}", "unit_cost": UNIT_COST}
        for n in range(1, 41)
    ])
    monkeypatch.setattr(deps, "get_db", lambda: _Conn(handle))
    monkeypatch.setattr(reports, "_get_raw_db", lambda: handle)
    monkeypatch.setattr(transfers, "_writeback_units_left", lambda *a, **k: None)
    # The FY of every number minted here (the books' dates stay real time).
    monkeypatch.setattr(repo_module, "now_ist", lambda: MAY_2026)
    transfers.STOCK_TRANSFERS.clear()
    return handle


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def _ship_to(to_store, qty=2, frm="ST-DHN-1"):
    from api.routers import transfers

    body = transfers.TransferInput(
        transfer_type="store_to_store",
        from_location_id=frm, from_location_name="Hirapur Dhanbad",
        to_location_id=to_store,
        to_location_name=next(s["store_name"] for s in SHOPS if s["store_id"] == to_store),
        items=[transfers.TransferItemInput(product_id="P-1", sku="FR-1",
                                           product_name="Carrera Frame",
                                           quantity_requested=qty)],
    )
    t = _run(transfers.create_transfer(body, ADMIN))["transfer"]
    return _run(transfers.ship_transfer(t["id"], None, None, None, False, ADMIN))["transfer"]


def _receive_complete(t, received=None):
    from api.routers import transfers

    line = transfers._get_transfer(t["id"])["items"][0]
    qty = line["quantity_shipped"] if received is None else received
    _run(transfers.receive_transfer(
        t["id"], [transfers.TransferItemReceive(transfer_item_id=line["id"],
                                                quantity_received=qty)], ADMIN))
    _run(transfers.complete_transfer(t["id"], None, ADMIN))


def _gstr1(world, store="ST-DHN-1"):
    from api.routers import reports

    bill = world["vendor_bills"].find_one({}) or {}
    month = str(bill.get("bill_date") or datetime.now(IST).isoformat())[:7]
    return reports._compute_gstr1(month, store)


def test_inter_gstin_ship_mints_the_senders_t_number(world):
    t = _ship_to("ST-BOK-1")
    assert t["tax_invoice_number"] == "DHN01/T2627/0001"
    assert len(t["tax_invoice_number"]) == 16
    stored = world["stock_transfers"].find_one({"id": t["id"]})
    assert stored["tax_invoice_number"] == "DHN01/T2627/0001" and stored["tax_invoice_date"]
    # The transfer keeps its own TRF- number; the tax invoice is separate.
    assert t["transfer_number"].startswith("TRF-")


def test_inter_gstin_ship_mints_once_on_retry_and_race(world):
    from api.routers import transfers

    t = _ship_to("ST-BOK-1")
    again = transfers._get_transfer(t["id"])
    assert transfers._transfer_tax_invoice(again) == "DHN01/T2627/0001"
    # Two presses racing on a transfer with no number yet: one number lands,
    # both answer it, the other serial is a gap.
    world["stock_transfers"].update_one(
        {"id": t["id"]}, {"$unset": {"tax_invoice_number": "", "tax_invoice_date": ""}}
    )
    copies = [transfers._get_transfer(t["id"]) for _ in range(2)]
    barrier = threading.Barrier(2, timeout=20)
    real = transfers.next_document_number

    def slow(*a, **k):
        n = real(*a, **k)
        barrier.wait()
        return n

    transfers.next_document_number = slow
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            got = list(pool.map(transfers._transfer_tax_invoice, copies))
    finally:
        transfers.next_document_number = real
    stored = world["stock_transfers"].find_one({"id": t["id"]})["tax_invoice_number"]
    assert got == [stored, stored]
    assert world["counters"].find_one({"_id": "gstdoc-T:DHN01:2026"})["seq"] == 3


def test_ship_refused_when_the_sender_cannot_number_it(world):
    world["stores"].update_one({"store_id": "ST-DHN-1"}, {"$set": {"invoice_code": "BOK01"}})
    with pytest.raises(Exception) as exc:
        _ship_to("ST-BOK-1")
    assert getattr(exc.value, "status_code", None) == 400
    assert "tax invoice" in exc.value.detail and "BOK01" in exc.value.detail
    assert world["stock_units"].count_documents({"status": "AVAILABLE"}) == 40


def test_same_gstin_transfer_stays_on_a_challan_not_in_gstr1(world):
    from api.routers import print_documents

    t = _ship_to("ST-DHN-2")
    assert "tax_invoice_number" not in t
    _receive_complete(t)
    assert world["vendor_bills"].count_documents({}) == 0
    assert world["counters"].count_documents({"_id": {"$regex": "^(invoice|gstdoc)"}}) == 0, list(world["counters"].find())
    rep = _gstr1(world)
    assert rep["b2b"] == [] and rep["totalTax"] == 0.0
    html = _run(print_documents.delivery_challan_for_transfer(
        t["id"], "ORIGINAL", False, ACCOUNTS)).body.decode()
    assert "Delivery Challan" in html and t["transfer_number"] in html
    assert "<h2>Tax Invoice</h2>" not in html and "Not a Tax Invoice" in html
    with pytest.raises(Exception) as exc:
        _run(print_documents.tax_invoice_for_transfer(t["id"], "ORIGINAL", False, ACCOUNTS))
    assert exc.value.status_code == 409


@pytest.mark.parametrize("to_store,heads", [
    ("ST-BOK-1", (92.5, 92.5, 0.0)),   # two companies, one state: CGST + SGST
    ("ST-PUN-1", (0.0, 0.0, 185.0)),   # one company, two states: IGST
])
def test_inter_gstin_transfer_in_gstr1_b2b(world, to_store, heads):
    t = _ship_to(to_store)
    _receive_complete(t)
    (bill,) = world["vendor_bills"].find({"source_transfer_id": t["id"]})
    assert bill["invoice_number"] == bill["bill_number"] == "DHN01/T2627/0001"
    rep = _gstr1(world)
    (row,) = rep["b2b"]
    assert row["invoiceNumber"] == "DHN01/T2627/0001"
    assert row["customerGSTIN"] == bill["recipient_gstin"]
    assert row["taxableValue"] == 3700.0
    assert (row["cgst"], row["sgst"], row["igst"]) == heads
    # The date printed on the tax invoice (an IST wall-clock stamp).
    assert len(row["invoiceDate"]) == 10
    assert bill["tax_invoice_date"].startswith(row["invoiceDate"])
    assert not any(i.get("issue_code") == "INVOICE_SERIAL_OVER_16"
                   for i in rep["validation"]["issues"])
    # The portal JSON files the same number.
    from api.services.gstn_export import to_gstr1_json

    out = to_gstr1_json(rep, gstin=GSTIN_Z_JH, period="2026-10")
    assert [inv["inum"] for b in out["b2b"] for inv in b["inv"]] == ["DHN01/T2627/0001"]
    # The receiver's inward copy: the same doc, booked to the receiving shop.
    assert (bill["to_store_id"], bill["store_id"], bill["recipient_entity_id"]) == (
        to_store, to_store, "ENT-Y" if to_store == "ST-BOK-1" else "ENT-Z")


def test_short_receipt_is_flagged_against_the_issued_invoice(world):
    t = _ship_to("ST-BOK-1")
    _receive_complete(t, received=1)
    rep = _gstr1(world)
    assert rep["b2b"][0]["taxableValue"] == UNIT_COST
    issues = [i["issue"] for i in rep["validation"]["issues"]]
    assert (
        "IMS files the RECEIVED value Rs 1850.00 (taxable) under DHN01/T2627/0001; "
        "the printed invoice and e-way bill show the SHIPPED value Rs 3700.00. "
        "Ask the CA whether to amend."
    ) in issues, issues


def test_a_move_shipped_before_numbering_is_numbered_at_complete(world):
    t = _ship_to("ST-BOK-1")
    world["stock_transfers"].update_one(
        {"id": t["id"]}, {"$unset": {"tax_invoice_number": "", "tax_invoice_date": ""}}
    )
    world["counters"].delete_many({})
    _receive_complete(t)
    (bill,) = world["vendor_bills"].find({})
    assert bill["invoice_number"] == "DHN01/T2627/0001"
    stored = world["stock_transfers"].find_one({"id": t["id"]})
    assert stored["tax_invoice_number"] == "DHN01/T2627/0001"


def test_reprint_and_books_keep_the_lines_frozen_at_ship(world, monkeypatch):
    """The invoice behind a number is the one issued at ship: an HSN or rate
    edited afterwards changes neither its reprint nor the books."""
    from api.routers import print_documents
    from api.services import gst_rates

    t = _ship_to("ST-PUN-1")
    (line,) = world["stock_transfers"].find_one({"id": t["id"]})["tax_invoice_lines"]
    assert (line["hsn"], line["gst_rate"], line["taxable"], line["igst"]) == (
        "900311", 5, 3700.0, 185.0)
    # After ship: the rate table now says 18% and the product a new HSN.
    monkeypatch.setattr(gst_rates, "resolve_gst_rate", lambda **_k: 18.0)
    world["products"].update_one({"product_id": "P-1"}, {"$set": {"hsn_code": "900410"}})
    # ...and the line's stamped HSN and cost are edited behind the screen.
    world["stock_transfers"].update_one(
        {"id": t["id"]}, {"$set": {"items.0.hsn_code": "", "items.0.unit_cost": 1900.0}}
    )
    html = _run(print_documents.tax_invoice_for_transfer(
        t["id"], "ORIGINAL", False, ACCOUNTS)).body.decode()
    assert "3,885.00" in html  # 3,700 + 5%: never 3,990 (1,900 a unit) or 4,366 (18%)
    assert "3,990.00" not in html and "4,366.00" not in html
    assert "<td>900311</td>" in html and "900410" not in html
    _receive_complete(t)
    (bill,) = world["vendor_bills"].find({})
    assert (bill["igst_total"], bill["lines"][0]["gst_rate"], bill["lines"][0]["hsn"]) == (
        185.0, 5, "900311")


def test_a_short_receipt_books_at_the_invoiced_rate(world, monkeypatch):
    """Two shipped at 5%, the rate table then says 18%, one arrives: the bill
    books the one received at the invoice's 5% (IGST 92.50), never 18%."""
    from api.services import gst_rates

    t = _ship_to("ST-PUN-1")
    monkeypatch.setattr(gst_rates, "resolve_gst_rate", lambda **_k: 18.0)
    _receive_complete(t, received=1)
    (bill,) = world["vendor_bills"].find({})
    assert (bill["taxable_amount"], bill["igst_total"], bill["lines"][0]["gst_rate"]) == (
        UNIT_COST, 92.5, 5)


def _ship_and_complete_across_month_end(monkeypatch):
    """Ship 30 Sep 23:30 IST (18:00 UTC), complete 2 Oct."""
    from api.routers import transfers

    monkeypatch.setattr(transfers, "utc_now_iso", lambda: "2026-09-30T18:00:00+00:00")
    t = _ship_to("ST-PUN-1")
    monkeypatch.setattr(transfers, "utc_now_iso", lambda: "2026-10-02T06:00:00+00:00")
    _receive_complete(t)
    return t


def test_month_boundary_row_carries_the_ship_date(world, monkeypatch):
    from api.routers import reports

    _ship_and_complete_across_month_end(monkeypatch)
    (row,) = reports._compute_gstr1("2026-10", "ST-DHN-1")["b2b"]
    assert (row["invoiceNumber"], row["invoiceDate"]) == ("DHN01/T2627/0001", "2026-09-30")


@pytest.mark.xfail(
    strict=True,
    reason="known gap: a transfer lands on the GSTR-1 (and 3B) of the month it is "
    "COMPLETED -- the mirror bill's bill_date -- not the month its tax invoice was "
    "issued; the sender's outward row at ship is the follow-up after #1238",
)
def test_month_boundary_row_files_in_the_ship_month(world, monkeypatch):
    from api.routers import reports

    _ship_and_complete_across_month_end(monkeypatch)
    sep = reports._compute_gstr1("2026-09", "ST-DHN-1")["b2b"]
    assert [r["invoiceNumber"] for r in sep] == ["DHN01/T2627/0001"]


def test_gstr1_flags_an_old_transfer_number(world):
    """A mirror bill booked before T numbers (TRF/TRF-YYYYMM-NNNN, 19 chars)
    is filed verbatim and FLAGGED, never passed unchecked."""
    t = _ship_to("ST-BOK-1")
    _receive_complete(t)
    world["vendor_bills"].update_one({}, {"$set": {"invoice_number": "TRF/TRF-202610-1001",
                                                   "bill_number": "TRF/TRF-202610-1001"}})
    rep = _gstr1(world)
    assert rep["b2b"][0]["invoiceNumber"] == "TRF/TRF-202610-1001"
    (issue,) = [i for i in rep["validation"]["issues"]
                if i.get("issue_code") == "INVOICE_SERIAL_OVER_16"]
    assert "19 characters" in issue["issue"]


def test_transfer_tax_invoice_prints_with_eway_data(world):
    from api.routers import print_documents

    t = _ship_to("ST-PUN-1")
    html = _run(print_documents.tax_invoice_for_transfer(
        t["id"], "ORIGINAL", False, ACCOUNTS)).body.decode()
    assert "<h2>Tax Invoice</h2>" in html and "DHN01/T2627/0001" in html
    assert GSTIN_Z_JH in html and GSTIN_Z_MH in html and "411004" in html
    assert "<td>900311</td>" in html and "BVQX01" in html
    assert "E-way Bill Data" in html and "Vehicle No." in html
    assert "E-way bill REQUIRED" not in html  # Rs 3,885 consignment


def test_eway_bill_flag_over_rs_50000(world):
    from api.routers import print_documents

    t = _ship_to("ST-PUN-1", qty=26)  # 26 x 1850 = 48,100 + 5% IGST = 50,505
    html = _run(print_documents.tax_invoice_for_transfer(
        t["id"], "ORIGINAL", False, ACCOUNTS)).body.decode()
    assert "E-way bill REQUIRED" in html and "50,505.00" in html


def test_eway_flag_boundary_is_strictly_over_50000():
    from api.services.print_render import render_transfer_tax_invoice

    def page(total):
        return render_transfer_tax_invoice(
            entity={}, store={}, invoice_number="DHN01/T2627/0001", invoice_date=MAY_2026,
            supplier_name="Z", supplier_address="", supplier_gstin=GSTIN_Z_JH,
            recipient_name="Y", recipient_address="", recipient_gstin=GSTIN_Y_JH,
            lines=[{"description": "Frame", "hsn": "900311", "qty": 1, "unit_price": total,
                    "taxable": total, "gst_rate": 0, "line_total": total}],
        )

    assert "E-way bill REQUIRED" not in page(50000.0)
    assert "E-way bill REQUIRED" in page(50000.01)


# ===========================================================================
# 2. Rule 53: debit notes (D) and credit notes (C), filed in full
# ===========================================================================

from test_returns import ctx, _payload, _staff_token  # noqa: E402,F401  (fixture)

NOTE_C = re.compile(r"^PUN01/C\d{4}/(\d{4})$")


def _return(ctx, **over):
    tok = _staff_token(["STORE_MANAGER"])
    payload = _payload(**over)
    return ctx["client"].post("/api/v1/returns", json=payload,
                              headers={"Authorization": f"Bearer {tok}"})


def test_store_credit_note_numbered_once_per_return_whatever_the_tender(ctx):
    """A return that reverses tax is a credit note numbered from the SALE
    shop's C series -- a store-credit refund (its ledger row carries the same
    number) and a cash refund (no ledger row; GSTR-1 files the return) alike."""
    credit = _return(ctx, return_type="CREDIT_NOTE", customer_id="CUST-1")
    assert credit.status_code == 201, credit.text
    first = credit.json()["credit_note_number"]
    assert NOTE_C.match(first).group(1) == "0001" and len(first) == 16
    (row,) = ctx["ledger_coll"].docs
    assert row["note_number"] == first
    assert ctx["returns_coll"].docs[0]["credit_note_number"] == first
    cash = _return(ctx)  # RETURN to the order's UPI
    assert cash.status_code == 201, cash.text
    assert NOTE_C.match(cash.json()["credit_note_number"]).group(1) == "0002"
    assert len(ctx["ledger_coll"].docs) == 1  # a cash refund writes no ledger row


def test_store_credit_note_refused_return_records_nothing(ctx):
    ctx["numbering"].get_collection("stores").update_one(
        {"store_id": "BV-PUN-01"}, {"$set": {"invoice_code": "BAD"}}
    )
    r = _return(ctx, return_type="CREDIT_NOTE", customer_id="CUST-1")
    assert r.status_code == 400
    assert "credit note cannot be numbered" in r.json()["detail"]
    assert ctx["returns_coll"].docs == [] and ctx["ledger_coll"].docs == []
    # The order line's returnable quantity, claimed before the number was
    # asked for, is released: nothing stays booked as returned.
    (line,) = ctx["order_repo"].collection.docs[0]["items"]
    assert line["returned_qty"] == 0
    # The quantity claim was released: the same return goes through once fixed.
    ctx["numbering"].get_collection("stores").update_one(
        {"store_id": "BV-PUN-01"}, {"$set": {"invoice_code": None}}
    )
    assert _return(ctx, return_type="CREDIT_NOTE", customer_id="CUST-1").status_code == 201


def test_credit_note_number_comes_from_the_sale_shop(ctx):
    """A sale of Pune's returned at Dhanbad's counter: the credit note reverses
    Pune's invoice, so it takes PUN01's C series (the real mint), never the
    processing counter's."""
    ctx["numbering"].get_collection("stores").insert_one(
        {"store_id": "BV-DHN-02", "store_code": "BV-DHN-02"}
    )
    tok = _staff_token(["ADMIN"], store_id="BV-DHN-02")
    r = ctx["client"].post(
        "/api/v1/returns",
        # The return names the sale's shop (the door refuses any other); the
        # counter it is processed at is the cashier's, Dhanbad.
        json=_payload(return_type="CREDIT_NOTE", customer_id="CUST-1"),
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 201, r.text
    number = r.json()["credit_note_number"]
    assert NOTE_C.match(number), number
    (row,) = ctx["ledger_coll"].docs
    assert (row["store_id"], row["note_number"]) == ("BV-PUN-01", number)
    ids = [d["_id"] for d in ctx["numbering"].get_collection("counters").docs]
    assert ids == ["gstdoc-C:PUN01:" + ids[0].rsplit(":", 1)[1]]


def test_store_credit_note_door_numbers_an_unnumbered_note(ctx):
    """The online refund door passes no number: the store-credit issuer
    numbers the note from the BOOKED store's series, or refuses it."""
    from api.routers import returns as returns_router

    user = {"user_id": "SYSTEM", "active_store_id": "BV-PUN-01"}
    entry = returns_router._issue_store_credit("CUST-1", 100.0, reason="Shopify refund",
                                               ref="RET-X", current_user=user)
    assert NOTE_C.match(entry["note_number"])
    kept = returns_router._issue_store_credit("CUST-1", 50.0, reason="r", ref="RET-Y",
                                              current_user=user, note_number="PUN01/C2627/0042")
    assert kept["note_number"] == "PUN01/C2627/0042"
    nowhere = returns_router._issue_store_credit("CUST-1", 50.0, reason="r", ref="RET-Z",
                                                 current_user={"active_store_id": "NO-SUCH"})
    assert nowhere is None
    assert [d["ref"] for d in ctx["ledger_coll"].docs] == ["RET-X", "RET-Y"]


def test_cdnr_files_the_full_number(world):
    """GSTR-1 CDNR (screen + portal JSON) files the note's own 16-character
    number -- the ledger pass and the cash-refund returns pass alike -- and a
    note issued before numbering verbatim and flagged, never cut to 16."""
    from api.routers import reports
    from api.services.gstn_export import to_gstr1_json

    when = datetime(2026, 10, 5, 6, 0)  # naive UTC, 11:30 IST
    world["customers"].insert_one({"customer_id": "C-B2B", "name": "Opti Traders",
                                   "gstin": GSTIN_Y_JH, "state": "Jharkhand"})
    world["credit_note_ledger"].insert_many([
        {"entry_id": "e1", "type": "ISSUED", "store_id": "ST-DHN-1", "customer_id": "C-B2B",
         "ref": "RET-261005-AAAAAA", "note_number": "DHN01/C2627/0001",
         "created_at": when.isoformat(), "amount": 1050.0, "net_refund": 1050.0,
         "taxable": 1000.0, "tax": 50.0, "gst_rate": 5},
        {"entry_id": "e2", "type": "ISSUED", "store_id": "ST-DHN-1", "customer_id": "C-B2B",
         "ref": "RET-261005-BBBBBB", "created_at": when.isoformat(), "amount": 105.0,
         "net_refund": 105.0, "taxable": 100.0, "tax": 5.0, "gst_rate": 5},
    ])
    world["returns"].insert_one({
        "return_id": "RET-261005-CCCCCC", "store_id": "ST-DHN-1", "status": "COMPLETED",
        "customer_id": "C-B2B", "credit_note_number": "DHN01/C2627/0002",
        "created_at": when, "gst_breakup": {"gross": 210.0, "taxable": 200.0, "tax": 10.0,
                                            "gst_rate": 5},
    })
    rep = reports._compute_gstr1("2026-10", "ST-DHN-1")
    refs = sorted(r["refReference"] for r in rep["cdnr"])
    assert refs == ["DHN01/C2627/0001", "DHN01/C2627/0002", "RET-261005-BBBBBB"]
    (issue,) = [i for i in rep["validation"]["issues"]
                if i.get("issue_code") == "INVOICE_SERIAL_OVER_16"]
    assert issue["count"] == 1 and "17 characters" in issue["issue"]
    out = to_gstr1_json(rep, gstin=GSTIN_Z_JH, period="2026-10")
    nums = sorted(n["nt_num"] for c in out["cdnr"] for n in c["nt"])
    assert nums == refs
