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
    the drift task names the press that re-sends (Send to website) and who
    can press it, and keeps every SKU still owed across a refresh; a tasks
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
    text names every SKU that keeps it open, never calls a SKU compared
    tonight 'not compared', and asks a SUPERADMIN-blocked SKU for 0 in
    Shopify admin, never the button.
  * round 8: a product IMS stopped selling has left the catalogue only once
    its take-down REACHED Shopify (online_catalog._delisted_live); a failed
    or dark take-down, a retired size or a twin-less SKU is still compared
    against the writer's 0 and gets its own line; a node that is not the
    item asked for is unknown; a store_id stored with a space still gets its
    task; an owed SKU keeps the numbers it last drifted with and the press
    that clears it (never 'nothing to press').

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


def _db(shelves, *, pune_units=0):
    """Two MAPPED shops (BV-A at LOC_A, BV-B at LOC_B), an UNMAPPED BV-PUN
    holding `pune_units` of SKU-1, and the ONLINE store. `shelves` is
    {sku: {store_id: available units}}; SKU-1 -> INV_1, SKU-2 -> INV_2."""
    db = StrictDB()
    db.seed(
        "stores",
        [_store("BV-A", LOC_A), _store("BV-B", LOC_B), _store("BV-PUN"), _store("BV-ONLINE-01", store_type="ONLINE")],
    )
    db.seed("products", [{"product_id": "p1", "sku": "SKU-1"}, {"product_id": "p2", "sku": "SKU-2"}])
    db.seed(
        "catalog_variants",
        [{"sku": "SKU-1", "shopify_inventory_item_id": INV_1}, {"sku": "SKU-2", "shopify_inventory_item_id": INV_2}],
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
    from api.services import online_catalog, online_sync_health as osh

    monkeypatch.setattr(online_catalog, "online_status_for_skus", lambda db, skus: {s: {"online": True} for s in skus})
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
    monkeypatch.setattr(catalog, "online_status_for_skus", lambda db, skus: {s: {"online": True} for s in skus})
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
    db.seed("catalog_variants", [{"sku": "SKU-9", "shopify_inventory_item_id": inv_9}])
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
    db.seed("catalog_products", [{"id": "c1", "sku": "SKU-1", "ecom": {"shopify_inventory_item_id": INV_1}}])
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


def test_drift_task_names_the_press_that_re_sends_the_numbers():
    """The 01:00 / 09:00 pass and Push stock (sync_stock_levels) send only
    products whose IMS number CHANGED since the recorded baseline
    (test_shopify_online_stock.py::test_T6), so neither undoes a hand edit on
    Shopify; Send to website (push_product -> sync_product_stock) always
    re-sends. The task must say so. Put back 'The next stock push (01:00 /
    09:00 IST, or Push stock) re-sends IMS's numbers.' -> fails."""
    db = _db({"SKU-1": {"BV-A": 5, "BV-B": 1}})
    _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {}})))
    (task,) = _tasks(db)
    assert "press Send to website" in task["description"]
    assert "re-send only numbers IMS changed" in task["description"]
    # Round 4 P7: the task carries the shop's store_id, so its first owner is
    # that shop's STORE_MANAGER (task_escalation), and Send to website is
    # SUPERADMIN / ADMIN only (CatalogProductDrawer.tsx `canPush`). The text
    # names what the store manager CAN do: check the shelf, ask an admin.
    # Put back 'open each product named here and press Send to website' as
    # the whole instruction -> fails.
    assert task["store_id"] == "BV-A"
    assert "Store manager: check each of these products on the shelf" in task["description"]
    assert "ask an ADMIN or SUPERADMIN" in task["description"]


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
    # on night 1 and the press that clears it.
    assert "SKU-2 (IMS 5 vs Shopify 0 when last compared)" in task["description"]
    assert "press Send to website" in task["description"]
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
def test_tick_an_owed_sku_keeps_its_numbers_and_its_press(one_id_per_batch, monkeypatch, how):
    """Round 8, the panel's probes. Night 1: SKU-2, BV-A shelf 0 vs LOC_A 5
    (an oversell) -> 'SKU-2 (IMS 0 vs Shopify 5) ... press Send to website'.
    Night 2: SKU-2 is not compared (its batch throttled, or the online block
    unreadable) while Shopify still lists 5. The drift is still real and
    nothing re-sends it by itself, so the refreshed task still names SKU-2
    with its last numbers and the press, never 'Nothing to press', and
    payload.last_seen keeps them. Put back the round-7 owed line (drift
    rows only on the press lines, payload without last_seen) -> fails."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 0, "BV-B": 0}})
    _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 5, LOC_B: 0}})))
    assert "SKU-2 (IMS 0 vs Shopify 5)" in _tasks(db)[0]["description"]
    out = _run(sp.run_parity_tick(db, graphql=_night_two_unread(how, monkeypatch)))
    assert out["checked"] is True and out["tasks"]["refreshed"] == ["BV-A"]
    (task,) = _tasks(db)
    text = task["description"]
    assert task["status"] == "OPEN" and task["payload"]["skus"] == ["SKU-2"]
    assert "SKU-2 (IMS 0 vs Shopify 5 when last compared)" in text
    assert "press Send to website" in text and "Nothing to press" not in text
    assert task["payload"]["last_seen"] == {"SKU-2": {"ims": 0, "shopify": 5}}


def test_tick_an_owed_blocked_sku_keeps_the_shopify_admin_line(one_id_per_batch):
    """Round 8: an owed SKU sits on the line its drift would. SKU-1 is
    SUPERADMIN-blocked and drifts at BV-A (IMS 0 vs Shopify 5) on night 1;
    on night 2 its Shopify batch fails. It is still owed, and still asks a
    SUPERADMIN for 0 in Shopify admin, never the button. Compute the block
    over tonight's drift only -> SKU-1 lands on the Send to website line ->
    fails."""
    db = _db({"SKU-1": {"BV-A": 5, "BV-B": 4}})
    db.seed("ecom_collections", [{"collection_id": "C-BAN", "collection_type": "CUSTOM",
                                  "online_sync_blocked": True, "products": [{"sku": "SKU-1"}]}])
    shop = _shopify({INV_1: {LOC_A: 5, LOC_B: 0}, INV_2: {LOC_A: 0, LOC_B: 0}})
    _run(sp.run_parity_tick(db, graphql=shop))
    out = _run(sp.run_parity_tick(db, graphql=_skipping({INV_2: {LOC_A: 0, LOC_B: 0}}, skip=INV_1)))
    assert out["tasks"]["refreshed"] == ["BV-A"]
    text = _tasks(db)[0]["description"]
    assert "Blocked from online sale by a SUPERADMIN: SKU-1 (IMS 0 vs Shopify 5 when last compared)" in text
    assert "Send to website to re-send" not in text


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
    """Round 7 P5, the panel's probe: 7 SKUs drift at BV-A (IMS 5 vs Shopify
    0 each). payload.skus holds all 7 and all 7 keep the task open, so the
    text names all 7 -- the top five with their numbers, the rest by SKU.
    Name only drift[:5] -> SKU-6 and SKU-7 appear nowhere -> fails."""
    skus = [f"SKU-{n}" for n in range(1, 8)]
    inv = {s: f"gid://shopify/InventoryItem/{90 + n}" for n, s in enumerate(skus, 1)}
    db = StrictDB()
    db.seed("stores", [_store("BV-A", LOC_A), _store("BV-B", LOC_B)])
    db.seed("products", [{"product_id": "p" + s, "sku": s} for s in skus])
    db.seed("catalog_variants", [{"sku": s, "shopify_inventory_item_id": inv[s]} for s in skus])
    db.seed("stock_units", [{"stock_id": f"{s}-{i}", "product_id": "p" + s, "store_id": "BV-A",
                             "status": "AVAILABLE"} for s in skus for i in range(5)])
    _run(sp.run_parity_tick(db, graphql=_shopify({inv[s]: {LOC_A: 0, LOC_B: 0} for s in skus})))
    (task,) = _tasks(db)
    assert task["payload"]["skus"] == skus
    assert [s for s in skus if s not in task["description"]] == []
    assert "; also SKU-6, SKU-7." in task["description"]


def _twin(ecom):
    """SKU-2's catalog_products twin with this ecom (a live listing's gid)."""
    return {"id": "c2", "sku": "SKU-2", "ecom": {"shopify_product_id": "gid://shopify/Product/2", **ecom}}


@pytest.mark.parametrize("how", ["deleted_in_shopify_admin", "taken_off_the_website"])
def test_tick_a_sku_that_left_the_online_catalogue_stops_keeping_its_task_open(how):
    """Round 7 P6, narrowed in round 8. Night 1: SKU-2 drifts at BV-A (IMS 5
    vs Shopify 0), the task names it. Then EITHER Shopify answers INV_2 null
    in a full answer (deleted in Shopify admin: nothing can compare it or
    re-send it) OR IMS's Delete button soft-deletes SKU-2 AND its take-down
    REACHED Shopify (twin DELISTED by a LIVE delist: the listing is a draft,
    nothing sells it). Night 2 compares SKU-1 clean and CLOSES the task; the
    deleted item is reported under missing_on_shopify. Drop `- set(gone)` ->
    the null SKU is owed, OPEN for ever; drop the `drafted` exclusion from
    _sample_variants -> IMS 0 vs Shopify 5 on a draft, refreshed for ever ->
    fails."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 5, "BV-B": 0}})
    _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 0, LOC_B: 0}})))
    assert _tasks(db)[0]["payload"]["skus"] == ["SKU-2"]
    if how == "deleted_in_shopify_admin":
        shop = _shopify({INV_1: {LOC_A: 1, LOC_B: 1}})
    else:
        db.get_collection("products").update_one({"sku": "SKU-2"}, {"$set": {"is_active": False}})
        db.seed("catalog_products", [_twin({"online_state": "DELISTED", "delist_mode": "LIVE"})])
        shop = _shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 5, LOC_B: 0}})
    out = _run(sp.run_parity_tick(db, graphql=shop))
    assert out["checked"] is True
    assert out["tasks"]["closed"] == ["BV-A"] and _tasks(db)[0]["status"] == "COMPLETED"
    assert out["missing_on_shopify"] == (["SKU-2"] if how == "deleted_in_shopify_admin" else [])


@pytest.mark.parametrize("how", ["take_down_failed", "take_down_dark", "retired_size", "no_twin"])
def test_tick_a_retired_sku_still_on_sale_on_shopify_stays_compared(how):
    """Round 8, the panel's probes. SKU-2 is retired in IMS (is_active
    False), so the writer lists it at 0 at every shop -- but its listing is
    NOT proven off Shopify: the take-down FAILED (DELIST_FAILED, the product
    still ACTIVE), ran DARK (a SIMULATED no-op), SKU-2 is a SIZE of SKU-1
    (its take-down is DENY + 0 on the parent's ACTIVE listing, stamped
    DELISTED/LIVE all the same) or it has no twin at all. Shopify still
    sells 3 at LOC_A (a hand edit, a dropped 0). Night 1 files BV-A's task
    (IMS 0 vs Shopify 3) on the retired line; night 2 is the same drift, so
    the task is REFRESHED, never auto-closed as 'left the online catalogue'.
    Put back the plain `is_active: {$ne: False}` sample filter -> sampled 1,
    drift 0, the task closed on night 2 -> fails; drop `not is_variant_of`
    -> the retired size is dropped -> fails."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 0, "BV-B": 0}})
    db.get_collection("products").update_one({"sku": "SKU-2"}, {"$set": {"is_active": False}})
    ecom = {
        "take_down_failed": {"online_state": "DELIST_FAILED", "delist_mode": "LIVE"},
        "take_down_dark": {"online_state": "DELISTED", "delist_mode": "SIMULATED"},
        "retired_size": {"online_state": "DELISTED", "delist_mode": "LIVE",
                         "variant_of": {"product_id": "p1", "twin_id": "c1", "sku": "SKU-1"}},
    }.get(how)
    if ecom is not None:
        db.seed("catalog_products", [_twin(ecom)])
    shop = {INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 3, LOC_B: 0}}
    out = _run(sp.run_parity_tick(db, graphql=_shopify(shop)))
    assert out["sampled"] == 2 and out["drift_count"] == 1 and out["tasks"]["filed"] == ["BV-A"]
    out = _run(sp.run_parity_tick(db, graphql=_shopify(shop)))
    assert out["tasks"] == {"filed": [], "refreshed": ["BV-A"], "closed": []}
    (task,) = _tasks(db)
    text = task["description"]
    assert task["status"] == "OPEN" and task["payload"]["skus"] == ["SKU-2"]
    assert "Deleted or deactivated in IMS but still listed at BV-A's Shopify location: SKU-2 (IMS 0 vs Shopify 3)" in text
    assert "press Take off website" in text and "on the shelf" not in text


def test_tick_a_blocked_sku_that_drifts_asks_for_shopify_admin_never_the_button():
    """Round 7 P7, the panel's input: SKU-1 is SUPERADMIN-blocked, shelves
    BV-A 5 / BV-B 4, Shopify LOC_A 5 (a hand edit, or the block's 0 never
    landed). IMS sends 0 -> drift at BV-A. push_product refuses a blocked
    product and the stock pass re-sends only numbers IMS changed, so the
    text sends nobody to the shelf or to Send to website for it: SKU-1 is on
    the blocked line, which asks a SUPERADMIN for 0 in Shopify admin (or
    lifting the block). Drop the blocked split -> 'press Send to website to
    re-send' and 'on the shelf' -> fails."""
    db = _db({"SKU-1": {"BV-A": 5, "BV-B": 4}})
    db.seed("ecom_collections", [{"collection_id": "C-BAN", "collection_type": "CUSTOM",
                                  "online_sync_blocked": True, "products": [{"sku": "SKU-1"}]}])
    _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 5, LOC_B: 0}, INV_2: {LOC_A: 0, LOC_B: 0}})))
    (task,) = _tasks(db)
    text = task["description"]
    assert "Blocked from online sale by a SUPERADMIN: SKU-1 (IMS 0 vs Shopify 5)" in text
    assert "to 0 at BV-A's location in Shopify admin" in text and "lift the block" in text
    assert "Send to website to re-send" not in text and "on the shelf" not in text


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
