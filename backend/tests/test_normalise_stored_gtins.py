"""
scripts/normalise_stored_gtins.py -- stores each saved manufacturer barcode
digits only, so a scan (an exact match) finds a GTIN typed with spaces or
hyphens before the doors sanitised it.

Pins (each red when its rule is removed):
  * a dry run writes NOTHING (revert `if not commit: return` -> rewritten -> red);
  * --commit rewrites ONLY a string with a separator that is a valid GTIN once
    they go: never an invalid value, never one already bare, never the legacy
    products.barcode (report only);
  * the write's filter carries the value it read: a value changed since the
    read is left alone (drop `f: raw` from the filter -> overwritten -> red);
  * duplicates are one GTIN in ANY spelling on more than one row;
  * it refuses any other collection with SystemExit, never a bare `assert`.

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
import normalise_stored_gtins as script  # noqa: E402

_EAN = "8056597720373"
_UPC = "036000291452"


def _products():
    return StrictCollection(
        "products",
        [
            {"product_id": "P-SP", "attributes": {"gtin": "8 056597 720373"}},
            {"product_id": "P-HY", "attributes": {"upc": "0360-0029-1452"}},
            {"product_id": "P-OK", "attributes": {"gtin": "8056597626088"}},
            {"product_id": "P-BAD", "attributes": {"gtin": "2511-661"}},
            {"product_id": "P-IN", "attributes": {"gtin": "2000000 000015"}},
            {"product_id": "P-LEG", "barcode": "5260181590836", "attributes": {}},
            {"product_id": "P-DUP", "attributes": {"gtin": "0" + _UPC}},
            {"product_id": "P-NONE", "attributes": {"gtin": ""}},
        ],
    )


def _attrs(coll):
    return {d["product_id"]: dict(d.get("attributes") or {}) for d in coll.docs}


def test_dry_run_writes_nothing_and_lists_the_rewrites():
    coll = _products()
    before = copy.deepcopy(coll.docs)
    out = script.normalise(coll, commit=False)
    assert coll.docs == before
    assert sorted(out["rewrite"]) == [
        ("P-HY", "attributes.upc", "0360-0029-1452", _UPC),
        ("P-SP", "attributes.gtin", "8 056597 720373", _EAN),
    ]


def test_commit_stores_only_valid_separated_values_digits_only():
    coll = _products()
    out = script.normalise(coll, commit=True)
    attrs = _attrs(coll)
    assert attrs["P-SP"] == {"gtin": _EAN}
    assert attrs["P-HY"] == {"upc": _UPC}
    # Invalid values -- a supplier code, an in-store GS1 20-29 code -- are
    # reported, never "fixed" into something that looks publishable.
    assert attrs["P-BAD"] == {"gtin": "2511-661"}
    assert attrs["P-IN"] == {"gtin": "2000000 000015"}
    assert sorted((r[0], r[3]) for r in out["invalid"]) == [
        ("P-BAD", "BADLEN"),
        ("P-IN", "RESTRICTED"),
    ]
    # The legacy products.barcode is reported (unverified), never moved or changed.
    leg = next(d for d in coll.docs if d["product_id"] == "P-LEG")
    assert leg["barcode"] == "5260181590836" and leg["attributes"] == {}
    assert [r[0] for r in out["legacy"]] == ["P-LEG"]
    # One GTIN in two spellings on two products is a duplicate.
    assert out["duplicates"] == [(_UPC.zfill(14), ["P-DUP", "P-HY"])]
    # A re-run finds nothing left to rewrite.
    assert script.normalise(coll, commit=True)["rewrite"] == []


def test_a_value_changed_since_the_read_is_left_alone():
    """The write's filter is the row's id AND the value it read: a cataloguer
    who saved a new code between the read and the write keeps it."""
    coll = _products()
    stale = copy.deepcopy(coll.docs)
    coll.find = lambda *_a, **_k: copy.deepcopy(stale)
    next(d for d in coll.docs if d["product_id"] == "P-SP")["attributes"]["gtin"] = "8056597626088"
    script.normalise(coll, commit=True)
    assert _attrs(coll)["P-SP"] == {"gtin": "8056597626088"}
    assert _attrs(coll)["P-HY"] == {"upc": _UPC}


def test_the_catalogue_twin_and_variant_rows_are_stored_digits_only():
    twins = StrictCollection(
        "catalog_products",
        [{"id": "T-1", "gtin": "805-6597-72037-3", "attributes": {"gtin": "805-6597-72037-3"}}],
    )
    script.normalise(twins, commit=True)
    assert twins.docs[0]["gtin"] == _EAN and twins.docs[0]["attributes"]["gtin"] == _EAN
    rows = StrictCollection("catalog_variants", [{"sku": "S-1", "gtin": " 8056597720373 "}])
    script.normalise(rows, commit=True)
    assert rows.docs[0]["gtin"] == _EAN


def test_refuses_any_other_collection():
    other = StrictCollection("stock_units", [{"barcode": "8 056597 720373", "attributes": {"gtin": "8 056597 720373"}}])
    with pytest.raises(SystemExit, match="refusing"):
        script.normalise(other, commit=True)
    assert other.docs[0]["attributes"]["gtin"] == "8 056597 720373"


def test_no_connection_is_a_clean_exit(monkeypatch):
    for k in ("MONGO_PUBLIC_URL", "MONGODB_URI", "MONGODB_URL", "MONGO_URL"):
        monkeypatch.delenv(k, raising=False)
    assert script.main([]) == 2


def test_the_command_is_a_dry_run_unless_commit(monkeypatch):
    """The runbook runs the COMMAND with no flag first: main() must pass commit
    only on --commit, and walk every one of the three collections."""
    import pymongo

    colls = {
        "products": _products(),
        "catalog_products": StrictCollection("catalog_products", [{"id": "T-1", "gtin": "805-6597-72037-3"}]),
        "catalog_variants": StrictCollection("catalog_variants", [{"sku": "S-1", "gtin": "805 6597 72037 3"}]),
    }

    class _Client:
        def __init__(self, *_a, **_k):
            pass

        def __getitem__(self, _db):
            return colls

    monkeypatch.setattr(pymongo, "MongoClient", _Client)
    before = {n: copy.deepcopy(c.docs) for n, c in colls.items()}
    assert script.main(["--mongo-uri", "mongodb://fake"]) == 0
    assert {n: c.docs for n, c in colls.items()} == before
    assert script.main(["--mongo-uri", "mongodb://fake", "--commit"]) == 0
    assert _attrs(colls["products"])["P-SP"] == {"gtin": _EAN}
    assert colls["catalog_products"].docs[0]["gtin"] == _EAN
    assert colls["catalog_variants"].docs[0]["gtin"] == _EAN
