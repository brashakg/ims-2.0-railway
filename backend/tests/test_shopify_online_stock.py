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
  Panel round 2 (2026-09-07, section 9): a blocked SKU never clears a shop
      whose read died (P1); two shops on one location write neither (R1); a
      legacy store doc with no is_active flag is still a shop; a sweep where
      nothing changed still files the unmapped task (S1); on-hand at a store
      id no shop matches is named (S3); zero mapped shops is never a green
      no-op (P2 + the fresh-catalogue first press); a store-read failure is
      UNKNOWN, never "no shops"; the summary counts what Shopify ACCEPTED
      (P6/P7); Preview-first names a missing Shopify target (R3).
  Panel round 3 (2026-09-07): one dead Shopify location never freezes every
      other shop's number (P1, the split-per-location retry); the pre-press
      gate chip counts "mapped" the way the WRITER does (P2); the dry-run
      product plan carries the writer's own code (P3); a shop holding exactly
      the safety buffer is still an unmapped holder (P4); a SKU that left the
      product never marks it changed forever (P6); a Shopify location that
      fulfils online orders with no shop behind it is never a green run
      (invariant 2, backend); a first publish whose stock was refused is not a
      clean success (ops P5); the blocked-SKU whole-batch abort; parity never
      counts a shop Shopify cannot see.
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

    def writes(self):
        """Every call that is not the READ-ONLY locations list. A LIVE stock
        pass reads Shopify's locations once (invariant 2), so "zero network"
        pins are about WRITES."""
        return [c for c in self.calls if "imsLocationList" not in c["query"]]

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
    assert spy.writes() == [], "Preview first must never WRITE to Shopify"
    # ...and it runs invariant 2's read, so the preview and the press cannot
    # disagree about a Shopify location that sells with no shop behind it.
    assert len(spy.calls_for("imsLocationList")) == 1
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


def test_T7b_a_failed_activation_is_retried_split_per_location_and_coded(monkeypatch):
    """T7b, REWRITTEN for round-4 P1. It used to pin `set == 0`, `written == []`
    and exactly TWO inventorySetQuantities calls over a THREE-location chunk --
    i.e. it pinned the hole: `_write_chunk` skipped the per-location split on
    the activation branch, which is the branch a dead location most often takes
    (bulk activation is one call per ITEM across all its locations, so ONE dead
    location poisons the activation for every location). Here EVERY location is
    genuinely dead, so nothing lands either way -- but the split must still have
    run, one recovery per location."""
    spy = _Spy(_responses(**{"inventorySetQuantities": _set_error("ITEM_NOT_STOCKED_AT_LOCATION")}))
    _live(monkeypatch, spy)
    out = _run(shopify_push.set_inventory_quantities(None, _rows3()))
    assert out["set"] == 0 and out["written"] == []
    assert out["code"] == shopify_push.STOCK_ACTIVATION_FAILED
    assert len(out["errors"]) == 1, "a split never inflates the caller's failure count"
    # whole chunk (set, activate, set) + one full recovery per location
    assert len(spy.calls_for("inventorySetQuantities")) == 2 + 2 * 3
    assert len(spy.calls_for("inventoryBulkToggleActivation")) == 1 + 3
    # ...and it surfaces on the press as the run's code.
    db = _listed(_db(a=2, b=1, c=0))
    spy2 = _Spy(_responses(**{"inventorySetQuantities": _set_error("ITEM_NOT_STOCKED_AT_LOCATION")}))
    _live(monkeypatch, spy2)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.ok is False and res.code == shopify_push.STOCK_ACTIVATION_FAILED
    assert _baseline(db) is None, "nothing accepted -> nothing in the baseline"


class _DeadLocationActivation(_Spy):
    """The shape round-4 P1 names. ``bad``'s Shopify location was DELETED in
    admin under a live mapping, so (a) any inventorySetQuantities call touching
    it answers ITEM_NOT_STOCKED_AT_LOCATION, and (b) the inventoryBulkToggle-
    Activation for an item whose update list includes it answers a userError --
    Shopify activates one ITEM across all of its locations in a single call, so
    one dead location poisons the activation for EVERY location."""

    def __init__(self, responses, bad):
        super().__init__(responses)
        self.bad = bad

    async def __call__(self, db, query, variables):  # noqa: ARG002
        if "inventorySetQuantities" in query and any(
            r["locationId"] == self.bad for r in variables["input"]["quantities"]
        ):
            self.calls.append({"query": query, "variables": variables})
            return _set_error(shopify_push.ITEM_NOT_STOCKED_AT_LOCATION)
        if "inventoryBulkToggleActivation" in query and any(
            u["locationId"] == self.bad for u in variables["inventoryItemUpdates"]
        ):
            self.calls.append({"query": query, "variables": variables})
            return {
                "data": {
                    "inventoryBulkToggleActivation": {
                        "inventoryItem": None,
                        "userErrors": [{"field": ["locationId"], "message": "Location does not exist"}],
                    }
                }
            }
        return await super().__call__(db, query, variables)


def test_R4_P1_a_dead_locations_activation_never_freezes_every_other_shop(monkeypatch):
    """ROUND-4 P1, REAL OVERSELL. Three mapped shops; BV-C's Shopify location
    was deleted in admin. The first set is refused with
    ITEM_NOT_STOCKED_AT_LOCATION, recovery 1's bulk activation is refused
    because it covers that same dead location, and the retry is refused again.
    `_write_chunk` then said `split and NOT activation`, so the per-location
    split -- the very thing its own docstring says exists to stop one dead
    location freezing every other shop -- was skipped on exactly this branch:
    set=0, written=[], BV-A and BV-B NEVER written. After a POS sale at BV-A the
    website kept selling the unit that had walked out, on every later sale and
    every 01:00 / 09:00 pass (the baseline stays None, so it re-fails forever).

    Revert to `if split and not activation and ...` -> A and B are unwritten,
    pushed 0, the baseline is None -> every assert below fails."""
    db = _listed(_db(a=1, b=1, c=0, sold=0))
    spy = _DeadLocationActivation(_responses(), LOC_C)
    _live(monkeypatch, spy)
    # the sale: BV-A's last unit walks out
    db.get_collection("stock_units").find_one_and_update(
        {"stock_id": "BV-A-u0", **item_events.on_hand_match()}, {"$set": {"status": "SOLD"}}
    )
    out = _run(wb.writeback_skus(db, ["SP-1"], "BV-A"))
    assert spy.rows() >= {(INV_GID, LOC_A, 0), (INV_GID, LOC_B, 1)}, "the live shops were written"
    assert out["pushed"] == 1 and out["failed"] == 1
    assert out["code"] == shopify_push.STOCK_ACTIVATION_FAILED
    base = _baseline(db)["quantities"]["SP-1"]
    assert base == {"BV-A": 0, "BV-B": 1}, "only the dead location is omitted"
    # ...and through the SCHEDULE, the same run is not-ok but the shops are live.
    spy2 = _DeadLocationActivation(_responses(), LOC_C)
    _live(monkeypatch, spy2)
    db.get_collection("stock_units").find_one_and_update(
        {"stock_id": "BV-B-u0", **item_events.on_hand_match()}, {"$set": {"status": "SOLD"}}
    )
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.ok is False and res.code == shopify_push.STOCK_ACTIVATION_FAILED
    assert (INV_GID, LOC_B, 0) in spy2.rows(), "B's post-sale 0 still reached Shopify"


def test_T7c_an_unrelated_user_error_never_activates(monkeypatch):
    """An error that is NOT ITEM_NOT_STOCKED_AT_LOCATION never activates -- but
    it IS retried split per location (round-3 P1) and it IS coded."""
    spy = _Spy(_responses(**{"inventorySetQuantities": _set_error("INVALID_QUANTITY", "bad")}))
    _live(monkeypatch, spy)
    out = _run(shopify_push.set_inventory_quantities(None, _rows3()))
    assert out["set"] == 0 and out["code"] == shopify_push.STOCK_WRITE_FAILED
    assert len(out["errors"]) == 1, "a split never inflates the caller's failure count"
    assert spy.calls_for("inventoryBulkToggleActivation") == []
    # the whole chunk, then one call per location (nothing landed either way)
    assert len(spy.calls_for("inventorySetQuantities")) == 4


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
        "ok": False,
        "code": shopify_push.STORE_UNMAPPED,
        "error": shopify_push.inventory._unmapped_error(
            [{"store_id": "BV-D", "store_code": "BV-D", "store_name": "Shop BV-D", "units": 1}]
        ),
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


# ---------------------------------------------------------------------------
# 9. Adversarial panel round 2 (2026-09-07): the oversell holes
# ---------------------------------------------------------------------------

INV_2 = "gid://shopify/InventoryItem/10"


def _two_skus(db):
    """SP-1 (INV_GID) + SP-2 (INV_2), both listed, both on the spine."""
    db.seed("products", [{"product_id": "spine-1", "sku": "SP-1"}, {"product_id": "spine-2", "sku": "SP-2"}])
    db.seed(
        "catalog_products",
        [
            _catalog_row("cat-1", "SP-1", gid=True),
            _catalog_row("cat-2", "SP-2", gid=True, shopify_inventory_item_id=INV_2),
        ],
    )
    return db


def test_P1_a_blocked_sku_never_clears_a_shop_whose_on_hand_read_died(monkeypatch):
    """P1 (OVERSELL): the SUPERADMIN block writes 0 -- but only where the pass
    actually READ the shelf. A blocked SKU used to re-insert EVERY store id,
    which made a dead shop look read: _unknown_stores went empty, ok flipped
    to True, and the OTHER SKU was never written at that shop at all, so
    Shopify kept its pre-sale number there. Revert either half (the `read`
    slice in online_stock_writeback, or `any(` in _unknown_stores) and the
    row set or the verdict below fails."""
    db = _two_skus(_db(a=2, b=1, c=0))
    db.seed(
        "ecom_collections",
        [{"collection_id": "C-BAN", "collection_type": "CUSTOM", "online_sync_blocked": True,
          "products": [{"sku": "SP-2", "position": 0}]}],
    )
    _break_shop(db, "BV-B")
    # THE RULE: BV-B is absent from BOTH SKUs -- the blocked 0 is not a read.
    assert wb.online_quantities_for_skus(db, ["SP-1", "SP-2"]) == {
        "SP-1": {"BV-A": 2, "BV-C": 0},
        "SP-2": {"BV-A": 0, "BV-C": 0},
    }
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    out = _run(shopify_push.push_skus_stock(db, ["SP-1", "SP-2"], source="sale"))
    # LIVE verdict (not just the preview's): a dead shop is never ok.
    assert out["ok"] is False and out["code"] == shopify_push.STOCK_ONHAND_UNKNOWN
    assert out["unknown_stores"] == ["BV-B"] and "BV-B" in (out["error"] or "")
    assert spy.rows() == {
        (INV_GID, LOC_A, 2), (INV_GID, LOC_C, 0),
        (INV_2, LOC_A, 0), (INV_2, LOC_C, 0),
    }
    assert not [r for r in spy.rows() if r[1] == LOC_B], "BV-B was written despite an unknown read"
    # The writer's own half of the rule, on a caller-precomputed batch (the POS
    # write-back / delist doors): a shop missing from ANY listed SKU is unknown.
    # With `all(...)` the one SKU that carries BV-B clears it and SP-2 is never
    # written there at all.
    ragged = _run(shopify_push.push_skus_stock(
        db, ["SP-1", "SP-2"], source="sale",
        quantities={"SP-1": {"BV-A": 2, "BV-B": 1, "BV-C": 0}, "SP-2": {"BV-A": 0, "BV-C": 0}},
    ))
    assert ragged["unknown_stores"] == ["BV-B"] and ragged["ok"] is False


def test_R1_two_shops_on_one_location_write_neither_and_say_so(monkeypatch):
    """R1 (OVERSELL + baseline corruption): Shopify takes ONE quantity per
    (item, location). Two shops on one gid used to send a duplicate pair in
    the same mutation -- the last one silently became that location's number
    AND the baseline's, losing the other shop's count for good. Revert the
    conflict exclusion in _mapped and the row set below grows the duplicate."""
    db = _listed(_db(a=2, b=1, c=0))
    db.get_collection("stores").update_one({"store_id": "BV-B"}, {"$set": {"shopify_location_id": LOC_A}})
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    out = _run(shopify_push.push_skus_stock(db, ["SP-1"], source="sale"))
    assert out["ok"] is False and out["code"] == shopify_push.STORE_LOCATION_DUPLICATE
    assert LOC_A in out["error"] and "BV-A" in out["error"] and "BV-B" in out["error"]
    # Only the unambiguous shop is written; neither claimant is.
    assert spy.rows() == {(INV_GID, LOC_C, 0)}
    assert _baseline(db)["quantities"] == {"SP-1": {"BV-C": 0}}
    # And they are reported as holders (they hold listed stock, nowhere to put it).
    assert {h["store_id"] for h in out["unmapped_stores"]} == {"BV-A", "BV-B"}


def test_a_legacy_store_doc_with_no_is_active_flag_is_still_a_shop(monkeypatch):
    """One rule, two spellings: routers/stores.py treats a MISSING is_active as
    active ("legacy docs"), stores_util used `is_active: True`. A legacy doc
    therefore fell out of physical_stores entirely -- its shelf stock was
    published nowhere AND it could never be an unmapped holder, so the run was
    green. Revert stores_util's `$ne: False` and both asserts fail."""
    db = _listed(_db(a=2, b=1, c=0))
    stores = db.get_collection("stores")
    stores.update_one({"store_id": "BV-B"}, {"$unset": {"is_active": ""}})
    assert wb.online_quantities_for_skus(db, ["SP-1"])["SP-1"] == {"BV-A": 2, "BV-B": 1, "BV-C": 0}
    # ... and unmapped, it is a holder rather than a silent hole.
    stores.update_one({"store_id": "BV-B"}, {"$unset": {"shopify_location_id": ""}})
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    out = _run(shopify_push.push_skus_stock(db, ["SP-1"], source="sale"))
    assert out["ok"] is False and out["code"] == shopify_push.STORE_UNMAPPED
    assert [h["store_id"] for h in out["unmapped_stores"]] == ["BV-B"]


def test_S1_a_sweep_where_nothing_changed_still_files_the_unmapped_task(monkeypatch):
    """S1: the design's named mitigation is a deduped SYSTEM task, but it was
    only reached inside push_skus_stock -- which the sweep calls for CHANGED
    products only. In the exact steady state the runbook creates (3 of 4 shops
    mapped, quiet catalogue) the task board never learned. Revert the
    _file_unmapped_task loop in sync_stock_levels and the task list is empty."""
    db = _listed(
        _db(a=2, b=1, c=0, d=3),
        online_stock={"quantities": {"SP-1": {"BV-A": 2, "BV-B": 1, "BV-C": 0}}, "tracked": True},
    )
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.action == "noop" and res.payload["changed"] == 0
    assert res.ok is False and res.code == shopify_push.STORE_UNMAPPED
    assert spy.calls_for("inventorySetQuantities") == []
    assert [t.get("source_ref") for t in db.get_collection("tasks").find({})] == [
        "shopify-store-unmapped:BV-D"
    ]


def test_S3_on_hand_at_a_store_id_no_shop_matches_is_named_not_silently_sold_out(monkeypatch):
    """S3 (silent undersell): 49 units stamped with a store CODE where the shop
    id is a UUID (the design's Pune hazard) count NOWHERE -- the website goes
    sold out and the run reads green. Revert orphan_stock_stores and ok is
    True with no code at all."""
    db = _listed(_db(a=0, b=0, c=0))
    db.get_collection("stock_units").insert_one(
        {"stock_id": "orphan-1", "product_id": "spine-1", "store_id": "9f2c-uuid-not-in-stores",
         "status": "AVAILABLE"}
    )
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    out = _run(shopify_push.push_skus_stock(db, ["SP-1"], source="sale"))
    assert out["orphan_stores"] == ["9f2c-uuid-not-in-stores"]
    assert out["ok"] is False and out["code"] == shopify_push.STOCK_STORE_ORPHAN
    assert "9f2c-uuid-not-in-stores" in out["error"]
    # The real shops are still written (the mapped rows always go out).
    assert spy.rows() == {(INV_GID, LOC_A, 0), (INV_GID, LOC_B, 0), (INV_GID, LOC_C, 0)}
    # A unit at a KNOWN-but-not-physical shop (closed / online) is not an orphan.
    assert wb.orphan_stock_stores(_db(a=1), ["SP-1"]) == []


def test_P2_zero_physical_shops_is_not_a_silent_ok_noop(monkeypatch):
    """P2 + the STRICT contract's two empties: zero physical shops returns the
    TRUTHY {sku: {}} (known: nothing to list) while "every shop failed"
    returns {} (unknown -> whole-batch abort). The truthy one slipped past
    `if not quantities`, so a shopless db reported ok=True having written
    nothing. Revert the `if not mapped` guard in push_skus_stock -> ok True."""
    db = _listed(_db(a=0, b=0, c=0, sold=0))
    db.get_collection("stores").delete_many({"store_type": "RETAIL"})
    db.get_collection("stock_units").delete_many({})
    assert wb.online_quantities_for_skus(db, ["SP-1"]) == {"SP-1": {}}  # truthy, known
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    out = _run(shopify_push.push_skus_stock(db, ["SP-1"], source="sale"))
    assert out["ok"] is False and out["code"] == shopify_push.STORE_UNMAPPED
    assert "no shop has a Shopify location" in out["error"]
    assert spy.calls_for("inventorySetQuantities") == [] and out["quantities"] == {}
    # The other empty: every shop's read failed -> {} -> the STRICT abort.
    class _DeadStock(StrictCollection):
        def aggregate(self, *a, **k):
            raise RuntimeError("stock read died")

    dead = _listed(_db(a=2, b=1, c=0))
    dead._collections["stock_units"] = _DeadStock("stock_units", [])
    assert wb.online_quantities_for_skus(dead, ["SP-1"]) == {}
    out2 = _run(shopify_push.push_skus_stock(dead, ["SP-1"], source="sale"))
    assert out2["ok"] is False and out2["code"] == shopify_push.STOCK_ONHAND_UNKNOWN


def test_no_mapped_shop_reports_a_failure_not_one_written(monkeypatch):
    """The fresh-catalogue first press (2026-09-07 reset): a listed product, no
    shop mapped yet. Tracking + DENY still go on (an untracked item sells
    without limit), but the pass must not count the listing as synced -- the
    page printed "1 of 1 listings changed, 1 written" with an empty
    transcript. Revert the stores_mapped half of the failed/synced test."""
    db = _listed(_db(a=0, b=0, c=0))
    db.get_collection("stores").update_many({}, {"$unset": {"shopify_location_id": ""}})
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.ok is False and res.code == shopify_push.STORE_UNMAPPED
    assert res.payload["synced"] == 0 and res.payload["failed"] == 1
    assert spy.calls_for("inventorySetQuantities") == []
    assert _baseline(db) is None


def test_the_store_read_failing_is_UNKNOWN_never_no_shops(monkeypatch):
    """_stores propagates a Mongo error on purpose ("an unknown shop list must
    never read as 'no shops'") -- nothing pinned it. Wrap _stores in a
    try/except returning [] and the code below becomes STORE_UNMAPPED."""

    class _DeadStores(StrictCollection):
        def find(self, *a, **k):
            raise RuntimeError("stores read died")

    db = _listed(_db(a=2, b=1, c=0))
    db._collections["stores"] = _DeadStores("stores", [])
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    out = _run(shopify_push.push_skus_stock(db, ["SP-1"], source="sale"))
    assert out["ok"] is False and out["code"] == shopify_push.STOCK_ONHAND_UNKNOWN
    assert spy.calls_for("inventorySetQuantities") == []
    # The path where the rule's own abort CANNOT mask it: the POS write-back /
    # delist doors hand in precomputed quantities. A swallowed store read there
    # is "no shops" (STORE_UNMAPPED, or worse a silent ok), never UNKNOWN.
    pre = _run(shopify_push.push_skus_stock(
        db, ["SP-1"], source="sale", quantities={"SP-1": {"BV-A": 2, "BV-B": 1, "BV-C": 0}},
    ))
    assert pre["ok"] is False and pre["code"] == shopify_push.STOCK_ONHAND_UNKNOWN
    assert spy.calls_for("inventorySetQuantities") == []


def test_P6_P7_the_summary_counts_what_shopify_accepted_not_what_was_planned(monkeypatch):
    """P6/P7 (reporting honesty): summary['quantities'] was filled in the
    row-build loop, BEFORE the write, so a refused chunk still printed the
    planned per-shop numbers as "last written" and writeback_skus counted
    every SKU as pushed when ANY chunk landed. Revert
    `summary["quantities"] = written_per_sku` and both asserts fail."""
    db = _two_skus(_db(a=2, b=1, c=0))
    from api.services.shopify_push import inventory as inv

    monkeypatch.setattr(inv, "_INVENTORY_SET_MAX", 3)  # one chunk per SKU
    spy = _Spy(_responses(inventorySetQuantities=[
        _ok_body("inventorySetQuantities", inventoryAdjustmentGroup={"createdAt": "now"}),
        _set_error("SOMETHING_ELSE", "chunk refused"),
    ]))
    _live(monkeypatch, spy)
    out = _run(wb.writeback_skus(db, ["SP-1", "SP-2"], "BV-A", source="sale"))
    assert out["pushed"] == 1 and out["failed"] == 1  # SP-1 landed, SP-2 did not
    res_rows = _baseline(db, "cat-2")
    assert res_rows is None, "a refused chunk must not seed a baseline"
    assert _baseline(db, "cat-1")["quantities"] == {"SP-1": {"BV-A": 2, "BV-B": 1, "BV-C": 0}}


def test_preview_first_names_a_missing_shopify_target_the_press_would_refuse(monkeypatch):
    """R3: the Preview-first checkbox promises "the SAME ok / code / error the
    live pass would report", but the whole-catalogue dry run never resolved a
    Shopify target -- the likeliest first-press failure on a rebuilt
    catalogue read GREEN. Revert the `missing` branch and the preview is ok."""
    db = _listed(_db(a=3, b=0, c=0), shopify_inventory_item_id=None)
    _live(monkeypatch, _explode)  # a preview makes ZERO calls
    prev = _run(shopify_push.sync_stock_levels(db, dry_run=True))
    assert prev.mode == "SIMULATED" and prev.ok is False
    assert prev.code == shopify_push.STOCK_TARGET_MISSING and "SP-1" in prev.error
    assert prev.payload["target_missing"] == ["SP-1"]
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    live = _run(shopify_push.sync_stock_levels(db))
    assert live.ok is False and live.code == shopify_push.STOCK_TARGET_MISSING
    assert spy.calls_for("inventorySetQuantities") == []


# ---------------------------------------------------------------------------
# 10. Panel round 3 (2026-09-07)
# ---------------------------------------------------------------------------


class _DeadLocation(_Spy):
    """Shopify refuses ANY inventorySetQuantities call that touches ``bad`` --
    a location the owner deleted, deactivated or renamed while its shop is
    still mapped. Nothing in such a call is applied."""

    def __init__(self, responses, bad):
        super().__init__(responses)
        self.bad = bad

    async def __call__(self, db, query, variables):  # noqa: ARG002
        if "inventorySetQuantities" in query and any(
            r["locationId"] == self.bad for r in variables["input"]["quantities"]
        ):
            self.calls.append({"query": query, "variables": variables})
            return {
                "data": {
                    "inventorySetQuantities": {
                        "inventoryAdjustmentGroup": None,
                        "userErrors": [
                            {
                                "field": ["input", "quantities", "2", "locationId"],
                                "message": "Location does not exist",
                                "code": "INVALID",
                            }
                        ],
                    }
                }
            }
        return await super().__call__(db, query, variables)


def test_R3_P1_one_dead_location_never_freezes_every_other_shops_number(monkeypatch):
    """R3 P1 (REAL OVERSELL). ONE inventorySetQuantities call carried up to 250
    rows across every mapped shop, and a chunk Shopify refused for any reason
    other than ITEM_NOT_STOCKED_AT_LOCATION applied NOT ONE of them. So a
    single Shopify location the owner deleted or deactivated froze EVERY other
    shop's number at its pre-sale value: the unit walks out of BV-A, the write
    is refused wholesale, and bettervision.in keeps selling it -- repeated by
    every later sale and every 01:00 / 09:00 pass until a human reads the
    sync_runs row. The writer already refuses to withhold mapped rows for the
    STORE_UNMAPPED case; this is the same rule.

    Revert the per-location split in `_write_chunk` -> written is empty, the
    baseline is None and BV-A keeps its pre-sale 1 -> every assert below fails.
    """
    db = _listed(_db(a=1, b=1, c=0, sold=0))
    spy = _DeadLocation(_responses(), LOC_C)
    _live(monkeypatch, spy)
    # the sale: BV-A's last unit walks out
    db.get_collection("stock_units").find_one_and_update(
        {"stock_id": "BV-A-u0", **item_events.on_hand_match()}, {"$set": {"status": "SOLD"}}
    )
    out = _run(wb.writeback_skus(db, ["SP-1"], "BV-A"))
    # the survivors landed -- A's 0 above all
    assert spy.rows() >= {(INV_GID, LOC_A, 0), (INV_GID, LOC_B, 1)}
    assert out["pushed"] == 1 and out["failed"] == 1
    assert out["code"] == shopify_push.STOCK_WRITE_FAILED, "a refused write is never codeless"
    assert "Location does not exist" in (out.get("error") or "")
    base = _baseline(db)["quantities"]["SP-1"]
    assert base == {"BV-A": 0, "BV-B": 1}, "the dead location is omitted, so the next pass re-sends it"
    # one call for the whole chunk, then one per location: only LOC_C is lost
    assert len(spy.calls_for("inventorySetQuantities")) == 4


def test_R3_P2_the_pre_press_gate_counts_mapped_the_way_the_writer_does(monkeypatch):
    """R3 P2: two spellings of "mapped". push_mode_status counted raw gids
    while the writer's _mapped drops BOTH shops of a location two shops claim,
    so the "Stock locations" gate chip -- the one the runbook makes the owner
    read BEFORE the first press -- went GREEN ("3 of 3 shops mapped") in
    exactly the case the press refuses (STORE_LOCATION_DUPLICATE, one row
    written). Revert to `sum(1 for r if r["shopify_location_id"])` -> 3 -> fails."""
    monkeypatch.setattr(shopify_push, "_graphql", _explode)
    db = _db()
    db.get_collection("stores").update_one(
        {"store_id": "BV-B"}, {"$set": {"shopify_location_id": LOC_A}}
    )
    status = shopify_push.push_mode_status(db)
    assert status["stores_total"] == 3 and status["stores_mapped"] == 1
    assert sorted(s["store_id"] for s in status["unmapped_stores"]) == ["BV-A", "BV-B"]
    # ...which is exactly what the press then does.
    out = _run(shopify_push.push_skus_stock(_listed(db), ["SP-1"], source="button", dry_run=True))
    assert out["ok"] is False and out["code"] == shopify_push.STORE_LOCATION_DUPLICATE
    assert out["stores_mapped"] == 1


def test_R3_P3_the_dry_run_product_plan_carries_the_writers_own_code(monkeypatch):
    """R3 P3: plan_product_stock swallowed a store-list failure into
    `stores = []` and returned no code at all -- and that dict IS the `stock`
    block of the dry-run product push, the preview an operator reads before a
    first publish, while the live press returns STOCK_ONHAND_UNKNOWN and
    writes nothing. Round 2 fixed this preview-vs-press divergence for
    sync_stock_levels and left the per-product plan behind. Revert the
    code/error/stores_total=None branch -> code is None -> fails."""
    db = _listed(_db(a=2, b=1, c=0))
    _dark(monkeypatch)
    from api.services.shopify_push import inventory as inv

    def _dead(_db_):
        raise RuntimeError("store list died")

    monkeypatch.setattr(inv, "_stores", _dead)
    plan = inv.plan_product_stock(db, db.get_collection("catalog_products").find_one({"id": "cat-1"}), [])
    assert plan["ok"] is False and plan["code"] == shopify_push.STOCK_ONHAND_UNKNOWN
    assert "store read failed" in plan["error"]
    assert plan["stores_total"] is None, "unknown is never 0"
    # ...and the plain "no shop mapped at all" case is coded too.
    monkeypatch.setattr(inv, "_stores", lambda _d: [])
    plan2 = inv.plan_product_stock(db, db.get_collection("catalog_products").find_one({"id": "cat-1"}), [])
    assert plan2["ok"] is False and plan2["code"] == shopify_push.STORE_UNMAPPED
    assert plan2["stores_total"] == 0


def test_R3_P4_a_shop_holding_exactly_the_buffer_is_still_an_unmapped_holder(monkeypatch):
    """R3 P4 (invariant 6, broken by the safety buffer): unmapped_holders summed
    the POST-allocation quantities, so with buffer B a shop sitting on exactly
    B units reported 0 units, was neither named nor tasked, and the run came
    back fully green over a shop whose whole shelf is invisible online.
    HOLDS is the shelf, not the published number. Revert unmapped_holders to
    the buffered `quantities` -> unmapped_stores [] and ok True -> fails."""
    monkeypatch.setenv("ONLINE_STOCK_SAFETY_BUFFER", "1")
    db = _listed(_db(a=2, b=1, c=0, d=1))  # BV-D unmapped, holding exactly the buffer
    assert wb.online_quantities_for_skus(db, ["SP-1"])["SP-1"]["BV-D"] == 0  # publishes nothing
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.ok is False and res.code == shopify_push.STORE_UNMAPPED
    assert [h["store_id"] for h in res.payload["unmapped_stores"]] == ["BV-D"]
    assert res.payload["unmapped_stores"][0]["units"] == 1
    assert "shopify-store-unmapped:BV-D" in [
        t.get("source_ref") for t in db.get_collection("tasks").find({})
    ]


def test_R3_P6_a_sku_that_left_the_product_never_marks_it_changed_forever(monkeypatch):
    """R3 P6: stock_changed compared the WHOLE baseline against a slice built
    only from the product's CURRENT SKUs, so a retired size row -- or, far more
    commonly, a SKU Shopify never accepted (the baseline records only accepted
    SKUs while the slice offers every one) -- left a key that can never match.
    The product then read as changed on EVERY 01:00 / 09:00 pass forever, which
    is exactly the "changed products only" property the schedule rests on and
    the noise that buries a real STORE_UNMAPPED report. Revert the `skus`
    restriction in stock_changed -> action "sync", changed 1 -> fails."""
    db = _listed(
        _db(a=2, b=1, c=0),
        online_stock={
            "tracked": True,
            "quantities": {
                "SP-1": {"BV-A": 2, "BV-B": 1, "BV-C": 0},
                "GONE-SKU": {"BV-A": 1},  # a size row retired off the parent
            },
        },
    )
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.action == "noop" and res.payload["changed"] == 0
    assert spy.calls_for("inventorySetQuantities") == []
    # ...and a REAL change still re-sends (the diff is not simply disarmed).
    db.get_collection("stock_units").find_one_and_update(
        {"stock_id": "BV-A-u0", **item_events.on_hand_match()}, {"$set": {"status": "SOLD"}}
    )
    assert _run(shopify_push.sync_stock_levels(db)).payload["changed"] == 1


def _locations(*nodes):
    return {"data": {"locations": {"nodes": list(nodes)}}}


def _loc(gid, name, *, fulfils=True, active=True):
    return {
        "id": gid,
        "name": name,
        "isActive": active,
        "fulfillsOnlineOrders": fulfils,
        "shipsInventory": True,
        "address": {"city": "Bokaro", "province": "Jharkhand"},
    }


def test_R3_a_fulfilling_shopify_location_with_no_shop_is_never_a_green_run(monkeypatch):
    """R3 (invariant 2, HIGH): a Shopify location that FULFILS ONLINE ORDERS
    but maps to no IMS shop keeps routing and selling whatever number it holds,
    and IMS -- which writes per shop, never a pooled total -- never touches it.
    That rule existed ONLY in React (an amber line a SUPERADMIN had to be
    looking at): no backend verdict mentioned Shopify's own location list, so
    the 01:00 / 09:00 pass recorded a fully green run beside it and filed no
    task. Revert the `stray_locations` term in _all_ok / _verdict -> ok True,
    code None -> fails."""
    db = _listed(_db(a=2, b=1, c=0))
    spy = _Spy(_responses(**{
        "imsLocationList": _locations(
            _loc(LOC_A, "Bokaro"), _loc(LOC_B, "Dhanbad"), _loc(LOC_C, "Sector 4"),
            _loc("gid://shopify/Location/76684427513", "GANGADHAM- PUNE"),
        )
    }))
    _live(monkeypatch, spy)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.ok is False and res.code == shopify_push.SHOPIFY_LOCATION_UNMAPPED
    assert "GANGADHAM- PUNE" in res.error
    assert res.payload["unmapped_locations"] == [
        {"id": "gid://shopify/Location/76684427513", "name": "GANGADHAM- PUNE"}
    ]
    assert "shopify-location-unmapped:gid://shopify/Location/76684427513" in [
        t.get("source_ref") for t in db.get_collection("tasks").find({})
    ]
    # ...the mapped shops were still written (never withhold a real number)...
    assert spy.rows() == {(INV_GID, LOC_A, 2), (INV_GID, LOC_B, 1), (INV_GID, LOC_C, 0)}
    # ...and a location that does NOT fulfil online orders is nobody's problem.
    db2 = _listed(_db(a=2, b=1, c=0))
    spy2 = _Spy(_responses(**{
        "imsLocationList": _locations(
            _loc(LOC_A, "Bokaro"), _loc(LOC_B, "Dhanbad"), _loc(LOC_C, "Sector 4"),
            _loc("gid://shopify/Location/999", "Warehouse", fulfils=False),
        )
    }))
    _live(monkeypatch, spy2)
    assert _run(shopify_push.sync_stock_levels(db2)).ok is True


def test_R3_a_first_publish_whose_stock_was_refused_is_not_a_clean_success(monkeypatch):
    """R3 ops P5: the owner's FIRST "Send to website" press on the rebuilt
    catalogue, before any shop is mapped. The press switches tracking on with
    the DENY policy and publishes -- a listing LIVE on bettervision.in reading
    sold out at every location -- while the stock sub-summary carries
    STORE_UNMAPPED and nothing renders it: formatPushResult reads only the
    top-level ok / code / error, so the toast was green. ok stays True (the
    product IS live, exactly like PRICE_NOT_SYNCED) but the code and the plain
    line now come out where every screen reads them. Revert the
    stock_not_written branch -> code None -> fails."""
    db = StrictDB()
    db.seed("stores", [_store("BV-A"), _store("BV-ONLINE-01", store_type="ONLINE")])
    db.seed("products", [{"product_id": "spine-1", "sku": "SP-1"}])
    db.seed("stock_units", [{"stock_id": "u0", "product_id": "spine-1", "store_id": "BV-A", "status": "AVAILABLE"}])
    _listed(db)
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, db.get_collection("catalog_products").find_one({"id": "cat-1"}), []))
    assert res.ok is True and res.mode == "LIVE", "the publish itself is never withheld for stock"
    assert res.code == shopify_push.STORE_UNMAPPED
    # Round-4 P2: the line the operator gets is the one that MATTERS -- nothing
    # was written anywhere -- not "BV-A holds stock, map it" (true, but it
    # leaves him thinking the other shops went out).
    assert "no shop has a Shopify location -- nothing written" in res.error
    assert res.stock["ok"] is False and res.stock["set"] == 0


def test_R3_a_blocked_sku_in_a_dead_batch_is_still_the_strict_abort():
    """R3 (suite gap): online_quantities_for_skus' whole-batch abort
    (`if stores and not read: return {}`) survived being mutated to
    `if False:` with the whole suite green -- it is only observable when a
    SUPERADMIN-blocked SKU is in the batch AND every shop's on-hand read
    failed, because the block loop then builds a TRUTHY {sku: {}} that
    push_skus_stock reads as "known" instead of aborting. Mutate the guard away
    -> {"SP-1": {}} != {} -> this fails."""
    db = _listed(_db(a=2, b=1, c=0))
    db.seed("collections", [{"collection_id": "c1", "online_blocked": True, "product_ids": ["cat-1"]}])

    class _DeadStock(StrictCollection):
        def aggregate(self, *a, **k):
            raise RuntimeError("every shop's read died")

    db._collections["stock_units"] = _DeadStock("stock_units", [])
    import api.services.online_block as ob

    original = ob.blocked_skus
    try:
        ob.blocked_skus = lambda _db, skus: list(skus)  # the SUPERADMIN block
        assert wb.online_quantities_for_skus(db, ["SP-1"]) == {}, "UNKNOWN, never a known 0"
    finally:
        ob.blocked_skus = original


def test_R3_parity_never_counts_a_shop_shopify_cannot_see(monkeypatch):
    """R3 (parity's known second rule): _pooled_availability summed on-hand
    over ALL physical shops and compared it against the sum of a SKU's Shopify
    inventoryLevels -- which only exist at MAPPED locations. An unmapped shop
    holding 3 units of a listed SKU therefore read as 3 units of drift on a
    perfectly correct system: past the default tolerance of 2, a false drift
    row and the deduped shopify-stock-parity-drift task. Running it through
    online_quantities_for_skus at buffer 0, restricted to inventory._mapped,
    also fixes the SUPERADMIN-blocked divergence the comment already named.
    Revert to `_on_hand_for_skus(db, skus, None)` -> 3 -> fails."""
    from api.services import shopify_stock_parity as parity

    db = _listed(_db(a=0, b=0, c=0, sold=0, d=3))  # every mapped shop empty, BV-D unmapped
    assert parity._pooled_availability(db, ["SP-1"]) == {"SP-1": 0}
    # a mapped shop's units DO count
    db2 = _listed(_db(a=2, b=1, c=0, sold=0, d=3))
    assert parity._pooled_availability(db2, ["SP-1"]) == {"SP-1": 3}


# ---------------------------------------------------------------------------
# Panel round 4 (2026-09-07)
# ---------------------------------------------------------------------------


def test_R4_P2_a_parent_with_mixed_variant_keys_writes_every_size(monkeypatch):
    """ROUND-4 P2, SILENT phantom stock. The sweep indexed catalog_variants
    TWICE (by parent_product_id, by parent_sku) and then picked
    `by_pid.get(pid) OR by_sku.get(sku)` -- an EITHER/OR over two indexes that
    are not alternatives. product_master._variant_row keys a size row on
    `parent.pim_product_id or parent.product_id`, so a size created BEFORE the
    parent's catalog twin existed carries the SPINE id and one created after
    carries the CATALOG id: a mixed set for one parent. The ONE row that landed
    in by_pid then hid EVERY row that only landed in by_sku -- that size's
    inventory item was written at NO location, while the run reported ok=True,
    synced=1, no task, no unknown_stores, no target_missing. IMS silently
    stopped being the master of that number, so a sale of it oversells forever.

    Revert `merge_variant_rows(...)` to `by_pid.get(pid) or by_sku.get(sku)`
    -> InventoryItem/88 is never sent -> this fails."""
    med_item = "gid://shopify/InventoryItem/88"
    db = StrictDB()
    db.seed("stores", [_store("BV-A", LOC_A)])
    db.seed(
        "products",
        [
            {"product_id": "sp-parent", "sku": "RB-META"},
            {"product_id": "sp-large", "sku": "RB-META-L"},
            {"product_id": "sp-medium", "sku": "RB-META-M"},
        ],
    )
    db.seed(
        "stock_units",
        [
            {"stock_id": "p0", "product_id": "sp-parent", "store_id": "BV-A", "status": "AVAILABLE"},
            {"stock_id": "l0", "product_id": "sp-large", "store_id": "BV-A", "status": "AVAILABLE"},
            {"stock_id": "l1", "product_id": "sp-large", "store_id": "BV-A", "status": "AVAILABLE"},
            {"stock_id": "m0", "product_id": "sp-medium", "store_id": "BV-A", "status": "AVAILABLE"},
            {"stock_id": "m1", "product_id": "sp-medium", "store_id": "BV-A", "status": "AVAILABLE"},
            {"stock_id": "m2", "product_id": "sp-medium", "store_id": "BV-A", "status": "AVAILABLE"},
        ],
    )
    db.seed("catalog_products", [_catalog_row("cat-parent", "RB-META", gid=True)])
    db.seed(
        "catalog_variants",
        [
            # created AFTER the catalog twin -> the CATALOG id
            {
                "variant_id": "v-l",
                "sku": "RB-META-L",
                "parent_product_id": "cat-parent",
                "parent_sku": "RB-META",
                "shopify_inventory_item_id": "gid://shopify/InventoryItem/77",
            },
            # created BEFORE it -> the SPINE id, same parent
            {
                "variant_id": "v-m",
                "sku": "RB-META-M",
                "parent_product_id": "sp-parent",
                "parent_sku": "RB-META",
                "shopify_inventory_item_id": med_item,
            },
        ],
    )
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.payload["candidates"] == 1 and res.payload["changed"] == 1
    rows = spy.rows()
    assert (med_item, LOC_A, 3) in rows, "the sku-linked size was written, not silently dropped"
    assert ("gid://shopify/InventoryItem/77", LOC_A, 2) in rows
    assert (INV_GID, LOC_A, 1) in rows, "the parent's own item still goes out"
    assert _baseline(db, "cat-parent")["quantities"] == {
        "RB-META-L": {"BV-A": 2},
        "RB-META-M": {"BV-A": 3},
        "RB-META": {"BV-A": 1},
    }


def test_R4_P2_the_union_is_ONE_rule_the_price_push_and_the_engine_share():
    """The identical exclusive-fallback shape was COPIED into
    shopify_live_sync.variants_for_product (the PRICE push -- the same size
    then ships at the parent's price) and online_discount_engine._load_variants.
    All three now read online_catalog.variant_rows_for_product /
    merge_variant_rows. Revert either copy to `if not rows: rows = ...` and the
    sku-linked row disappears from that caller -> this fails."""
    from api.services import online_catalog, online_discount_engine, shopify_live_sync

    db = StrictDB()
    parent = {"id": "cat-parent", "sku": "RB-META"}
    db.seed("catalog_products", [parent])
    db.seed(
        "catalog_variants",
        [
            {"variant_id": "v-l", "sku": "RB-META-L", "parent_product_id": "cat-parent"},
            {"variant_id": "v-m", "sku": "RB-META-M", "parent_sku": "RB-META"},
        ],
    )
    want = ["RB-META-L", "RB-META-M"]
    assert [v["sku"] for v in online_catalog.variant_rows_for_product(db, parent)] == want
    assert [v["sku"] for v in shopify_live_sync.variants_for_product(db, parent)] == want
    assert [v["sku"] for v in online_discount_engine._load_variants(db, parent)] == want


def test_R4_P1_a_dry_run_with_no_shop_mapped_is_never_ok(monkeypatch):
    """ROUND-4 P1 (HOLLOW GUARD -- nothing pinned it; deleting `bool(mapped)
    and` from `_rows_ok` left the whole stock suite green). That clause is the
    ONLY carrier of ok=False for the SIMULATED / dry-run verdict when no shop is
    mapped: the LIVE branch is carried by a DIFFERENT guard (`if not mapped:
    return summary`, before `_rows_ok` is ever called), so this clause governs
    the PREVIEW alone -- precisely the preview-vs-press divergence its own
    docstring exists to prevent, and precisely the state prod is in after the
    2026-09-07 catalogue deletion. Without it the preview reads ok=True with the
    failure code still set, and the first publish's toast is green over a
    listing that went tracked=true + DENY with no quantity anywhere.

    Delete `bool(mapped) and` -> ok True -> this fails."""
    db = _listed(_db(a=0, b=0, c=0, sold=0))
    db.get_collection("stores").update_many({}, {"$unset": {"shopify_location_id": ""}})
    db.get_collection("stock_units").delete_many({})
    _live(monkeypatch, _Spy(_responses()))
    out = _run(shopify_push.push_skus_stock(db, ["SP-1"], source="sale", dry_run=True))
    assert out["stores_mapped"] == 0 and out["code"] == shopify_push.STORE_UNMAPPED
    assert out["ok"] is False, "a green preview must mean a press would write something"


def test_R4_P2_no_shop_mapped_says_nothing_was_written_not_only_map_them(monkeypatch):
    """ROUND-4 P2: the copy was TRUE but not the TRUE THING. push_skus_stock
    assigned its code/error with `or`, so the HOLDERS line ("shops holding
    listed stock with no Shopify location: ... map them") always won whenever
    any unmapped shop held a listed unit -- which, on a fresh catalogue with
    zero shops mapped, is true the moment stock exists. `_no_mapping_error()`,
    the line that matters, could never fire, so the operator was never told that
    NOTHING was written at all while the press had already switched the variant
    to tracked=true + DENY. sync_stock_levels' own ladder already put "nothing
    writable" first: there is ONE ladder now, `_verdict_for`.

    Revert to the `or` assignments -> the holders line -> this fails."""
    db = _listed(_db(a=2, b=0, c=0, sold=0))
    db.get_collection("stores").update_many({}, {"$unset": {"shopify_location_id": ""}})
    _live(monkeypatch, _Spy(_responses()))
    out = _run(shopify_push.push_skus_stock(db, ["SP-1"], source="product_push"))
    assert out["code"] == shopify_push.STORE_UNMAPPED and out["set"] == 0
    assert "no shop has a Shopify location -- nothing written" in out["error"]
    # the sweep's verdict says the same thing, from the same ladder
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.code == shopify_push.STORE_UNMAPPED
    assert res.error == out["error"]


def test_R4_P2_an_unmapped_shop_whose_read_died_is_reported_not_scored_zero():
    """ROUND-4 P2 (silent fallback). BV-D has no Shopify location, holds 3 units
    of a listed SKU, and its on-hand aggregate dies this pass. The rule omits a
    shop it could not read, `unmapped_holders` then scored it 0 units ("not a
    holder"), and `_unknown_stores` only ever looked at the MAPPED shops -- so
    no guard mentioned D at all and the run was green over 3 units invisible
    online. Invariant 6 is ONE rule: a shop the pass could not read is named,
    mapped or not, and an unreadable shelf is never scored 0.

    Revert `_unknown_stores` to iterate `mapped`, or restore the
    `held = quantities` fallback in `unmapped_holders` -> ok True -> fails."""
    db = _listed(_db(a=2, b=1, c=0, d=3))
    _break_shop(db, "BV-D")
    quantities = wb.online_quantities_for_skus(db, ["SP-1"])
    assert "BV-D" not in quantities["SP-1"], "the rule omits the shop it could not read"
    out = _run(
        shopify_push.push_skus_stock(
            db, ["SP-1"], quantities=quantities, source="sale", dry_run=True
        )
    )
    assert "BV-D" in out["unknown_stores"]
    assert out["ok"] is False
    # ...and it is reported as an unmapped shop of UNKNOWN size, never as 0.
    holder = next(h for h in out["unmapped_stores"] if h["store_id"] == "BV-D")
    assert holder["units"] is None


def test_R4_P3_a_dead_buffer_read_never_scores_an_unmapped_shop_zero(monkeypatch):
    """ROUND-4 P3 (latent). `unmapped_holders` re-reads the shelf at buffer 0 so
    a shop sitting on exactly the safety buffer is still a holder -- but the
    re-read was wrapped in try/except with `held = quantities` as the fallback,
    i.e. straight back to the POST-allocation numbers the re-read exists to
    avoid. On the day the owner sets the buffer to 1 (Q6(b)), a shop holding
    exactly 1 unit publishes 0, is not named, is not tasked and the run is fully
    green over a shop whose entire shelf is invisible online -- the very rule
    test_R3_P4 forbids, restored by an exception.

    Restore `held = quantities` -> BV-D scores 0, is not a holder -> fails."""
    db = _listed(_db(a=2, b=1, c=0, d=1))
    real = wb.online_quantities_for_skus

    def _buffer0_dies(_db, skus, *, safety_buffer=None):
        if safety_buffer == 0:
            raise RuntimeError("the buffer-0 re-read died")
        return real(_db, skus, safety_buffer=safety_buffer)

    monkeypatch.setattr(wb, "online_quantities_for_skus", _buffer0_dies)
    quantities = real(db, ["SP-1"], safety_buffer=1)
    assert quantities["SP-1"]["BV-D"] == 0, "post-allocation, the holder looks empty"
    out = _run(
        shopify_push.push_skus_stock(
            db, ["SP-1"], quantities=quantities, source="sale", dry_run=True
        )
    )
    holder = next((h for h in out["unmapped_stores"] if h["store_id"] == "BV-D"), None)
    assert holder is not None and holder["units"] is None
    assert out["ok"] is False and out["code"] == shopify_push.STORE_UNMAPPED


def test_R4_P3_a_stray_location_makes_the_sales_own_run_row_not_ok(monkeypatch):
    """ROUND-4 P3: a green run over a live oversell. `push_skus_stock` never
    runs invariant 2's stray-location guard (it lives in `sync_stock_levels`,
    which reads Shopify's locations once per run), so a POS sale wrote its SKU,
    reported code None and filed a sync_runs row saying `ok: True,
    items_synced: 1` -- while Shopify kept routing online orders to a fourth
    location no IMS shop carries and selling whatever number it holds. A
    per-sale locations read is not worth it (a stray location only appears when
    a human edits Shopify admin), so the sale CARRIES the last sweep's verdict.

    Revert the `_last_stray_locations` block in `_record_run` -> ok True ->
    this fails."""
    db = _listed(_db(a=2, b=1, c=0, sold=0))
    stray = {"id": "gid://shopify/Location/1004", "name": "Gangadham Pune"}
    nodes = [
        {"id": LOC_A, "name": "A", "isActive": True, "fulfillsOnlineOrders": True},
        {"id": LOC_B, "name": "B", "isActive": True, "fulfillsOnlineOrders": True},
        {"id": LOC_C, "name": "C", "isActive": True, "fulfillsOnlineOrders": True},
        {**stray, "isActive": True, "fulfillsOnlineOrders": True},
    ]
    locations = {"imsLocationList": {"data": {"locations": {"nodes": nodes}}}}
    _live(monkeypatch, _Spy(_responses(**locations)))
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.code == shopify_push.SHOPIFY_LOCATION_UNMAPPED
    assert shopify_push.last_stray_locations(db) == [stray]
    # now the SALE -- one write, no locations read of its own
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    db.get_collection("stock_units").find_one_and_update(
        {"stock_id": "BV-A-u0", **item_events.on_hand_match()}, {"$set": {"status": "SOLD"}}
    )
    out = _run(wb.writeback_skus(db, ["SP-1"], "BV-A"))
    assert out["pushed"] == 1 and spy.calls_for("imsLocationList") == []
    row = db.get_collection("sync_runs").find_one({"kind": "stock_writeback"})
    assert row["ok"] is False, "the row a sale produced must not say the website was corrected"
    assert "SHOPIFY_LOCATION_UNMAPPED" in row["error"] and "Gangadham Pune" in row["error"]


def test_R4_P2_the_live_payload_prints_what_shopify_accepted_not_the_plan(monkeypatch):
    """ROUND-4 P2 (money-adjacent copy). `payload['plan']` was built from the
    PRE-write mapped slice and the LIVE branch never replaced it, while
    push_skus_stock has replaced its own summary["quantities"] with the ACCEPTED
    rows since round 2 -- and the sync page renders `plan`, not that summary,
    under "Per shop:". With BV-C's Shopify location deleted, the page read
    "0 written, 1 failed" and then printed BV-C's 3 as a number that never
    reached Shopify.

    Revert the `"plan": accepted[:50]` line -> BV-C is printed -> this fails."""
    db = _listed(_db(a=2, b=1, c=3, sold=0))
    spy = _DeadLocation(_responses(), LOC_C)
    _live(monkeypatch, spy)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.ok is False and res.code == shopify_push.STOCK_WRITE_FAILED
    printed = res.payload["plan"][0]["quantities"]["SP-1"]
    assert printed == {"BV-A": 2, "BV-B": 1}, "the refused location is not printed as written"


def test_R4_P3_the_first_night_over_an_empty_catalogue_files_no_location_task(monkeypatch):
    """ROUND-4 P3 (ops noise). The 01:00 tick after the 2026-09-07 catalogue
    deletion: zero listings, zero shops mapped, Shopify still holding its two
    online-fulfilling locations. Both stray-location statements are true, but
    with `candidates == 0` no listing can oversell from them, so the owner's
    first night handed him a red sync page AND two unassigned P1 SYSTEM tasks
    about a system with nothing on it. The VERDICT still reports it; only the
    TASK waits until there is something to sell.

    Remove the `if pairs:` gate -> two tasks -> this fails."""
    db = _db(a=0, b=0, c=0, sold=0)  # no catalog_products at all
    nodes = [
        {"id": "gid://shopify/Location/2001", "name": "Sector 4", "isActive": True,
         "fulfillsOnlineOrders": True},
        {"id": "gid://shopify/Location/2002", "name": "Pune", "isActive": True,
         "fulfillsOnlineOrders": True},
    ]
    locations = {"imsLocationList": {"data": {"locations": {"nodes": nodes}}}}
    _live(monkeypatch, _Spy(_responses(**locations)))
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.payload["candidates"] == 0
    assert res.code == shopify_push.SHOPIFY_LOCATION_UNMAPPED, "the verdict still says it"
    assert list(db.get_collection("tasks").find({})) == []
    # ...and the moment there IS a listing, the tasks are filed.
    _listed(db)
    _live(monkeypatch, _Spy(_responses(**locations)))
    _run(shopify_push.sync_stock_levels(db))
    assert len(list(db.get_collection("tasks").find({}))) == 2


def test_R4_P4_the_stray_location_rule_is_the_backends_not_the_pages():
    """ROUND-4 P4 (one rule, two implementations -- display echo). The sync page
    re-derived "fulfils online orders and maps to no IMS shop" in TypeScript,
    and the two spellings already differed: the backend requires `isActive`
    truthy, the page used `isActive !== false`, so a location with no isActive
    field was reported on the page and fine in the verdict. GET /push/locations
    now stamps the WRITER's own predicate on every row and the page renders
    that. Loosen `is_stray_fulfilling` to the page's `!== false` -> the
    no-isActive row flips -> this fails."""
    have = {LOC_A}
    ok = shopify_push.is_stray_fulfilling
    assert ok({"id": LOC_B, "isActive": True, "fulfillsOnlineOrders": True}, have) is True
    assert ok({"id": LOC_A, "isActive": True, "fulfillsOnlineOrders": True}, have) is False
    assert ok({"id": LOC_B, "fulfillsOnlineOrders": True}, have) is False, "no isActive is not active"
    assert ok({"id": LOC_B, "isActive": True}, have) is False
