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

StrictDB + injected Shopify boundary -- no network, no production.
"""

import asyncio
import os
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


def _shopify(levels, *, fail=False):
    """A fake _graphql answering the levels query from {item_gid: {loc: qty}}."""
    calls = []

    async def gql(db, query, variables):  # noqa: ARG001
        calls.append(variables)
        if fail:
            raise RuntimeError("throttled")
        return {
            "data": {
                "nodes": [
                    {
                        "id": gid,
                        "inventoryLevels": {
                            "edges": [
                                {"node": {"location": {"id": loc}, "quantities": [{"name": "available", "quantity": q}]}}
                                for loc, q in per.items()
                            ]
                        },
                    }
                    for gid, per in levels.items()
                    if gid in variables["ids"]
                ]
            }
        }

    gql.calls = calls
    return gql


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


def test_levels_reader_is_none_when_any_batch_fails(monkeypatch):
    """Half an answer is no answer. Revert the failed-batch `return None` to
    `continue` -> the reader returns the other batch -> this fails (and the
    tick would close a drifted shop's task on the half it did read)."""
    monkeypatch.setattr(sp, "_INV_BATCH", 1)
    seen = []

    async def gql(db, query, variables):  # noqa: ARG001
        seen.append(variables["ids"])
        if len(seen) == 2:
            raise RuntimeError("throttled")
        return {"data": {"nodes": []}}

    assert _run(sp.shopify_levels_by_item(None, [INV_1, INV_2], graphql=gql)) is None
    assert _run(sp.shopify_levels_by_item(None, [INV_1], graphql=_shopify({}))) == {}


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


def test_tick_a_partial_shopify_read_closes_nothing(monkeypatch):
    """BV-A's open task is about SKU-2. Tonight SKU-1's batch reads clean and
    SKU-2's batch fails. Half an answer is no answer: nothing is compared and
    the task stays OPEN. Revert the reader's failed-batch `return None` to
    `continue` -> BV-A compares 1 clean row (SKU-2 unknown) -> its task is
    closed on a read that skipped the drifted SKU -> this fails."""
    monkeypatch.setattr(sp, "_INV_BATCH", 1)
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 5, "BV-B": 0}})
    db.seed("tasks", [{"task_id": "T-1", "source_ref": "shopify-stock-parity-drift:BV-A", "status": "OPEN"}])
    clean = _shopify({INV_1: {LOC_A: 1, LOC_B: 1}})

    async def gql(db_, query, variables):
        if INV_2 in variables["ids"]:
            raise RuntimeError("throttled")
        return await clean(db_, query, variables)

    out = _run(sp.run_parity_tick(db, graphql=gql))
    assert out["checked"] is False and "read failed" in out["reason"]
    assert out["tasks"]["closed"] == []
    assert _tasks(db)[0]["status"] == "OPEN"


def test_levels_reader_top_level_errors_beside_nodes_is_a_failed_read():
    """Shopify can answer a list of nodes WITH top-level `errors` (a node it
    failed to resolve comes back null). Half an answer is no answer. Drop the
    `or body.get("errors")` -> the reader returns INV_1 alone -> this fails."""

    async def gql(db, query, variables):  # noqa: ARG001
        return {
            "data": {"nodes": [{"id": INV_1, "inventoryLevels": {"edges": []}}, None]},
            "errors": [{"message": "Internal error", "path": ["nodes", 1]}],
        }

    assert _run(sp.shopify_levels_by_item(None, [INV_1, INV_2], graphql=gql)) is None


def _partial(levels, *, errors=False):
    """INV_1 answered from `levels`, INV_2 answered null (a deleted item, or a
    per-node failure when `errors`)."""
    clean = _shopify(levels)

    async def gql(db_, query, variables):
        body = await clean(db_, query, variables)
        body["data"]["nodes"].append(None)
        if errors:
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
    out = _run(sp.run_parity_tick(db, graphql=_partial({INV_1: {LOC_A: 1, LOC_B: 1}}, errors=True)))
    assert out["checked"] is False and out["tasks"]["closed"] == []
    assert _tasks(db)[0]["status"] == "OPEN"


def test_tick_a_drifted_sku_that_was_not_re_read_keeps_its_task_open():
    """Night 1: SKU-2 drifts at BV-A. Night 2: a CLEAN answer (no errors) but
    INV_2 comes back null (deleted item), SKU-1 compares clean. The task names
    SKU-2, SKU-2 was never re-read -> the task stays OPEN; night 3 re-reads
    SKU-2 clean -> closed. Revert the close gate to `if active and
    summary.get("compared"):` (drop `not owed`) -> night 2 closes it on a read
    that skipped the drifted SKU -> this fails."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 5, "BV-B": 0}})
    _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 0, LOC_B: 0}})))
    out = _run(sp.run_parity_tick(db, graphql=_partial({INV_1: {LOC_A: 1, LOC_B: 1}})))
    bva = next(s for s in out["stores"] if s["store_id"] == "BV-A")
    assert out["checked"] is True and bva["compared"] == 1 and bva["unknown"] == 1
    assert out["tasks"]["closed"] == [] and _tasks(db)[0]["status"] == "OPEN"
    out = _run(sp.run_parity_tick(db, graphql=_shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 5, LOC_B: 0}})))
    assert out["tasks"]["closed"] == ["BV-A"] and _tasks(db)[0]["status"] == "COMPLETED"


def test_tick_a_drifted_sku_whose_ims_side_went_unknown_keeps_its_task_open():
    """Same gate, IMS side: SKU-2 drifted, then its spine row is gone (IMS
    unknown) while SKU-1 compares clean -> still owed, still OPEN."""
    db = _db({"SKU-1": {"BV-A": 1, "BV-B": 1}, "SKU-2": {"BV-A": 5, "BV-B": 0}})
    shop = _shopify({INV_1: {LOC_A: 1, LOC_B: 1}, INV_2: {LOC_A: 0, LOC_B: 0}})
    _run(sp.run_parity_tick(db, graphql=shop))
    db.get_collection("products").delete_many({"sku": "SKU-2"})
    out = _run(sp.run_parity_tick(db, graphql=shop))
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
