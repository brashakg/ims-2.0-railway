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
    invoice (not a challan -- it splits the run; its T number is an invoice),
    and a cancelled one."""
    db = _db()
    _use(monkeypatch, db)
    monkeypatch.setattr(transfers, "_transfers_coll", lambda: db.get_collection("stock_transfers"))
    c1, c2, c3 = (transfers.generate_transfer_number("S1") for _ in range(3))
    t_inv = repo.next_document_number(db, "S1", "T")
    db.seed("stock_transfers", [
        {"id": "t1", "transfer_number": c1, "status": "in_transit"},
        {"id": "t2", "transfer_number": c2, "status": "completed", "tax_invoice_number": t_inv},
        {"id": "t3", "transfer_number": c3, "status": "cancelled"},
    ])
    docs = r._compute_gstr1(MONTH, "S1")["docsIssued"]
    assert [(d["docNum"], d["from"], d["to"], d["total"], d["cancelled"]) for d in docs] == [
        (1, t_inv, t_inv, 1, 0),
        (12, c1, c1, 1, 0),
        (12, c3, c3, 1, 1),
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
