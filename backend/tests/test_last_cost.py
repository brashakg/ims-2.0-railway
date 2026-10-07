"""GET /vendors/last-cost -- the price a vendor was last really paid per product
(procurement Phase 2C: pre-fill the cost box instead of guessing).

THE one rule: the cost at acceptance of the vendor's newest accepted goods-
receipt line (read back off the units the receipt minted), dated by acceptance;
only if there is none, the price on its newest order that was SENT to it, dated
when it was sent. Never a DRAFT / CANCELLED / APPROVED / PENDING order, never a
cancelled line, never a receipt line that accepted nothing. Read-only, fail-soft, store-scoped.

Calls the router functions directly over the REAL PurchaseOrderRepository /
GRNRepository / StockRepository on strict in-memory collections, and every
receipt is accepted through the REAL goods-receipt accept: the status filters
and the newest-first sorts are the real ones, and the cost read back is the one
acceptance stamped -- not a fake's guess of either.
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
from database.repositories.product_repository import StockRepository  # noqa: E402
from database.repositories.vendor_repository import (  # noqa: E402
    GRNRepository,
    PurchaseOrderRepository,
)
from strict_fakes import StrictCollection  # noqa: E402

_ADMIN = {
    "user_id": "admin",
    "username": "admin",
    "roles": ["ADMIN"],
    "active_store_id": "S1",
    "store_ids": ["S1"],
}


def _user(roles=("STORE_MANAGER",), active="S1"):
    return {
        "user_id": "u1",
        "username": "t",
        "roles": list(roles),
        "active_store_id": active,
        "store_ids": [active],
    }


class _World:
    """One vendor's purchase history on the real repositories."""

    def __init__(self, monkeypatch):
        self.pos = PurchaseOrderRepository(StrictCollection("purchase_orders"))
        self.grns = GRNRepository(StrictCollection("grns"))
        self.stock = StockRepository(StrictCollection("stock_units"))
        monkeypatch.setattr(v, "get_purchase_order_repository", lambda: self.pos)
        monkeypatch.setattr(v, "get_grn_repository", lambda: self.grns)
        monkeypatch.setattr(v, "get_stock_repository", lambda: self.stock)
        # Accept mints without the catalogue gate and the item-event ledger.
        monkeypatch.setattr(v, "get_product_repository", lambda: None)
        monkeypatch.setattr(v, "_get_db", lambda: None)
        monkeypatch.setattr(v, "is_online_store", lambda db, sid: False)

    def po(self, n, price, status="SENT", sent_at=None, created_at="2026-01-01", store="S1", pid="P1"):
        """PO-<n> for one product. created_at is deliberately far from sent_at:
        the answer must never be ranked or dated by it."""
        doc = {
            "po_id": f"PO{n}",
            "po_number": f"PO-{n}",
            "vendor_id": "V1",
            "delivery_store_id": store,
            "created_at": created_at,
            "status": status,
            "items": [{"product_id": pid, "quantity": 2, "unit_price": price}],
        }
        if sent_at:
            doc["sent_at"] = sent_at
        self.pos.collection.insert_one(doc)
        return doc

    def receive(self, n, po, lines, accepted_at, store="S1", status=None):
        """Log GRN-<n> against `po` and accept it through the REAL accept, then
        pin its acceptance time (accept stamps the wall clock)."""
        gid = f"G{n}"
        self.grns.collection.insert_one(
            {
                "grn_id": gid,
                "grn_number": f"GRN-{n}",
                "po_id": po["po_id"],
                "po_number": po["po_number"],
                "vendor_id": "V1",
                "store_id": store,
                "status": "PENDING",
                "created_at": "2026-01-01T00:00:00",
                "items": lines,
            }
        )
        if status == "PENDING":
            return  # logged at the counter, never accepted
        out = asyncio.run(v.accept_grn(gid, _ADMIN))
        assert out["grn_status"] == "ACCEPTED", out
        patch = {"accepted_at": accepted_at}
        if status:
            patch["status"] = status
        self.grns.collection.update_one({"grn_id": gid}, {"$set": patch})


def _costs(product_ids="P1", user=None):
    return asyncio.run(
        v.get_last_purchase_cost(
            vendor_id="V1", product_ids=product_ids, current_user=user or _user()
        )
    )["costs"]


def test_the_accepted_receipt_cost_wins_and_is_dated_by_acceptance(monkeypatch):
    # P1 was received and accepted against PO-1 at 3100 on 12 Sep (and against
    # PO-0 at 2900 in June). PO-5 quoting 3500 was sent later but has not
    # arrived: it is not what the vendor was paid.
    w = _World(monkeypatch)
    po0 = w.po(0, 2900, sent_at="2026-05-21T10:00:00")
    po1 = w.po(1, 3100, sent_at="2026-09-03T10:00:00")
    w.po(5, 3500, sent_at="2026-10-01T10:00:00")
    w.receive(0, po0, [{"product_id": "P1", "accepted_qty": 1}], "2026-06-01T11:00:00")
    w.receive(1, po1, [{"product_id": "P1", "accepted_qty": 2}], "2026-09-12T16:30:00")
    assert w.pos.find_by_id("PO1")["status"] == "RECEIVED"
    hit = _costs()["P1"]
    assert hit["unit_price"] == 3100.0
    assert hit["po_number"] == "PO-1"
    assert hit["date"] == "2026-09-12T16:30:00"  # the receipt, not the order


def test_a_receipt_line_priced_on_its_own_keeps_that_price(monkeypatch):
    # The receipt line says 3050 against an order at 3100: acceptance stamps
    # 3050 on the units, so 3050 is what was paid.
    w = _World(monkeypatch)
    po1 = w.po(1, 3100, sent_at="2026-09-03T10:00:00")
    w.receive(
        1,
        po1,
        [{"product_id": "P1", "accepted_qty": 2, "unit_price": 3050}],
        "2026-09-12T16:30:00",
    )
    assert {u["unit_cost"] for u in w.stock.collection.docs} == {3050.0}
    assert _costs()["P1"]["unit_price"] == 3050.0


def test_a_receipt_with_a_held_line_still_counts(monkeypatch):
    # PARTIALLY_ACCEPTED: another line waits for cataloguing, but P1's units are
    # on the shelf at their cost -- the newest price really paid.
    w = _World(monkeypatch)
    po0 = w.po(0, 2900, sent_at="2026-05-21T10:00:00")
    po1 = w.po(1, 3100, sent_at="2026-09-03T10:00:00")
    w.receive(0, po0, [{"product_id": "P1", "accepted_qty": 1}], "2026-06-01T11:00:00")
    w.receive(
        1,
        po1,
        [{"product_id": "P1", "accepted_qty": 2}],
        "2026-09-12T16:30:00",
        status="PARTIALLY_ACCEPTED",
    )
    assert _costs()["P1"]["unit_price"] == 3100.0


@pytest.mark.parametrize("status", ["DRAFT", "CANCELLED", "APPROVED", "PENDING"])
def test_an_order_never_sent_is_no_price_paid(monkeypatch, status):
    # A history of only drafts, cancelled orders, or states the vendor flow
    # never sent answers nothing: the form keeps the catalogue cost and shows
    # no caption.
    w = _World(monkeypatch)
    w.po(3, 9999, status=status)
    assert _costs() == {}


def test_draft_and_cancelled_never_beat_an_older_sent_order(monkeypatch):
    w = _World(monkeypatch)
    w.po(3, 9999, status="CANCELLED", sent_at="2026-10-01T10:00:00")
    w.po(4, 31000, status="DRAFT", created_at="2026-09-30")
    w.po(1, 3100, status="RECEIVED", sent_at="2026-09-01T10:00:00")
    w.po(2, 31000, status="DRAFT", created_at="2026-09-30", pid="P2")
    out = _costs("P1,P2")
    assert out["P1"]["unit_price"] == 3100.0
    assert out["P1"]["po_number"] == "PO-1"
    assert "P2" not in out


def test_a_cancelled_line_is_no_price_paid(monkeypatch):
    # The newest sent order had its P1 line cancelled (a line cancel, draft
    # #1165): only the older order's price was agreed and bought.
    w = _World(monkeypatch)
    w.po(1, 3100, sent_at="2026-09-01T10:00:00")
    po5 = w.po(5, 9999, sent_at="2026-10-01T10:00:00")
    po5["items"][0]["line_status"] = "CANCELLED"
    w.pos.collection.update_one({"po_id": "PO5"}, {"$set": {"items": po5["items"]}})
    assert _costs()["P1"]["unit_price"] == 3100.0


@pytest.mark.parametrize(
    "status", ["SENT", "ACKNOWLEDGED", "PARTIAL", "PARTIALLY_RECEIVED", "RECEIVED"]
)
def test_every_order_sent_to_the_vendor_counts(monkeypatch, status):
    w = _World(monkeypatch)
    w.po(3, 3100, status=status, sent_at="2026-10-01T10:00:00")
    assert _costs()["P1"]["unit_price"] == 3100.0


def test_an_order_is_ranked_and_dated_by_when_it_was_sent(monkeypatch):
    # PO-A was raised first but sent last; nothing has arrived from either.
    w = _World(monkeypatch)
    w.po("A", 3000, created_at="2026-09-01", sent_at="2026-09-10T10:00:00")
    w.po("B", 3200, created_at="2026-09-05", sent_at="2026-09-06T10:00:00")
    hit = _costs()["P1"]
    assert hit["unit_price"] == 3000.0
    assert hit["date"] == "2026-09-10T10:00:00"  # sent, not created


def test_a_logged_but_unaccepted_receipt_leaves_the_order_standing(monkeypatch):
    # The goods are at the counter but nobody has accepted them yet: the sent
    # order is still the last price agreed.
    w = _World(monkeypatch)
    po5 = w.po(5, 3500, sent_at="2026-10-01T10:00:00")
    w.receive(5, po5, [{"product_id": "P1", "accepted_qty": 2}], None, status="PENDING")
    assert _costs()["P1"]["unit_price"] == 3500.0


def test_an_all_rejected_receipt_is_not_a_price_paid(monkeypatch):
    # PO-7's delivery came in and every P1 was rejected (P2 was accepted). The
    # older accepted PO-1 receipt is the last price really paid for P1.
    w = _World(monkeypatch)
    po1 = w.po(1, 3100, sent_at="2026-09-01T10:00:00")
    po7 = w.po(7, 9999, sent_at="2026-09-20T10:00:00")
    po7["items"].append({"product_id": "P2", "quantity": 1, "unit_price": 500})
    w.pos.collection.update_one({"po_id": "PO7"}, {"$set": {"items": po7["items"]}})
    w.receive(1, po1, [{"product_id": "P1", "accepted_qty": 2}], "2026-09-12T16:30:00")
    w.receive(
        7,
        po7,
        [
            {"product_id": "P1", "accepted_qty": 0, "rejected_qty": 2},
            {"product_id": "P2", "accepted_qty": 1},
        ],
        "2026-10-02T09:00:00",
    )
    assert w.pos.find_by_id("PO7")["status"] == "PARTIALLY_RECEIVED"
    hit = _costs()["P1"]
    assert hit["unit_price"] == 3100.0
    assert hit["date"] == "2026-09-12T16:30:00"


def test_an_order_whose_only_receipt_rejected_everything_does_not_count(monkeypatch):
    # Still PARTIALLY_RECEIVED (sent, part-received), but nothing of P1 was ever
    # taken in: its 9999 is a quote the shop refused, not a price paid.
    w = _World(monkeypatch)
    po7 = w.po(7, 9999, sent_at="2026-09-20T10:00:00")
    w.receive(
        7,
        po7,
        [{"product_id": "P1", "accepted_qty": 0, "rejected_qty": 2}],
        "2026-10-02T09:00:00",
    )
    assert w.pos.find_by_id("PO7")["status"] == "PARTIALLY_RECEIVED"
    assert _costs() == {}


def test_cross_store_price_not_leaked(monkeypatch):
    # The only order and receipt carrying P1 are for STORE-B; a STORE-A
    # manager must not see either.
    w = _World(monkeypatch)
    po9 = w.po(9, 420, sent_at="2026-06-12T10:00:00", store="STORE-B")
    w.po(8, 450, sent_at="2026-06-30T10:00:00", store="STORE-B")
    w.receive(9, po9, [{"product_id": "P1", "accepted_qty": 1}], "2026-06-20T10:00:00", store="STORE-B")
    assert _costs(user=_user(active="STORE-A")) == {}
    # A cross-store role sees the receipt.
    assert _costs(user=_user(roles=("ADMIN",), active="STORE-A"))["P1"]["unit_price"] == 420.0


def test_zero_and_missing_prices_skipped(monkeypatch):
    w = _World(monkeypatch)
    w.po(1, 0, sent_at="2026-06-12T10:00:00")
    w.pos.collection.insert_one(
        {
            "po_id": "PO2",
            "po_number": "PO-2",
            "vendor_id": "V1",
            "delivery_store_id": "S1",
            "status": "SENT",
            "sent_at": "2026-06-13T10:00:00",
            "items": [{"product_id": "P2"}],  # no price
        }
    )
    assert _costs("P1,P2,P3") == {}


def test_empty_inputs_and_no_repo(monkeypatch):
    _World(monkeypatch)
    assert asyncio.run(v.get_last_purchase_cost(vendor_id="", product_ids="P1", current_user=_user()))["costs"] == {}
    assert asyncio.run(v.get_last_purchase_cost(vendor_id="V1", product_ids="", current_user=_user()))["costs"] == {}
    monkeypatch.setattr(v, "get_purchase_order_repository", lambda: None)
    assert _costs() == {}


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
