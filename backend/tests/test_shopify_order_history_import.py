"""
IMS 2.0 - Shopify ORDER-HISTORY import tests (historical=True bypass)
====================================================================
scripts/import_shopify_order_history.py replays the Shopify back-catalogue through
the SAME api.services.online_order_mapper.map_shopify_order the live webhook path
uses, but with historical=True. These tests pin the load-bearing guarantees of
that new mode:

  Terminal, settled, date-preserving
    - a historical order is CREATED tagged channel='ONLINE' + historical=True, in a
      TERMINAL status (DELIVERED), fully settled (amount_paid==grand_total,
      balance_due==0), carrying its REAL Shopify order date (not import time), and
      with NO fresh GST invoice serial (invoice_number is None -- a back-dated order
      must never consume the live per-FY sequence).

  NO live side effects (the hard safety contract)
    - historical=True fires NONE of: Rx flag-and-hold evaluation, Rx-hold task,
      inventory decrement, oversell write-back, or system-task creation
      (loyalty / messaging have no hook in this path at all).
    - CONTROL: the live path (historical=False) DOES fire those hooks, proving the
      bypass is real and the live webhook path is unchanged.

  Customer LINK, never duplicate
    - an already-imported buyer is MATCHED (by mobile) and the order carries that
      customer_id; NO new customer is minted.
    - an unmatched buyer becomes a guest sale (customer_id None), still no create.

  Idempotent
    - re-importing the same Shopify order does NOT create a 2nd IMS order.

Pure: an in-memory fake DB + a real OrderRepository/CustomerRepository over fake
collections exercise the real _build_invoice_gst_split (no network, no real Mongo).
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test_x")
os.environ["GST_PRICING_MODE"] = "inclusive"
os.environ["ONLINE_STORE_ID"] = "BV-ONLINE-01"

from api.services import online_order_mapper, shopify_ingest


# ---------------------------------------------------------------------------
# Minimal in-memory Mongo emulator (only what the mapper + ingest touch).
# ---------------------------------------------------------------------------


class _DuplicateKeyError(Exception):
    pass


def _match(doc, filter_) -> bool:
    if not filter_:
        return True
    for k, expected in filter_.items():
        if k == "$or":
            if not any(_match(doc, sub) for sub in expected):
                return False
            continue
        actual = doc.get(k)
        if isinstance(expected, dict):
            for op, op_val in expected.items():
                if op == "$type":
                    if actual is None:
                        return False
                elif op == "$gte":
                    if actual is None or actual < op_val:
                        return False
                elif op == "$lte":
                    if actual is None or actual > op_val:
                        return False
                else:
                    return False
        else:
            if actual != expected:
                return False
    return True


class FakeCollection:
    def __init__(self, name: str, database: "FakeDB"):
        self._name = name
        self.database = database
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
        return iter([dict(d) for d in self.docs if _match(d, filter_)])

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


class Spy:
    """A callable that records whether/when it was invoked."""

    def __init__(self, ret=None):
        self.calls = 0
        self.ret = ret

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return self.ret


@pytest.fixture
def wired(monkeypatch):
    db = FakeDB()

    from database.repositories.order_repository import OrderRepository
    from database.repositories.customer_repository import CustomerRepository

    order_repo = OrderRepository(db.get_collection("orders"))
    customer_repo = CustomerRepository(db.get_collection("customers"))

    class _StoreRepo:
        def find_by_id(self, _store_id):
            return {"gstin": "", "state_code": "20"}

        def find_active(self, filter=None):
            return [{"store_id": "BV-ONLINE-01", "state_code": "20"}]

    import api.dependencies as deps

    monkeypatch.setattr(deps, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(deps, "get_product_repository", lambda: None)
    monkeypatch.setattr(deps, "get_store_repository", lambda: _StoreRepo())
    monkeypatch.setattr(deps, "get_customer_repository", lambda: customer_repo)

    return {
        "db": db,
        "orders": db.get_collection("orders"),
        "customers": db.get_collection("customers"),
        "customer_repo": customer_repo,
    }


@pytest.fixture
def spies(monkeypatch):
    """Spy out every live side effect the historical path must NOT trigger."""
    import api.services.online_rx_hold as rx_mod
    import api.services.online_stock_writeback as wb_mod
    import api.services.task_triggers as task_mod

    s = {
        "claim": Spy(ret=(0, [])),
        "writeback": Spy(),
        "rx_eval": Spy(ret={"rx_pending": False, "reasons": [], "lines": [], "detail": ""}),
        "rx_task": Spy(),
        "system_task": Spy(),
    }
    monkeypatch.setattr(shopify_ingest, "_claim_units_multistore", s["claim"])
    monkeypatch.setattr(wb_mod, "writeback_after_sale", s["writeback"])
    monkeypatch.setattr(rx_mod, "evaluate_rx_hold", s["rx_eval"])
    monkeypatch.setattr(rx_mod, "raise_rx_hold_task", s["rx_task"])
    monkeypatch.setattr(task_mod, "create_system_task", s["system_task"])
    return s


def _hist_order(order_id, *, created_at="2023-05-10T09:30:00Z", price="999.00",
                financial="paid", phone="+91 98765 43210", email="buyer@example.com"):
    """A Shopify order payload as pulled from the Admin REST API (paid frame)."""
    return {
        "id": order_id,
        "name": f"#{order_id}",
        "created_at": created_at,
        "financial_status": financial,
        "fulfillment_status": "fulfilled",
        "total_price": price,
        "email": email,
        "phone": phone,
        "customer": {"id": 555, "first_name": "Ravi", "last_name": "Kumar", "phone": phone},
        "shipping_address": {"province": "20", "province_code": "20"},
        "line_items": [
            {
                "id": 9001,
                "product_id": 7001,
                "variant_id": 999001,
                "title": "Ray-Ban Frame RB1234",
                "product_type": "Frames",
                "sku": "RB-1234",
                "quantity": 1,
                "price": price,
                "total_discount": "0.00",
            }
        ],
        # Stamp the online store bucket so attribution is deterministic in the test.
        "_ims_online_store_id": "BV-ONLINE-01",
    }


# ---------------------------------------------------------------------------
# Terminal, settled, date-preserving, no serial
# ---------------------------------------------------------------------------


def test_historical_import_creates_terminal_settled_order(wired):
    res = online_order_mapper.map_shopify_order(
        _hist_order(20001), wired["db"], topic="orders/create", historical=True
    )
    assert res["status"] == "created"
    assert res.get("historical") is True
    assert res.get("invoice_number") is None

    order = wired["orders"].find_one({"shopify_order_id": "20001"})
    assert order is not None
    assert order["channel"] == "ONLINE"
    assert order["source"] == "shopify"
    assert order["historical"] is True
    assert order["import_source"] == "shopify_order_history"
    # Terminal status + settled payment (no phantom receivable).
    assert order["status"] == "DELIVERED"
    assert order["payment_status"] == "PAID"
    assert order["amount_paid"] == order["grand_total"]
    assert order["balance_due"] == 0.0
    # NO fresh GST serial consumed for a back-dated order.
    assert order["invoice_number"] is None
    # Not held.
    assert order["rx_pending"] is False
    assert order["fulfillment_hold"] is False
    # GST-inclusive total is the all-in price the buyer paid; tax math reused.
    assert order["grand_total"] == 999.0
    assert round(order["tax_totals"]["taxable"] + order["tax_totals"]["tax"], 2) == 999.0


def test_historical_preserves_shopify_order_date(wired):
    online_order_mapper.map_shopify_order(
        _hist_order(20002, created_at="2022-11-01T12:00:00Z"),
        wired["db"],
        topic="orders/create",
        historical=True,
    )
    order = wired["orders"].find_one({"shopify_order_id": "20002"})
    # Order date = the Shopify created_at (2022), NOT the import time.
    assert str(order["created_at"]).startswith("2022-11-01")
    assert str(order["invoice_date"]).startswith("2022-11-01")


def test_refunded_history_lands_refunded_status(wired):
    online_order_mapper.map_shopify_order(
        _hist_order(20003, financial="refunded"),
        wired["db"],
        topic="orders/create",
        historical=True,
    )
    order = wired["orders"].find_one({"shopify_order_id": "20003"})
    assert order["status"] == "REFUNDED"
    assert order["payment_status"] == "REFUNDED"


# ---------------------------------------------------------------------------
# NO live side effects (hard safety) + live-path control
# ---------------------------------------------------------------------------


def test_historical_fires_no_side_effects(wired, spies):
    res = online_order_mapper.map_shopify_order(
        _hist_order(20010), wired["db"], topic="orders/create", historical=True
    )
    assert res["status"] == "created"
    # None of the live hooks may fire for a settled back-catalogue order.
    assert spies["rx_eval"].calls == 0, "no Rx flag-and-hold evaluation"
    assert spies["rx_task"].calls == 0, "no Rx-hold follow-up task"
    assert spies["claim"].calls == 0, "no inventory decrement"
    assert spies["writeback"].calls == 0, "no oversell write-back"
    assert spies["system_task"].calls == 0, "no system-task creation"


def test_live_path_still_fires_side_effects(wired, spies):
    # CONTROL: the same order via the LIVE path (historical=False) DOES run the
    # hooks -- proving the historical bypass is real, not a broken code path.
    res = online_order_mapper.map_shopify_order(
        _hist_order(20011), wired["db"], topic="orders/create", historical=False
    )
    assert res["status"] == "created"
    assert spies["rx_eval"].calls >= 1, "live path evaluates Rx flag-and-hold"
    assert spies["writeback"].calls >= 1, "live path pushes stock write-back"


# ---------------------------------------------------------------------------
# Customer LINK, never duplicate
# ---------------------------------------------------------------------------


def test_historical_links_existing_customer_without_duplicate(wired):
    wired["customer_repo"].create(
        {"customer_id": "CUST-IMPORTED", "name": "Ravi K", "mobile": "9876543210"}
    )
    before = len(wired["customers"].docs)

    res = online_order_mapper.map_shopify_order(
        _hist_order(20020), wired["db"], topic="orders/create", historical=True
    )
    assert res["customer_id"] == "CUST-IMPORTED"
    order = wired["orders"].find_one({"shopify_order_id": "20020"})
    assert order["customer_id"] == "CUST-IMPORTED"
    assert order.get("is_guest_order") is False
    assert len(wired["customers"].docs) == before, "no duplicate customer minted"


def test_historical_unmatched_buyer_is_guest_no_customer_created(wired):
    # A buyer with no matching IMS customer + no usable Indian mobile / email.
    payload = _hist_order(20021, phone="", email="")
    payload["customer"] = {"id": 999, "first_name": "Foreign", "last_name": "Buyer"}
    before = len(wired["customers"].docs)

    res = online_order_mapper.map_shopify_order(
        payload, wired["db"], topic="orders/create", historical=True
    )
    assert res["status"] == "created"
    assert res["customer_id"] is None
    order = wired["orders"].find_one({"shopify_order_id": "20021"})
    assert order["customer_id"] is None
    assert order["is_guest_order"] is True
    assert len(wired["customers"].docs) == before, "historical import never creates a customer"


def test_historical_matches_by_shopify_customer_id(wired):
    # The import stamped shopify_customer_id on the customer; a buyer with a
    # non-Indian phone still links via that id.
    wired["customers"].insert_one(
        {"customer_id": "CUST-SHOP", "name": "X", "shopify_customer_id": "555"}
    )
    before = len(wired["customers"].docs)
    payload = _hist_order(20022, phone="+1 415 555 0000", email="")
    res = online_order_mapper.map_shopify_order(
        payload, wired["db"], topic="orders/create", historical=True
    )
    assert res["customer_id"] == "CUST-SHOP"
    assert len(wired["customers"].docs) == before


# ---------------------------------------------------------------------------
# Idempotent
# ---------------------------------------------------------------------------


def test_historical_reimport_is_idempotent(wired):
    first = online_order_mapper.map_shopify_order(
        _hist_order(20030), wired["db"], topic="orders/create", historical=True
    )
    assert first["status"] == "created"
    second = online_order_mapper.map_shopify_order(
        _hist_order(20030), wired["db"], topic="orders/create", historical=True
    )
    assert second["status"] == "duplicate"
    online = [d for d in wired["orders"].docs if d.get("shopify_order_id") == "20030"]
    assert len(online) == 1, "count-once: still exactly one IMS order"
    # The historical order's terminal status is NOT re-synced by a replay.
    assert online[0]["status"] == "DELIVERED"
