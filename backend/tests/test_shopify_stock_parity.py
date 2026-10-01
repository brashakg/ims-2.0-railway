"""
Nightly Shopify stock parity, PER LOCATION (multi-location PR 4).

Pins, each with its revert named in the test:
  * the PURE comparator: drift only past tolerance; an unknown on EITHER side
    is never drift; worst first.
  * the PURE row builder compares per (SKU, mapped shop) against THAT shop's
    location -- a pooled compare would call a swapped pair of shelves clean.
  * an item Shopify returned but has not stocked at a mapped location reads 0
    there; an item Shopify did not return is unknown.
  * unmapped holders (Pune) and unclaimed Shopify locations are REPORTED,
    never drift, never a drift task.
  * the IMS side is the writer's own call: a non-zero safety buffer is not
    drift, and neither is the SUPERADMIN online block.
  * a location two shops claim is UNCLAIMED (the writer's definition).
  * ONE drift task PER SHOP: filed, refreshed while drift persists (never a
    second one), closed only when every SKU it names compares clean, filed
    AGAIN when the drift returns; the old pooled ref is never filed; a failed
    or partial Shopify read, a skipped drifted SKU or an unread shop closes
    nothing; a shop that leaves the mapped set has its task closed; an open
    task is found past 100 closed ones.
  * fail-soft: no creds, a raising shop list -> a reason, never a raise.
  * the Stock Tally page and the catalog reconciliation screen read the SAME
    per-location pairs (unbacked_units): unmapped Pune never backs another
    shop's listing, a swapped pair is a risk, one shop's filter reads its own
    location only; OVERSELL is past the shelf on both, 'recommended' /
    'sellable' / OVER_ALLOCATED are the writer's number (buffer, block).
  * the sample goes through the writer's item resolver (the ecom fallback);
    the drift task keeps every SKU still owed across a refresh; a tasks
    read failure files no second task.
  * round 4: a task whose SKUs left the catalogue (or an empty catalogue)
    closes; a night that compared nothing still retires a shop that left
    the map; only a task write that succeeded is reported; both screens
    order and report delta per location; an empty store_id is all stores.
  * round 5: the OVERSELL line is the physical shelf (a SUPERADMIN-blocked
    SKU listed within its shelf is OVER_ALLOCATED); both screens' second
    (units no shelf backs) and third (units past the writer, per location)
    sort keys are pinned.
  * round 7: every Shopify query stays under the 1,000-point cap (the fake
    prices each query as Shopify does and refuses one over it) and shrinks
    from the cost Shopify quotes; a failed batch leaves only its own SKUs
    unknown; an item Shopify answers null has left the catalogue; the task
    text names every SKU that keeps it open and never calls a SKU compared
    tonight 'not compared'.
  * round 8: a node that is not the item asked for is unknown; a store_id
    stored with a space still gets its task; an owed SKU keeps the numbers
    it last drifted with and the step that clears it.
  * round 16 (decided 2026-10-01): parity compares ONLY SKUs whose listing
    (a size's parent's, the writer's listings_for_skus) is live -- the
    writer's listing_visible, not retired in IMS; a LIVE take-down by any
    door closes the task; every drift gets ONE safe instruction (the
    Shopify admin quantity, never a press that changes a listing's
    status); the worst delta is over drifted rows only.
  * round 17: a retired SKU (a size too) is never compared, so every SKU a
    task names is a row in its shop's view; live is ONE reader
    (inventory.skus_on_live_listings) for parity, the Stock Tally and the
    reconciliation screen -- no second "taken down" computation, and the
    screen tests read it unpatched; live is
    listing_visible (a staged-PUBLISHED draft and a gid-less PUBLISHED twin
    are not live, an untracked live listing is); a dead status read
    touches no task; a deactivated location sells nothing; the task names
    EVERY SKU with both numbers and sends the admin to the shop's
    Recommended (IMS's number now), never to a number it carries.

StrictDB + injected Shopify boundary -- no network, no production.
"""

import asyncio
import os
import re
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from strict_fakes import StrictDB  # noqa: E402
from api.services import shopify_stock_parity as sp  # noqa: E402

LOC_A = "gid://shopify/Location/1001"
LOC_B = "gid://shopify/Location/1002"
LOC_STRAY = "gid://shopify/Location/7777"
INV_1 = "gid://shopify/InventoryItem/91"
INV_2 = "gid://shopify/InventoryItem/92"


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("ONLINE_STOCK_SAFETY_BUFFER", "SHOPIFY_STOCK_PARITY_TOLERANCE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("api.services.shopify_push._has_shopify_creds", lambda db, *a, **k: True)


def _store(sid, loc=None, store_type="RETAIL"):
    row = {"store_id": sid, "store_code": sid, "store_name": "Shop " + sid, "store_type": store_type, "is_active": True}
    if loc:
        row["shopify_location_id"] = loc
    return row


def _listing(n, sku, **ecom):
    """Catalog twin c<n> for `sku`: a LIVE listing (a gid and PUBLISHED, the
    writer's inventory.listing_visible) unless `ecom` says otherwise."""
    return {"id": f"c{n}", "sku": sku,
            "ecom": {"shopify_product_id": f"gid://shopify/Product/{n}", "status": "PUBLISHED", **ecom}}


def _db(shelves, *, pune_units=0):
    """Two MAPPED shops (BV-A at LOC_A, BV-B at LOC_B), an UNMAPPED BV-PUN
    holding `pune_units` of SKU-1, and the ONLINE store. `shelves` is
    {sku: {store_id: available units}}; SKU-1 -> INV_1 on live listing c1,
    SKU-2 -> INV_2 on live listing c2."""
    db = StrictDB()
    db.seed(
        "stores",
        [_store("BV-A", LOC_A), _store("BV-B", LOC_B), _store("BV-PUN"), _store("BV-ONLINE-01", store_type="ONLINE")],
    )
    db.seed("products", [{"product_id": "p1", "sku": "SKU-1"}, {"product_id": "p2", "sku": "SKU-2"}])
    db.seed("catalog_products", [_listing(1, "SKU-1"), _listing(2, "SKU-2")])
    db.seed(
        "catalog_variants",
        [{"sku": "SKU-1", "parent_product_id": "c1", "shopify_inventory_item_id": INV_1},
         {"sku": "SKU-2", "parent_product_id": "c2", "shopify_inventory_item_id": INV_2}],
    )
    pid = {"SKU-1": "p1", "SKU-2": "p2"}
    units = []
    shelves = {k: dict(v) for k, v in shelves.items()}
    if pune_units:
        shelves.setdefault("SKU-1", {})["BV-PUN"] = pune_units
    for sku, per in shelves.items():
        for sid, n in per.items():
            units += [
                {"stock_id": f"{sku}-{sid}-{i}", "product_id": pid[sku], "store_id": sid, "status": "AVAILABLE"}
                for i in range(n)
            ]
    db.seed("stock_units", units)
    return db


_FIRST = re.compile(r"inventoryLevels\(first:\s*(\d+)\)")


def _shopify(levels, *, fail=False, id_cost=None):
    """A fake _graphql answering the levels query from {item_gid: {loc: qty}}
    the way Shopify does: nodes in the ORDER of the ids asked, null for an id
    it has no item for, at most `first:` level rows per item (pageInfo.
    hasNextPage past them). It PRICES the query first, as Shopify does -- per
    id the item (1) + the inventoryLevels connection (2) + pageInfo (1) +
    `first:` x (level, location, quantities) (3), or `id_cost` -- and above
    1,000 points answers MAX_COST_EXCEEDED with no data at all."""
    calls = []
    costs = []

    async def gql(db, query, variables):  # noqa: ARG001
        calls.append(variables)
        if fail:
            raise RuntimeError("throttled")
        first = int(_FIRST.search(query).group(1))
        ids = variables["ids"]
        cost = len(ids) * (id_cost or 4 + 3 * first)
        costs.append(cost)
        if cost > 1000:
            return {"errors": [{
                "message": f"Query cost is {cost}, which exceeds the single query max cost limit (1000).",
                "extensions": {"code": "MAX_COST_EXCEEDED", "cost": cost, "maxCost": 1000},
            }]}

        def node(gid):
            if gid not in levels:
                return None
            rows = list(levels[gid].items())
            return {"id": gid, "inventoryLevels": {
                "pageInfo": {"hasNextPage": len(rows) > first},
                "edges": [
                    {"node": {"location": {"id": loc}, "quantities": [{"name": "available", "quantity": q}]}}
                    for loc, q in rows[:first]
                ],
            }}

        return {"data": {"nodes": [node(g) for g in ids]},
                "extensions": {"cost": {"requestedQueryCost": cost}}}

    gql.calls = calls
    gql.costs = costs
    return gql


def _skipping(levels, skip=INV_2):
    """Shopify answers from `levels`, except that any batch asking for `skip`
    FAILS (a raise). With one id per batch (the `one_id_per_batch` fixture)
    that SKU alone is unread tonight: unknown, never 0, never deleted."""
    clean = _shopify(levels)

    async def gql(db_, query, variables):
        if skip in variables["ids"]:
            raise RuntimeError("throttled")
        return await clean(db_, query, variables)

    return gql


@pytest.fixture
def one_id_per_batch(monkeypatch):
    monkeypatch.setattr(sp, "_INV_BATCH", 1)


def _tasks(db):
    return db.get_collection("tasks").docs


# ---------------------------------------------------------------------------
# Pure comparator + row builder
# ---------------------------------------------------------------------------


def test_compare_respects_tolerance_and_unknown_on_either_side():
    rows = [
        {"sku": "A", "store_id": "S", "ims_available": 10, "shopify_available": 10},  # 0
        {"sku": "B", "store_id": "S", "ims_available": 10, "shopify_available": 8},  # 2 == tol
        {"sku": "C", "store_id": "S", "ims_available": 10, "shopify_available": 7},  # 3 > tol
        {"sku": "D", "store_id": "S", "ims_available": 20, "shopify_available": 5},  # 15 > tol
        {"sku": "E", "store_id": "S", "ims_available": 10, "shopify_available": None},  # Shopify unknown
        {"sku": "F", "store_id": "S", "ims_available": None, "shopify_available": 5},  # IMS unknown
    ]
    out = sp.compare_variant_parity(rows, tolerance=2)
    assert out["compared"] == 4 and out["unknown"] == 2
    assert out["drift_count"] == 2 and out["max_delta"] == 15
    assert [d["sku"] for d in out["drift"]] == ["D", "C"]
    assert out["drift"][0]["store_id"] == "S"


def test_ims_unknown_is_never_read_as_zero():
    """A shop whose on-hand read failed is absent from the rule's answer. Revert
    the comparator to `int(r.get("ims_available") or 0)` -> IMS 0 vs Shopify 5
    -> a false drift row and a task on a shop IMS simply could not read."""
    out = sp.compare_variant_parity(
        [{"sku": "F", "store_id": "S", "ims_available": None, "shopify_available": 5}], tolerance=2
    )
    assert out["drift_count"] == 0 and out["unknown"] == 1 and out["compared"] == 0


def test_rows_compare_each_shop_with_its_own_location_never_a_sum():
    """The PR 4 rule. Shelves A=3, B=0; Shopify holds them SWAPPED (A=0, B=3).
    Pooled that is 3 vs 3 -- clean -- while each location sells the other
    shop's number. Revert parity_rows to compare against the sum of the item's
    levels (or the rule's sum) -> no drift -> this fails."""
    rows = sp.parity_rows(
        [{"sku": "SKU-1", "inventory_item_id": INV_1}],
        {"SKU-1": {"BV-A": 3, "BV-B": 0}},
        {INV_1: {LOC_A: 0, LOC_B: 3}},
        {"BV-A": LOC_A, "BV-B": LOC_B},
    )
    out = sp.compare_location_parity(rows, tolerance=2)
    assert out["drift_count"] == 2
    assert {d["store_id"] for d in out["drift"]} == {"BV-A", "BV-B"}
    assert out["stores"]["BV-A"]["drift"][0]["ims"] == 3
    assert out["stores"]["BV-A"]["drift"][0]["shopify"] == 0


def test_rows_not_stocked_at_a_location_is_zero_but_a_missing_item_is_unknown():
    """Revert `int(item_levels.get(gid, 0))` to `.get(gid)` -> the not-stocked
    location reads unknown and its 5 unsellable units never drift."""
    rows = sp.parity_rows(
        [{"sku": "SKU-1", "inventory_item_id": INV_1}, {"sku": "SKU-2", "inventory_item_id": INV_2}],
        {"SKU-1": {"BV-A": 5, "BV-B": 1}, "SKU-2": {"BV-A": 4, "BV-B": 4}},
        {INV_1: {LOC_B: 1}},  # INV_1 not stocked at LOC_A; INV_2 not returned at all
        {"BV-A": LOC_A, "BV-B": LOC_B},
    )
    out = sp.compare_location_parity(rows, tolerance=2)
    assert out["drift_count"] == 1 and out["drift"][0]["store_id"] == "BV-A"
    assert out["unknown"] == 2  # both SKU-2 rows


def test_unclaimed_locations_are_reported_with_their_units():
    out = sp.unclaimed_locations(
        [{"sku": "SKU-1", "inventory_item_id": INV_1}],
        {INV_1: {LOC_A: 3, LOC_STRAY: 4, "gid://shopify/Location/8": 0}},
        {LOC_A, LOC_B},
    )
    assert out == [{"location_id": LOC_STRAY, "units": 4, "skus": ["SKU-1"]}]


def test_parity_tolerance_env_override(monkeypatch):
    monkeypatch.setenv("SHOPIFY_STOCK_PARITY_TOLERANCE", "5")
    assert sp.parity_tolerance() == 5
    monkeypatch.setenv("SHOPIFY_STOCK_PARITY_TOLERANCE", "junk")
    assert sp.parity_tolerance() == 2


# ---------------------------------------------------------------------------
# The Shopify reader
# ---------------------------------------------------------------------------


def test_levels_reader_keys_by_location_and_normalises_bare_ids():
    async def gql(db, query, variables):  # noqa: ARG001
        return {"data": {"nodes": [{"id": INV_1, "inventoryLevels": {"edges": [
            {"node": {"location": {"id": "1001"}, "quantities": [{"name": "available", "quantity": 2}]}},
            {"node": {"location": {"id": LOC_B}, "quantities": [{"name": "available", "quantity": 5}]}},
        ]}}]}}

    assert _run(sp.shopify_levels_by_item(None, [INV_1], graphql=gql)) == {INV_1: {LOC_A: 2, LOC_B: 5}}


def test_levels_reader_a_failed_batch_is_only_its_own_items_unknown(one_id_per_batch):
    """Round 7 P1: a failed batch fails only itself. INV_2's batch raises:
    INV_1 is read, INV_2 is ABSENT (unknown -- never a level, never None).
    Only a read with NO batch answered is None. Put back the whole-read
    `return None` on a failed batch -> None -> fails (and every SKU on the
    Stock Tally and the reconciliation screen goes listed-unknown)."""
    gql = _skipping({INV_1: {LOC_A: 2}, INV_2: {LOC_A: 3}})
    assert _run(sp.shopify_levels_by_item(None, [INV_1, INV_2], graphql=gql)) == {INV_1: {LOC_A: 2}}
    assert _run(sp.shopify_levels_by_item(None, [INV_1, INV_2], graphql=_shopify({}, fail=True))) is None


def test_levels_reader_stays_under_the_query_cost_cap():
    """Round 7 P1, the panel's arithmetic: 100 ids x inventoryLevels(first:
    50) is ~15,000 points; Shopify refuses anything over 1,000 with NO nodes,
    so every night read 'shopify inventory read failed'. 130 items (the
    rebuilt catalogue is 121 products) through the pricing fake: every item
    read, every query priced at or under 1,000. Put back `_INV_BATCH = 100`
    (the old size) -> the first query is priced over 1,000 -> fails; with
    the cost read also gone it is refused outright and nothing is read.
    (`first: 50` alone re-sizes the batch from the cost, as it should.)"""
    items = [f"gid://shopify/InventoryItem/{n}" for n in range(1000, 1130)]
    gql = _shopify({inv: {LOC_A: 1, LOC_B: 2} for inv in items})
    out = _run(sp.shopify_levels_by_item(None, items, graphql=gql))
    assert out is not None and len(out) == 130 and out[items[-1]] == {LOC_A: 1, LOC_B: 2}
    assert max(gql.costs) <= 1000 and len(gql.calls) == -(-130 // sp._INV_BATCH)


def test_levels_reader_shrinks_the_batch_from_the_cost_shopify_quotes():
    """Round 7 P1: Shopify prices these items at 100 points an id (more than
    the estimate). The first batch is refused with its cost; the reader asks
    for the same ids again in pieces that fit and keeps that size for the
    rest -- every item read. Stop reading the cost (`cost = None`) -> the
    refused batches' items are unknown -> fails."""
    items = [f"gid://shopify/InventoryItem/{n}" for n in range(2000, 2040)]
    gql = _shopify({inv: {LOC_A: 1} for inv in items}, id_cost=100)
    out = _run(sp.shopify_levels_by_item(None, items, graphql=gql))
    assert out is not None and len(out) == 40
    assert gql.costs[0] > 1000 and max(gql.costs[1:]) <= 1000


def test_levels_reader_never_keys_a_short_answer_by_position():
    """nodes(ids:) answers positionally. A list shorter than the ids asked
    cannot be keyed: the batch is unread, never INV_2's levels filed under
    INV_1. Drop the length check -> {INV_1: {LOC_A: 7}} -> fails."""

    async def gql(db, query, variables):  # noqa: ARG001
        return {"data": {"nodes": [{"id": INV_2, "inventoryLevels": {"edges": [
            {"node": {"location": {"id": LOC_A}, "quantities": [{"name": "available", "quantity": 7}]}}]}}]}}

    assert _run(sp.shopify_levels_by_item(None, [INV_1, INV_2], graphql=gql)) is None


def test_levels_reader_an_item_past_one_page_of_locations_is_unknown():
    """Round 7 P1: the level connection is small now (_LEVELS_FIRST). An item
    Shopify stocks at more locations than one page is UNKNOWN (absent), never
    read from its first page with a later mapped location as 0. Drop the
    hasNextPage check -> INV_2 comes back with 10 of its 11 -> fails."""
    many = {f"gid://shopify/Location/{n}": 1 for n in range(1, sp._LEVELS_FIRST + 2)}
    out = _run(sp.shopify_levels_by_item(None, [INV_1, INV_2], graphql=_shopify({INV_1: {LOC_A: 1}, INV_2: many})))
    assert out == {INV_1: {LOC_A: 1}}


def test_levels_reader_a_node_that_is_not_the_item_asked_for_is_unknown():
    """Round 8, the panel's probe: a stored shopify_inventory_item_id of
    another type makes the `... on InventoryItem` fragment match nothing, so
    Shopify answers `{}` in its place -- no id, no levels. That is not 'stocked
    nowhere' (0 at every mapped shop, a drift task and 0 on both screens):
    the item stays ABSENT (unknown), and so does a node carrying another id.
    Drop the node-id check -> {INV_1: {}, INV_2: {}} -> fails."""

    async def gql(db, query, variables):  # noqa: ARG001
        return {"data": {"nodes": [{}, {"id": INV_1, "inventoryLevels": {"edges": []}}]}}

    assert _run(sp.shopify_levels_by_item(None, [INV_1, INV_2], graphql=gql)) == {}


# ---------------------------------------------------------------------------
# The tick, end to end on the real rule (StrictDB)
# ---------------------------------------------------------------------------


def test_tick_clean_system_files_nothing():
    db = _db({"SKU-1": {"BV-A": 2, "BV-B": 1}, "SKU-2": {"BV-A": 0, "BV-B": 4}})
    out = _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 2, LOC_B: 1}, INV_2: {LOC_A: 0, LOC_B: 4}})))
    assert out["checked"] is True and out["reason"] is None
    assert out["compared"] == 4 and out["drift_count"] == 0
    assert [s["store_id"] for s in out["stores"]] == ["BV-A", "BV-B"]
    assert out["task_filed"] is False and _tasks(db) == []
    assert db.get_collection("shopify_stock_parity_snapshots").docs  # snapshot persisted


def test_tick_unmapped_holder_is_reported_never_drift():
    """Pune (no location) holds 3 units. It is reported as an unmapped holder
    and appears in NO drift row and NO task. Revert run_parity_tick to build
    rows over every physical shop (not `mapped`) -> a BV-PUN row with IMS 3 vs
    Shopify unknown/0 -> this fails."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}}, pune_units=3)
    out = _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 0, LOC_B: 0}})))
    assert out["drift_count"] == 0
    assert [h["store_id"] for h in out["unmapped_holders"]] == ["BV-PUN"]
    assert out["unmapped_holders"][0]["units"] == 3
    assert all(s["store_id"] != "BV-PUN" for s in out["stores"])
    assert _tasks(db) == []


def test_tick_unclaimed_location_is_reported_never_drift():
    """Shopify holds 6 units at a location no shop carries."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}})
    out = _run(sp.run_parity_tick(
        db, graphql=_shopify({INV_1: {LOC_A: 1, LOC_B: 1, LOC_STRAY: 6}, INV_2: {}})
    ))
    assert out["drift_count"] == 0
    assert out["unclaimed_locations"] == [{"location_id": LOC_STRAY, "units": 6, "skus": ["SKU-1"]}]
    assert _tasks(db) == []


def test_tick_reads_ims_with_the_writers_buffer(monkeypatch):
    """Safety buffer 3, shelf 5 at BV-A: the writer sends 2, Shopify holds 2 --
    a correct system. Revert the tick's call to
    `online_quantities_for_skus(db, skus, safety_buffer=0)` -> IMS 5 vs 2 ->
    delta 3 > tolerance 2 -> a false drift task -> this fails."""
    monkeypatch.setenv("ONLINE_STOCK_SAFETY_BUFFER", "3")
    db = _db({"SKU-1": {"BV-A": 5, "BV-B": 0}})
    out = _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 2, LOC_B: 0}, INV_2: {LOC_A: 0, LOC_B: 0}})))
    assert out["compared"] == 4
    assert out["drift_count"] == 0, out["drift"]


def test_tick_one_task_per_shop_filed_refreshed_then_closed():
    """BV-A drifts (IMS 5, Shopify 1); BV-B is clean.
      tick 1 -> ONE task, ref ...:BV-A, store_id BV-A; none for BV-B; never the
                bare pooled ref.
      tick 2 -> the drift persists with a new number -> NO second task, the
                open one's description + payload are refreshed.
      tick 3 -> Shopify matches -> the task is COMPLETED.
    Revert the refresh branch (fall through to create_system_task) -> tick 2
    leaves the stale description -> fails. Revert the close branch -> tick 3
    leaves it OPEN -> fails."""
    db = _db({"SKU-1": {"BV-A": 5, "BV-B": 1}})
    out = _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {}})))
    assert out["tasks"] == {"filed": ["BV-A"], "refreshed": [], "closed": []}
    assert out["task_filed"] is True
    (task,) = _tasks(db)
    assert task["source_ref"] == "shopify-stock-parity-drift:BV-A"
    assert task["source_ref"] != "shopify-stock-parity-drift"
    assert task["store_id"] == "BV-A" and task["status"] == "OPEN"
    assert "IMS 5 vs Shopify 1" in task["description"]

    out = _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 0, LOC_B: 1}, INV_2: {}})))
    assert out["tasks"] == {"filed": [], "refreshed": ["BV-A"], "closed": []}
    (task,) = _tasks(db)
    assert "IMS 5 vs Shopify 0" in task["description"]
    assert task["payload"]["max_delta"] == 5

    out = _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 5, LOC_B: 1}, INV_2: {}})))
    assert out["tasks"] == {"filed": [], "refreshed": [], "closed": ["BV-A"]}
    (task,) = _tasks(db)
    assert task["status"] == "COMPLETED"

    # tick 4 -> the drift RETURNS -> a NEW open task (the closed one is history,
    # never "refreshed" out of sight). Count COMPLETED as active -> tick 4
    # reports `refreshed` onto the closed task and nobody sees an open one.
    out = _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 0, LOC_B: 1}, INV_2: {}})))
    assert out["tasks"] == {"filed": ["BV-A"], "refreshed": [], "closed": []}
    assert sorted(t["status"] for t in _tasks(db)) == ["COMPLETED", "OPEN"]


def test_tick_an_escalated_task_is_refreshed_not_duplicated():
    db = _db({"SKU-1": {"BV-A": 5, "BV-B": 1}})
    db.seed("tasks", [{"task_id": "T-1", "source_ref": "shopify-stock-parity-drift:BV-A", "status": "ESCALATED",
                       "description": "old"}])
    out = _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {}})))
    assert out["tasks"]["refreshed"] == ["BV-A"] and len(_tasks(db)) == 1
    assert _tasks(db)[0]["status"] == "ESCALATED" and _tasks(db)[0]["description"] != "old"


def test_tick_a_failed_batch_leaves_only_its_skus_unknown(one_id_per_batch):
    """Round 7 P1. Night 1: SKU-2 drifts at BV-A, the task names it. Night 2:
    SKU-2's batch fails, SKU-1's reads. The night is CHECKED: SKU-1 is
    compared at both shops, SKU-2 is unknown and still owed, so BV-A's task
    stays OPEN and names it. Put back the whole-read failure -> checked False
    -> fails; drop `not owed` from the close -> closed on a read that
    skipped the drifted SKU -> fails."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 5, "BV-B": 0}})
    _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 0, LOC_B: 0}})))
    assert _tasks(db)[0]["payload"]["skus"] == ["SKU-2"]
    out = _run(sp.run_parity_tick(db, graphql=_skipping({INV_1: {LOC_A: 1, LOC_B: 1}})))
    assert out["checked"] is True and out["compared"] == 2 and out["unknown"] == 2
    assert out["tasks"]["closed"] == [] and _tasks(db)[0]["status"] == "OPEN"
    assert "not compared tonight" in _tasks(db)[0]["description"] and "SKU-2" in _tasks(db)[0]["description"]


def test_levels_reader_top_level_errors_beside_nodes_is_a_failed_read():
    """Shopify can answer a list of nodes WITH top-level `errors` (a node it
    failed to resolve comes back null). That batch is unread -- its null is
    never read as 'deleted in Shopify'. Drop the `or body.get("errors")` ->
    the reader returns INV_1 and a deleted INV_2 -> this fails."""

    async def gql(db, query, variables):  # noqa: ARG001
        return {
            "data": {"nodes": [{"id": INV_1, "inventoryLevels": {"edges": []}}, None]},
            "errors": [{"message": "Internal error", "path": ["nodes", 1]}],
        }

    assert _run(sp.shopify_levels_by_item(None, [INV_1, INV_2], graphql=gql)) is None


def _partial(levels):
    """INV_1 answered from `levels`, INV_2 answered null WITH a top-level
    error: a per-node failure, never a deleted item."""
    clean = _shopify(levels)

    async def gql(db_, query, variables):
        body = await clean(db_, query, variables)
        body["errors"] = [{"message": "Internal error", "path": ["nodes", 1]}]
        return body

    return gql


def test_tick_a_partial_answer_with_errors_closes_nothing():
    """The panel's P3 input: BV-A's open task is about SKU-2 (IMS 5 vs
    Shopify 0). Tonight Shopify answers INV_1 and a null INV_2 plus a
    top-level error. Nothing is compared and the task stays OPEN."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 5, "BV-B": 0}})
    _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 0, LOC_B: 0}})))
    (task,) = _tasks(db)
    assert task["payload"]["skus"] == ["SKU-2"]
    out = _run(sp.run_parity_tick(db, graphql=_partial({INV_1: {LOC_A: 1, LOC_B: 1}})))
    assert out["checked"] is False and out["tasks"]["closed"] == []
    assert _tasks(db)[0]["status"] == "OPEN"


def test_tick_a_drifted_sku_that_was_not_re_read_keeps_its_task_open(one_id_per_batch):
    """Night 1: SKU-2 drifts at BV-A. Night 2: SKU-2's batch fails, SKU-1
    compares clean. The task names SKU-2, SKU-2 was never re-read -> the
    task stays OPEN; night 3 re-reads SKU-2 clean -> closed. Revert the close
    gate to `if active and summary.get("compared"):` (drop `not owed`) ->
    night 2 closes it on a read that skipped the drifted SKU -> this fails."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 5, "BV-B": 0}})
    _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 0, LOC_B: 0}})))
    out = _run(sp.run_parity_tick(db, graphql=_skipping({INV_1: {LOC_A: 1, LOC_B: 1}})))
    bva = next(s for s in out["stores"] if s["store_id"] == "BV-A")
    assert out["checked"] is True and bva["compared"] == 1 and bva["unknown"] == 1
    assert out["tasks"]["closed"] == [] and _tasks(db)[0]["status"] == "OPEN"
    out = _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 5, LOC_B: 0}})))
    assert out["tasks"]["closed"] == ["BV-A"] and _tasks(db)[0]["status"] == "COMPLETED"


def test_tick_a_drifted_sku_whose_ims_side_went_unknown_keeps_its_task_open(monkeypatch):
    """Same gate, IMS side: SKU-2 drifted and is STILL in the online
    catalogue, but tonight the rule has no answer for it (unknown) while
    SKU-1 compares clean -> still owed, still OPEN. (A SKU that LEFT the
    catalogue is a different night: see the next test.) Drop `not owed` ->
    closed -> fails."""
    from api.services import online_stock_writeback as wb

    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 5, "BV-B": 0}})
    shop = _shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 0, LOC_B: 0}})
    _run(sp.run_parity_tick(db, graphql=shop))
    real = wb.online_quantities_for_skus
    monkeypatch.setattr(wb, "online_quantities_for_skus",
                        lambda db, skus, **k: {s: q for s, q in real(db, skus, **k).items() if s != "SKU-2"})
    out = _run(sp.run_parity_tick(db, graphql=shop))
    assert out["compared"] == 2 and out["unknown"] == 2
    assert out["tasks"]["closed"] == [] and _tasks(db)[0]["status"] == "OPEN"


@pytest.mark.parametrize("gone_from", [("products",), ("catalog_variants",), ("products", "catalog_variants")])
def test_tick_a_task_whose_sku_left_the_catalogue_is_closed(gone_from):
    """Round 4 P5, the stuck July task one shop at a time. Night 1: SKU-2
    drifts at BV-A (IMS 5 vs Shopify 0), the task names SKU-2. SKU-2 then
    leaves the online catalogue (its spine row, its Shopify item mapping, or
    both deleted). Night 2 compares the 2 rows left, all clean: there is no
    drift left to measure, and parity would never compare SKU-2 again ->
    CLOSED. Drop `& set(mapped_skus)` from `owed` -> OPEN forever -> fails."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 5, "BV-B": 0}})
    shop = _shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 0, LOC_B: 0}})
    _run(sp.run_parity_tick(db, graphql=shop))
    assert _tasks(db)[0]["payload"]["skus"] == ["SKU-2"]
    for name in gone_from:
        db.get_collection(name).delete_many({"sku": "SKU-2"})
    out = _run(sp.run_parity_tick(db, graphql=shop))
    assert out["compared"] == 2 and out["drift_count"] == 0
    assert out["tasks"]["closed"] == ["BV-A"] and _tasks(db)[0]["status"] == "COMPLETED"


def test_tick_an_empty_catalogue_still_closes_every_task():
    """Round 4 P6, probe P1: night 1 files BV-A's task; the catalogue is then
    emptied. Every SKU the task names is gone -> closed, on a night that
    reads 'no online-mapped variants' (checked). Put back the early return on
    an empty sample -> the task stays OPEN for ever -> fails."""
    db = _db({"SKU-1": {"BV-A": 5, "BV-B": 1}})
    _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {}})))
    assert _tasks(db)[0]["payload"]["skus"] == ["SKU-1"]
    db.get_collection("products").delete_many({})
    out = _run(sp.run_parity_tick(db, graphql=_shopify({})))
    assert out["checked"] is True and out["reason"] == "no online-mapped variants" and out["sampled"] == 0
    assert out["tasks"] == {"filed": [], "refreshed": [], "closed": ["BV-A"]}
    assert _tasks(db)[0]["status"] == "COMPLETED"


def test_tick_an_empty_catalogue_still_retires_a_shop_that_left_the_map():
    """Probe P2: BV-A's location is cleared AND the catalogue is empty -> its
    task is retired (retiring needs the map, not the sample)."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}})
    db.seed("tasks", [{"task_id": "T-1", "source_ref": "shopify-stock-parity-drift:BV-A", "status": "ESCALATED"}])
    db.get_collection("stores").update_one({"store_id": "BV-A"}, {"$unset": {"shopify_location_id": ""}})
    db.get_collection("products").delete_many({})
    out = _run(sp.run_parity_tick(db, graphql=_shopify({})))
    assert out["tasks"]["closed"] == ["BV-A"] and _tasks(db)[0]["status"] == "COMPLETED"


@pytest.mark.parametrize("failure", ["catalog", "shopify", "creds"])
def test_tick_a_night_that_compared_nothing_still_retires_a_shop_that_left_the_map(monkeypatch, failure):
    """Round 4 P6: the catalog-read failure, the Shopify-read failure and the
    no-creds night compare nothing, so no MAPPED shop's task moves (BV-B's
    stays OPEN) -- but the shop map IS known, so BV-A, whose location was
    cleared, has its task retired. Return without the retire (`closed = []`
    in not_compared) -> BV-A OPEN -> fails."""
    from api.services import online_catalog

    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}})
    db.seed("tasks", [
        {"task_id": "T-A", "source_ref": "shopify-stock-parity-drift:BV-A", "status": "ESCALATED"},
        {"task_id": "T-B", "source_ref": "shopify-stock-parity-drift:BV-B", "status": "OPEN"},
    ])
    db.get_collection("stores").update_one({"store_id": "BV-A"}, {"$unset": {"shopify_location_id": ""}})
    if failure == "catalog":
        def boom(db, skus):
            raise RuntimeError("catalog_variants read died")

        monkeypatch.setattr(online_catalog, "inventory_items_for_skus", boom)
    if failure == "creds":
        monkeypatch.setattr("api.services.shopify_push._has_shopify_creds", lambda db, *a, **k: False)
    out = _run(sp.run_parity_tick(db, graphql=_shopify({}, fail=failure == "shopify")))
    assert out["checked"] is False and out["compared"] == 0
    assert out["tasks"] == {"filed": [], "refreshed": [], "closed": ["BV-A"]}
    assert {t["task_id"]: t["status"] for t in _tasks(db)} == {"T-A": "COMPLETED", "T-B": "OPEN"}


def test_tick_counts_only_task_writes_that_succeeded():
    """Round 4 P8: BaseRepository.update swallows a rejected write into False
    (and complete_task returns it). Night 1 files BV-A's task; then every
    tasks write is rejected. Night 2 (drift persists) must NOT report
    'refreshed', night 3 (clean) must NOT report 'closed', and a retire that
    did not land is not 'closed' either -- the task is still OPEN. Ignore the
    write's return again -> the snapshot lists the shop -> fails."""
    db = _db({"SKU-1": {"BV-A": 5, "BV-B": 1}})
    _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {}})))
    coll = db.get_collection("tasks")

    def rejected(*_a, **_k):
        raise RuntimeError("write rejected")

    coll.update_one = rejected
    out = _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 0, LOC_B: 1}, INV_2: {}})))
    assert out["tasks"] == {"filed": [], "refreshed": [], "closed": []}
    assert "IMS 5 vs Shopify 1" in _tasks(db)[0]["description"]
    out = _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 5, LOC_B: 1}, INV_2: {}})))
    assert out["tasks"] == {"filed": [], "refreshed": [], "closed": []}
    db.get_collection("stores").update_one({"store_id": "BV-A"}, {"$unset": {"shopify_location_id": ""}})
    out = _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_B: 1}, INV_2: {}})))
    assert out["tasks"]["closed"] == []
    assert _tasks(db)[0]["status"] == "OPEN"


def test_tick_a_rejected_task_insert_is_never_reported_filed():
    """Round 5, the panel's input: BV-A drifts (IMS 5 vs Shopify 1) and
    tasks.insert_one raises. BaseRepository.create swallows it into None; the
    tick must report nothing filed and task_filed False -- the collection is
    empty. Put back `return created or task` in create_system_task -> 'filed':
    ['BV-A'] -> fails."""
    db = _db({"SKU-1": {"BV-A": 5, "BV-B": 1}})
    coll = db.get_collection("tasks")

    def rejected(*_a, **_k):
        raise RuntimeError("write rejected")

    coll.insert_one = rejected
    out = _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {}})))
    assert out["drift_count"] == 1
    assert out["tasks"] == {"filed": [], "refreshed": [], "closed": []} and out["task_filed"] is False
    assert _tasks(db) == []


def test_tick_a_drifted_sku_that_fell_out_of_the_sample_keeps_its_task_open():
    """The sample is capped (_sample_variants): SKU-2 drifted at BV-A, then
    tonight's sample holds SKU-1 only, which compares clean. SKU-2 was never
    compared -> still owed, still OPEN. Drop `not owed` -> closed -> fails."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 5, "BV-B": 0}})
    shop = _shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 0, LOC_B: 0}})
    _run(sp.run_parity_tick(db, graphql=shop))
    out = _run(sp.run_parity_tick(db, graphql=shop, sample_limit=1))
    assert out["sampled"] == 1 and out["compared"] == 2
    assert out["tasks"]["closed"] == [] and _tasks(db)[0]["status"] == "OPEN"


def test_tick_ims_side_carries_the_online_block():
    """SKU-1 is in a SUPERADMIN online-blocked collection: the writer sends 0
    at every shop, Shopify holds 0 -- a correct system. The IMS side must be
    the writer's own call. A second computation that loops the shops with
    recommend_allocation(_on_hand_for_skus(...), _safety_buffer(db)) keeps the
    buffer but drops the block -> IMS 5 / 4 vs 0 -> drift tasks at BV-A and
    BV-B -> this fails."""
    db = _db({"SKU-1": {"BV-A": 5, "BV-B": 4}})
    db.seed("ecom_collections", [{"collection_id": "C-BAN", "collection_type": "CUSTOM",
                                  "online_sync_blocked": True, "products": [{"sku": "SKU-1"}]}])
    out = _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 0, LOC_B: 0}, INV_2: {LOC_A: 0, LOC_B: 0}})))
    assert out["compared"] == 4
    assert out["drift_count"] == 0, out["drift"]
    assert _tasks(db) == []


def test_tick_a_location_two_shops_claim_is_unclaimed():
    """BV-B and BV-C both point at LOC_B (a doc written around the 409 door):
    the writer maps NEITHER, so the 7 units Shopify sells at LOC_B are
    written by nobody. Parity reports them. Revert `claimed` to the raw set of
    every shop's location -> LOC_B counts as claimed -> a fully green check ->
    this fails."""
    db = _db({"SKU-1": {"BV-A": 1}})
    db.seed("stores", [_store("BV-C", LOC_B)])
    out = _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 1, LOC_B: 7}, INV_2: {}})))
    assert [s["store_id"] for s in out["stores"]] == ["BV-A"]
    assert out["unclaimed_locations"] == [{"location_id": LOC_B, "units": 7, "skus": ["SKU-1"]}]


def test_tick_a_store_id_stored_with_a_space_still_gets_its_drift_task():
    """Round 8, the panel's probe: BV-A's store doc reads 'BV-A ' (mapped to
    LOC_A; inventory._mapped strips it to 'BV-A'), shelf 5, Shopify 0. The
    snapshot shows the drift at BV-A, so the task must be filed under BV-A's
    ref. Drop either `.strip()` -> the tick loop never finds 'BV-A ' in the
    map (tasks all empty), or the task is filed under 'BV-A ' -> fails."""
    db = _db({"SKU-1": {"BV-A": 5, "BV-B": 1}})
    db.get_collection("stores").update_one({"store_id": "BV-A"}, {"$set": {"store_id": "BV-A "}})
    out = _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 0, LOC_B: 1}, INV_2: {}})))
    assert out["drift_count"] == 1 and out["tasks"]["filed"] == ["BV-A"]
    (task,) = _tasks(db)
    assert task["source_ref"] == "shopify-stock-parity-drift:BV-A" and task["payload"]["store_id"] == "BV-A"


def test_tick_a_shop_that_left_the_mapped_set_has_its_task_closed():
    """BV-A has an open drift task; its location is then cleared. Parity never
    compares BV-A again, so nothing would ever refresh or close the task. The
    tick closes it. Drop the retire call -> tasks all empty, the task OPEN
    forever -> this fails. The pooled ref is not touched (the script's job)."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}})
    db.seed("tasks", [
        {"task_id": "T-1", "source_ref": "shopify-stock-parity-drift:BV-A", "status": "ESCALATED"},
        {"task_id": "T-0", "source_ref": "shopify-stock-parity-drift", "status": "ESCALATED"},
    ])
    db.get_collection("stores").update_one({"store_id": "BV-A"}, {"$unset": {"shopify_location_id": ""}})
    out = _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_B: 1}, INV_2: {}})))
    assert out["tasks"]["closed"] == ["BV-A"]
    by_id = {t["task_id"]: t["status"] for t in _tasks(db)}
    assert by_id == {"T-1": "COMPLETED", "T-0": "ESCALATED"}


def test_tick_finds_the_open_task_past_100_closed_ones():
    """100 closed episodes, then an OPEN task, on BV-A's ref. The repo's
    default page is 100 rows: without the status filter IN the query the OPEN
    row falls off it -> every drifting night files a NEW task and a clean
    night closes none. Drop the status filter from task_triggers.active_tasks
    -> this fails."""
    db = _db({"SKU-1": {"BV-A": 5, "BV-B": 1}})
    ref = "shopify-stock-parity-drift:BV-A"
    db.seed("tasks", [{"task_id": f"T-{i}", "source_ref": ref, "status": "COMPLETED"} for i in range(100)]
            + [{"task_id": "T-OPEN", "source_ref": ref, "status": "OPEN"}])
    out = _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {}})))
    assert out["tasks"] == {"filed": [], "refreshed": ["BV-A"], "closed": []}
    out = _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 5, LOC_B: 1}, INV_2: {}})))
    assert out["tasks"]["closed"] == ["BV-A"]
    assert [t for t in _tasks(db) if t["status"] != "COMPLETED"] == []


def test_tick_a_shop_ims_could_not_read_keeps_its_task_open(monkeypatch):
    """BV-A's on-hand read fails: its rows are unknown, compared 0 -> its open
    task stays open. Revert the close condition to `if active:` (drop the
    `compared` check) -> the unread shop's task is closed -> fails."""
    db = _db({"SKU-1": {"BV-A": 5, "BV-B": 1}})
    db.seed("tasks", [{"task_id": "T-1", "source_ref": "shopify-stock-parity-drift:BV-A", "status": "OPEN"}])
    from api.services import online_stock_writeback as wb

    real = wb._on_hand_for_skus
    monkeypatch.setattr(wb, "_on_hand_for_skus", lambda db, skus, sid: {} if sid == "BV-A" else real(db, skus, sid))
    out = _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 5, LOC_B: 1}, INV_2: {}})))
    assert next(s for s in out["stores"] if s["store_id"] == "BV-A")["compared"] == 0
    assert out["tasks"]["closed"] == []
    assert _tasks(db)[0]["status"] == "OPEN"


def test_tick_no_creds_is_fail_soft(monkeypatch):
    monkeypatch.setattr("api.services.shopify_push._has_shopify_creds", lambda db, *a, **k: False)
    out = _run(sp.run_parity_tick(StrictDB()))
    assert out["checked"] is False and "creds" in out["reason"]


def test_tick_never_raises_into_sentinel(monkeypatch):
    import api.services.shopify_push.inventory as inv

    def boom(db):
        raise RuntimeError("mongo down")

    monkeypatch.setattr(inv, "_stores", boom)
    out = _run(sp.run_parity_tick(_db({"SKU-1": {"BV-A": 1}}), graphql=_shopify({})))
    assert out["checked"] is False and "tick error" in out["reason"]


# ---------------------------------------------------------------------------
# The other IMS-vs-Shopify readers: tally + reconciliation, per location
# ---------------------------------------------------------------------------


def test_unbacked_units_per_location_never_pooled():
    """Pure. Pune (unmapped) holds 5: it backs nothing. BV-A 0 vs LOC_A 3 ->
    3 unbacked; a stray location's units are all unbacked; an item Shopify did
    not return has no key; an unread shop behind a listing is None."""
    mapped = {"BV-A": LOC_A, "BV-B": LOC_B}
    out = sp.unbacked_units(
        [{"sku": "SKU-1", "inventory_item_id": INV_1}, {"sku": "SKU-2", "inventory_item_id": INV_2},
         {"sku": "SKU-3", "inventory_item_id": "gid://shopify/InventoryItem/93"}],
        {"SKU-1": {"BV-A": 0, "BV-B": 0, "BV-PUN": 5}, "SKU-2": {"BV-B": 1}},
        {INV_1: {LOC_A: 3, LOC_B: 0, LOC_STRAY: 2}, INV_2: {LOC_A: 4, LOC_B: 1}},
        mapped,
    )
    assert out == {"SKU-1": 5, "SKU-2": None}


def _tally(monkeypatch, db, levels):
    from api.services import online_sync_health as osh

    monkeypatch.setattr("api.services.shopify_push._graphql", _shopify(levels))
    return _run(osh.stock_tally_live(db))


def _tally_and_parity(monkeypatch, db, levels):
    tally = _tally(monkeypatch, db, levels)
    parity = _run(sp.run_parity_tick(db, graphql=_shopify(levels)))
    return {r["sku"]: r for r in tally["items"]}, parity


def _fail_shelf(monkeypatch, store_id):
    """That shop's on-hand read fails (the rule's STRICT {}); others read."""
    from api.services import online_stock_writeback as wb

    real = wb._on_hand_for_skus
    monkeypatch.setattr(wb, "_on_hand_for_skus", lambda db, skus, sid: {} if sid == store_id else real(db, skus, sid))


def test_tally_pune_never_backs_another_shops_listing(monkeypatch):
    """The panel's input. BV-A 0, BV-B 0, Pune (unmapped) 5; Shopify sells 3
    at BV-A's location. Parity flags BV-A; the tally must say the same: listed
    3, sellable 0 (Pune is sold online nowhere), OVERSELL RISK. Put the pooled
    `listed > on_hand` back as the risk -> 3 > 5 is False -> this fails."""
    db = _db({"SKU-1": {"BV-A": 0, "BV-B": 0}}, pune_units=5)
    rows, parity = _tally_and_parity(monkeypatch, db, {INV_1: {LOC_A: 3, LOC_B: 0}, INV_2: {}})
    assert [(d["store_id"], d["ims"], d["shopify"]) for d in parity["drift"]] == [("BV-A", 0, 3)]
    row = rows["SKU-1"]
    assert row["online_listed_qty"] == 3 and row["on_hand"] == 5 and row["sellable"] == 0
    assert row["oversell_risk"] is True


def test_tally_a_swapped_pair_is_a_risk_not_a_pooled_match(monkeypatch):
    """BV-A 3, BV-B 0; Shopify holds them swapped (LOC_A 0, LOC_B 3). Pooled
    that is 3 vs 3; LOC_B sells 3 units BV-B does not have. Put a pooled
    `listed > sellable` back as the risk -> False -> this fails."""
    db = _db({"SKU-1": {"BV-A": 3, "BV-B": 0}})
    rows, parity = _tally_and_parity(monkeypatch, db, {INV_1: {LOC_A: 0, LOC_B: 3}, INV_2: {}})
    assert parity["drift_count"] == 2
    assert rows["SKU-1"]["sellable"] == 3 and rows["SKU-1"]["oversell_risk"] is True


def _reconcile_page(monkeypatch, db, levels, store_id):
    from api.routers import catalog

    monkeypatch.setattr(catalog, "_get_db", lambda: db)
    monkeypatch.setattr(catalog, "online_mapping_available", lambda db: True)
    monkeypatch.setattr("api.services.shopify_push._graphql", _shopify(levels))
    return _run(catalog.online_stock_reconcile(store_id=store_id, limit=1000, current_user={"user_id": "u1"}))


def _reconcile(monkeypatch, db, levels, store_id):
    return {r["sku"]: r for r in _reconcile_page(monkeypatch, db, levels, store_id)["items"]}


def _cols(row, *keys):
    return tuple(row[k] for k in keys)


def test_reconcile_one_shop_reads_its_own_location_only(monkeypatch):
    """The panel's input: a CORRECT system (BV-A 2 = LOC_A 2, BV-B 3 = LOC_B
    3), the page filtered to BV-A. Its row is BV-A's location: online 2, OK --
    never 2 against all 5 (a false OVERSELL_RISK). Drop the store_id branch
    (compare one shop against every location) -> this fails."""
    db = _db({"SKU-1": {"BV-A": 2, "BV-B": 3}})
    levels = {INV_1: {LOC_A: 2, LOC_B: 3}, INV_2: {}}
    row = _reconcile(monkeypatch, db, levels, "BV-A")["SKU-1"]
    assert (row["in_store"], row["online"], row["status"]) == (2, 2, "OK")
    row = _reconcile(monkeypatch, db, levels, None)["SKU-1"]
    assert (row["in_store"], row["online"], row["status"]) == (5, 5, "OK")
    # An unmapped shop lists nothing from its own shelf: 0 online, never all 5.
    row = _reconcile(monkeypatch, _db({"SKU-1": {"BV-A": 2, "BV-B": 3}}, pune_units=4), levels, "BV-PUN")["SKU-1"]
    assert (row["in_store"], row["online"], row["status"]) == (4, 0, "OK")


def test_reconcile_all_shops_is_decided_location_by_location(monkeypatch):
    """"All stores": BV-A 0, BV-B 0, Pune 5; LOC_A lists 3. Pooled that is 5
    in store vs 3 online -- OK. BV-A's location oversells 3. Drop the row's
    `unbacked` (fall back to the pooled online > in_store) -> OK -> this
    fails."""
    db = _db({"SKU-1": {"BV-A": 0, "BV-B": 0}}, pune_units=5)
    row = _reconcile(monkeypatch, db, {INV_1: {LOC_A: 3, LOC_B: 0}, INV_2: {}}, None)["SKU-1"]
    assert (row["in_store"], row["online"], row["status"]) == (5, 3, "OVERSELL_RISK")


def test_reconcile_recommended_is_what_the_writer_sends_per_location(monkeypatch):
    """Panel probe A. Writer buffer 2, BV-A 3, BV-B 3: the writer sends 1 and
    1; Shopify lists 2 and 2, so parity at tolerance 0 flags both locations.
    The page agrees: 'recommended' is the writer's number (2 over the mapped
    shops, 1 for BV-A) and both views say OVER_ALLOCATED -- never 'OK, within
    safe allocation'. The correct system (1 and 1) is OK. Put back the
    pooled `in_store - buffer` recommendation with `online > recommended`
    (drop the row's `recommended` / `excess`) -> recommended 6, OK -> fails."""
    monkeypatch.setenv("ONLINE_STOCK_SAFETY_BUFFER", "2")
    monkeypatch.setenv("SHOPIFY_STOCK_PARITY_TOLERANCE", "0")
    db = _db({"SKU-1": {"BV-A": 3, "BV-B": 3}})
    levels = {INV_1: {LOC_A: 2, LOC_B: 2}, INV_2: {}}
    parity = _run(sp.run_parity_tick(db, graphql=_shopify(levels)))
    assert sorted((d["store_id"], d["ims"], d["shopify"]) for d in parity["drift"]) == [
        ("BV-A", 1, 2), ("BV-B", 1, 2)]
    page = _reconcile_page(monkeypatch, db, levels, None)
    row = page["items"][0]
    assert _cols(row, "sku", "in_store", "online", "recommended", "delta", "status") == (
        "SKU-1", 6, 4, 2, 2, "OVER_ALLOCATED")
    assert page["summary"]["safety_buffer"] == 2
    row = _reconcile(monkeypatch, db, levels, "BV-A")["SKU-1"]
    assert _cols(row, "in_store", "online", "recommended", "status") == (3, 2, 1, "OVER_ALLOCATED")
    row = _reconcile(monkeypatch, db, {INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {}}, None)["SKU-1"]
    assert _cols(row, "recommended", "delta", "status") == (2, 0, "OK")


def test_reconcile_unmapped_pune_never_backs_a_recommendation(monkeypatch):
    """Panel probe B. Buffer 1, BV-A 0, BV-B 2, Pune (unmapped) 5: the writer
    sends BV-B 1. LOC_B listing 2 is over the writer's number -> recommended
    1, delta 1, OVER_ALLOCATED (the pooled page said recommended 6, OK: Pune's
    shelf backed BV-B's buffer). LOC_B listing 1 is OK. Sum `recommended`
    over every shop the rule read instead of the MAPPED ones -> 5 -> fails."""
    monkeypatch.setenv("ONLINE_STOCK_SAFETY_BUFFER", "1")
    db = _db({"SKU-1": {"BV-A": 0, "BV-B": 2}}, pune_units=5)
    row = _reconcile(monkeypatch, db, {INV_1: {LOC_A: 0, LOC_B: 2}, INV_2: {}}, None)["SKU-1"]
    assert _cols(row, "in_store", "online", "recommended", "delta", "status") == (7, 2, 1, 1, "OVER_ALLOCATED")
    row = _reconcile(monkeypatch, db, {INV_1: {LOC_A: 0, LOC_B: 1}, INV_2: {}}, None)["SKU-1"]
    assert _cols(row, "recommended", "status") == (1, "OK")


def test_reconcile_recommended_carries_the_online_block(monkeypatch):
    """SKU-1 is in a SUPERADMIN online-blocked collection: the writer sends 0
    at every shop and Shopify holds 0 -- a correct system. 'recommended' is
    0, not the 9 on the shelves. Re-derive it from the shelf -> 9 -> fails."""
    db = _db({"SKU-1": {"BV-A": 5, "BV-B": 4}})
    db.seed("ecom_collections", [{"collection_id": "C-BAN", "collection_type": "CUSTOM",
                                  "online_sync_blocked": True, "products": [{"sku": "SKU-1"}]}])
    row = _reconcile(monkeypatch, db, {INV_1: {LOC_A: 0, LOC_B: 0}, INV_2: {}}, None)["SKU-1"]
    assert _cols(row, "in_store", "online", "recommended", "status") == (9, 0, 0, "OK")


def test_a_blocked_sku_listed_within_its_shelf_is_over_allocated_never_an_oversell(monkeypatch):
    """Round 5, the panel's input: SKU-1 is SUPERADMIN-blocked, shelves BV-A 5
    / BV-B 4, Shopify LOC_A 2 / LOC_B 0. The writer sends 0 (the block), but
    the 5 on BV-A's shelf back the 2 listed there: OVER_ALLOCATED (listed
    past the writer's number, within the shelf), 0 units unbacked; the tally
    shows no oversell and sellable 0. Read the shelf through the rule again
    (block included) -> OVERSELL_RISK, 2 units 'beyond stock' -> fails."""
    db = _db({"SKU-1": {"BV-A": 5, "BV-B": 4}})
    db.seed("ecom_collections", [{"collection_id": "C-BAN", "collection_type": "CUSTOM",
                                  "online_sync_blocked": True, "products": [{"sku": "SKU-1"}]}])
    levels = {INV_1: {LOC_A: 2, LOC_B: 0}, INV_2: {}}
    page = _reconcile_page(monkeypatch, db, levels, None)
    row = {r["sku"]: r for r in page["items"]}["SKU-1"]
    assert _cols(row, "in_store", "online", "recommended", "delta", "status") == (9, 2, 0, 2, "OVER_ALLOCATED")
    assert page["summary"]["oversell_risk_units"] == 0 and page["summary"]["over_allocated"] == 1
    row = {r["sku"]: r for r in _tally(monkeypatch, db, levels)["items"]}["SKU-1"]
    assert _cols(row, "online_listed_qty", "sellable", "oversell_risk") == (2, 0, False)


def test_tally_and_reconcile_draw_the_same_oversell_line(monkeypatch):
    """Both screens call it OVERSELL only past the SHELF behind a location;
    the writer's buffer only moves `sellable` / `recommended` (and, on the
    reconciliation screen, OVER_ALLOCATED). Buffer 1, BV-B shelf 2:
      LOC_B lists 2 -> tally sellable 1, no oversell; page OVER_ALLOCATED;
      LOC_B lists 3 -> tally oversell; page OVERSELL_RISK.
    Tally at the writer's buffer (mutation 3) -> listing 2 is a risk ->
    fails. Page at the writer's buffer (mutation 4) -> OVERSELL_RISK at 2 ->
    fails. Tally `sellable` from the shelf -> 2 -> fails."""
    monkeypatch.setenv("ONLINE_STOCK_SAFETY_BUFFER", "1")
    db = _db({"SKU-1": {"BV-A": 0, "BV-B": 2}})
    at_2 = {INV_1: {LOC_A: 0, LOC_B: 2}, INV_2: {}}
    at_3 = {INV_1: {LOC_A: 0, LOC_B: 3}, INV_2: {}}
    row = {r["sku"]: r for r in _tally(monkeypatch, db, at_2)["items"]}["SKU-1"]
    assert _cols(row, "online_listed_qty", "sellable", "oversell_risk") == (2, 1, False)
    assert _reconcile(monkeypatch, db, at_2, None)["SKU-1"]["status"] == "OVER_ALLOCATED"
    row = {r["sku"]: r for r in _tally(monkeypatch, db, at_3)["items"]}["SKU-1"]
    assert _cols(row, "online_listed_qty", "sellable", "oversell_risk") == (3, 1, True)
    assert _reconcile(monkeypatch, db, at_3, None)["SKU-1"]["status"] == "OVERSELL_RISK"


def test_tally_a_mapped_shop_ims_could_not_read_is_unknown_never_a_row(monkeypatch):
    """Panel input: BV-A shelf 1, BV-B's on-hand read FAILS; Shopify LOC_A 1,
    LOC_B 3 -- three units selling at the location of a shop IMS could not
    read. The tally says on_hand_unknown and lists nothing (never sellable 1
    beside a clean row). Delete the rule_by_location guard (`any(sid not in
    per_shop ...)`) -> rows come back -> fails."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 0}})
    _fail_shelf(monkeypatch, "BV-B")
    out = _tally(monkeypatch, db, {INV_1: {LOC_A: 1, LOC_B: 3}, INV_2: {}})
    assert out["items"] == [] and out["summary"]["on_hand_unknown"] is True


def test_tally_an_unreadable_online_block_is_unknown_never_sellable_zero(monkeypatch):
    """Round 7 P2, the panel's input: the online-block read fails while every
    shelf reads fine. The rule is unknown (the writer aborts its batch on
    it), so the tally says on_hand_unknown and lists nothing -- never every
    online SKU as a confident sellable 0. Delete rule_by_location's
    `if skus and not quantities: return None` -> rows come back -> fails."""
    from api.services import online_block

    def dead(*_a, **_k):
        raise RuntimeError("ecom_collections read died")

    monkeypatch.setattr(online_block, "blocked_skus", dead)
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 0}})
    out = _tally(monkeypatch, db, {INV_1: {LOC_A: 1, LOC_B: 0}, INV_2: {}})
    assert out["items"] == [] and out["summary"]["on_hand_unknown"] is True


def test_tally_a_listing_ims_has_no_rule_for_is_a_risk(monkeypatch):
    """Panel input: a products row SKU-9 with no product_id (so the rule has
    no answer for it) and Shopify LOC_A listing 4 of it. unbacked_units says
    None (unknown); the tally flags it -- unknown is never "no risk". Read
    None as 0 again (`(unbacked.get(sku) or 0) > 0`) -> False -> fails."""
    inv_9 = "gid://shopify/InventoryItem/99"
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 0}})
    db.seed("products", [{"sku": "SKU-9"}])
    db.seed("catalog_products", [_listing(9, "SKU-9")])
    db.seed("catalog_variants", [{"sku": "SKU-9", "parent_product_id": "c9", "shopify_inventory_item_id": inv_9}])
    out = _tally(monkeypatch, db, {INV_1: {LOC_A: 1, LOC_B: 0}, INV_2: {}, inv_9: {LOC_A: 4}})
    rows = {r["sku"]: r for r in out["items"]}
    assert _cols(rows["SKU-9"], "online_listed_qty", "sellable", "oversell_risk") == (4, 0, True)
    assert rows["SKU-1"]["oversell_risk"] is False


def test_reconcile_one_shop_is_never_blanked_by_another_shops_failed_read(monkeypatch):
    """Panel input: BV-A 2, BV-B 3; Shopify LOC_A 2, LOC_B 3.
      * BV-B's read fails, page filtered to BV-A -> BV-A's own row, OK
        (another shop's failed read never blanks it);
      * the same, "All stores" -> ONHAND_UNKNOWN with the 5 Shopify lists;
      * BV-A's OWN read fails, filtered to BV-A -> ONHAND_UNKNOWN, online
        still the 2 Shopify lists there -- never a confident 0 (delta -2).
    Drop the store_id narrowing in rule_by_location -> the first case is
    ONHAND_UNKNOWN -> fails. Throw the map away with the rule (`mapped = {}`
    when the rule is unknown) -> online 0 -> fails."""
    db = _db({"SKU-1": {"BV-A": 2, "BV-B": 3}})
    levels = {INV_1: {LOC_A: 2, LOC_B: 3}, INV_2: {}}
    _fail_shelf(monkeypatch, "BV-B")
    row = _reconcile(monkeypatch, db, levels, "BV-A")["SKU-1"]
    assert _cols(row, "in_store", "online", "recommended", "status") == (2, 2, 2, "OK")
    row = _reconcile(monkeypatch, db, levels, None)["SKU-1"]
    assert _cols(row, "online", "recommended", "delta", "status") == (5, None, None, "ONHAND_UNKNOWN")
    _fail_shelf(monkeypatch, "BV-A")
    row = _reconcile(monkeypatch, db, levels, "BV-A")["SKU-1"]
    assert _cols(row, "in_store", "online", "recommended", "delta", "status") == (2, 2, None, None, "ONHAND_UNKNOWN")


def test_reconcile_and_tally_put_the_worst_swapped_pair_first(monkeypatch):
    """Round 4 P1 + P2, the panel's input. SKU-1: BV-A 3 / BV-B 0 on the
    shelf against Shopify LOC_A 0 / LOC_B 3 -- 3 units unbacked at LOC_B
    (the totals match, 3 vs 3). SKU-2: BV-A 0 / BV-B 1 against 1 / 1 -- 1
    unit unbacked. Both screens lead with SKU-1, and the reconciliation row
    reports delta 3 (never 'listed total - recommended total' = 0 on an
    OVERSELL_RISK row). Put back the pooled `online - rec` delta/sort ->
    SKU-2 first, SKU-1 delta 0 -> fails; put back the tally's
    -(listed - sellable) sort -> SKU-2 first -> fails."""
    db = _db({"SKU-1": {"BV-A": 3, "BV-B": 0}, "SKU-2": {"BV-A": 0, "BV-B": 1}})
    levels = {INV_1: {LOC_A: 0, LOC_B: 3}, INV_2: {LOC_A: 1, LOC_B: 1}}
    page = _reconcile_page(monkeypatch, db, levels, None)
    assert [_cols(r, "sku", "status", "delta") for r in page["items"]] == [
        ("SKU-1", "OVERSELL_RISK", 3), ("SKU-2", "OVERSELL_RISK", 1)]
    assert page["summary"]["oversell_risk_units"] == 4
    tally = _tally(monkeypatch, db, levels)
    assert [(r["sku"], r["oversell_risk"]) for r in tally["items"]] == [("SKU-1", True), ("SKU-2", True)]


def test_both_screens_put_the_most_units_no_shelf_backs_first(monkeypatch):
    """Round 5, the panel's input (the second sort key). Buffer 4, so the
    writer sends 0 everywhere. SKU-1: BV-A shelf 4 vs LOC_A 5 -> 1 unit no
    shelf backs, 5 past the writer. SKU-2: BV-A shelf 0 vs LOC_A 3 -> 3
    unbacked, 3 past the writer. Both OVERSELL_RISK; SKU-2 leads on both
    screens. Drop `-(over or 0)` from reconcile_items' key, or
    `-(unbacked.get(sku) or 0)` from the Stock Tally's -> SKU-1 (delta 5)
    first -> fails."""
    monkeypatch.setenv("ONLINE_STOCK_SAFETY_BUFFER", "4")
    db = _db({"SKU-1": {"BV-A": 4, "BV-B": 0}, "SKU-2": {"BV-A": 0, "BV-B": 0}})
    levels = {INV_1: {LOC_A: 5, LOC_B: 0}, INV_2: {LOC_A: 3, LOC_B: 0}}
    page = _reconcile_page(monkeypatch, db, levels, None)
    assert [_cols(r, "sku", "status", "delta") for r in page["items"]] == [
        ("SKU-2", "OVERSELL_RISK", 3), ("SKU-1", "OVERSELL_RISK", 5)]
    tally = _tally(monkeypatch, db, levels)
    assert [(r["sku"], r["oversell_risk"]) for r in tally["items"]] == [("SKU-2", True), ("SKU-1", True)]


def test_both_screens_order_over_allocated_rows_per_location_never_pooled(monkeypatch):
    """Round 5, the panel's input (the third sort key, which orders the whole
    OVER_ALLOCATED band: every row there has 0 units unbacked). Buffer 2.
    SKU-1: shelves 3 / 5 (the writer sends 1 / 3) vs LOC_A 3 / LOC_B 1 -> 2
    units past the writer at LOC_A, while the totals match (4 vs 4). SKU-2:
    shelves 3 / 3 (sends 1 / 1) vs 2 / 1 -> 1 unit past the writer. SKU-1
    leads on both screens. Put back the pooled forms, `-((online or 0) -
    recommended)` in reconcile_items or `-((listed or 0) - sellable)` in the
    Stock Tally -> SKU-2 first -> fails."""
    monkeypatch.setenv("ONLINE_STOCK_SAFETY_BUFFER", "2")
    db = _db({"SKU-1": {"BV-A": 3, "BV-B": 5}, "SKU-2": {"BV-A": 3, "BV-B": 3}})
    levels = {INV_1: {LOC_A: 3, LOC_B: 1}, INV_2: {LOC_A: 2, LOC_B: 1}}
    page = _reconcile_page(monkeypatch, db, levels, None)
    assert [_cols(r, "sku", "online", "recommended", "delta", "status") for r in page["items"]] == [
        ("SKU-1", 4, 4, 2, "OVER_ALLOCATED"), ("SKU-2", 3, 2, 1, "OVER_ALLOCATED")]
    tally = _tally(monkeypatch, db, levels)
    assert [_cols(r, "sku", "online_listed_qty", "sellable", "oversell_risk") for r in tally["items"]] == [
        ("SKU-1", 4, 4, False), ("SKU-2", 3, 2, False)]


def test_screens_read_an_item_deleted_in_shopify_as_listed_unknown(monkeypatch):
    """Round 7: Shopify answers INV_2 null (deleted in Shopify admin). The
    reconciliation screen reads it as listed-unknown, as an unread batch --
    on the one-shop view too, never a crash. Drop the None-strip in
    live_listed_qty_for_skus -> `per.get` on None -> fails."""
    db = _db({"SKU-1": {"BV-A": 2, "BV-B": 3}, "SKU-2": {"BV-A": 1}})
    for sid in ("BV-A", None):
        row = _reconcile(monkeypatch, db, {INV_1: {LOC_A: 2, LOC_B: 3}}, sid)["SKU-2"]
        assert _cols(row, "online", "status") == (None, "LISTED_UNKNOWN"), sid


def test_reconcile_an_empty_store_id_is_all_stores(monkeypatch):
    """Round 4 P4: `?store_id=` (an empty string). The route and the on-hand
    reader read it as 'all stores'; rule_by_location narrowed the map to {}
    (`is not None`), so every listing looked unclaimed. BV-A 2 / BV-B 1 /
    Pune 5 against LOC_A 2 / LOC_B 1 must read 8 / 3 / 3 / OK both ways. Put
    back `if store_id is not None:` -> recommended 0, OVERSELL_RISK -> fails."""
    db = _db({"SKU-1": {"BV-A": 2, "BV-B": 1}}, pune_units=5)
    levels = {INV_1: {LOC_A: 2, LOC_B: 1}, INV_2: {}}
    for sid in (None, ""):
        row = _reconcile(monkeypatch, db, levels, sid)["SKU-1"]
        assert _cols(row, "in_store", "online", "recommended", "status") == (8, 3, 3, "OK"), sid


def test_tick_samples_through_the_writers_item_resolver():
    """Panel input: SKU-1's inventory item lives only on catalog_products.ecom
    (the resolver's documented fallback) -- the writer finds INV_1 there and
    plans BV-A 1 / BV-B 0. Shopify holds 9 at each location: parity must
    see it. The old catalog_variants-only sample never sampled SKU-1 ->
    compared 0, 'no online-mapped variants' -> fails."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 0}})
    db.get_collection("catalog_variants").delete_many({"sku": "SKU-1"})
    db.get_collection("catalog_products").update_one({"id": "c1"}, {"$set": {"ecom.shopify_inventory_item_id": INV_1}})
    out = _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 9, LOC_B: 9}, INV_2: {}})))
    assert out["sampled"] == 2
    assert sorted((d["sku"], d["store_id"], d["ims"], d["shopify"]) for d in out["drift"]) == [
        ("SKU-1", "BV-A", 1, 9), ("SKU-1", "BV-B", 0, 9)]


def test_tick_an_unreadable_catalog_touches_no_task(monkeypatch):
    """The item resolver is STRICT: a read failure is UNKNOWN, reported as
    such, never 'checked, no online-mapped variants'. Fail soft to [] -> the
    tick reads checked True -> fails."""
    from api.services import online_catalog

    def boom(db, skus):
        raise RuntimeError("catalog_variants read died")

    monkeypatch.setattr(online_catalog, "inventory_items_for_skus", boom)
    db = _db({"SKU-1": {"BV-A": 1}})
    db.seed("tasks", [{"task_id": "T-1", "source_ref": "shopify-stock-parity-drift:BV-A", "status": "OPEN"}])
    out = _run(sp.run_parity_tick(db, graphql=_shopify({})))
    assert out["checked"] is False and "catalog read failed" in out["reason"]
    assert _tasks(db)[0]["status"] == "OPEN"


_ONE_STEP = ("open Online Stock (sidebar, Stock & supply; the page is titled Online vs In-store Stock), "
             "pick Shop BV-A (BV-A), and ask an ADMIN or SUPERADMIN to set each "
             "product's quantity at Shop BV-A (BV-A)'s location in Shopify admin to its Recommended number there "
             "(what IMS sends now)")


def _one_safe_step(text):
    """The ONE instruction (decided 2026-10-01) and nothing that presses a
    listing's status: never Send to website (push_product re-lists a
    draft), never Take off website, never 'lift the block'. Round 17: it
    points at the number IMS sends NOW (the shop's Recommended), never a
    number the task carries, and makes neither false claim of round 16 (a
    sale re-sends an absolute number, so a Shopify edit IS undone)."""
    assert _ONE_STEP in text, text
    assert "Send to website" not in text and "Take off website" not in text and "lift the block" not in text
    assert "number above" not in text and "never undo" not in text and "nothing re-sends" not in text


def test_drift_task_gives_the_one_safe_instruction():
    """The 01:00 / 09:00 pass and Push stock (sync_stock_levels) send only
    products whose IMS number CHANGED since the recorded baseline
    (test_shopify_online_stock.py::test_T6), and no stock-only press
    re-sends an unchanged number: the one safe instruction is the Shopify
    admin quantity -- IMS's number now, read off the shop's Online Stock
    view. The task carries the
    shop's store_id, so its first owner is that shop's STORE_MANAGER, who
    asks an admin. Put back the round-15 'press Send to website' line ->
    fails."""
    db = _db({"SKU-1": {"BV-A": 5, "BV-B": 1}})
    _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {}})))
    (task,) = _tasks(db)
    _one_safe_step(task["description"])
    assert "IMS re-sends a product's number only when it changes in IMS" in task["description"]
    assert task["store_id"] == "BV-A"
    assert "Store manager: open Online Stock (sidebar" in task["description"]
    assert "Inventory > Online Stock" not in task["description"]
    assert "lines" not in task["payload"]


def test_tick_a_refreshed_task_keeps_every_sku_still_owed(one_id_per_batch):
    """Night 1: SKU-2 drifts at BV-A (IMS 5, Shopify 0) -> the task names
    SKU-2. Night 2: SKU-1 drifts at BV-A (IMS 1, Shopify 5) and SKU-2's
    Shopify batch fails -> refreshed, and the task names SKU-1 AND SKU-2.
    Night 3: SKU-1 clean, SKU-2 unread again -> SKU-2 is still owed, the task
    stays OPEN. Drop `owed |` from payload.skus -> night 2 names SKU-1 only
    and night 3 closes it -> fails."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 5, "BV-B": 0}})
    _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 0, LOC_B: 0}})))
    assert _tasks(db)[0]["payload"]["skus"] == ["SKU-2"]
    out = _run(sp.run_parity_tick(db, graphql=_skipping({INV_1: {LOC_A: 5, LOC_B: 1}})))
    assert out["tasks"]["refreshed"] == ["BV-A"]
    assert _tasks(db)[0]["payload"]["skus"] == ["SKU-1", "SKU-2"]
    out = _run(sp.run_parity_tick(db, graphql=_skipping({INV_1: {LOC_A: 1, LOC_B: 1}})))
    assert out["tasks"]["closed"] == [] and _tasks(db)[0]["status"] == "OPEN"


def test_tick_the_task_text_names_every_sku_that_keeps_it_open(one_id_per_batch):
    """Round 5, the panel's input: the test above carried on. Night 2's text
    must name SKU-2 (owed, its batch unread) beside SKU-1's drift. Nights
    3-7: SKU-1 compares clean, SKU-2 stays unread -- the task stays OPEN because of
    SKU-2 alone, so the refreshed text names SKU-2, no longer claims SKU-1
    drifted, and payload.skus is SKU-2 only. Drop the owed line (`if owed:`)
    -> SKU-2 is nowhere in the text -> fails; stop refreshing on a night
    with no drift (`if drift:`) -> night 3 closes the task -> fails."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 5, "BV-B": 0}})
    _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 0, LOC_B: 0}})))
    _run(sp.run_parity_tick(db, graphql=_skipping({INV_1: {LOC_A: 5, LOC_B: 1}})))
    text = _tasks(db)[0]["description"]
    assert "SKU-1 (IMS 1 vs Shopify 5)" in text and "not compared tonight" in text and "SKU-2" in text
    for _night in range(3, 8):
        out = _run(sp.run_parity_tick(db, graphql=_skipping({INV_1: {LOC_A: 1, LOC_B: 1}})))
        assert out["tasks"] == {"filed": [], "refreshed": ["BV-A"], "closed": []}
    (task,) = _tasks(db)
    assert task["status"] == "OPEN" and task["payload"]["skus"] == ["SKU-2"]
    assert "not compared tonight" in task["description"] and ": SKU-2." in task["description"]
    assert "SKU-1" not in task["description"]
    # Round 8: five nights on, SKU-2 still carries the numbers it drifted with
    # on night 1 and the step that clears it.
    assert "SKU-2 (IMS 5 vs Shopify 0 when last compared)" in task["description"]
    _one_safe_step(task["description"])
    assert task["payload"]["last_seen"] == {"SKU-2": {"ims": 5, "shopify": 0}}


def _night_two_unread(how, monkeypatch):
    """Night 2 of the panel's probes: SKU-2's Shopify batch is throttled, or
    one online-block read blips (the rule then has no number for any SKU)."""
    if how == "shopify_batch_throttled":
        return _skipping({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 5, LOC_B: 0}})
    from api.services import online_stock_writeback as wb

    monkeypatch.setattr(wb, "_blocked_online", lambda db, skus: None)
    return _shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 5, LOC_B: 0}})


@pytest.mark.parametrize("how", ["shopify_batch_throttled", "online_block_read_blip"])
def test_tick_an_owed_sku_keeps_its_numbers_and_its_step(one_id_per_batch, monkeypatch, how):
    """Round 8, the panel's probes. Night 1: SKU-2, BV-A shelf 0 vs LOC_A 5
    (an oversell) -> 'SKU-2 (IMS 0 vs Shopify 5)' and the Shopify admin step.
    Night 2: SKU-2 is not compared (its batch throttled, or the online block
    unreadable) while Shopify still lists 5. The drift is still real and
    nothing re-sends it by itself, so the refreshed task still names SKU-2
    with its last numbers and the step, never 'Nothing to press', and
    payload.last_seen keeps them. Put back the round-7 owed line (drift
    rows only on the step line, payload without last_seen) -> fails."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 0, "BV-B": 0}})
    _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 5, LOC_B: 0}})))
    assert "SKU-2 (IMS 0 vs Shopify 5)" in _tasks(db)[0]["description"]
    out = _run(sp.run_parity_tick(db, graphql=_night_two_unread(how, monkeypatch)))
    assert out["checked"] is True and out["tasks"]["refreshed"] == ["BV-A"]
    (task,) = _tasks(db)
    text = task["description"]
    assert task["status"] == "OPEN" and task["payload"]["skus"] == ["SKU-2"]
    assert "SKU-2 (IMS 0 vs Shopify 5 when last compared)" in text
    _one_safe_step(text)
    assert "Nothing to press" not in text
    assert task["payload"]["last_seen"] == {"SKU-2": {"ims": 0, "shopify": 5}}


def test_tick_a_sku_that_drifts_again_is_never_called_not_compared():
    """Round 7 P3, the panel's input: SKU-1 drifts at BV-A on night 1 (IMS 5
    vs Shopify 1) and again on night 2 (IMS 5 vs Shopify 0). Night 2 WAS
    compared: the text names SKU-1 on the drift line only, never under 'not
    compared tonight'. Drop `- drifted` from `owed` -> the owed sentence
    names SKU-1 right after its own numbers -> fails."""
    db = _db({"SKU-1": {"BV-A": 5, "BV-B": 1}})
    _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {}})))
    _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 0, LOC_B: 1}, INV_2: {}})))
    text = _tasks(db)[0]["description"]
    assert "SKU-1 (IMS 5 vs Shopify 0)" in text and "not compared tonight" not in text


def test_tick_the_task_text_names_every_drifted_sku_past_the_top_five():
    """Round 7 P5 and round 17, the panel's probe: 7 SKUs drift at BV-A
    (SKU-n: IMS n+2 vs Shopify 0). payload.skus holds all 7 and all 7 keep
    the task open, so the text names all 7 WITH both numbers (decided
    2026-10-01: the task names each drifted SKU with IMS vs Shopify). Name
    only drift[:5] -> SKU-6 and SKU-7 appear nowhere -> fails; give numbers
    to the first five only (round 16's `_named`) -> 'SKU-1 (IMS 3 vs
    Shopify 0)' is missing -> fails."""
    skus = [f"SKU-{n}" for n in range(1, 8)]
    inv = {s: f"gid://shopify/InventoryItem/{90 + n}" for n, s in enumerate(skus, 1)}
    db = StrictDB()
    db.seed("stores", [_store("BV-A", LOC_A), _store("BV-B", LOC_B)])
    db.seed("products", [{"product_id": "p" + s, "sku": s} for s in skus])
    db.seed("catalog_products", [_listing(n, s) for n, s in enumerate(skus, 1)])
    db.seed("catalog_variants", [{"sku": s, "parent_product_id": f"c{n}", "shopify_inventory_item_id": inv[s]}
                                 for n, s in enumerate(skus, 1)])
    db.seed("stock_units", [{"stock_id": f"{s}-{i}", "product_id": "p" + s, "store_id": "BV-A",
                             "status": "AVAILABLE"} for n, s in enumerate(skus, 1) for i in range(n + 2)])
    _run(sp.run_parity_tick(db, graphql=_shopify({inv[s]: {LOC_A: 0, LOC_B: 0} for s in skus})))
    (task,) = _tasks(db)
    assert task["payload"]["skus"] == skus
    text = task["description"]
    assert [s for n, s in enumerate(skus, 1) if f"{s} (IMS {n + 2} vs Shopify 0)" not in text] == []
    _one_safe_step(text)


def test_a_tasks_read_failure_files_no_second_task():
    """task_triggers.active_tasks promises to RAISE on a read error, but the
    real repository's find_many swallowed it into [] -- 'no active task' --
    so a drifting night with a blipped tasks read filed a SECOND open task
    beside the ESCALATED one. Read through find_many again -> 2 tasks ->
    fails."""
    from database.repositories.task_repository import TaskRepository

    db = StrictDB()
    coll = db.seed("tasks", [{"task_id": "T-1", "source_ref": "shopify-stock-parity-drift:BV-A",
                              "status": "ESCALATED"}])

    def dead(*_a, **_k):
        raise RuntimeError("tasks read died")

    coll.find = dead
    summary = {"drift_count": 1, "drift": [{"sku": "SKU-1", "ims": 5, "shopify": 1, "delta": 4}],
               "tolerance": 2, "max_delta": 4, "compared": 1, "clean_skus": []}
    assert sp.sync_drift_task(TaskRepository(coll), {"store_id": "BV-A"}, summary, mapped_skus={"SKU-1"}) is None
    assert len(coll.docs) == 1


# ---------------------------------------------------------------------------
# Snapshot pruning
# ---------------------------------------------------------------------------


def test_prune_snapshots_uses_iso_cutoff():
    class _Coll:
        deleted_query = None

        def delete_many(self, query):
            self.deleted_query = query
            return types.SimpleNamespace(deleted_count=3)

    coll = _Coll()
    assert sp.prune_snapshots(coll, retention_days=30) == 3
    assert "$lt" in coll.deleted_query["generated_at"]
    assert sp.prune_snapshots(None) == 0


# ---------------------------------------------------------------------------
# Round 10
# ---------------------------------------------------------------------------

LOC_OLD = "gid://shopify/Location/4444"


def _locations(db, unticked=(), unknown=False, inactive=()):
    """Record Shopify's own location list as the writer does (fresh): LOC_A,
    LOC_B and LOC_OLD (mapped to no shop), ACTIVE unless named in `inactive`,
    ticked to fulfil online orders unless named in `unticked`. `unknown`
    records nothing."""
    from api.services.shopify_push.inventory import record_location_verdict

    if not unknown:
        record_location_verdict(db, {"rows": [
            {"id": gid, "name": gid, "isActive": gid not in inactive, "fulfillsOnlineOrders": gid not in unticked}
            for gid in (LOC_A, LOC_B, LOC_OLD)
        ]})


@pytest.mark.parametrize("old", ["unticked", "deactivated", "ticked", "unknown"])
def test_a_location_the_storefront_does_not_sell_from_oversells_nothing(monkeypatch, old):
    """Round 10, the panel's probe. BV-A shelf 1 = LOC_A 1, BV-B 0 = LOC_B 0,
    and LOC_OLD -- mapped to no shop, ACTIVE, NOT ticked to fulfil online
    orders (the owner took the writer's own "untick it" advice) -- still
    holds 4 units. The storefront sells 1: the writer's verdict is green, so
    the Stock Tally, the reconciliation screen and parity's unclaimed report
    are too. Round 17: so is a DEACTIVATED LOC_OLD still ticked to fulfil
    online orders (the other leg of inventory.dead_mapped_reason). Ticked
    and active, the 4 units sell online with no shelf behind them: all three
    say so. Location list unknown: every location counts, as before. Drop
    online_selling_locations' filter from live_listed_qty_for_skus -> the
    tally's oversell / the page's OVERSELL_RISK come back -> fails; drop it
    from unclaimed_locations -> LOC_OLD is reported -> fails; re-spell the
    dead test as `not fulfillsOnlineOrders` -> the deactivated LOC_OLD sells
    on all three -> fails."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 0}})
    _locations(db, unticked=(LOC_OLD,) if old == "unticked" else (), unknown=old == "unknown",
               inactive=(LOC_OLD,) if old == "deactivated" else ())
    levels = {INV_1: {LOC_A: 1, LOC_B: 0, LOC_OLD: 4}, INV_2: {}}
    sells = old in ("ticked", "unknown")
    rows, parity = _tally_and_parity(monkeypatch, db, levels)
    assert _cols(rows["SKU-1"], "online_listed_qty", "oversell_risk") == ((5, True) if sells else (1, False))
    row = _reconcile(monkeypatch, db, levels, None)["SKU-1"]
    assert _cols(row, "online", "status") == ((5, "OVERSELL_RISK") if sells else (1, "OK"))
    assert parity["unclaimed_locations"] == (
        [{"location_id": LOC_OLD, "units": 4, "skus": ["SKU-1"]}] if sells else [])
    assert parity["drift_count"] == 0


def test_a_mapped_location_that_cannot_sell_online_oversells_nothing_and_still_drifts(monkeypatch):
    """Round 10, the same root on a MAPPED shop: LOC_B (BV-B's own) is not
    ticked to fulfil online orders -- the writer's dead_mapped_reason,
    flagged on every press. Its 3 units sell nothing online, so neither
    screen calls them an oversell. Round 13 (decided 2026-10-01): parity
    measures whether Shopify holds the writer's number at each MAPPED
    location, sells online or not, so it files BV-B's drift (0 vs 3) -- and
    the screens read that same full level for the Online column and the
    OVER_ALLOCATED verdict: BV-B's own view is 3 listed vs 0 sent,
    OVER_ALLOCATED, never 0 / OK beside the task that files this same
    over-listing (the two verdicts still ask different questions: round
    14's test_a_shops_view_shows_its_tasks_numbers_and_asks_its_own_question).
    Drop the non-selling
    filter everywhere -> OVERSELL_RISK -> fails; drop the mapped location's
    full level too (the round-10 filter) -> BV-B reads 0 / OK -> fails."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 0}})
    _locations(db, unticked=(LOC_B,))
    levels = {INV_1: {LOC_A: 1, LOC_B: 3}, INV_2: {}}
    rows, parity = _tally_and_parity(monkeypatch, db, levels)
    assert _cols(rows["SKU-1"], "online_listed_qty", "sellable", "oversell_risk") == (4, 1, False)
    row = _reconcile(monkeypatch, db, levels, "BV-B")["SKU-1"]
    assert _cols(row, "online", "recommended", "delta", "status") == (3, 0, 3, "OVER_ALLOCATED")
    row = _reconcile(monkeypatch, db, levels, None)["SKU-1"]
    assert _cols(row, "online", "recommended", "delta", "status") == (4, 1, 3, "OVER_ALLOCATED")
    assert [(d["store_id"], d["ims"], d["shopify"]) for d in parity["drift"]] == [("BV-B", 0, 3)]
    assert parity["tasks"]["filed"] == ["BV-B"] and "Products: SKU-1 (IMS 0 vs Shopify 3)" in _tasks(db)[0]["description"]


def _press_take_down(monkeypatch, db, twin_id):
    """An ADMIN presses Take off website, LIVE: Shopify accepts the DRAFT."""
    from api.routers import online_store_push as osp
    from api.services.shopify_push import product as push_product_mod

    async def gql(db_, query, variables):  # noqa: ARG001
        return {"data": {"productUpdate": {"product": {"id": variables["input"]["id"]}, "userErrors": []}}}

    monkeypatch.setattr(push_product_mod, "_live_or_reason", lambda db_: (True, None))
    monkeypatch.setattr(push_product_mod, "_graphql", gql)
    monkeypatch.setattr(osp, "_get_db", lambda: db)
    return _run(osp.take_down_product(twin_id, current_user={"user_id": "u1", "roles": ["ADMIN"]}))["result"]


@pytest.mark.parametrize("listed", [2, 1, -1])
def test_no_tolerance_where_the_writer_sends_zero(listed):
    """Round 10, the owner-safe default: the tolerance (2) holds only where
    the writer sends MORE than 0. The panel's probe: shelves BV-A 0 / BV-B 0
    (the writer sends 0), Shopify LOC_A 2 / LOC_B 2 -- four units no shelf
    backs, which the per-location tolerance let through at both locations.
    Every unit off the writer's 0 is drift. Put back `delta <= tol` for
    every row -> drift_count 0, no task -> fails."""
    db = _db({"SKU-1": {"BV-A": 0, "BV-B": 0}})
    out = _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: listed, LOC_B: listed}, INV_2: {}})))
    assert out["drift_count"] == 2
    assert out["tasks"]["filed"] == ["BV-A", "BV-B"]
    assert "(none where IMS lists 0)" in _tasks(db)[0]["description"]


def test_the_tolerance_still_holds_where_the_writer_sends_more_than_zero():
    """The control: BV-A 5 vs LOC_A 3 (delta 2) is within tolerance, and so is
    IMS 1 vs Shopify 0 (the writer sends 1); IMS 0 vs Shopify 1 is not.
    Drop the tolerance everywhere -> A and B drift -> fails."""
    out = sp.compare_variant_parity(
        [{"sku": "A", "store_id": "S", "ims_available": 5, "shopify_available": 3},
         {"sku": "B", "store_id": "S", "ims_available": 1, "shopify_available": 0},
         {"sku": "C", "store_id": "S", "ims_available": 0, "shopify_available": 1}],
        tolerance=2,
    )
    assert [d["sku"] for d in out["drift"]] == ["C"] and out["clean_skus"] == ["A", "B"]


LOC_LEGACY = "gid://shopify/Location/5555"


def test_a_location_missing_from_shopifys_list_still_sells(monkeypatch):
    """Round 11, the panel's probe P1. Shopify's list (locations(first: 50),
    no includeLegacy) never shows LOC_LEGACY -- a legacy fulfillment-service
    location, mapped to no shop -- yet its levels hold 4 units. Missing from
    the list is unknown, never proven non-selling: the Stock Tally lists 5
    and flags the oversell, the reconciliation screen says OVERSELL_RISK and
    parity reports LOC_LEGACY. Keep only the gids the list shows as selling
    (the round-10 filter) -> 1 / OK / [] -> fails."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 0}})
    _locations(db)
    levels = {INV_1: {LOC_A: 1, LOC_B: 0, LOC_LEGACY: 4}, INV_2: {}}
    rows, parity = _tally_and_parity(monkeypatch, db, levels)
    assert _cols(rows["SKU-1"], "online_listed_qty", "oversell_risk") == (5, True)
    assert _cols(_reconcile(monkeypatch, db, levels, None)["SKU-1"], "online", "status") == (5, "OVERSELL_RISK")
    assert parity["unclaimed_locations"] == [{"location_id": LOC_LEGACY, "units": 4, "skus": ["SKU-1"]}]


def _sentinel_db(db):
    """What SENTINEL hands the tick: the REAL SeededDatabaseConnection (no
    item access) over `db` as its connected real database."""
    from database.connection import SeededDatabaseConnection

    conn = object.__new__(SeededDatabaseConnection)  # past the singleton
    conn._real_db = types.SimpleNamespace(is_connected=True, db=db, get_collection=db.get_collection)
    return conn


def test_the_tick_compares_through_sentinels_connection():
    """Round 11 BLOCKER. The only caller is SENTINEL's run_parity_tick(self.db),
    and self.db is database.connection.SeededDatabaseConnection, which has no
    item access: the rule's block read (db["ecom_collections"]) raised, the
    rule answered {} and every night compared nothing -- no task ever filed
    or closed. Shelves BV-A 0 / BV-B 0, Shopify LOC_A 3: through the wrapper
    the tick compares all 4 pairs and files BV-A's task, as on the raw db.
    Drop the tick's _raw_db unwrap -> compared 0, nothing filed -> fails."""
    db = _db({"SKU-1": {"BV-A": 0, "BV-B": 0}})
    out = _run(sp.run_parity_tick(_sentinel_db(db), graphql=_shopify({INV_1: {LOC_A: 3, LOC_B: 0}, INV_2: {}})))
    assert (out["checked"], out["compared"], out["unknown"], out["drift_count"]) == (True, 4, 0, 1)
    assert out["tasks"]["filed"] == ["BV-A"] and len(_tasks(db)) == 1


# ---------------------------------------------------------------------------
# Round 13
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("twin", [False, True])
@pytest.mark.parametrize("stale", [
    {"sku": "SKU-1 ", "product_id": "p0", "is_active": False},  # a padded old row
    {"sku": "SKU-1", "is_active": False},  # a row with no product_id
])
def test_parity_takes_retired_from_the_rules_own_reader(stale, twin):
    """Round 13, the panel's probe, round 16's reader. A stale spine row for
    SKU-1 -- padded, or with no product_id -- is stored AHEAD of the live
    one (p1, active). The rule (_sku_to_pid: the first row WITH a
    product_id, looked up exactly) reads SKU-1 as ACTIVE and sends BV-A its
    shelf 3; Shopify lists 9, on a listing that is PUBLISHED -- so it is
    compared and drifts. `twin`: the listing also carries a DELISTED/LIVE
    stamp beside PUBLISHED -- live is read from the status every take-down
    door writes, never from a second marker. Give parity its own first-row
    retired loop back, or read the stamp -> nothing compared -> fails."""
    db = _db({"SKU-1": {"BV-A": 3, "BV-B": 0}})
    db.get_collection("products").docs.insert(0, dict(stale))
    if twin:
        db.get_collection("catalog_products").update_one(
            {"id": "c1"}, {"$set": {"ecom.online_state": "DELISTED", "ecom.delist_mode": "LIVE"}})
    out = _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 9, LOC_B: 0}, INV_2: {}})))
    assert [(d["store_id"], d["ims"], d["shopify"]) for d in out["drift"]] == [("BV-A", 3, 9)]
    (task,) = _tasks(db)
    assert "Products: SKU-1 (IMS 3 vs Shopify 9)" in task["description"]


def test_the_shop_filter_is_spelled_as_the_writers_map(monkeypatch):
    """Round 13, the panel's probe. The stores row is 'BV-A ' (padded); the
    writer's map (inventory._mapped) strips it to 'BV-A' -> LOC_A, and the
    rule reads BV-A's shelf (5) under that same spelling. The screen's
    dropdown sends the stored id unchanged. Filtered to 'BV-A ' the row is
    BV-A's own location -- 9 listed vs 5 sent, OVERSELL_RISK -- exactly the
    row filtered to 'BV-A', never 0 / 0 / OK (a mapped shop read as
    unmapped, a real oversell hidden). Compare the raw filter against the
    map's keys -> 0 / OK -> fails."""
    db = _db({"SKU-1": {"BV-A": 5, "BV-B": 0}})
    db.get_collection("stores").update_one({"store_id": "BV-A"}, {"$set": {"store_id": "BV-A "}})
    levels = {INV_1: {LOC_A: 9, LOC_B: 0}, INV_2: {}}
    want = (5, 9, 5, 4, "OVERSELL_RISK")
    for sid in ("BV-A ", "BV-A"):
        row = _reconcile(monkeypatch, db, levels, sid)["SKU-1"]
        assert _cols(row, "in_store", "online", "recommended", "delta", "status") == want, sid


# ---------------------------------------------------------------------------
# Round 14
# ---------------------------------------------------------------------------


def _ban(db, *skus):
    db.seed("ecom_collections", [{"collection_id": "C-BAN", "collection_type": "CUSTOM",
                                  "online_sync_blocked": True, "products": [{"sku": s} for s in skus]}])


@pytest.mark.parametrize("dead", ["retired_reader", "parent_listing_read", "live_listing_read"])
def test_an_unreadable_live_answer_touches_no_task(monkeypatch, dead):
    """Round 14's test gap, round 16's reader. Night 1 files SKU-2 (IMS 0 vs
    LOC_A 3). Night 2 one read behind 'is this listing live' fails: the
    rule's own reader of 'retired' (online_stock_writeback._sku_to_pid), or
    the read of the listing that carries each SKU (the parents of the
    catalog_variants rows, inside online_catalog.listings_for_skus), or
    (round 17) the read of those listings' status (listing_visible's
    input, inside inventory.skus_on_live_listings). Which SKU is live is
    unknown, so the tick compares nothing and leaves BV-A's task exactly as
    it was. Read the failed answer as 'none retired' (`retired = set()`),
    call listings_for_skus fail-soft (strict=False: no listing found,
    nothing live) or call skus_on_live_listings fail-soft (a dead status
    read is 'nothing live') -> the night is 'checked' -> fails."""
    import copy

    from api.services import online_stock_writeback as wb

    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 0, "BV-B": 0}})
    shop = _shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 3, LOC_B: 0}})
    _run(sp.run_parity_tick(db, graphql=shop))
    before = copy.deepcopy(_tasks(db)[0])
    assert before["payload"]["skus"] == ["SKU-2"]
    coll = db.get_collection("catalog_products")
    real = coll.find
    if dead == "retired_reader":
        monkeypatch.setattr(wb, "_sku_to_pid", lambda db_, skus: None)
    else:
        def find(flt=None, *a, **k):
            if dead == "parent_listing_read" and any("id" in c for c in (flt or {}).get("$or") or []):
                raise RuntimeError("parent read died")
            if dead == "live_listing_read" and "$in" in ((flt or {}).get("id") or {}):
                raise RuntimeError("live read died")
            return real(flt, *a, **k)

        coll.find = find
    out = _run(sp.run_parity_tick(db, graphql=shop))
    assert out["checked"] is False and "catalog read failed" in out["reason"]
    (task,) = _tasks(db)
    assert (task["status"], task["description"], task["payload"]) == (
        "OPEN", before["description"], before["payload"])


@pytest.mark.parametrize("case", ["own_location_unticked", "under_listed", "within_tolerance"])
def test_a_shops_view_shows_its_tasks_numbers_and_asks_its_own_question(monkeypatch, case):
    """Round 14, the panel's counter-inputs to 'the view and the task always
    agree'. One shop's view reads parity's full level and the writer's
    number -- the two numbers its task names -- but its verdict is its own:
    OVER_ALLOCATED is any unit listed past the writer's number, while parity
    drift is either way, past its tolerance where the writer sends more
    than 0. (a) BV-B's own location unticked, writer 4, LOC_B 0: task 4 vs
    0, view 0 / 4 / OK. (b) writer 5, LOC_A 1: task 5 vs 1, view 1 / 5 /
    OK. (c) buffer 2, shelf 7 (writer 5), LOC_A 6: no task, view 6 / 5 /
    OVER_ALLOCATED. Pins the narrowed claims (catalog.online_stock_reconcile,
    online_sync_health.live_listed_qty_for_skus): change either comparator
    without them -> fails."""
    if case == "own_location_unticked":
        db = _db({"SKU-1": {"BV-A": 0, "BV-B": 4}})
        _locations(db, unticked=(LOC_B,))
        sid, per_loc, row_want, task_want = "BV-B", {LOC_A: 0, LOC_B: 0}, (0, 4, 0, "OK"), [("BV-B", 4, 0)]
    elif case == "under_listed":
        db = _db({"SKU-1": {"BV-A": 5, "BV-B": 0}})
        sid, per_loc, row_want, task_want = "BV-A", {LOC_A: 1, LOC_B: 0}, (1, 5, 0, "OK"), [("BV-A", 5, 1)]
    else:
        monkeypatch.setenv("ONLINE_STOCK_SAFETY_BUFFER", "2")
        db = _db({"SKU-1": {"BV-A": 7, "BV-B": 0}})
        sid, per_loc, row_want, task_want = "BV-A", {LOC_A: 6, LOC_B: 0}, (6, 5, 1, "OVER_ALLOCATED"), []
    levels = {INV_1: per_loc, INV_2: {}}
    row = _reconcile(monkeypatch, db, levels, sid)["SKU-1"]
    assert _cols(row, "online", "recommended", "delta", "status") == row_want
    parity = _run(sp.run_parity_tick(db, graphql=_shopify(levels)))
    assert [(d["store_id"], d["ims"], d["shopify"]) for d in parity["drift"]] == task_want


# ---------------------------------------------------------------------------
# Round 16: parity compares only SKUs on a LIVE listing; one safe instruction
# ---------------------------------------------------------------------------


def _set(db, coll, key, **fields):
    db.get_collection(coll).update_one(key, {"$set": fields})


def _size_of_c1(db):
    """SKU-2 becomes a SIZE of SKU-1: its catalog_variants row rides c1 (the
    writer's listings_for_skus answers c1 for it) and its own twin is a
    variant-of child that owns no listing."""
    _set(db, "catalog_variants", {"sku": "SKU-2"}, parent_product_id="c1")
    _set(db, "catalog_products", {"id": "c2"},
         **{"ecom.variant_of": {"product_id": "p1", "twin_id": "c1", "sku": "SKU-1"}})


_TAKEN_DOWN = {"ecom.status": "DRAFT", "ecom.taken_down_at": "2026-10-01T00:00:00"}


def _retire(sku, **stamp):
    def change(db):
        _set(db, "products", {"sku": sku}, is_active=False)
        if stamp:
            _set(db, "catalog_products", {"sku": sku}, **{f"ecom.{k}": v for k, v in stamp.items()})
    return change


def _retire_by_pim(sku, twin_id):
    """The panel's round-17 probe: the spine row is linked to its twin by
    pim_product_id (the create door's own link) and the twin's own sku is a
    legacy one, so no sku-only link reaches it. Retired, its take-down
    failed: the twin still says PUBLISHED and the Catalog screen says
    DELIST_FAILED."""
    def change(db):
        _set(db, "products", {"sku": sku}, is_active=False, pim_product_id=twin_id)
        _set(db, "catalog_products", {"id": twin_id}, sku=f"{sku}-LEGACY",
             **{"ecom.online_state": "DELIST_FAILED", "ecom.delist_mode": "LIVE"})
    return change


# state -> (SKU-2 is a size of SKU-1, the change, SKU-2 still compared on night 2)
_LIVE_STATES = {
    "taken_down": (False, lambda db: _set(db, "catalog_products", {"id": "c2"}, **_TAKEN_DOWN), False),
    "never_published": (False, lambda db: _set(db, "catalog_products", {"id": "c2"}, **{"ecom.status": "DRAFT"}), False),
    # Round 17: PUBLISHED is not live by itself (inventory.listing_visible).
    "staged_published_draft": (False, lambda db: _set(
        db, "catalog_products", {"id": "c2"}, **{"ecom.online_stock": {"tracked": False, "quantities": {}}}), False),
    "published_without_gid": (False, lambda db: _set(
        db, "catalog_products", {"id": "c2"}, **{"ecom.shopify_product_id": None}), False),
    "retired_take_down_failed": (False, _retire("SKU-2", online_state="DELIST_FAILED", delist_mode="LIVE"), False),
    "retired_take_down_dark": (False, _retire("SKU-2", online_state="DELISTED", delist_mode="SIMULATED"), False),
    "retired_twin_by_pim_link": (False, _retire_by_pim("SKU-2", "c2"), False),
    "size_parent_taken_down": (True, lambda db: _set(db, "catalog_products", {"id": "c1"}, **_TAKEN_DOWN), False),
    # Round 17 (open problem 1): ONE reader, no second "taken down"
    # computation. A retired product whose take-down failed (or ran DARK)
    # leaves its listing PUBLISHED, and that listing still sells its ACTIVE
    # size: the size stays compared, as both screens assess it. So does one
    # an ADMIN re-published after a LIVE retire (Send to website writes
    # PUBLISHED back and clears every off stamp).
    "size_parent_retired": (True, _retire("SKU-1", online_state="DELIST_FAILED", delist_mode="LIVE"), True),
    "size_parent_retired_dark": (True, _retire("SKU-1", online_state="DELISTED", delist_mode="SIMULATED"), True),
    "size_parent_retired_by_pim_link": (True, _retire_by_pim("SKU-1", "c1"), True),
    "size_parent_retired_then_republished": (True, _retire("SKU-1", status="PUBLISHED", online_state=None,
                                                           taken_down_at=None), True),
    # Round 17: a retired SKU is never compared, a size too -- no shop's view lists it.
    "size_retired_on_a_live_parent": (True, _retire("SKU-2"), False),
    "still_live": (False, lambda db: None, True),
    # Round 17: a live listing a stock pass recorded untracked (a size minted
    # untracked: Shopify sells it without limit) is live -- listing_visible,
    # never listing_already_live.
    "live_but_untracked": (False, lambda db: _set(
        db, "catalog_products", {"id": "c2"}, **{"ecom.online_stock": {"tracked": False, "quantities": {"BV-A": 0}}}),
        True),
}


@pytest.mark.parametrize("state", list(_LIVE_STATES))
def test_parity_compares_only_skus_on_a_live_listing(state):
    """Round 16 (decided 2026-10-01), the panel's open problems 1, 2, 3 and 5.
    Night 1: SKU-2 is on a LIVE listing and drifts at BV-A (IMS 0 vs LOC_A
    3) -> BV-A's task. Then the listing that carries SKU-2 -- its own, or for
    a size its PARENT's (the writer's listings_for_skus) -- stops being live:
    taken down (DRAFT + taken_down_at, what every LIVE take-down writes),
    never published, or retired in IMS whatever its take-down did (failed
    or DARK: the listing still says PUBLISHED). Night 2 CLOSES the task:
    SKU-2 left the live set. Round 17: so does a retired SIZE on its
    parent's live listing (no shop's view lists a retired SKU), a
    staged-PUBLISHED draft and a PUBLISHED twin with no gid (live is
    listing_visible, never the status alone). The controls stay compared
    and the task is refreshed: an untouched listing, a live one recorded
    untracked, and -- round 17's open problem 1 -- an ACTIVE size whose
    retired parent's listing is still PUBLISHED (its take-down failed, ran
    DARK, or was undone by a re-publish): that listing still sells the size.
    Drop listing_visible, or re-spell it as status == PUBLISHED -> a draft
    stays compared; drop the retired SKU check -> a retired size stays
    compared; put back a second 'taken down' computation (the retired
    product's twin found by _resolve_twin) -> the size on a still-PUBLISHED
    listing is dropped; judge live by listing_already_live -> the untracked
    listing is dropped -> fails."""
    size, change, compared = _LIVE_STATES[state]
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 0, "BV-B": 0}})
    if size:
        _size_of_c1(db)
    shop = _shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 3, LOC_B: 0}})
    out = _run(sp.run_parity_tick(db, graphql=shop))
    assert out["tasks"]["filed"] == ["BV-A"] and _tasks(db)[0]["payload"]["skus"] == ["SKU-2"]
    change(db)
    out = _run(sp.run_parity_tick(db, graphql=shop))
    if compared:
        assert out["tasks"] == {"filed": [], "refreshed": ["BV-A"], "closed": []}
        assert [(d["sku"], d["ims"], d["shopify"]) for d in out["drift"]] == [("SKU-2", 0, 3)]
        assert _tasks(db)[0]["status"] == "OPEN"
    else:
        assert out["tasks"] == {"filed": [], "refreshed": [], "closed": ["BV-A"]}
        assert out["drift"] == [] and _tasks(db)[0]["status"] == "COMPLETED"


@pytest.mark.parametrize("state", list(_LIVE_STATES))
def test_a_shops_view_and_its_task_read_one_live_reader(monkeypatch, state):
    """Round 17, open problem 2: 'is this listing live on Shopify' had two
    readers -- parity's listing_visible and the screens' online flag (a gid
    OR PUBLISHED, so a draft counted). Take off website pressed LIVE on c2
    (DRAFT, gid kept), BV-A's shelf 0 and LOC_A still 3: parity closed
    BV-A's task while BV-A's view said SKU-2 OVERSELL_RISK. Now ONE reader
    (inventory.skus_on_live_listings), with NO monkeypatch of it here: for
    every listing state of round 16/17, SKU-2 is in BV-A's task exactly
    when BV-A's view assesses it (not NOT_ONLINE) and the Stock Tally lists it;
    an assessed row shows the task's two numbers and its oversell; a row
    not assessed is NOT_ONLINE, never an alarm. Put the screens back on
    online_status_for_skus().online -> a drafted listing is assessed and
    alarms beside no task -> fails."""
    size, change, compared = _LIVE_STATES[state]
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 0, "BV-B": 0}})
    if size:
        _size_of_c1(db)
    change(db)
    levels = {INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 3, LOC_B: 0}}
    tally, parity = _tally_and_parity(monkeypatch, db, levels)
    assert [(d["sku"], d["store_id"], d["ims"], d["shopify"]) for d in parity["drift"]] == (
        [("SKU-2", "BV-A", 0, 3)] if compared else [])
    assert [t["store_id"] for t in _tasks(db)] == (["BV-A"] if compared else [])
    view = _reconcile(monkeypatch, db, levels, "BV-A")
    row = view.get("SKU-2")
    assert ("SKU-2" in tally) is compared
    assert bool(row and row["status"] != "NOT_ONLINE") is compared
    if compared:
        assert _cols(row, "online", "recommended", "status") == (3, 0, "OVERSELL_RISK")
        assert tally["SKU-2"]["oversell_risk"] is True
    elif row is not None:
        assert _cols(row, "online", "status") == (0, "NOT_ONLINE")


def test_the_take_off_website_press_leaves_no_alarm_beside_a_closed_task(monkeypatch):
    """Round 17, open problem 2 through the real door: night 1 files BV-A
    (SKU-2: IMS 0 vs LOC_A 3); an ADMIN presses Take off website LIVE on c2
    (DRAFT, gid kept). Night 2 closes the task, and BV-A's view reads SKU-2
    NOT_ONLINE, the Stock Tally no longer lists it -- never OVERSELL_RISK
    with no task beside it. Screens on the old online flag -> fails."""
    from api.routers import online_store_push as osp

    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 0, "BV-B": 0}})
    levels = {INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 3, LOC_B: 0}}
    _run(sp.run_parity_tick(db, graphql=_shopify(levels)))
    assert _reconcile(monkeypatch, db, levels, "BV-A")["SKU-2"]["status"] == "OVERSELL_RISK"
    _live_shopify(monkeypatch)
    monkeypatch.setattr(osp, "_get_db", lambda: db)
    res = _run(osp.take_down_product("c2", current_user={"user_id": "u1", "roles": ["ADMIN"]}))["result"]
    assert res["mode"] == "LIVE"
    tally, parity = _tally_and_parity(monkeypatch, db, levels)
    assert parity["tasks"]["closed"] == ["BV-A"]
    row = _reconcile(monkeypatch, db, levels, "BV-A")["SKU-2"]
    assert _cols(row, "online", "status") == (0, "NOT_ONLINE")
    assert "SKU-2" not in tally


def _live_shopify(monkeypatch):
    """The push gates LIVE and Shopify accepting every productUpdate."""
    from api.services.shopify_push import product as push_product_mod

    async def gql(db_, query, variables):  # noqa: ARG001
        return {"data": {"productUpdate": {"product": {"id": variables["input"]["id"]}, "userErrors": []}}}

    monkeypatch.setattr(push_product_mod, "_live_or_reason", lambda db_: (True, None))
    monkeypatch.setattr(push_product_mod, "_graphql", gql)


@pytest.mark.parametrize("door", ["take_off_website", "block_cutover", "retire_hook", "dark_take_off_website"])
def test_a_live_take_down_by_any_door_closes_the_task(monkeypatch, door):
    """Round 16, open problem 1: 'off the website' had two markers and the
    SUPERADMIN block cutover wrote only one. Now there is ONE: the DRAFT
    (+ taken_down_at) push_product_delist writes on every LIVE take-down,
    read through the writer's listing_visible. Night 1: SKU-2 drifts at BV-A
    (IMS 0 vs LOC_A 3). Then its listing is taken down LIVE by the Take off
    website press, the SUPERADMIN block of a collection naming it, or the
    retire hook -- night 2 CLOSES the task. A DARK press is a SIMULATED plan
    (nothing left Shopify): the task is refreshed. Read the old DELISTED/
    LIVE stamp instead of the status -> the press and the cutover leave it
    OPEN -> fails."""
    from api.routers import online_store_collections as osc
    from api.routers import online_store_push as osp
    from api.services import online_delist

    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 0, "BV-B": 0}})
    shop = _shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 3, LOC_B: 0}})
    _run(sp.run_parity_tick(db, graphql=shop))
    assert _tasks(db)[0]["payload"]["skus"] == ["SKU-2"]
    if door != "dark_take_off_website":
        _live_shopify(monkeypatch)
    admin = {"user_id": "u1", "roles": ["SUPERADMIN"]}
    if door in ("take_off_website", "dark_take_off_website"):
        monkeypatch.setattr(osp, "_get_db", lambda: db)
        res = _run(osp.take_down_product("c2", current_user=admin))["result"]
        assert res["mode"] == ("SIMULATED" if door.startswith("dark") else "LIVE")
    elif door == "block_cutover":
        db.seed("ecom_collections", [{"collection_id": "C-BAN", "collection_type": "CUSTOM",
                                      "products": [{"sku": "SKU-2", "position": 0}]}])
        monkeypatch.setattr(osc, "_get_db", lambda: db)
        assert _run(osc.block_collection("C-BAN", current_user=admin))["delisted"] == 1
    else:
        twin = db.get_collection("catalog_products").find_one({"id": "c2"})
        assert _run(online_delist.delist_if_live(db, twin, reason="deactivated", actor=admin))["mode"] == "LIVE"
    out = _run(sp.run_parity_tick(db, graphql=shop))
    if door == "dark_take_off_website":
        assert out["tasks"]["refreshed"] == ["BV-A"] and _tasks(db)[0]["status"] == "OPEN"
    else:
        assert out["tasks"]["closed"] == ["BV-A"] and _tasks(db)[0]["status"] == "COMPLETED"


# case -> (the drifted SKU, IMS, Shopify at LOC_A)
_CLASSES = {
    "plain": ("SKU-1", 5, 0),
    "blocked": ("SKU-1", 0, 5),
    "size_of_a_blocked_parent": ("SKU-2", 1, 5),
}


@pytest.mark.parametrize("case", list(_CLASSES))
def test_every_drift_gets_the_one_safe_instruction(case):
    """Round 16, open problems 3, 5 and 6: the per-class lines named presses
    that re-list a product (Send to website on a draft or a retired parent)
    or that are refused (Send to website on a blocked parent, on a size).
    Every drift now gets ONE instruction -- set the quantity at the shop's
    location in Shopify admin to the IMS number -- whatever the class, and
    the text names both numbers; round 17: the number to set is the shop's
    Recommended now. A blocked listing the cutover has not drafted (DARK)
    is still live and still compared (a retired size no longer is: round
    17's size_retired_on_a_live_parent). Put back any per-class line ->
    'Send to website' / 'Take off website' / 'lift the block' -> fails."""
    sku, ims, listed = _CLASSES[case]
    db = _db({"SKU-1": {"BV-A": 5, "BV-B": 0}, "SKU-2": {"BV-A": 1, "BV-B": 0}})
    if "size" in case:
        _size_of_c1(db)
    if "blocked" in case:
        _ban(db, "SKU-1")
    one = {LOC_A: listed, LOC_B: 0}
    # The other SKU compares clean: SKU-1 lists what the writer sends (5, or 0 blocked).
    levels = {INV_1: one, INV_2: {LOC_A: 1, LOC_B: 0}} if sku == "SKU-1" else \
        {INV_1: {LOC_A: 0 if "blocked" in case else 5, LOC_B: 0}, INV_2: one}
    out = _run(sp.run_parity_tick(db, graphql=_shopify(levels)))
    assert [(d["sku"], d["store_id"], d["ims"], d["shopify"]) for d in out["drift"]] == [(sku, "BV-A", ims, listed)]
    (task,) = _tasks(db)
    assert f"Products: {sku} (IMS {ims} vs Shopify {listed})." in task["description"]
    _one_safe_step(task["description"])
    assert "lines" not in task["payload"]


def test_the_worst_delta_is_the_worst_drifted_row():
    """Round 16, open problem 4: since round 10's zero tolerance, a row can
    drift at a SMALLER delta than a clean one (IMS 0 vs Shopify 1 drifts at
    1; IMS 5 vs Shopify 3 is within tolerance at 2). 'Worst delta' is over
    the drifted rows, so it is a number the task names. Take max_delta over
    every compared row again -> 'worst delta 2' beside 'Products: SKU-1 (IMS 0 vs
    Shopify 1)' -> fails."""
    db = _db({"SKU-1": {"BV-A": 0, "BV-B": 0}, "SKU-2": {"BV-A": 5, "BV-B": 0}})
    out = _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 1, LOC_B: 0}, INV_2: {LOC_A: 3, LOC_B: 0}})))
    assert [(d["sku"], d["delta"]) for d in out["drift"]] == [("SKU-1", 1)]
    assert out["max_delta"] == 1 and next(s for s in out["stores"] if s["store_id"] == "BV-A")["max_delta"] == 1
    (task,) = _tasks(db)
    assert "worst delta 1." in task["description"] and task["payload"]["max_delta"] == 1


# ---------------------------------------------------------------------------
# Round 17: a task names only SKUs its shop's view lists; the step reads now
# ---------------------------------------------------------------------------


def test_every_sku_a_task_names_is_a_row_in_its_shops_view(monkeypatch):
    """Round 17, the panel's probe: SKU-2 is a size of c1 (PUBLISHED) and
    retired in IMS; BV-A holds 2 of it and LOC_A lists 3. The Stock Tally and
    the reconciliation screen list no retired SKU, so parity compares none
    either (decided: retired is skipped): BV-A's task names SKU-1 alone, and
    BV-A's view holds SKU-1 with the task's two numbers (Online = Shopify,
    Recommended = IMS). Drop parity's retired-SKU check -> the task names
    'SKU-2 (IMS 0 vs Shopify 3)', a SKU neither screen shows -> fails."""
    db = _db({"SKU-1": {"BV-A": 5, "BV-B": 0}, "SKU-2": {"BV-A": 2, "BV-B": 0}})
    _size_of_c1(db)
    _set(db, "products", {"sku": "SKU-2"}, is_active=False)
    levels = {INV_1: {LOC_A: 1, LOC_B: 0}, INV_2: {LOC_A: 3, LOC_B: 0}}
    rows, parity = _tally_and_parity(monkeypatch, db, levels)
    assert [(d["sku"], d["store_id"], d["ims"], d["shopify"]) for d in parity["drift"]] == [("SKU-1", "BV-A", 5, 1)]
    view = _reconcile(monkeypatch, db, levels, "BV-A")
    assert sorted(view) == sorted(rows) == ["SKU-1"]
    for d in parity["drift"]:
        assert _cols(_reconcile(monkeypatch, db, levels, d["store_id"])[d["sku"]], "online", "recommended") == (
            d["shopify"], d["ims"])
    (task,) = _tasks(db)
    assert "SKU-2" not in task["description"] and task["payload"]["skus"] == ["SKU-1"]


def test_the_step_points_at_the_number_ims_sends_now(monkeypatch):
    """Round 17, the panel's probe p7: night 1 SKU-1 at BV-A is IMS 5 vs
    Shopify 0, so the task names 'SKU-1 (IMS 5 vs Shopify 0)'. During the
    day a sale drops the shelf to 4 and the writer re-sends 4 (a sale
    re-sends an absolute number, so 'never undo' was false). An admin who
    set the task's 5 would list one unit with no shelf behind it, within
    tolerance, and night 2 would close the task. The step sends the admin
    to BV-A's Recommended -- 4 now -- and says the task's numbers may be out
    of date. Put back 'to the IMS number above' -> fails."""
    db = _db({"SKU-1": {"BV-A": 5, "BV-B": 0}})
    levels = {INV_1: {LOC_A: 0, LOC_B: 0}, INV_2: {}}
    _run(sp.run_parity_tick(db, graphql=_shopify(levels)))
    (task,) = _tasks(db)
    text = task["description"]
    assert "SKU-1 (IMS 5 vs Shopify 0)" in text and "may be out of date" in text
    _one_safe_step(text)
    db.get_collection("stock_units").update_one({"stock_id": "SKU-1-BV-A-0"}, {"$set": {"status": "SOLD"}})
    assert _reconcile(monkeypatch, db, levels, "BV-A")["SKU-1"]["recommended"] == 4


# ---------------------------------------------------------------------------
# Round 17, review round 1: the one live reader, read honestly everywhere
# ---------------------------------------------------------------------------


def _dead_live_read(db, where):
    """One catalog read behind the live set dies: the listings' status read
    (skus_on_live_listings' own find) or the parent-listing read inside
    listings_for_skus."""
    coll = db.get_collection("catalog_products")
    real = coll.find

    def find(flt=None, *a, **k):
        if where == "status" and "$in" in ((flt or {}).get("id") or {}):
            raise RuntimeError("live read died")
        if where == "parents" and any("id" in c for c in (flt or {}).get("$or") or []):
            raise RuntimeError("parent read died")
        return real(flt, *a, **k)

    coll.find = find
    return lambda: setattr(coll, "find", real)


@pytest.mark.parametrize("where", ["status", "parents"])
def test_a_dead_live_listing_read_is_unknown_on_both_screens(monkeypatch, where):
    """Review round 1 (fail-soft screens): night 1 files BV-A (SKU-2: IMS 0
    vs LOC_A 3). Then a read behind the live set dies. Parity compares
    nothing and keeps the task OPEN; the screens must say UNKNOWN, never a
    confident 'not online' beside it: every BV-A row LISTED_UNKNOWN with
    live_listings_unknown, and the Stock Tally tallies nothing and says so.
    Read the reader fail-soft on the screens (set() on a dead read) -> every
    row NOT_ONLINE, the tally 'nothing listed' -> fails."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 0, "BV-B": 0}})
    _size_of_c1(db)
    levels = {INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 3, LOC_B: 0}}
    _run(sp.run_parity_tick(db, graphql=_shopify(levels)))
    restore = _dead_live_read(db, where)
    out = _run(sp.run_parity_tick(db, graphql=_shopify(levels)))
    assert out["checked"] is False and _tasks(db)[0]["status"] == "OPEN"
    page = _reconcile_page(monkeypatch, db, levels, "BV-A")
    assert {r["sku"]: r["status"] for r in page["items"]} == {"SKU-1": "LISTED_UNKNOWN", "SKU-2": "LISTED_UNKNOWN"}
    # Review round 2: the columns beside 'Unverified' are unknown too, never a confident 0.
    assert {r["sku"]: (r["online"], r["delta"]) for r in page["items"]} == {"SKU-1": (None, None), "SKU-2": (None, None)}
    assert page["live_listings_unknown"] is True and page["listed_qty_live"] is False
    assert page["summary"]["not_online"] == 0
    tally = _tally(monkeypatch, db, levels)
    assert tally["items"] == [] and tally["summary"]["live_listings_unknown"] is True
    assert tally["summary"]["listed_qty_live"] is False
    restore()
    assert _reconcile(monkeypatch, db, levels, "BV-A")["SKU-2"]["status"] == "OVERSELL_RISK"


def test_the_tally_reads_the_live_set_once(monkeypatch):
    """Review round 1: stock_tally_live read the live set for the Shopify
    read and AGAIN for the rows; a second read that died answered 'fully
    live, 0 SKUs, 0 at risk' with no banner beside an open task. It is read
    once and handed down. Read it again inside stock_tally_summary -> the
    second (dying) read blanks the tally -> fails."""
    from api.services.shopify_push import inventory

    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 0, "BV-B": 0}})
    real = inventory.skus_on_live_listings
    calls = []

    def once(db_, skus, **k):
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("second read died")
        return real(db_, skus, **k)

    monkeypatch.setattr(inventory, "skus_on_live_listings", once)
    tally = _tally(monkeypatch, db, {INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 3, LOC_B: 0}})
    assert len(calls) == 1
    rows = {r["sku"]: r for r in tally["items"]}
    assert rows["SKU-2"]["oversell_risk"] is True and tally["summary"]["listed_qty_live"] is True


def test_no_live_listing_is_covered_never_shopify_unavailable(monkeypatch):
    """Review round 1: with every listing a DRAFT (nothing published yet),
    there is nothing to read from Shopify -- the pages must not say 'Live
    Shopify quantities are unavailable'. listed_qty_live is True on both
    (vacuous coverage), and nothing is assessed. Drop the no-live-listing
    case -> listed_qty_live False -> fails."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 0, "BV-B": 0}})
    for c in ("c1", "c2"):
        _set(db, "catalog_products", {"id": c}, **{"ecom.status": "DRAFT"})
    levels = {INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 3, LOC_B: 0}}
    tally = _tally(monkeypatch, db, levels)
    assert tally["items"] == [] and tally["summary"]["listed_qty_live"] is True
    assert not tally["summary"].get("live_listings_unknown")
    page = _reconcile_page(monkeypatch, db, levels, "BV-A")
    assert page["listed_qty_live"] is True and page["live_listings_unknown"] is False
    assert {r["status"] for r in page["items"]} == {"NOT_ONLINE"}


def test_both_screens_read_shopify_only_for_live_skus(monkeypatch):
    """Review round 1 test gap: the screens hand the Shopify level read only
    the SKUs on a live listing, so a drafted SKU never takes a slot of the
    capped read or counts in listed_mapped_rows. Hand it every SKU again ->
    SKU-2 (drafted) is read and counted -> fails."""
    from api.services import online_sync_health as osh

    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 0, "BV-B": 0}})
    _set(db, "catalog_products", {"id": "c2"}, **{"ecom.status": "DRAFT"})
    asked = []
    real = osh.live_listed_qty_for_skus

    async def spy(db_, skus, *a, **k):
        asked.append(sorted(skus))
        return await real(db_, skus, *a, **k)

    monkeypatch.setattr(osh, "live_listed_qty_for_skus", spy)
    levels = {INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 3, LOC_B: 0}}
    tally = _tally(monkeypatch, db, levels)
    page = _reconcile_page(monkeypatch, db, levels, "BV-A")
    assert asked == [["SKU-1"], ["SKU-1"]]
    assert tally["summary"]["listed_mapped_rows"] == page["listed_mapped_rows"] == 1


def test_a_spine_sku_with_a_space_is_unknown_on_the_screens_never_not_online(monkeypatch):
    """Review round 1 test gap: the reader answers stripped keys; a spine
    row stored 'SKU-2 ' is on a live listing, so the screens assess it (its
    level is keyed by the stripped SKU, so it reads unknown) -- never NOT
    ONLINE / missing. Compare the raw key with the reader's -> NOT_ONLINE
    on the view, no tally row -> fails."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 0, "BV-B": 0}})
    _set(db, "products", {"sku": "SKU-2"}, sku="SKU-2 ")
    levels = {INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 3, LOC_B: 0}}
    row = _reconcile(monkeypatch, db, levels, "BV-A")["SKU-2 "]
    assert row["status"] == "LISTED_UNKNOWN"
    tally = {r["sku"]: r for r in _tally(monkeypatch, db, levels)["items"]}
    assert "SKU-2 " in tally and tally["SKU-2 "]["online_listed_qty"] is None


def _listing_target_on_the_product_row(db):
    """Case C (review round 2): SKU-2's own size row carries NO item and
    hangs under a DRAFT listing c3, while its PUBLISHED twin c2 carries the
    item on its ecom -- the target is c2's, so the listing is c2."""
    db.seed("catalog_products", [_listing(3, "SKU-3", status="DRAFT")])
    _set(db, "catalog_variants", {"sku": "SKU-2"}, parent_product_id="c3", shopify_inventory_item_id=None)
    _set(db, "catalog_products", {"id": "c2"}, **{"ecom.shopify_inventory_item_id": INV_2})


def _listing_unplaced_by_parent(db):
    """Case A: SKU-2's size row points at the SPINE id 'p1' (made before the
    twin existed) and parent_sku SKU-1, while twin c1 carries a legacy sku --
    no parent link lands. Its own twin c2 is a size of c1."""
    _size_of_c1(db)
    _set(db, "catalog_variants", {"sku": "SKU-2"}, parent_product_id="p1", parent_sku="SKU-1")
    _set(db, "catalog_products", {"id": "c1"}, sku="SKU-1-LEGACY")
    _set(db, "products", {"sku": "SKU-1"}, pim_product_id="c1")
    _set(db, "catalog_variants", {"sku": "SKU-1"}, parent_sku="SKU-1-LEGACY")


def _listing_shadowed_by_a_barcode(db):
    """Case B: SKU-2 is a standalone listing whose item sits only on c2's
    ecom (no variant row of its own); an unrelated, unpushed size row of a
    DRAFT product c3 carries barcode 'SKU-2'."""
    db.get_collection("catalog_variants").delete_one({"sku": "SKU-2"})
    _set(db, "catalog_products", {"id": "c2"}, **{"ecom.shopify_inventory_item_id": INV_2})
    db.seed("catalog_products", [_listing(3, "SKU-3", status="DRAFT")])
    db.seed("catalog_variants", [{"sku": "SKU-3-M", "barcode": "SKU-2", "parent_product_id": "c3"}])


@pytest.mark.parametrize("case", [_listing_unplaced_by_parent, _listing_shadowed_by_a_barcode,
                                  _listing_target_on_the_product_row])
def test_the_listing_is_the_one_that_carries_the_target(monkeypatch, case):
    """Review round 1 (listings_for_skus' blind spot): the writer's target
    for SKU-2 is INV_2, but the listing reader named no listing (a parent
    link that lands nowhere) or an unrelated DRAFT listing (another
    product's barcode), so parity skipped SKU-2 and both screens called it
    not online while LOC_A sells 3 against a 0 shelf. The listing now
    follows the target's own precedence: parity files BV-A, the view says
    OVERSELL_RISK, the tally flags it. Put back 'every matched variant row
    names its parent, none falls through' -> fails; review round 2: name an
    item-less own size row's parent before the product row that carries
    the target -> case C judges SKU-2 by the DRAFT c3 -> fails."""
    from api.services import online_catalog

    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 0, "BV-B": 0}})
    case(db)
    assert online_catalog.inventory_items_for_skus(db, ["SKU-2"]) == {"SKU-2": INV_2}
    levels = {INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 3, LOC_B: 0}}
    tally, parity = _tally_and_parity(monkeypatch, db, levels)
    assert [(d["sku"], d["store_id"], d["ims"], d["shopify"]) for d in parity["drift"]] == [("SKU-2", "BV-A", 0, 3)]
    assert _reconcile(monkeypatch, db, levels, "BV-A")["SKU-2"]["status"] == "OVERSELL_RISK"
    assert tally["SKU-2"]["oversell_risk"] is True


def test_the_inventory_online_column_reads_the_one_live_reader(monkeypatch):
    """Review round 1: the Inventory screen's Online column and count read
    /catalog/online-status, whose `online` counted a draft (gid OR
    PUBLISHED). After Take off website (DRAFT, gid kept) it said Online
    while the Online Stock view said NOT_ONLINE. `online` is now the one
    live reader. Serve online_status_for_skus().online again -> SKU-2 reads
    online -> fails."""
    from api.routers import catalog

    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 0, "BV-B": 0}})
    _set(db, "catalog_products", {"id": "c2"}, **_TAKEN_DOWN)
    monkeypatch.setattr(catalog, "_get_db", lambda: db)
    body = catalog.OnlineStatusRequest(skus=["SKU-1", "SKU-2"])
    statuses = _run(catalog.post_online_status(body, current_user={"user_id": "u1"}))["statuses"]
    assert {k: v["online"] for k, v in statuses.items()} == {"SKU-1": True, "SKU-2": False}
    got = _run(catalog.get_online_status(skus="SKU-1,SKU-2", current_user={"user_id": "u1"}))["statuses"]
    assert {k: v["online"] for k, v in got.items()} == {"SKU-1": True, "SKU-2": False}
    view = _reconcile(monkeypatch, db, {INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 3, LOC_B: 0}}, "BV-A")
    assert view["SKU-2"]["status"] == "NOT_ONLINE"


def test_a_dead_products_read_on_the_reconcile_view_is_unknown_never_covered(monkeypatch):
    """Review round 2: the reconcile route swallowed a failed products read
    into 'no SKUs', and no SKU meant no live SKU, so the page said 'fully
    covered' and 'No overselling risk' beside BV-A's open task. A dead
    products read is unknown: live_listings_unknown, listed_qty_live False.
    Swallow it into [] again -> listed_qty_live True -> fails."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 0, "BV-B": 0}})
    levels = {INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 3, LOC_B: 0}}
    _run(sp.run_parity_tick(db, graphql=_shopify(levels)))
    coll = db.get_collection("products")

    def dead(*a, **k):
        raise RuntimeError("products read died")

    monkeypatch.setattr(coll, "find", dead)
    page = _reconcile_page(monkeypatch, db, levels, "BV-A")
    assert page["items"] == [] and page["live_listings_unknown"] is True
    assert page["listed_qty_live"] is False and _tasks(db)[0]["status"] == "OPEN"


def test_the_inventory_online_column_says_unknown_on_a_dead_live_read(monkeypatch):
    """Review round 2: a failed live read answered {} on /catalog/online-status,
    so the Inventory screen showed every row 'In-store only' and the card
    'none synced online' -- a confident 'not online' beside an open task.
    The statuses stay and `online` is None (the screen says Unverified).
    Answer {} or online False again -> fails."""
    from api.routers import catalog

    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 0, "BV-B": 0}})
    monkeypatch.setattr(catalog, "_get_db", lambda: db)
    _dead_live_read(db, "status")
    body = catalog.OnlineStatusRequest(skus=["SKU-1", "SKU-2"])
    statuses = _run(catalog.post_online_status(body, current_user={"user_id": "u1"}))["statuses"]
    assert {k: v["online"] for k, v in statuses.items()} == {"SKU-1": None, "SKU-2": None}


def test_no_live_listing_beside_an_unread_shelf_is_still_covered(monkeypatch):
    """Review round 2 test gap: with no live listing the tally has nothing
    to read from Shopify, even on a night a shop's shelf cannot be read (the
    on-hand-unknown early return): listed_qty_live stays True, so the page
    shows the on-hand note alone, not a second 'Shopify unavailable' one.
    Drop the early no-live-listing branch -> False -> fails."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 0, "BV-B": 0}})
    for c in ("c1", "c2"):
        _set(db, "catalog_products", {"id": c}, **{"ecom.status": "DRAFT"})
    _fail_shelf(monkeypatch, "BV-A")
    tally = _tally(monkeypatch, db, {INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 3, LOC_B: 0}})
    assert tally["summary"].get("on_hand_unknown") is True
    assert tally["summary"]["listed_qty_live"] is True


# ---------------------------------------------------------------------------
# Round 17, review round 3
# ---------------------------------------------------------------------------


def _unminted_size_of_c1(db):
    """SKU-2 is a size of live c1 that Shopify has no variant for yet: its
    size row rides c1 with no inventory item, and its twin is a gid-less
    DRAFT variant-of child."""
    _size_of_c1(db)
    _set(db, "catalog_variants", {"sku": "SKU-2"}, shopify_inventory_item_id=None)
    _set(db, "catalog_products", {"id": "c2"}, **{"ecom.shopify_product_id": None, "ecom.status": "DRAFT"})


def test_a_size_not_yet_on_shopify_is_assessed_by_no_screen(monkeypatch):
    """Review round 3: a size not yet minted on a live parent was 'live' to
    the one reader (its listing is), so the view showed it Unverified, the
    tally listed it and the Inventory column called it online, forever --
    while parity (no Shopify item to read) never compared it. The reader now
    needs the writer's target too: not on sale, assessed by nobody. Drop
    the target from the reader -> LISTED_UNKNOWN on the view -> fails."""
    from api.routers import catalog

    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 2, "BV-B": 0}})
    _unminted_size_of_c1(db)
    levels = {INV_1: {LOC_A: 1, LOC_B: 1}}
    tally, parity = _tally_and_parity(monkeypatch, db, levels)
    assert parity["drift"] == [] and parity["compared"] == 2
    assert "SKU-2" not in tally and _reconcile(monkeypatch, db, levels, "BV-A")["SKU-2"]["status"] == "NOT_ONLINE"
    monkeypatch.setattr(catalog, "_get_db", lambda: db)
    body = catalog.OnlineStatusRequest(skus=["SKU-1", "SKU-2"])
    statuses = _run(catalog.post_online_status(body, current_user={"user_id": "u1"}))["statuses"]
    assert statuses["SKU-1"]["online"] is True and statuses["SKU-2"]["online"] is False


def test_a_dead_products_read_on_the_tally_is_unknown_never_nothing_listed(monkeypatch):
    """Review round 3: the Stock Tally answered a failed products read with
    an empty, 'fully covered' tally (the reconcile route was fixed in round
    2). It is now live_listings_unknown. Swallow it again -> fails."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 0, "BV-B": 0}})

    def dead(*a, **k):
        raise RuntimeError("products read died")

    monkeypatch.setattr(db.get_collection("products"), "find", dead)
    tally = _tally(monkeypatch, db, {INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 3, LOC_B: 0}})
    assert tally["items"] == [] and tally["summary"]["live_listings_unknown"] is True
    assert tally["summary"]["listed_qty_live"] is False


def test_the_tally_reads_the_products_once(monkeypatch):
    """Review round 3: stock_tally_live read the products for the Shopify
    read and stock_tally_summary read them AGAIN for the rows, so a SKU only
    the second read saw was skipped under 'covered'. One read, handed down.
    Read them again in the summary -> two reads -> fails."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 0, "BV-B": 0}})
    coll = db.get_collection("products")
    real = coll.find
    reads = []

    def counted(flt=None, *a, **k):
        if (flt or {}).get("is_active") == {"$ne": False}:
            reads.append(1)
        return real(flt, *a, **k)

    monkeypatch.setattr(coll, "find", counted)
    tally = _tally(monkeypatch, db, {INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 3, LOC_B: 0}})
    assert len(reads) == 1 and {r["sku"] for r in tally["items"]} == {"SKU-1", "SKU-2"}


def test_the_inventory_online_column_says_unknown_when_the_catalog_read_dies(monkeypatch):
    """Review round 3: /catalog/online-status answered {} when its own
    catalogue lookup failed (only the live read answered None), so the
    Inventory screen still said 'In-store only' on the common blip. Every
    asked key is now online None. Answer {} again -> fails."""
    from api.routers import catalog

    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 0, "BV-B": 0}})
    monkeypatch.setattr(catalog, "_get_db", lambda: db)

    def dead(*a, **k):
        raise RuntimeError("catalog read died")

    for name in ("catalog_products", "catalog_variants"):
        monkeypatch.setattr(db.get_collection(name), "find", dead)
        monkeypatch.setattr(db.get_collection(name), "find_one", dead)
    body = catalog.OnlineStatusRequest(skus=["SKU-1", " SKU-2 "])
    statuses = _run(catalog.post_online_status(body, current_user={"user_id": "u1"}))["statuses"]
    assert {k: v["online"] for k, v in statuses.items()} == {"SKU-1": None, "SKU-2": None}


def test_the_close_note_never_says_a_retired_listing_left_the_website():
    """Review round 3: a retired SKU (skipped, the owner's ruling) whose
    take-down failed still sells on a PUBLISHED listing, yet the close note
    said it is 'no longer live on the website'. The note gives the same
    reasons as the description. Put the old words back -> fails."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 0, "BV-B": 0}})
    shop = _shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 3, LOC_B: 0}})
    _run(sp.run_parity_tick(db, graphql=shop))
    _retire("SKU-2", online_state="DELIST_FAILED", delist_mode="LIVE")(db)
    out = _run(sp.run_parity_tick(db, graphql=shop))
    assert out["tasks"]["closed"] == ["BV-A"]
    (task,) = _tasks(db)
    note = task["completion_notes"]
    assert "Auto-closed" in note and "no longer live on the website" not in note
    assert "IMS no longer sells it" in note


# ---------------------------------------------------------------------------
# Round 18: the review's nine items
# ---------------------------------------------------------------------------


def _levels_gql(edges_by_item):
    """A fake _graphql answering each asked id with the given raw `edges`
    (or None for a null node), whatever shape they are."""

    async def gql(db, query, variables):  # noqa: ARG001
        nodes = []
        for g in variables["ids"]:
            e = edges_by_item.get(g)
            nodes.append(None if e is None else {"id": g, "inventoryLevels": {"pageInfo": {"hasNextPage": False}, "edges": e}})
        return {"data": {"nodes": nodes}}

    return gql


def _edge(loc, quantities):
    return {"node": {"location": {"id": loc}, "quantities": quantities}}


_MALFORMED = [
    pytest.param([{"name": "available", "quantity": "abc"}], id="abc"),
    pytest.param([], id="empty-list"),
    pytest.param("available", id="non-list"),
    pytest.param(None, id="none-quantities"),
    pytest.param([{"name": "available", "quantity": None}], id="none-quantity"),
    pytest.param([{"name": "on_hand", "quantity": 3}], id="no-available-name"),
]


@pytest.mark.parametrize("quantities", _MALFORMED)
def test_r18_malformed_location_quantity_makes_the_item_unknown_never_zero(quantities):
    """Review item 2. A location edge with no parseable `available` leaves the
    WHOLE item absent (unknown). Only a location Shopify does not return is a
    valid 0. Restore the skip-and-continue parse (`pass` on ValueError, no
    edge check) -> the location is never recorded, the item reads {LOC_B: 1}
    and the SKU is 0 at the malformed shop -> fails."""
    gql = _levels_gql({INV_1: [_edge(LOC_A, quantities), _edge(LOC_B, [{"name": "available", "quantity": 1}])],
                       INV_2: [_edge(LOC_A, [{"name": "available", "quantity": 4}])]})
    out = _run(sp.shopify_levels_by_item(None, [INV_1, INV_2], graphql=gql))
    assert out == {INV_2: {LOC_A: 4}}


@pytest.mark.parametrize("quantities", _MALFORMED)
@pytest.mark.parametrize("ims", [3, 0])
def test_r18_malformed_quantity_files_no_drift_and_claims_no_clean(quantities, ims):
    """Review item 2 end to end, at IMS 3 (was a false drift 3 vs 0) and IMS 0
    (was a false clean). BV-A's SKU-1 row is UNKNOWN: no drift, not compared,
    not in clean_skus."""
    db = _db({"SKU-1": {"BV-A": ims}})
    gql = _levels_gql({INV_1: [_edge(LOC_A, quantities)], INV_2: [_edge(LOC_A, [{"name": "available", "quantity": 0}])]})
    out = _run(sp.run_parity_tick(db, graphql=gql))
    bva = next(s for s in out["stores"] if s["store_id"] == "BV-A")
    assert out["drift_count"] == 0 and _tasks(db) == []
    assert bva["compared"] == 1 and bva["unknown"] == 1  # SKU-2 compared, SKU-1 unknown


def test_r18_a_malformed_node_costs_only_itself():
    """Review item 6. A location that is a string raises inside the parse; the
    other items of the batch stand (no 'tick error' for the whole night).
    Drop the per-node try/except -> the call returns None -> fails."""
    bad = {"node": {"location": "gid://shopify/Location/1001", "quantities": [{"name": "available", "quantity": 1}]}}
    gql = _levels_gql({INV_1: [bad], INV_2: [_edge(LOC_A, [{"name": "available", "quantity": 4}])]})
    assert _run(sp.shopify_levels_by_item(None, [INV_1, INV_2], graphql=gql)) == {INV_2: {LOC_A: 4}}
    db = _db({"SKU-1": {"BV-A": 1}})
    out = _run(sp.run_parity_tick(db, graphql=gql))
    assert out["checked"] is True and out["compared"] > 0


def test_r18_the_same_location_twice_is_unknown_not_summed():
    """Review item 9. Two edges for LOC_A with 3 each used to sum to 6 (a false
    drift). Restore `per_location.get(loc, 0) + ...` -> {LOC_A: 6} -> fails."""
    q = [{"name": "available", "quantity": 3}]
    gql = _levels_gql({INV_1: [_edge(LOC_A, q), _edge(LOC_A, q)], INV_2: [_edge(LOC_A, q)]})
    assert _run(sp.shopify_levels_by_item(None, [INV_1, INV_2], graphql=gql)) == {INV_2: {LOC_A: 3}}


def test_r18_an_item_shopify_answers_null_is_missing_and_closes_its_task():
    """Review item 7. Night 1 names SKU-2 (IMS 5 vs Shopify 0 at BV-A); night 2
    Shopify answers INV_2 null (deleted in admin): it lands in
    missing_on_shopify and the task closes. Delete `out[gid] = None` for a
    null node -> the item reads unknown, SKU-2 stays owed, the task stays
    OPEN -> fails."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 5, "BV-B": 0}})
    _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 0, LOC_B: 0}})))
    assert _tasks(db)[0]["payload"]["skus"] == ["SKU-2"]
    out = _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 1, LOC_B: 1}})))
    assert out["missing_on_shopify"] == ["SKU-2"]
    assert out["tasks"]["closed"] == ["BV-A"] and _tasks(db)[0]["status"] == "COMPLETED"


def test_r18_the_task_names_each_shop_as_the_picker_does():
    """Review items 4 and 8. The picker shows store_name; the task says "Better
    Vision Bokaro (BV-BKR)" (name AND code) in the title and every mention,
    and points at the sidebar entry. Back to `store_code or store_name` ->
    "BV-BKR" matches no picker option -> fails."""
    db = _db({"SKU-1": {"BV-A": 5, "BV-B": 1}})
    for st in db.get_collection("stores").docs:
        if st["store_id"] == "BV-A":
            st["store_code"], st["store_name"] = "BV-BKR", "Better Vision Bokaro"
    _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {}})))
    (task,) = _tasks(db)
    assert task["title"] == "Shopify stock drift at Better Vision Bokaro (BV-BKR)"
    assert "pick Better Vision Bokaro (BV-BKR)," in task["description"]
    assert "Online Stock (sidebar, Stock & supply" in task["description"]
    assert task["store_id"] == "BV-A"


@pytest.mark.parametrize(
    "store,label",
    [
        ({"store_id": "S1", "store_name": "Bokaro", "store_code": "BV-BKR"}, "Bokaro (BV-BKR)"),
        ({"store_id": "S1", "store_name": "BV-BKR", "store_code": "BV-BKR"}, "BV-BKR"),
        ({"store_id": "S1", "store_code": "BV-BKR"}, "BV-BKR"),
        ({"store_id": "S1", "store_name": "Bokaro"}, "Bokaro"),
        ({"store_id": "S1"}, "S1"),
    ],
)
def test_r18_shop_label_matches_the_picker(store, label):
    assert sp.shop_label(store) == label


def _shared_db():
    """SKU-1 and SKU-2 both carry INV_1 (two IMS products on one Shopify
    item), SKU-3 has its own INV_3 and drifts at BV-A."""
    db = _db({"SKU-1": {"BV-A": 3}, "SKU-2": {"BV-A": 0}})
    db.get_collection("catalog_variants").docs[1]["shopify_inventory_item_id"] = INV_1
    db.get_collection("products").insert_one({"product_id": "p3", "sku": "SKU-3"})
    db.get_collection("catalog_products").insert_one(_listing(3, "SKU-3"))
    db.get_collection("catalog_variants").insert_one(
        {"sku": "SKU-3", "parent_product_id": "c3", "shopify_inventory_item_id": INV_3})
    db.get_collection("stock_units").insert_many(
        [{"stock_id": f"S3-{i}", "product_id": "p3", "store_id": "BV-A", "status": "AVAILABLE"} for i in range(6)])
    return db


INV_3 = "gid://shopify/InventoryItem/93"


def test_r18_skus_sharing_one_shopify_item_are_in_no_view_and_named_apart(monkeypatch):
    """Review item 1. The writer sends NEITHER of two SKUs on one Shopify item
    (_duplicates_or_error), so parity, the reconcile view and the Stock Tally
    skip both through the one live reader, and the shop's task names them
    separately ("fix in IMS"), never as drift to set in Shopify. Drop the
    duplicate guard from live_listing_split -> SKU-1 and SKU-2 are compared
    (SKU-2: IMS 0 vs Shopify 3) -> fails."""
    from api.services import online_sync_health as osh
    from api.services.shopify_push.inventory import live_listing_split, skus_on_live_listings

    db = _shared_db()
    live, shared = live_listing_split(db, ["SKU-1", "SKU-2", "SKU-3"], strict=True)
    assert live == {"SKU-3"} and shared == {"SKU-1", "SKU-2"}
    assert skus_on_live_listings(db, ["SKU-1", "SKU-2", "SKU-3"], strict=True) == {"SKU-3"}
    levels = {INV_1: {LOC_A: 3, LOC_B: 0}, INV_3: {LOC_A: 1, LOC_B: 0}, INV_2: {}}
    out = _run(sp.run_parity_tick(db, graphql=_shopify(levels)))
    assert [d["sku"] for d in out["drift"]] == ["SKU-3"]
    assert out["shared_item_skus"] == ["SKU-1", "SKU-2"]
    (task,) = _tasks(db)
    assert "Two IMS products share one Shopify item - fix in IMS" in task["description"]
    assert "SKU-1, SKU-2" in task["description"]
    assert task["payload"]["skus"] == ["SKU-3"]
    # The other two readers skip them as well.
    monkeypatch.setattr("api.services.shopify_push._graphql", _shopify(levels))
    tally = _run(osh.stock_tally_live(db))
    assert {r["sku"] for r in tally["items"]} == {"SKU-3"}
    page = _reconcile_page(monkeypatch, db, levels, "BV-A")
    assert {r["sku"] for r in page["items"] if r["status"] != "NOT_ONLINE"} == {"SKU-3"}


def test_r18_an_unreadable_claim_guard_is_unknown_never_nothing_shared(monkeypatch):
    """Review item 1: the claim read raising is UNKNOWN. The strict reader
    raises (parity: nothing compared, no task touched); fail-soft gives
    nothing live. Swallow the claim error as {} -> SKU-1 and SKU-2 read live
    -> fails."""
    from api.services import online_catalog
    from api.services.shopify_push.inventory import skus_on_live_listings

    db = _shared_db()

    def boom(db_, gids):
        raise RuntimeError("claim read died")

    monkeypatch.setattr(online_catalog, "skus_claiming_inventory_items", boom)
    with pytest.raises(Exception):
        skus_on_live_listings(db, ["SKU-3"], strict=True)
    assert skus_on_live_listings(db, ["SKU-3"]) == set()
    out = _run(sp.run_parity_tick(db, graphql=_shopify({})))
    assert out["checked"] is False and "catalog read failed" in out["reason"]
