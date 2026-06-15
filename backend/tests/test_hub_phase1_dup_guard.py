"""
Hub Phase 1 -- duplicate hard-block (409 + show-existing) + identity key.

Locks the Phase-1 contract (docs/roadmap/PRODUCT_HUB_RECOMMENDATION.md sec 4):
entering a product that already exists -- by SKU, by brand+model+colour identity,
or by barcode -- is REFUSED with a 409 carrying the existing row so the FE can
link to it ("add stock or a variant instead"). The DB unique index is the
race-safe backstop: a create that loses the race (DuplicateKeyError) surfaces as
the SAME clean 409, not a swallowed None -> 500.

Run: JWT_SECRET_KEY=test python -m pytest \
        backend/tests/test_hub_phase1_dup_guard.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("PM_MIRROR_ENABLED", "")  # mirror OFF for these unit tests

import pytest  # noqa: E402

from api.services import product_master as pm  # noqa: E402


# A locally-named DuplicateKeyError: product_master matches the exception by
# CLASS NAME ("DuplicateKeyError"), so this faithfully simulates the pymongo
# unique-index raise without importing pymongo.
class DuplicateKeyError(Exception):
    pass


class _FakeRepo:
    """Minimal product repo: SKU/identity/barcode finders + a create that can
    simulate losing the unique-index race exactly once (race_winner)."""

    def __init__(self, existing=None, race_winner=None):
        self.rows = [dict(r) for r in (existing or [])]
        self.race_winner = race_winner
        self._raced = False

    def _winner_visible(self, field, value):
        return (
            self._raced
            and self.race_winner is not None
            and self.race_winner.get(field) == value
        )

    def find_by_sku(self, sku):
        if self._winner_visible("sku", sku):
            return dict(self.race_winner)
        return next((dict(r) for r in self.rows if r.get("sku") == sku), None)

    def find_by_identity_key(self, key):
        if not key:
            return None
        if self._winner_visible("identity_key", key):
            return dict(self.race_winner)
        return next((dict(r) for r in self.rows if r.get("identity_key") == key), None)

    def find_by_barcode(self, bc):
        return next((dict(r) for r in self.rows if r.get("barcode") == bc), None)

    def create(self, data, *, raise_on_duplicate=False):
        if self.race_winner is not None and not self._raced:
            self._raced = True
            if raise_on_duplicate:
                raise DuplicateKeyError("E11000 duplicate key")
        data.setdefault("product_id", "P-%d" % (len(self.rows) + 1))
        self.rows.append(dict(data))
        return dict(data)

    def update(self, pid, fields):
        for r in self.rows:
            if r.get("product_id") == pid:
                r.update(fields)
                return True
        return False


def _frame_attrs(brand="Ray-Ban", model="RB-2140", colour="BLK"):
    return {"brand_name": brand, "model_no": model, "colour_code": colour}


def _create(repo, *, sku="FR-1", attrs=None, extra_fields=None):
    return pm.create_product(
        category="FRAME",
        attributes=attrs if attrs is not None else _frame_attrs(),
        mrp=5000.0,
        offer_price=4500.0,
        actor="u1",
        sku=sku,
        cost_price=2000.0,
        product_repo=repo,
        extra_fields=extra_fields,
    )


# ===========================================================================
# 1. compute_identity_key + normalise_payload stamp
# ===========================================================================


def test_identity_key_normalises_case_space_and_punctuation():
    # case + whitespace + PUNCTUATION (- / _ .) all fold, matching the SKU builder,
    # so "RB-2140" and "RB 2140" are the SAME identity (they mint the same SKU).
    a = pm.compute_identity_key("Ray Ban", "RB-2140", "BLK")
    b = pm.compute_identity_key("  ray-ban ", "rb  2140", "blk")
    assert a == b == "ray ban|rb 2140|blk"


def test_identity_key_punctuation_variants_collide():
    assert pm.compute_identity_key("RB", "RB-2140", "BLK") == pm.compute_identity_key(
        "RB", "RB 2140", "BLK"
    )


def test_identity_key_none_without_brand_or_model():
    assert pm.compute_identity_key("", "RB-1", "BLK") is None
    assert pm.compute_identity_key("Ray-Ban", "", "BLK") is None


def test_identity_key_colour_distinguishes():
    assert pm.compute_identity_key("RB", "M", "BLK") != pm.compute_identity_key(
        "RB", "M", "RED"
    )


def test_normalise_payload_stamps_identity_key():
    doc = pm.normalise_payload(
        category="FRAME",
        attributes=_frame_attrs(),
        mrp=5000.0,
        offer_price=4500.0,
        sku="FR-ID-1",
        cost_price=2000.0,
    )
    assert doc["identity_key"] == "ray ban|rb 2140|blk"


def test_normalise_payload_no_identity_for_services():
    doc = pm.normalise_payload(
        category="SERVICES",
        attributes={"name": "Lens fitting"},
        mrp=500.0,
        offer_price=450.0,
        sku="SVC-1",
        cost_price=100.0,
        as_draft=True,
    )
    assert "identity_key" not in doc


# ===========================================================================
# 2. Duplicate hard-block (409 + show-existing)
# ===========================================================================


def test_dup_by_sku_blocked_409_with_existing():
    repo = _FakeRepo()
    first = _create(repo, sku="FR-DUP")
    with pytest.raises(pm.ProductMasterError) as ei:
        _create(repo, sku="FR-DUP", attrs=_frame_attrs(model="RB-OTHER"))
    err = ei.value
    assert err.status == 409
    assert err.code == "DUPLICATE_PRODUCT"
    assert err.conflict["product_id"] == first["product_id"]
    assert err.conflict["sku"] == "FR-DUP"


def test_dup_by_identity_blocked_even_with_new_sku():
    repo = _FakeRepo()
    first = _create(repo, sku="FR-A")
    # Same brand+model+colour, DIFFERENT sku -> identity collision -> 409.
    with pytest.raises(pm.ProductMasterError) as ei:
        _create(repo, sku="FR-B")
    err = ei.value
    assert err.status == 409
    assert err.conflict["identity_key"] == first["identity_key"]


def test_dup_by_barcode_blocked():
    repo = _FakeRepo()
    _create(repo, sku="FR-BC-1", extra_fields={"barcode": "8901234567890"})
    with pytest.raises(pm.ProductMasterError) as ei:
        _create(
            repo,
            sku="FR-BC-2",
            attrs=_frame_attrs(model="RB-OTHER"),
            extra_fields={"barcode": "8901234567890"},
        )
    assert ei.value.status == 409


def test_distinct_products_both_create():
    repo = _FakeRepo()
    a = _create(repo, sku="FR-1", attrs=_frame_attrs(colour="BLK"))
    b = _create(repo, sku="FR-2", attrs=_frame_attrs(colour="RED"))
    assert a["product_id"] != b["product_id"]
    assert a["identity_key"] != b["identity_key"]


# ===========================================================================
# 3. Race-safe: DuplicateKeyError -> 409 (not a swallowed 500)
# ===========================================================================


def test_race_lost_insert_surfaces_409():
    # The pre-check sees nothing (the winner lands between check and insert);
    # the unique index raises DuplicateKeyError; we re-query + 409.
    winner = {
        "product_id": "P-WINNER",
        "sku": "FR-RACE",
        "identity_key": "ray ban|rb 2140|blk",
    }
    repo = _FakeRepo(race_winner=winner)
    with pytest.raises(pm.ProductMasterError) as ei:
        _create(repo, sku="FR-RACE")
    err = ei.value
    assert err.status == 409
    assert err.conflict["product_id"] == "P-WINNER"


def test_create_succeeds_when_no_duplicate():
    repo = _FakeRepo()
    created = _create(repo, sku="FR-OK")
    assert created["sku"] == "FR-OK"
    assert created["identity_key"] == "ray ban|rb 2140|blk"
    assert created["catalog_status"] == pm.CATALOG_STATUS_ACTIVE


# ===========================================================================
# 4. dedupe-prep migration (identity backfill + dup fixes)
# ===========================================================================


class _FakeColl:
    """List-backed products collection: find({}) + update_one(match, {$set/$unset})."""

    def __init__(self, docs):
        self.docs = [dict(d) for d in docs]

    def find(self, _filter=None):
        return [dict(d) for d in self.docs]

    def _match(self, doc, m):
        return all(doc.get(k) == v for k, v in m.items())

    def update_one(self, match, update):
        for d in self.docs:
            if self._match(d, match):
                d.update(update.get("$set", {}))
                for k in update.get("$unset", {}) or {}:
                    d.pop(k, None)
                return type("R", (), {"modified_count": 1})()
        return type("R", (), {"modified_count": 0})()


def test_dedupe_backfills_identity_key():
    from scripts import backfill_dedupe_prep as dd

    coll = _FakeColl(
        [{"product_id": "P1", "brand": "Ray-Ban", "model": "RB-1", "color": "BLK"}]
    )
    stats = dd.run_dedupe(coll, apply=True)
    assert stats["identity_backfilled"] == 1
    assert coll.docs[0]["identity_key"] == "ray ban|rb 1|blk"
    # idempotent
    assert dd.run_dedupe(coll, apply=True)["identity_backfilled"] == 0


def test_dedupe_unsets_empty_barcode():
    from scripts import backfill_dedupe_prep as dd

    coll = _FakeColl([{"product_id": "P1", "barcode": ""}])
    dd.run_dedupe(coll, apply=True)
    assert "barcode" not in coll.docs[0]


def test_dedupe_resku_duplicate_skus():
    from scripts import backfill_dedupe_prep as dd

    coll = _FakeColl(
        [
            {"product_id": "P1", "sku": "FR-DUP"},
            {"product_id": "P2", "sku": "FR-DUP"},
            {"product_id": "P3", "sku": "FR-DUP"},
        ]
    )
    stats = dd.run_dedupe(coll, apply=True)
    assert stats["resku"] == 2
    skus = sorted(d["sku"] for d in coll.docs)
    assert skus == ["FR-DUP", "FR-DUP-DUP2", "FR-DUP-DUP3"]


def test_dedupe_assigns_missing_product_id():
    from scripts import backfill_dedupe_prep as dd

    coll = _FakeColl([{"_id": "x1", "sku": "FR-NOPID"}])
    stats = dd.run_dedupe(coll, apply=True)
    assert stats["pid_assigned"] == 1
    assert coll.docs[0].get("product_id")


def test_dedupe_dry_run_writes_nothing():
    from scripts import backfill_dedupe_prep as dd

    coll = _FakeColl([{"product_id": "P1", "brand": "B", "model": "M", "color": "C"}])
    stats = dd.run_dedupe(coll, apply=False)
    assert stats["identity_backfilled"] == 1  # counted
    assert "identity_key" not in coll.docs[0]  # but not written
