"""
IMS 2.0 - Shopify online-order ingestion tests (Council B10)
============================================================
IMS is the GST invoice system-of-record for online (bettervision.in / Shopify)
orders. These tests pin the load-bearing guarantees of
api.services.shopify_ingest.ingest_shopify_order:

  Idempotency (revenue must never double-count)
    - ingesting the SAME Shopify order payload twice -> exactly ONE IMS order +
      ONE invoice (the second call returns status "duplicate", same order id).
    - a replayed X-Shopify-Webhook-Id short-circuits (status "replayed").

  GST place-of-supply (the whole reason IMS mints the invoice)
    - inter-state delivery (buyer state != store state) -> IGST, CGST/SGST = 0.
    - intra-state delivery (buyer state == store state) -> CGST + SGST, IGST = 0.
    - the per-line rate resolves via resolve_gst_rate (5% optical-dominant
      default; an explicit 18% category -> 18%).

  Channel + fail-soft
    - the IMS order is tagged channel='ONLINE'.
    - no DB -> "simulated", never a crash.

These run pure (no network, no real Mongo): an in-memory fake DB + a real
OrderRepository wrapped around a fake collection exercises the real invoice
serial allocator (counters $inc) and the real _build_invoice_gst_split.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test_x")
# Pin inclusive pricing so the assertions are deterministic regardless of env.
os.environ["GST_PRICING_MODE"] = "inclusive"

from api.services import shopify_ingest


# ---------------------------------------------------------------------------
# Minimal in-memory Mongo emulator (just what the ingest path touches).
# ---------------------------------------------------------------------------


class FakeCollection:
    def __init__(self, name: str, database: "FakeDB"):
        self._name = name
        self.database = database  # next_invoice_number reads .database["counters"]
        self.docs: list = []
        self._unique_fields: set = set()

    # --- index registration (we only honour the unique ones we care about) ---
    def create_index(self, keys, **kwargs):
        if kwargs.get("unique"):
            if isinstance(keys, str):
                self._unique_fields.add(keys)
        return None

    def _violates_unique(self, doc) -> bool:
        for f in self._unique_fields:
            val = doc.get(f) if f != "_id" else doc.get("_id")
            if val is None:
                continue
            for d in self.docs:
                existing = d.get(f) if f != "_id" else d.get("_id")
                if existing == val:
                    return True
        # _id is always unique in Mongo
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
        return type("R", (), {"modified_count": 0, "matched_count": 0})()


class _DuplicateKeyError(Exception):
    pass


def _match(doc, filter_) -> bool:
    if not filter_:
        return True
    for k, expected in filter_.items():
        if isinstance(expected, dict):
            actual = doc.get(k)
            for op, op_val in expected.items():
                if op == "$type":
                    if actual is None:
                        return False
                else:
                    return False
        else:
            if doc.get(k) != expected:
                return False
    return True


class FakeDB:
    is_connected = True

    def __init__(self):
        self._collections: dict = {}

    def __getitem__(self, name):  # counters access: self.collection.database["counters"]
        return self.get_collection(name)

    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = FakeCollection(name, self)
        return self._collections[name]


# ---------------------------------------------------------------------------
# Wiring: patch the three dependency getters the service resolves lazily.
# ---------------------------------------------------------------------------


@pytest.fixture
def wired(monkeypatch):
    db = FakeDB()

    # A real OrderRepository on a fake `orders` collection -> exercises the real
    # atomic invoice serial allocator (counters $inc) + ensure_invoice_index.
    from database.repositories.order_repository import OrderRepository

    orders_coll = db.get_collection("orders")
    order_repo = OrderRepository(orders_coll)

    # No product master / no store doc in these tests: SKU lookup misses (so the
    # category hint path is used) and the store state is supplied by the test
    # via a patched store repo when needed.
    store_state = {"code": "20"}  # Jharkhand, by default

    class _StoreRepo:
        def find_by_id(self, _store_id):
            return {"gstin": "", "state_code": store_state["code"]}

    import api.dependencies as deps

    monkeypatch.setattr(deps, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(deps, "get_product_repository", lambda: None)
    monkeypatch.setattr(deps, "get_store_repository", lambda: _StoreRepo())

    return {"db": db, "orders": orders_coll, "store_state": store_state}


def _frame_order(order_id: int, buyer_state: str, price: str = "999.00"):
    """A Shopify orders/create payload: one 5%-GST frame line."""
    return {
        "id": order_id,
        "name": f"#{order_id}",
        "financial_status": "paid",
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


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_double_ingest_yields_one_order_and_one_invoice(wired):
    payload = _frame_order(1001, buyer_state="20")  # intra-state

    first = shopify_ingest.ingest_shopify_order(wired["db"], payload, topic="orders/create")
    second = shopify_ingest.ingest_shopify_order(wired["db"], payload, topic="orders/create")

    assert first["status"] == "created"
    assert first["order_id"]
    assert first["invoice_number"]

    # Second ingest of the SAME Shopify order id -> duplicate, same IMS order,
    # NO new order doc, NO second invoice.
    assert second["status"] == "duplicate"
    assert second["order_id"] == first["order_id"]
    assert second["invoice_number"] == first["invoice_number"]

    online_orders = [d for d in wired["orders"].docs if d.get("shopify_order_id") == "1001"]
    assert len(online_orders) == 1, "exactly one IMS order for the Shopify order"


def test_replayed_webhook_id_short_circuits(wired):
    payload = _frame_order(1002, buyer_state="20")
    wid = "abcdef-webhook-id-123"

    first = shopify_ingest.ingest_shopify_order(
        wired["db"], payload, webhook_id=wid, topic="orders/create"
    )
    replay = shopify_ingest.ingest_shopify_order(
        wired["db"], payload, webhook_id=wid, topic="orders/create"
    )

    assert first["status"] == "created"
    assert replay["status"] == "replayed"
    assert replay["order_id"] == first["order_id"]
    online_orders = [d for d in wired["orders"].docs if d.get("shopify_order_id") == "1002"]
    assert len(online_orders) == 1


# ---------------------------------------------------------------------------
# GST place-of-supply: IGST vs CGST+SGST
# ---------------------------------------------------------------------------


def test_interstate_delivery_yields_igst(wired):
    wired["store_state"]["code"] = "20"  # supplier: Jharkhand
    payload = _frame_order(2001, buyer_state="27")  # buyer: Maharashtra -> inter-state

    res = shopify_ingest.ingest_shopify_order(wired["db"], payload, topic="orders/create")
    assert res["status"] == "created"
    assert res["interstate"] is True

    order = wired["orders"].find_one({"shopify_order_id": "2001"})
    totals = order["tax_totals"]
    assert totals["igst"] > 0, "inter-state must charge IGST"
    assert totals["cgst"] == 0 and totals["sgst"] == 0
    # taxable + tax reconciles to grand_total.
    assert round(totals["taxable"] + totals["igst"], 2) == order["grand_total"]


def test_intrastate_delivery_yields_cgst_sgst(wired):
    wired["store_state"]["code"] = "20"  # supplier: Jharkhand
    payload = _frame_order(2002, buyer_state="20")  # buyer: Jharkhand -> intra-state

    res = shopify_ingest.ingest_shopify_order(wired["db"], payload, topic="orders/create")
    assert res["status"] == "created"
    assert res["interstate"] is False

    order = wired["orders"].find_one({"shopify_order_id": "2002"})
    totals = order["tax_totals"]
    assert totals["cgst"] > 0 and totals["sgst"] > 0, "intra-state must split CGST+SGST"
    assert totals["igst"] == 0
    # CGST + SGST + taxable reconciles to grand_total.
    assert round(totals["taxable"] + totals["cgst"] + totals["sgst"], 2) == order["grand_total"]


def test_rate_resolves_via_resolve_gst_rate(wired):
    # A frame line (product_type "Frames") resolves to 5% via the category hint.
    payload = _frame_order(3001, buyer_state="20", price="1050.00")
    res = shopify_ingest.ingest_shopify_order(wired["db"], payload, topic="orders/create")
    assert res["status"] == "created"
    order = wired["orders"].find_one({"shopify_order_id": "3001"})
    rates = {round(i["gst_rate"], 2) for i in order["items"]}
    assert rates == {5.0}, "frame -> 5% optical rate via resolve_gst_rate"

    # A sunglass line (product_type "Sunglasses") resolves to 18%.
    sg = _frame_order(3002, buyer_state="20", price="1180.00")
    sg["line_items"][0]["product_type"] = "Sunglasses"
    sg["line_items"][0]["title"] = "Polarized Sunglasses"
    sg["line_items"][0]["sku"] = "SG-9"
    res2 = shopify_ingest.ingest_shopify_order(wired["db"], sg, topic="orders/create")
    order2 = wired["orders"].find_one({"shopify_order_id": "3002"})
    rates2 = {round(i["gst_rate"], 2) for i in order2["items"]}
    assert rates2 == {18.0}, "sunglass -> 18% via resolve_gst_rate"


# ---------------------------------------------------------------------------
# Channel tag + fail-soft
# ---------------------------------------------------------------------------


def test_order_tagged_channel_online(wired):
    payload = _frame_order(4001, buyer_state="20")
    shopify_ingest.ingest_shopify_order(wired["db"], payload, topic="orders/create")
    order = wired["orders"].find_one({"shopify_order_id": "4001"})
    assert order["channel"] == "ONLINE"
    assert order["source"] == "shopify"
    # Shopify "paid" -> the IMS online order is PAID on ingestion.
    assert order["payment_status"] == "PAID"


def test_no_db_simulates_without_crashing():
    payload = _frame_order(5001, buyer_state="20")
    res = shopify_ingest.ingest_shopify_order(None, payload, topic="orders/create")
    assert res["status"] == "simulated"
    assert res["shopify_order_id"] == "5001"


def test_non_order_topic_ignored(wired):
    payload = _frame_order(6001, buyer_state="20")
    res = shopify_ingest.ingest_shopify_order(
        wired["db"], payload, topic="products/update"
    )
    assert res["status"] == "ignored"
    assert not [d for d in wired["orders"].docs if d.get("shopify_order_id") == "6001"]


# ---------------------------------------------------------------------------
# Oversell fix: an online order DECREMENTS physical stock (BVI cutover)
# ---------------------------------------------------------------------------


class _FakeProductRepo:
    def find_by_sku(self, sku):
        if sku == "RB-1234":
            return {"product_id": "P-RB", "hsn_code": "9003", "category": "FRAME"}
        return None


class _FakeStockRepo:
    """Records FIFO claims so we can assert the decrement fired with the IMS pid."""

    def __init__(self):
        self.claims = []

    def claim_one_available(self, pid, store_id, order_id, used):
        self.claims.append((pid, store_id, order_id))
        return f"unit-{len(self.claims)}"


def test_online_order_decrements_physical_stock(wired, monkeypatch):
    """The oversell fix: ingesting an online order FIFO-claims the sold
    serialized units (so a walk-in can't sell the same unit), keyed by the IMS
    product_id (resolved via SKU), at the fulfillment store."""
    import api.dependencies as deps
    from api.routers import orders as orders_mod

    monkeypatch.setattr(deps, "get_product_repository", lambda: _FakeProductRepo())
    stock = _FakeStockRepo()
    monkeypatch.setattr(orders_mod, "get_stock_repository", lambda: stock)

    res = shopify_ingest.ingest_shopify_order(
        wired["db"], _frame_order(7100, buyer_state="20"), topic="orders/create"
    )
    assert res["status"] == "created"
    # Exactly one unit claimed, by the IMS product_id (NOT the Shopify 7001),
    # at the fulfillment store (defaults to the online billing store).
    assert len(stock.claims) == 1
    assert stock.claims[0][0] == "P-RB"
    assert stock.claims[0][1] == "BV-ONLINE-01"


def test_online_order_unmapped_sku_skips_decrement(wired, monkeypatch):
    """A line whose SKU has no IMS product is NOT our serialized stock -> no
    decrement attempted (fail-soft, never blocks the booked order)."""
    import api.dependencies as deps
    from api.routers import orders as orders_mod

    monkeypatch.setattr(deps, "get_product_repository", lambda: _FakeProductRepo())
    stock = _FakeStockRepo()
    monkeypatch.setattr(orders_mod, "get_stock_repository", lambda: stock)

    order = _frame_order(7101, buyer_state="20")
    order["line_items"][0]["sku"] = "NOT-IN-IMS"
    res = shopify_ingest.ingest_shopify_order(wired["db"], order, topic="orders/create")
    assert res["status"] == "created"  # invoice still booked
    assert stock.claims == []  # nothing claimed


class _FakeStockRepoNoStock:
    """A stock repo with NOTHING available -> every FIFO claim returns None."""

    def claim_one_available(self, pid, store_id, order_id, used):
        return None


def test_online_order_underclaim_records_loud_stock_miss(wired, monkeypatch):
    """FAIL-LOUD: when a paid online order cannot claim all its physical units
    (out of on-hand at the fulfillment store), the invoice still books BUT an
    `online_stock_miss` doc is written so the oversell surfaces (sync-health +
    Sentry) instead of slipping by as a warning."""
    import api.dependencies as deps
    from api.routers import orders as orders_mod

    monkeypatch.setattr(deps, "get_product_repository", lambda: _FakeProductRepo())
    monkeypatch.setattr(
        orders_mod, "get_stock_repository", lambda: _FakeStockRepoNoStock()
    )

    res = shopify_ingest.ingest_shopify_order(
        wired["db"], _frame_order(7102, buyer_state="20"), topic="orders/create"
    )
    assert res["status"] == "created"  # paid order is NEVER blocked
    misses = wired["db"].get_collection("online_stock_miss").docs
    assert len(misses) == 1, "an unfulfillable online order must record a stock miss"
    assert misses[0]["reason"] == "under_claim"
    assert misses[0]["resolved"] is False
    assert misses[0]["detail"]["expected"] == 1
    assert misses[0]["detail"]["claimed"] == 0


# ---------------------------------------------------------------------------
# Clinical FLAG & HOLD: spectacle-lens online orders missing a valid Rx
# A paid online sale is NEVER refused -- a prescription-lens line without a
# valid customer-matching non-expired Rx is BOOKED but flagged rx_pending +
# fulfillment_hold, and ONE follow-up task is raised.
# ---------------------------------------------------------------------------


def _lens_order(order_id: int, *, sku="OL-1", with_rx=None, sph=None, properties=None):
    """A Shopify order with ONE optical (spectacle) LENS line -> Rx-required."""
    line = {
        "id": 9100,
        "product_id": 7100,
        "title": "Zeiss Single Vision Lens",
        "product_type": "Optical Lens",
        "sku": sku,
        "quantity": 1,
        "price": "1500.00",
        "total_discount": "0.00",
    }
    if with_rx is not None:
        line["prescription_id"] = with_rx
    if sph is not None:
        line["sph"] = sph
    if properties is not None:
        line["properties"] = properties
    return {
        "id": order_id,
        "name": f"#{order_id}",
        "financial_status": "paid",
        "email": "buyer@example.com",
        "customer": {"id": 555, "first_name": "Ravi", "last_name": "Kumar"},
        "shipping_address": {"province": "20", "province_code": "20"},
        "line_items": [line],
    }


class _RxRepo:
    """Minimal prescription repo: a dict of rx_id -> rx doc."""

    def __init__(self, by_id=None, by_customer=None):
        self._by_id = by_id or {}
        self._by_customer = by_customer or {}

    def find_by_id(self, rx_id):
        return self._by_id.get(rx_id)

    def find_by_customer(self, customer_id):
        return self._by_customer.get(str(customer_id), [])


def _valid_rx(rx_id="RX-1", customer_id="555"):
    # prescription_date now -> validity 12 months -> non-expired.
    from datetime import datetime as _dt

    return {
        "prescription_id": rx_id,
        "customer_id": customer_id,
        "prescription_date": _dt.now().isoformat(),
        "validity_months": 12,
    }


def _expired_rx(rx_id="RX-OLD", customer_id="555"):
    return {
        "prescription_id": rx_id,
        "customer_id": customer_id,
        "prescription_date": "2020-01-01T00:00:00",
        "validity_months": 12,
    }


def test_lens_line_no_rx_creates_order_flagged_and_one_task(wired, monkeypatch):
    """(a) spectacle-lens line, NO Rx -> order CREATED with rx_pending=true +
    exactly ONE follow-up task (NOT rejected)."""
    import api.dependencies as deps

    monkeypatch.setattr(deps, "get_prescription_repository", lambda: _RxRepo())

    res = shopify_ingest.ingest_shopify_order(
        wired["db"], _lens_order(8001), topic="orders/create"
    )
    assert res["status"] == "created"  # paid sale NEVER refused
    assert res["rx_pending"] is True

    order = wired["orders"].find_one({"shopify_order_id": "8001"})
    assert order["rx_pending"] is True
    assert order["fulfillment_hold"] is True
    assert "RX_MISSING" in order["rx_hold_reasons"]

    tasks = wired["db"].get_collection("tasks").docs
    hold_tasks = [t for t in tasks if t.get("order_id") == order["order_id"]]
    assert len(hold_tasks) == 1
    assert hold_tasks[0]["task_type"] == "online_rx_hold"
    assert hold_tasks[0]["source"] == "ONLINE_RX_HOLD"


def test_lens_line_with_valid_rx_no_flag_no_task(wired, monkeypatch):
    """(b) spectacle-lens line, valid customer-matching non-expired Rx ->
    no flag, no task."""
    import api.dependencies as deps

    repo = _RxRepo(by_id={"RX-1": _valid_rx("RX-1", "555")})
    monkeypatch.setattr(deps, "get_prescription_repository", lambda: repo)

    res = shopify_ingest.ingest_shopify_order(
        wired["db"], _lens_order(8002, with_rx="RX-1"), topic="orders/create"
    )
    assert res["status"] == "created"
    assert res["rx_pending"] is False

    order = wired["orders"].find_one({"shopify_order_id": "8002"})
    assert order["rx_pending"] is False
    assert order["fulfillment_hold"] is False
    tasks = wired["db"].get_collection("tasks").docs
    assert [t for t in tasks if t.get("order_id") == order["order_id"]] == []


def test_lens_line_finds_customer_rx_without_explicit_link(wired, monkeypatch):
    """A lens line with no explicit Rx id but a valid Rx on the customer's file
    is NOT flagged (the customer fallback lookup resolves it)."""
    import api.dependencies as deps

    repo = _RxRepo(by_customer={"555": [_valid_rx("RX-9", "555")]})
    monkeypatch.setattr(deps, "get_prescription_repository", lambda: repo)

    res = shopify_ingest.ingest_shopify_order(
        wired["db"], _lens_order(8003), topic="orders/create"
    )
    assert res["rx_pending"] is False


def test_frame_line_no_rx_not_flagged(wired, monkeypatch):
    """(c.1) a FRAME line, no Rx -> no flag (frames are EXEMPT)."""
    import api.dependencies as deps

    monkeypatch.setattr(deps, "get_prescription_repository", lambda: _RxRepo())
    res = shopify_ingest.ingest_shopify_order(
        wired["db"], _frame_order(8004, buyer_state="20"), topic="orders/create"
    )
    assert res["rx_pending"] is False
    order = wired["orders"].find_one({"shopify_order_id": "8004"})
    assert order["rx_pending"] is False
    assert wired["db"].get_collection("tasks").docs == []


def test_contact_lens_line_no_rx_not_flagged(wired, monkeypatch):
    """(c.2) a CONTACT-lens line, no Rx -> no flag (contacts are EXEMPT)."""
    import api.dependencies as deps

    monkeypatch.setattr(deps, "get_prescription_repository", lambda: _RxRepo())
    order = _frame_order(8005, buyer_state="20")
    order["line_items"][0]["title"] = "Acuvue Daily Contact Lens"
    order["line_items"][0]["product_type"] = "Contact Lenses"
    order["line_items"][0]["sku"] = "CL-1"
    res = shopify_ingest.ingest_shopify_order(
        wired["db"], order, topic="orders/create"
    )
    assert res["rx_pending"] is False


def test_reingest_does_not_duplicate_task_or_double_flag(wired, monkeypatch):
    """(d) re-ingest the SAME order -> duplicate, NO second task, still one flag."""
    import api.dependencies as deps

    monkeypatch.setattr(deps, "get_prescription_repository", lambda: _RxRepo())
    payload = _lens_order(8006)

    first = shopify_ingest.ingest_shopify_order(wired["db"], payload, topic="orders/create")
    second = shopify_ingest.ingest_shopify_order(wired["db"], payload, topic="orders/create")

    assert first["status"] == "created" and first["rx_pending"] is True
    assert second["status"] == "duplicate"

    orders = [d for d in wired["orders"].docs if d.get("shopify_order_id") == "8006"]
    assert len(orders) == 1  # single order doc (single flag)
    tasks = wired["db"].get_collection("tasks").docs
    hold_tasks = [t for t in tasks if t.get("order_id") == orders[0]["order_id"]]
    assert len(hold_tasks) == 1  # exactly ONE task despite the re-ingest


def test_out_of_range_power_still_created_reason_recorded(wired, monkeypatch):
    """(e) out-of-range power -> still CREATED, reason recorded (data error caught
    for staff, not dropped)."""
    import api.dependencies as deps

    # Valid Rx is on file so the ONLY problem is the bad power on the line.
    repo = _RxRepo(by_id={"RX-1": _valid_rx("RX-1", "555")})
    monkeypatch.setattr(deps, "get_prescription_repository", lambda: repo)

    # sph = +99.00 is far outside the -20..+20 clinical range.
    payload = _lens_order(8007, with_rx="RX-1", sph="99.00")
    res = shopify_ingest.ingest_shopify_order(wired["db"], payload, topic="orders/create")
    assert res["status"] == "created"
    assert res["rx_pending"] is True

    order = wired["orders"].find_one({"shopify_order_id": "8007"})
    assert "RX_POWER_OUT_OF_RANGE" in order["rx_hold_reasons"]
    assert "99" in order["rx_hold_reason"]


def test_expired_rx_lens_line_flagged(wired, monkeypatch):
    """An EXPIRED linked Rx on a lens line flags the order (online has no
    Store-Manager override -- it holds for the store to refresh the Rx)."""
    import api.dependencies as deps

    repo = _RxRepo(by_id={"RX-OLD": _expired_rx("RX-OLD", "555")})
    monkeypatch.setattr(deps, "get_prescription_repository", lambda: repo)

    res = shopify_ingest.ingest_shopify_order(
        wired["db"], _lens_order(8008, with_rx="RX-OLD"), topic="orders/create"
    )
    assert res["status"] == "created"
    assert res["rx_pending"] is True
    order = wired["orders"].find_one({"shopify_order_id": "8008"})
    assert "RX_NOT_FOUND" in order["rx_hold_reasons"]


def test_rx_powers_from_line_properties(wired, monkeypatch):
    """A lens line carrying powers via Shopify line-item `properties` is power-
    validated too (out-of-range -> flagged)."""
    import api.dependencies as deps

    repo = _RxRepo(by_id={"RX-1": _valid_rx("RX-1", "555")})
    monkeypatch.setattr(deps, "get_prescription_repository", lambda: repo)

    payload = _lens_order(
        8009,
        with_rx="RX-1",
        properties=[{"name": "cyl", "value": "9.99"}],  # out of -6..+6 range
    )
    res = shopify_ingest.ingest_shopify_order(wired["db"], payload, topic="orders/create")
    assert res["rx_pending"] is True
    order = wired["orders"].find_one({"shopify_order_id": "8009"})
    assert "RX_POWER_OUT_OF_RANGE" in order["rx_hold_reasons"]
