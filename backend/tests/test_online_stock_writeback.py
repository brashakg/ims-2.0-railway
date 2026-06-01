"""
IMS -> Shopify stock write-back tests (council B11 -- oversell guard)
====================================================================
IMS is the inventory master: an in-store sale pushes the reduced AVAILABLE
quantity (on_hand - safety_buffer, the absolute value) up to Shopify so the
website can't oversell. These tests pin:

  * the pushed quantity = on_hand - buffer, sent to the right variant + location
  * DISPATCH_MODE=off  -> NO live Shopify call (default == today, byte-identical)
  * DISPATCH_MODE=live -> a real inventorySetQuantities call is made
  * no Shopify mapping for a SKU -> skipped (no-op, not every product is online)
  * a setter EXCEPTION never propagates into the sale path
  * the GraphQL setter is gated on IMS_SHOPIFY_WRITES then DISPATCH_MODE

Everything is mocked: the BVI Postgres target resolver, the Mongo on-hand
lookup, and the Shopify GraphQL HTTP call. No DB / network is touched.
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

from agents import nexus_providers  # noqa: E402
from api.services import online_stock_writeback as wb  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


class _FakeResp:
    def __init__(self, status_code=200, json_body=None):
        self.status_code = status_code
        self._json = json_body if json_body is not None else {
            "data": {"inventorySetQuantities": {
                "inventoryAdjustmentGroup": {"createdAt": "now", "reason": "correction"},
                "userErrors": [],
            }}
        }
        self.text = "ok"

    def json(self):
        return self._json


class _FakeAsyncClient:
    """Stand-in for httpx.AsyncClient that records the GraphQL call and returns
    a canned success (or whatever is injected)."""
    calls = []
    resp = None

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        _FakeAsyncClient.calls.append({"url": url, "json": json})
        return _FakeAsyncClient.resp or _FakeResp()


@pytest.fixture(autouse=True)
def _reset_fake():
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.resp = None
    yield


def _live_shopify(monkeypatch):
    """Enable writes + live dispatch + creds, and stub the HTTP client."""
    monkeypatch.setenv("IMS_SHOPIFY_WRITES", "1")
    monkeypatch.setattr(nexus_providers, "dispatch_mode", lambda: "live")
    monkeypatch.setattr(
        nexus_providers, "_load_integration_config",
        lambda db, t: {"shop_url": "test.myshopify.com", "access_token": "tok"},
    )
    monkeypatch.setattr(nexus_providers.httpx, "AsyncClient", _FakeAsyncClient)


# ---------------------------------------------------------------------------
# pure quantity math (reuses the canonical recommend_allocation)
# ---------------------------------------------------------------------------

def test_pushed_qty_is_on_hand_minus_buffer():
    from api.services import stock_allocation

    assert stock_allocation.recommend_allocation(10, 0) == 10
    assert stock_allocation.recommend_allocation(10, 1) == 9
    assert stock_allocation.recommend_allocation(0, 1) == 0   # floored at 0
    assert stock_allocation.recommend_allocation(3, 5) == 0   # never negative


def test_skus_from_items_skips_service_and_virtual_lines():
    items = [
        {"sku": "SP-1", "product_id": "p1", "item_type": "PRODUCT"},
        {"sku": "EXAM", "product_id": "c1", "item_type": "EYE_TEST"},     # service
        {"sku": "LENS", "product_id": "lens-abc"},                         # virtual
        {"sku": "", "product_id": "p2"},                                   # no sku
        {"sku": "SP-1", "product_id": "p1"},                               # dup
    ]
    assert wb.skus_from_items(items) == ["SP-1"]


# ---------------------------------------------------------------------------
# the GraphQL setter (gating + payload)
# ---------------------------------------------------------------------------

def test_setter_noop_when_writes_disabled(monkeypatch):
    monkeypatch.delenv("IMS_SHOPIFY_WRITES", raising=False)
    res = _run(nexus_providers.shopify_set_inventory_available(
        None, "gid://shopify/InventoryItem/1", "gid://shopify/Location/1", 5))
    assert res.ok is True
    assert "RETIRED" in (res.notes or "")
    assert _FakeAsyncClient.calls == []  # never hit the network


def test_setter_simulated_when_dispatch_off(monkeypatch):
    monkeypatch.setenv("IMS_SHOPIFY_WRITES", "1")
    monkeypatch.setattr(nexus_providers, "dispatch_mode", lambda: "off")
    monkeypatch.setattr(nexus_providers.httpx, "AsyncClient", _FakeAsyncClient)
    res = _run(nexus_providers.shopify_set_inventory_available(
        None, "123", "456", 7))
    assert res.ok is True
    assert res.items_synced == 0
    assert "SIMULATED" in (res.notes or "")
    assert _FakeAsyncClient.calls == []  # NO live call in off mode


def test_setter_live_call_sends_absolute_qty(monkeypatch):
    _live_shopify(monkeypatch)
    res = _run(nexus_providers.shopify_set_inventory_available(
        None, "123", "456", 4))
    assert res.ok is True
    assert res.items_synced == 1
    assert len(_FakeAsyncClient.calls) == 1
    sent = _FakeAsyncClient.calls[0]["json"]
    q = sent["variables"]["input"]["quantities"][0]
    # bare ids promoted to GIDs; absolute quantity carried through.
    assert q["inventoryItemId"] == "gid://shopify/InventoryItem/123"
    assert q["locationId"] == "gid://shopify/Location/456"
    assert q["quantity"] == 4
    assert sent["variables"]["input"]["name"] == "available"


def test_setter_reports_user_errors_as_failure(monkeypatch):
    _live_shopify(monkeypatch)
    _FakeAsyncClient.resp = _FakeResp(json_body={
        "data": {"inventorySetQuantities": {
            "inventoryAdjustmentGroup": None,
            "userErrors": [{"field": "quantities", "message": "bad item"}],
        }}
    })
    res = _run(nexus_providers.shopify_set_inventory_available(None, "1", "2", 3))
    assert res.ok is False
    assert "userErrors" in (res.error or "")


# ---------------------------------------------------------------------------
# the orchestrator (sale -> compute qty -> push the right variant)
# ---------------------------------------------------------------------------

def test_sale_pushes_on_hand_minus_buffer_to_mapped_variant(monkeypatch):
    _live_shopify(monkeypatch)
    monkeypatch.setenv("ONLINE_STOCK_SAFETY_BUFFER", "1")
    # SKU -> Shopify target (as if resolved from the BVI Postgres).
    monkeypatch.setattr(
        wb, "_resolve_db", lambda db: object())  # truthy db; collections mocked below
    import api.services.online_catalog as oc
    monkeypatch.setattr(oc, "ecommerce_db_configured", lambda: True)
    monkeypatch.setattr(
        oc, "online_variant_targets_for_skus",
        lambda skus: {"SP-1": {"inventory_item_id": "999", "location_id": "loc-1"}},
    )
    # on-hand AFTER the sale = 3 units left for SP-1.
    monkeypatch.setattr(wb, "_on_hand_for_skus", lambda db, skus, store: {"SP-1": 3})
    monkeypatch.setattr(wb, "_record_run", lambda db, summary: None)

    summary = _run(wb.writeback_skus(object(), ["SP-1"], "store-1"))
    assert summary["pushed"] == 1
    assert summary["failed"] == 0
    # 3 on-hand - 1 buffer = 2 pushed.
    q = _FakeAsyncClient.calls[0]["json"]["variables"]["input"]["quantities"][0]
    assert q["quantity"] == 2
    assert q["inventoryItemId"] == "gid://shopify/InventoryItem/999"


def test_no_mapping_is_skipped(monkeypatch):
    _live_shopify(monkeypatch)
    import api.services.online_catalog as oc
    monkeypatch.setattr(oc, "ecommerce_db_configured", lambda: True)
    monkeypatch.setattr(oc, "online_variant_targets_for_skus", lambda skus: {})
    monkeypatch.setattr(wb, "_on_hand_for_skus", lambda db, skus, store: {})

    summary = _run(wb.writeback_skus(object(), ["NOT-ONLINE"], "store-1"))
    assert summary["pushed"] == 0
    assert summary["skipped_no_mapping"] == 1
    assert _FakeAsyncClient.calls == []  # nothing pushed


def test_dispatch_off_makes_no_live_call_via_orchestrator(monkeypatch):
    # Writes enabled but DISPATCH_MODE=off -> setter returns SIMULATED, NO HTTP.
    monkeypatch.setenv("IMS_SHOPIFY_WRITES", "1")
    monkeypatch.setattr(nexus_providers, "dispatch_mode", lambda: "off")
    monkeypatch.setattr(nexus_providers.httpx, "AsyncClient", _FakeAsyncClient)
    import api.services.online_catalog as oc
    monkeypatch.setattr(oc, "ecommerce_db_configured", lambda: True)
    monkeypatch.setattr(
        oc, "online_variant_targets_for_skus",
        lambda skus: {"SP-1": {"inventory_item_id": "999", "location_id": "loc-1"}},
    )
    monkeypatch.setattr(wb, "_on_hand_for_skus", lambda db, skus, store: {"SP-1": 3})
    monkeypatch.setattr(wb, "_record_run", lambda db, summary: None)

    summary = _run(wb.writeback_skus(object(), ["SP-1"], "store-1"))
    assert summary["pushed"] == 0
    assert summary["simulated"] == 1
    assert _FakeAsyncClient.calls == []  # byte-identical to today: no live write


def test_setter_exception_does_not_propagate(monkeypatch):
    # The orchestrator must swallow a setter raise and keep going.
    import api.services.online_catalog as oc
    monkeypatch.setattr(oc, "ecommerce_db_configured", lambda: True)
    monkeypatch.setattr(
        oc, "online_variant_targets_for_skus",
        lambda skus: {"SP-1": {"inventory_item_id": "999", "location_id": "loc-1"}},
    )
    monkeypatch.setattr(wb, "_on_hand_for_skus", lambda db, skus, store: {"SP-1": 3})
    monkeypatch.setattr(wb, "_record_run", lambda db, summary: None)

    async def _boom(*a, **k):
        raise RuntimeError("shopify exploded")

    monkeypatch.setattr(nexus_providers, "shopify_set_inventory_available", _boom)
    # Patch the symbol the orchestrator imports lazily, too.
    monkeypatch.setattr(
        "agents.nexus_providers.shopify_set_inventory_available", _boom, raising=False
    )

    summary = _run(wb.writeback_skus(object(), ["SP-1"], "store-1"))
    assert summary["failed"] == 1   # recorded, not raised
    assert summary["pushed"] == 0


def test_after_sale_never_raises_into_sale_path(monkeypatch):
    """writeback_after_sale is the POS hook: even if EVERYTHING under it blows
    up, it must return None and never raise (the sale already happened)."""
    def _explode(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(wb, "skus_from_items", _explode)
    # Should not raise.
    assert wb.writeback_after_sale(None, [{"sku": "X"}], "store-1") is None


def test_after_sale_dispatches_for_sold_skus(monkeypatch):
    """The POS hook schedules a push for the real sold SKUs (fire-and-forget,
    inline in this sync test context)."""
    captured = {}

    async def _fake_writeback(db, skus, store_id, source="sale", safety_buffer=None):
        captured["skus"] = skus
        captured["store_id"] = store_id
        captured["source"] = source
        return {"pushed": 0}

    monkeypatch.setattr(wb, "writeback_skus", _fake_writeback)
    items = [
        {"sku": "SP-1", "product_id": "p1", "item_type": "PRODUCT"},
        {"sku": "EXAM", "product_id": "c1", "item_type": "EYE_TEST"},
    ]
    wb.writeback_after_sale(None, items, "store-9")
    assert captured["skus"] == ["SP-1"]
    assert captured["store_id"] == "store-9"
    assert captured["source"] == "sale"
