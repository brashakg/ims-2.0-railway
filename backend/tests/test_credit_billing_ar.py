"""
Regression tests for the credit-billing AR lifecycle.

A CREDIT tender (pay-later) must NOT be counted as cash received: it should
leave the order outstanding (payment_status 'CREDIT') so it shows as a
receivable in finance /outstanding. Real money tenders reduce the balance.

Exercises OrderRepository.add_payment against an in-memory fake collection.
"""

import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.repositories.order_repository import OrderRepository  # noqa: E402


class _FakeCollection:
    """Just enough Mongo surface for add_payment: find_one + update_one
    ($push / $set). find_one returns a DEEP copy so the fetched doc's lists are
    independent of later $push, matching real Mongo deserialization."""

    def __init__(self, doc):
        self.doc = doc

    def find_one(self, flt):
        oid = flt.get("order_id")
        return copy.deepcopy(self.doc) if self.doc.get("order_id") == oid else None

    def update_one(self, flt, upd):
        if "$push" in upd:
            for k, v in upd["$push"].items():
                self.doc.setdefault(k, []).append(v)
        if "$set" in upd:
            self.doc.update(upd["$set"])

        class _R:
            matched_count = 1
            modified_count = 1

        return _R()


def _repo(grand_total=5000.0):
    doc = {
        "order_id": "ord-1",
        "grand_total": grand_total,
        "amount_paid": 0.0,
        "balance_due": grand_total,
        "payment_status": "UNPAID",
        "payments": [],
    }
    return OrderRepository(_FakeCollection(doc)), doc


def test_full_credit_tender_stays_outstanding():
    repo, doc = _repo(5000)
    assert repo.add_payment("ord-1", {"method": "CREDIT", "amount": 5000})
    assert doc["payment_status"] == "CREDIT"   # surfaces in /outstanding
    assert doc["amount_paid"] == 0.0           # not cash
    assert doc["balance_due"] == 5000.0


def test_cash_tender_marks_paid():
    repo, doc = _repo(5000)
    assert repo.add_payment("ord-1", {"method": "CASH", "amount": 5000})
    assert doc["payment_status"] == "PAID"
    assert doc["amount_paid"] == 5000.0
    assert doc["balance_due"] == 0.0


def test_partial_cash_then_credit_is_receivable():
    repo, doc = _repo(5000)
    repo.add_payment("ord-1", {"method": "CASH", "amount": 2000})
    assert doc["payment_status"] == "PARTIAL"
    assert doc["amount_paid"] == 2000.0
    # Remaining 3000 put on credit -> still a receivable, cash unchanged.
    repo.add_payment("ord-1", {"method": "CREDIT", "amount": 3000})
    assert doc["payment_status"] == "CREDIT"
    assert doc["amount_paid"] == 2000.0
    assert doc["balance_due"] == 3000.0


def test_credit_then_full_settlement_marks_paid():
    repo, doc = _repo(5000)
    repo.add_payment("ord-1", {"method": "CREDIT", "amount": 5000})
    assert doc["payment_status"] == "CREDIT"
    # Customer later pays the full amount in cash -> balance clears -> PAID.
    repo.add_payment("ord-1", {"method": "CASH", "amount": 5000})
    assert doc["payment_status"] == "PAID"
    assert doc["amount_paid"] == 5000.0
    assert doc["balance_due"] == 0.0
