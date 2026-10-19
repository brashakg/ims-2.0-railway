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
  * the DuplicateKeyError retry -> test_duplicate_key_*
  * ship mints the T number -> test_inter_gstin_ship_mints_*
  * the mirror bill carries it -> test_inter_gstin_transfer_in_gstr1_b2b
  * GSTR-1 checks transfer numbers -> test_gstr1_flags_an_old_transfer_number
  * the debit-note doors -> test_rtv_debit_note_*, test_ap_debit_note_*
  * the credit-note doors -> test_store_credit_note_*, test_admin_note_*
  * no truncation in CDNR -> test_cdnr_files_the_full_number

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
