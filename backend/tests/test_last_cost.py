"""GET /vendors/last-cost -- the price a vendor was last really paid per product
(procurement Phase 2C: pre-fill the cost box instead of guessing).

THE one rule: the cost at acceptance on the vendor's newest ACCEPTED goods
receipt line, else the price on its newest order that was sent to it; never a
DRAFT, never a CANCELLED order. Read-only, fail-soft, store-scoped.

Calls the router function directly over the REAL PurchaseOrderRepository /
GRNRepository on strict in-memory collections, so the status filters and the
newest-first sorts are the real ones, not a fake's guess.
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from api.routers import vendors as v  # noqa: E402
from database.repositories.vendor_repository import (  # noqa: E402
    GRNRepository,
    PurchaseOrderRepository,
)
from strict_fakes import StrictCollection  # noqa: E402


def _po(po_id, po_number, created_at, items, store="S1", status="RECEIVED", sent_at=None):
    doc = {
        "po_id": po_id,
        "po_number": po_number,
        "vendor_id": "V1",
        "delivery_store_id": store,
        "created_at": created_at,
        "status": status,
        "items": items,
    }
    if sent_at:
        doc["sent_at"] = sent_at
    return doc


def _grn(grn_id, po, accepted_at, items, store="S1", status="ACCEPTED"):
    doc = {
        "grn_id": grn_id,
        "grn_number": grn_id.replace("G", "GRN-"),
        "po_id": po["po_id"],
        "po_number": po["po_number"],
        "vendor_id": "V1",
        "store_id": store,
        "status": status,
        "created_at": "2026-01-01T00:00:00",
        "items": items,
    }
    if accepted_at:
        doc["accepted_at"] = accepted_at
    return doc


def _wire(monkeypatch, pos, grns=()):
    po_repo = PurchaseOrderRepository(StrictCollection("purchase_orders", docs=pos))
    grn_repo = GRNRepository(StrictCollection("grns", docs=list(grns)))
    monkeypatch.setattr(v, "get_purchase_order_repository", lambda: po_repo)
    monkeypatch.setattr(v, "get_grn_repository", lambda: grn_repo)


def _user(roles=("STORE_MANAGER",), active="S1", stores=None):
    return {
        "user_id": "u1",
        "username": "t",
        "roles": list(roles),
        "active_store_id": active,
        "store_ids": stores if stores is not None else [active],
    }


def _call(**kw):
    return asyncio.run(v.get_last_purchase_cost(**kw))


def test_returns_most_recent_price_per_product(monkeypatch):
    # P1 last paid 420 on the newest PO, P2 only on the older one.
    pos = [
        _po(
            "PO1",
            "PO-1",
            "2026-05-01",
            [
                {"product_id": "P1", "unit_price": 400},
                {"product_id": "P2", "unit_price": 999},
            ],
        ),
        _po("PO2", "PO-2", "2026-06-12", [{"product_id": "P1", "unit_price": 420}]),
    ]
    _wire(monkeypatch, pos)
    out = _call(vendor_id="V1", product_ids="P1,P2", current_user=_user())
    assert out["costs"]["P1"]["unit_price"] == 420.0
    assert out["costs"]["P1"]["po_number"] == "PO-2"
    assert out["costs"]["P1"]["date"] == "2026-06-12"
    assert out["costs"]["P2"]["unit_price"] == 999.0  # only on the older PO


def test_draft_and_cancelled_prices_are_not_last_paid(monkeypatch):
    # The newest PO is CANCELLED (P1 at 9999) and a DRAFT carries P2 at 31000;
    # only the older RECEIVED order is a price actually paid.
    pos = [
        _po("PO3", "PO-3", "2026-10-01", [{"product_id": "P1", "unit_price": 9999}], status="CANCELLED"),
        _po("PO4", "PO-4", "2026-09-30", [{"product_id": "P2", "unit_price": 31000}], status="DRAFT"),
        _po("PO1", "PO-1", "2026-09-01", [{"product_id": "P1", "unit_price": 3100}], status="RECEIVED"),
    ]
    _wire(monkeypatch, pos)
    out = _call(vendor_id="V1", product_ids="P1,P2", current_user=_user())
    assert out["costs"]["P1"]["unit_price"] == 3100.0
    assert out["costs"]["P1"]["po_number"] == "PO-1"
    assert "P2" not in out["costs"]


@pytest.mark.parametrize("status", ["DRAFT", "CANCELLED", "APPROVED"])
def test_an_order_never_sent_is_no_price_paid(monkeypatch, status):
    # A history of only drafts, only cancelled orders, or a state the vendor
    # flow never sends (legacy APPROVED) answers nothing: the form keeps the
    # catalogue cost and shows no caption.
    _wire(
        monkeypatch,
        [_po("PO3", "PO-3", "2026-10-01", [{"product_id": "P1", "unit_price": 9999}], status=status)],
    )
    assert _call(vendor_id="V1", product_ids="P1", current_user=_user())["costs"] == {}


@pytest.mark.parametrize(
    "status", ["SENT", "ACKNOWLEDGED", "PARTIAL", "PARTIALLY_RECEIVED", "RECEIVED"]
)
def test_every_order_sent_to_the_vendor_counts(monkeypatch, status):
    _wire(
        monkeypatch,
        [_po("PO3", "PO-3", "2026-10-01", [{"product_id": "P1", "unit_price": 3100}], status=status)],
    )
    out = _call(vendor_id="V1", product_ids="P1", current_user=_user())
    assert out["costs"]["P1"]["unit_price"] == 3100.0


def test_an_order_is_dated_when_it_was_sent(monkeypatch):
    _wire(
        monkeypatch,
        [
            _po(
                "PO1",
                "PO-1",
                "2026-09-01",
                [{"product_id": "P1", "unit_price": 3100}],
                status="SENT",
                sent_at="2026-09-03T10:00:00",
            )
        ],
    )
    out = _call(vendor_id="V1", product_ids="P1", current_user=_user())
    assert out["costs"]["P1"]["date"] == "2026-09-03T10:00:00"


def test_the_accepted_receipt_cost_wins_and_is_dated_by_acceptance(monkeypatch):
    # P1 was received and accepted against PO-1 at 3100 on 12 Sep (and at 2900
    # in June). A newer order quoting 3500 has not arrived: it is not what the
    # vendor was paid.
    po0 = _po("PO0", "PO-0", "2026-05-20", [{"product_id": "P1", "unit_price": 2900}])
    po1 = _po(
        "PO1",
        "PO-1",
        "2026-09-01",
        [{"product_id": "P1", "unit_price": 3100}],
        sent_at="2026-09-03T10:00:00",
    )
    po5 = _po("PO5", "PO-5", "2026-10-01", [{"product_id": "P1", "unit_price": 3500}], status="SENT")
    grns = [
        _grn("G0", po0, "2026-06-01T11:00:00", [{"product_id": "P1", "accepted_qty": 1}]),
        _grn("G1", po1, "2026-09-12T16:30:00", [{"product_id": "P1", "accepted_qty": 2}]),
    ]
    _wire(monkeypatch, [po0, po1, po5], grns)
    hit = _call(vendor_id="V1", product_ids="P1", current_user=_user())["costs"]["P1"]
    assert hit["unit_price"] == 3100.0
    assert hit["po_number"] == "PO-1"
    assert hit["date"] == "2026-09-12T16:30:00"  # the receipt, not the order


def test_a_receipt_line_priced_on_its_own_keeps_that_price(monkeypatch):
    # The same cost goods receipt stamps on the units: the line's own price
    # first, the order's price only when the line carries none.
    po1 = _po("PO1", "PO-1", "2026-09-01", [{"product_id": "P1", "unit_price": 3100}])
    grn = _grn(
        "G1",
        po1,
        "2026-09-12T16:30:00",
        [{"product_id": "P1", "accepted_qty": 2, "unit_price": 3050}],
    )
    _wire(monkeypatch, [po1], [grn])
    out = _call(vendor_id="V1", product_ids="P1", current_user=_user())
    assert out["costs"]["P1"]["unit_price"] == 3050.0


def test_rejected_lines_and_unaccepted_receipts_are_not_a_price_paid(monkeypatch):
    po1 = _po("PO1", "PO-1", "2026-09-01", [{"product_id": "P1", "unit_price": 3100}])
    po7 = _po("PO7", "PO-7", "2026-09-20", [{"product_id": "P1", "unit_price": 9999}], status="PARTIALLY_RECEIVED")
    grns = [
        # Newest accepted receipt, but every unit of P1 was rejected.
        _grn(
            "G9",
            po7,
            "2026-10-02T09:00:00",
            [
                {"product_id": "P1", "accepted_qty": 0, "rejected_qty": 2},
                {"product_id": "P2", "accepted_qty": 1},
            ],
        ),
        # Logged but not accepted, and voided.
        _grn("G8", po7, None, [{"product_id": "P1", "accepted_qty": 2}], status="PENDING"),
        _grn("G7", po7, "2026-10-01T09:00:00", [{"product_id": "P1", "accepted_qty": 2}], status="VOID"),
        _grn("G1", po1, "2026-09-12T16:30:00", [{"product_id": "P1", "accepted_qty": 2}]),
    ]
    _wire(monkeypatch, [po1, po7], grns)
    hit = _call(vendor_id="V1", product_ids="P1", current_user=_user())["costs"]["P1"]
    assert hit["unit_price"] == 3100.0
    assert hit["date"] == "2026-09-12T16:30:00"


def test_cross_store_price_not_leaked(monkeypatch):
    # The only PO and receipt carrying P1 are for STORE-B; a STORE-A manager
    # must not see either.
    po9 = _po(
        "PO9",
        "PO-9",
        "2026-06-12",
        [{"product_id": "P1", "unit_price": 420}],
        store="STORE-B",
    )
    grn = _grn("G9", po9, "2026-06-20T10:00:00", [{"product_id": "P1", "accepted_qty": 1}], store="STORE-B")
    _wire(monkeypatch, [po9], [grn])
    out = _call(vendor_id="V1", product_ids="P1", current_user=_user(active="STORE-A"))
    assert out["costs"] == {}


def test_admin_sees_any_store(monkeypatch):
    _wire(
        monkeypatch,
        [
            _po(
                "PO9",
                "PO-9",
                "2026-06-12",
                [{"product_id": "P1", "unit_price": 420}],
                store="STORE-B",
            )
        ],
    )
    out = _call(
        vendor_id="V1",
        product_ids="P1",
        current_user=_user(roles=("ADMIN",), active="STORE-A"),
    )
    assert out["costs"]["P1"]["unit_price"] == 420.0


def test_zero_and_missing_prices_skipped(monkeypatch):
    _wire(
        monkeypatch,
        [
            _po(
                "PO1",
                "PO-1",
                "2026-06-12",
                [
                    {"product_id": "P1", "unit_price": 0},
                    {"product_id": "P2"},  # no price
                ],
            ),
        ],
    )
    out = _call(vendor_id="V1", product_ids="P1,P2,P3", current_user=_user())
    assert out["costs"] == {}


def test_empty_inputs_and_no_repo(monkeypatch):
    _wire(monkeypatch, [])
    assert _call(vendor_id="", product_ids="P1", current_user=_user())["costs"] == {}
    assert _call(vendor_id="V1", product_ids="", current_user=_user())["costs"] == {}
    monkeypatch.setattr(v, "get_purchase_order_repository", lambda: None)
    assert _call(vendor_id="V1", product_ids="P1", current_user=_user())["costs"] == {}


def test_rbac_row_catalogued():
    from api.services.rbac_policy import POLICY

    rows = [
        r
        for r in POLICY
        if r.get("path") == "/api/v1/vendors/last-cost" and r.get("method") == "GET"
    ]
    assert len(rows) == 1
    assert set(rows[0]["allowed"]) == {
        "ACCOUNTANT",
        "ADMIN",
        "AREA_MANAGER",
        "STORE_MANAGER",
    }
