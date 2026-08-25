"""
The one-time backfill that puts the EXISTING catalogue into the push queue.
===========================================================================
The queue fix (test_online_push_dirty_flag.py) only queues products created or
edited from now on. The ~59 items already in the catalogue carry no flag at all,
so without this backfill the owner would press "push all pending" and watch
nothing happen.

The backfill is a WRITE against production data, so the contract under test is:
  * dry run changes NOTHING and still reports the count,
  * the real run queues ONLY rows that never reached Shopify,
  * running it twice changes nothing the second time,
  * and it NEVER queues a row the photo rule would refuse (owner ruling
    2026-08-25, "no photo, no publish") -- the safety rule that outranks the
    feature: no path, including this one, may publish a photo-less product.

Run: JWT_SECRET_KEY=test python -m pytest \\
        backend/tests/test_online_store_backfill.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import copy  # noqa: E402

import pytest  # noqa: E402

from api.routers import online_store_push as push_router  # noqa: E402
from scripts import backfill_online_store_queue as bf  # noqa: E402

# The sibling suite's in-memory store -- the same _Coll that nests dot-notation
# $set the way real Mongo does, rather than a weaker double.
from test_online_push_dirty_flag import _DB  # noqa: E402


PHOTO = "https://cdn.example.com/rb.jpg"


@pytest.fixture
def db():
    return _DB()


def _row(pid, **over):
    """A catalogued product as the purge left it: no locally_modified flag, no
    Shopify id, a photograph on the product doc."""
    doc = {
        "id": pid,
        "sku": "SKU-%s" % pid,
        "title": "Ray-Ban RB2140",
        "mrp": 5000.0,
        "images": [PHOTO],
        "ecom": {"status": "DRAFT"},
    }
    doc.update(over)
    return doc


def _seed(db, *docs):
    for d in docs:
        db["catalog_products"].insert_one(copy.deepcopy(d))
    return db["catalog_products"]


def _snapshot(db):
    return copy.deepcopy(sorted(push_router._all_docs(db, "catalog_products"),
                                key=lambda d: d["id"]))


# ===========================================================================
# The dry run
# ===========================================================================


def test_the_dry_run_changes_nothing_and_still_reports_the_count(db):
    coll = _seed(db, _row("P1"), _row("P2"))
    before = _snapshot(db)

    counts, queued, _ = bf.backfill(coll, set(), apply=False)

    assert counts["queue"] == 2, "the dry run must still say how many it WOULD queue"
    assert sorted(queued) == ["SKU-P1", "SKU-P2"]
    assert _snapshot(db) == before, "the dry run wrote to the database"
    assert push_router._product_counts(db)["pending"] == 0


# ===========================================================================
# The real run
# ===========================================================================


def test_the_real_run_queues_only_rows_that_never_reached_shopify(db):
    """A product already on the live storefront must not be re-queued: it would
    be re-pushed and re-priced against a LIVE storefront for no reason."""
    coll = _seed(
        db,
        _row("P1"),
        _row("P2", ecom={"status": "PUBLISHED",
                         "shopify_product_id": "gid://shopify/Product/900"}),
    )

    counts, queued, _ = bf.backfill(coll, set(), apply=True)

    assert counts["queue"] == 1 and queued == ["SKU-P1"]
    assert counts["already_live"] == 1
    assert db["catalog_products"].find_one({"id": "P1"})["ecom"]["locally_modified"] is True
    live = db["catalog_products"].find_one({"id": "P2"})["ecom"]
    assert "locally_modified" not in live, "a live product was re-queued"
    assert live["shopify_product_id"] == "gid://shopify/Product/900"


def test_running_it_twice_changes_nothing_the_second_time(db):
    coll = _seed(db, _row("P1"), _row("P2"))

    first, _, _ = bf.backfill(coll, set(), apply=True)
    after_first = _snapshot(db)
    second, queued2, _ = bf.backfill(coll, set(), apply=True)

    assert first["queue"] == 2
    assert second["queue"] == 0 and queued2 == []
    assert second["already_queued"] == 2
    assert _snapshot(db) == after_first, "the second run mutated the catalogue"


def test_a_queued_row_lands_in_a_status_bucket(db):
    """A row the catalog door built with no ecom.status must not end up queued
    while belonging to neither the DRAFT nor the PUBLISHED card (6eede9b)."""
    coll = _seed(db, _row("P1", ecom={}))

    bf.backfill(coll, set(), apply=True)

    assert db["catalog_products"].find_one({"id": "P1"})["ecom"]["status"] == "DRAFT"


def test_the_queued_rows_are_exactly_what_the_sweep_will_walk(db):
    """The backfill's promise is that pressing publish afterwards DOES
    something -- so what it stamps has to be the set the sweep selects."""
    coll = _seed(db, _row("P1"), _row("P2", images=[]), _row("P3"))

    bf.backfill(coll, set(), apply=True)

    from test_online_push_dirty_flag import _dirty_ids

    assert sorted(_dirty_ids(db)) == ["P1", "P3"]
    assert push_router._product_counts(db)["pending"] == 2


# ===========================================================================
# THE PHOTO RULE -- the safety rule that outranks the feature
# ===========================================================================


def test_a_photo_less_row_is_never_queued_and_is_named(db):
    """"NEVER publish a product the photo rule would refuse, by any path,
    including the backfill." Queueing one would park a guaranteed refusal in
    the queue -- and the owner would never learn WHICH product needs a photo."""
    coll = _seed(db, _row("P1"), _row("P2", images=[]))

    counts, queued, no_photo = bf.backfill(coll, set(), apply=True)

    assert counts["no_photograph"] == 1
    assert no_photo == ["SKU-P2"], "the operator is not told which product to fix"
    assert queued == ["SKU-P1"]
    assert "locally_modified" not in db["catalog_products"].find_one({"id": "P2"})["ecom"]


def test_a_local_upload_path_is_not_a_photograph(db):
    """Same rule as the push: Shopify pulls the bytes over the internet and
    cannot fetch a private /uploads/... path."""
    coll = _seed(db, _row("P1", images=["/uploads/x.jpg"]))

    counts, queued, no_photo = bf.backfill(coll, set(), apply=True)

    assert counts["no_photograph"] == 1 and queued == [] and no_photo == ["SKU-P1"]


# ===========================================================================
# The other refusals -- nothing queued that the push would turn away
# ===========================================================================


@pytest.mark.parametrize(
    "over,bucket",
    [
        ({"ecom": {"status": "ARCHIVED"}}, "archived"),
        ({"is_active": False}, "inactive"),
    ],
)
def test_rows_the_push_would_turn_away_are_not_queued(db, over, bucket):
    coll = _seed(db, _row("P1", **over))

    counts, queued, _ = bf.backfill(coll, set(), apply=True)

    assert counts[bucket] == 1 and queued == []
    assert "locally_modified" not in db["catalog_products"].find_one({"id": "P1"})["ecom"]


def test_a_blocked_product_is_never_queued(db):
    """A member of an online_sync_blocked collection is banned from the
    storefront. The sweep would skip it anyway; the queue must not carry it."""
    coll = _seed(db, _row("P1"), _row("P2"))

    counts, queued, _ = bf.backfill(coll, {"SKU-P2"}, apply=True)

    assert counts["blocked"] == 1 and queued == ["SKU-P1"]


def test_already_live_beats_every_other_bucket(db):
    """A row on the live storefront is never touched by a backfill, whatever
    else is true of it -- including having lost its photograph in IMS."""
    doc = _row("P1", images=[], is_active=False,
               ecom={"status": "PUBLISHED",
                     "shopify_product_id": "gid://shopify/Product/900"})

    assert bf.classify(doc, {"SKU-P1"}) == "already_live"
