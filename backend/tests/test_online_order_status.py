"""
Owner rulings 2026-09-28 -- ONE rule for an online order's status
(api.services.online_order_status): every Shopify / courier event reduces to
one fact, and one table says what the fact does to the status IMS holds.

  1. Shopify "fulfilled" means SHIPPED; DELIVERED only from the courier.
  2. DELIVERED + a Shopify cancel / refund / delete stays DELIVERED, with ONE
     task for a person.
  3. A Shopify edit never moves an order backwards.
  A finished order (CANCELLED / REFUNDED / VOID) stays finished.

Every rule here goes red when that rule alone is reverted.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.services import online_order_status as oos  # noqa: E402

FACTS = (oos.SHIP, oos.DELIVER, oos.CANCEL, oos.REFUND, oos.DELETE)
_W, _C = (None, "withheld"), (None, "conflict")
_OPEN = {
    oos.SHIP: ("SHIPPED", None), oos.DELIVER: ("DELIVERED", None),
    oos.CANCEL: ("CANCELLED", None), oos.REFUND: ("REFUNDED", None), oos.DELETE: ("VOID", None),
}

# (status IMS holds) -> {fact: decide() answer}. The whole rule, cell by cell.
GOLDEN = {
    "CONFIRMED": _OPEN,
    "PROCESSING": _OPEN,
    "READY": _OPEN,
    "SHIPPED": {**_OPEN, oos.SHIP: (None, None)},
    "DELIVERED": {oos.SHIP: _W, oos.DELIVER: (None, None),
                  oos.CANCEL: _C, oos.REFUND: _C, oos.DELETE: _C},
    "CANCELLED": {**{f: _W for f in FACTS}, oos.CANCEL: (None, None)},
    "REFUNDED": {**{f: _W for f in FACTS}, oos.REFUND: (None, None)},
    "VOID": {**{f: _W for f in FACTS}, oos.DELETE: (None, None)},
    "VOIDED": {**{f: _W for f in FACTS}, oos.DELETE: (None, None)},
    "DRAFT": {f: (None, None) for f in FACTS},
    "HISTORICAL": {f: (None, None) for f in FACTS},
}


@pytest.mark.parametrize("status", sorted(GOLDEN))
@pytest.mark.parametrize("fact", FACTS)
def test_every_cell_of_the_table(status, fact):
    assert oos.decide({"status": status}, fact) == GOLDEN[status][fact]
    # Case / whitespace on the stored status never changes the answer.
    assert oos.decide({"status": f" {status.lower()} "}, fact) == GOLDEN[status][fact]


def test_no_fact_writes_nothing():
    for status in GOLDEN:
        assert oos.decide({"status": status}, None) == (None, None)


_RANK = {"CONFIRMED": 0, "PROCESSING": 1, "READY": 2, "SHIPPED": 3,
         "DELIVERED": 4, "CANCELLED": 4, "REFUNDED": 4, "VOID": 4}


def test_the_table_only_moves_forward_and_finished_rows_are_empty():
    for row, cells in oos.TABLE.items():
        for cell in cells.values():
            if cell in (oos.KEEP, oos.TASK):
                continue
            assert cell not in ("CONFIRMED", "PROCESSING", "READY"), (row, cell)  # ruling 3
            assert _RANK[cell] >= _RANK[row], (row, cell)
    assert set(oos.TABLE["DELIVERED"].values()) == {oos.TASK}  # ruling 2
    for row in ("CANCELLED", "REFUNDED", "VOID", "VOIDED"):
        assert oos.TABLE.get(row, {}) == {}, row  # a finished order stays finished


def test_only_deliver_leads_to_delivered():
    """Ruling 1: DELIVERED is reached by the DELIVER fact and nothing else."""
    for row, cells in oos.TABLE.items():
        for fact, cell in cells.items():
            if cell == "DELIVERED":
                assert fact == oos.DELIVER, (row, fact)


@pytest.mark.parametrize("body, ful_stale, fact", [
    ({"cancelled_at": "x", "financial_status": "refunded", "fulfillment_status": "fulfilled"}, False, oos.CANCEL),
    ({"financial_status": "refunded", "fulfillment_status": "fulfilled"}, False, oos.REFUND),
    ({"financial_status": "paid", "fulfillment_status": "fulfilled"}, False, oos.SHIP),
    ({"financial_status": "paid", "fulfillment_status": "fulfilled"}, True, None),
    ({"financial_status": "paid"}, False, None),
    ({"financial_status": "partially_refunded"}, False, None),
    ({"financial_status": "pending", "fulfillment_status": "partial"}, False, None),
    ({"fulfillment_status": "restocked"}, False, None),
    ({"note": "gift wrap"}, False, None),
])
def test_order_fact(body, ful_stale, fact):
    assert oos.order_fact(body, ful_stale=ful_stale) == fact


@pytest.mark.parametrize("ful, shipment, awb, fact", [
    ("FULFILLED", "delivered", "AWB", oos.DELIVER),
    ("PARTIAL", "delivered", "", oos.DELIVER),
    ("FULFILLED", "in_transit", "AWB", oos.SHIP),
    ("FULFILLED", "", "", oos.SHIP),
    ("PARTIAL", "", "AWB", oos.SHIP),
    ("PARTIAL", "", "", None),
    ("CANCELLED", "delivered", "AWB", None),
    ("CANCELLED", "", "AWB", None),
    ("ERROR", "", "AWB", None),
])
def test_fulfilment_fact(ful, shipment, awb, fact):
    assert oos.fulfilment_fact(ful, shipment, awb) == fact


@pytest.mark.parametrize("courier, fact", [
    ("DELIVERED", oos.DELIVER), ("Delivered", oos.DELIVER), (" delivered ", oos.DELIVER),
    ("RTO DELIVERED", None), ("RTO Delivered", None), ("OUT FOR DELIVERY", None),
    ("UNDELIVERED", None), (None, None), ("", None),
])
def test_courier_fact_is_an_exact_delivered(courier, fact):
    assert oos.courier_fact(courier) == fact
