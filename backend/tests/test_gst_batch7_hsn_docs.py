"""
GST batch 7 (audit 2026-10-22; CA answers 2026-10-23)
=====================================================
(a) GSTR-1 Table 12: the HSN summary in TWO parts, B2B and B2C, each row per
    HSN per rate with its UQC and total quantity (portal hsn_b2b / hsn_b2c).
(b) Table 13 (documents issued): per series run from-to, total, cancelled,
    from the document register every minted number is logged in.
(c) Two concurrent transfers never share a challan number (atomic $inc, not
    count_documents()+1).
(d) Each shop numbers its delivery challans in its OWN series (TRF-{code}-),
    never one chain-wide series.

Revert map: (a) -> test_hsn_summary_split_b2b_b2c_with_quantity;
(b) -> test_table_13_*; (c) -> test_concurrent_transfers_get_distinct_numbers;
(d) -> test_each_shop_owns_its_challan_series.
"""

from __future__ import annotations

import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import api.routers.reports as r  # noqa: E402
from api.routers import transfers  # noqa: E402
from api.services import gstn_export  # noqa: E402
from api.utils.ist import IST  # noqa: E402
from database.repositories import order_repository as repo  # noqa: E402
from strict_fakes import StrictDB  # noqa: E402

WHEN = datetime(2026, 6, 15, 6, 0)  # naive UTC, mid-June 2026
MONTH = "2026-06"
GSTIN = "20AAACR0000A1ZZ"
SHOPS = [
    {"store_id": "S1", "store_code": "BV-BOK-02", "gstin": GSTIN, "store_name": "Bokaro",
     "state": "Jharkhand"},
    {"store_id": "S2", "store_code": "BV-DHN-02", "gstin": GSTIN, "store_name": "Dhanbad",
     "state": "Jharkhand"},
]


def _db(**seed) -> StrictDB:
    db = StrictDB()
    db.seed("stores", [dict(s) for s in SHOPS])
    for name, docs in seed.items():
        db.seed(name, docs)
    return db


@pytest.fixture
def june(monkeypatch):
    """Every number is minted (and logged) in June 2026."""
    monkeypatch.setattr(repo, "utc_now", lambda: WHEN.replace(tzinfo=timezone.utc))
    monkeypatch.setattr(repo, "now_ist", lambda: WHEN.replace(tzinfo=timezone.utc).astimezone(IST))


def _use(monkeypatch, db):
    monkeypatch.setattr(r, "_get_raw_db", lambda: db)
    monkeypatch.setattr(transfers, "_get_db", lambda: db)


def _line(rate, taxable, tax, hsn, qty):
    return {"gst_rate": rate, "taxable_value": taxable, "tax_amount": tax, "hsn_code": hsn,
            "item_total": taxable + tax, "quantity": qty}


def _order(oid, items, customer, status="COMPLETED", number=None):
    taxable = round(sum(i["taxable_value"] for i in items), 2)
    tax = round(sum(i["tax_amount"] for i in items), 2)
    return {"order_id": oid, "order_number": oid, "invoice_number": number or f"BV/26/{oid}",
            "store_id": "S1", "status": status, "created_at": WHEN,
            "grand_total": round(taxable + tax, 2), "tax_amount": tax,
            "customer_id": customer, "items": items}


# ---------------------------------------------------------------------------
# (a) Table 12: B2B / B2C apart, with quantity
# ---------------------------------------------------------------------------


def test_hsn_summary_split_b2b_b2c_with_quantity(monkeypatch):
    """The same frame HSN sold 2 pieces to a GSTIN buyer and 3 to a walk-in:
    Table 12 files a B2B row (qty 2) and a B2C row (qty 3), never one row with
    no quantity. REVERT CHECK: before, the export had one `data` list, qty 0,
    and no B2B / B2C split."""
    db = _db(
        customers=[{"customer_id": "B", "name": "Acme", "gstin": "20AAACA1111A1ZZ",
                    "state": "Jharkhand"},
                   {"customer_id": "W", "name": "Walk-in", "state": "Jharkhand"}],
        orders=[_order("O1", [_line(5, 2000.0, 100.0, "900311", 2),
                              _line(18, 1000.0, 180.0, "900410", 1)], "B"),
                _order("O2", [_line(5, 3000.0, 150.0, "900311", 3)], "W")],
    )
    _use(monkeypatch, db)
    rep = r._compute_gstr1(MONTH, "S1")
    b2b = {(h["hsnCode"], h["gstRate"]): h for h in rep["hsnB2B"]}
    b2c = {(h["hsnCode"], h["gstRate"]): h for h in rep["hsnB2C"]}
    assert set(b2b) == {("900311", 5), ("900410", 18)} and set(b2c) == {("900311", 5)}
    assert (b2b[("900311", 5)]["quantity"], b2b[("900311", 5)]["taxableValue"]) == (2, 2000.0)
    assert (b2b[("900410", 18)]["quantity"], b2b[("900410", 18)]["cgst"]) == (1, 90.0)
    assert (b2c[("900311", 5)]["quantity"], b2c[("900311", 5)]["taxableValue"]) == (3, 3000.0)
    # The combined screen view still adds both parts.
    combined = {(h["hsnCode"], h["gstRate"]): h for h in rep["hsnSummary"]}
    assert combined[("900311", 5)]["quantity"] == 5

    hsn = gstn_export.to_gstr1_json(rep, gstin=GSTIN, period=MONTH)["hsn"]
    assert sorted((x["hsn_sc"], x["rt"], x["uqc"], x["qty"]) for x in hsn["hsn_b2b"]) == [
        ("900311", 5.0, "PCS", 2.0), ("900410", 18.0, "PCS", 1.0)]
    assert [(x["hsn_sc"], x["qty"], x["txval"]) for x in hsn["hsn_b2c"]] == [("900311", 3.0, 3000.0)]


# ---------------------------------------------------------------------------
# (b) Table 13: documents issued, from the register
# ---------------------------------------------------------------------------


def test_table_13_counts_issued_cancelled_and_unused_numbers(monkeypatch, june):
    """Three invoice numbers minted: one billed, one on a cancelled order, one
    whose order was never written -- 1 to 3, total 3, cancelled 2. A credit
    note files under Credit Note. Another shop's numbers stay off this
    return. REVERT CHECK: before, GSTR-1 had no Table 13 at all and the mint
    kept no register."""
    db = _db()
    _use(monkeypatch, db)
    n1, n2, n3 = (repo.next_document_number(db, "S1", "") for _ in range(3))
    cn = repo.next_document_number(db, "S1", "C")
    repo.next_document_number(db, "S2", "")  # Dhanbad's own series
    w = {"customer_id": "W", "name": "Walk-in", "state": "Jharkhand"}
    db.seed("customers", [w])
    db.seed("orders", [_order("O1", [_line(5, 100.0, 5.0, "9004", 1)], "W", number=n1),
                       _order("O2", [_line(5, 100.0, 5.0, "9004", 1)], "W", "CANCELLED", n2)])
    docs = r._compute_gstr1(MONTH, "S1")["docsIssued"]
    assert docs == [
        {"docNum": 1, "docType": "Invoices for outward supply", "from": n1, "to": n3,
         "total": 3, "cancelled": 2},
        {"docNum": 5, "docType": "Credit Note", "from": cn, "to": cn, "total": 1, "cancelled": 0},
    ]
    assert (n1, n3) == ("BOK02/2627/00001", "BOK02/2627/00003")
    det = gstn_export.to_gstr1_json({"docsIssued": docs})["doc_issue"]["doc_det"]
    assert det[0]["docs"][0]["net_issue"] == 1


def test_table_13_delivery_challans_and_transfer_invoices(monkeypatch, june):
    """Challans: a move inside the GSTIN (issued), one that went on a tax
    invoice (not a challan; its T number is an invoice), and one cancelled
    before it shipped (no challan was issued)."""
    db = _db()
    _use(monkeypatch, db)
    monkeypatch.setattr(transfers, "_transfers_coll", lambda: db.get_collection("stock_transfers"))
    c1, c2, c3 = (transfers.generate_transfer_number("S1") for _ in range(3))
    t_inv = repo.next_document_number(db, "S1", "T")
    db.seed("stock_transfers", [
        {"id": "t1", "transfer_number": c1, "status": "in_transit", "from_location_id": "S1",
         "shipped_at": "2026-06-15T06:00:00+00:00"},
        {"id": "t2", "transfer_number": c2, "status": "completed", "tax_invoice_number": t_inv},
        {"id": "t3", "transfer_number": c3, "status": "cancelled"},
    ])
    docs = r._compute_gstr1(MONTH, "S1")["docsIssued"]
    assert [(d["docNum"], d["from"], d["to"], d["total"], d["cancelled"]) for d in docs] == [
        (1, t_inv, t_inv, 1, 0),
        (12, c1, c1, 1, 0),
    ]


def test_a_register_write_failure_refuses_the_number(monkeypatch):
    db = _db()
    log = db.get_collection(repo.DOCUMENT_LOG)

    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(log, "insert_one", boom)
    with pytest.raises(repo.InvoiceNumberError, match="register"):
        repo.next_document_number(db, "S1", "C")


# ---------------------------------------------------------------------------
# (c) + (d) the delivery-challan (TRF) number
# ---------------------------------------------------------------------------


def test_concurrent_transfers_get_distinct_numbers(monkeypatch):
    """16 threads x 200 transfers from one shop: 200 distinct, consecutive
    numbers. REVERT CHECK: count_documents()+1 gave every one the same."""
    db = _db()
    _use(monkeypatch, db)
    monkeypatch.setattr(transfers, "_transfers_coll", lambda: db.get_collection("stock_transfers"))
    counters = db.get_collection("counters")
    lock = threading.Lock()
    raw = counters.find_one_and_update

    def atomic(*a, **k):
        with lock:
            return raw(*a, **k)

    monkeypatch.setattr(counters, "find_one_and_update", atomic)
    with ThreadPoolExecutor(max_workers=16) as pool:
        nums = list(pool.map(lambda _i: transfers.generate_transfer_number("S1"), range(200)))
    assert len(set(nums)) == 200
    assert sorted(nums) == [f"TRF-BOK02-{i:05d}" for i in range(1, 201)]


def test_each_shop_owns_its_challan_series(monkeypatch):
    """Bokaro and Dhanbad each number from 1 in their own series (<= 16
    characters, Rule 55); a shop with no code uses the shared X series."""
    db = _db()
    _use(monkeypatch, db)
    monkeypatch.setattr(transfers, "_transfers_coll", lambda: db.get_collection("stock_transfers"))
    got = [transfers.generate_transfer_number(s) for s in ("S1", "S2", "S1", "NOPE")]
    assert got == ["TRF-BOK02-00001", "TRF-DHN02-00001", "TRF-BOK02-00002", "TRF-X-00001"]
    assert all(len(n) <= 16 for n in got)
    logged = [(d["number"], d["series"], d["store_id"]) for d in db.get_collection(repo.DOCUMENT_LOG).docs]
    assert ("TRF-DHN02-00001", "TRF", "S2") in logged


# ---------------------------------------------------------------------------
# Review round 1 (2026-11-03)
# ---------------------------------------------------------------------------

from api.routers.reports import gstr1 as g1  # noqa: E402

B = {"customer_id": "B", "name": "Acme", "gstin": "20AAACA1111A1ZZ", "state": "Jharkhand"}
W = {"customer_id": "W", "name": "Walk-in", "state": "Jharkhand"}


def _frame(qty=3, item_id="L1"):
    return {**_line(5, 1000.0 * qty, 50.0 * qty, "900311", qty), "item_id": item_id,
            "product_id": "P-" + item_id}


def _return(rid, oid, customer, qty=1, item_id="L1"):
    return {"return_id": rid, "order_id": oid, "store_id": "S1", "status": "COMPLETED",
            "customer_id": customer, "created_at": WHEN, "credit_note_number": "",
            "gst_breakup": {"gross": 1050.0 * qty, "taxable": 1000.0 * qty, "tax": 50.0 * qty,
                            "gst_rate": 5, "by_rate": {"5": {"taxable": 1000.0 * qty}}},
            "items": [{"order_item_id": item_id, "product_id": "P-" + item_id,
                       "return_qty": qty, "gst_rate": 5}]}


def test_table_12_services_file_uqc_na_and_no_quantity(monkeypatch):
    """SAC 99xx (a fitting 998599 @18%, an eye test 9993 @0%) files UQC NA and
    quantity 0; goods keep PCS and their pieces. REVERT CHECK: every row was
    PCS with the summed line quantity."""
    db = _db(customers=[W], orders=[_order("O1", [
        _line(18, 400.0, 72.0, "998599", 4), _line(0, 300.0, 0.0, "9993", 1),
        _line(5, 1000.0, 50.0, "900311", 1)], "W")])
    _use(monkeypatch, db)
    rep = r._compute_gstr1(MONTH, "S1")
    got = {h["hsnCode"]: (h["uqc"], h["quantity"]) for h in rep["hsnB2C"]}
    assert got == {"998599": ("NA", 0), "9993": ("NA", 0), "900311": ("PCS", 1)}
    hsn = gstn_export.to_gstr1_json(rep, gstin=GSTIN, period=MONTH)["hsn"]["hsn_b2c"]
    assert ("998599", "NA", 0.0) in {(x["hsn_sc"], x["uqc"], x["qty"]) for x in hsn}


def test_credit_notes_take_pieces_off_their_own_table_12_part(monkeypatch):
    """3 frames sold to a GSTIN buyer and 3 to a walk-in; 1 of each comes back
    under a credit note (the buyer's via the store-credit ledger, the walk-in's
    as an in-store return). Each part nets to 2 pieces / Rs 2,000: the
    registered note reduces hsnB2B, the walk-in's hsnB2C. REVERT CHECK: notes
    carried no qty (3 filed against the taxable of 2), and a note tagged B2C
    always left B2B at 3000 and B2C at 1000."""
    db = _db(
        customers=[B, W],
        orders=[_order("O1", [_frame()], "B"), _order("O2", [_frame()], "W")],
        returns=[_return("RET-1", "O1", "B"), _return("RET-2", "O2", "W")],
        credit_note_ledger=[{"store_id": "S1", "type": "ISSUED", "ref": "RET-1",
                             "customer_id": "B", "created_at": WHEN.isoformat(),
                             "taxable": 1000.0, "tax": 50.0, "gst_rate": 5,
                             "gross_refund": 1050.0, "net_refund": 1050.0}],
    )
    _use(monkeypatch, db)
    rep = r._compute_gstr1(MONTH, "S1")
    assert len(rep["cdnr"]) == 2
    for part in ("hsnB2B", "hsnB2C"):
        (row,) = rep[part]
        assert (row["hsnCode"], row["quantity"], row["taxableValue"]) == ("900311", 2, 2000.0), part


def test_transfer_deemed_supply_and_its_credit_note_file_in_b2b(monkeypatch):
    """An MH->JH transfer tax invoice (3 frames at cost) files in hsnB2B with
    its quantity, never hsnB2C; its short-delivery credit note (1 lost) takes
    the piece and value off. REVERT CHECK: an untagged transfer line went to
    hsnB2C; a qty-0 transfer line filed value with no pieces."""
    to = "27AAACR0000A1ZZ"
    bills = [
        {"source_transfer_id": "t1", "from_store_id": "S1", "recipient_gstin": to,
         "invoice_number": "BOK02/T/2627/00001", "invoice_date": "2026-06-15",
         "taxable_amount": 3000.0, "igst_total": 150.0,
         "lines": [{"hsn": "900311", "gst_rate": 5, "qty": 3, "taxable": 3000.0, "igst": 150.0}]},
        {"doc_type": "TRANSFER_CREDIT_NOTE", "source_transfer_id": "t1", "from_store_id": "S1",
         "recipient_gstin": to, "invoice_number": "BOK02/C/2627/00001",
         "invoice_date": "2026-06-20", "taxable_amount": -1000.0, "igst_total": -50.0,
         "lines": [{"hsn": "900311", "gst_rate": 5, "qty": 1, "taxable": -1000.0, "igst": -50.0}]},
    ]
    db = _db()
    _use(monkeypatch, db)
    monkeypatch.setattr(g1, "_transfer_outward_bills", lambda *a, **k: bills)
    rep = r._compute_gstr1(MONTH, "S1")
    assert rep["hsnB2C"] == []
    (row,) = rep["hsnB2B"]
    assert (row["hsnCode"], row["quantity"], row["taxableValue"], row["igst"]) == (
        "900311", 2, 2000.0, 100.0)


def _trf(db, number, status, shipped=None, store="S1", **extra):
    db.get_collection("stock_transfers").insert_one(
        {"id": number, "transfer_number": number, "status": status, "from_location_id": store,
         "shipped_at": shipped.replace(tzinfo=timezone.utc).isoformat() if shipped else None,
         **extra})


def test_table_13_counts_a_challan_only_once_its_goods_left(monkeypatch, june):
    """A transfer request never shipped (pending, approved) is no challan; a
    move across GSTINs is a tax invoice whether or not its T number is out yet;
    a challan files in its DISPATCH month, not the month its number was taken.
    REVERT CHECK: every uncancelled TRF counted as an issued challan, the
    cross-GSTIN one only left the run once its T number existed."""
    db = _db()
    _use(monkeypatch, db)
    monkeypatch.setattr(transfers, "_transfers_coll", lambda: db.get_collection("stock_transfers"))
    c = [transfers.generate_transfer_number("S1") for _ in range(5)]
    _trf(db, c[0], "pending_approval")
    _trf(db, c[1], "approved")
    _trf(db, c[2], "in_transit", WHEN, gst_registrations={"crosses": True})
    _trf(db, c[3], "completed", WHEN)
    _trf(db, c[4], "in_transit", datetime(2026, 7, 2, 6, 0))  # numbered June, left July

    def docs(m):
        return [(d["docNum"], d["from"], d["to"], d["total"], d["cancelled"])
                for d in r._compute_gstr1(m, "S1")["docsIssued"]]

    assert docs(MONTH) == [(12, c[3], c[3], 1, 0)]
    assert docs("2026-07") == [(12, c[4], c[4], 1, 0)]


def test_table_13_shared_x_series_never_shows_another_shops_number_as_cancelled(monkeypatch, june):
    """Two code-less shops share TRF-X-: shop A holds 00001 and 00003, shop B
    00002. A's Table 13 reports its own two challans, never 00002 as a
    cancelled one of its own."""
    db = _db()
    _use(monkeypatch, db)
    monkeypatch.setattr(transfers, "_transfers_coll", lambda: db.get_collection("stock_transfers"))
    a1, b1, a2 = (transfers.generate_transfer_number(s) for s in ("NOPE", "NOPE2", "NOPE"))
    for n, shop in ((a1, "NOPE"), (b1, "NOPE2"), (a2, "NOPE")):
        _trf(db, n, "completed", WHEN, shop)
    got = [(d["from"], d["to"], d["total"], d["cancelled"])
           for d in r._compute_gstr1(MONTH, "NOPE")["docsIssued"]]
    assert got == [(a1, a1, 1, 0), (a2, a2, 1, 0)]


def test_table_13_files_an_invoice_in_its_orders_month(monkeypatch):
    """An order booked 31 May (IST) and invoiced 2 June files in MAY's B2CS
    and so in May's Table 13 -- never June's, where its number splits the run.
    REVERT CHECK: the register's mint date put it in June's Table 13 only."""
    db = _db(customers=[W])
    _use(monkeypatch, db)

    def mint(when):
        when = when.replace(tzinfo=timezone.utc)
        monkeypatch.setattr(repo, "utc_now", lambda: when)
        monkeypatch.setattr(repo, "now_ist", lambda: when.astimezone(IST))
        return repo.next_document_number(db, "S1", "")

    june_1, late, june_2 = (mint(datetime(2026, 6, d, 6, 0)) for d in (1, 2, 3))
    item = [_line(5, 100.0, 5.0, "9004", 1)]
    db.seed("orders", [
        _order("O1", item, "W", number=june_1),
        {**_order("O2", item, "W", number=late), "created_at": datetime(2026, 5, 31, 12, 0)},
        _order("O3", item, "W", number=june_2),
    ])

    def docs(m):
        return [(d["from"], d["to"], d["total"], d["cancelled"])
                for d in r._compute_gstr1(m, "S1")["docsIssued"]]

    assert docs("2026-05") == [(late, late, 1, 0)]
    assert docs(MONTH) == [(june_1, june_1, 1, 0), (june_2, june_2, 1, 0)]
