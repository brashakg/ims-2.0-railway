"""
IMS -> Shopify stock write-back tests (council B11 -- oversell guard; per-store
Shopify locations, owner ruling 2026-09-06)
==============================================================================
IMS is the inventory master: a unit leaving a shop's shelf pushes THAT shop's
reduced AVAILABLE quantity (on_hand - safety_buffer, absolute) to that shop's
Shopify location -- and every other mapped shop's own number rides the same
call -- so the website can't oversell. These tests pin:

  * the pushed rows = each shop's own on_hand - buffer, at its own location
    (NEVER the pooled chain total -- the #1125 rule, inverted here)
  * DISPATCH_MODE=off  -> NO live Shopify call (SIMULATED plan)
  * DISPATCH_MODE=live -> one inventorySetQuantities call carrying every shop
  * no Shopify mapping for a SKU -> skipped (no-op, not every product is online)
  * BUT an ONLINE SKU with no mapping is a GUARD GAP -> loud alert + SYSTEM task
  * a shop whose on-hand read failed is written NOWHERE (named), the others go
    out; a whole-batch unknown ABORTS with a not-ok run
  * a shop that HOLDS a listed unit but has no location -> STORE_UNMAPPED, a
    not-ok run row (critic 9) -- the mapped shops still written
  * a transport EXCEPTION never propagates into the sale path

Shopify is mocked at shopify_push._graphql (the single network boundary).
No DB / network.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

from api.services import online_stock_writeback as wb  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


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
# the orchestrator (a unit leaves a shop -> THE rule per shop -> THE writer)
# ---------------------------------------------------------------------------

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from strict_fakes import StrictDB, StrictCollection  # noqa: E402
from api.services import shopify_push  # noqa: E402

LOC_A = "gid://shopify/Location/1001"
LOC_B = "gid://shopify/Location/1002"
INV = "gid://shopify/InventoryItem/999"


class _Spy:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    async def __call__(self, db, query, variables):  # noqa: ARG002
        self.calls.append({"query": query, "variables": variables})
        if self.fail:
            raise RuntimeError("shopify exploded")
        return {"data": {"inventorySetQuantities": {"userErrors": [], "inventoryAdjustmentGroup": {}}}}

    def rows(self):
        out = set()
        for c in self.calls:
            if "inventorySetQuantities" not in c["query"]:
                continue
            for r in c["variables"]["input"]["quantities"]:
                out.add((r["inventoryItemId"], r["locationId"], r["quantity"]))
        return out


def _live(monkeypatch, spy):
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    monkeypatch.setattr(shopify_push, "_has_shopify_creds", lambda db, storefront_id="BV": True)
    monkeypatch.setattr(shopify_push, "_graphql", spy)


def _store(sid, loc=None, **extra):
    row = {"store_id": sid, "store_code": sid, "store_name": sid, "store_type": "RETAIL", "is_active": True, **extra}
    if loc:
        row["shopify_location_id"] = loc
    return row


def _db(*, a=3, b=1, sku="SP-1", listed=True, d=0):
    """Two MAPPED shops (A, B), the ONLINE store and (with `d`) an UNMAPPED
    shop D holding `d` units. One spine product with `a`/`b` AVAILABLE units
    per shop plus a phantom AVAILABLE unit on the online store. `listed`
    gives it a catalog_variants row carrying the Shopify inventory item."""
    db = StrictDB()
    stores = [_store("BV-A", LOC_A), _store("BV-B", LOC_B), _store("BV-ONLINE-01", store_type="ONLINE")]
    if d:
        stores.append(_store("BV-D"))
    db.seed("stores", stores)
    db.seed("products", [{"product_id": "P1", "sku": sku}])
    units = []
    for shop, n in (("BV-A", a), ("BV-B", b), ("BV-D", d)):
        units += [{"stock_id": f"{shop}-{i}", "product_id": "P1", "store_id": shop, "status": "AVAILABLE"} for i in range(n)]
    units.append({"stock_id": "phantom", "product_id": "P1", "store_id": "BV-ONLINE-01", "status": "AVAILABLE"})
    db.seed("stock_units", units)
    if listed:
        db.seed("catalog_products", [{"id": "cat-1", "sku": sku, "ecom": {"status": "PUBLISHED",
                                     "shopify_product_id": "gid://shopify/Product/1"}}])
        db.seed("catalog_variants", [{"sku": sku, "parent_product_id": "cat-1", "shopify_inventory_item_id": INV}])
    return db


def _runs(db):
    return list(db.get_collection("sync_runs").find({}))


def test_sale_pushes_each_shops_own_on_hand_minus_buffer_at_its_own_location(monkeypatch):
    spy = _Spy()
    _live(monkeypatch, spy)
    monkeypatch.setenv("ONLINE_STOCK_SAFETY_BUFFER", "1")
    db = _db(a=3, b=1)
    summary = _run(wb.writeback_skus(db, ["SP-1"], "BV-A"))
    assert summary["pushed"] == 1 and summary["failed"] == 0, summary
    assert len(spy.calls) == 1
    # 3 - 1 at A, 1 - 1 at B: the buffer applies per shop.
    assert spy.rows() == {(INV, LOC_A, 2), (INV, LOC_B, 0)}


def test_sale_pushes_each_shops_own_number_never_the_pooled_total(monkeypatch):
    """INVERTS the #1125 pin: 1 unit at Store A + 9 at Store B, the sale at A.
    The website must show A:1 and B:9 at their own locations -- a pooled 10
    at any location is the oversell this whole change exists to end."""
    spy = _Spy()
    _live(monkeypatch, spy)
    monkeypatch.delenv("ONLINE_STOCK_SAFETY_BUFFER", raising=False)
    monkeypatch.setattr(wb, "_safety_buffer", lambda db: 0)
    db = _db(a=1, b=9)
    summary = _run(wb.writeback_skus(db, ["SP-1"], "BV-A"))
    assert summary["pushed"] == 1 and summary["store_id"] == "BV-A"  # context only
    assert spy.rows() == {(INV, LOC_A, 1), (INV, LOC_B, 9)}
    assert not any(q == 10 for _i, _l, q in spy.rows())


def test_no_mapping_is_skipped(monkeypatch):
    spy = _Spy()
    _live(monkeypatch, spy)
    import api.services.online_catalog as oc
    # The SKU is NOT listed online -> a silent, correct no-op (no alert).
    monkeypatch.setattr(oc, "online_status_for_skus", lambda db, skus: {})
    summary = _run(wb.writeback_skus(_db(listed=False), ["NOT-ONLINE"], "BV-A"))
    assert summary["pushed"] == 0
    assert summary["skipped_no_mapping"] == 1
    assert summary["unmapped_online"] == 0  # not online -> no guard-gap alert
    assert spy.calls == []  # nothing pushed


def test_unmapped_but_sellable_online_sku_alerts_loudly(monkeypatch):
    """OS-015 guard-gap: a sold SKU that is SELLABLE online but has no Shopify
    inventory mapping must alert LOUDLY (summary flag + deduped SYSTEM task),
    never fail soft-silent."""
    spy = _Spy()
    _live(monkeypatch, spy)
    import api.services.online_catalog as oc
    monkeypatch.setattr(oc, "online_mapping_available", lambda db: True)
    monkeypatch.setattr(oc, "inventory_items_for_skus", lambda db, skus: {})
    monkeypatch.setattr(
        oc, "online_status_for_skus",
        lambda db, skus: {
            "SP-ONLINE": {"online": True, "sellable_online": True, "online_stock": None, "status": "PUBLISHED"}
        },
    )
    filed = {}
    monkeypatch.setattr(wb, "_file_guard_gap_task", lambda db, skus: filed.setdefault("skus", skus))
    recorded = {}
    monkeypatch.setattr(wb, "_record_run", lambda db, summary: recorded.update(summary))

    summary = _run(wb.writeback_skus(_db(), ["SP-ONLINE", "SP-OFFLINE"], "s1"))
    assert summary["skipped_no_mapping"] == 2
    assert summary["unmapped_online"] == 1      # only the sellable SKU alerts
    assert filed["skus"] == ["SP-ONLINE"]       # SYSTEM task filed for it
    assert recorded.get("unmapped_online") == 1  # and the run was recorded
    assert spy.calls == []                       # nothing pushed


def test_unmapped_draft_sku_never_alerts(monkeypatch):
    """Fix-round P1: an unpurchasable Shopify DRAFT (gid present, status DRAFT
    -- e.g. the 2,032 staged drafts) canNOT oversell, so selling its in-store
    stock must NOT fire the guard-gap alarm or file a task."""
    spy = _Spy()
    _live(monkeypatch, spy)
    import api.services.online_catalog as oc
    monkeypatch.setattr(oc, "online_mapping_available", lambda db: True)
    monkeypatch.setattr(oc, "inventory_items_for_skus", lambda db, skus: {})
    monkeypatch.setattr(
        oc, "online_status_for_skus",
        lambda db, skus: {
            "SP-DRAFT": {"online": True, "sellable_online": False, "online_stock": None, "status": "DRAFT"}
        },
    )
    filed = {}
    monkeypatch.setattr(wb, "_file_guard_gap_task", lambda db, skus: filed.setdefault("skus", skus))

    summary = _run(wb.writeback_skus(_db(), ["SP-DRAFT"], "s1"))
    assert summary["skipped_no_mapping"] == 1
    assert summary["unmapped_online"] == 0   # draft -> no guard gap
    assert filed == {}                        # no task filed
    assert spy.calls == []


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
# unknown-on-hand safety, per shop (fix-round P1, now per store)
# ---------------------------------------------------------------------------


class _FailingStock(StrictCollection):
    """stock_units whose aggregate dies for the named shops -- immediately
    (`mid=False`, a Mongo blip) or after yielding one healthy row (`mid=True`,
    a cursor death) -- and completes for every other shop. shops=None: all."""

    def __init__(self, base, shops=None, *, mid=False):
        super().__init__(base.name, base.docs)
        self.shops = shops
        self.mid = mid

    def aggregate(self, pipeline, **kwargs):
        match = next((st["$match"] for st in pipeline if "$match" in st), {})
        if self.shops is None or match.get("store_id") in self.shops:
            if not self.mid:
                raise RuntimeError("mongo aggregate blew up")

            def _gen():
                yield {"_id": "P1", "n": 3}
                raise RuntimeError("cursor died mid-iteration")

            return _gen()
        return super().aggregate(pipeline, **kwargs)


def _break(db, shops=None, *, mid=False):
    db._collections["stock_units"] = _FailingStock(db.get_collection("stock_units"), shops, mid=mid)
    return db


def test_aggregate_raise_at_every_shop_aborts_batch_never_writes_zero(monkeypatch):
    """Round-2 P1: a stock_units aggregate that RAISES must flow into the
    batch abort (not default every spine-resolved SKU to 0). Zero Shopify
    calls, skipped_no_onhand == len(targets), not-ok sync_runs row."""
    spy = _Spy()
    _live(monkeypatch, spy)
    db = _break(_db())
    summary = _run(wb.writeback_skus(db, ["SP-1"], "BV-A"))
    assert spy.calls == []                       # ZERO Shopify writes
    assert summary["pushed"] == 0
    assert summary["skipped_no_onhand"] == 1     # == len(targets)
    runs = _runs(db)
    assert len(runs) == 1 and runs[0]["ok"] is False
    assert "on-hand UNKNOWN" in runs[0]["error"]


def test_aggregate_mid_iteration_raise_discards_partial_and_aborts(monkeypatch):
    """Round-2 P1 (worse mode): the cursor yields one healthy row THEN dies at
    every shop. The partial result must be discarded. Also pins the abort-
    branch rider: an unmapped sellable-online SKU in the same batch STILL
    alerts."""
    spy = _Spy()
    _live(monkeypatch, spy)
    import api.services.online_catalog as oc
    monkeypatch.setattr(
        oc, "online_status_for_skus",
        lambda db, skus: {
            "SP-GAP": {"online": True, "sellable_online": True, "online_stock": None, "status": "PUBLISHED"}
        },
    )
    filed = {}
    monkeypatch.setattr(wb, "_file_guard_gap_task", lambda db, skus: filed.setdefault("skus", skus))
    db = _break(_db(), mid=True)
    summary = _run(wb.writeback_skus(db, ["SP-1", "SP-GAP"], "BV-A"))
    assert spy.calls == []                       # ZERO Shopify writes
    assert summary["pushed"] == 0
    assert summary["skipped_no_onhand"] == 1     # == len(targets)
    assert summary["skipped_no_mapping"] == 1    # SP-GAP
    assert summary["unmapped_online"] == 1       # rider: gap still alerted
    assert filed["skus"] == ["SP-GAP"]
    runs = _runs(db)
    assert len(runs) == 1 and runs[0]["ok"] is False


def test_one_shops_unknown_on_hand_is_written_nowhere_the_others_go_out(monkeypatch):
    """T4 at the POS door: B's read dies mid-iteration. A is written with its
    true number, B gets NO row (never a 0), the run is not-ok and names B."""
    spy = _Spy()
    _live(monkeypatch, spy)
    monkeypatch.setattr(wb, "_safety_buffer", lambda db: 0)
    db = _break(_db(a=3, b=1), ["BV-B"], mid=True)
    summary = _run(wb.writeback_skus(db, ["SP-1"], "BV-A"))
    assert spy.rows() == {(INV, LOC_A, 3)}
    assert summary["pushed"] == 1 and summary["unknown_stores"] == ["BV-B"]
    runs = _runs(db)
    assert len(runs) == 1 and runs[0]["ok"] is False and "BV-B" in runs[0]["error"]
    # The baseline omits B so the next pass re-sends it.
    base = db.get_collection("catalog_products").find_one({"id": "cat-1"})["ecom"]["online_stock"]
    assert base["quantities"] == {"SP-1": {"BV-A": 3}}


def test_unknown_on_hand_aborts_batch_never_writes_zero(monkeypatch):
    """Fix-round P1: when the on-hand lookup returns {} at EVERY shop for a
    non-empty target set (Mongo blip / missing spine rows), the batch must
    ABORT with a not-ok run -- writing absolute 0 would delist in-stock
    products."""
    spy = _Spy()
    _live(monkeypatch, spy)
    monkeypatch.setattr(wb, "_on_hand_for_skus", lambda db, skus, store: {})
    recorded = {}
    monkeypatch.setattr(wb, "_record_run", lambda db, summary: recorded.update(summary))
    summary = _run(wb.writeback_skus(_db(), ["SP-1"], "s1"))
    assert spy.calls == []                       # ZERO Shopify writes
    assert summary["pushed"] == 0
    assert summary["skipped_no_onhand"] == 1
    assert recorded.get("skipped_no_onhand") == 1  # run recorded (not-ok)


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
    """A SKU absent from the on-hand map (no spine row) is skipped; a SKU
    PRESENT with 0 still pushes 0 at every mapped shop (that IS the oversell
    guard)."""
    spy = _Spy()
    _live(monkeypatch, spy)
    monkeypatch.setattr(wb, "_safety_buffer", lambda db: 0)
    db = _db(a=0, b=0, sku="SP-ZERO")
    # SP-MISSING is listed online but has no spine row -> unknown.
    db.seed("catalog_variants", [{"sku": "SP-MISSING", "parent_product_id": "cat-1",
                                  "shopify_inventory_item_id": "gid://shopify/InventoryItem/992"}])
    summary = _run(wb.writeback_skus(db, ["SP-ZERO", "SP-MISSING"], "s1"))
    assert summary["pushed"] == 1
    assert summary["skipped_no_onhand"] == 1
    assert spy.rows() == {(INV, LOC_A, 0), (INV, LOC_B, 0)}  # genuinely-zero pushes 0, per shop


def test_unmapped_holder_is_a_loud_not_ok_run_and_the_mapped_shops_still_go_out(monkeypatch):
    """Critic 9: a POS sale at a shop with no Shopify location must never be a
    silent ok=False -- the run row is written, not-ok, naming the shop."""
    spy = _Spy()
    _live(monkeypatch, spy)
    monkeypatch.setattr(wb, "_safety_buffer", lambda db: 0)
    db = _db(a=3, b=1, d=2)
    summary = _run(wb.writeback_skus(db, ["SP-1"], "BV-D"))
    assert summary["code"] == "STORE_UNMAPPED"
    assert [s["store_id"] for s in summary["unmapped_stores"]] == ["BV-D"]
    assert spy.rows() == {(INV, LOC_A, 3), (INV, LOC_B, 1)}
    runs = _runs(db)
    assert len(runs) == 1 and runs[0]["ok"] is False and "BV-D" in runs[0]["error"]
    assert "STORE_UNMAPPED" in runs[0]["error"]


def test_record_run_gate_opens_on_an_unmapped_holder_even_when_nothing_was_pushed(monkeypatch):
    """Critic 9's silent case: EVERY mapped shop's read failed (rows=[] ->
    set=0 -> pushed=0) while an unmapped shop holds the sold SKU. The run row
    is still written, not-ok, naming both -- `pushed` cannot open the gate
    here; only the unmapped_stores / unknown_stores clauses can."""
    spy = _Spy()
    _live(monkeypatch, spy)
    monkeypatch.setattr(wb, "_safety_buffer", lambda db: 0)
    db = _break(_db(a=3, b=1, d=2), ["BV-A", "BV-B"], mid=True)
    summary = _run(wb.writeback_skus(db, ["SP-1"], "BV-D"))
    assert summary["pushed"] == 0 and spy.rows() == set()
    assert [s["store_id"] for s in summary["unmapped_stores"]] == ["BV-D"]
    assert summary["unknown_stores"] == ["BV-A", "BV-B"]
    runs = _runs(db)
    assert len(runs) == 1 and runs[0]["ok"] is False, runs
    assert "BV-D" in runs[0]["error"] and "UNKNOWN" in runs[0]["error"]


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

    # Critic 9: the two per-store gates.
    wb._record_run(_Db(), {"pushed": 0, "failed": 0, "source": "sale",
                           "unmapped_stores": [{"store_id": "BV-D", "store_code": "BV-D"}]})
    assert rows[-1]["ok"] is False and "BV-D" in rows[-1]["error"]
    wb._record_run(_Db(), {"pushed": 1, "failed": 0, "source": "sale", "unknown_stores": ["BV-B"]})
    assert rows[-1]["ok"] is False and "BV-B" in rows[-1]["error"]


def test_dispatch_off_makes_no_live_call_via_orchestrator(monkeypatch):
    # Writes enabled but DISPATCH_MODE=off -> the writer returns a SIMULATED
    # plan, NO network.
    spy = _Spy()
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "off")
    monkeypatch.setattr(shopify_push, "_graphql", spy)
    summary = _run(wb.writeback_skus(_db(), ["SP-1"], "BV-A"))
    assert summary["pushed"] == 0
    assert summary["simulated"] == 1
    assert spy.calls == []  # byte-identical to today: no live write


def test_transport_exception_does_not_propagate(monkeypatch):
    # The orchestrator must swallow a writer raise and record it, never raise.
    spy = _Spy(fail=True)
    _live(monkeypatch, spy)
    summary = _run(wb.writeback_skus(_db(), ["SP-1"], "BV-A"))
    assert summary["failed"] == 1   # recorded, not raised
    assert summary["pushed"] == 0
    assert len(spy.calls) == 1


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
