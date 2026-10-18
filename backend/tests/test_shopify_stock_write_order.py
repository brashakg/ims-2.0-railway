"""Audit SF01: an OLDER stock count can never overwrite a NEWER one on Shopify.

The writer sends ABSOLUTE quantities (ignoreCompareQuantity), read at one
moment and sent later across awaits. Before the write-order guard, a held
older ``available=1`` that landed after a newer ``available=0`` won: Shopify
showed stock that had already been sold. Every test here drives the ONE
writer (``set_inventory_quantities``) with a fake Shopify that remembers what
it was last told.

The first test is the audit's own probe (shopify_audit_probes.py), kept as a
permanent regression: it FAILS on the pre-fix writer and passes with the
guard.
"""

import asyncio
import os
import sys
import threading
import time

import httpx
import mongomock
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from strict_fakes import StrictDB  # noqa: E402
from api.services.shopify_push import inventory  # noqa: E402

ITEM = "gid://shopify/InventoryItem/1"
LOC = "gid://shopify/Location/2"
OK = {"data": {"inventorySetQuantities": {"inventoryAdjustmentGroup": {}, "userErrors": []}}}


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch):
    # raising=False: the probe test must also RUN (and fail) on the pre-fix writer.
    monkeypatch.setattr(inventory, "_POLL_S", 0.01, raising=False)


class _Shopify:
    """A fake Shopify: remembers the available quantity per (item, location)
    and the order of every set it applied. ``hold`` = a quantity whose send is
    held mid-flight until ``release`` is set."""

    def __init__(self, start=1, hold=None):
        self.available = {(ITEM, LOC): start}
        self.applied = []
        self.hold = hold
        self.held = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, db, query, variables):  # noqa: ARG002
        if "inventorySetQuantities" in query:
            rows = variables["input"]["quantities"]
            if self.hold is not None and rows[0]["quantity"] == self.hold:
                self.held.set()
                await self.release.wait()
            for r in rows:
                self.available[(r["inventoryItemId"], r["locationId"])] = r["quantity"]
                self.applied.append(r["quantity"])
            return OK
        if "imsInventoryLevelsRead" in query:
            loc = variables["locationId"]
            return {
                "data": {
                    "nodes": [
                        {
                            "id": i,
                            "inventoryLevel": {
                                "quantities": [{"name": "available", "quantity": self.available.get((i, loc))}]
                            },
                        }
                        for i in variables["ids"]
                    ]
                }
            }
        raise AssertionError(f"unexpected Shopify call: {query[:60]}")


def test_audit_probe_older_held_write_can_no_longer_restore_a_sold_unit(monkeypatch):
    """THE AUDIT PROBE. Hold an old available=1 send mid-flight; a newer
    available=0 write starts; release the old one. Pre-fix: the newer write
    went straight out, the old one landed after it and Shopify ended at 1
    (a unit already sold). Now the newer write waits for the key and lands
    LAST: Shopify ends at 0."""
    async def scenario():
        db = StrictDB()
        shop = _Shopify(start=1, hold=1)
        monkeypatch.setattr(inventory, "_graphql", shop)
        stale = asyncio.create_task(inventory.set_inventory_quantities(db, [(ITEM, LOC, 1)]))
        await shop.held.wait()
        sold = asyncio.create_task(inventory.set_inventory_quantities(db, [(ITEM, LOC, 0)]))
        await asyncio.sleep(0.1)  # the newer write is under way while the old send is in flight
        shop.release.set()
        old, new = await stale, await sold
        assert old["set"] == new["set"] == 1 and old["errors"] == new["errors"] == []
        assert shop.applied == [1, 0], "the newer count lands last"
        assert shop.available[(ITEM, LOC)] == 0, "an older count restored stock already sold"

    asyncio.run(scenario())


def test_newer_reading_sent_first_drops_the_older_one_unsent(monkeypatch):
    """The other ordering: the OLDER reading (lower ticket) only reaches the
    writer after the NEWER one has already landed. It is dropped -- not sent,
    not counted written, not an error."""
    db = StrictDB()
    shop = _Shopify(start=1)
    monkeypatch.setattr(inventory, "_graphql", shop)
    older, newer = inventory.stock_write_ticket(db), inventory.stock_write_ticket(db)
    assert newer > older
    new = asyncio.run(inventory.set_inventory_quantities(db, [(ITEM, LOC, 0)], ticket=newer))
    old = asyncio.run(inventory.set_inventory_quantities(db, [(ITEM, LOC, 1)], ticket=older))
    assert new["set"] == 1 and new["written"] == [(ITEM, LOC, 0)]
    assert old == {"set": 0, "written": [], "activated": 0, "errors": [], "code": None}
    assert shop.applied == [0] and shop.available[(ITEM, LOC)] == 0


def test_older_reading_sent_first_then_newer_both_land_in_order(monkeypatch):
    db = StrictDB()
    shop = _Shopify(start=2)
    monkeypatch.setattr(inventory, "_graphql", shop)
    older, newer = inventory.stock_write_ticket(db), inventory.stock_write_ticket(db)
    asyncio.run(inventory.set_inventory_quantities(db, [(ITEM, LOC, 1)], ticket=older))
    asyncio.run(inventory.set_inventory_quantities(db, [(ITEM, LOC, 0)], ticket=newer))
    assert shop.applied == [1, 0] and shop.available[(ITEM, LOC)] == 0


def test_a_busy_key_is_retried_and_a_key_claimed_meanwhile_by_a_newer_reading_is_dropped(monkeypatch):
    """Guard mismatch -> retry. The older write's first send is refused with
    ITEM_NOT_STOCKED_AT_LOCATION; while it activates the item, a NEWER reading
    claims the key and lands 0. The older write's retry then finds its key
    taken by a newer reading and sends nothing -- the retry never resends the
    old number."""
    db = StrictDB()
    sent = []
    older, newer = inventory.stock_write_ticket(db), inventory.stock_write_ticket(db)
    calls = {"set": 0}

    async def fake(db_, query, variables):
        if "inventorySetQuantities" in query:
            calls["set"] += 1
            q = variables["input"]["quantities"][0]["quantity"]
            if calls["set"] == 1:
                return {"data": {"inventorySetQuantities": {"userErrors": [
                    {"field": ["input"], "message": "not stocked", "code": "ITEM_NOT_STOCKED_AT_LOCATION"}]}}}
            sent.append(q)
            return OK
        if "inventoryBulkToggleActivation" in query:
            # the newer reading lands while the older one is activating
            out = await inventory.set_inventory_quantities(db_, [(ITEM, LOC, 0)], ticket=newer)
            assert out["set"] == 1
            return {"data": {"inventoryBulkToggleActivation": {"inventoryItem": {"id": ITEM}, "userErrors": []}}}
        raise AssertionError(query[:60])

    monkeypatch.setattr(inventory, "_graphql", fake)
    old = asyncio.run(inventory.set_inventory_quantities(db, [(ITEM, LOC, 1)], ticket=older))
    assert sent == [0], "the older count was never re-sent over the newer one"
    assert old["set"] == 0 and old["written"] == [] and old["errors"] == []


def test_multi_worker_two_processes_sharing_only_mongo(monkeypatch):
    """Two WORKERS -- separate threads, separate event loops, nothing shared
    but the database (mongomock enforces the unique _id real Mongo does). The
    older worker's send is held in flight; the newer worker must not send
    until it has landed, and lands last."""
    db = mongomock.MongoClient().db
    applied = []
    lock = threading.Lock()
    held, release = threading.Event(), threading.Event()

    async def fake(db_, query, variables):  # noqa: ARG001
        q = variables["input"]["quantities"][0]["quantity"]
        if q == 1:
            held.set()
            release.wait(5)
        with lock:
            applied.append(q)
        return OK

    monkeypatch.setattr(inventory, "_graphql", fake)
    older, newer = inventory.stock_write_ticket(db), inventory.stock_write_ticket(db)
    results = {}

    def worker(name, qty, ticket):
        results[name] = asyncio.run(inventory.set_inventory_quantities(db, [(ITEM, LOC, qty)], ticket=ticket))

    a = threading.Thread(target=worker, args=("old", 1, older))
    a.start()
    assert held.wait(5)
    b = threading.Thread(target=worker, args=("new", 0, newer))
    b.start()
    time.sleep(0.3)
    assert applied == [], "the newer worker must not send while the older send is in flight"
    release.set()
    a.join(10)
    b.join(10)
    assert applied == [1, 0]
    assert results["old"]["set"] == results["new"]["set"] == 1

    # ...and the other ordering across workers: the older worker arrives
    # after the newer one has landed, and sends nothing.
    applied.clear()
    release.set()
    t_old, t_new = inventory.stock_write_ticket(db), inventory.stock_write_ticket(db)
    worker("new2", 0, t_new)
    th = threading.Thread(target=worker, args=("old2", 1, t_old))
    th.start()
    th.join(10)
    assert applied == [0] and results["old2"]["set"] == 0 and results["old2"]["errors"] == []


def test_timeout_after_send_that_did_land_is_confirmed_by_rereading(monkeypatch):
    """Shopify applied the write, then the reply was lost (read timeout). The
    writer neither assumes failure nor success: it re-reads, sees exactly
    what it sent, and counts the row written -- the key is free again."""
    db = StrictDB()
    shop = _Shopify(start=3)

    async def fake(db_, query, variables):
        body = await shop(db_, query, variables)
        if "inventorySetQuantities" in query and len(shop.applied) == 1:
            raise httpx.ReadTimeout("reply lost after commit")
        return body

    monkeypatch.setattr(inventory, "_graphql", fake)
    out = asyncio.run(inventory.set_inventory_quantities(db, [(ITEM, LOC, 0)]))
    assert out["set"] == 1 and out["written"] == [(ITEM, LOC, 0)] and out["errors"] == []
    guard = db.get_collection(inventory._GUARD_COLLECTION).find_one({"_id": f"{ITEM}|{LOC}"})
    assert guard["lease"] is None


def test_timeout_after_send_not_confirmed_is_refused_and_fenced(monkeypatch):
    """No answer AND the re-read shows the old number: the row is NOT counted
    written (so its baseline stays and the next pass re-sends), the run is
    coded, and the key stays fenced -- a newer write cannot go out while the
    unanswered request might still land; once the fence ends it can."""
    db = StrictDB()
    shop = _Shopify(start=3)

    async def fake(db_, query, variables):
        if "inventorySetQuantities" in query and not shop.applied and variables["input"]["quantities"][0]["quantity"] == 1:
            raise httpx.ReadTimeout("no answer")
        return await shop(db_, query, variables)

    monkeypatch.setattr(inventory, "_graphql", fake)
    monkeypatch.setattr(inventory, "_FENCE_S", 0.3)
    monkeypatch.setattr(inventory, "_WAIT_S", 0.05)
    out = asyncio.run(inventory.set_inventory_quantities(db, [(ITEM, LOC, 1)]))
    assert out["set"] == 0 and out["written"] == []
    assert out["code"] == inventory.STOCK_WRITE_FAILED and "no answer" in out["errors"][0]
    # a newer write during the fence is refused, never sent around it
    blocked = asyncio.run(inventory.set_inventory_quantities(db, [(ITEM, LOC, 0)]))
    assert blocked["set"] == 0 and shop.applied == []
    assert inventory.STOCK_WRITE_GUARD_FAILED in blocked["errors"][0]
    time.sleep(0.35)
    after = asyncio.run(inventory.set_inventory_quantities(db, [(ITEM, LOC, 0)]))
    assert after["set"] == 1 and shop.applied == [0]


def test_an_unreadable_reread_is_unconfirmed_never_success(monkeypatch):
    db = StrictDB()

    async def fake(db_, query, variables):  # noqa: ARG001
        raise httpx.ReadTimeout("down")

    monkeypatch.setattr(inventory, "_graphql", fake)
    out = asyncio.run(inventory.set_inventory_quantities(db, [(ITEM, LOC, 1)]))
    assert out["set"] == 0 and out["written"] == [] and out["code"] == inventory.STOCK_WRITE_FAILED


def test_no_guard_no_write(monkeypatch):
    """Unknown is never assumed: with no database (no ticket, no guard) the
    writer refuses and names it -- it never sends unguarded."""
    shop = _Shopify()
    monkeypatch.setattr(inventory, "_graphql", shop)
    out = asyncio.run(inventory.set_inventory_quantities(None, [(ITEM, LOC, 0)]))
    assert out["set"] == 0 and shop.applied == []
    assert out["code"] == inventory.STOCK_WRITE_FAILED
    assert inventory.STOCK_WRITE_GUARD_FAILED in out["errors"][0]
