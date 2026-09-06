"""
Per-store Shopify quantities (owner ruling 2026-09-06: every physical shop is a
Shopify location; "product will be shipped from whichever store holds it")
===========================================================================
THE ONE RULE:  qty at location L for SKU s = recommend_allocation(on_hand(s,
store(L)), buffer), per active physical shop (online_stock_writeback.
online_quantities_for_skus -> {sku: {store_id: qty}}).
THE ONE WRITER: shopify_push.inventory.set_inventory_quantities(db, rows) --
rows = (inventory_item_gid, location_gid, qty), one per mapped shop and SKU,
an explicit 0 included -- behind push_skus_stock, which every door uses.

Pinned here, each REVERT-PROOF (revert the named piece and the test fails):

  T1  a press writes each shop at its own location incl. an explicit 0
      (a pooled revert -> one row of 3 -> fails).
  T2  an unmapped shop that HOLDS a listed unit -> STORE_UNMAPPED naming it,
      ok=False, the mapped rows STILL written, no row for that shop, ONE
      deduped task; an unmapped shop holding nothing never blocks.
  T3  a POS sale at A -> one inventorySetQuantities, a row for every mapped
      shop, A's number = its own on-hand minus the buffer, B untouched.
  T4  one shop's aggregate raises mid-iteration -> that shop written NOWHERE,
      the others written, STOCK_ONHAND_UNKNOWN names it (a revert that writes
      0 fails); the whole-batch STRICT abort still holds.
  T5a the flat #1125 baseline re-sends on the first run and noops on the
      second with _graphql=_explode -- with an unmapped holder present too.
  T5b one unit moved A -> B with the chain total unchanged -> both rows
      re-sent (a pooled diff noops -> fails).
  T6  after a POS write-back the scheduled sync_stock_levels is a zero-network
      noop; the button, the sweep and sync_live_products each hit ONE
      monkeypatched sync_stock_levels exactly once.
  T7  ITEM_NOT_STOCKED_AT_LOCATION -> set, activate per item at the chunk's
      locations, set again; a second failure -> STOCK_ACTIVATION_FAILED and no
      third set; an unrelated userError never activates.
  T11 dry_run=True with LIVE gates -> a SIMULATED plan with per-store rows and
      an EMPTY transcript.
  T12 push_mode_status reports mapped counts with _graphql=_explode.
  T13 buffer 1 applies PER shop (A:2 B:1 -> A:1 B:0).
  T14 a phantom unit on BV-ONLINE-01 never counts anywhere.
  T15 a listing with size rows still writes its OWN inventory item (the
      standalone variant seeded before the rows existed).
  +   the item_events ledger hook and the door one-liners feed the writer;
      route + rbac row + package surface.
  Panel round (2026-09-07): a POS sale landing while the sweep is mid-loop
  is written, not overwritten with the snapshot; the SUPERADMIN block is
  IN the rule so the schedule agrees with the POS door; ONE sku -> listing
  resolver (a size variant's twin never carries a baseline); Preview first
  names a mapped shop whose read failed.

Every Shopify call is MOCKED at shopify_push._graphql. No network, no Mongo.
Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest backend/tests/test_shopify_online_stock.py -q
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from strict_fakes import StrictDB, StrictCollection  # noqa: E402
from api.services import shopify_push  # noqa: E402
from api.services import online_stock_writeback as wb  # noqa: E402
from api.services import item_events  # noqa: E402
from api.services import rbac_policy as rbac  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

LOC_A = "gid://shopify/Location/1001"
LOC_B = "gid://shopify/Location/1002"
LOC_C = "gid://shopify/Location/1003"
PRODUCT_GID = "gid://shopify/Product/111"
VARIANT_GID = "gid://shopify/ProductVariant/5"
INV_GID = "gid://shopify/InventoryItem/9"
BACKEND = os.path.join(os.path.dirname(__file__), "..")


def _run(coro):
    return asyncio.run(coro)


async def _explode(db, query, variables):  # noqa: ARG001
    raise AssertionError("shopify_push._graphql reached -- a DARK path made a network call")


class _Spy:
    """Per-mutation canned bodies (longest marker first) + a full transcript.
    A body may be a LIST: consumed in order, the last one repeats."""

    def __init__(self, responses):
        self.calls = []
        self._responses = dict(responses)

    async def __call__(self, db, query, variables):  # noqa: ARG002
        self.calls.append({"query": query, "variables": variables})
        for marker, body in sorted(self._responses.items(), key=lambda kv: -len(kv[0])):
            if marker in query:
                if isinstance(body, list):
                    if len(body) > 1:
                        return body.pop(0)
                    return body[0]
                return body
        return {"data": {}}

    def calls_for(self, marker):
        return [c for c in self.calls if marker in c["query"]]

    def rows(self):
        """Every (inventoryItemId, locationId, quantity) sent, as a set."""
        out = set()
        for c in self.calls_for("inventorySetQuantities"):
            for r in c["variables"]["input"]["quantities"]:
                out.add((r["inventoryItemId"], r["locationId"], r["quantity"]))
        return out

    def order(self, *markers):
        return [next(m for m in markers if m in c["query"]) for c in self.calls if any(m in c["query"] for m in markers)]


def _ok_body(field, **extra):
    return {"data": {field: {"userErrors": [], **extra}}}


def _set_error(code, message="not stocked"):
    return {
        "data": {
            "inventorySetQuantities": {
                "inventoryAdjustmentGroup": None,
                "userErrors": [{"field": ["input"], "message": message, "code": code}],
            }
        }
    }


def _product_body(field):
    return {
        "data": {
            field: {
                "product": {
                    "id": PRODUCT_GID,
                    "handle": "frame-1",
                    "variants": {
                        "nodes": [
                            {
                                "id": VARIANT_GID,
                                "title": "Default Title",
                                "selectedOptions": [],
                                "inventoryItem": {"id": INV_GID},
                            }
                        ]
                    },
                    "media": {"nodes": [{"id": "gid://shopify/MediaImage/1"}]},
                },
                "userErrors": [],
            }
        }
    }


def _responses(**override):
    base = {
        "productCreate(": _product_body("productCreate"),
        "productUpdate(": _product_body("productUpdate"),
        "productVariantsBulkUpdate": _ok_body("productVariantsBulkUpdate", productVariants=[]),
        "productVariantsBulkCreate": _ok_body("productVariantsBulkCreate", productVariants=[]),
        "inventorySetQuantities": _ok_body(
            "inventorySetQuantities", inventoryAdjustmentGroup={"createdAt": "now", "reason": "correction"}
        ),
        "inventoryBulkToggleActivation": _ok_body("inventoryBulkToggleActivation", inventoryItem={"id": INV_GID}),
        "publications(": {"data": {"publications": {"nodes": [{"id": "gid://shopify/Publication/1", "name": "Online Store"}]}}},
        "publishablePublish": _ok_body("publishablePublish"),
        "metafieldsSet": _ok_body("metafieldsSet", metafields=[]),
    }
    base.update(override)
    return base


def _live(monkeypatch, spy):
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    monkeypatch.setattr(shopify_push, "_has_shopify_creds", lambda db, storefront_id="BV": True)
    monkeypatch.setattr(shopify_push, "_graphql", spy)


def _dark(monkeypatch):
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
    monkeypatch.setattr(shopify_push, "_graphql", _explode)


def _store(sid, loc=None, *, store_type="RETAIL", active=True):
    row = {
        "store_id": sid,
        "store_code": sid,
        "store_name": "Shop " + sid,
        "store_type": store_type,
        "is_active": active,
    }
    if loc:
        row["shopify_location_id"] = loc
        row["shopify_location_name"] = "Shopify " + sid
    return row


def _catalog_row(pid, sku, *, gid=True, online_stock=None, **ecom_extra):
    ecom = {
        "status": "DRAFT",
        "shopify_variant_id": VARIANT_GID if gid else None,
        "shopify_inventory_item_id": INV_GID if gid else None,
        **ecom_extra,
    }
    if gid:
        ecom["shopify_product_id"] = PRODUCT_GID
    if online_stock is not None:
        ecom["online_stock"] = online_stock
    return {
        "id": pid,
        "sku": sku,
        "name": "Frame " + sku,
        "price": 1500,
        "mrp": 1500,
        "images": ["https://cdn.example.com/p.jpg"],
        "ecom": ecom,
    }


def _db(*, a=2, b=1, c=0, sold=1, sku="SP-1", d=0, with_d=False, c_mapped=True):
    """A StrictDB with three MAPPED shops (A, B, C), the ONLINE store, an
    INACTIVE mapped shop (never in the loop) and, when asked, an UNMAPPED shop
    D. ONE spine product: `a`/`b`/`c`/`d` AVAILABLE units per shop, `sold`
    SOLD units at A, and one phantom AVAILABLE unit parked on the online store
    (must never be counted)."""
    db = StrictDB()
    stores = [
        _store("BV-A", LOC_A),
        _store("BV-B", LOC_B),
        _store("BV-C", LOC_C if c_mapped else None),
        _store("BV-ONLINE-01", store_type="ONLINE"),
        _store("BV-OLD", "gid://shopify/Location/1999", active=False),
    ]
    if with_d or d:
        stores.append(_store("BV-D"))
    db.seed("stores", stores)
    db.seed("products", [{"product_id": "spine-1", "sku": sku}])
    units = []
    for shop, n in (("BV-A", a), ("BV-B", b), ("BV-C", c), ("BV-D", d)):
        units += [
            {"stock_id": f"{shop}-u{i}", "product_id": "spine-1", "store_id": shop, "status": "AVAILABLE"}
            for i in range(n)
        ]
    units += [
        {"stock_id": f"s{i}", "product_id": "spine-1", "store_id": "BV-A", "status": "SOLD"}
        for i in range(sold)
    ]
    units.append({"stock_id": "phantom", "product_id": "spine-1", "store_id": "BV-ONLINE-01", "status": "AVAILABLE"})
    units.append({"stock_id": "old", "product_id": "spine-1", "store_id": "BV-OLD", "status": "AVAILABLE"})
    db.seed("stock_units", units)
    return db


def _listed(db, pid="cat-1", sku="SP-1", **kw):
    db.seed("catalog_products", [_catalog_row(pid, sku, gid=True, **kw)])
    return db


def _baseline(db, pid="cat-1"):
    return db.get_collection("catalog_products").find_one({"id": pid})["ecom"].get("online_stock")


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    shopify_push._publication_id_cache.clear()
    for k in ("SHOPIFY_ONLINE_LOCATION_ID", "ONLINE_STOCK_SAFETY_BUFFER", "SHOPIFY_ONLINE_STORE_PUBLICATION_ID"):
        monkeypatch.delenv(k, raising=False)
    yield
    shopify_push._publication_id_cache.clear()


# ---------------------------------------------------------------------------
# 1. THE ONE RULE (T13, T14, T4-rule)
# ---------------------------------------------------------------------------


def test_T13_rule_is_per_shop_and_the_buffer_applies_per_shop():
    db = _db(a=2, b=1, c=0)
    assert wb.online_quantities_for_skus(db, ["SP-1"]) == {"SP-1": {"BV-A": 2, "BV-B": 1, "BV-C": 0}}
    assert wb.online_quantities_for_skus(db, ["SP-1"], safety_buffer=1) == {"SP-1": {"BV-A": 1, "BV-B": 0, "BV-C": 0}}
    assert wb.online_quantities_for_skus(db, ["SP-1"], safety_buffer=9) == {"SP-1": {"BV-A": 0, "BV-B": 0, "BV-C": 0}}
    # An unmapped shop is IN the rule (the writer decides what to do with it).
    assert wb.online_quantities_for_skus(_db(d=3), ["SP-1"])["SP-1"]["BV-D"] == 3


def test_T14_phantom_online_unit_and_inactive_shop_never_count_anywhere():
    per = wb.online_quantities_for_skus(_db(a=0, b=0, c=0), ["SP-1"])["SP-1"]
    assert per == {"BV-A": 0, "BV-B": 0, "BV-C": 0}  # the phantom + BV-OLD units count nowhere
    assert "BV-ONLINE-01" not in per and "BV-OLD" not in per
    # UNKNOWN sku -> absent (never 0); no spine at all -> {} (STRICT).
    assert wb.online_quantities_for_skus(_db(), ["NOPE"]) == {}
    empty = StrictDB()
    empty.seed("stores", [_store("BV-A", LOC_A)])
    assert wb.online_quantities_for_skus(empty, ["SP-1"]) == {}


class _MidFailAt(StrictCollection):
    """stock_units whose aggregate yields one row then DIES for the named
    shop only -- every other shop's aggregate completes."""

    def __init__(self, base, store_id):
        super().__init__(base.name, base.docs)
        self.store_id = store_id

    def aggregate(self, pipeline, **kwargs):
        match = next((st["$match"] for st in pipeline if "$match" in st), {})
        if match.get("store_id") == self.store_id:
            def _gen():
                yield {"_id": "spine-1", "n": 3}
                raise RuntimeError("cursor died mid-iteration")

            return _gen()
        return super().aggregate(pipeline, **kwargs)


def _break_shop(db, store_id):
    db._collections["stock_units"] = _MidFailAt(db.get_collection("stock_units"), store_id)


def test_T4_rule_one_shop_unknown_is_absent_every_other_shop_present():
    db = _db(a=2, b=1, c=0)
    _break_shop(db, "BV-B")
    assert wb.online_quantities_for_skus(db, ["SP-1"]) == {"SP-1": {"BV-A": 2, "BV-C": 0}}
    # Every shop failed -> {} (the whole-batch STRICT abort), never {sku: {}}.
    for shop in ("BV-A", "BV-C"):
        _break_shop(db, shop)
    db._collections["stock_units"] = _MidFailAt(_db().get_collection("stock_units"), "BV-A")
    db._collections["stock_units"] = _AllFail()
    assert wb.online_quantities_for_skus(db, ["SP-1"]) == {}


class _AllFail(StrictCollection):
    def __init__(self):
        super().__init__("stock_units", [])

    def aggregate(self, pipeline, **kwargs):
        raise RuntimeError("mongo blew up")


def test_rule_shop_list_unknown_is_the_whole_batch_unknown():
    db = _db()

    class _Boom(StrictCollection):
        def find(self, *a, **k):
            raise RuntimeError("stores read failed")

    db._collections["stores"] = _Boom("stores")
    assert wb.online_quantities_for_skus(db, ["SP-1"]) == {}


# ---------------------------------------------------------------------------
# 2. THE ONE WRITER: the press (T1, T2, T5a, T5b, T4-writer, T11)
# ---------------------------------------------------------------------------


def test_T1_press_writes_each_shop_at_its_own_location_including_an_explicit_zero(monkeypatch):
    db = _listed(_db(a=2, b=1, c=0))
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.mode == "LIVE" and res.ok is True and res.action == "sync", res
    assert res.payload["candidates"] == 1 and res.payload["changed"] == 1 and res.payload["synced"] == 1
    assert res.payload["stores_total"] == 3 and res.payload["stores_mapped"] == 3
    assert len(spy.calls_for("inventorySetQuantities")) == 1
    assert spy.rows() == {(INV_GID, LOC_A, 2), (INV_GID, LOC_B, 1), (INV_GID, LOC_C, 0)}
    inp = spy.calls_for("inventorySetQuantities")[0]["variables"]["input"]
    assert inp["name"] == "available" and inp["ignoreCompareQuantity"] is True
    base = _baseline(db)
    assert base["quantities"] == {"SP-1": {"BV-A": 2, "BV-B": 1, "BV-C": 0}}
    assert base["tracked"] is True and "location_id" not in base


def test_T2_unmapped_holder_is_named_the_mapped_rows_still_go_out_one_task(monkeypatch):
    db = _listed(_db(a=2, b=1, c=0, d=1))
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.ok is False and res.code == shopify_push.STORE_UNMAPPED, res
    assert "BV-D" in res.error and "Organization" in res.error
    assert res.payload["unmapped_stores"] == [
        {"store_id": "BV-D", "store_code": "BV-D", "store_name": "Shop BV-D", "units": 1}
    ]
    # The mapped shops were STILL written; nothing was written for D.
    assert spy.rows() == {(INV_GID, LOC_A, 2), (INV_GID, LOC_B, 1), (INV_GID, LOC_C, 0)}
    assert all(loc in (LOC_A, LOC_B, LOC_C) for _i, loc, _q in spy.rows())
    # The baseline holds the mapped slice only (critic 4).
    assert _baseline(db)["quantities"] == {"SP-1": {"BV-A": 2, "BV-B": 1, "BV-C": 0}}
    # ONE deduped task for D, even after a second press.
    tasks = db.get_collection("tasks")
    refs = [t.get("source_ref") for t in tasks.find({})]
    assert refs == ["shopify-store-unmapped:BV-D"]
    _run(shopify_push.push_skus_stock(db, ["SP-1"], source="test"))
    assert [t.get("source_ref") for t in tasks.find({})] == ["shopify-store-unmapped:BV-D"]
    assert tasks.find_one({})["store_id"] == "BV-D"


def test_T2b_unmapped_shop_holding_nothing_never_blocks(monkeypatch):
    db = _listed(_db(a=2, b=1, c=0, with_d=True))
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.ok is True and res.code is None and res.payload["unmapped_stores"] == []
    assert res.payload["stores_total"] == 4 and res.payload["stores_mapped"] == 3
    assert list(db.get_collection("tasks").find({})) == []


def test_T5a_flat_baseline_resends_once_then_noops_even_with_an_unmapped_holder(monkeypatch):
    db = _listed(
        _db(a=2, b=1, c=0, d=1),
        online_stock={"quantities": {"SP-1": 3}, "tracked": True, "location_id": "gid://shopify/Location/77"},
    )
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    first = _run(shopify_push.sync_stock_levels(db))
    assert first.payload["changed"] == 1 and first.code == shopify_push.STORE_UNMAPPED
    assert spy.rows() == {(INV_GID, LOC_A, 2), (INV_GID, LOC_B, 1), (INV_GID, LOC_C, 0)}
    monkeypatch.setattr(shopify_push, "_graphql", _explode)
    again = _run(shopify_push.sync_stock_levels(db))
    assert again.action == "noop" and again.payload["changed"] == 0
    # ...and still honest about the unmapped holder.
    assert again.ok is False and again.code == shopify_push.STORE_UNMAPPED


def test_T5b_moving_a_unit_between_shops_resends_both_rows_though_the_total_is_unchanged(monkeypatch):
    db = _listed(_db(a=2, b=1, c=0))
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    _run(shopify_push.sync_stock_levels(db))
    spy.calls.clear()
    # One unit walks A -> B (a received transfer): chain total still 3.
    db.get_collection("stock_units").update_one({"stock_id": "BV-A-u0"}, {"$set": {"store_id": "BV-B"}})
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.payload["changed"] == 1, "a pooled diff would have nooped here"
    assert spy.rows() == {(INV_GID, LOC_A, 1), (INV_GID, LOC_B, 2), (INV_GID, LOC_C, 0)}
    assert _baseline(db)["quantities"] == {"SP-1": {"BV-A": 1, "BV-B": 2, "BV-C": 0}}


def test_T4_writer_unknown_shop_is_written_nowhere_and_named_others_still_go_out(monkeypatch):
    db = _listed(_db(a=2, b=1, c=0))
    _break_shop(db, "BV-B")
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.ok is False and res.code == shopify_push.STOCK_ONHAND_UNKNOWN, res
    assert res.payload["unknown_stores"] == ["BV-B"]
    assert "BV-B" in res.error
    # A and C written; NOTHING for B -- not even a 0.
    assert spy.rows() == {(INV_GID, LOC_A, 2), (INV_GID, LOC_C, 0)}
    assert not any(loc == LOC_B for _i, loc, _q in spy.rows())
    # The baseline omits B, so the next pass re-sends it once B reads again.
    assert _baseline(db)["quantities"] == {"SP-1": {"BV-A": 2, "BV-C": 0}}
    db._collections["stock_units"] = StrictCollection("stock_units", db.get_collection("stock_units").docs)
    spy.calls.clear()
    healed = _run(shopify_push.sync_stock_levels(db))
    assert healed.ok is True and healed.payload["changed"] == 1
    assert spy.rows() == {(INV_GID, LOC_A, 2), (INV_GID, LOC_B, 1), (INV_GID, LOC_C, 0)}


def test_T4_whole_batch_unknown_is_a_strict_abort(monkeypatch):
    db = StrictDB()
    db.seed("stores", [_store("BV-A", LOC_A)])
    db.seed("catalog_products", [_catalog_row("cat-1", "SP-1", gid=True)])  # no spine, no units
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.ok is False and res.code == shopify_push.STOCK_ONHAND_UNKNOWN
    assert spy.calls == []


def test_T11_preview_first_returns_the_per_store_plan_with_zero_network(monkeypatch):
    db = _listed(_db(a=2, b=1, c=0))
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push.sync_stock_levels(db, dry_run=True))
    assert res.mode == "SIMULATED" and res.ok is True and res.action == "sync"
    assert "dry_run" in (res.reason or "")
    assert res.payload["plan"] == [{"product_id": "cat-1", "quantities": {"SP-1": {"BV-A": 2, "BV-B": 1, "BV-C": 0}}}]
    assert res.payload["stores_mapped"] == 3
    assert spy.calls == [], "Preview first must never touch Shopify"
    assert _baseline(db) is None


def test_press_never_touches_shopify_when_dark(monkeypatch):
    db = _listed(_db(a=2, b=1, c=0))
    _dark(monkeypatch)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.mode == "SIMULATED" and res.ok is True
    assert res.payload["plan"][0]["quantities"] == {"SP-1": {"BV-A": 2, "BV-B": 1, "BV-C": 0}}
    assert _baseline(db) is None


# ---------------------------------------------------------------------------
# 3. THE ONE WRITER: activation retry (T7)
# ---------------------------------------------------------------------------


def _rows3():
    return [(INV_GID, LOC_A, 2), (INV_GID, LOC_B, 1), (INV_GID, LOC_C, 0)]


def test_T7_item_not_stocked_activates_per_item_at_the_chunks_locations_then_retries_once(monkeypatch):
    ok = _ok_body("inventorySetQuantities", inventoryAdjustmentGroup={"createdAt": "now", "reason": "correction"})
    spy = _Spy(_responses(**{"inventorySetQuantities": [_set_error("ITEM_NOT_STOCKED_AT_LOCATION"), ok]}))
    _live(monkeypatch, spy)
    out = _run(shopify_push.set_inventory_quantities(None, _rows3()))
    assert out["errors"] == [] and out["set"] == 3 and out["activated"] == 1 and out["code"] is None
    assert spy.order("inventorySetQuantities", "inventoryBulkToggleActivation") == [
        "inventorySetQuantities", "inventoryBulkToggleActivation", "inventorySetQuantities",
    ]
    act = spy.calls_for("inventoryBulkToggleActivation")[0]["variables"]
    assert act["inventoryItemId"] == INV_GID
    assert act["inventoryItemUpdates"] == [
        {"locationId": LOC_A, "activate": True},
        {"locationId": LOC_B, "activate": True},
        {"locationId": LOC_C, "activate": True},
    ]
    assert set(out["written"]) == set(_rows3())


def test_T7b_second_failure_is_stock_activation_failed_and_no_third_set(monkeypatch):
    spy = _Spy(_responses(**{"inventorySetQuantities": _set_error("ITEM_NOT_STOCKED_AT_LOCATION")}))
    _live(monkeypatch, spy)
    out = _run(shopify_push.set_inventory_quantities(None, _rows3()))
    assert out["set"] == 0 and out["written"] == []
    assert out["code"] == shopify_push.STOCK_ACTIVATION_FAILED
    assert len(out["errors"]) == 1 and out["errors"][0].startswith("STOCK_ACTIVATION_FAILED")
    assert len(spy.calls_for("inventorySetQuantities")) == 2
    assert len(spy.calls_for("inventoryBulkToggleActivation")) == 1
    # ...and it surfaces on the press as the run's code.
    db = _listed(_db(a=2, b=1, c=0))
    spy2 = _Spy(_responses(**{"inventorySetQuantities": _set_error("ITEM_NOT_STOCKED_AT_LOCATION")}))
    _live(monkeypatch, spy2)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.ok is False and res.code == shopify_push.STOCK_ACTIVATION_FAILED
    assert _baseline(db) is None, "nothing accepted -> nothing in the baseline"


def test_T7c_an_unrelated_user_error_never_activates(monkeypatch):
    spy = _Spy(_responses(**{"inventorySetQuantities": _set_error("INVALID_QUANTITY", "bad")}))
    _live(monkeypatch, spy)
    out = _run(shopify_push.set_inventory_quantities(None, _rows3()))
    assert out["set"] == 0 and out["code"] is None and len(out["errors"]) == 1
    assert spy.calls_for("inventoryBulkToggleActivation") == []
    assert len(spy.calls_for("inventorySetQuantities")) == 1


def test_writer_chunks_at_shopifys_cap_and_records_only_accepted_rows(monkeypatch):
    ok = _ok_body("inventorySetQuantities", inventoryAdjustmentGroup={"createdAt": "now", "reason": "correction"})
    spy = _Spy(_responses(**{"inventorySetQuantities": [ok, _set_error("INVALID_QUANTITY")]}))
    _live(monkeypatch, spy)
    rows = [(f"gid://shopify/InventoryItem/{i}", LOC_A, 1) for i in range(251)]
    out = _run(shopify_push.set_inventory_quantities(None, rows))
    assert len(spy.calls_for("inventorySetQuantities")) == 2
    assert out["set"] == 250 and len(out["written"]) == 250 and len(out["errors"]) == 1


# ---------------------------------------------------------------------------
# 4. THE POS / ingest / restock door (T3, T6)
# ---------------------------------------------------------------------------


def test_T3_pos_sale_at_A_lowers_only_As_location_and_resends_every_mapped_shop(monkeypatch):
    db = _listed(_db(a=2, b=1, c=0))
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    # The sale: the guarded status flip the POS claim door performs.
    flipped = db.get_collection("stock_units").find_one_and_update(
        {"stock_id": "BV-A-u0", **item_events.on_hand_match()}, {"$set": {"status": "SOLD"}}
    )
    assert flipped is not None
    summary = _run(wb.writeback_skus(db, ["SP-1"], "BV-A"))
    assert summary["pushed"] == 1 and summary["failed"] == 0 and summary["store_id"] == "BV-A", summary
    assert len(spy.calls_for("inventorySetQuantities")) == 1
    assert spy.rows() == {(INV_GID, LOC_A, 1), (INV_GID, LOC_B, 1), (INV_GID, LOC_C, 0)}
    # With a buffer: A's number = its own on-hand minus the buffer; B its own.
    spy.calls.clear()
    _run(wb.writeback_skus(db, ["SP-1"], "BV-A", safety_buffer=1))
    assert spy.rows() == {(INV_GID, LOC_A, 0), (INV_GID, LOC_B, 0), (INV_GID, LOC_C, 0)}


def test_T3b_pos_sale_at_an_unmapped_shop_is_a_loud_not_ok_run(monkeypatch):
    db = _listed(_db(a=2, b=1, c=0, d=2))
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    summary = _run(wb.writeback_skus(db, ["SP-1"], "BV-D"))
    assert summary["code"] == shopify_push.STORE_UNMAPPED
    assert [s["store_id"] for s in summary["unmapped_stores"]] == ["BV-D"]
    assert spy.rows() == {(INV_GID, LOC_A, 2), (INV_GID, LOC_B, 1), (INV_GID, LOC_C, 0)}
    runs = list(db.get_collection("sync_runs").find({}))
    assert len(runs) == 1 and runs[0]["ok"] is False and "BV-D" in runs[0]["error"]  # critic 9


def test_T6_after_a_pos_writeback_the_scheduled_pass_is_a_zero_network_noop(monkeypatch):
    db = _listed(_db(a=2, b=1, c=0))
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    _run(shopify_push.sync_stock_levels(db))  # the first press: tracked + the baseline
    assert _baseline(db)["quantities"] == {"SP-1": {"BV-A": 2, "BV-B": 1, "BV-C": 0}}
    spy.calls.clear()
    db.get_collection("stock_units").update_one({"stock_id": "BV-A-u0"}, {"$set": {"status": "SOLD"}})
    summary = _run(wb.writeback_skus(db, ["SP-1"], "BV-A"))
    assert summary["pushed"] == 1 and len(spy.calls_for("inventorySetQuantities")) == 1
    base = _baseline(db)
    assert base["quantities"] == {"SP-1": {"BV-A": 1, "BV-B": 1, "BV-C": 0}} and base["tracked"] is True
    # The 01:00 / 09:00 pass diffs against the SAME baseline -> noop, no call.
    _live(monkeypatch, _explode)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.action == "noop" and res.ok is True and res.payload["changed"] == 0


def test_T6b_button_sweep_and_live_sync_each_hit_sync_stock_levels_exactly_once(monkeypatch):
    from api.routers import online_store_push as router
    from api.services import shopify_live_sync as ls

    calls = []

    async def _counted(db, *, dry_run=False):
        calls.append(dry_run)
        return shopify_push.PushResult(mode="SIMULATED", entity="stock", action="noop", ok=True, payload={})

    monkeypatch.setattr(shopify_push, "sync_stock_levels", _counted)
    monkeypatch.setattr(router, "_write_audit", lambda *a, **k: None)
    db = _listed(_db())
    monkeypatch.setattr(router, "_get_db", lambda: db)
    user = {"user_id": "u", "roles": ["SUPERADMIN"]}
    # The button, plain and with Preview first.
    _run(router.push_stock(dry_run=False, current_user=user))
    _run(router.push_stock(dry_run=True, current_user=user))
    assert calls == [False, True]
    # The all-pending sweep.
    _dark(monkeypatch)
    calls.clear()
    _run(router.push_all_pending(entities=None, limit=5, offset=0, current_user=user))
    assert calls == [False]
    # The scheduled / manual live-product sync.
    calls.clear()
    monkeypatch.setattr(ls, "live_sync_config", lambda: {"enabled": True, "slots": ["01:00", "09:00"], "max_products_per_run": 5})
    monkeypatch.setattr(ls, "write_push_audit", lambda *a, **k: None)
    _run(ls.sync_live_products(db, trigger="manual", actor="u"))
    assert calls == [False]


# ---------------------------------------------------------------------------
# 5. the product push writes per-store stock
# ---------------------------------------------------------------------------


def _assert_tracked(spy, policy="DENY"):
    tracking = [
        c for c in spy.calls_for("productVariantsBulkUpdate")
        if any("inventoryPolicy" in row for row in c["variables"]["variants"])
    ]
    assert len(tracking) == 1, "exactly one tracking/policy update"
    rows = tracking[0]["variables"]["variants"]
    assert tracking[0]["variables"]["productId"] == PRODUCT_GID
    assert {r["id"] for r in rows} == {VARIANT_GID}
    assert all(r["inventoryPolicy"] == policy and r["inventoryItem"] == {"tracked": True} for r in rows)


def test_live_create_tracks_denies_and_writes_each_shops_quantity(monkeypatch):
    db = _db(a=2, b=1, c=0)
    db.seed("catalog_products", [_catalog_row("cat-1", "SP-1", gid=False)])
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, db.get_collection("catalog_products").find_one({"id": "cat-1"}), []))
    assert res.mode == "LIVE" and res.action == "create", res
    _assert_tracked(spy)
    assert spy.rows() == {(INV_GID, LOC_A, 2), (INV_GID, LOC_B, 1), (INV_GID, LOC_C, 0)}
    assert res.stock["ok"] is True and res.stock["quantities"] == {"SP-1": {"BV-A": 2, "BV-B": 1, "BV-C": 0}}
    assert res.stock["tracked"] == 1 and res.stock["stores_mapped"] == 3 and "location_id" not in res.stock
    # The stock step ran BEFORE the publish, so the listing never went visible untracked.
    assert spy.order("inventorySetQuantities", "publishablePublish") == ["inventorySetQuantities", "publishablePublish"]
    base = _baseline(db)
    assert base["quantities"] == {"SP-1": {"BV-A": 2, "BV-B": 1, "BV-C": 0}} and base["tracked"] is True
    assert db.get_collection("catalog_products").find_one({"id": "cat-1"})["ecom"]["locally_modified"] is False


def test_live_update_also_writes_stock_and_allow_oversell_selects_continue(monkeypatch):
    assert shopify_push.inventory_policy_for({"ecom": {}}) == "DENY"
    assert shopify_push.inventory_policy_for({"ecom": {"allow_oversell": True}}) == "CONTINUE"
    db = _listed(_db(a=2, b=1, c=0), allow_oversell=True)
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, db.get_collection("catalog_products").find_one({"id": "cat-1"}), []))
    assert res.action == "update" and res.mode == "LIVE"
    _assert_tracked(spy, policy="CONTINUE")
    assert res.stock["quantities"] == {"SP-1": {"BV-A": 2, "BV-B": 1, "BV-C": 0}}


def test_live_push_with_no_mapped_shop_writes_nothing_and_names_the_holder(monkeypatch):
    db = StrictDB()
    db.seed("stores", [_store("BV-A"), _store("BV-ONLINE-01", store_type="ONLINE")])
    db.seed("products", [{"product_id": "spine-1", "sku": "SP-1"}])
    db.seed("stock_units", [{"stock_id": "u0", "product_id": "spine-1", "store_id": "BV-A", "status": "AVAILABLE"}])
    _listed(db)
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, db.get_collection("catalog_products").find_one({"id": "cat-1"}), []))
    assert spy.calls_for("inventorySetQuantities") == []
    _assert_tracked(spy)  # tracked + DENY regardless: an untracked item sells without limit
    assert res.stock["ok"] is False and res.stock["code"] == shopify_push.STORE_UNMAPPED
    assert res.stock["unmapped_stores"][0]["store_id"] == "BV-A"
    assert _baseline(db) is None


def test_dark_push_plans_per_store_stock_with_zero_network(monkeypatch):
    db = _listed(_db(a=2, b=1, c=0, d=1))
    _dark(monkeypatch)
    res = _run(shopify_push.push_product(db, db.get_collection("catalog_products").find_one({"id": "cat-1"}), []))
    assert res.mode == "SIMULATED"
    assert res.stock == {
        "tracked": True,
        "policy": "DENY",
        "quantities": {"SP-1": {"BV-A": 2, "BV-B": 1, "BV-C": 0}},
        "stores_mapped": 3,
        "stores_total": 4,
        "unmapped_stores": [{"store_id": "BV-D", "store_code": "BV-D", "store_name": "Shop BV-D", "units": 1}],
    }


def test_sync_stock_levels_pushes_only_changed_gid_products(monkeypatch):
    db = _db(a=2, b=1, c=0)
    db.seed("products", [{"product_id": "spine-2", "sku": "SP-2"}, {"product_id": "spine-3", "sku": "SP-3"}])
    db.seed(
        "stock_units",
        [
            {"stock_id": "b1", "product_id": "spine-2", "store_id": "BV-B", "status": "AVAILABLE"},
            {"stock_id": "c1", "product_id": "spine-3", "store_id": "BV-C", "status": "AVAILABLE"},
        ],
    )
    db.seed(
        "catalog_products",
        [
            # A: on Shopify, never sent -> changed.
            _catalog_row("cat-1", "SP-1", gid=True),
            # B: on Shopify, last send equals today's per-store numbers -> unchanged.
            _catalog_row("cat-2", "SP-2", gid=True,
                         online_stock={"quantities": {"SP-2": {"BV-A": 0, "BV-B": 1, "BV-C": 0}}, "tracked": True}),
            # C: NOT on Shopify -> never a candidate.
            _catalog_row("cat-3", "SP-3", gid=False),
        ],
    )
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.payload["candidates"] == 2 and res.payload["changed"] == 1 and res.payload["synced"] == 1
    assert spy.rows() == {(INV_GID, LOC_A, 2), (INV_GID, LOC_B, 1), (INV_GID, LOC_C, 0)}  # only A


# ---------------------------------------------------------------------------
# 6. every door that removes availability feeds the writer
# ---------------------------------------------------------------------------


def _capture_restock(monkeypatch):
    seen = []
    monkeypatch.setattr(
        wb, "writeback_after_restock",
        lambda db, skus, store_id, *, source="return_restock": seen.append((list(skus), store_id, source)),
    )
    return seen


def test_ledger_hook_fires_on_every_on_hand_to_not_on_hand_transition_only(monkeypatch):
    db = _db(a=2, b=1, c=0)
    seen = _capture_restock(monkeypatch)
    ev = item_events.record_event(
        db, event_type=item_events.ItemEventType.QUARANTINE_IN, actor_id="u", stock_id="BV-A-u0",
        from_state=item_events.StockState.AVAILABLE, to_state=item_events.StockState.QUARANTINED,
    )
    assert ev is not None
    assert seen == [(["SP-1"], "BV-A", "ledger:quarantine.in")]
    # Coming BACK on hand is not an oversell: no immediate call (the next pass has it).
    seen.clear()
    ev = item_events.record_event(
        db, event_type=item_events.ItemEventType.QUARANTINE_OUT, actor_id="u", stock_id="BV-A-u0",
        from_state=item_events.StockState.QUARANTINED, to_state=item_events.StockState.AVAILABLE,
        enforce_transition=False,
    )
    assert ev is not None and seen == []
    # A lost CAS never fires.
    ev = item_events.record_event(
        db, event_type=item_events.ItemEventType.SELL, actor_id="u", stock_id="nope",
        from_state=item_events.StockState.AVAILABLE, to_state=item_events.StockState.SOLD,
    )
    assert ev is None and seen == []


def test_units_left_entrypoint_resolves_skus_and_never_raises(monkeypatch):
    db = _db()
    seen = _capture_restock(monkeypatch)
    wb.writeback_after_units_left(db, ["spine-1", "spine-1", "ghost"], "BV-A", source="transfer_ship")
    assert seen == [(["SP-1"], "BV-A", "transfer_ship")]
    assert wb.writeback_after_units_left(object(), ["spine-1"], "BV-A", source="x") is None
    assert wb.writeback_after_units_left(db, [], "BV-A", source="x") is None


def test_the_three_doors_and_the_pos_call_feed_the_writer():
    def _src(*parts):
        return open(os.path.join(BACKEND, *parts), encoding="utf-8").read()

    assert '_writeback_units_left(moved_pids, from_store, "transfer_ship")' in _src("api", "routers", "transfers.py")
    assert 'source="quarantine_in"' in _src("api", "routers", "inventory", "quarantine.py")
    assert 'source="stock_count_writeoff"' in _src("api", "routers", "inventory", "stock_count_reconcile.py")
    ingest = _src("api", "services", "shopify_ingest.py")
    assert "_mark_units_sold(" in ingest and "writeback_after_sale(" in ingest
    assert "writeback_after_sale(" in _src("api", "routers", "orders", "create.py")
    # POST /orders/{id}/items flips AVAILABLE -> SOLD too (an API client adding
    # a line to a DRAFT order): the same fail-soft call, or a ~16 h oversell.
    items = _src("api", "routers", "orders", "items.py")
    assert "_mark_units_sold(order_id, [item_data], _store_id)" in items
    assert "writeback_after_sale(None, [item_data], _store_id)" in items
    assert "_writeback_left_on_hand(db, product_id, store_id, event_type)" in _src("api", "services", "item_events.py")


# ---------------------------------------------------------------------------
# 7. status, route, rbac row, package surface (T12)
# ---------------------------------------------------------------------------


def test_T12_push_mode_status_reports_mapped_counts_without_network(monkeypatch):
    monkeypatch.setattr(shopify_push, "_graphql", _explode)
    status = shopify_push.push_mode_status(_db(with_d=True))
    assert status["stores_total"] == 4 and status["stores_mapped"] == 3
    assert status["unmapped_stores"] == [{"store_id": "BV-D", "store_code": "BV-D", "store_name": "Shop BV-D"}]
    assert [s["store_id"] for s in status["stores"]] == ["BV-A", "BV-B", "BV-C", "BV-D"]
    assert status["stores"][0]["shopify_location_id"] == LOC_A
    assert "online_location_id" not in status
    # The resolved read asks Shopify for nothing location-shaped either.
    monkeypatch.setenv("SHOPIFY_ONLINE_STORE_PUBLICATION_ID", "1")
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    monkeypatch.setattr(shopify_push, "_has_shopify_creds", lambda db, storefront_id="BV": True)
    resolved = _run(shopify_push.push_mode_status_resolved(_db()))
    assert resolved["is_live"] is True and resolved["stores_mapped"] == 3


def test_stock_route_is_catalogued_admin_superadmin_only():
    entry = rbac.policy_for("POST", "/api/v1/online-store/push/stock")
    assert entry is not None
    assert set(entry["allowed"]) == {"ADMIN", "SUPERADMIN"}


def test_stock_route_is_mounted_with_the_dry_run_flag_and_sweep_carries_stock():
    from api.main import app

    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/api/v1/online-store/push/stock" in paths
    src = open(os.path.join(BACKEND, "api", "routers", "online_store_push.py"), encoding="utf-8").read()
    assert "sync_stock_levels(db, dry_run=dry_run)" in src and '"stock": stock' in src


def test_package_exports_and_patch_forwarding():
    assert shopify_push.inventory in shopify_push._SUBMODULES
    for name in ("sync_stock_levels", "sync_product_stock", "push_skus_stock", "set_inventory_quantities", "list_locations"):
        assert callable(getattr(shopify_push, name))
    for gone in ("resolve_online_location_id", "pick_online_location", "stored_online_location_id", "_online_location_cache"):
        assert not hasattr(shopify_push, gone), gone


# ---------------------------------------------------------------------------
# 8. panel round (2026-09-07): mid-loop sale, own inventory item, the block
#    in the rule, one listing resolver, an honest preview
# ---------------------------------------------------------------------------

INV_2 = "gid://shopify/InventoryItem/92"
INV_ROW = "gid://shopify/InventoryItem/952"


def test_P2_a_sale_landing_while_the_sweep_is_mid_loop_is_written_not_overwritten(monkeypatch):
    """Two listings; the spy flips a unit of the SECOND product to SOLD the
    moment the FIRST product's quantity write lands (the POS sale that
    arrives mid-pass). The second product's row must carry the post-sale
    number: the rule runs again right before each write, never from the
    pass's opening snapshot."""
    db = _db(a=2, b=1, c=0)
    db.seed("products", [{"product_id": "spine-2", "sku": "SP-2"}])
    db.seed(
        "stock_units",
        [
            {"stock_id": "a2-0", "product_id": "spine-2", "store_id": "BV-A", "status": "AVAILABLE"},
            {"stock_id": "a2-1", "product_id": "spine-2", "store_id": "BV-A", "status": "AVAILABLE"},
        ],
    )
    db.seed(
        "catalog_products",
        [_catalog_row("cat-1", "SP-1", gid=True), _catalog_row("cat-2", "SP-2", gid=True, shopify_inventory_item_id=INV_2)],
    )

    class _SaleMidLoop(_Spy):
        async def __call__(self, db_, query, variables):
            body = await super().__call__(db_, query, variables)
            if "inventorySetQuantities" in query and len(self.calls_for("inventorySetQuantities")) == 1:
                db.get_collection("stock_units").update_one({"stock_id": "a2-0"}, {"$set": {"status": "SOLD"}})
            return body

    spy = _SaleMidLoop(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.ok is True and res.payload["changed"] == 2 and res.payload["synced"] == 2, res
    assert (INV_2, LOC_A, 1) in spy.rows(), spy.rows()
    assert (INV_2, LOC_A, 2) not in spy.rows(), "the pre-sale snapshot overwrote the sale"
    assert _baseline(db, "cat-2")["quantities"] == {"SP-2": {"BV-A": 1, "BV-B": 0, "BV-C": 0}}
    # ...and the next pass is a true noop: the baseline holds what was written.
    _live(monkeypatch, _explode)
    assert _run(shopify_push.sync_stock_levels(db)).action == "noop"


def test_T15_a_listing_with_size_rows_still_writes_its_own_inventory_item(monkeypatch):
    """A parent listed BEFORE its size rows existed carries its own inventory
    item (ecom.shopify_inventory_item_id -- the standalone variant); the size
    rows carry theirs. Both are written: left out, the parent's own number
    on Shopify would survive every pass (probe E)."""
    db = _db(a=1, b=0, c=0)
    db.seed("products", [{"product_id": "spine-52", "sku": "SP-1-52"}])
    db.seed("stock_units", [{"stock_id": "r52", "product_id": "spine-52", "store_id": "BV-B", "status": "AVAILABLE"}])
    _listed(db)  # cat-1 / SP-1 with INV_GID on the product itself
    db.seed(
        "catalog_variants",
        [{"sku": "SP-1-52", "parent_product_id": "cat-1", "shopify_variant_id": "gid://shopify/ProductVariant/52",
          "shopify_inventory_item_id": INV_ROW}],
    )
    product = db.get_collection("catalog_products").find_one({"id": "cat-1"})
    rows = list(db.get_collection("catalog_variants").find({}))
    assert shopify_push.product_skus(product, rows) == ["SP-1-52", "SP-1"]
    # A product WITHOUT its own item lists its rows only (unchanged).
    no_own = {**product, "ecom": {k: v for k, v in product["ecom"].items() if k != "shopify_inventory_item_id"}}
    assert shopify_push.product_skus(no_own, rows) == ["SP-1-52"]
    assert shopify_push.product_skus(no_own, []) == ["SP-1"]
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.ok is True, res
    assert spy.rows() == {
        (INV_GID, LOC_A, 1), (INV_GID, LOC_B, 0), (INV_GID, LOC_C, 0),
        (INV_ROW, LOC_A, 0), (INV_ROW, LOC_B, 1), (INV_ROW, LOC_C, 0),
    }
    assert _baseline(db)["quantities"] == {
        "SP-1": {"BV-A": 1, "BV-B": 0, "BV-C": 0},
        "SP-1-52": {"BV-A": 0, "BV-B": 1, "BV-C": 0},
    }


def test_two_skus_on_one_inventory_item_write_it_once_and_name_the_second(monkeypatch):
    """A mis-stamped mapping (two SKUs, one inventory item) must not send a
    duplicate (item, location) pair -- Shopify would refuse the whole chunk."""
    db = _listed(_db(a=2, b=1, c=0))
    db.seed("products", [{"product_id": "spine-x", "sku": "SP-X"}])
    db.seed("catalog_variants", [{"sku": "SP-X", "parent_product_id": "cat-1", "shopify_inventory_item_id": INV_GID}])
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    out = _run(shopify_push.push_skus_stock(db, ["SP-1", "SP-X"], source="test"))
    assert spy.rows() == {(INV_GID, LOC_A, 2), (INV_GID, LOC_B, 1), (INV_GID, LOC_C, 0)}
    assert out["ok"] is False and any("not written twice" in e and "SP-X" in e for e in out["errors"])


def test_blocked_sku_is_zero_in_the_rule_so_the_schedule_agrees_with_the_pos_door(monkeypatch):
    """The SUPERADMIN collection block is part of THE rule: the press and the
    POS door write 0 at every shop, and the next 01:00 / 09:00 pass diffs
    0 == 0 and sends NOTHING. It used to live only in writeback_skus, so the
    schedule wrote the shelf count straight back (0 <-> on-hand, a real
    inventorySetQuantities each time)."""
    db = _listed(_db(a=2, b=1, c=0))
    db.seed(
        "ecom_collections",
        [{"collection_id": "C-BAN", "collection_type": "CUSTOM", "online_sync_blocked": True,
          "products": [{"sku": "SP-1", "position": 0}]}],
    )
    zeros = {(INV_GID, LOC_A, 0), (INV_GID, LOC_B, 0), (INV_GID, LOC_C, 0)}
    assert wb.online_quantities_for_skus(db, ["SP-1"]) == {"SP-1": {"BV-A": 0, "BV-B": 0, "BV-C": 0}}
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    assert _run(shopify_push.sync_stock_levels(db)).ok is True  # the press
    assert spy.rows() == zeros, "the press wrote the shelf count for a BLOCKED sku"
    spy.calls.clear()
    s = _run(wb.writeback_skus(db, ["SP-1"], "BV-A"))  # the POS door
    assert s["pushed"] == 1 and spy.rows() == zeros
    _live(monkeypatch, _explode)
    assert _run(shopify_push.sync_stock_levels(db)).action == "noop"


def test_a_size_variants_pos_writeback_lands_on_the_parent_listing_never_the_child_twin(monkeypatch):
    """ONE sku -> listing resolver (online_catalog.listings_for_skus): a size
    variant's own catalog_products row (same SKU as its variant row, a
    variant_of link, no listing of its own) never receives a baseline the
    schedule never diffs; the PARENT listing does -- with or without the
    variant row."""
    from api.services import online_catalog

    child = {
        "id": "cat-1-L", "sku": "SP-1-L", "name": "Frame L",
        "ecom": {"status": "DRAFT", "variant_of": {"product_id": "spine-1", "twin_id": "cat-1", "sku": "SP-1"}},
    }
    db = _db(a=2, b=1, c=0, sku="SP-1-L")
    db.seed("catalog_products", [_catalog_row("cat-1", "SP-1", gid=True), child])
    db.seed(
        "catalog_variants",
        [{"sku": "SP-1-L", "parent_product_id": "cat-1", "shopify_variant_id": "gid://shopify/ProductVariant/52",
          "shopify_inventory_item_id": INV_ROW}],
    )
    got = online_catalog.listings_for_skus(db, ["SP-1-L", "SP-1"])
    assert {k: sorted(v) for k, v in got.items()} == {"cat-1": ["SP-1", "SP-1-L"]}
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    s = _run(wb.writeback_skus(db, ["SP-1-L"], "BV-A"))
    assert s["pushed"] == 1 and spy.rows() == {(INV_ROW, LOC_A, 2), (INV_ROW, LOC_B, 1), (INV_ROW, LOC_C, 0)}
    assert _baseline(db)["quantities"] == {"SP-1-L": {"BV-A": 2, "BV-B": 1, "BV-C": 0}}
    assert "online_stock" not in db.get_collection("catalog_products").find_one({"id": "cat-1-L"})["ecom"]
    # No variant row at all: the child twin still resolves to its PARENT.
    bare = StrictDB()
    bare.seed("catalog_products", [child])
    assert online_catalog.listings_for_skus(bare, ["SP-1-L"]) == {"cat-1": ["SP-1-L"]}


def test_T11b_preview_first_names_a_mapped_shop_whose_read_failed(monkeypatch):
    """Section-7 step 4: the owner reads the preview. A mapped shop whose
    on-hand read failed is simply ABSENT from the plan rows, so the preview
    says so exactly as the live pass would -- ok=False, STOCK_ONHAND_UNKNOWN
    naming it -- never a green 'nothing sent'."""
    db = _listed(_db(a=2, b=1, c=0))
    # B's store_id and store_code differ (Pune's id is a UUID on prod): the
    # payload key stays the id, the line the owner reads names the CODE.
    db.get_collection("stores").update_one({"store_id": "BV-B"}, {"$set": {"store_code": "HIRAPUR-DHN"}})
    _break_shop(db, "BV-B")
    _live(monkeypatch, _explode)
    res = _run(shopify_push.sync_stock_levels(db, dry_run=True))
    assert res.mode == "SIMULATED" and res.ok is False, res
    assert res.code == shopify_push.STOCK_ONHAND_UNKNOWN and "HIRAPUR-DHN" in (res.error or "")
    assert res.payload["unknown_stores"] == ["BV-B"]
    # ...and the POS door's summary names the code the same way.
    s = _run(wb.writeback_skus(db, ["SP-1"], "BV-A"))
    assert s["unknown_stores"] == ["BV-B"] and "HIRAPUR-DHN" in (s.get("error") or "")
    assert res.payload["plan"][0]["quantities"] == {"SP-1": {"BV-A": 2, "BV-C": 0}}
    assert _baseline(db) is None


def test_a_retired_size_variant_is_zeroed_at_every_mapped_location(monkeypatch):
    """P4 (merge with feat/variant-of-rule): deactivating ONE size writes 0 for
    its inventory item at EVERY mapped shop's location through the one
    writer -- a 0 at one location would leave the size on sale from the other
    shops -- and the PARENT's nested baseline records the 0 per shop, so a
    reactivation diffs. DENY on the child's own variant, never a
    productUpdate on the parent."""
    child = {
        "id": "cat-1-L", "sku": "SP-1-L", "name": "Frame L",
        "ecom": {"status": "DRAFT", "variant_of": {"product_id": "spine-1", "twin_id": "cat-1", "sku": "SP-1"}},
    }
    db = _db(a=2, b=1, c=0, sku="SP-1-L")
    db.seed("catalog_products", [_catalog_row("cat-1", "SP-1", gid=True), child])
    db.seed(
        "catalog_variants",
        [{"sku": "SP-1-L", "parent_product_id": "cat-1", "shopify_variant_id": "gid://shopify/ProductVariant/52",
          "shopify_inventory_item_id": INV_ROW}],
    )
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push._delist_variant_row(db, child))
    assert res.ok is True and res.mode == "LIVE" and res.action == "delist", res
    assert spy.rows() == {(INV_ROW, LOC_A, 0), (INV_ROW, LOC_B, 0), (INV_ROW, LOC_C, 0)}
    assert res.payload["rows"] == {"SP-1-L": {"BV-A": 0, "BV-B": 0, "BV-C": 0}} and res.payload["stores_mapped"] == 3
    tracking = spy.calls_for("productVariantsBulkUpdate")
    assert len(tracking) == 1 and tracking[0]["variables"]["variants"] == [
        {"id": "gid://shopify/ProductVariant/52", "inventoryPolicy": "DENY", "inventoryItem": {"tracked": True}}
    ]
    assert spy.calls_for("productUpdate(") == []
    assert _baseline(db)["quantities"] == {"SP-1-L": {"BV-A": 0, "BV-B": 0, "BV-C": 0}}
    assert "online_stock" not in db.get_collection("catalog_products").find_one({"id": "cat-1-L"})["ecom"]
    # No shop mapped at all: nothing can be written -- said so, not ok.
    bare = _db(a=1, b=0, c=0, sku="SP-1-L")
    bare.get_collection("stores").update_many({}, {"$unset": {"shopify_location_id": ""}})
    bare.seed("catalog_products", [_catalog_row("cat-1", "SP-1", gid=True), child])
    bare.seed("catalog_variants", [{"sku": "SP-1-L", "parent_product_id": "cat-1",
                                    "shopify_variant_id": "gid://shopify/ProductVariant/52",
                                    "shopify_inventory_item_id": INV_ROW}])
    spy2 = _Spy(_responses())
    _live(monkeypatch, spy2)
    res2 = _run(shopify_push._delist_variant_row(bare, child))
    assert res2.ok is False and res2.code == shopify_push.STORE_UNMAPPED and spy2.rows() == set()
