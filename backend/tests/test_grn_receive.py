"""
IMS 2.0 - GRN receive-flow pure logic tests
===========================================
Covers the database-free decision helpers that drive "receive stock against a
PO" (GRN posting):

  * classify_grn_line_variance  -- per-line SHORT / EXACT / OVER / UNMATCHED.
  * compute_po_receipt_state    -- PARTIALLY_RECEIVED vs RECEIVED from the
                                   cumulative received-by-product tally.
  * grn_has_discrepancy         -- the existing variance-task predicate, kept
                                   in lockstep with the per-line classifier.

These are the load-bearing rules for partial vs full receipt and the
short/exact/over flags. They are pure functions, so they run locally; the
serialized stock_units write + audit are exercised on CI's mongo:7.0 through
the endpoint, not here (the stock minting needs a real repo).
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers.vendors import (  # noqa: E402
    classify_grn_line_variance,
    compute_po_receipt_state,
    grn_has_discrepancy,
)


# ---------------------------------------------------------------------------
# classify_grn_line_variance
# ---------------------------------------------------------------------------


def test_variance_exact():
    assert classify_grn_line_variance(10, 10) == "EXACT"


def test_variance_short():
    assert classify_grn_line_variance(7, 10) == "SHORT"


def test_variance_over():
    assert classify_grn_line_variance(12, 10) == "OVER"


def test_variance_unmatched_when_no_ordered():
    # A line not present on the PO has no ordered_qty to compare against.
    assert classify_grn_line_variance(5, None) == "UNMATCHED"


def test_variance_tolerance_absorbs_small_delta():
    # Within tolerance counts as EXACT either side.
    assert classify_grn_line_variance(9, 10, tolerance=1) == "EXACT"
    assert classify_grn_line_variance(11, 10, tolerance=1) == "EXACT"
    # Beyond tolerance still flags.
    assert classify_grn_line_variance(8, 10, tolerance=1) == "SHORT"
    assert classify_grn_line_variance(12, 10, tolerance=1) == "OVER"


def test_variance_handles_garbage_numbers():
    # None/garbage received coerces to 0 -> short of a positive order.
    assert classify_grn_line_variance(None, 10) == "SHORT"
    assert classify_grn_line_variance("oops", 10) == "SHORT"
    # Zero ordered + zero received is EXACT, not a crash.
    assert classify_grn_line_variance(0, 0) == "EXACT"


# ---------------------------------------------------------------------------
# compute_po_receipt_state
# ---------------------------------------------------------------------------

_PO_ITEMS = [
    {"product_id": "P1", "quantity": 10},
    {"product_id": "P2", "quantity": 5},
]


def test_po_fully_received():
    received = {"P1": 10, "P2": 5}
    assert compute_po_receipt_state(_PO_ITEMS, received) == "RECEIVED"


def test_po_partially_received_one_line_short():
    received = {"P1": 10, "P2": 3}
    assert compute_po_receipt_state(_PO_ITEMS, received) == "PARTIALLY_RECEIVED"


def test_po_partially_received_nothing_yet():
    assert compute_po_receipt_state(_PO_ITEMS, {}) == "PARTIALLY_RECEIVED"


def test_po_over_receipt_counts_as_received():
    # Receiving MORE than ordered still closes the line.
    received = {"P1": 12, "P2": 5}
    assert compute_po_receipt_state(_PO_ITEMS, received) == "RECEIVED"


def test_po_multiple_lines_same_product_roll_up():
    # Two PO lines for the same product -> ordered total is 8; 8 received closes.
    items = [
        {"product_id": "P1", "quantity": 5},
        {"product_id": "P1", "quantity": 3},
    ]
    assert compute_po_receipt_state(items, {"P1": 8}) == "RECEIVED"
    assert compute_po_receipt_state(items, {"P1": 7}) == "PARTIALLY_RECEIVED"


def test_po_no_items_is_received():
    # Nothing ordered -> nothing left to receive.
    assert compute_po_receipt_state([], {}) == "RECEIVED"
    assert compute_po_receipt_state(None, {}) == "RECEIVED"


def test_po_tolerance_closes_near_full():
    received = {"P1": 9, "P2": 5}
    assert compute_po_receipt_state(_PO_ITEMS, received, tolerance=1) == "RECEIVED"


# ---------------------------------------------------------------------------
# grn_has_discrepancy stays consistent with the per-line classifier
# ---------------------------------------------------------------------------


def test_discrepancy_false_on_exact_clean_receipt():
    grn = {
        "items": [
            {
                "product_id": "P1",
                "received_qty": 10,
                "ordered_qty": 10,
                "rejected_qty": 0,
            }
        ],
        "total_ordered": 10,
        "total_received": 10,
    }
    assert grn_has_discrepancy(grn) is False
    # And the per-line classifier agrees.
    assert classify_grn_line_variance(10, 10) == "EXACT"


def test_discrepancy_true_on_short_receipt():
    grn = {
        "items": [
            {
                "product_id": "P1",
                "received_qty": 7,
                "ordered_qty": 10,
                "rejected_qty": 0,
            }
        ],
        "total_ordered": 10,
        "total_received": 7,
    }
    assert grn_has_discrepancy(grn) is True
    assert classify_grn_line_variance(7, 10) == "SHORT"


def test_discrepancy_true_on_rejected_units_even_when_qty_matches():
    grn = {
        "items": [
            {
                "product_id": "P1",
                "received_qty": 10,
                "ordered_qty": 10,
                "rejected_qty": 2,
            }
        ],
    }
    assert grn_has_discrepancy(grn) is True
