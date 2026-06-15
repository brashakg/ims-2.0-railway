"""
Hub Phase 4 -- clone-and-vary: clone one product across N attribute variations
into N catalog_status=DRAFT variants (docs/roadmap/PRODUCT_HUB_RECOMMENDATION
sec 4). Each variant inherits the source's catalog fields + attributes, applies
the per-variation overrides (colour/size/...), mints its OWN unique SKU, lands
DRAFT for review (never auto-published, even when complete), and is still
subject to the Phase-1 duplicate guard.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_hub_phase4_clone_vary.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("PM_MIRROR_ENABLED", "")  # mirror OFF for these unit tests

import pytest  # noqa: E402

from api.services import product_master as pm  # noqa: E402


class DuplicateKeyError(Exception):
    pass


class _FakeRepo:
    """Phase-1 repo + find_by_id (clone reads the source via get_product)."""

    def __init__(self, existing=None):
        self.rows = [dict(r) for r in (existing or [])]
        self._n = 0

    def find_by_id(self, pid):
        return next((dict(r) for r in self.rows if r.get("product_id") == pid), None)

    def find_by_sku(self, sku):
        return next((dict(r) for r in self.rows if r.get("sku") == sku), None)

    def find_by_identity_key(self, key):
        if not key:
            return None
        return next((dict(r) for r in self.rows if r.get("identity_key") == key), None)

    def find_by_barcode(self, bc):
        return next((dict(r) for r in self.rows if r.get("barcode") == bc), None)

    def create(self, data, *, raise_on_duplicate=False):
        # identity collision (Phase-1 dup guard backstop) -> simulate the index raise
        ik = data.get("identity_key")
        if ik and any(r.get("identity_key") == ik for r in self.rows):
            if raise_on_duplicate:
                raise DuplicateKeyError("E11000 duplicate key")
        self._n += 1
        data.setdefault("product_id", "P-NEW-%d" % self._n)
        self.rows.append(dict(data))
        return dict(data)

    def update(self, pid, fields):
        for r in self.rows:
            if r.get("product_id") == pid:
                r.update(fields)
                return True
        return False


def _source_frame():
    # a COMPLETE frame so variants would be ACTIVE -> force-DRAFT must apply
    return {
        "product_id": "SRC-1",
        "sku": "FRRAYBANRB2140BLK",
        "category": "FRAME",
        "attributes": {
            "brand_name": "Ray-Ban",
            "model_no": "RB-2140",
            "colour_code": "BLK",
        },
        "identity_key": "ray ban|rb 2140|blk",
        "mrp": 5000.0,
        "offer_price": 4500.0,
        "cost_price": 2000.0,
        "hsn_code": "9003",
        "gst_rate": 5.0,
        "catalog_status": "ACTIVE",
        "is_active": True,
    }


def test_clone_across_colours_makes_draft_variants():
    repo = _FakeRepo(existing=[_source_frame()])
    out = pm.clone_and_vary(
        source_id="SRC-1",
        variations=[
            {"colour_code": "RED"},
            {"colour_code": "BLU"},
            {"colour_code": "GRN"},
        ],
        actor="u-cat",
        product_repo=repo,
        db=None,
    )
    assert out["source_id"] == "SRC-1"
    assert len(out["created"]) == 3 and not out["errors"]
    # distinct SKUs, varied colour, every variant DRAFT (not ACTIVE)
    skus = {c["sku"] for c in out["created"]}
    assert len(skus) == 3
    colours = {c["attributes"]["colour_code"] for c in out["created"]}
    assert colours == {"RED", "BLU", "GRN"}
    for c in out["created"]:
        row = repo.find_by_id(c["product_id"])
        assert row["catalog_status"] == "DRAFT", row
        # inherited the source's price/category
        assert row["category"] == "FRAME" and row["mrp"] == 5000.0


def test_clone_source_not_found_404():
    repo = _FakeRepo(existing=[])
    with pytest.raises(pm.ProductMasterError) as ei:
        pm.clone_and_vary(
            source_id="NOPE",
            variations=[{"colour_code": "RED"}],
            actor="u",
            product_repo=repo,
            db=None,
        )
    assert ei.value.status == 404


def test_clone_duplicate_variation_collected_not_created():
    # a variation that reproduces the SOURCE's own colour collides on identity
    repo = _FakeRepo(existing=[_source_frame()])
    out = pm.clone_and_vary(
        source_id="SRC-1",
        variations=[
            {"colour_code": "BLK"},  # same as source -> identity dup -> 409
            {"colour_code": "RED"},  # fine
        ],
        actor="u",
        product_repo=repo,
        db=None,
    )
    assert len(out["created"]) == 1
    assert out["created"][0]["attributes"]["colour_code"] == "RED"
    assert len(out["errors"]) == 1
    assert out["errors"][0]["index"] == 0


def test_clone_by_size_makes_distinct_variants():
    # size is folded into identity_key -> the SAME frame in N sizes is N distinct
    # products (no false DUPLICATE_PRODUCT). Was the lens-flagged drop bug.
    repo = _FakeRepo(existing=[_source_frame()])
    out = pm.clone_and_vary(
        source_id="SRC-1",
        variations=[{"size": "52"}, {"size": "54"}, {"size": "56"}],
        actor="u",
        product_repo=repo,
        db=None,
    )
    assert len(out["created"]) == 3 and not out["errors"]
    # all DRAFT (born-DRAFT), distinct SKUs
    assert {c["catalog_status"] for c in out["created"]} == {"DRAFT"}
    assert len({c["sku"] for c in out["created"]}) == 3


def test_identity_key_folds_size_when_present():
    # backward-compatible: sizeless keeps the 3-part key; sized appends size
    assert pm.compute_identity_key("RB", "2140", "BLK") == "rb|2140|blk"
    assert pm.compute_identity_key("RB", "2140", "BLK", "52") == "rb|2140|blk|52"
    assert pm.compute_identity_key(
        "RB", "2140", "BLK", "52"
    ) != pm.compute_identity_key("RB", "2140", "BLK", "54")


def test_clone_empty_variation_overrides_nothing():
    # an empty/None override just clones the source colour -> identity dup -> error
    repo = _FakeRepo(existing=[_source_frame()])
    out = pm.clone_and_vary(
        source_id="SRC-1",
        variations=[{}],
        actor="u",
        product_repo=repo,
        db=None,
    )
    # clones source as-is -> collides with the source identity
    assert len(out["created"]) == 0 and len(out["errors"]) == 1
