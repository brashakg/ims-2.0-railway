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
  * BUT an ONLINE SKU with no mapping is a GUARD GAP -> loud alert + SYSTEM task
  * a setter EXCEPTION never propagates into the sale path
  * the GraphQL setter is gated on IMS_SHOPIFY_WRITES then DISPATCH_MODE

Everything is mocked: the IMS Mongo target resolver (online_catalog), the
on-hand lookup, and the Shopify GraphQL HTTP call. No DB / network is touched.
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
        lambda db, t, storefront_id=None: {"shop_url": "test.myshopify.com", "access_token": "tok"},
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
    # SKU -> Shopify target (as if resolved from the IMS Mongo mapping).
    monkeypatch.setattr(
        wb, "_resolve_db", lambda db: object())  # truthy db; collections mocked below
    import api.services.online_catalog as oc
    monkeypatch.setattr(oc, "online_mapping_available", lambda db: True)
    monkeypatch.setattr(
        oc, "online_variant_targets_for_skus",
        lambda db, skus: {"SP-1": {"inventory_item_id": "999", "location_id": "loc-1"}},
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
    monkeypatch.setattr(oc, "online_mapping_available", lambda db: True)
    monkeypatch.setattr(oc, "online_variant_targets_for_skus", lambda db, skus: {})
    # The SKU is NOT listed online -> a silent, correct no-op (no alert).
    monkeypatch.setattr(oc, "online_status_for_skus", lambda db, skus: {})
    monkeypatch.setattr(wb, "_on_hand_for_skus", lambda db, skus, store: {})

    summary = _run(wb.writeback_skus(object(), ["NOT-ONLINE"], "store-1"))
    assert summary["pushed"] == 0
    assert summary["skipped_no_mapping"] == 1
    assert summary["unmapped_online"] == 0  # not online -> no guard-gap alert
    assert _FakeAsyncClient.calls == []  # nothing pushed


def test_unmapped_but_sellable_online_sku_alerts_loudly(monkeypatch):
    """OS-015 guard-gap: a sold SKU that is SELLABLE online but has no Shopify
    inventory mapping must alert LOUDLY (summary flag + deduped SYSTEM task),
    never fail soft-silent."""
    _live_shopify(monkeypatch)
    import api.services.online_catalog as oc
    monkeypatch.setattr(oc, "online_mapping_available", lambda db: True)
    monkeypatch.setattr(oc, "online_variant_targets_for_skus", lambda db, skus: {})
    monkeypatch.setattr(
        oc, "online_status_for_skus",
        lambda db, skus: {
            "SP-ONLINE": {
                "online": True,
                "sellable_online": True,
                "online_stock": None,
                "status": "PUBLISHED",
            }
        },
    )
    filed = {}
    monkeypatch.setattr(
        wb, "_file_guard_gap_task", lambda db, skus: filed.setdefault("skus", skus)
    )
    recorded = {}
    monkeypatch.setattr(
        wb, "_record_run", lambda db, summary: recorded.update(summary)
    )

    summary = _run(wb.writeback_skus(object(), ["SP-ONLINE", "SP-OFFLINE"], "s1"))
    assert summary["skipped_no_mapping"] == 2
    assert summary["unmapped_online"] == 1      # only the sellable SKU alerts
    assert filed["skus"] == ["SP-ONLINE"]       # SYSTEM task filed for it
    assert recorded.get("unmapped_online") == 1  # and the run was recorded
    assert _FakeAsyncClient.calls == []          # nothing pushed


def test_unmapped_draft_sku_never_alerts(monkeypatch):
    """Fix-round P1: an unpurchasable Shopify DRAFT (gid present, status DRAFT
    -- e.g. the 2,032 staged drafts) canNOT oversell, so selling its in-store
    stock must NOT fire the guard-gap alarm or file a task."""
    _live_shopify(monkeypatch)
    import api.services.online_catalog as oc
    monkeypatch.setattr(oc, "online_mapping_available", lambda db: True)
    monkeypatch.setattr(oc, "online_variant_targets_for_skus", lambda db, skus: {})
    monkeypatch.setattr(
        oc, "online_status_for_skus",
        lambda db, skus: {
            "SP-DRAFT": {
                "online": True,            # display flag: pushed as a draft
                "sellable_online": False,  # but customers cannot buy it
                "online_stock": None,
                "status": "DRAFT",
            }
        },
    )
    filed = {}
    monkeypatch.setattr(
        wb, "_file_guard_gap_task", lambda db, skus: filed.setdefault("skus", skus)
    )

    summary = _run(wb.writeback_skus(object(), ["SP-DRAFT"], "s1"))
    assert summary["skipped_no_mapping"] == 1
    assert summary["unmapped_online"] == 0   # draft -> no guard gap
    assert filed == {}                        # no task filed
    assert _FakeAsyncClient.calls == []


def test_guard_gap_task_merges_new_skus_into_open_task(monkeypatch):
    """Fix-round P1: while a guard-gap task is OPEN, a NEW distinct gap must
    not vanish behind the dedupe -- the new SKUs are $addToSet-merged into the
    open task's payload."""
    import api.services.task_triggers as tt

    # Simulate "a task with this source_ref is already open" -> dedupe None.
    monkeypatch.setattr(tt, "create_system_task", lambda *a, **k: None)

    calls = {}

    class _TasksColl:
        def update_one(self, flt, update):
            calls["flt"] = flt
            calls["update"] = update

    class _Db:
        def get_collection(self, name):
            return _TasksColl() if name == "tasks" else None

    wb._file_guard_gap_task(_Db(), ["SKU-NEW-1", "SKU-NEW-2"])
    assert calls["flt"]["source_ref"] == wb._GUARD_GAP_TASK_REF
    assert calls["flt"]["status"]["$in"] == ["OPEN", "IN_PROGRESS", "ESCALATED"]
    assert calls["update"]["$addToSet"]["payload.skus"]["$each"] == [
        "SKU-NEW-1",
        "SKU-NEW-2",
    ]


# ---------------------------------------------------------------------------
# POOLED availability (fix-round P0) + unknown-on-hand safety (fix-round P1)
# ---------------------------------------------------------------------------


class _SpineProducts:
    """products collection: sku -> product_id resolution."""

    def __init__(self, rows):
        self._rows = rows

    def find(self, query=None, _proj=None, *_a, **_k):
        skus = ((query or {}).get("sku") or {}).get("$in") or []
        return iter([dict(r) for r in self._rows if r.get("sku") in skus])


class _SpineStock:
    """stock_units collection: honours the aggregate's product_id + OPTIONAL
    store_id match, so the test proves the write-back does NOT scope the
    on-hand math to the selling store."""

    def __init__(self, rows):
        self._rows = rows

    def aggregate(self, pipeline):
        match = {}
        for stage in pipeline:
            if "$match" in stage:
                match = stage["$match"]
        pid_in = ((match.get("product_id") or {}).get("$in")) or []
        store = match.get("store_id")
        counts = {}
        for r in self._rows:
            if r.get("product_id") not in pid_in:
                continue
            if store and r.get("store_id") != store:
                continue
            if r.get("status") not in (None, "AVAILABLE"):
                continue
            pid = r.get("product_id")
            counts[pid] = counts.get(pid, 0) + 1
        return iter([{"_id": pid, "n": n} for pid, n in counts.items()])


class _SpineDb:
    is_connected = True

    def __init__(self, products, stock):
        self._colls = {"products": products, "stock_units": stock}

    def get_collection(self, name):
        return self._colls.get(name)


def test_sale_pushes_pooled_all_store_on_hand_not_selling_store(monkeypatch):
    """Fix-round P0: 1 unit at Store A + 9 at Store B; the sale happens at A.
    The pushed absolute quantity must be the POOLED all-store on-hand (10),
    NEVER the selling store's remainder -- the online store sells from every
    shop combined, so scoping to A would zero/underlist a stocked product."""
    _live_shopify(monkeypatch)
    monkeypatch.delenv("ONLINE_STOCK_SAFETY_BUFFER", raising=False)
    import api.services.online_catalog as oc
    monkeypatch.setattr(oc, "online_mapping_available", lambda db: True)
    monkeypatch.setattr(
        oc, "online_variant_targets_for_skus",
        lambda db, skus: {"SP-1": {"inventory_item_id": "999", "location_id": "loc-1"}},
    )
    monkeypatch.setattr(wb, "_safety_buffer", lambda db: 0)
    monkeypatch.setattr(wb, "_record_run", lambda db, summary: None)

    db = _SpineDb(
        _SpineProducts([{"sku": "SP-1", "product_id": "P1"}]),
        _SpineStock(
            [{"product_id": "P1", "store_id": "STORE-A", "status": "AVAILABLE"}]
            + [
                {"product_id": "P1", "store_id": "STORE-B", "status": "AVAILABLE"}
                for _ in range(9)
            ]
        ),
    )
    # Sale at STORE-A (which holds only 1 unit).
    summary = _run(wb.writeback_skus(db, ["SP-1"], "STORE-A"))
    assert summary["pushed"] == 1
    assert summary["store_id"] == "STORE-A"  # context only
    q = _FakeAsyncClient.calls[0]["json"]["variables"]["input"]["quantities"][0]
    assert q["quantity"] == 10  # POOLED (1 at A + 9 at B), not A's remainder


class _RaisingStock:
    """stock_units whose aggregate raises IMMEDIATELY (Mongo blip)."""

    def aggregate(self, _pipeline):
        raise RuntimeError("mongo aggregate blew up")


class _MidFailStock:
    """stock_units whose aggregate yields ONE row then raises mid-iteration
    (cursor death) -- the partial result must be discarded, never trusted."""

    def aggregate(self, _pipeline):
        def _gen():
            yield {"_id": "P1", "n": 3}
            raise RuntimeError("cursor died mid-iteration")

        return _gen()


class _SyncRunsColl:
    def __init__(self):
        self.rows = []

    def insert_one(self, doc):
        self.rows.append(doc)


def _raising_db(stock, sync_runs):
    """_SpineDb + a sync_runs capture collection so the REAL _record_run runs."""
    db = _SpineDb(
        _SpineProducts(
            [{"sku": "SP-1", "product_id": "P1"}, {"sku": "SP-2", "product_id": "P2"}]
        ),
        stock,
    )
    db._colls["sync_runs"] = sync_runs
    return db


def test_aggregate_raise_aborts_batch_never_writes_zero(monkeypatch):
    """Round-2 P1: a stock_units aggregate that RAISES must flow into the
    batch abort (not default every spine-resolved SKU to 0 as the swallowed
    inventory helper did). Zero Shopify calls, skipped_no_onhand ==
    len(targets), not-ok sync_runs row."""
    _live_shopify(monkeypatch)
    import api.services.online_catalog as oc
    monkeypatch.setattr(oc, "online_mapping_available", lambda db: True)
    monkeypatch.setattr(
        oc, "online_variant_targets_for_skus",
        lambda db, skus: {
            "SP-1": {"inventory_item_id": "991", "location_id": "loc-1"},
            "SP-2": {"inventory_item_id": "992", "location_id": "loc-1"},
        },
    )
    sync_runs = _SyncRunsColl()
    db = _raising_db(_RaisingStock(), sync_runs)

    summary = _run(wb.writeback_skus(db, ["SP-1", "SP-2"], "STORE-A"))
    assert _FakeAsyncClient.calls == []          # ZERO Shopify writes
    assert summary["pushed"] == 0
    assert summary["skipped_no_onhand"] == 2     # == len(targets)
    assert len(sync_runs.rows) == 1              # run recorded...
    assert sync_runs.rows[0]["ok"] is False      # ...as NOT ok
    assert "on-hand UNKNOWN" in sync_runs.rows[0]["error"]


def test_aggregate_mid_iteration_raise_discards_partial_and_aborts(monkeypatch):
    """Round-2 P1 (worse mode): the cursor yields one healthy row THEN dies.
    The partial result must be discarded -- surviving pids must not look
    healthy while missing pids silently get 0. Also pins the abort-branch
    rider: an unmapped sellable-online SKU in the same batch STILL alerts."""
    _live_shopify(monkeypatch)
    import api.services.online_catalog as oc
    monkeypatch.setattr(oc, "online_mapping_available", lambda db: True)
    monkeypatch.setattr(
        oc, "online_variant_targets_for_skus",
        lambda db, skus: {
            "SP-1": {"inventory_item_id": "991", "location_id": "loc-1"},
            "SP-2": {"inventory_item_id": "992", "location_id": "loc-1"},
        },
    )
    # A third sold SKU with NO mapping that IS sellable online -> must alert
    # even though the batch aborts (round-2 rider).
    monkeypatch.setattr(
        oc, "online_status_for_skus",
        lambda db, skus: {
            "SP-GAP": {
                "online": True,
                "sellable_online": True,
                "online_stock": None,
                "status": "PUBLISHED",
            }
        },
    )
    filed = {}
    monkeypatch.setattr(
        wb, "_file_guard_gap_task", lambda db, skus: filed.setdefault("skus", skus)
    )
    sync_runs = _SyncRunsColl()
    db = _raising_db(_MidFailStock(), sync_runs)

    summary = _run(wb.writeback_skus(db, ["SP-1", "SP-2", "SP-GAP"], "STORE-A"))
    assert _FakeAsyncClient.calls == []          # ZERO Shopify writes
    assert summary["pushed"] == 0
    assert summary["skipped_no_onhand"] == 2     # == len(targets)
    assert summary["skipped_no_mapping"] == 1    # SP-GAP
    assert summary["unmapped_online"] == 1       # rider: gap still alerted
    assert filed["skus"] == ["SP-GAP"]
    assert len(sync_runs.rows) == 1
    assert sync_runs.rows[0]["ok"] is False


def test_unknown_on_hand_aborts_batch_never_writes_zero(monkeypatch):
    """Fix-round P1: when the on-hand lookup returns {} for a non-empty target
    set (Mongo blip / missing spine rows), the batch must ABORT with a not-ok
    run -- writing absolute 0 would delist in-stock products."""
    _live_shopify(monkeypatch)
    import api.services.online_catalog as oc
    monkeypatch.setattr(oc, "online_mapping_available", lambda db: True)
    monkeypatch.setattr(
        oc, "online_variant_targets_for_skus",
        lambda db, skus: {
            "SP-1": {"inventory_item_id": "991", "location_id": "loc-1"},
            "SP-2": {"inventory_item_id": "992", "location_id": "loc-1"},
        },
    )
    monkeypatch.setattr(wb, "_on_hand_for_skus", lambda db, skus, store: {})
    recorded = {}
    monkeypatch.setattr(wb, "_record_run", lambda db, summary: recorded.update(summary))

    summary = _run(wb.writeback_skus(object(), ["SP-1", "SP-2"], "s1"))
    assert _FakeAsyncClient.calls == []          # ZERO Shopify writes
    assert summary["pushed"] == 0
    assert summary["skipped_no_onhand"] == 2
    assert recorded.get("skipped_no_onhand") == 2  # run recorded (not-ok)


def test_record_run_flags_unknown_on_hand_as_not_ok():
    """The abort path's sync_runs row is NOT ok and says why."""
    rows = []

    class _Coll:
        def insert_one(self, doc):
            rows.append(doc)

    class _Db:
        def get_collection(self, name):
            return _Coll()

    wb._record_run(
        _Db(),
        {"pushed": 0, "failed": 0, "unmapped_online": 0,
         "skipped_no_onhand": 2, "source": "sale"},
    )
    assert len(rows) == 1
    assert rows[0]["ok"] is False
    assert "on-hand UNKNOWN" in rows[0]["error"]


def test_partially_unknown_on_hand_skips_only_missing_sku(monkeypatch):
    """A SKU absent from the on-hand map is skipped; a SKU PRESENT with 0
    still pushes 0 (that IS the oversell guard)."""
    _live_shopify(monkeypatch)
    import api.services.online_catalog as oc
    monkeypatch.setattr(oc, "online_mapping_available", lambda db: True)
    monkeypatch.setattr(
        oc, "online_variant_targets_for_skus",
        lambda db, skus: {
            "SP-ZERO": {"inventory_item_id": "991", "location_id": "loc-1"},
            "SP-MISSING": {"inventory_item_id": "992", "location_id": "loc-1"},
        },
    )
    monkeypatch.setattr(wb, "_safety_buffer", lambda db: 0)
    # SP-ZERO is KNOWN to be sold out (0); SP-MISSING is unknown.
    monkeypatch.setattr(
        wb, "_on_hand_for_skus", lambda db, skus, store: {"SP-ZERO": 0}
    )
    monkeypatch.setattr(wb, "_record_run", lambda db, summary: None)

    summary = _run(wb.writeback_skus(object(), ["SP-ZERO", "SP-MISSING"], "s1"))
    assert summary["pushed"] == 1
    assert summary["skipped_no_onhand"] == 1
    assert len(_FakeAsyncClient.calls) == 1
    q = _FakeAsyncClient.calls[0]["json"]["variables"]["input"]["quantities"][0]
    assert q["inventoryItemId"] == "gid://shopify/InventoryItem/991"
    assert q["quantity"] == 0  # genuinely-zero pushes 0


def test_record_run_writes_sync_row_for_guard_gap():
    """A guard-gap run (unmapped_online > 0) writes a NOT-ok sync_runs row so
    the sync-health tile can see it; a pure no-op run stays silent."""
    rows = []

    class _Coll:
        def insert_one(self, doc):
            rows.append(doc)

    class _Db:
        def get_collection(self, name):
            return _Coll()

    wb._record_run(_Db(), {"pushed": 0, "failed": 0, "unmapped_online": 2, "source": "sale"})
    assert len(rows) == 1
    assert rows[0]["ok"] is False
    assert "oversell-guard gap" in rows[0]["error"]

    rows.clear()
    wb._record_run(_Db(), {"pushed": 0, "failed": 0, "unmapped_online": 0, "source": "sale"})
    assert rows == []  # pure no-op -> no spam


def test_dispatch_off_makes_no_live_call_via_orchestrator(monkeypatch):
    # Writes enabled but DISPATCH_MODE=off -> setter returns SIMULATED, NO HTTP.
    monkeypatch.setenv("IMS_SHOPIFY_WRITES", "1")
    monkeypatch.setattr(nexus_providers, "dispatch_mode", lambda: "off")
    monkeypatch.setattr(nexus_providers.httpx, "AsyncClient", _FakeAsyncClient)
    import api.services.online_catalog as oc
    monkeypatch.setattr(oc, "online_mapping_available", lambda db: True)
    monkeypatch.setattr(
        oc, "online_variant_targets_for_skus",
        lambda db, skus: {"SP-1": {"inventory_item_id": "999", "location_id": "loc-1"}},
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
    monkeypatch.setattr(oc, "online_mapping_available", lambda db: True)
    monkeypatch.setattr(
        oc, "online_variant_targets_for_skus",
        lambda db, skus: {"SP-1": {"inventory_item_id": "999", "location_id": "loc-1"}},
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
