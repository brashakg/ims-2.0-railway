"""
IMS 2.0 - RC-A live-path money leftovers (OS-007 / OS-008 / OS-030)
===================================================================
PRs #935/#942 fixed the HISTORICAL import's money shape but left the LIVE
webhook ingest path with three defects this file pins the fixes for:

  OS-007  A partially_paid Shopify order booked amount_paid == grand_total AND
          balance_due == grand_total (2x money) under payment_status "UNPAID".
          Now: payment_status comes from the mapper's ONE vocabulary
          (partially_paid -> PARTIAL) and amount_paid derives from Shopify's
          total_outstanding (grand - outstanding, clamped), balance_due =
          grand - amount_paid -- on CREATE and on the status SYNC.

  OS-030  A live-ingested online order booked payments: [] so the Day-End
          tender columns and the GST cross-check payments_collected (sum of
          order.payments[].amount) read zero against real online sales.
          Now: one synthesized SETTLED method="SHOPIFY" row (amount ==
          amount_paid) at create, kept coherent ROW-GRANULARLY by the status
          sync (money-panel fix round): only the pipeline's own row is ever
          upserted (amount = amount_paid - staff tenders, identity preserved,
          unchanged list not written, snapshot-conditional against races);
          staff-recorded rows are never touched, and the header is never
          written below recorded staff tenders.

  OS-008  Finance/GST consumers recomputed inter/intra-state from
          customers.state (never set for online buyers -> every inter-state
          online sale misfiled CGST/SGST). Now: every consumer prefers the
          order doc's OWN persisted `interstate` flag (stamped at ingest from
          the delivery address), keeping the state-map heuristic ONLY as the
          fallback for docs without the flag (POS orders). Plus the mapper now
          persists the buyer's delivery state on newly-minted online customers
          so the fallback itself becomes truthful over time.

Pure: an in-memory fake DB + real Order/Customer repositories over fake
collections (no network, no real Mongo), the same harness as
test_shopify_ingest.py / test_online_order_mapper.py.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test_x")
os.environ["GST_PRICING_MODE"] = "inclusive"
os.environ.setdefault("ONLINE_STORE_ID", "BV-ONLINE-01")

from api.services import online_order_mapper, shopify_ingest


# ---------------------------------------------------------------------------
# Minimal in-memory Mongo emulator (only what these paths touch).
# ---------------------------------------------------------------------------


class _DuplicateKeyError(Exception):
    pass


def _cmp_ok(actual, op, op_val) -> bool:
    if actual is None:
        return op in ("$ne", "$nin")
    try:
        if op == "$gte":
            return actual >= op_val
        if op == "$gt":
            return actual > op_val
        if op == "$lte":
            return actual <= op_val
        if op == "$lt":
            return actual < op_val
    except TypeError:
        # Mongo type-bracketing: incomparable types simply don't match.
        return False
    if op == "$in":
        return actual in op_val
    if op == "$nin":
        return actual not in op_val
    if op == "$ne":
        return actual != op_val
    if op == "$exists":
        return (actual is not None) == bool(op_val)
    return True  # unknown operator -> permissive (not under test here)


def _match(doc, filter_) -> bool:
    if not filter_:
        return True
    for k, expected in filter_.items():
        if k == "$or":
            if not any(_match(doc, sub) for sub in expected):
                return False
            continue
        if isinstance(expected, dict):
            actual = doc.get(k)
            for op, op_val in expected.items():
                if not _cmp_ok(actual, op, op_val):
                    return False
        else:
            if doc.get(k) != expected:
                return False
    return True


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *_a, **_k):
        return self

    def limit(self, n):
        if n:
            self._docs = self._docs[:n]
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def __iter__(self):
        return iter([dict(d) for d in self._docs])


class FakeCollection:
    def __init__(self, name: str, database: "FakeDB"):
        self._name = name
        self.database = database  # next_invoice_number reads .database["counters"]
        self.docs: list = []
        self._unique_fields: set = set()

    def create_index(self, keys, **kwargs):
        if kwargs.get("unique") and isinstance(keys, str):
            self._unique_fields.add(keys)
        return None

    def _violates_unique(self, doc) -> bool:
        for f in self._unique_fields:
            val = doc.get(f)
            if val is None:
                continue
            for d in self.docs:
                if d.get(f) == val:
                    return True
        if "_id" in doc:
            for d in self.docs:
                if d.get("_id") == doc.get("_id"):
                    return True
        return False

    def insert_one(self, doc):
        if self._violates_unique(doc):
            raise _DuplicateKeyError("E11000 duplicate key error")
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def find_one(self, filter_=None, projection=None):
        for d in self.docs:
            if _match(d, filter_):
                return dict(d)
        return None

    def find(self, filter_=None, projection=None):
        return _Cursor([d for d in self.docs if _match(d, filter_)])

    def count_documents(self, filter_=None):
        return len([d for d in self.docs if _match(d, filter_)])

    def find_one_and_update(self, filter_, update, upsert=False, return_document=None):
        target = None
        for d in self.docs:
            if _match(d, filter_):
                target = d
                break
        if target is None and upsert:
            target = dict(filter_)
            self.docs.append(target)
        if target is None:
            return None
        for op, fields in (update or {}).items():
            if op == "$inc":
                for k, v in fields.items():
                    target[k] = (target.get(k) or 0) + v
            elif op == "$set":
                for k, v in fields.items():
                    target[k] = v
        return dict(target)

    def update_one(self, filter_, update, upsert=False):
        for d in self.docs:
            if _match(d, filter_):
                for k, v in (update.get("$set") or {}).items():
                    d[k] = v
                return type("R", (), {"modified_count": 1, "matched_count": 1})()
        if upsert:
            doc = dict(filter_)
            for k, v in (update.get("$set") or {}).items():
                doc[k] = v
            self.docs.append(doc)
            return type(
                "R", (), {"modified_count": 0, "matched_count": 0, "upserted_id": 1}
            )()
        return type("R", (), {"modified_count": 0, "matched_count": 0})()


class FakeDB:
    is_connected = True

    def __init__(self):
        self._collections: dict = {}

    def __getitem__(self, name):
        return self.get_collection(name)

    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = FakeCollection(name, self)
        return self._collections[name]


@pytest.fixture
def wired(monkeypatch):
    db = FakeDB()

    from database.repositories.customer_repository import CustomerRepository
    from database.repositories.order_repository import OrderRepository

    order_repo = OrderRepository(db.get_collection("orders"))
    customer_repo = CustomerRepository(db.get_collection("customers"))

    store_state = {"code": "20"}  # Jharkhand by default

    class _StoreRepo:
        def find_by_id(self, _store_id):
            return {"gstin": "", "state_code": store_state["code"]}

        def find_active(self, filter=None):
            return [{"store_id": "BV-ONLINE-01", "state_code": store_state["code"]}]

    import api.dependencies as deps

    monkeypatch.setattr(deps, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(deps, "get_product_repository", lambda: None)
    monkeypatch.setattr(deps, "get_store_repository", lambda: _StoreRepo())
    monkeypatch.setattr(deps, "get_customer_repository", lambda: customer_repo)

    return {
        "db": db,
        "orders": db.get_collection("orders"),
        "customers": db.get_collection("customers"),
        "store_state": store_state,
        "customer_repo": customer_repo,
    }


def _frame_order(
    order_id: int,
    buyer_state: str = "20",
    price: str = "999.00",
    financial_status: str = "paid",
    total_outstanding=None,
    phone: str = "",
):
    """A Shopify orders/create payload: one 5%-GST frame line (inclusive)."""
    payload = {
        "id": order_id,
        "name": f"#{order_id}",
        "financial_status": financial_status,
        "email": "buyer@example.com",
        "customer": {"id": 555, "first_name": "Ravi", "last_name": "Kumar"},
        "shipping_address": {"province": buyer_state, "province_code": buyer_state},
        "line_items": [
            {
                "id": 9001,
                "product_id": 7001,
                "title": "Ray-Ban Frame RB1234",
                "product_type": "Frames",
                "sku": "RB-1234",
                "quantity": 1,
                "price": price,
                "total_discount": "0.00",
            }
        ],
    }
    if total_outstanding is not None:
        payload["total_outstanding"] = total_outstanding
    if phone:
        payload["customer"]["phone"] = phone
        payload["phone"] = phone
    return payload


def _the_order(wired, shopify_id):
    docs = [
        d for d in wired["orders"].docs if d.get("shopify_order_id") == str(shopify_id)
    ]
    assert len(docs) == 1
    return docs[0]


# ---------------------------------------------------------------------------
# OS-007 -- create-time payment truth
# ---------------------------------------------------------------------------


def test_partially_paid_books_collected_not_double(wired):
    payload = _frame_order(
        7001, financial_status="partially_paid", total_outstanding="300.00"
    )
    res = shopify_ingest.ingest_shopify_order(
        wired["db"], payload, topic="orders/create"
    )
    assert res["status"] == "created"
    o = _the_order(wired, 7001)
    grand = o["grand_total"]
    assert o["payment_status"] == "PARTIAL"
    assert o["amount_paid"] == round(grand - 300.0, 2)
    assert o["balance_due"] == 300.0
    # The whole point: collected + due can never again exceed the order value.
    assert round(o["amount_paid"] + o["balance_due"], 2) == round(grand, 2)


def test_partially_paid_without_outstanding_books_conservative_zero(wired):
    payload = _frame_order(7002, financial_status="partially_paid")
    shopify_ingest.ingest_shopify_order(wired["db"], payload, topic="orders/create")
    o = _the_order(wired, 7002)
    assert o["payment_status"] == "PARTIAL"
    assert o["amount_paid"] == 0.0
    assert o["balance_due"] == o["grand_total"]


def test_paid_still_books_full_settled(wired):
    payload = _frame_order(7003, financial_status="paid")
    shopify_ingest.ingest_shopify_order(wired["db"], payload, topic="orders/create")
    o = _the_order(wired, 7003)
    assert o["payment_status"] == "PAID"
    assert o["amount_paid"] == o["grand_total"]
    assert o["balance_due"] == 0.0


def test_pending_books_unpaid_nothing_collected(wired):
    payload = _frame_order(7004, financial_status="pending")
    shopify_ingest.ingest_shopify_order(wired["db"], payload, topic="orders/create")
    o = _the_order(wired, 7004)
    assert o["payment_status"] == "UNPAID"
    assert o["amount_paid"] == 0.0
    assert o["balance_due"] == o["grand_total"]
    assert o["payments"] == []


def test_outstanding_is_clamped_to_the_order_value(wired):
    # Garbage negative outstanding must not book MORE than the order value...
    payload = _frame_order(
        7005, financial_status="partially_paid", total_outstanding="-50.00"
    )
    shopify_ingest.ingest_shopify_order(wired["db"], payload, topic="orders/create")
    o = _the_order(wired, 7005)
    assert o["amount_paid"] == o["grand_total"]
    assert o["balance_due"] == 0.0
    # ...and an outstanding larger than the order books zero collected.
    payload2 = _frame_order(
        7006, financial_status="partially_paid", total_outstanding="99999.00"
    )
    shopify_ingest.ingest_shopify_order(wired["db"], payload2, topic="orders/create")
    o2 = _the_order(wired, 7006)
    assert o2["amount_paid"] == 0.0
    assert o2["balance_due"] == o2["grand_total"]


# ---------------------------------------------------------------------------
# OS-030 -- synthesized SETTLED gateway payment row at create
# ---------------------------------------------------------------------------


def test_live_paid_order_books_settled_gateway_payment(wired):
    payload = _frame_order(7010, financial_status="paid")
    shopify_ingest.ingest_shopify_order(wired["db"], payload, topic="orders/create")
    o = _the_order(wired, 7010)
    assert len(o["payments"]) == 1
    row = o["payments"][0]
    assert row["method"] == "SHOPIFY"
    assert row["status"] == "SETTLED"
    assert row["settled_outside_ims"] is True
    assert row["amount"] == o["grand_total"] == o["amount_paid"]
    assert row["reference"] == "shopify:7010"


def test_partial_payment_row_matches_collected_amount(wired):
    payload = _frame_order(
        7011, financial_status="partially_paid", total_outstanding="300.00"
    )
    shopify_ingest.ingest_shopify_order(wired["db"], payload, topic="orders/create")
    o = _the_order(wired, 7011)
    assert len(o["payments"]) == 1
    assert o["payments"][0]["amount"] == o["amount_paid"] == round(
        o["grand_total"] - 300.0, 2
    )


# ---------------------------------------------------------------------------
# OS-007/OS-030 -- status-sync half (orders/updated webhooks)
# ---------------------------------------------------------------------------


def _seed_online_order(wired, shopify_id, grand=999.0, **over):
    doc = {
        "order_id": f"o-{shopify_id}",
        "_id": f"o-{shopify_id}",
        "shopify_order_id": str(shopify_id),
        "channel": "ONLINE",
        "source": "shopify",
        "grand_total": grand,
        "amount_paid": 0.0,
        "balance_due": grand,
        "payment_status": "UNPAID",
        "status": "CONFIRMED",
        "payments": [],
    }
    doc.update(over)
    wired["orders"].docs.append(doc)
    return doc


def _sync_payload(shopify_id, financial_status, total_outstanding=None):
    p = {"id": shopify_id, "financial_status": financial_status}
    if total_outstanding is not None:
        p["total_outstanding"] = total_outstanding
    return p


def test_sync_partial_transition_recomputes_money_and_payment_row(wired):
    # Pre-fix shape: double-counted partial (amount_paid == balance_due == grand).
    _seed_online_order(
        wired, 8001, grand=999.0, amount_paid=999.0, balance_due=999.0
    )
    ok = online_order_mapper._sync_existing_order_status(
        wired["db"], "8001", _sync_payload(8001, "partially_paid", "300.00")
    )
    assert ok is True
    o = _the_order(wired, 8001)
    assert o["payment_status"] == "PARTIAL"
    assert o["amount_paid"] == 699.0
    assert o["balance_due"] == 300.0
    assert len(o["payments"]) == 1
    assert o["payments"][0]["amount"] == 699.0
    assert o["payments"][0]["method"] == "SHOPIFY"


def test_sync_paid_transition_synthesizes_full_payment_row(wired):
    # Created while pending (nothing collected, payments []) -> paid webhook.
    _seed_online_order(wired, 8002, grand=999.0)
    ok = online_order_mapper._sync_existing_order_status(
        wired["db"], "8002", _sync_payload(8002, "paid")
    )
    assert ok is True
    o = _the_order(wired, 8002)
    assert o["payment_status"] == "PAID"
    assert o["amount_paid"] == 999.0
    assert o["balance_due"] == 0.0
    assert len(o["payments"]) == 1
    assert o["payments"][0]["amount"] == 999.0


def test_sync_adds_gateway_row_alongside_frozen_staff_row(wired):
    """Panel fix 2 (row-granular freeze): order created pending, staff records a
    CASH deposit, Shopify collects the remainder and sends orders/paid. The
    gateway money must appear as its own SHOPIFY row NEXT TO the untouched
    staff row so sum(payments) == amount_paid (cross-check / tender truth)."""
    staff_row = {
        "payment_id": "p-1",
        "method": "CASH",
        "amount": 100.0,
        "received_by": "user-1",
    }
    _seed_online_order(wired, 8003, grand=999.0, payments=[staff_row])
    online_order_mapper._sync_existing_order_status(
        wired["db"], "8003", _sync_payload(8003, "paid")
    )
    o = _the_order(wired, 8003)
    assert o["amount_paid"] == 999.0 and o["balance_due"] == 0.0
    # The staff-recorded tender row is untouched...
    assert staff_row in o["payments"]
    # ...the gateway leg appears as the synthesized SHOPIFY row...
    synth = [p for p in o["payments"] if p.get("settled_outside_ims")]
    assert len(synth) == 1
    assert synth[0]["method"] == "SHOPIFY"
    assert synth[0]["amount"] == 899.0
    # ...and the rows explain the header exactly.
    assert round(sum(p["amount"] for p in o["payments"]), 2) == o["amount_paid"]


def test_sync_partial_never_writes_header_below_staff_tenders(wired):
    """Panel fix 1: Shopify never learns of an in-store balance collection, so
    a routine orders/updated (financial_status still partially_paid with
    total_outstanding = the amount the till already collected) must NOT reduce
    amount_paid or resurrect balance_due."""
    synth_row = {
        "payment_id": "p-s",
        "method": "SHOPIFY",
        "amount": 400.0,
        "status": "SETTLED",
        "settled_outside_ims": True,
        "reference": "shopify:8005",
        "received_at": "2026-07-01T10:00:00",
    }
    staff_row = {"payment_id": "p-c", "method": "CASH", "amount": 600.0}
    _seed_online_order(
        wired,
        8005,
        grand=1000.0,
        amount_paid=1000.0,
        balance_due=0.0,
        payment_status="PAID",
        payments=[synth_row, staff_row],
    )
    online_order_mapper._sync_existing_order_status(
        wired["db"], "8005", _sync_payload(8005, "partially_paid", "600.00")
    )
    o = _the_order(wired, 8005)
    assert o["amount_paid"] == 1000.0  # never reduced below recorded tenders
    assert o["balance_due"] == 0.0  # never resurrected
    assert o["payment_status"] == "PAID"  # fully covered -> stays PAID
    # No churn: the gateway row still explains exactly the gateway leg.
    assert o["payments"] == [synth_row, staff_row]


def test_sync_preserves_synth_row_identity_and_skips_unchanged(wired):
    """Panel fix 2 riders: an unchanged amount writes nothing (no payment_id /
    received_at churn); a changed amount mutates ONLY the amount."""
    synth_row = {
        "payment_id": "p-keep",
        "method": "SHOPIFY",
        "amount": 999.0,
        "status": "SETTLED",
        "settled_outside_ims": True,
        "reference": "shopify:8006",
        "received_at": "2026-07-01T10:00:00",
    }
    _seed_online_order(
        wired,
        8006,
        grand=999.0,
        amount_paid=999.0,
        balance_due=0.0,
        payment_status="PAID",
        payments=[synth_row],
    )
    # Routine re-delivery of the paid status: amount unchanged -> no rewrite.
    online_order_mapper._sync_existing_order_status(
        wired["db"], "8006", _sync_payload(8006, "paid")
    )
    o = _the_order(wired, 8006)
    assert o["payments"] == [synth_row]

    # A partial correction changes the amount -- identity is still preserved.
    online_order_mapper._sync_existing_order_status(
        wired["db"], "8006", _sync_payload(8006, "partially_paid", "300.00")
    )
    o = _the_order(wired, 8006)
    assert len(o["payments"]) == 1
    row = o["payments"][0]
    assert row["amount"] == 699.0
    assert row["payment_id"] == "p-keep"  # identity preserved
    assert row["received_at"] == "2026-07-01T10:00:00"  # collection date kept


def test_nan_or_inf_outstanding_never_books_non_finite_money(wired):
    """Panel P3 rider: 'NaN'/'inf' strings parse to floats that pass every
    clamp -- both halves must treat them exactly like a missing value."""
    import math as _math

    payload = _frame_order(
        7020, financial_status="partially_paid", total_outstanding="NaN"
    )
    shopify_ingest.ingest_shopify_order(wired["db"], payload, topic="orders/create")
    o = _the_order(wired, 7020)
    assert o["amount_paid"] == 0.0
    assert _math.isfinite(o["balance_due"])
    assert o["balance_due"] == o["grand_total"]

    _seed_online_order(
        wired, 8007, grand=999.0, amount_paid=999.0, balance_due=999.0
    )
    online_order_mapper._sync_existing_order_status(
        wired["db"], "8007", _sync_payload(8007, "partially_paid", "inf")
    )
    o2 = _the_order(wired, 8007)
    # Money untouched (status still corrected by the normal vocabulary).
    assert o2["amount_paid"] == 999.0 and o2["balance_due"] == 999.0


def test_sync_partial_without_outstanding_leaves_money_untouched(wired):
    _seed_online_order(
        wired, 8004, grand=999.0, amount_paid=999.0, balance_due=999.0
    )
    online_order_mapper._sync_existing_order_status(
        wired["db"], "8004", _sync_payload(8004, "partially_paid")
    )
    o = _the_order(wired, 8004)
    # Status still corrected, but no guess at the money split.
    assert o["payment_status"] == "PARTIAL"
    assert o["amount_paid"] == 999.0
    assert o["balance_due"] == 999.0


# ---------------------------------------------------------------------------
# OS-008 -- consumers prefer the order-carried interstate flag
# ---------------------------------------------------------------------------


def test_split_output_tax_prefers_order_flag_over_missing_state():
    from api.routers import finance

    # Online order: stateless buyer, but the doc says interstate (IGST).
    orders = [
        {"store_id": "s1", "customer_id": None, "tax_amount": 100.0, "interstate": True}
    ]
    cgst, sgst, igst = finance._split_output_tax(orders, {}, {})
    assert (cgst, sgst, igst) == (0.0, 0.0, 100.0)


def test_split_output_tax_flag_false_wins_over_differing_states():
    from api.routers import finance

    # The order's own invoice said intra-state; a (stale) customer state map
    # disagreeing must NOT override the invoice.
    orders = [
        {"store_id": "s1", "customer_id": "c1", "tax_amount": 100.0, "interstate": False}
    ]
    cgst, sgst, igst = finance._split_output_tax(
        orders, {"s1": "Jharkhand"}, {"c1": "Maharashtra"}
    )
    assert igst == 0.0
    assert round(cgst + sgst, 2) == 100.0


def test_split_output_tax_fallback_unchanged_without_flag():
    from api.routers import finance

    orders = [{"store_id": "s1", "customer_id": "c1", "tax_amount": 100.0}]
    cgst, sgst, igst = finance._split_output_tax(
        orders, {"s1": "Jharkhand"}, {"c1": "Maharashtra"}
    )
    assert (cgst, sgst, igst) == (0.0, 0.0, 100.0)


def test_gst_reconciliation_prefers_order_flag():
    from api.routers import finance

    recon = finance.gst_reconciliation(
        [
            {
                "store_id": "s1",
                "customer_id": None,
                "tax_amount": 100.0,
                "interstate": True,
            }
        ],
        [],
        {"s1": "e1"},
        {"e1": "Entity One"},
        store_state_by_id={},
        customer_state_by_id={},
    )
    ent = recon["entities"][0]
    assert ent["igst"] == 100.0
    assert ent["cgst"] == 0.0 and ent["sgst"] == 0.0


def test_books_and_tally_uses_flag_and_counts_gateway_payments():
    from api.routers import finance

    db = FakeDB()
    db.get_collection("orders").docs.append(
        {
            "order_id": "o-1",
            "store_id": "BV-ONLINE-01",
            "customer_id": None,
            "channel": "ONLINE",
            "status": "CONFIRMED",
            "grand_total": 999.0,
            "tax_amount": 47.57,
            "interstate": True,
            "payments": [{"method": "SHOPIFY", "amount": 999.0, "status": "SETTLED"}],
            "created_at": datetime(2026, 7, 10, 6, 0, 0),
        }
    )
    start = datetime(2026, 7, 1)
    end = datetime(2026, 8, 1)
    books, tally = finance._books_and_tally_for_stores(db, None, start, end)
    # OS-008: the order-carried flag files the tax under IGST.
    assert tally["igst"] == 47.57
    assert tally["cgst"] == 0.0 and tally["sgst"] == 0.0
    # OS-030 consumer proof: the synthesized gateway row is real collections.
    assert books["payments_collected"] == 999.0
    assert books["sales_grand_total"] == 999.0


def test_tally_sales_jv_route_uses_order_flag(monkeypatch):
    from api.routers import finance

    db = FakeDB()
    db.get_collection("orders").docs.append(
        {
            "order_id": "o-jv-1",
            "store_id": "BV-ONLINE-01",
            "customer_id": None,
            "status": "CONFIRMED",
            "grand_total": 999.0,
            "tax_amount": 47.57,
            "interstate": True,
        }
    )
    monkeypatch.setattr(finance, "_get_db", lambda: db)

    captured = {}

    def _capture_xml(orders, store_meta):
        captured["orders"] = orders
        return "<ENVELOPE/>"

    import agents.nexus_providers as nx

    monkeypatch.setattr(nx, "tally_build_day_voucher_xml", _capture_xml)

    asyncio.run(
        finance.get_tally_sales_jv(
            from_date=None,
            to_date=None,
            store_id=None,
            entity_id=None,
            current_user={"roles": ["SUPERADMIN"]},
        )
    )
    assert len(captured["orders"]) == 1
    o = captured["orders"][0]
    # The voucher books the full tax to the IGST output ledger.
    assert o["igst_amount"] == 47.57
    assert o["cgst_amount"] == 0.0 and o["sgst_amount"] == 0.0


# ---------------------------------------------------------------------------
# OS-008 -- GSTR-1 / GSTR-3B honour the order-carried flag
# ---------------------------------------------------------------------------


class _Coll:
    def __init__(self, docs):
        self.docs = docs

    def find_one(self, query, projection=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                return d
        return None

    def find(self, query=None, projection=None):
        out = []
        for d in self.docs:
            if not query or all(
                d.get(k) == v for k, v in query.items() if not isinstance(v, dict)
            ):
                out.append(dict(d))
        return out


class _DB:
    def __init__(self, mapping):
        self._m = mapping

    def get_collection(self, name):
        return self._m.get(name, _Coll([]))

    def __getitem__(self, name):
        return self.get_collection(name)


_GSTR_STORE = {
    "store_id": "BV-ONLINE-01",
    "gstin": "20AABCB0001Q1ZZ",
    "store_name": "BV Online",
    "state": "Jharkhand",
}


def _online_order_doc(interstate):
    doc = {
        "order_id": "ORD-ONL-1",
        "order_number": "ONL-1",
        "invoice_number": "BV/2026-27/000123",
        "store_id": "BV-ONLINE-01",
        "customer_id": None,  # guest / stateless online buyer
        "created_at": datetime(2026, 4, 15, 10, 0, 0),
        "status": "CONFIRMED",
        "grand_total": 1050.0,
        "tax_amount": 50.0,
        "items": [
            {"hsn_code": "900311", "gst_rate": 5, "category": "FRAME", "item_total": 1050.0}
        ],
    }
    if interstate is not None:
        doc["interstate"] = interstate
    return doc


def _patch_reports_db(monkeypatch, order_doc):
    import api.routers.reports as r

    db = _DB(
        {
            "stores": _Coll([_GSTR_STORE]),
            "customers": _Coll([]),
            "orders": _Coll([order_doc]),
            "credit_note_ledger": _Coll([]),
        }
    )
    monkeypatch.setattr(r, "_get_raw_db", lambda: db)


def test_gstr1_b2cs_files_online_interstate_as_igst(monkeypatch):
    from api.routers.reports import _compute_gstr1

    _patch_reports_db(monkeypatch, _online_order_doc(interstate=True))
    report = _compute_gstr1("2026-04", "BV-ONLINE-01")
    rows = report["b2cs"]
    assert rows, "expected one B2CS bucket"
    assert round(sum(r["igst"] for r in rows), 2) == 50.0
    assert round(sum(r["cgst"] + r["sgst"] for r in rows), 2) == 0.0


def test_gstr1_without_flag_keeps_intra_fallback(monkeypatch):
    from api.routers.reports import _compute_gstr1

    _patch_reports_db(monkeypatch, _online_order_doc(interstate=None))
    report = _compute_gstr1("2026-04", "BV-ONLINE-01")
    rows = report["b2cs"]
    assert rows
    assert round(sum(r["igst"] for r in rows), 2) == 0.0
    assert round(sum(r["cgst"] + r["sgst"] for r in rows), 2) == 50.0


def test_gstr3b_outward_igst_from_order_flag(monkeypatch):
    from api.routers.reports import _compute_gstr3b

    _patch_reports_db(monkeypatch, _online_order_doc(interstate=True))
    report = _compute_gstr3b("2026-04", "BV-ONLINE-01")
    out = report["outwardTaxableSupplies"]
    assert out["integratedTax"] == 50.0
    assert out["centralTax"] == 0.0 and out["stateTax"] == 0.0


# ---------------------------------------------------------------------------
# Panel fix 3 -- CDNR credit notes reverse under the parent order's head
# ---------------------------------------------------------------------------


def _cdnr_ledger_row(interstate):
    row = {
        "entry_id": "cn-1",
        "customer_id": "",  # stateless online buyer
        "type": "ISSUED",
        "amount": 525.0,
        "store_id": "BV-ONLINE-01",
        "ref": "RET-1",
        "created_at": "2026-04-20T10:00:00",
        "gross_refund": 525.0,
        "net_refund": 525.0,
        "taxable": 500.0,
        "tax": 25.0,
        "gst_rate": 5,
    }
    if interstate is not None:
        row["interstate"] = interstate
    return row


def _patch_reports_db_with_cn(monkeypatch, cn_row):
    import api.routers.reports as r

    db = _DB(
        {
            "stores": _Coll([_GSTR_STORE]),
            "customers": _Coll([]),
            "orders": _Coll([]),
            "credit_note_ledger": _Coll([cn_row]),
        }
    )
    monkeypatch.setattr(r, "_get_raw_db", lambda: db)


def test_gstr1_cdnr_prefers_stamped_interstate(monkeypatch):
    """A refund of an IGST-filed online sale must reverse under IGST -- the
    ledger row's booking-time `interstate` stamp (from the parent order) wins
    over the customers.state heuristic."""
    from api.routers.reports import _compute_gstr1

    _patch_reports_db_with_cn(monkeypatch, _cdnr_ledger_row(interstate=True))
    report = _compute_gstr1("2026-04", "BV-ONLINE-01")
    cdnr = report["cdnr"]
    assert len(cdnr) == 1
    assert cdnr[0]["igst"] == 25.0
    assert cdnr[0]["cgst"] == 0.0 and cdnr[0]["sgst"] == 0.0


def test_gstr1_cdnr_without_stamp_keeps_state_fallback(monkeypatch):
    from api.routers.reports import _compute_gstr1

    _patch_reports_db_with_cn(monkeypatch, _cdnr_ledger_row(interstate=None))
    report = _compute_gstr1("2026-04", "BV-ONLINE-01")
    cdnr = report["cdnr"]
    assert len(cdnr) == 1
    # Legacy row, stateless buyer -> intra fallback exactly as before.
    assert cdnr[0]["igst"] == 0.0
    assert cdnr[0]["cgst"] == 12.5 and cdnr[0]["sgst"] == 12.5


def test_issue_store_credit_stamps_interstate(monkeypatch):
    """The ONE credit_note_ledger door both the Shopify refund path and the
    in-store CREDIT_NOTE path book through stamps the parent's interstate flag
    (bool-gated; absent stays absent)."""
    import api.routers.returns as ret
    from database.repositories.customer_repository import CustomerRepository

    db = FakeDB()
    db.get_collection("customers").docs.append(
        {"customer_id": "c1", "name": "A", "store_credit": 0.0}
    )
    repo = CustomerRepository(db.get_collection("customers"))
    monkeypatch.setattr(ret, "_get_db", lambda: db)
    monkeypatch.setattr(ret, "get_customer_repository", lambda: repo)
    user = {"active_store_id": "BV-ONLINE-01", "user_id": "u1"}

    entry = ret._issue_store_credit(
        "c1", 100.0, reason="r", ref="REF-1", current_user=user, interstate=True
    )
    assert entry is not None and entry["interstate"] is True
    rows = db.get_collection("credit_note_ledger").docs
    assert rows and rows[-1].get("interstate") is True

    entry2 = ret._issue_store_credit(
        "c1", 50.0, reason="r", ref="REF-2", current_user=user
    )
    assert entry2 is not None and "interstate" not in entry2


def test_historical_refund_cn_carries_parent_interstate(wired):
    """#935 historical import: the synthesized whole-order refund credit note
    carries the parent order's interstate flag so historical CDNR rows file
    under the same head as the (excluded-from-revenue) parent."""
    wired["store_state"]["code"] = "20"  # supplier Jharkhand
    payload = _frame_order(7030, buyer_state="27", financial_status="refunded")
    payload["created_at"] = "2024-06-10T10:00:00Z"
    res = online_order_mapper.map_shopify_order(
        payload, wired["db"], historical=True
    )
    assert res["status"] == "created"
    rows = wired["db"].get_collection("credit_note_ledger").docs
    assert rows, "whole-order historical refund must book a credit note"
    assert rows[-1].get("interstate") is True


# ---------------------------------------------------------------------------
# OS-008 -- the mapper persists the buyer's delivery state on NEW customers
# ---------------------------------------------------------------------------


def test_extract_buyer_state_from_shipping_address():
    buyer = online_order_mapper._extract_buyer(
        {
            "customer": {"id": 1, "first_name": "A"},
            "shipping_address": {"province": "Maharashtra", "province_code": "MH"},
        }
    )
    assert buyer["state"] == "Maharashtra"


def test_new_online_customer_carries_delivery_state(wired):
    payload = _frame_order(9001, buyer_state="Maharashtra", phone="+91 98765 43210")
    res = online_order_mapper.map_shopify_order(payload, wired["db"])
    assert res["status"] == "created"
    assert res["customer_id"]
    cust = wired["customers"].find_one({"customer_id": res["customer_id"]})
    assert cust is not None
    assert cust.get("state") == "Maharashtra"


def test_matched_customer_state_is_never_overwritten(wired):
    wired["customers"].docs.append(
        {
            "customer_id": "c-exist",
            "name": "Ravi Kumar",
            "mobile": "9876543210",
            "phone": "9876543210",
            "state": "Jharkhand",
        }
    )
    payload = _frame_order(9002, buyer_state="Maharashtra", phone="+91 98765 43210")
    res = online_order_mapper.map_shopify_order(payload, wired["db"])
    assert res["customer_id"] == "c-exist"
    cust = wired["customers"].find_one({"customer_id": "c-exist"})
    # A delivery address must never rewrite an existing customer's home state.
    assert cust["state"] == "Jharkhand"


def test_email_only_marketing_contact_carries_state(wired):
    payload = _frame_order(9003, buyer_state="Maharashtra")  # no phone -> email path
    res = online_order_mapper.map_shopify_order(payload, wired["db"])
    assert res["status"] == "created"
    cust = wired["customers"].find_one({"customer_id": res["customer_id"]})
    assert cust is not None
    assert cust.get("state") == "Maharashtra"
    assert cust.get("contact_tier") == "MARKETING"
