"""
IMS 2.0 — /finance/gst/summary must source ITC from vendor_bills, not POs
==========================================================================
Bug: the GST summary computed input credit (ITC) by summing tax_amount over
`purchase_orders` matched on a `date` field that POs DON'T have (they carry
created_at/expected_date). The match found nothing, so ITC was always 0 and net
GST payable was overstated. Purchase ORDERS are intent; ITC is claimable on
vendor BILLS (GRN-backed) — the same source /itc-register reads.

These tests are BEHAVIOURAL: they seed a vendor bill AND a competing purchase
order into strict in-memory collections, call the real endpoint, and assert on
the rupee figures it returns. The previous versions asserted that the strings
"vendor_bills", "bill_date", "parse_date" and "field=" did / did not appear in
inspect.getsource(get_gst_summary) — which proves nothing about which
collection is actually read, and which silently pointed at a DIFFERENT
function's body when getsource desynchronised mid-suite (see
tests/source_guard.py).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-key-itc-source")

from strict_fakes import StrictDB  # noqa: E402

MONTH, YEAR = 6, 2026
_URL = f"/api/v1/finance/gst/summary?month={MONTH}&year={YEAR}"


@pytest.fixture
def gst_db(monkeypatch):
    """Strict in-memory DB behind the finance router's _get_db()."""
    from api.routers import finance as finance_mod

    db = StrictDB()
    monkeypatch.setattr(finance_mod, "_get_db", lambda: db)
    return db


def _bill(bill_id, tax, bill_date, **extra):
    doc = {
        "bill_id": bill_id,
        "vendor_id": "v-1",
        "bill_number": bill_id,
        "bill_date": bill_date,
        "taxable_amount": 5000.0,
        "tax_amount": tax,
        "total_amount": 5000.0 + tax,
    }
    doc.update(extra)
    return doc


def test_itc_is_sourced_from_vendor_bills_not_purchase_orders(
    client, auth_headers, gst_db
):
    """A vendor BILL supplies ITC; a purchase ORDER for a bigger amount does not.

    If the reader ever regresses to summing purchase_orders, ITC becomes 4500
    (or 0, the original bug). Only reading vendor_bills yields 900.
    """
    gst_db.seed("vendor_bills", [_bill("BILL-1", 900.0, "2026-06-10")])
    gst_db.seed(
        "purchase_orders",
        [
            {
                "po_id": "PO-1",
                "date": "2026-06-10",
                "expected_date": "2026-06-15",
                "tax_amount": 4500.0,
                "total_amount": 29500.0,
            }
        ],
    )

    resp = client.get(_URL, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["gst_input_credit"] == 900.0, (
        f"ITC must come from vendor_bills (900), not purchase_orders (4500) "
        f"and not the original always-zero bug; got {body['gst_input_credit']}"
    )


def test_itc_excludes_bills_outside_the_tax_period(client, auth_headers, gst_db):
    """The date filter must actually filter.

    A filter that silently matches everything is the classic way a money test
    passes while proving nothing, so assert the out-of-period bill is dropped
    AND the in-period one is kept.
    """
    gst_db.seed(
        "vendor_bills",
        [
            _bill("BILL-IN", 900.0, "2026-06-10"),
            _bill("BILL-PREV", 700.0, "2026-05-31"),
            _bill("BILL-NEXT", 500.0, "2026-07-01"),
        ],
    )
    body = client.get(_URL, headers=auth_headers).json()
    assert body["gst_input_credit"] == 900.0, body


def test_itc_accepts_a_datetime_bill_date(client, auth_headers, gst_db):
    """bill_date may be stored as a BSON datetime OR an ISO string.

    Replaces a grep for the literal "parse_date": what matters is that BOTH
    storage shapes are counted, which a Mongo range bound on one type would
    silently fail to do.
    """
    gst_db.seed(
        "vendor_bills",
        [
            _bill("BILL-DT", 300.0, datetime(2026, 6, 12, 9, 30)),
            _bill("BILL-STR", 200.0, "2026-06-13"),
        ],
    )
    body = client.get(_URL, headers=auth_headers).json()
    assert body["gst_input_credit"] == 500.0, body


def test_ineligible_bills_are_held_back_and_reported(client, auth_headers, gst_db):
    """A 17(5)-blocked bill must not inflate claimable ITC, but must still be
    visible in the excluded bucket rather than vanishing."""
    gst_db.seed(
        "vendor_bills",
        [
            _bill("BILL-OK", 900.0, "2026-06-10"),
            _bill("BILL-BLOCKED", 400.0, "2026-06-11", itc_blocked=True),
        ],
    )
    body = client.get(_URL, headers=auth_headers).json()
    assert body["gst_input_credit"] == 900.0, body
    assert body["gst_input_credit_excluded"] == 400.0, body


def test_summary_returns_a_complete_envelope_without_raising(
    client, auth_headers, gst_db
):
    """Regression guard for the earlier ``_apply_created_at_range(field=...)``
    call, which passed a kwarg the helper does not accept (runtime TypeError)
    and broke CI.

    The old test grepped for the substring "field=" in the source. This asserts
    the observable consequence instead: the endpoint completes and returns the
    full envelope. A TypeError on that path would surface as a 500 here.
    """
    gst_db.seed("vendor_bills", [_bill("BILL-1", 180.0, "2026-06-02")])
    resp = client.get(_URL, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for key in (
        "gst_collected",
        "cgst",
        "sgst",
        "igst",
        "gst_input_credit",
        "gst_input_credit_excluded",
        "net_gst_payable",
        "gstr1_due_date",
        "gstr3b_due_date",
    ):
        assert key in body, f"missing {key} in {sorted(body)}"
    # net payable = output tax - claimable ITC
    assert body["net_gst_payable"] == round(
        body["gst_collected"] - body["gst_input_credit"], 2
    )
