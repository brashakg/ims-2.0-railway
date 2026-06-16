"""Purchase P1 / S5 -- per-store, per-FY purchase document numbering."""
from __future__ import annotations

import os
import sys
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import purchase_numbering as pn  # noqa: E402


class _FakeCounters:
    """Mimics the atomic $inc on the shared counters collection."""

    def __init__(self):
        self.seqs: dict = {}

    def find_one_and_update(self, flt, update, upsert=False, return_document=None):
        key = flt["_id"]
        self.seqs[key] = self.seqs.get(key, 0) + int(update["$inc"]["seq"])
        return {"_id": key, "seq": self.seqs[key]}


WHEN = datetime(2026, 6, 17, 11, 30, 0)  # FY 2026-27 -> "26-27"


def test_format_is_prefix_store_fy_serial():
    assert pn.format_purchase_number("PO", "BOK-01", "26-27", 1) == "PO/BOK-01/26-27/0001"


def test_doc_prefix_aliases():
    assert pn.doc_prefix("PO") == "PO"
    assert pn.doc_prefix("grn") == "RCPT"
    assert pn.doc_prefix("receipt") == "RCPT"
    assert pn.doc_prefix("PINV") == "PINV"
    assert pn.doc_prefix("purchase_invoice") == "PINV"


def test_serial_increments_per_store_and_type():
    c = _FakeCounters()
    a = pn.next_purchase_number(c, doc_type="PO", store_code="BOK-01", when=WHEN)
    b = pn.next_purchase_number(c, doc_type="PO", store_code="BOK-01", when=WHEN)
    assert a == "PO/BOK-01/26-27/0001"
    assert b == "PO/BOK-01/26-27/0002"
    # A different store has its OWN series.
    o = pn.next_purchase_number(c, doc_type="PO", store_code="DHN-01", when=WHEN)
    assert o == "PO/DHN-01/26-27/0001"
    # A different doc-type has its OWN series for the same store.
    g = pn.next_purchase_number(c, doc_type="GRN", store_code="BOK-01", when=WHEN)
    assert g == "RCPT/BOK-01/26-27/0001"


def test_fy_resets_serial():
    c = _FakeCounters()
    # Mar 2026 is FY 2025-26; Apr 2026 is FY 2026-27 -> separate counters.
    mar = pn.next_purchase_number(c, doc_type="PINV", store_code="BOK-01",
                                  when=datetime(2026, 3, 31, 9, 0))
    apr = pn.next_purchase_number(c, doc_type="PINV", store_code="BOK-01",
                                  when=datetime(2026, 4, 1, 9, 0))
    assert mar == "PINV/BOK-01/25-26/0001"
    assert apr == "PINV/BOK-01/26-27/0001"


def test_store_segment_sanitised_and_fallback():
    c = _FakeCounters()
    # store_code with a slash is sanitised so it can't break the format.
    r = pn.next_purchase_number(c, doc_type="PO", store_code="A/B 01", when=WHEN)
    assert r.startswith("PO/AB01/26-27/")
    # No store at all -> HQ.
    r2 = pn.next_purchase_number(c, doc_type="PO", when=WHEN)
    assert r2.startswith("PO/HQ/26-27/")


def test_fail_soft_without_counters():
    # No counters collection -> time-derived suffix, same prefix/store/fy shape.
    r = pn.next_purchase_number(None, doc_type="GRN", store_code="BOK-01", when=WHEN)
    assert r.startswith("RCPT/BOK-01/26-27/")
    # Suffix is the mmddHHMMSS of WHEN.
    assert r.endswith(WHEN.strftime("%m%d%H%M%S"))
