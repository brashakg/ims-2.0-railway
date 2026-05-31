"""
IMS 2.0 — /finance/gst/summary must source ITC from vendor_bills, not POs
==========================================================================
Bug: the GST summary computed input credit (ITC) by summing tax_amount over
`purchase_orders` matched on a `date` field that POs DON'T have (they carry
created_at/expected_date). The match found nothing, so ITC was always 0 and net
GST payable was overstated. Purchase ORDERS are intent; ITC is claimable on
vendor BILLS (GRN-backed) — the same source /itc-register reads. This locks the
fix structurally (a full endpoint test needs a live Mongo).
"""

from __future__ import annotations

import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-key-itc-source")


def test_gst_summary_reads_vendor_bills_for_itc():
    import api.routers.finance as finance

    src = inspect.getsource(finance.get_gst_summary)
    # ITC must come from vendor_bills...
    assert "vendor_bills" in src, "ITC must be sourced from vendor_bills"
    # ...keyed on the bill date...
    assert 'field="bill_date"' in src
    # ...and must NOT re-introduce the purchase_orders.date aggregation bug.
    assert 'purchase_orders").aggregate' not in src


def test_gst_summary_uses_shared_date_range_helper():
    """Reusing _apply_created_at_range guarantees the same (BSON-datetime) date
    semantics as /itc-register, so the two reconcile."""
    import api.routers.finance as finance

    src = inspect.getsource(finance.get_gst_summary)
    assert "_apply_created_at_range" in src
