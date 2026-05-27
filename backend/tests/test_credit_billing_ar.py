"""
Regression tests for the credit-billing AR lifecycle.

A CREDIT tender (pay-later) must NOT be counted as cash received: it should
leave the order outstanding (payment_status 'CREDIT') so it shows as a
receivable in finance /outstanding. Real money tenders reduce the balance.

Council Branch A residuals on top of PR #256:
  - Over-tender raises ValueError (cash + credit must not exceed grand_total).
  - Sticky credit_sale flag: once a credit sale, always flagged.
  - Refund/negative tender re-aggregates correctly (may flip PAID back).
  - Multiple CREDIT tender rows: summed correctly, never added to amount_paid.

Exercises OrderRepository.add_payment against an in-memory fake collection.
"""

import copy
import os
import sys

import pytest

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
    assert doc["credit_sale"] is True          # sticky audit flag


def test_cash_tender_marks_paid():
    repo, doc = _repo(5000)
    assert repo.add_payment("ord-1", {"method": "CASH", "amount": 5000})
    assert doc["payment_status"] == "PAID"
    assert doc["amount_paid"] == 5000.0
    assert doc["balance_due"] == 0.0
    assert doc["credit_sale"] is False         # never a credit sale


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
    assert doc["credit_sale"] is True


def test_credit_then_full_settlement_marks_paid_but_flag_sticks():
    """After a CREDIT, customer later pays with cash -> PAID, but the sticky
    credit_sale flag stays True so auditors know it was originally on credit."""
    repo, doc = _repo(5000)
    repo.add_payment("ord-1", {"method": "CREDIT", "amount": 5000})
    assert doc["payment_status"] == "CREDIT"
    assert doc["credit_sale"] is True
    repo.add_payment("ord-1", {"method": "CASH", "amount": 5000})
    assert doc["payment_status"] == "PAID"
    assert doc["amount_paid"] == 5000.0
    assert doc["balance_due"] == 0.0
    # Sticky: still True after settlement.
    assert doc["credit_sale"] is True


def test_over_tender_raises_value_error_on_cash():
    """Cash collected (non-CREDIT) must not exceed grand_total."""
    repo, _doc = _repo(5000)
    repo.add_payment("ord-1", {"method": "CASH", "amount": 3000})
    with pytest.raises(ValueError):
        repo.add_payment("ord-1", {"method": "CASH", "amount": 3000})  # 6000 > 5000


def test_credit_plus_cash_settlement_is_not_over_tender():
    """A CREDIT promise followed by full cash settlement is legitimate -- the
    cash is settling the credit, not double-collecting. NOT an over-tender."""
    repo, doc = _repo(5000)
    repo.add_payment("ord-1", {"method": "CREDIT", "amount": 5000})
    # The next cash tender pays it off and must NOT raise.
    repo.add_payment("ord-1", {"method": "CASH", "amount": 5000})
    assert doc["payment_status"] == "PAID"
    assert doc["amount_paid"] == 5000.0


def test_refund_negative_amount_flips_paid_back():
    """A negative-amount tender (refund) re-aggregates: PAID may go back to
    PARTIAL."""
    repo, doc = _repo(5000)
    repo.add_payment("ord-1", {"method": "CASH", "amount": 5000})
    assert doc["payment_status"] == "PAID"
    assert doc["amount_paid"] == 5000.0
    # Refund 1500 (negative cash tender).
    repo.add_payment("ord-1", {"method": "CASH", "amount": -1500})
    assert doc["amount_paid"] == 3500.0
    assert doc["balance_due"] == 1500.0
    assert doc["payment_status"] == "PARTIAL"


def test_multiple_credit_tenders_sum_correctly():
    """Two CREDIT rows -> neither bumps amount_paid; balance still grand_total."""
    repo, doc = _repo(5000)
    repo.add_payment("ord-1", {"method": "CREDIT", "amount": 2000})
    repo.add_payment("ord-1", {"method": "CREDIT", "amount": 3000})
    assert doc["amount_paid"] == 0.0
    assert doc["balance_due"] == 5000.0
    assert doc["payment_status"] == "CREDIT"
    assert doc["credit_sale"] is True
    assert len(doc["payments"]) == 2


def test_credit_then_cash_partial_then_cash_settle():
    """End-to-end: credit -> partial cash -> final cash. Each step recomputes."""
    repo, doc = _repo(5000)
    repo.add_payment("ord-1", {"method": "CREDIT", "amount": 5000})
    assert doc["payment_status"] == "CREDIT"
    repo.add_payment("ord-1", {"method": "CASH", "amount": 2000})
    # Still has a CREDIT row + outstanding balance.
    assert doc["payment_status"] == "CREDIT"
    assert doc["amount_paid"] == 2000.0
    assert doc["balance_due"] == 3000.0
    repo.add_payment("ord-1", {"method": "UPI", "amount": 3000})
    assert doc["payment_status"] == "PAID"
    assert doc["amount_paid"] == 5000.0
    assert doc["balance_due"] == 0.0
    assert doc["credit_sale"] is True
