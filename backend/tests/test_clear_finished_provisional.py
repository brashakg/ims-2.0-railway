"""
scripts/clear_finished_provisional.py -- the one-time clear of the ordered-
draft mark on products finished before PR #1171 cleared it on the finishing
save (round 9, problems 6+7).

Pins (each red when its rule is removed):
  * a dry run writes NOTHING;
  * --commit writes the finishing save's own fields (restamp_on_update:
    provisional False, bought_before_catalogued True) on EXACTLY the finished
    provisional rows (catalog_status ACTIVE, or none -- a legacy row reads
    ACTIVE) and on nothing else: a real ordered draft (DRAFT) is untouched;
  * a re-run changes nothing; it refuses any handle but `products`.

StrictCollection only -- no network, no production.
"""

import copy
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(_HERE)), "scripts"))

from strict_fakes import StrictCollection  # noqa: E402
import clear_finished_provisional as script  # noqa: E402
from api.services.product_master import is_ordered_draft  # noqa: E402


def _products():
    return StrictCollection(
        "products",
        [
            # finished before the mark was cleared: on sale, still marked
            {"product_id": "P-DONE", "sku": "D-1", "provisional": True, "catalog_status": "ACTIVE", "is_active": True},
            # finished and switched off, no status field (a legacy row reads ACTIVE)
            {"product_id": "P-LEGACY", "sku": "L-1", "provisional": True, "is_active": False},
            # a real ordered draft: the cataloguer's, never touched -- even one
            # whose every field is filled (its save restamps and releases it)
            {"product_id": "P-DRAFT", "sku": "R-1", "provisional": True, "catalog_status": "DRAFT", "is_active": False,
             "category": "FRAME", "brand": "Boss", "model": "BOSS 1700", "mrp": 2990, "offer_price": 2790,
             "cost_price": 1200, "hsn_code": "900311", "gst_rate": 5,
             "attributes": {"brand_name": "Boss", "model_no": "BOSS 1700", "colour_code": "C2", "lens_size": "52"}},
            # not marked: never touched
            {"product_id": "P-PLAIN", "sku": "P-1", "catalog_status": "ACTIVE", "is_active": True},
        ],
    )


def _by_id(coll):
    return {d["product_id"]: d for d in copy.deepcopy(coll.docs)}


def test_dry_run_writes_nothing():
    products = _products()
    before = copy.deepcopy(products.docs)
    assert sorted(script.clear(products, commit=False)) == ["P-DONE", "P-LEGACY"]
    assert products.docs == before


def test_commit_clears_exactly_the_finished_rows():
    products = _products()
    before = _by_id(products)
    script.clear(products, commit=True)
    after = _by_id(products)
    expected = copy.deepcopy(before)
    for pid in ("P-DONE", "P-LEGACY"):
        expected[pid].update(provisional=False, bought_before_catalogued=True)
    assert after == expected
    assert is_ordered_draft(after["P-DRAFT"]) and not is_ordered_draft(after["P-DONE"])


def test_a_rerun_changes_nothing():
    products = _products()
    script.clear(products, commit=True)
    once = copy.deepcopy(products.docs)
    assert script.clear(products, commit=True) == []
    assert products.docs == once


def test_refuses_any_handle_but_products():
    other = StrictCollection("catalog_products", copy.deepcopy(_products().docs))
    before = copy.deepcopy(other.docs)
    with pytest.raises(SystemExit, match="refusing"):
        script.clear(other, commit=True)
    assert other.docs == before


def test_the_command_is_a_dry_run_unless_commit(monkeypatch):
    import pymongo

    products = _products()

    class _Client:
        def __init__(self, *_a, **_k):
            pass

        def __getitem__(self, _db):
            return {"products": products}

    monkeypatch.setattr(pymongo, "MongoClient", _Client)
    before = copy.deepcopy(products.docs)
    assert script.main(["--mongo-uri", "mongodb://test"]) == 0
    assert products.docs == before
    assert script.main(["--mongo-uri", "mongodb://test", "--commit"]) == 0
    assert products.docs != before
