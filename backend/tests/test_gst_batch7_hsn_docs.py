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
from database.repositories.order_repository import DOCUMENT_LOG as DOC_LOG  # noqa: E402

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


# ---------------------------------------------------------------------------
# Review round 2 (2026-11-03)
# ---------------------------------------------------------------------------


def _docs(month="2026-06", store="S1"):
    return [(d["docNum"], d["from"], d["to"], d["total"], d["cancelled"])
            for d in r._compute_gstr1(month, store)["docsIssued"]]


def test_table_13_keeps_an_invoice_reversed_by_a_whole_invoice_credit_note(monkeypatch, june):
    """Invoices 1-3; 2 was CANCELLED by the refund-advance door behind a
    credit note for its whole invoice -- B2CS still files it, so Table 13
    counts it issued: total 3, cancelled 0. REVERT CHECK: the Books view
    (is_real_sale without gst_documents) gave cancelled 1."""
    db = _db(customers=[W])
    _use(monkeypatch, db)
    n = [repo.next_document_number(db, "S1", "") for _ in range(3)]
    item = [_line(5, 100.0, 5.0, "9004", 1)]
    db.seed("orders", [
        _order("O1", item, "W", number=n[0]),
        {**_order("O2", item, "W", "CANCELLED", n[1]), "advance_refund_status": "COMPLETED",
         "advance_refund_credit_note": "C0001"},
        _order("O3", item, "W", number=n[2]),
    ])
    rep = r._compute_gstr1(MONTH, "S1")
    assert len(rep["b2cs"]) >= 1
    assert _docs() == [(1, n[0], n[2], 3, 0)]


def test_table_13_counts_invoices_challans_and_t_numbers_minted_before_the_register(monkeypatch):
    """No register rows at all (numbers minted before it existed): the
    window's invoice, a shipped in-GSTIN challan in the old TRF-YYYYMM- form,
    and a shipped cross-GSTIN transfer's T invoice still file in Table 13. An
    invoice of ANOTHER month inside the run ends it, never reads cancelled;
    so does a pre-register gap (00008: nothing says it was cancelled).
    REVERT CHECK: Table 13 was [] while B2CS filed the invoices."""
    db = _db(customers=[W])
    _use(monkeypatch, db)
    item = [_line(5, 100.0, 5.0, "9004", 1)]
    db.seed("orders", [
        _order("O1", item, "W", number="BOK02/2627/00005"),
        {**_order("O2", item, "W", number="BOK02/2627/00006"),
         "created_at": datetime(2026, 5, 31, 12, 0)},
        _order("O3", item, "W", number="BOK02/2627/00007"),
        _order("O4", item, "W", number="BOK02/2627/00009"),
    ])
    _trf(db, "TRF-202606-0042", "completed", WHEN)
    _trf(db, "TRF-202606-0043", "in_transit", WHEN, tax_invoice_number="BOK02/T/2627/00003",
         gst_registrations={"crosses": True})
    assert _docs() == [
        (1, "BOK02/2627/00005", "BOK02/2627/00005", 1, 0),
        (1, "BOK02/2627/00007", "BOK02/2627/00007", 1, 0),
        (1, "BOK02/2627/00009", "BOK02/2627/00009", 1, 0),
        (1, "BOK02/T/2627/00003", "BOK02/T/2627/00003", 1, 0),
        (12, "TRF-202606-0042", "TRF-202606-0042", 1, 0),
    ]


def test_table_13_warns_on_a_filed_credit_note_the_register_never_saw(monkeypatch):
    """A CDNR note numbered before the register cannot be placed in a run:
    the return SAYS so instead of an under-reported Table 13 with ok True."""
    db = _db(customers=[W], orders=[_order("O2", [_frame()], "W")],
             returns=[{**_return("RET-2", "O2", "W"), "credit_note_number": "BOK02/C2627/0004"}])
    _use(monkeypatch, db)
    rep = r._compute_gstr1(MONTH, "S1")
    assert [c["refReference"] for c in rep["cdnr"]] == ["BOK02/C2627/0004"]
    assert any("Table 13" in i["issue"] and "BOK02/C2627/0004" in i["issue"]
               for i in rep["validation"]["issues"])


def test_table_13_a_written_off_credit_note_serial_is_cancelled(monkeypatch, june):
    """Two C serials minted, one written off (CREDIT_NOTE_NUMBER_UNUSED):
    total 2, cancelled 1. REVERT CHECK: both read issued."""
    db = _db()
    _use(monkeypatch, db)
    c1, c2 = (repo.next_document_number(db, "S1", "C") for _ in range(2))
    db.seed("audit_logs", [{"action": "CREDIT_NOTE_NUMBER_UNUSED", "store_id": "S1",
                            "details": {"unused_credit_note_number": c2}}])
    assert _docs() == [(5, c1, c2, 2, 1)]


def test_table_13_t_invoice_on_a_cancelled_or_missing_transfer_is_cancelled(monkeypatch, june):
    """T 1: a completed transfer (issued); T 2: a transfer cancelled after a
    failed ship kept its number; T 3: no transfer holds it. Total 3,
    cancelled 2. REVERT CHECK: always-issued gave cancelled 0 / 1."""
    db = _db()
    _use(monkeypatch, db)
    t = [repo.next_document_number(db, "S1", "T") for _ in range(3)]
    db.seed("stock_transfers", [
        {"id": "a", "transfer_number": "TRF-BOK02-00001", "status": "completed",
         "tax_invoice_number": t[0]},
        {"id": "b", "transfer_number": "TRF-BOK02-00002", "status": "cancelled",
         "tax_invoice_number": t[1]},
    ])
    assert _docs() == [(1, t[0], t[2], 3, 2)]


def test_a_return_on_two_same_hsn_lines_takes_off_only_its_pieces(monkeypatch):
    """Two 900311 @5% frame lines (2 each) and 1 frame back from L1: Table 12
    files 3 pieces. REVERT CHECK: units.get (not pop) put the piece on both
    lines -- 2 pieces."""
    db = _db(customers=[W], orders=[_order("O2", [_frame(2, "L1"), _frame(2, "L2")], "W")],
             returns=[_return("RET-2", "O2", "W")])
    _use(monkeypatch, db)
    (row,) = r._compute_gstr1(MONTH, "S1")["hsnB2C"]
    assert (row["quantity"], row["taxableValue"]) == (3, 3000.0)


def test_returned_units_match_by_product_when_the_line_id_is_blank(monkeypatch):
    """A return line with no order_item_id still finds its sale line by
    product_id. REVERT CHECK: item-only matching dropped the piece (qty 3)."""
    ret = _return("RET-2", "O2", "W")
    ret["items"][0]["order_item_id"] = ""
    db = _db(customers=[W], orders=[_order("O2", [_frame()], "W")], returns=[ret])
    _use(monkeypatch, db)
    (row,) = r._compute_gstr1(MONTH, "S1")["hsnB2C"]
    assert row["quantity"] == 2


def test_returned_units_match_the_named_line_before_the_product(monkeypatch):
    """Two sale lines of ONE product on different HSNs; the return names L2:
    the piece comes off L2's HSN. REVERT CHECK: product-only matching took it
    off L1's."""
    l1 = _frame(2, "L1")
    l2 = {**_frame(2, "L2"), "hsn_code": "900410", "product_id": "P-L1"}
    ret = _return("RET-2", "O2", "W", item_id="L2")
    ret["items"][0]["product_id"] = "P-L1"
    db = _db(customers=[W], orders=[_order("O2", [l1, l2], "W")], returns=[ret])
    _use(monkeypatch, db)
    got = {h["hsnCode"]: h["quantity"] for h in r._compute_gstr1(MONTH, "S1")["hsnB2C"]}
    assert got == {"900311": 2, "900410": 1}


# ---------------------------------------------------------------------------
# Review round 3 (2026-11-03)
# ---------------------------------------------------------------------------


def test_table_13_files_a_t_invoice_once_in_its_dispatch_month(monkeypatch, june):
    """Both T numbers minted 15 June. T 1 is dated 15 June (the date B2B files
    it on) and only shipped 2 July; T 2's first ship failed and the retry
    re-dated it 2 July. Each counts ONCE, in its tax invoice's month: T 1 in
    June, T 2 in July. REVERT CHECK: June counted both (mint month) and July
    counted T 1 again (shipped_at)."""
    db = _db()
    _use(monkeypatch, db)
    t1, t2 = (repo.next_document_number(db, "S1", "T") for _ in range(2))
    july = "2026-07-02T06:00:00+00:00"
    for tid, n, dated in (("a", t1, "2026-06-15T06:00:00+00:00"), ("b", t2, july)):
        db.get_collection("stock_transfers").insert_one(
            {"id": tid, "transfer_number": f"TRF-BOK02-0000{tid}", "status": "in_transit",
             "from_location_id": "S1", "tax_invoice_number": n, "tax_invoice_date": dated,
             "shipped_at": july, "gst_registrations": {"crosses": True}})
    assert _docs() == [(1, t1, t1, 1, 0)]
    assert _docs("2026-07") == [(1, t2, t2, 1, 0)]


def test_table_13_runs_never_span_an_old_format_outlier(monkeypatch):
    """Pre-register invoices 0001, 0002 and a 7-digit 0300000 (the old
    fallback shape): two runs, nothing cancelled, and 'to' is a real number.
    REVERT CHECK: one row 0001..300000, total 300000, cancelled 299997."""
    db = _db(customers=[W])
    _use(monkeypatch, db)
    item = [_line(5, 100.0, 5.0, "9004", 1)]
    p = "BV/BOK02/26-27/"
    db.seed("orders", [_order(f"O{i}", item, "W", number=f"{p}{s}")
                       for i, s in enumerate(("0001", "0002", "0300000"))])
    assert _docs() == [
        (1, f"{p}0001", f"{p}0002", 2, 0),
        (1, f"{p}0300000", f"{p}0300000", 1, 0),
    ]


def test_table_13_gap_rules_pre_register_rerouted_and_capped(monkeypatch):
    """Pre-register 00007, 00009: the gap 00008 cannot be known cancelled --
    two runs. Registered 00102, 00104 with 00103 held by an order now at
    ANOTHER shop (re-routed): the gap ends the run. Registered 06000 and
    11000: a 4,999 gap is no real run -- split, and the return says so.
    REVERT CHECK: one 00007..11000 row of 10,994 with 10,988 cancelled (the
    pre-register gap, the re-routed order and the 4,999 gap all read cancelled)."""
    db = _db(customers=[W])
    _use(monkeypatch, db)
    item = [_line(5, 100.0, 5.0, "9004", 1)]
    p = "BOK02/2627/"
    db.seed(DOC_LOG, [{"number": f"{p}{s}", "series": "", "store_id": "S1", "issued_at": WHEN}
                      for s in ("00102", "00104", "06000", "11000")])
    db.seed("orders", [_order(f"O{s}", item, "W", number=f"{p}{s}")
                       for s in ("00007", "00009", "00102", "00104", "06000", "11000")]
            + [{**_order("Ox", item, "W", number=f"{p}00103"), "store_id": "S2",
                "created_at": datetime(2026, 5, 3, 6, 0)}])
    assert _docs() == [(1, f"{p}{s}", f"{p}{s}", 1, 0)
                       for s in ("00007", "00009", "00102", "00104", "06000", "11000")]
    issues = r._compute_gstr1(MONTH, "S1")["validation"]["issues"]
    assert any("Table 13" in i["issue"] and f"{p}06000" in i["issue"] for i in issues)
