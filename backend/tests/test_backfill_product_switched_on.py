"""
scripts/backfill_product_switched_on.py -- the one-time F48 backfill that gives
provisional products switched on / deleted before the stamps existed the
switched_on_at / deleted_at reorder_policy.discontinued() reads.

Pins (each red when its rule is removed):
  * a dry run writes NOTHING;
  * --commit stamps EXACTLY the two classes -- (1) provisional, no
    switched_on_at, active now or sold: switched_on_at = earliest sale, else
    now; (2) provisional, inactive, no deleted_at, catalog twin deleted (its
    deleted_at, or a DELETED / ARCHIVED status): the twin's deleted_at -- and
    no other field on any row;
  * a re-run changes nothing;
  * it refuses any handle but products / catalog_products / orders with
    SystemExit, never a bare `assert`;
  * the command is a dry run unless --commit.

StrictCollection only -- no network, no production.
"""

import copy
import os
import sys
from datetime import datetime, timezone

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(_HERE)), "scripts"))

from strict_fakes import StrictCollection  # noqa: E402
import backfill_product_switched_on as script  # noqa: E402
from api.services.reorder_policy import discontinued  # noqa: E402

NOW = datetime(2026, 10, 8, 6, 0, tzinfo=timezone.utc)
TWIN_DELETED_AT = "2026-08-01T10:00:00"


def _db():
    products = StrictCollection(
        "products",
        [
            # class 1: active now, never sold -> now. Its twin was deleted and
            # the spine switched back on since: live, so never stamped deleted
            {"product_id": "P-ON", "provisional": True, "is_active": True},
            # class 1: switched off now, sold twice -> the EARLIER sale
            {"product_id": "P-SOLD", "provisional": True, "is_active": False},
            # still new: never switched on, never sold, twin alive
            {"product_id": "P-NEW", "provisional": True, "is_active": False, "pim_product_id": "C-NEW"},
            # class 2: twin (keyed by pim_product_id) deleted by the old DELETE
            {"product_id": "P-DEL", "provisional": True, "is_active": False, "pim_product_id": "C-DEL"},
            # class 2: twin (keyed by product_id) archived, no deleted_at -> now
            {"product_id": "P-ARCH", "provisional": True, "is_active": False},
            # already stamped: untouched
            {"product_id": "P-STAMPED", "provisional": True, "is_active": True,
             "switched_on_at": datetime(2026, 10, 7, tzinfo=timezone.utc)},
            {"product_id": "P-SPINE-DEL", "provisional": True, "is_active": False,
             "deleted_at": "2026-10-07T00:00:00+00:00", "switched_on_at": NOW},
            # not provisional: never touched, however it looks
            {"product_id": "P-PLAIN", "is_active": False, "pim_product_id": "C-PLAIN"},
        ],
    )
    catalog = StrictCollection(
        "catalog_products",
        [
            {"id": "C-NEW", "sku": "NEW-1", "is_active": False},
            {"id": "P-ON", "deleted_at": TWIN_DELETED_AT},
            {"id": "C-DEL", "sku": "DEL-1", "is_active": False, "deleted_at": TWIN_DELETED_AT},
            {"id": "P-ARCH", "sku": "ARCH-1", "ecom": {"status": "ARCHIVED"}},
            {"id": "P-SPINE-DEL", "deleted_at": TWIN_DELETED_AT},
            {"id": "C-PLAIN", "deleted_at": TWIN_DELETED_AT},
            # a deleted twin of SOMEONE ELSE that shares nothing with P-NEW
            {"id": "C-OTHER", "sku": "NEW-1-OLD", "deleted_at": TWIN_DELETED_AT},
        ],
    )
    orders = StrictCollection(
        "orders",
        [
            {"order_id": "O-2", "created_at": datetime(2026, 9, 20, 5, 30), "items": [{"product_id": "P-SOLD"}]},
            {"order_id": "O-1", "created_at": "2026-09-02T04:00:00+00:00",
             "items": [{"product_id": "X"}, {"product_id": "P-SOLD"}]},
            {"order_id": "O-3", "created_at": datetime(2026, 9, 1), "items": [{"product_id": "P-PLAIN"}]},
        ],
    )
    return products, catalog, orders


def _by_id(coll):
    return {d["product_id"]: d for d in copy.deepcopy(coll.docs)}


def test_dry_run_writes_nothing():
    products, catalog, orders = _db()
    before = copy.deepcopy([products.docs, catalog.docs, orders.docs])
    out = script.backfill(products, catalog, orders, commit=False, now=NOW)
    assert sorted(out["switched_on"]) == ["P-ON", "P-SOLD"]
    assert sorted(out["deleted"]) == ["P-ARCH", "P-DEL"]
    assert [products.docs, catalog.docs, orders.docs] == before


def test_commit_stamps_exactly_the_two_classes_and_nothing_else():
    products, catalog, orders = _db()
    before = _by_id(products)
    script.backfill(products, catalog, orders, commit=True, now=NOW)
    after = _by_id(products)

    expected = copy.deepcopy(before)
    expected["P-ON"]["switched_on_at"] = NOW
    expected["P-SOLD"]["switched_on_at"] = datetime(2026, 9, 2, 4, 0, tzinfo=timezone.utc)
    expected["P-DEL"]["deleted_at"] = TWIN_DELETED_AT
    expected["P-ARCH"]["deleted_at"] = NOW.isoformat()
    assert after == expected
    assert catalog.docs == _db()[1].docs and orders.docs == _db()[2].docs

    # what the stamps are for: the rule now reads them right
    assert discontinued(after["P-SOLD"]) and discontinued(after["P-DEL"]) and discontinued(after["P-ARCH"])
    assert not discontinued(after["P-NEW"])


def test_a_rerun_changes_nothing():
    products, catalog, orders = _db()
    script.backfill(products, catalog, orders, commit=True, now=NOW)
    once = copy.deepcopy(products.docs)
    later = datetime(2026, 10, 9, tzinfo=timezone.utc)
    assert script.backfill(products, catalog, orders, commit=True, now=later) == {"switched_on": [], "deleted": []}
    assert products.docs == once


def test_every_write_reasserts_its_class():
    """A row that left its class between the read and the write (stamped by a
    live door meanwhile) is not overwritten: each update re-asserts the class
    filter. Simulated by a read that ignores the filter and hands back every
    row."""
    products, catalog, orders = _db()
    products.find = lambda _flt, proj=None: StrictCollection.find(products, {}, proj)
    before = _by_id(products)
    script.backfill(products, catalog, orders, commit=True, now=NOW)
    after = _by_id(products)
    for pid in ("P-STAMPED", "P-SPINE-DEL", "P-PLAIN", "P-NEW"):
        assert after[pid] == before[pid], pid


@pytest.mark.parametrize("which", [0, 1, 2])
def test_refuses_any_handle_but_products_catalog_orders(which):
    handles = list(_db())
    handles[which] = StrictCollection("stock_units", copy.deepcopy(handles[which].docs))
    before = copy.deepcopy(handles[0].docs)
    with pytest.raises(SystemExit, match="refusing"):
        script.backfill(*handles, commit=True, now=NOW)
    assert handles[0].docs == before


def test_no_connection_is_a_clean_exit(monkeypatch):
    for k in ("MONGO_PUBLIC_URL", "MONGODB_URI", "MONGODB_URL", "MONGO_URL"):
        monkeypatch.delenv(k, raising=False)
    assert script.main([]) == 2


def test_the_command_is_a_dry_run_unless_commit(monkeypatch):
    """The runbook runs the COMMAND with no flag first: main() must pass
    commit only on --commit."""
    import pymongo

    products, catalog, orders = _db()
    colls = {"products": products, "catalog_products": catalog, "orders": orders}

    class _Client:
        def __init__(self, *_a, **_k):
            pass

        def __getitem__(self, _db):
            return colls

    monkeypatch.setattr(pymongo, "MongoClient", _Client)
    before = copy.deepcopy(products.docs)
    assert script.main(["--mongo-uri", "mongodb://fake"]) == 0
    assert products.docs == before
    assert script.main(["--mongo-uri", "mongodb://fake", "--commit"]) == 0
    assert _by_id(products)["P-DEL"]["deleted_at"] == TWIN_DELETED_AT
