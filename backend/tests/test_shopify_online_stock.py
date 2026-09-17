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
  Panel round 5 (2026-09-12): a re-map of a shop holding no listed stock is
      still seen by the diff and the NEW location still written (P1, in
      test_store_shopify_location.py); two SKUs on one Shopify inventory item
      write NEITHER, with a code at the BOTTOM of the ladder so it cannot hide
      a live STORE_UNMAPPED; ONE locations read answers BOTH questions -- a
      MAPPED shop whose location cannot sell online (unticked / deactivated /
      gone) is never a green run, and an EMPTY locations answer is UNKNOWN, not
      "every shop dead"; the PRODUCT press carries invariant 2 too (recorded
      verdict, and the one read itself on day 1).
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
# A SECOND listing's own item -- one Shopify inventory item is ONE SKU's shelf.
INV_TWO = "gid://shopify/InventoryItem/10"
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
        # Shopify's location list, READ and answering the three mapped shops,
        # ticked. It used to be unanswered ({'data': {}} -> nodes [] -> read
        # False), so EVERY green pin in this file exercised the unread branch
        # and none could notice that an unreadable list scored as "no stray,
        # no dead" (recheck round 1). Unread is now its own not-ok line, so a
        # green pin needs a real answer; override it to test the failures.
        "imsLocationList": _locations(_loc(LOC_A, "Bokaro"), _loc(LOC_B, "Dhanbad"), _loc(LOC_C, "Sector 4")),
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
    # The 01:00 / 09:00 pass diffs against the SAME baseline -> noop, no WRITE
    # (a LIVE pass still makes its one read-only locations query -- and an
    # unanswered one is no longer a green pass, so it is answered here).
    quiet = _Spy(_responses())
    _live(monkeypatch, quiet)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.action == "noop" and res.ok is True and res.payload["changed"] == 0
    assert quiet.writes() == []


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
            # Its OWN inventory item: one Shopify item is one SKU's shelf, and
            # two listings sharing one is the STOCK_TARGET_DUPLICATE refusal
            # (test_R7_two_LISTINGS_on_one_inventory_item_write_NEITHER), not
            # the "only changed products go out" rule this test is about.
            _catalog_row("cat-2", "SP-2", gid=True, shopify_inventory_item_id=INV_TWO,
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


def _spine_off(db, sku):
    """What the retire hook / the DELETE door does BEFORE the delist row runs:
    the spine's is_active off -- the only off-sale marker the rule reads."""
    db.get_collection("products").update_one({"sku": sku}, {"$set": {"is_active": False}})


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


def test_R5_two_skus_on_one_inventory_item_write_NEITHER(monkeypatch):
    """ROUND-5 (wrong number + permanent not-ok). A mis-stamped mapping (two
    SKUs, one Shopify inventory item) must not send a duplicate (item,
    location) pair -- Shopify refuses the whole chunk. It used to name the
    SECOND SKU and write the FIRST, so ITERATION ORDER picked which shelf the
    website showed: product_skus returns the variant rows (sorted by sku)
    before the product's own SKU, so an alphabetically earlier size row won by
    accident and Shopify showed 1 behind a variant IMS held 4 units for. The
    baseline then carried only the winner while the diff compared both, so the
    listing was "changed" with ok=False on EVERY 01:00 / 09:00 pass forever and
    never self-healed.

    Same answer as two shops on one location: NEITHER is written, once, loudly.
    Restore the `item_of` winner-takes-first branch -> a row is written and the
    code is None -> this fails."""
    db = _listed(_db(a=3, b=0, c=0, sku="PARENT-1"), sku="PARENT-1")
    db.seed("products", [{"product_id": "spine-x", "sku": "AAA-SIZE-L"}])
    db.seed("stock_units", [{"stock_id": "x1", "product_id": "spine-x", "store_id": "BV-A", "status": "AVAILABLE"}])
    db.seed("catalog_variants", [
        {"sku": "AAA-SIZE-L", "parent_product_id": "cat-1", "shopify_inventory_item_id": INV_GID}
    ])
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    out = _run(shopify_push.push_skus_stock(db, ["AAA-SIZE-L", "PARENT-1"], source="test"))
    assert spy.rows() == set(), "one quantity per (item, location): neither shelf is guessed at"
    assert out["ok"] is False and out["code"] == shopify_push.STOCK_TARGET_DUPLICATE
    assert "AAA-SIZE-L, PARENT-1" in out["error"] and INV_GID in out["error"]
    assert _baseline(db) is None, "nothing was written, so nothing enters the baseline"
    # The rule is spelled ONCE: the sweep's PREVIEW reports the same verdict as
    # the press (no WRITE on the preview; the one locations read is answered).
    quiet = _Spy(_responses())
    _live(monkeypatch, quiet)
    plan = _run(shopify_push.sync_stock_levels(db, dry_run=True))
    assert plan.ok is False and plan.code == shopify_push.STOCK_TARGET_DUPLICATE
    assert quiet.writes() == []
    # ...and it never outranks a LIVE oversell report: an unmapped holder wins.
    db2 = _listed(_db(a=3, b=0, c=0, d=2, sku="PARENT-1"), sku="PARENT-1")
    db2.seed("catalog_variants", [
        {"sku": "AAA-SIZE-L", "parent_product_id": "cat-1", "shopify_inventory_item_id": INV_GID}
    ])
    db2.seed("products", [{"product_id": "spine-x", "sku": "AAA-SIZE-L"}])
    spy2 = _Spy(_responses())
    _live(monkeypatch, spy2)
    out2 = _run(shopify_push.push_skus_stock(db2, ["AAA-SIZE-L", "PARENT-1"], source="test"))
    assert out2["code"] == shopify_push.STORE_UNMAPPED, (
        "a permanent data defect must never hide a shop whose stock is invisible online"
    )


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
    quiet = _Spy(_responses())
    _live(monkeypatch, quiet)
    res = _run(shopify_push.sync_stock_levels(db, dry_run=True))
    assert res.mode == "SIMULATED" and res.ok is False, res
    assert quiet.writes() == [], "a preview never writes"
    assert res.code == shopify_push.STOCK_ONHAND_UNKNOWN and "HIRAPUR-DHN" in (res.error or "")
    assert res.payload["unknown_stores"] == ["BV-B"]
    assert res.payload["plan"][0]["quantities"] == {"SP-1": {"BV-A": 2, "BV-C": 0}}
    assert _baseline(db) is None, "a preview writes no baseline"
    # ...and the POS door's summary names the code the same way -- and writes
    # the shops it COULD read, omitting the unknown one so the next pass re-sends it.
    s = _run(wb.writeback_skus(db, ["SP-1"], "BV-A"))
    assert s["unknown_stores"] == ["BV-B"] and "HIRAPUR-DHN" in (s.get("error") or "")
    assert _baseline(db)["quantities"] == {"SP-1": {"BV-A": 2, "BV-C": 0}}


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
    _spine_off(db, "SP-1-L")  # the retire hook flipped the spine first
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
    _spine_off(bare, "SP-1-L")
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
    quiet = _Spy(_responses())
    _live(monkeypatch, quiet)  # a preview makes ZERO writes
    prev = _run(shopify_push.sync_stock_levels(db, dry_run=True))
    assert prev.mode == "SIMULATED" and prev.ok is False
    assert quiet.writes() == []
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
    plan = _run(inv.plan_product_stock(db, db.get_collection("catalog_products").find_one({"id": "cat-1"}), []))
    assert plan["ok"] is False and plan["code"] == shopify_push.STOCK_ONHAND_UNKNOWN
    assert "store read failed" in plan["error"]
    assert plan["stores_total"] is None, "unknown is never 0"
    # ...and the plain "no shop mapped at all" case is coded too.
    monkeypatch.setattr(inv, "_stores", lambda _d: [])
    plan2 = _run(inv.plan_product_stock(db, db.get_collection("catalog_products").find_one({"id": "cat-1"}), []))
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


def test_R5_a_mapped_location_that_cannot_sell_online_is_never_a_green_run(monkeypatch):
    """ROUND-5 P1 (HIGH, one rule / one read). The locations read answered four
    facts and exactly ONE was ever asked: "fulfils online orders AND maps to no
    shop". The MIRROR -- a shop IS mapped, but its location does not fulfil
    online orders, or is deactivated, or is not in Shopify's list at all -- was
    asked NOWHERE in the backend: no code existed, no rung, no term in _all_ok.

    That is the state the design's own runbook creates (section 6 step 4: tick
    "fulfil online orders" for Gangadham Pune ONLY) and prod's three mapped
    shops ARE the Jharkhand ones. Shopify counts online availability only at
    ticked locations, so IMS wrote 2/1/0 at three locations the storefront does
    not sell from and reported ok=True, code=None, no task -- over a
    bettervision.in reading SOLD OUT on all 121 products.

    Delete the `dead_locations` rung in _verdict_for or its term in _all_ok ->
    ok True, code None -> this fails."""
    db = _listed(_db(a=2, b=1, c=0))
    unticked = _Spy(_responses(**{
        "imsLocationList": _locations(
            _loc(LOC_A, "Bokaro", fulfils=False),
            _loc(LOC_B, "Dhanbad", fulfils=False),
            _loc(LOC_C, "Sector 4", fulfils=False),
        )
    }))
    _live(monkeypatch, unticked)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.ok is False and res.code == shopify_push.SHOPIFY_LOCATION_NOT_SELLING
    assert "Bokaro" in res.error and "fulfil online orders" in res.error
    assert [d["store_id"] for d in res.payload["dead_locations"]] == ["BV-A", "BV-B", "BV-C"]
    # ...the real numbers still went out (never withhold a true number)...
    assert unticked.rows() == {(INV_GID, LOC_A, 2), (INV_GID, LOC_B, 1), (INV_GID, LOC_C, 0)}
    # A DEACTIVATED location reads the same way.
    db2 = _listed(_db(a=2, b=1, c=0))
    _live(monkeypatch, _Spy(_responses(**{
        "imsLocationList": _locations(
            _loc(LOC_A, "Bokaro", active=False), _loc(LOC_B, "Dhanbad"), _loc(LOC_C, "Sector 4"),
        )
    })))
    res2 = _run(shopify_push.sync_stock_levels(db2))
    assert res2.code == shopify_push.SHOPIFY_LOCATION_NOT_SELLING
    assert res2.payload["dead_locations"][0]["reason"] == "deactivated in Shopify"
    # ...and a gid Shopify does not list at all is the sharpest case: the PRESS
    # would be refused, so the PREVIEW must not read green either (this module
    # promises twice that a green preview means a green press).
    db3 = _listed(_db(a=2, b=1, c=0))
    gone = {"imsLocationList": _locations(_loc(LOC_B, "Dhanbad"), _loc(LOC_C, "Sector 4"))}
    _live(monkeypatch, _Spy(_responses(**gone)))
    preview = _run(shopify_push.sync_stock_levels(db3, dry_run=True))
    assert preview.ok is False and preview.code == shopify_push.SHOPIFY_LOCATION_NOT_SELLING
    assert preview.payload["dead_locations"] == [
        {"store_id": "BV-A", "location_id": LOC_A, "name": None,
         "reason": "Shopify does not list this location any more"}
    ]


def test_R5_an_empty_locations_read_is_unknown_not_every_shop_dead(monkeypatch):
    """The guard on the guard. A shop ALWAYS has at least one Shopify location,
    so an empty list means the read told us nothing -- concluding "Shopify
    lists none of your mapped locations" from it would flag every shop on every
    pass. `read` is False for a dark, failed or empty answer. Drop the
    `or not rows` term -> every mapped shop is reported dead -> this fails.

    ...and UNKNOWN is not GREEN (recheck round 1): this pin used to assert
    ok=True / code=None on the empty read, i.e. that "we could not read the
    list" may score as "no stray, no dead". It may not. The rows still go
    out; the run says the location question went unanswered, in its own code.
    Drop the `locations_unread` rung -> ok True -> this fails."""
    db = _listed(_db(a=2, b=1, c=0))
    spy = _Spy(_responses(**{"imsLocationList": _locations()}))
    _live(monkeypatch, spy)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.payload["dead_locations"] == [] and res.payload["unmapped_locations"] == []
    assert res.ok is False and res.code == shopify_push.SHOPIFY_UNREACHABLE, res.error
    assert res.payload["locations_read"] is False and "could not be read" in (res.error or "")
    assert spy.rows() == {(INV_GID, LOC_A, 2), (INV_GID, LOC_B, 1), (INV_GID, LOC_C, 0)}, "still written"


def test_R5_the_product_press_carries_invariant_2(monkeypatch):
    """FIRST-PUSH P1 (HIGH). `push_skus_stock` -- the door all 121 first
    publishes go through (sync_product_stock) and every POS sale goes through
    (writeback_skus) -- never called the locations read at all, and `_rows_ok`
    had no stray term. So with prod's exact shape (three shops mapped,
    Gangadham Pune mapped to nobody and ticked to fulfil online orders) a
    publish press returned ok=True / code=None / ZERO locations reads, and the
    listing went live showing Pune's stale number while IMS wrote three
    locations Shopify does not sell from.

    Round 4 fixed this for the POS door by CARRYING the sweep's recorded
    verdict; the product door was left out. Delete the `locations` argument of
    either _rows_ok call, or the stray/dead terms in _rows_ok -> ok True ->
    this fails."""
    db = _listed(_db(a=2, b=1, c=0))
    pune = _loc("gid://shopify/Location/76684427513", "Gangadham Pune")
    all_locs = {"imsLocationList": _locations(
        _loc(LOC_A, "Bokaro"), _loc(LOC_B, "Dhanbad"), _loc(LOC_C, "Sector 4"), pune,
    )}
    # 1. A sweep has recorded the verdict: the press carries it, zero reads.
    _live(monkeypatch, _Spy(_responses(**all_locs)))
    _run(shopify_push.sync_stock_levels(db))
    spy = _Spy(_responses())  # no locations answer -- and none is needed
    _live(monkeypatch, spy)
    out = _run(shopify_push.push_skus_stock(db, ["SP-1"], source="product_push"))
    assert spy.calls_for("imsLocationList") == [], "the recorded verdict is carried, not re-read"
    assert out["ok"] is False and out["code"] == shopify_push.SHOPIFY_LOCATION_UNMAPPED
    assert out["unmapped_locations"] == [{"id": pune["id"], "name": "Gangadham Pune"}]
    assert out["set"] == 3, "the mapped shops' numbers still went out"
    # 2. DAY ONE: nothing has ever been recorded, so the press makes the ONE
    #    read itself -- otherwise the owner's very first publish is green over
    #    a location Shopify is already selling from.
    fresh = _listed(_db(a=2, b=1, c=0))
    spy2 = _Spy(_responses(**all_locs))
    _live(monkeypatch, spy2)
    res = _run(shopify_push.push_product(
        fresh, fresh.get_collection("catalog_products").find_one({"id": "cat-1"}), []
    ))
    assert len(spy2.calls_for("imsLocationList")) == 1
    assert res.stock["ok"] is False
    assert res.stock["code"] == shopify_push.SHOPIFY_LOCATION_UNMAPPED
    assert shopify_push.last_stray_locations(fresh) == [
        {"id": pune["id"], "name": "Gangadham Pune"}
    ], "the read it had to make is recorded, so the next press carries it"


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
        ob.blocked_skus = lambda _db, skus, **kw: list(skus)  # the SUPERADMIN block
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
    # Both location statements are true here -- these two Shopify locations map
    # to no shop, and the three shops' own locations are absent from this list
    # -- so the round-7 verdict names both on one rung, storefront-wide first.
    assert res.code == shopify_push.SHOPIFY_LOCATION_NOT_SELLING
    assert [l["name"] for l in res.payload["unmapped_locations"]] == ["Sector 4", "Pune"]
    assert "Pune" in (res.error or ""), "the verdict still says it"
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


# ---------------------------------------------------------------------------
# 11. Adversarial panel round 6 (2026-09-12): the recorded verdict is ROWS, not
#     a scored snapshot; the sweep scores a product on what it WROTE; the
#     preview names a listing it could not read; a delist is not a false
#     STORE_UNMAPPED; an unreadable SUPERADMIN block is UNKNOWN.
# ---------------------------------------------------------------------------


def _sweep_recorded(monkeypatch, db, *locations):
    """Run ONE live sweep so `online_sync_state/shopify_stray_locations` holds
    Shopify's location rows, and return a fresh spy that answers NO locations
    query -- so any later read is visible as a missing answer, not a silent one."""
    _live(monkeypatch, _Spy(_responses(imsLocationList=_locations(*locations))))
    _run(shopify_push.sync_stock_levels(db))
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    return spy


def test_R6_P2_a_shop_mapped_after_the_sweep_is_not_reported_stray(monkeypatch):
    """ROUND-6 P2. `writer_location_verdict` took `mapped` and never used it:
    it replayed the SCORED snapshot the last LIVE sweep recorded. So the moment
    the owner fixes a mapping -- step 3 of the first-push runbook -- every
    press until the next 01:00 tick reported the location he JUST mapped as
    stray: ok=False, SHOPIFY_LOCATION_UNMAPPED and a deduped P1 SYSTEM task per
    press, over 121 publishes.

    A verdict is Shopify's rows PLUS the IMS mapping. The rows are cached; the
    mapping is read fresh and re-scored (`score_locations`). Make
    `last_location_verdict` replay a stored stray list instead of re-scoring
    -> the press names Sector 4 -> this fails."""
    db = _listed(_db(a=2, b=1, c=0, c_mapped=False))
    spy = _sweep_recorded(
        monkeypatch, db,
        _loc(LOC_A, "Bokaro"), _loc(LOC_B, "Dhanbad"), _loc(LOC_C, "Sector 4"),
    )
    # the sweep DID see it as stray -- BV-C had no location then
    assert shopify_push.last_stray_locations(db) == [{"id": LOC_C, "name": "Sector 4"}]
    # the owner maps it on the Organization page; no sweep has run since
    db.get_collection("stores").update_one(
        {"store_id": "BV-C"}, {"$set": {"shopify_location_id": LOC_C}}
    )
    out = _run(shopify_push.push_skus_stock(db, ["SP-1"], source="product_push"))
    assert spy.calls_for("imsLocationList") == [], "still zero network -- the ROWS are cached"
    assert out["unmapped_locations"] == [], "the shop IS mapped now"
    assert out["ok"] is True and out["code"] is None
    assert shopify_push.last_stray_locations(db) == []


def test_R6_P1_a_location_that_went_dead_after_the_sweep_is_never_a_green_press(monkeypatch):
    """ROUND-6 P1 (HIGH, SILENT -- the direction that sells air). Same cache,
    the other way round: design section 6 step 4 tells the owner to tick
    "Fulfill online orders" for Gangadham Pune ONLY and untick the Jharkhand
    ones. Map a shop to a location that is NOT ticked, AFTER a verdict was
    recorded, and the stored dead list -- computed over the OLD mapped set --
    was empty, so the press came back ok=True / code=None / dead_locations=[]
    and wrote rows at a location the storefront reads SOLD OUT from.

    Re-scoring the recorded ROWS against the caller's own `mapped` is the fix
    (`dead_mapped_reason` is a pure function of the row). Replay a stored dead
    list instead -> ok True -> this fails."""
    db = _listed(_db(a=2, b=1, c=0, c_mapped=False))
    spy = _sweep_recorded(
        monkeypatch, db,
        _loc(LOC_A, "Bokaro"), _loc(LOC_B, "Dhanbad"), _loc(LOC_C, "Sector 4", fulfils=False),
    )
    # unticked AND unmapped is neither stray nor dead -- the recorded verdict is clean
    assert shopify_push.last_stray_locations(db) == []
    db.get_collection("stores").update_one(
        {"store_id": "BV-C"}, {"$set": {"shopify_location_id": LOC_C}}
    )
    out = _run(shopify_push.push_skus_stock(db, ["SP-1"], source="product_push"))
    assert spy.calls_for("imsLocationList") == []
    assert out["ok"] is False
    assert out["code"] == shopify_push.SHOPIFY_LOCATION_NOT_SELLING
    assert [d["store_id"] for d in out["dead_locations"]] == ["BV-C"]
    assert "sold out" in (out["error"] or "")


def test_R6_P4_preview_first_records_the_verdict_it_measured(monkeypatch):
    """ROUND-6 P4. "Preview first" is the mandatory step 4 of the first-push
    runbook. It ran the FRESH `location_verdict` and threw it away:
    `record_location_verdict` sat AFTER the `if not live or dry_run: return`,
    so the one button the runbook tells the owner to press before the press
    could not fix the cache it was about to be read from. Move the record back
    below the dry-run return -> nothing is stored -> this fails."""
    db = _listed(_db(a=2, b=1, c=0, c_mapped=False))
    _live(monkeypatch, _Spy(_responses(imsLocationList=_locations(
        _loc(LOC_A, "Bokaro"), _loc(LOC_B, "Dhanbad"), _loc(LOC_C, "Gangadham Pune"),
    ))))
    res = _run(shopify_push.sync_stock_levels(db, dry_run=True))
    assert res.code == shopify_push.SHOPIFY_LOCATION_UNMAPPED
    assert shopify_push.last_stray_locations(db) == [{"id": LOC_C, "name": "Gangadham Pune"}]


def test_R6_P3_the_product_preview_asks_the_same_source_as_the_press(monkeypatch):
    """ROUND-6 P3 (preview/press divergence -- the class this module claims
    twice to have closed). `plan_product_stock` read the recorded verdict with
    NO day-1 fallback while the press read Shopify itself when nothing had ever
    been recorded. On the rebuilt catalogue -- no sweep ever run, Gangadham
    Pune fulfilling online, BV-C unmapped -- the preview was ok=True/code=None
    and the press on the SAME db came back SHOPIFY_LOCATION_UNMAPPED.

    Revert `plan_product_stock` to the recorded verdict with no day-1 read
    -> the plan reads green -> this fails."""
    db = _listed(_db(a=2, b=1, c=0, c_mapped=False))
    spy = _Spy(_responses(imsLocationList=_locations(
        _loc(LOC_A, "Bokaro"), _loc(LOC_B, "Dhanbad"), _loc(LOC_C, "Gangadham Pune"),
    )))
    _live(monkeypatch, spy)
    product = db.get_collection("catalog_products").find_one({"id": "cat-1"})
    plan = _run(shopify_push.plan_product_stock(db, product, []))
    assert plan["ok"] is False
    assert plan["code"] == shopify_push.SHOPIFY_LOCATION_UNMAPPED
    assert len(spy.calls_for("imsLocationList")) == 1, "the ONE read the press would make"
    # ...and the press agrees, carrying what the preview recorded.
    out = _run(shopify_push.push_skus_stock(db, ["SP-1"], source="product_push"))
    assert out["code"] == plan["code"]
    assert len(spy.calls_for("imsLocationList")) == 1, "recorded once, carried after"


def _one_listing_with_no_spine_row(db):
    """cat-1/SP-1 on the spine, cat-2/SP-2 NOT -- the documented catalogue-stray
    class, and the shape a transient `_sku_to_pid` failure wears. Both are
    single-SKU listings, exactly the 121-product catalogue's shape."""
    db.seed("products", [{"product_id": "spine-1", "sku": "SP-1"}])
    db.seed(
        "catalog_products",
        [
            _catalog_row("cat-1", "SP-1", gid=True),
            _catalog_row("cat-2", "SP-2", gid=True, shopify_inventory_item_id=INV_2),
        ],
    )
    return db


def test_R6_a_listing_whose_own_stock_call_aborted_is_never_scored_synced(monkeypatch):
    """ROUND-6 first-push P2 (HIGH, "written" meaning nothing was written). The
    sweep re-derived each product's success from res["errors"] and
    res["stores_mapped"] instead of what was WRITTEN. The whole-batch abort
    inside `push_skus_stock` sets only `code` + `error` and returns with
    errors == [] and `stores_mapped` already stamped -- so a single-SKU
    listing whose SKU has no spine row scored as `synced`, the page rendered a
    grey "2 of 2 listings changed, 2 written, 0 failed" with no error line, and
    the audit row said ok. A mixed-SKU product hits the per-SKU
    `errors.append` and WAS caught; a single-SKU one -- all 121 of them -- was
    not.

    Drop `or not res.get("set")` from the scoring branch -> synced 2, failed 0,
    ok True -> this fails."""
    db = _one_listing_with_no_spine_row(_db(a=2, b=1, c=0))
    spy = _Spy(_responses(imsLocationList=_locations(
        _loc(LOC_A, "A"), _loc(LOC_B, "B"), _loc(LOC_C, "C"),
    )))
    _live(monkeypatch, spy)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.payload["changed"] == 2
    assert res.payload["synced"] == 1 and res.payload["failed"] == 1
    assert res.ok is False
    assert any("cat-2" in e for e in res.payload["errors"]), res.payload["errors"]
    assert res.code == shopify_push.STOCK_ONHAND_UNKNOWN
    # and the transcript proves it: only SP-1's three rows ever went out
    assert spy.rows() == {(INV_GID, LOC_A, 2), (INV_GID, LOC_B, 1), (INV_GID, LOC_C, 0)}


def test_R6_the_preview_names_the_listing_it_could_not_read(monkeypatch):
    """ROUND-6 first-push P3. `unknown_stores` answers "which SHOP could not be
    read" and `target_missing` answers "which SKU has no Shopify item"; NOTHING
    answered "which SKU could not be read AT ALL", so `_all_ok()` had no term
    for it and Preview first read fully green (ok=True, code=None, error=None)
    over the exact state the LIVE press codes STOCK_ONHAND_UNKNOWN. The tell
    was already in the payload and unacted on: a `plan` row with empty
    quantities.

    Drop `or unknown_skus` from `_all_ok` -> ok True -> this fails."""
    db = _one_listing_with_no_spine_row(_db(a=2, b=1, c=0))
    _live(monkeypatch, _Spy(_responses(imsLocationList=_locations(
        _loc(LOC_A, "A"), _loc(LOC_B, "B"), _loc(LOC_C, "C"),
    ))))
    res = _run(shopify_push.sync_stock_levels(db, dry_run=True))
    assert res.payload["unknown_skus"] == ["SP-2"]
    assert res.payload["unknown_stores"] == [] and res.payload["target_missing"] == []
    assert res.ok is False and res.code == shopify_push.STOCK_ONHAND_UNKNOWN
    assert "SP-2" in (res.error or "")


def test_R6_P6_a_delist_is_not_a_false_unmapped_report(monkeypatch):
    """ROUND-6 P6. The delist door forces 0 at EVERY physical shop, mapped or
    not -- taking the size off the website everywhere IS the intent. But
    `push_skus_stock` computed its holders from its OWN buffer-0 re-read of the
    rule, not from the caller's forced quantities, so a fully successful delist
    of a size Gangadham Pune still holds came back ok=False + STORE_UNMAPPED +
    a deduped P1 SYSTEM task. On prod today that is every delist of any size
    held at the unmapped shop.

    Recheck round 2: the door no longer forces a 0 (and needs no `delisting`
    exemption) -- the RULE writes, and an inactive spine is 0 at every shop,
    the buffer-0 holders re-read included, so BV-D holds nothing the website
    is asked to sell. Stop zeroing an inactive spine in `_sku_to_pid` -> BV-D
    holds 2 -> ok False with STORE_UNMAPPED -> this fails."""
    child = {
        "id": "cat-1-L", "sku": "SP-1-L", "name": "Frame L",
        "ecom": {"status": "DRAFT", "variant_of": {"product_id": "spine-1", "twin_id": "cat-1", "sku": "SP-1"}},
    }
    db = _db(a=1, b=0, c=0, d=2, sku="SP-1-L")  # BV-D holds 2 and has NO location
    _spine_off(db, "SP-1-L")
    db.seed("catalog_products", [_catalog_row("cat-1", "SP-1", gid=True), child])
    db.seed(
        "catalog_variants",
        [{"sku": "SP-1-L", "parent_product_id": "cat-1", "shopify_variant_id": "gid://shopify/ProductVariant/52",
          "shopify_inventory_item_id": INV_ROW}],
    )
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push._delist_variant_row(db, child))
    assert res.ok is True and res.code is None, res.error
    assert res.payload["rows"] == {"SP-1-L": {"BV-A": 0, "BV-B": 0, "BV-C": 0}}
    assert db.get_collection("tasks").count_documents({}) == 0, "no P1 for a delist that worked"


def test_R6_an_unreadable_online_block_is_unknown_not_unblocked():
    """ROUND-6 oversell P4. `_blocked_online` returned an empty set on ANY
    error -- the OPPOSITE polarity to every other read in the rule, where
    unknown is never written as a number. The block is IN the rule now, so it
    is the only thing holding a SUPERADMIN-blocked collection at 0 online: one
    Mongo blip and the pass published the SHELF count as an absolute quantity,
    recorded it as the baseline, and the diff then read the product as
    UNCHANGED -- back on sale until a human noticed.

    Return an empty set from `_blocked_online` (or drop `strict=True`) -> the
    shelf count comes back -> this fails."""
    db = _listed(_db(a=2, b=1, c=0))

    class _Boom(StrictCollection):
        def find(self, *a, **k):
            raise RuntimeError("ecom_collections read died")

    db._collections["ecom_collections"] = _Boom("ecom_collections", [])
    assert wb.online_quantities_for_skus(db, ["SP-1"]) == {}, "UNKNOWN, never 'not blocked'"


# ---------------------------------------------------------------------------
# Panel round 7 (2026-09-16)
# ---------------------------------------------------------------------------


def test_R7_a_single_sku_door_refuses_an_item_a_SECOND_sku_claims(monkeypatch):
    """ROUND-7 P1 (OVERSELL, the lead finding). The duplicate-item guard asked a
    BATCH-LOCAL question -- "do the SKUs in THIS call collide?" -- while the
    invariant it protects is database-GLOBAL: Shopify holds one quantity per
    (item, location) whoever writes it. Every single-SKU door (POS sale, ingest
    claim, return restock, transfer ship, quarantine, write-off) wrote straight
    through it: one SKU in, no collision visible, a fully green write of the
    SIZE's per-shop numbers onto the PARENT's inventory item.

    Its own mirror on the other axis, `_location_conflicts`, has always been
    asked of the WHOLE shop list. One axis global, one axis batch-local: the
    same database that answers STOCK_TARGET_DUPLICATE to the sweep answered
    "fine, written" to the sale.

    Revert `duplicate_inventory_items` to the batch-only claim (drop the
    `skus_claiming_inventory_items` read) -> a row is written, code is None ->
    this fails."""
    db = _listed(_db(a=2, b=0, c=0))  # cat-1 / SP-1, its OWN item, 2 at BV-A
    # The mis-stamp: a size row on the SAME Shopify inventory item, 1 at BV-B.
    db.seed("products", [{"product_id": "spine-L", "sku": "SP-1-L"}])
    db.seed("stock_units", [
        {"stock_id": "L1", "product_id": "spine-L", "store_id": "BV-B", "status": "AVAILABLE"}
    ])
    db.seed("catalog_variants", [
        {"sku": "SP-1-L", "parent_product_id": "cat-1", "shopify_inventory_item_id": INV_GID}
    ])
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    out = _run(shopify_push.push_skus_stock(db, ["SP-1-L"], source="sale"))
    assert spy.rows() == set(), "the size's shelf must never be written onto the parent's item"
    assert out["ok"] is False and out["code"] == shopify_push.STOCK_TARGET_DUPLICATE
    assert "SP-1" in out["error"] and "SP-1-L" in out["error"]
    assert _baseline(db) is None, "nothing written, so nothing enters the baseline"
    # ONE rule: the sweep over the SAME database says exactly the same thing.
    spy2 = _Spy(_responses())
    _live(monkeypatch, spy2)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.code == shopify_push.STOCK_TARGET_DUPLICATE and spy2.rows() == set()
    # ...and the finding's own transcript, through the finding's own door: the
    # POS sale of the size at BV-B. It used to come back pushed=1 failed=0 with
    # rows (INV/9, LOC_A, 0), (INV/9, LOC_B, 1), (INV/9, LOC_C, 0) -- the
    # SIZE's shelf written onto the PARENT's item, fully green.
    spy3 = _Spy(_responses())
    _live(monkeypatch, spy3)
    sale = _run(wb.writeback_skus(db, ["SP-1-L"], "BV-B", source="sale"))
    assert spy3.rows() == set(), "the sale door is the same writer, so the same refusal"
    assert sale["pushed"] == 0 and sale["failed"] == 1
    assert _baseline(db) is None


def test_R7_two_LISTINGS_on_one_inventory_item_write_NEITHER(monkeypatch):
    """ROUND-7 P1, the cross-listing half. The sweep resolved duplicates PER
    LISTING, so two different listings stamped with one inventory item passed
    both checks: ok=True, synced=2, failed=0, the calls writing (INV, LOC_A, 0)
    then (INV, LOC_A, 2) in order, so the shared item ended at 2 while cat-2's
    baseline recorded 0 -- a lie about what the site now shows, cemented by the
    next pass reading noop.

    Scope the claim read to one listing's own SKUs -> both are written -> this
    fails."""
    db = _db(a=2, b=0, c=0)
    db.seed("catalog_products", [_catalog_row("cat-1", "SP-1"), _catalog_row("cat-2", "SP-2")])
    db.seed("products", [{"product_id": "spine-2", "sku": "SP-2"}])  # holds nothing
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push.sync_stock_levels(db))
    assert spy.rows() == set(), "one quantity per (item, location): neither listing is guessed at"
    assert res.ok is False and res.code == shopify_push.STOCK_TARGET_DUPLICATE
    assert "SP-1, SP-2" in res.error
    assert res.payload["synced"] == 0 and res.payload["failed"] == 2


def test_R7_a_listed_sku_that_cannot_be_read_is_named_on_EVERY_pass(monkeypatch):
    """ROUND-7 one-rule P1 (silent fallback, write path). `unknown_skus` was
    computed over the CHANGED products only, and `stock_changed` restricts both
    sides of the diff to the product's current SKUs -- so a listed SKU whose
    on-hand cannot be read AT ALL (no `products` spine row: the catalogue-stray
    class) was named LOUDLY on the first pass and went SILENT for ever after,
    while the press had already set tracked=true + DENY on its variant and
    nobody was writing its number.

    Invariant 5/6 is one rule: unknown is named on every pass, mapped or not.
    Scope `unknown_skus` back to `changed_skus` -> pass 2 is a green noop ->
    this fails."""
    db = _listed(_db(a=1, b=0, c=0))
    db.seed("catalog_variants", [
        # a live Shopify item, but NO products spine row -> unreadable on-hand
        {"sku": "SP-1-L", "parent_product_id": "cat-1", "shopify_inventory_item_id": INV_ROW}
    ])
    _live(monkeypatch, _Spy(_responses()))
    first = _run(shopify_push.sync_stock_levels(db))
    assert first.ok is False and first.code == shopify_push.STOCK_ONHAND_UNKNOWN
    assert first.payload["unknown_skus"] == ["SP-1-L"]
    _live(monkeypatch, _Spy(_responses()))
    second = _run(shopify_push.sync_stock_levels(db))
    assert second.action == "noop", "the diff still noops -- this is about the REPORT"
    assert second.payload["unknown_skus"] == ["SP-1-L"], "silent from pass 2 on"
    assert second.ok is False and second.code == shopify_push.STOCK_ONHAND_UNKNOWN
    assert "SP-1-L" in (second.error or "")


def test_R7_a_retired_size_shopify_still_shows_a_number_for_is_named(monkeypatch):
    """ROUND-7 P2 (phantom stock). A SKU that leaves the rule's reach is dropped
    from BOTH sides of the diff, so its live Shopify number freezes under a
    permanently GREEN noop -- and once that unit sells at the counter the
    website is selling stock IMS no longer holds anywhere.

    Reachable through a runbook already on main: scripts/delete_catalog_products
    drops `catalog_variants` rows that carry live Shopify gids. The number can
    no longer be zeroed by IMS (the gid went with the row), so the only honest
    answer left is to NAME it on every pass instead of noop'ing green over it.

    A row the delist door zeroed properly is NOT named (its baseline is all
    zeros -- nothing is being advertised). Drop the `stray_skus` rung or its
    term in `_all_ok` -> a green noop -> this fails."""
    db = _listed(_db(a=1, b=0, c=0))
    db.seed("products", [{"product_id": "spine-L", "sku": "SP-1-L"}])
    db.seed("stock_units", [
        {"stock_id": "L1", "product_id": "spine-L", "store_id": "BV-A", "status": "AVAILABLE"}
    ])
    db.seed("catalog_variants", [
        {"sku": "SP-1-L", "parent_product_id": "cat-1", "shopify_inventory_item_id": INV_ROW}
    ])
    _live(monkeypatch, _Spy(_responses()))
    assert _run(shopify_push.sync_stock_levels(db)).ok is True
    assert _baseline(db)["quantities"]["SP-1-L"] == {"BV-A": 1, "BV-B": 0, "BV-C": 0}
    # The hard-delete runbook takes the row away; Shopify keeps showing 1.
    db.get_collection("catalog_variants").delete_one({"sku": "SP-1-L"})
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.action == "noop" and spy.rows() == set(), "nothing to write -- the gid is gone"
    assert res.ok is False and res.code == shopify_push.STOCK_BASELINE_STRAY
    assert res.payload["stray_skus"] == ["SP-1-L"]
    assert "SP-1-L" in (res.error or "")
    # A SKU the delist door zeroed everywhere is advertising nothing: silent.
    db2 = _listed(
        _db(a=1, b=0, c=0),
        online_stock={"tracked": True, "quantities": {
            "SP-1": {"BV-A": 1, "BV-B": 0, "BV-C": 0},
            "RETIRED": {"BV-A": 0, "BV-B": 0, "BV-C": 0},
        }},
    )
    _live(monkeypatch, _Spy(_responses()))
    res2 = _run(shopify_push.sync_stock_levels(db2))
    assert res2.ok is True and res2.payload["stray_skus"] == []


def test_R7_a_stale_recorded_location_verdict_is_re_read_by_the_next_press(monkeypatch):
    """ROUND-7 first-push. `record_location_verdict` stamps `at` and NOTHING
    ever read it, so the SHOPIFY half of the verdict was frozen until the next
    sweep -- up to 12 hours. MEASURED: tick the locations, run a sweep, untick
    "Fulfill online orders" in Shopify admin, press Send to website -> ZERO
    locations reads and a fully green press over a shop the storefront can no
    longer sell from. That is the exact silent direction
    SHOPIFY_LOCATION_NOT_SELLING was added to close, reopened by the cache that
    keeps the per-product press network-free. The mirror is a false red: fix a
    tick and every press still codes NOT_SELLING until a sweep runs.

    Drop the TTL in `last_location_verdict` -> the stale verdict is replayed,
    zero reads, ok True -> this fails.

    ONE MINUTE, not ten (recheck round 1): at ten, a press five minutes after
    the untick was still green and still wrote at the dead location. The
    record below is aged NINETY SECONDS -- put the TTL back to 600 and the
    stale verdict is replayed with zero reads -> this fails."""
    from datetime import datetime, timedelta, timezone

    db = _listed(_db(a=2, b=1, c=0))
    ticked = {"imsLocationList": _locations(
        _loc(LOC_A, "Bokaro"), _loc(LOC_B, "Dhanbad"), _loc(LOC_C, "Sector 4"),
    )}
    _live(monkeypatch, _Spy(_responses(**ticked)))
    _run(shopify_push.sync_stock_levels(db))  # records what Shopify said
    unticked = {"imsLocationList": _locations(
        _loc(LOC_A, "Bokaro"), _loc(LOC_B, "Dhanbad"), _loc(LOC_C, "Sector 4", fulfils=False),
    )}
    # Within the TTL the press still carries the cache: zero network, as designed.
    fresh = _Spy(_responses(**unticked))
    _live(monkeypatch, fresh)
    out = _run(shopify_push.push_skus_stock(db, ["SP-1"], source="product_push"))
    assert fresh.calls_for("imsLocationList") == [] and out["dead_locations"] == []
    # Ninety seconds later the same press re-reads -- and refuses to call it green.
    db.get_collection("online_sync_state").update_one(
        {"_id": "shopify_stray_locations"},
        {"$set": {"at": datetime.now(timezone.utc) - timedelta(seconds=90)}},
    )
    stale = _Spy(_responses(**unticked))
    _live(monkeypatch, stale)
    out2 = _run(shopify_push.push_skus_stock(db, ["SP-1"], source="product_push"))
    assert len(stale.calls_for("imsLocationList")) == 1, "a stale verdict is re-read, once"
    assert out2["ok"] is False and out2["code"] == shopify_push.SHOPIFY_LOCATION_NOT_SELLING
    assert [d["store_id"] for d in out2["dead_locations"]] == ["BV-C"]


def test_R7_a_dead_mapped_location_is_named_even_beside_a_stray_one(monkeypatch):
    """ROUND-7 first-push (the ladder). The state the design's own runbook
    creates on day 1 -- Gangadham Pune ticked and deliberately mapped to no
    shop, the three mapped Jharkhand locations not ticked -- put the STRAY rung
    above the DEAD one, so the single code+error line a press surfaces sent the
    owner to fix the location that holds nothing and NEVER told him why all 121
    listings read SOLD OUT.

    Both are true at once, so both are said, and the storefront-wide one leads.
    Revert the ladder to "stray wins, dead silent" -> the dead shops vanish from
    the line -> this fails."""
    db = _listed(_db(a=2, b=1, c=0))
    pune = _loc("gid://shopify/Location/76684427513", "Gangadham Pune")
    spy = _Spy(_responses(**{"imsLocationList": _locations(
        _loc(LOC_A, "Bokaro", fulfils=False),
        _loc(LOC_B, "Dhanbad", fulfils=False),
        _loc(LOC_C, "Sector 4", fulfils=False),
        pune,
    )}))
    _live(monkeypatch, spy)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.ok is False and res.code == shopify_push.SHOPIFY_LOCATION_NOT_SELLING
    assert "SOLD OUT" in res.error and "Bokaro" in res.error, "why every listing is sold out"
    assert "Gangadham Pune" in res.error, "...and the stray location is still named"
    assert [d["store_id"] for d in res.payload["dead_locations"]] == ["BV-A", "BV-B", "BV-C"]
    assert res.payload["unmapped_locations"] == [{"id": pune["id"], "name": "Gangadham Pune"}]


def test_R7_an_unreadable_claim_check_is_UNKNOWN_on_the_sweep_as_on_the_press(monkeypatch):
    """The claim read is STRICT on the press (round-7 P1): {} from a swallowed
    exception is "nobody else claims this item", the one answer that lets an
    absolute writer overwrite another SKU's shelf. The sweep caught the same
    raise in the try that also resolves the TARGETS and reset `have` to {}, so
    every changed SKU came out STOCK_TARGET_MISSING -- "no Shopify inventory
    item mapped" -- a false statement about the data, and a second answer to
    the failure the press codes STOCK_ONHAND_UNKNOWN. One rule: an unreadable
    guard is UNKNOWN, nothing written, the same words on both doors.

    Fold the claim read back into the target try -> `target_missing` fills and
    the code is STOCK_TARGET_MISSING -> this fails."""
    from api.services import online_catalog

    def _boom(db, gids):  # noqa: ARG001
        raise RuntimeError("catalog_variants read failed")

    monkeypatch.setattr(online_catalog, "skus_claiming_inventory_items", _boom)
    db = _listed(_db(a=2, b=1, c=0))
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push.sync_stock_levels(db))
    assert spy.rows() == set(), "an unreadable guard writes nothing"
    assert res.ok is False and res.code == shopify_push.STOCK_ONHAND_UNKNOWN
    assert res.payload["target_missing"] == [], "the item IS mapped; the CLAIM check is what failed"
    assert "claim check" in (res.error or "")
    # The preview says it too (zero writes), and the press says it in the same words.
    quiet = _Spy(_responses())
    _live(monkeypatch, quiet)
    plan = _run(shopify_push.sync_stock_levels(db, dry_run=True))
    assert quiet.writes() == []
    assert plan.ok is False and plan.code == shopify_push.STOCK_ONHAND_UNKNOWN
    out = _run(shopify_push.push_skus_stock(db, ["SP-1"], source="product_push", dry_run=True))
    assert out["code"] == shopify_push.STOCK_ONHAND_UNKNOWN and out["error"] == plan.error


# ---------------------------------------------------------------------------
# 12. Recheck round 1 (2026-09-17): the per-product doors, the unread list,
#     the spelling axis, the soft-deleted claimant, the holders+dead line
# ---------------------------------------------------------------------------


def _same_listing_dup(other_spelling=INV_GID):
    """cat-1 / SP-1 owns INV_GID (2 at BV-A); a size row SP-1-L (1 at BV-B) is
    stamped on the SAME item, spelled ``other_spelling``."""
    db = _listed(_db(a=2, b=0, c=0))
    db.seed("products", [{"product_id": "spine-L", "sku": "SP-1-L"}])
    db.seed("stock_units", [
        {"stock_id": "L1", "product_id": "spine-L", "store_id": "BV-B", "status": "AVAILABLE"}
    ])
    db.seed("catalog_variants", [
        {"sku": "SP-1-L", "parent_product_id": "cat-1", "shopify_inventory_item_id": other_spelling}
    ])
    return db


def test_R8_a_bare_id_claimant_beside_a_full_gid_row_is_still_a_duplicate(monkeypatch):
    """OVERSELL (recheck round 1). The claim guard added both spellings of THIS
    batch's stored value, while the reverse read matches the STORED string of
    the OTHER row: with this row the full gid and the other row the bare id,
    the parent's own POS sale wrote its shelf onto the item the size shares,
    fully green -- while the size's door and the sweep refused the same
    database. Drop the ``rsplit`` token from ``spellings`` -> rows written,
    code None -> this fails."""
    db = _same_listing_dup(other_spelling="9")  # the OTHER row is the BARE id
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    out = _run(shopify_push.push_skus_stock(db, ["SP-1"], source="sale"))
    assert spy.rows() == set(), ("the parent's sale wrote through a bare-id claimant", spy.rows())
    assert out["ok"] is False and out["code"] == shopify_push.STOCK_TARGET_DUPLICATE
    assert "SP-1, SP-1-L" in out["error"]
    # ...and the mirror spelling (this row bare, the other the full gid) too.
    db2 = _db(a=2, b=0, c=0)
    db2.seed("catalog_products", [_catalog_row("cat-1", "SP-1", shopify_inventory_item_id="9")])
    db2.seed("products", [{"product_id": "spine-L", "sku": "SP-1-L"}])
    db2.seed("stock_units", [{"stock_id": "L1", "product_id": "spine-L", "store_id": "BV-B", "status": "AVAILABLE"}])
    db2.seed("catalog_variants", [{"sku": "SP-1-L", "parent_product_id": "cat-1", "shopify_inventory_item_id": INV_GID}])
    spy2 = _Spy(_responses())
    _live(monkeypatch, spy2)
    out2 = _run(shopify_push.push_skus_stock(db2, ["SP-1"], source="sale"))
    assert spy2.rows() == set() and out2["code"] == shopify_push.STOCK_TARGET_DUPLICATE


def _parent_whose_size_row_was_deleted(monkeypatch):
    db = _listed(_db(a=1, b=0, c=0))
    db.seed("products", [{"product_id": "spine-L", "sku": "SP-1-L"}])
    db.seed("stock_units", [{"stock_id": "L1", "product_id": "spine-L", "store_id": "BV-A", "status": "AVAILABLE"}])
    db.seed("catalog_variants", [{"sku": "SP-1-L", "parent_product_id": "cat-1", "shopify_inventory_item_id": INV_TWO}])
    _live(monkeypatch, _Spy(_responses()))
    assert _run(shopify_push.sync_stock_levels(db)).ok is True
    assert _baseline(db)["quantities"]["SP-1-L"] == {"BV-A": 1, "BV-B": 0, "BV-C": 0}
    db.get_collection("catalog_variants").delete_one({"sku": "SP-1-L"})
    return db


def test_R8_the_press_the_preview_and_the_sale_row_name_a_stray_the_sweep_names(monkeypatch):
    """PHANTOM (recheck round 1). Round 7 closed the retired-size phantom on
    the SWEEP only: the Send-to-website press, the drawer preview and the POS
    sale's own run row -- the doors the owner actually uses -- stayed green
    while bettervision.in kept selling SP-1-L = 1 at BV-A. The stray question
    is asked per LISTING wherever the listing is written. Drop
    ``listing_strays`` from the press (or ``baseline_strays`` from the plan)
    -> green -> this fails. The rows STILL go out: a stray is a report, and
    withholding the sibling's true number would be a second oversell."""
    db = _parent_whose_size_row_was_deleted(monkeypatch)
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    press = _run(shopify_push.push_skus_stock(db, ["SP-1"], source="product_push", product_id="cat-1"))
    assert press["ok"] is False and press["code"] == shopify_push.STOCK_BASELINE_STRAY
    assert press["stray_skus"] == ["SP-1-L"] and "SP-1-L" in press["error"]
    assert spy.rows() == {(INV_GID, LOC_A, 1), (INV_GID, LOC_B, 0), (INV_GID, LOC_C, 0)}, "still written"
    # The drawer preview (DARK, zero network) says the same.
    _dark(monkeypatch)
    product = db.get_collection("catalog_products").find_one({"id": "cat-1"})
    plan = _run(shopify_push.plan_product_stock(db, product, []))
    assert plan["ok"] is False and plan["code"] == shopify_push.STOCK_BASELINE_STRAY
    # ...and the POS sale's own run row (the door with only a SKU in hand).
    spy2 = _Spy(_responses())
    _live(monkeypatch, spy2)
    sale = _run(wb.writeback_skus(db, ["SP-1"], "BV-A", source="sale"))
    assert sale.get("code") == shopify_push.STOCK_BASELINE_STRAY and sale["pushed"] == 1
    run = list(db.get_collection("sync_runs").find({}))[-1]
    assert run["ok"] is False and "STOCK_BASELINE_STRAY" in run["error"]
    assert "SP-1-L" in _baseline(db)["quantities"], "the merge keeps the stray so it stays named"


def test_R8_the_per_product_preview_carries_the_data_defect_rungs(monkeypatch):
    """PHANTOM (recheck round 1). ``plan_product_stock`` passed only the four
    mapping inputs to the ladder: over the round-7 P1 database (a size row on
    the parent's item) it read ok=True and PRINTED both SKUs as rows that
    would be written while the press wrote neither; over a shop whose
    aggregate died it read green with that shop silently absent. Drop
    ``duplicate_targets`` / ``unknown_error`` from the plan's ladder call ->
    ok True -> this fails."""
    _dark(monkeypatch)
    db = _same_listing_dup()
    product = db.get_collection("catalog_products").find_one({"id": "cat-1"})
    variants = list(db.get_collection("catalog_variants").find({"parent_product_id": "cat-1"}))
    plan = _run(shopify_push.plan_product_stock(db, product, variants))
    assert plan["ok"] is False and plan["code"] == shopify_push.STOCK_TARGET_DUPLICATE
    assert plan["quantities"] == {}, "neither SKU of a duplicated item is a row the press would write"
    # A shop whose read died: the same code, the same words, as the press.
    db2 = _listed(_db(a=2, b=1, c=0))
    _break_shop(db2, "BV-B")
    product2 = db2.get_collection("catalog_products").find_one({"id": "cat-1"})
    plan2 = _run(shopify_push.plan_product_stock(db2, product2, []))
    assert plan2["ok"] is False and plan2["code"] == shopify_push.STOCK_ONHAND_UNKNOWN
    assert "BV-B" in plan2["error"] and plan2["quantities"] == {"SP-1": {"BV-A": 2, "BV-C": 0}}
    # ...and the whole-batch unknown (no spine at all) is the press's own line.
    db3 = StrictDB()
    db3.seed("stores", [_store("BV-A", LOC_A)])
    db3.seed("catalog_products", [_catalog_row("cat-1", "SP-1")])
    plan3 = _run(shopify_push.plan_product_stock(db3, db3.get_collection("catalog_products").find_one({"id": "cat-1"}), []))
    assert plan3["ok"] is False and plan3["code"] == shopify_push.STOCK_ONHAND_UNKNOWN
    assert "every listed SKU" in plan3["error"]


def test_R8_a_soft_deleted_twin_whose_take_down_never_reached_shopify_still_claims_its_item(monkeypatch):
    """OVERSELL (recheck round 1, low). The claim read dropped EVERY
    soft-deleted twin on the premise that it is off the site -- but the delete
    door's take-down is fail-soft (the delete stands if Shopify says no) and a
    DARK delist is a SIMULATED ok stamped DELISTED with zero network. Such a
    twin is still ACTIVE on Shopify with a live item, and a SKU mis-stamped on
    that item wrote its shelf onto it. Only a LIVE DELISTED stamp lets the twin
    go. Restore ``if deleted_at: continue`` -> the sale writes -> this fails."""
    def _world(**ecom_marks):
        db = _db(a=2, b=0, c=0)
        dead = _catalog_row("cat-1", "SP-1", **ecom_marks)
        dead["deleted_at"] = "2026-09-17T00:00:00"
        db.seed("catalog_products", [dead, _catalog_row("cat-2", "SP-2")])
        db.seed("products", [{"product_id": "spine-2", "sku": "SP-2"}])
        db.seed("stock_units", [{"stock_id": "s2", "product_id": "spine-2", "store_id": "BV-B", "status": "AVAILABLE"}])
        return db

    for marks in ({}, {"online_state": "DELIST_FAILED"}, {"online_state": "DELISTED", "delist_mode": "SIMULATED"}):
        db = _world(**marks)
        spy = _Spy(_responses())
        _live(monkeypatch, spy)
        out = _run(wb.writeback_skus(db, ["SP-2"], "BV-B", source="sale"))
        assert spy.rows() == set(), (marks, spy.rows())
        assert out.get("code") == shopify_push.STOCK_TARGET_DUPLICATE, marks
    # The one honest exception: a twin a LIVE take-down actually took down.
    db = _world(online_state="DELISTED", delist_mode="LIVE")
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    out = _run(wb.writeback_skus(db, ["SP-2"], "BV-B", source="sale"))
    assert out.get("code") is None and spy.rows() == {(INV_GID, LOC_A, 0), (INV_GID, LOC_B, 1), (INV_GID, LOC_C, 0)}


class _RaisingLocations(_Spy):
    """Every mutation answers; the READ-ONLY locations list raises (a 429
    after 121 product pushes, a token blip)."""

    async def __call__(self, db, query, variables):
        if "imsLocationList" in query:
            self.calls.append({"query": query, "variables": variables})
            raise RuntimeError("Throttled")
        return await super().__call__(db, query, variables)


def test_R8_an_unreadable_locations_list_is_UNKNOWN_on_the_preview_the_press_and_the_sweep(monkeypatch):
    """SILENT FALLBACK (recheck round 1, LIVE, day-1 reachable). A raised read,
    a Shopify error body or an empty node list all came back stray=[] dead=[]
    and nothing downstream looked at ``read``, so the mandatory Preview-first
    read GREEN over Pune stray and three unticked Jharkhand locations. Two
    answers to one question from one database. Now: its own code on every
    door, the rows still written, and NOT recorded (the next caller re-reads).
    Drop the ``locations_unread`` rung -> ok True -> this fails."""
    db = _listed(_db(a=2, b=1, c=0))
    spy = _RaisingLocations(_responses())
    _live(monkeypatch, spy)
    plan = _run(shopify_push.sync_stock_levels(db, dry_run=True))
    assert plan.ok is False and plan.code == shopify_push.SHOPIFY_UNREACHABLE
    assert plan.payload["locations_read"] is False and spy.writes() == []
    # The press over the same failure: UNKNOWN, rows still written.
    out = _run(shopify_push.push_skus_stock(db, ["SP-1"], source="product_push"))
    assert out["ok"] is False and out["code"] == shopify_push.SHOPIFY_UNREACHABLE
    assert out["locations_unread"] is True and out["set"] == 3
    assert out["dead_locations"] == [] and out["unmapped_locations"] == []
    # The LIVE sweep too.
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.ok is False and res.code == shopify_push.SHOPIFY_UNREACHABLE and res.error == out["error"]
    assert (INV_GID, LOC_A, 2) in spy.rows()
    # Nothing was recorded, so the next press with Shopify back does the one read.
    good = _Spy(_responses())
    _live(monkeypatch, good)
    again = _run(shopify_push.push_skus_stock(db, ["SP-1"], source="product_push"))
    assert len(good.calls_for("imsLocationList")) == 1 and again["ok"] is True and again["code"] is None
    # The drawer preview (a per-product plan) says it in the same words.
    _live(monkeypatch, _RaisingLocations(_responses()))
    db.get_collection("online_sync_state").delete_one({"_id": "shopify_stray_locations"})
    product = db.get_collection("catalog_products").find_one({"id": "cat-1"})
    plan2 = _run(shopify_push.plan_product_stock(db, product, []))
    assert plan2["ok"] is False and plan2["code"] == shopify_push.SHOPIFY_UNREACHABLE and plan2["error"] == out["error"]


def test_R8_an_unmapped_holder_never_hides_the_dead_mapped_shops(monkeypatch):
    """LADDER (recheck round 1, the round-7 shape one rung up). Prod day 1: the
    one stock unit sits at Pune, Pune is unmapped, the runbook unticks the
    three Jharkhand locations -> the press said "map BV-D" and NOTHING about
    every listing reading SOLD OUT until the owner had mapped Pune and pressed
    again. Both are said; the holders line leads. Drop the concat under the
    holders rung -> SOLD OUT vanishes from the line -> this fails."""
    db = _listed(_db(a=0, b=0, c=0, d=1, with_d=True))
    unticked = {"imsLocationList": _locations(
        _loc(LOC_A, "Bokaro", fulfils=False), _loc(LOC_B, "Dhanbad", fulfils=False), _loc(LOC_C, "Sector 4", fulfils=False),
    )}
    _live(monkeypatch, _Spy(_responses(**unticked)))
    out = _run(shopify_push.push_skus_stock(db, ["SP-1"], source="product_push"))
    assert out["ok"] is False and out["code"] == shopify_push.STORE_UNMAPPED
    assert "BV-D" in out["error"] and "SOLD OUT" in out["error"] and "Bokaro" in out["error"]
    assert [d["store_id"] for d in out["dead_locations"]] == ["BV-A", "BV-B", "BV-C"]
    # The sweep's line is the same line.
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.code == shopify_push.STORE_UNMAPPED and "SOLD OUT" in res.error and "BV-D" in res.error


def test_R8_an_unreadable_target_read_is_UNKNOWN_on_the_sweep_not_TARGET_MISSING(monkeypatch):
    """SILENT FALLBACK (recheck round 1). ``inventory_items_for_skus`` was
    fail-soft to {}, which the sweep read as "no Shopify inventory item mapped"
    for every changed SKU (a false statement about the data) and the sale
    door read as "not online" (a vanished write-back). STRICT now: one
    answer, UNKNOWN, on every door. Make the reader swallow again -> the
    sweep codes STOCK_TARGET_MISSING -> this fails."""
    from api.services import online_catalog

    def _boom(db, skus):  # noqa: ARG001
        raise RuntimeError("catalog_variants read failed")

    monkeypatch.setattr(online_catalog, "inventory_items_for_skus", _boom)
    db = _listed(_db(a=2, b=1, c=0))
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push.sync_stock_levels(db))
    assert spy.rows() == set() and res.ok is False
    assert res.code == shopify_push.STOCK_ONHAND_UNKNOWN and res.payload["target_missing"] == []
    assert "mapping could not be read" in res.error
    out = _run(shopify_push.push_skus_stock(db, ["SP-1"], source="product_push"))
    assert out["code"] == shopify_push.STOCK_ONHAND_UNKNOWN and out["error"] == res.error
    # ...and the reader itself raises rather than answering {} on a dead collection.
    monkeypatch.undo()
    db2 = _listed(_db())
    orig = db2.get_collection

    class _Dead:
        def find(self, *a, **k):
            raise RuntimeError("catalog_variants unreadable")

    monkeypatch.setattr(db2, "get_collection", lambda name: _Dead() if name == "catalog_variants" else orig(name))
    with pytest.raises(RuntimeError):
        online_catalog.inventory_items_for_skus(db2, ["SP-1"])


# ---------------------------------------------------------------------------
# Recheck round 2 (2026-09-17)
# ---------------------------------------------------------------------------


class _ThrottledTracking(_Spy):
    """Shopify answers every other call as usual; the ONE productVariantsBulkUpdate
    carrying inventoryPolicy (tracking + DENY) comes back with a userError --
    a throttle after N of the 121 first presses."""

    async def __call__(self, db, query, variables):  # noqa: ARG002
        if "productVariantsBulkUpdate" in query and any(
            "inventoryPolicy" in r for r in (variables.get("variants") or [])
        ):
            self.calls.append({"query": query, "variables": variables})
            return {"data": {"productVariantsBulkUpdate": {"productVariants": [], "userErrors": [
                {"field": ["variants"], "message": "Throttled", "code": "THROTTLED"}
            ]}}}
        return await super().__call__(db, query, variables)


def test_R8_a_first_publish_whose_tracking_call_fails_is_withheld_and_coded(monkeypatch):
    """RECHECK ROUND 2 (first-push, OVERSELL direction). productCreate ok,
    seeding ok, the productVariantsBulkUpdate carrying inventoryPolicy answers
    userErrors [THROTTLED] -> the quantities were written, publishablePublish
    was SENT, and the listing was LIVE and UNTRACKED: Shopify sells it without
    limit. `sync_product_stock` flipped ok=False with NO code, so the press
    promoted nothing, the drawer toast, the sweep toast and the row tick were
    all GREEN, and `_tally` filed it under `pushed`. The module's own comment
    calls an untracked item "the worse failure"; it was the one failure with
    no name -- and a name alone still left it live.

    Now it is the design's "tracked + DENY before publish": the press
    WITHHOLDS the publish. The transcript below has no publishablePublish, the
    result is ok=False / publish_withheld / STOCK_TRACKING_FAILED, the twin
    stays DRAFT and queued, and the sweep says the same.

    Drop `tracking_ok` from the publish precondition in push_product -> the
    publish is sent over an untracked variant -> this fails. Drop the
    STOCK_TRACKING_FAILED branch in sync_product_stock -> no code -> the
    precondition cannot see it -> this fails."""
    db = _db(a=2, b=1, c=0)
    db.seed("catalog_products", [_catalog_row("cat-1", "SP-1", gid=False)])
    spy = _ThrottledTracking(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, db.get_collection("catalog_products").find_one({"id": "cat-1"}), []))
    # THE TRANSCRIPT: create -> tracking (refused) -> quantities -> NO publish.
    assert spy.order("productCreate(", "productVariantsBulkUpdate", "inventorySetQuantities", "publishablePublish") == [
        "productCreate(", "productVariantsBulkUpdate", "productVariantsBulkUpdate", "inventorySetQuantities",
    ], [c["query"][:40] for c in spy.calls]
    assert spy.calls_for("publishablePublish") == [], "an untracked variant is never published"
    assert spy.rows() == {(INV_GID, LOC_A, 2), (INV_GID, LOC_B, 1), (INV_GID, LOC_C, 0)}, "the true numbers still go out"
    assert res.mode == "LIVE" and res.action == "create"
    assert res.ok is False and res.reason == "publish_withheld", res
    assert res.code == shopify_push.STOCK_TRACKING_FAILED, "the stock pass's own code, on the press"
    assert res.error.startswith("publish withheld") and "WITHOUT LIMIT" in res.error and "Throttled" in res.error
    assert res.publication == {"published": False, "code": shopify_push.STOCK_TRACKING_FAILED, "error": res.error}
    assert res.stock["ok"] is False and res.stock["tracked"] == 0 and res.stock["code"] == shopify_push.STOCK_TRACKING_FAILED
    twin = db.get_collection("catalog_products").find_one({"id": "cat-1"})["ecom"]
    assert twin["status"] == "DRAFT" and twin["shopify_product_id"] == PRODUCT_GID, "on Shopify, not on the storefront"
    assert twin["locally_modified"] is True, "queued, so the next press retries tracking + publish"
    assert _baseline(db)["tracked"] is False, "so the next pass re-sends tracking"
    # The sweep under the same throttle is not green either, and says why.
    spy2 = _ThrottledTracking(_responses())
    _live(monkeypatch, spy2)
    sw = _run(shopify_push.sync_stock_levels(db))
    assert sw.ok is False and sw.code == shopify_push.STOCK_TRACKING_FAILED, sw
    assert "WITHOUT LIMIT" in sw.error
    # CONTROL: tracking accepted -> the same press publishes and is green.
    ok_spy = _Spy(_responses())
    _live(monkeypatch, ok_spy)
    ok = _run(shopify_push.push_product(db, db.get_collection("catalog_products").find_one({"id": "cat-1"}), []))
    assert ok.ok is True and ok.code is None and len(ok_spy.calls_for("publishablePublish")) == 1, ok
    assert db.get_collection("catalog_products").find_one({"id": "cat-1"})["ecom"]["status"] == "PUBLISHED"


def test_R8_the_location_rung_never_hides_a_stray_sku(monkeypatch):
    """RECHECK ROUND 2 (first-push, phantom hidden). In the configuration the
    runbook itself tells the owner to run on day 1 (Pune ticked only, the
    three mapped Jharkhand locations unticked) SHOPIFY_LOCATION_NOT_SELLING is
    the PERMANENT code of every press and every sweep -- and the ladder
    returned exactly one line, so every rung below it was invisible: SP-1-L
    written 1 at BV-A, its row deleted (the round-7 phantom), the locations
    unticked -> code NOT_SELLING, an error that never said 'SP-1-L', and the
    only place the name appeared was the audit payload JSON. bettervision.in
    kept selling SP-1-L for as long as the locations stayed unticked, which
    the runbook told him to keep them.

    Every true rung is named, top first. Return only the top rung's line ->
    'SP-1-L' leaves the error -> this fails."""
    db = _parent_whose_size_row_was_deleted(monkeypatch)
    unticked = {"imsLocationList": _locations(
        _loc(LOC_A, "Bokaro", fulfils=False),
        _loc(LOC_B, "Dhanbad", fulfils=False),
        _loc(LOC_C, "Sector 4", fulfils=False),
    )}
    _live(monkeypatch, _Spy(_responses(**unticked)))
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.ok is False and res.code == shopify_push.SHOPIFY_LOCATION_NOT_SELLING
    assert res.payload["stray_skus"] == ["SP-1-L"]
    assert "SOLD OUT" in res.error, "the storefront-wide rung leads"
    assert "SP-1-L" in res.error, "...and the stray the site keeps selling rides under it"
    # The press on the parent -- the one line the owner reads -- says the same.
    _live(monkeypatch, _Spy(_responses(**unticked)))
    press = _run(shopify_push.push_skus_stock(db, ["SP-1"], source="product_push", product_id="cat-1"))
    assert press["code"] == shopify_push.SHOPIFY_LOCATION_NOT_SELLING
    assert "SP-1-L" in press["error"] and "SOLD OUT" in press["error"]


def test_R8_the_location_rung_never_hides_a_listings_own_failure(monkeypatch):
    """The other half of the same masking (recheck round 2, first-push): the
    sweep joined its per-listing failures into `error` ONLY when the ladder
    had set none (`if errors and not error`). Under the day-1 configuration
    the ladder always sets SHOPIFY_LOCATION_NOT_SELLING, so a listing whose
    tracking call was THROTTLED (live and untracked) failed the run with a
    line that never said which listing or why.

    Every true rung is said: the listing's own line rides under the ladder's.
    Put `and not error` back -> 'WITHOUT LIMIT' leaves the error -> this
    fails."""
    db = _db(a=2, b=1, c=0)
    _listed(db, online_stock={"quantities": {}, "tracked": False, "policy": "DENY"})
    unticked = {"imsLocationList": _locations(
        _loc(LOC_A, "Bokaro", fulfils=False),
        _loc(LOC_B, "Dhanbad", fulfils=False),
        _loc(LOC_C, "Sector 4", fulfils=False),
    )}
    spy = _ThrottledTracking(_responses(**unticked))
    _live(monkeypatch, spy)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.ok is False and res.code == shopify_push.SHOPIFY_LOCATION_NOT_SELLING, res
    assert res.payload["failed"] == 1 and "cat-1" in res.payload["errors"][0]
    assert "SOLD OUT" in res.error, "the storefront-wide rung leads"
    assert "cat-1" in res.error and "WITHOUT LIMIT" in res.error and "Throttled" in res.error, res.error


def test_R8_a_delist_writes_the_rules_number_and_says_when_the_size_is_still_on_sale(monkeypatch):
    """RECHECK ROUND 2 (one rule: two implementations of 'this size is off
    sale'). `_delist_variant_row` handed the writer a forced 0 (plus a
    `delisting` exemption from the holders question) while the RULE's only
    off-sale marker is the spine's is_active. The DELETE door deactivates the
    spine fail-soft with the repository's answer unchecked, so a swallowed
    Mongo error there left the spine ACTIVE: the row wrote 0 green, and the
    next sync_stock_levels diffed shelf 1 vs sent 0 and put the deleted size
    straight back on sale, ok=True, code None. Two answers to one SKU.

    ONE now -- the second spelling is deleted, the door writes the RULE's
    number. Spine still active -> the shelf goes out (the true number, the
    one the next pass writes too) and the door is NOT green: it names the
    shop and the count and says the spine is still active. Spine off -> 0
    everywhere, green. Either way the next pass AGREES with the door: a
    zero-write noop. Spine unreadable -> UNKNOWN, nothing written.

    Hand the writer a forced 0 from the door again -> the active-spine delist
    writes 0 and goes green, and the sweep writes 1 back -> this fails. Drop
    the still-on-sale check -> ok True over a shelf count -> this fails."""
    child = {
        "id": "cat-1-L", "sku": "SP-1-L", "name": "Frame L",
        "ecom": {"status": "DRAFT", "variant_of": {"product_id": "spine-1", "twin_id": "cat-1", "sku": "SP-1"}},
    }
    sent = {"quantities": {"SP-1": {"BV-A": 0, "BV-B": 0, "BV-C": 0}}, "tracked": True, "policy": "DENY"}
    row = {"sku": "SP-1-L", "parent_product_id": "cat-1",
           "shopify_variant_id": "gid://shopify/ProductVariant/52", "shopify_inventory_item_id": INV_ROW}

    def _world():
        w = _db(a=1, b=0, c=0, sku="SP-1-L")  # spine-1 / SP-1-L carries NO is_active -> ACTIVE; 1 unit at A
        w.seed("products", [{"product_id": "spine-P", "sku": "SP-1"}])
        w.seed("catalog_products", [_catalog_row("cat-1", "SP-1", gid=True, online_stock=dict(sent)), child])
        w.seed("catalog_variants", [dict(row)])
        return w

    # 1. Spine ACTIVE (the swallowed deactivate): the rule's number, said red.
    db = _world()
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push._delist_variant_row(db, child))
    assert spy.rows() == {(INV_ROW, LOC_A, 1), (INV_ROW, LOC_B, 0), (INV_ROW, LOC_C, 0)}, "the rule's number, never a forced 0"
    assert res.ok is False and res.code is None, res
    assert "SP-1-L is still on sale (BV-A: 1)" in res.error and "ACTIVE" in res.error, res.error
    assert _baseline(db)["quantities"]["SP-1-L"] == {"BV-A": 1, "BV-B": 0, "BV-C": 0}
    spy2 = _Spy(_responses())
    _live(monkeypatch, spy2)
    sw = _run(shopify_push.sync_stock_levels(db))
    assert sw.action == "noop" and spy2.writes() == [], "the door and the sweep read ONE rule -- nothing to undo"
    # 2. Spine OFF (what the doors do first): 0 everywhere, green, and the
    #    next pass still agrees.
    _spine_off(db, "SP-1-L")
    spy3 = _Spy(_responses())
    _live(monkeypatch, spy3)
    ok = _run(shopify_push._delist_variant_row(db, child))
    assert ok.ok is True and ok.code is None, ok.error
    assert spy3.rows() == {(INV_ROW, LOC_A, 0), (INV_ROW, LOC_B, 0), (INV_ROW, LOC_C, 0)}
    assert _baseline(db)["quantities"]["SP-1-L"] == {"BV-A": 0, "BV-B": 0, "BV-C": 0}
    spy4 = _Spy(_responses())
    _live(monkeypatch, spy4)
    assert _run(shopify_push.sync_stock_levels(db)).action == "noop" and spy4.writes() == []
    # 3. Spine UNREADABLE: unknown, nothing written, never "off sale".
    db5 = _world()

    class _Dead(StrictCollection):
        def find(self, *a, **k):
            raise RuntimeError("products read died")

    db5._collections["products"] = _Dead("products", [])
    spy5 = _Spy(_responses())
    _live(monkeypatch, spy5)
    unk = _run(shopify_push._delist_variant_row(db5, child))
    assert unk.ok is False and unk.code == shopify_push.STOCK_ONHAND_UNKNOWN and spy5.rows() == set(), unk
