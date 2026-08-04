"""
IMS 2.0 -- catalog_products.sku is written, and stays written
=============================================================
THE DEFECT these tests lock down: `product_master._build_pim_doc` wrote the
catalog_products PIM row with `parent_sku` and NO top-level `sku`. Every ONLINE
consumer joins catalog_products on `sku` -- the push-sweep block classifier
(`doc.get("sku") in blocked_set`), `online_block._membership_hit`
(`sku = product.get("sku")`, False the moment it is falsy), shopify_push's
collection-member lookup and ecom_smart_rules. So the "block this collection
from online sale" guard silently NO-OPPED for every PM-created product.

WHY THESE TESTS USE `catalog_repo=None, db=<fake>` AND NOT A FAKE REPO
----------------------------------------------------------------------
There is NO CatalogProductRepository in the codebase and NO production caller
passes `catalog_repo` -- routers/product_master.py hardcodes `catalog_repo=None`
and every other door leaves it defaulted. Production therefore ALWAYS takes the
`db` branch in both `_write_mirror` and `_stage_catalog_draft`. A test that
injected a fake repo would leave the real write path completely untested, so
these tests assert on the doc that ACTUALLY LANDS in the fake
`catalog_products` collection.

Also covers the backfill script's pure core (gates abort with zero writes; a
clean set applies exactly N; a second run applies 0).

Run: JWT_SECRET_KEY=test ENVIRONMENT=test \
     python -m pytest backend/tests/test_pim_sku_autogen.py -q
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from database.connection import MockCollection  # noqa: E402
from database.repositories.product_repository import ProductRepository  # noqa: E402
from database.repositories.audit_repository import AuditRepository  # noqa: E402
from database.repositories.catalog_variant_repository import (  # noqa: E402
    CatalogVariantRepository,
)
from api.services import online_block  # noqa: E402
from api.services import product_master as pm  # noqa: E402
from scripts import backfill_pim_sku_from_parent as bf  # noqa: E402


# ---------------------------------------------------------------------------
# Fake DB: the real MockCollection matcher, plus the two things the PRODUCTION
# write path needs that MockCollection does not implement -- update_one(upsert=)
# and a db.get_collection(name) accessor.
# ---------------------------------------------------------------------------


class _UpsertCollection(MockCollection):
    """MockCollection + upsert support (the production staging writer calls
    `update_one({"id": ...}, {"$set": doc}, upsert=True)`)."""

    def update_one(self, filter: Dict, update: Dict, upsert: bool = False):  # noqa: A002
        existing = self.find_one(filter)
        if existing is None and upsert:
            doc: Dict[str, Any] = {}
            for key, val in (filter or {}).items():
                if not key.startswith("$") and not isinstance(val, dict):
                    doc[key] = val
            doc.update(dict(update.get("$set") or {}))
            self.insert_one(doc)
            return type("obj", (object,), {"modified_count": 0, "upserted_id": 1})()
        return super().update_one(filter, update)


class _FakeDb:
    """Both access styles: `db.get_collection(name)` (product_master) and
    `db[name]` (online_block)."""

    def __init__(self):
        self._colls: Dict[str, _UpsertCollection] = {}

    def get_collection(self, name: str) -> _UpsertCollection:
        if name not in self._colls:
            self._colls[name] = _UpsertCollection(name)
        return self._colls[name]

    def __getitem__(self, name: str) -> _UpsertCollection:
        return self.get_collection(name)

    def docs(self, name: str) -> List[Dict[str, Any]]:
        return [dict(d) for d in self.get_collection(name)._data.values()]


@pytest.fixture(autouse=True)
def _mirror_off(monkeypatch):
    """Default to mirror OFF (a fresh deploy). Individual tests opt in."""
    monkeypatch.setenv("PM_MIRROR_ENABLED", "")
    monkeypatch.delenv("DISPATCH_MODE", raising=False)
    yield


@pytest.fixture
def product_repo():
    return ProductRepository(MockCollection("products"))


@pytest.fixture
def variant_repo():
    return CatalogVariantRepository(MockCollection("catalog_variants"))


@pytest.fixture
def audit_repo():
    return AuditRepository(MockCollection("audit_logs"))


@pytest.fixture
def fake_db():
    return _FakeDb()


def _frame_attrs(**over) -> Dict[str, Any]:
    a = {"brand_name": "Burberry", "model_no": "B 3142", "colour_code": "1109/71"}
    a.update(over)
    return a


def _form_payload(**over) -> Dict[str, Any]:
    p = {
        "category": "FRAME",
        "brand": "Burberry",
        "model": "B 3142",
        "color": "1109/71",
        "mrp": 5000.0,
        "offer_price": 4500.0,
    }
    p.update(over)
    return p


def _landed_pim(fake_db: _FakeDb) -> Dict[str, Any]:
    """The single catalog_products doc that actually landed."""
    docs = fake_db.docs("catalog_products")
    assert len(docs) == 1, f"expected exactly 1 catalog_products doc, got {len(docs)}"
    return docs[0]


# ===========================================================================
# 1. Every door lands a catalog_products doc carrying its OWN sku
# ===========================================================================


@pytest.mark.parametrize("source", ["FORM", "BULK", "CATALOG", "IMPORT"])
def test_every_door_lands_pim_doc_with_top_level_sku(
    source, product_repo, variant_repo, audit_repo, fake_db, monkeypatch
):
    monkeypatch.setenv("PM_MIRROR_ENABLED", "1")
    created = pm.create_via_door(
        _form_payload(model=f"B {source}"),
        source=source,
        actor="u1",
        product_repo=product_repo,
        variant_repo=variant_repo,
        audit_repo=audit_repo,
        db=fake_db,
    )
    doc = _landed_pim(fake_db)
    assert doc.get("sku"), "the PIM row landed with NO sku -- the online guards no-op"
    assert doc["sku"] == created["sku"]
    # The PIM row IS the parent: its own sku and parent_sku agree.
    assert doc["sku"] == doc["parent_sku"]
    assert doc["id"] == created["pim_product_id"]


def test_master_door_create_product_directly_lands_sku(
    product_repo, variant_repo, audit_repo, fake_db, monkeypatch
):
    """routers/product_master.py calls pm.create_product DIRECTLY (not via
    create_via_door) with catalog_repo=None -- the real MASTER path."""
    monkeypatch.setenv("PM_MIRROR_ENABLED", "1")
    created = pm.create_product(
        category="FRAME",
        attributes=_frame_attrs(),
        mrp=1000,
        offer_price=900,
        actor="u1",
        product_repo=product_repo,
        catalog_repo=None,  # exactly what the router hardcodes
        variant_repo=variant_repo,
        audit_repo=audit_repo,
        db=fake_db,
    )
    doc = _landed_pim(fake_db)
    assert doc["sku"] == created["sku"]
    assert doc["sku"] == doc["parent_sku"]
    assert doc["sku"]


def test_pim_sku_present_when_mirror_flag_is_OFF(
    product_repo, variant_repo, audit_repo, fake_db, monkeypatch
):
    """The fix must NOT be flag-dependent. With the mirror OFF the only writer
    is _stage_catalog_draft -- its doc must carry the sku too, otherwise every
    normal deploy still writes skuless PIM rows."""
    monkeypatch.setenv("PM_MIRROR_ENABLED", "")
    assert pm.mirror_enabled() is False
    created = pm.create_via_door(
        _form_payload(),
        source="FORM",
        actor="u1",
        product_repo=product_repo,
        variant_repo=variant_repo,
        audit_repo=audit_repo,
        db=fake_db,
    )
    # The flag-gated mirror was skipped...
    targets = created["sync_status"]["targets"]
    assert targets["catalog_products"]["status"] == "SKIPPED"
    # ...but the ALWAYS-ON draft staging still landed a doc, WITH the sku.
    assert targets["catalog_draft"]["status"] == "OK"
    doc = _landed_pim(fake_db)
    assert doc["sku"] == created["sku"]
    assert doc["sku"] == doc["parent_sku"]


# ===========================================================================
# 2. A skuless spine is refused LOUDLY but never raises out of the mirror
# ===========================================================================


def test_write_mirror_records_FAILED_and_does_not_raise_without_sku(
    fake_db, monkeypatch
):
    """DEFENCE-IN-DEPTH, unreachable by construction in production: every spine
    reaching a writer went through normalise_payload, which ALWAYS sets a
    non-blank sku (product_master.py doc build, `"sku": resolved_sku`) -- there
    is no live path that hands _write_mirror a skuless spine. This pins the
    failure MODE if that invariant is ever broken, not a live risk:
    catalog_products.sku_1 is unique+sparse, an explicit null would insert once
    then DuplicateKeyError forever, so a skuless spine must be REFUSED -- but
    _write_mirror's contract is 'NEVER raises' (it runs after the spine is
    already committed), so the refusal has to surface as a FAILED target, not
    an exception."""
    monkeypatch.setenv("PM_MIRROR_ENABLED", "1")
    assert pm.mirror_enabled() is True
    spine = {"pim_product_id": "PIM-1", "category": "FRAME", "sku": None}

    targets = pm._write_mirror(spine, catalog_repo=None, variant_repo=None, db=fake_db)
    by_name = {t.name: t for t in targets}
    assert by_name["catalog_products"].status == "FAILED"
    # Nothing was written -- no null-sku row to poison the unique index.
    assert fake_db.docs("catalog_products") == []


def test_stage_catalog_draft_records_FAILED_and_does_not_raise_without_sku(fake_db):
    """DEFENCE-IN-DEPTH, unreachable by construction in production (see the
    _write_mirror twin above: normalise_payload always sets a sku). Pins the
    same failure MODE for the ALWAYS-ON staging writer: fail-soft FAILED
    target, no exception into the create, no skuless doc written."""
    spine = {"pim_product_id": "PIM-2", "category": "FRAME", "sku": "  "}
    target = pm._stage_catalog_draft(spine, catalog_repo=None, db=fake_db)
    assert target.name == "catalog_draft"
    assert target.status == "FAILED"
    assert fake_db.docs("catalog_products") == []


def test_build_pim_doc_is_pure_and_never_raises():
    """_build_pim_doc must stay PURE. If it raised on a missing sku, the raise
    would escape _write_mirror (the call sits OUTSIDE its try block) as a 500
    AFTER the spine row is committed, and would skip _stage_catalog_draft --
    leaving exactly the dangling pim_product_id that staging exists to prevent.
    """
    doc = pm._build_pim_doc({"pim_product_id": "PIM-3", "category": "FRAME"})
    assert doc["id"] == "PIM-3"
    assert doc["sku"] is None and doc["parent_sku"] is None
    # The precondition lives in the separate assertion helper instead.
    with pytest.raises(pm.ProductMasterError):
        pm._assert_pim_sku({"sku": None})
    pm._assert_pim_sku({"sku": "FRX-1"})  # does not raise


def test_write_mirror_db_path_lands_the_sku_on_an_existing_pim_doc(
    fake_db, monkeypatch
):
    """The MIRROR writer's db branch is observed actually landing the sku:
    pre-seed a catalog_products doc at the pim id, run _write_mirror with the
    mirror ON, and the mirrored doc carries sku == parent_sku == the spine sku.

    NOTE (pre-existing, out of scope here): _write_mirror's db branch uses a
    NO-upsert update_one and records OK even when it matched 0 docs -- a false
    OK for a doc that does not exist yet. The always-on _stage_catalog_draft
    upsert is what actually creates the doc; this test therefore pre-seeds."""
    monkeypatch.setenv("PM_MIRROR_ENABLED", "1")
    fake_db["catalog_products"].insert_one({"id": "PIM-MIRROR-1"})
    spine = {
        "pim_product_id": "PIM-MIRROR-1",
        "sku": "FRMIRROR9",
        "category": "FRAME",
        "attributes": {},
    }
    targets = pm._write_mirror(spine, catalog_repo=None, variant_repo=None, db=fake_db)
    by_name = {t.name: t for t in targets}
    assert by_name["catalog_products"].status == "OK"
    doc = fake_db["catalog_products"].find_one({"id": "PIM-MIRROR-1"})
    assert doc is not None
    assert doc["sku"] == "FRMIRROR9"
    assert doc["parent_sku"] == "FRMIRROR9"


# ===========================================================================
# 2b. P1: the mint consults catalog_products; a unique-index collision is a
#     NAMED failure, never a generic FAILED behind a 201
# ===========================================================================


def test_mint_unique_sku_consults_catalog_products(fake_db):
    """Prod holds sku-carrying catalog_products rows with NO products spine
    (SGRAYMETARW4006601 / SB5050 etc.) -- invisible to product_repo.find_by_sku.
    build_sku is deterministic, so a Gen-2-style create can mint exactly one of
    those strings. The mint must consult catalog_products too and take the
    existing '-<counter>' suffix path."""
    attrs = _frame_attrs()
    base = pm.build_sku("FRAME", attrs)
    fake_db["catalog_products"].insert_one({"id": "CAT-ONLY-1", "sku": base})
    minted = pm.mint_unique_sku("FRAME", attrs, product_repo=None, db=fake_db)
    assert minted != base
    assert minted.startswith(base + "-"), minted


def test_mint_unique_sku_without_db_behaves_as_before():
    """Fail-soft plumbing: no repo AND no db -> the deterministic base,
    untouched, no counter burn (what the bulk-row validator relies on)."""
    attrs = _frame_attrs()
    assert pm.mint_unique_sku("FRAME", attrs) == pm.build_sku("FRAME", attrs)


def test_mint_unique_sku_free_in_both_collections_returns_base(fake_db):
    attrs = _frame_attrs()
    base = pm.build_sku("FRAME", attrs)
    # catalog_products holds only an UNRELATED sku -> no collision.
    fake_db["catalog_products"].insert_one({"id": "CAT-OTHER", "sku": "FRSOMETHINGELSE"})
    assert pm.mint_unique_sku("FRAME", attrs, product_repo=None, db=fake_db) == base


class _DupRaisingCollection(_UpsertCollection):
    """update_one raises the REAL pymongo DuplicateKeyError, as the sku_1
    unique index would."""

    def update_one(self, filter: Dict, update: Dict, upsert: bool = False):  # noqa: A002
        from pymongo.errors import DuplicateKeyError

        raise DuplicateKeyError(
            "E11000 duplicate key error collection: ims_2_0.catalog_products "
            "index: sku_1"
        )


def _conflict_db() -> _FakeDb:
    db = _FakeDb()
    coll = _DupRaisingCollection("catalog_products")
    coll.insert_one({"id": "PIM-EXISTING", "sku": "FRCOLLIDE1"})
    db._colls["catalog_products"] = coll
    return db


def test_stage_catalog_draft_surfaces_FAILED_SKU_CONFLICT(caplog):
    """A DuplicateKeyError from the catalog write surfaces as the DISTINCT
    FAILED_SKU_CONFLICT target (not generic FAILED), with an ERROR log naming
    the colliding sku AND the existing row's id."""
    import logging as _logging

    db = _conflict_db()
    spine = {
        "pim_product_id": "PIM-NEW",
        "sku": "FRCOLLIDE1",
        "category": "FRAME",
        "attributes": {},
    }
    with caplog.at_level(_logging.ERROR, logger="api.services.product_master"):
        target = pm._stage_catalog_draft(spine, catalog_repo=None, db=db)
    assert target.name == "catalog_draft"
    assert target.status == "FAILED_SKU_CONFLICT"
    assert "FRCOLLIDE1" in (target.detail or "")
    assert "PIM-EXISTING" in (target.detail or "")
    assert "FRCOLLIDE1" in caplog.text
    assert "PIM-EXISTING" in caplog.text


def test_write_mirror_surfaces_FAILED_SKU_CONFLICT(monkeypatch, caplog):
    """Same distinct status from the flag-gated mirror writer."""
    import logging as _logging

    monkeypatch.setenv("PM_MIRROR_ENABLED", "1")
    db = _conflict_db()
    spine = {
        "pim_product_id": "PIM-NEW-2",
        "sku": "FRCOLLIDE1",
        "category": "FRAME",
        "attributes": {},
    }
    with caplog.at_level(_logging.ERROR, logger="api.services.product_master"):
        targets = pm._write_mirror(
            spine, catalog_repo=None, variant_repo=None, db=db
        )
    by_name = {t.name: t for t in targets}
    assert by_name["catalog_products"].status == "FAILED_SKU_CONFLICT"
    assert "FRCOLLIDE1" in caplog.text
    assert "PIM-EXISTING" in caplog.text


def test_create_door_survives_a_catalog_side_base_collision(
    product_repo, variant_repo, audit_repo, fake_db
):
    """End-to-end (the Gen-2 shape): the deterministic base sku already exists
    as a SPINELESS catalog_products row. The create must NOT dangle -- the mint
    suffixes, the create succeeds under the suffixed sku, and the staged draft
    lands under the new pim id."""
    attrs = _frame_attrs()
    base = pm.build_sku("FRAME", attrs)
    fake_db["catalog_products"].insert_one({"id": "CAT-ONLY-GEN2", "sku": base})
    created = pm.create_via_door(
        _form_payload(),
        source="FORM",
        actor="u1",
        product_repo=product_repo,
        variant_repo=variant_repo,
        audit_repo=audit_repo,
        db=fake_db,
    )
    assert created["sku"] != base
    assert created["sku"].startswith(base + "-")
    staged = fake_db["catalog_products"].find_one({"id": created["pim_product_id"]})
    assert staged is not None
    assert staged["sku"] == created["sku"]
    assert created["sync_status"]["targets"]["catalog_draft"]["status"] == "OK"


# ===========================================================================
# 3. THE P1 THE OWNER IS EXPOSED TO: the online-block guard can now match
# ===========================================================================


def test_pim_doc_is_matched_by_the_online_block_guard(fake_db):
    """A contractually-banned brand is blocked by flagging its collection
    online_sync_blocked=True. `online_block._membership_hit` reads
    `product.get("sku")` and returns False the moment it is falsy -- so before
    this fix a PM-built PIM doc could NEVER be blocked, and a banned product
    would sail through to Shopify."""
    doc = pm._build_pim_doc(
        {
            "pim_product_id": "PIM-BLOCK",
            "sku": "FRBANNEDBRANDX1",
            "category": "FRAME",
            "attributes": {},
        }
    )
    fake_db["ecom_collections"].insert_one(
        {
            "collection_id": "COL-BANNED",
            "collection_type": "CUSTOM",
            "online_sync_blocked": True,
            "products": [{"sku": "FRBANNEDBRANDX1", "position": 0}],
        }
    )
    assert online_block.is_blocked_from_online(doc, fake_db) is True
    # ...and the strict (push-path) classifier agrees.
    assert online_block.is_blocked_from_online_strict(doc, fake_db) is True


def test_skuless_pim_doc_cannot_be_blocked_which_is_why_sku_is_required(fake_db):
    """The regression guard, stated as the defect: strip the sku and the SAME
    banned product becomes unblockable. This is exactly what 53 prod rows
    looked like."""
    doc = pm._build_pim_doc(
        {"pim_product_id": "PIM-X", "sku": "FRBANNEDBRANDX1", "category": "FRAME"}
    )
    doc.pop("sku")  # the pre-fix shape: only parent_sku
    fake_db["ecom_collections"].insert_one(
        {
            "collection_id": "COL-BANNED",
            "collection_type": "CUSTOM",
            "online_sync_blocked": True,
            "products": [{"sku": "FRBANNEDBRANDX1", "position": 0}],
        }
    )
    assert online_block.is_blocked_from_online(doc, fake_db) is False


def test_blocked_sku_batch_classifier_matches_the_landed_pim_sku(
    product_repo, variant_repo, audit_repo, fake_db, monkeypatch
):
    """The block classifier, keyed on the LANDED sku, matches. NOTE the honest
    scope: this REIMPLEMENTS the push-sweep's `dirty_skus` construction
    (`doc.get("sku")` -> classify_blocked_skus) rather than calling the sweep
    itself -- it pins the classifier's contract on the landed doc shape, not
    the sweep's end-to-end wiring."""
    monkeypatch.setenv("PM_MIRROR_ENABLED", "1")
    created = pm.create_via_door(
        _form_payload(),
        source="FORM",
        actor="u1",
        product_repo=product_repo,
        variant_repo=variant_repo,
        audit_repo=audit_repo,
        db=fake_db,
    )
    doc = _landed_pim(fake_db)
    fake_db["ecom_collections"].insert_one(
        {
            "collection_id": "COL-BANNED",
            "collection_type": "CUSTOM",
            "online_sync_blocked": True,
            "products": [{"sku": created["sku"], "position": 0}],
        }
    )
    dirty_skus = [doc.get("sku")] if doc.get("sku") else []
    assert dirty_skus, "the sweep would not even see this product"
    blocked_set, verifiable = online_block.classify_blocked_skus(fake_db, dirty_skus)
    assert verifiable is True
    assert doc.get("sku") in blocked_set


# ===========================================================================
# 3b. R3-4: FAILED_SKU_CONFLICT must be diagnosable OUTSIDE the Railway log
# ===========================================================================
# sync_status used to be returned by exactly ONE route (routers/product_master
# .py, the MASTER door). The FORM door returned {product_id, sku} and BULK
# returned {index, ok, errors, sku, product_id} -- so the failure this branch
# added FAILED_SKU_CONFLICT to surface still presented to the cataloguer as a
# clean 201 over a spine row whose pim_product_id points at a catalog doc that
# was never created: a product that can NEVER be pushed online, evidenced by one
# ERROR line in a log nobody is watching. The fix is ADDITIVE (nothing renamed).
#
# The blip modelled here is the one the mint CANNOT prevent: _catalog_sku_taken
# is deliberately fail-soft, so a catalog read that returns nothing while the
# unique index still rejects the write re-opens the path even post-mint-fix.


class _BlindDupCollection(_UpsertCollection):
    """catalog_products whose READ says "free" but whose WRITE is rejected by
    the sku_1 unique index -- exactly the fail-soft-read blip that survives the
    mint fix (find_one is what _catalog_sku_taken consults)."""

    def find_one(self, filter: Dict, *a, **k):  # noqa: A002
        if "sku" in (filter or {}):
            return None
        return super().find_one(filter, *a, **k)

    def update_one(self, filter: Dict, update: Dict, upsert: bool = False):  # noqa: A002
        from pymongo.errors import DuplicateKeyError

        raise DuplicateKeyError(
            "E11000 duplicate key error collection: ims_2_0.catalog_products "
            "index: sku_1"
        )


class _RouterDb(_FakeDb):
    """_FakeDb + the `is_connected` flag the router branches on."""

    is_connected = True


@pytest.fixture
def router_world(monkeypatch):
    """Wire the FORM + BULK product routes onto in-memory storage whose
    catalog_products rejects every write. Returns (repo, db)."""
    import api.dependencies as deps
    import api.routers.products as products_router

    db = _RouterDb()
    db._colls["catalog_products"] = _BlindDupCollection("catalog_products")
    repo = ProductRepository(db.get_collection("products"))
    audit_repo = AuditRepository(db.get_collection("audit_logs"))

    monkeypatch.setattr(deps, "DATABASE_AVAILABLE", True, raising=False)
    monkeypatch.setattr(deps, "get_db", lambda: db)
    monkeypatch.setattr(deps, "get_audit_repository", lambda: audit_repo)
    monkeypatch.setattr(products_router, "get_product_repository", lambda: repo)
    return repo, db


_ADMIN = {"user_id": "u1", "username": "admin", "roles": ["ADMIN"]}


def test_form_door_response_surfaces_FAILED_SKU_CONFLICT(router_world):
    """R3-4: the FORM door's 201 must SAY the catalog mirror did not land."""
    import asyncio

    from api.routers.products import ProductCreate, create_product

    created = asyncio.run(
        create_product(
            ProductCreate(**_form_payload()),
            _ADMIN,
        )
    )
    assert created["product_id"] and created["sku"]  # contract UNCHANGED
    targets = (created.get("sync_status") or {}).get("targets") or {}
    assert targets, "the FORM door tells the cataloguer nothing about the mirror"
    assert targets["catalog_draft"]["status"] == "FAILED_SKU_CONFLICT", targets


def test_bulk_door_row_surfaces_FAILED_SKU_CONFLICT(router_world):
    """Same for each BULK row: ok:true is not the whole truth."""
    import asyncio

    from api.routers.products import BulkCreateRequest, bulk_create_products

    body = BulkCreateRequest(products=[_form_payload()])
    res = asyncio.run(bulk_create_products(body, _ADMIN))

    row = res["results"][0]
    assert row["ok"] is True and row["sku"]  # contract UNCHANGED
    targets = (row.get("sync_status") or {}).get("targets") or {}
    assert targets, "the BULK row tells the cataloguer nothing about the mirror"
    assert targets["catalog_draft"]["status"] == "FAILED_SKU_CONFLICT", targets


# ===========================================================================
# 4. The backfill script's PURE CORE (no live Mongo)
# ===========================================================================


class _BfCursor(list):
    pass


class _BfColl:
    """Minimal find/find_one/count_documents/update_one/index_information fake
    supporting exactly the predicates the backfill issues."""

    def __init__(self, rows=None, index_info=None):
        self.rows = [dict(r) for r in (rows or [])]
        self._index_info = index_info if index_info is not None else {
            "sku_1": {"key": [("sku", 1)], "unique": True, "sparse": True}
        }
        self.write_calls: List[Any] = []

    # -- matching ---------------------------------------------------------
    def _match(self, row, query) -> bool:
        for key, cond in (query or {}).items():
            if key == "$or":
                if not any(self._match(row, c) for c in cond):
                    return False
                continue
            present = key in row
            val = row.get(key)
            if isinstance(cond, dict):
                if "$exists" in cond and bool(cond["$exists"]) != present:
                    return False
                if "$in" in cond:
                    # Mongo semantics: a MISSING field matches $in [None, ...].
                    probe = val if present else None
                    if probe not in cond["$in"]:
                        return False
                if "$nin" in cond:
                    probe = val if present else None
                    if probe in cond["$nin"]:
                        return False
            elif val != cond:
                return False
        return True

    def find(self, query=None, *_a, **_k):
        return _BfCursor(dict(r) for r in self.rows if self._match(r, query or {}))

    def find_one(self, query=None, *_a, **_k):
        for r in self.rows:
            if self._match(r, query or {}):
                return dict(r)
        return None

    def count_documents(self, query=None, *_a, **_k):
        return sum(1 for r in self.rows if self._match(r, query or {}))

    def index_information(self):
        return dict(self._index_info)

    def update_one(self, filter, update, *_a, **_k):  # noqa: A002
        self.write_calls.append((filter, update))
        for r in self.rows:
            if self._match(r, filter or {}):
                r.update(dict(update.get("$set") or {}))
                return type("obj", (object,), {"modified_count": 1})()
        return type("obj", (object,), {"modified_count": 0})()


def _bf_world(n: int = 3):
    """A CLEAN world of n repairable rows, all six gates satisfied."""
    catalog_rows = [
        {"id": f"PIM-{i}", "parent_sku": f"FRSKU{i}", "ecom": {"status": "DRAFT"}}
        for i in range(n)
    ]
    catalog_rows.append({"id": "PIM-OK", "parent_sku": "FROK", "sku": "FROK"})
    products_rows = [
        {"pim_product_id": f"PIM-{i}", "sku": f"FRSKU{i}"} for i in range(n)
    ]
    variant_rows = [
        {"parent_product_id": f"PIM-{i}", "sku": f"FRSKU{i}"} for i in range(n)
    ]
    return (
        _BfColl(catalog_rows),
        _BfColl(products_rows),
        _BfColl(variant_rows),
    )


def test_backfill_selects_both_missing_key_and_explicit_null():
    """ONE shared predicate for selection, the write filter and the verify. An
    `$exists:false`-only predicate would silently skip a `sku: null` row on the
    write while still reporting PASS."""
    catalog = _BfColl(
        [
            {"id": "A", "parent_sku": "S-A"},  # key absent
            {"id": "B", "parent_sku": "S-B", "sku": None},  # explicit null
            {"id": "C", "parent_sku": "S-C", "sku": ""},  # empty string
            {"id": "D", "parent_sku": "S-D", "sku": "S-D"},  # already fine
        ]
    )
    ids = [d["id"] for d in bf.select_targets(catalog)]
    assert ids == ["A", "B", "C"]


def test_backfill_clean_set_applies_exactly_n():
    catalog, products, variants = _bf_world(3)
    targets = bf.select_targets(catalog)
    assert len(targets) == 3
    bf.run_gates(targets, catalog, products, variants)  # must not raise
    modified_ids, total = bf.apply_repairs(catalog, targets)
    assert total == len(targets) == 3
    assert modified_ids == ["PIM-0", "PIM-1", "PIM-2"]
    for i in range(3):
        assert catalog.find_one({"id": f"PIM-{i}"})["sku"] == f"FRSKU{i}"
    # The already-fine row was never touched.
    assert catalog.find_one({"id": "PIM-OK"})["sku"] == "FROK"


def test_backfill_second_run_is_idempotent_applies_zero():
    catalog, products, variants = _bf_world(3)
    targets = bf.select_targets(catalog)
    bf.run_gates(targets, catalog, products, variants)
    bf.apply_repairs(catalog, targets)
    # Second run: the SAME predicate now selects nothing.
    assert bf.select_targets(catalog) == []
    again, total = bf.apply_repairs(catalog, bf.select_targets(catalog))
    assert (again, total) == ([], 0)
    assert catalog.count_documents(bf.TARGET_PREDICATE) == 0


def test_backfill_never_touches_ecom():
    """Only `sku` is set -- ecom (and therefore locally_modified) must be
    untouched, so the repair can never enqueue a Shopify push."""
    catalog, products, variants = _bf_world(2)
    targets = bf.select_targets(catalog)
    bf.run_gates(targets, catalog, products, variants)
    bf.apply_repairs(catalog, targets)
    for _filter, update in catalog.write_calls:
        assert set(update.keys()) == {"$set"}
        assert set(update["$set"].keys()) == {"sku"}
    assert catalog.find_one({"id": "PIM-0"})["ecom"] == {"status": "DRAFT"}


@pytest.mark.parametrize(
    "gate,mutate",
    [
        # G1: a blank parent_sku
        ("G1", lambda c, p, v: c.rows[0].update({"parent_sku": "   "})),
        # G2: two targets that would be repaired to the SAME sku
        ("G2", lambda c, p, v: c.rows[1].update({"parent_sku": "FRSKU0"})),
        # G3: the repair value collides with an EXISTING catalog sku
        ("G3", lambda c, p, v: c.rows[-1].update({"sku": "FRSKU0"})),
        # G4: no products spine row for this pim id
        ("G4", lambda c, p, v: p.rows.pop(0)),
        # G5: the spine disagrees about the sku
        ("G5", lambda c, p, v: p.rows[0].update({"sku": "SOMETHING-ELSE"})),
        # G6a: two variants -> the parent's identity is ambiguous
        (
            "G6",
            lambda c, p, v: v.rows.append(
                {"parent_product_id": "PIM-0", "sku": "FRSKU0-B"}
            ),
        ),
        # G6b: the single variant's sku disagrees with parent_sku
        ("G6", lambda c, p, v: v.rows[0].update({"sku": "MISMATCH"})),
        # G6c: no variant at all
        ("G6", lambda c, p, v: v.rows.pop(0)),
    ],
)
def test_backfill_each_gate_detects_and_names_its_corruption(gate, mutate):
    """Each corruption raises GateFailure classified as the RIGHT gate, with
    offenders attached. HONEST SCOPE: run_gates only ever READS, so asserting
    'zero writes' here would be tautological -- the actual zero-writes-on-gate-
    failure guarantee is main()'s ordering (gates run before apply_repairs),
    pinned end-to-end by test_backfill_main_gate_failure_exits_4_zero_writes
    below."""
    catalog, products, variants = _bf_world(3)
    mutate(catalog, products, variants)
    targets = bf.select_targets(catalog)
    with pytest.raises(bf.GateFailure) as ei:
        bf.run_gates(targets, catalog, products, variants)
    assert ei.value.gate == gate
    assert ei.value.offenders


def test_backfill_fingerprint_must_match_before_any_write():
    catalog, products, _variants = _bf_world(3)
    # Wrong db name, wrong counts.
    problems = bf.check_fingerprint("some_other_db", catalog, products)
    assert any("db name" in p for p in problems)
    assert any("catalog_products count" in p for p in problems)
    assert any("products count" in p for p in problems)


def test_backfill_fingerprint_rejects_a_non_unique_or_non_sparse_index():
    """The index shape is not cosmetic: `sparse` is why 53 key-absent rows can
    coexist, and `unique` is why an explicit null would collide forever."""
    catalog = _BfColl([], index_info={"sku_1": {"unique": False, "sparse": True}})
    problems = bf.check_fingerprint(bf.EXPECTED_DB_NAME, catalog, _BfColl([]))
    assert any("not unique" in p for p in problems)

    catalog = _BfColl([], index_info={"sku_1": {"unique": True, "sparse": False}})
    problems = bf.check_fingerprint(bf.EXPECTED_DB_NAME, catalog, _BfColl([]))
    assert any("not sparse" in p for p in problems)

    catalog = _BfColl([], index_info={})
    problems = bf.check_fingerprint(bf.EXPECTED_DB_NAME, catalog, _BfColl([]))
    assert any("MISSING" in p for p in problems)


def test_backfill_write_filter_carries_the_shared_predicate():
    """The per-row write filter is {id} ANDed with the SAME no-sku predicate,
    so a row that gained a sku between selection and write is a no-op (counted
    as a shortfall) rather than a clobber."""
    catalog, _products, _variants = _bf_world(1)
    targets = bf.select_targets(catalog)
    # Simulate a concurrent writer filling the sku in.
    catalog.rows[0]["sku"] = "SOMEONE-ELSE-WON"
    modified_ids, total = bf.apply_repairs(catalog, targets)
    assert (modified_ids, total) == ([], 0)
    assert catalog.find_one({"id": "PIM-0"})["sku"] == "SOMEONE-ELSE-WON"
    write_filter = catalog.write_calls[0][0]
    assert write_filter["id"] == "PIM-0"
    assert write_filter["$or"] == bf.TARGET_PREDICATE["$or"]


def test_backfill_resolve_uri_fails_loud_without_env(monkeypatch):
    """No DatabaseConfig.from_env() fallback -- that helper silently defaults to
    localhost/ims_2_0 and would APPLY to a local mongo while printing success."""
    for key in ("MONGODB_URL", "MONGO_PUBLIC_URL", "MONGO_URL"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError):
        bf.resolve_uri()
    monkeypatch.setenv("MONGO_PUBLIC_URL", "mongodb://h:1/x")
    assert bf.resolve_uri() == "mongodb://h:1/x"


def _bf_prod_world():
    """The EXACT prod fingerprint shape as RE-verified live 2026-07-29 (after
    the latent-defects repair created spines for 5 of the 6 spineless
    strays): 59 catalog rows = 53 skuless targets + 1 sku-carrying row with
    NO spine (the remaining SGRAYMETARW-style stray) + 5 sku-carrying rows
    WITH spines; 58 products spine rows; 53 variants. Gap = 1, targets = 53.
    """
    catalog_rows = [
        {
            "id": f"PIM-{i:02d}",
            "parent_sku": f"FRSKU{i:02d}",
            "ecom": {"status": "DRAFT"},
        }
        for i in range(53)
    ]
    catalog_rows.append({"id": "CAT-ONLY-0", "sku": "SGSTRAY0"})
    catalog_rows += [{"id": f"PROMOTED-{j}", "sku": f"SGPROM{j}"} for j in range(5)]
    products_rows = [
        {"pim_product_id": f"PIM-{i:02d}", "sku": f"FRSKU{i:02d}"} for i in range(53)
    ]
    products_rows += [
        {"pim_product_id": f"PROMOTED-{j}", "sku": f"SGPROM{j}"} for j in range(5)
    ]
    variant_rows = [
        {"parent_product_id": f"PIM-{i:02d}", "sku": f"FRSKU{i:02d}"}
        for i in range(53)
    ]
    return _BfColl(catalog_rows), _BfColl(products_rows), _BfColl(variant_rows)


def test_backfill_fingerprint_growth_tolerant_but_target_count_exact():
    """P2 (counts freeze): exact 59/53 would brick the script on the FIRST
    ordinary product create after deploy (it adds a row to BOTH collections).
    The floors are >=, while the no-sku TARGET count (== 53) and the
    (catalog - products) gap (== 1, re-verified live after the latent-defects
    repair moved it 6 -> 1) stay EXACT unless --expect-targets /
    --expect-gap record a re-verified change."""
    catalog, products, _variants = _bf_prod_world()
    # Exact prod shape passes.
    assert bf.check_fingerprint(bf.EXPECTED_DB_NAME, catalog, products) == []
    # Post-deploy growth: two creates add one row to BOTH collections each.
    for i in range(2):
        catalog.rows.append(
            {"id": f"NEW-{i}", "sku": f"FRNEW{i}", "parent_sku": f"FRNEW{i}"}
        )
        products.rows.append({"pim_product_id": f"NEW-{i}", "sku": f"FRNEW{i}"})
    assert bf.check_fingerprint(bf.EXPECTED_DB_NAME, catalog, products) == []
    # A CHANGED target set aborts... (a 54th skuless row appears, WITH spine
    # so the gap is untouched)
    catalog.rows.append({"id": "T-NEW", "parent_sku": "FRTNEW"})
    products.rows.append({"pim_product_id": "T-NEW", "sku": "FRTNEW"})
    problems = bf.check_fingerprint(bf.EXPECTED_DB_NAME, catalog, products)
    assert any("target" in p.lower() for p in problems)
    # ...unless --expect-targets records the verified new count.
    assert (
        bf.check_fingerprint(bf.EXPECTED_DB_NAME, catalog, products, expect_targets=54)
        == []
    )
    # The gap check stays HARD: a catalog-only row (no spine) breaks == 1...
    catalog.rows.append({"id": "STRAY-NEW", "sku": "SGSTRAYNEW"})
    problems = bf.check_fingerprint(
        bf.EXPECTED_DB_NAME, catalog, products, expect_targets=54
    )
    assert any("gap" in p.lower() for p in problems)
    # ...and --expect-gap is the explicit, re-verified way through (e.g. the
    # last stray gaining a spine would move the gap 1 -> 0).
    assert (
        bf.check_fingerprint(
            bf.EXPECTED_DB_NAME, catalog, products, expect_targets=54, expect_gap=2
        )
        == []
    )


def test_backfill_fingerprint_db_name_and_index_shape_stay_hard():
    """The db name + sku_1 unique+sparse assertions were NOT loosened."""
    catalog, products, _variants = _bf_prod_world()
    assert any(
        "db name" in p for p in bf.check_fingerprint("wrong_db", catalog, products)
    )
    bad_index = _BfColl(
        [dict(r) for r in catalog.rows],
        index_info={"sku_1": {"unique": False, "sparse": True}},
    )
    assert any(
        "not unique" in p
        for p in bf.check_fingerprint(bf.EXPECTED_DB_NAME, bad_index, products)
    )


# ===========================================================================
# 5. bf.main() end-to-end against injected fakes (P1: main was untested)
# ===========================================================================


class _BfFakeMongoDb:
    def __init__(self, name: str, colls: Dict[str, Any]):
        self.name = name
        self._colls = colls

    def command(self, *_a, **_k):
        return {"ok": 1}

    def __getitem__(self, name: str):
        return self._colls.get(name, _BfColl([]))


class _BfFakeMongoClient:
    """Stands in for pymongo.MongoClient: returns the SAME fake db whatever
    name the uri carries (main derives the db name from the uri, so the test
    controls it via the MONGO_PUBLIC_URL it sets)."""

    _colls: Dict[str, Any] = {}

    def __init__(self, *_a, **_k):
        pass

    def __getitem__(self, name: str):
        return _BfFakeMongoDb(name, type(self)._colls)

    def close(self):
        pass


@pytest.fixture
def bf_main_world(monkeypatch, tmp_path):
    """Wire bf.main() to a clean full-prod-shape fake world. Returns
    (catalog, products, variants, audit_path, run) where run(argv) invokes
    main with the audit path appended and returns the exit code."""
    import json as _json

    catalog, products, variants = _bf_prod_world()
    _BfFakeMongoClient._colls = {
        "catalog_products": catalog,
        "products": products,
        "catalog_variants": variants,
    }
    monkeypatch.setattr(bf, "MongoClient", _BfFakeMongoClient)
    monkeypatch.setenv("MONGO_PUBLIC_URL", "mongodb://h:1/ims_2_0")
    monkeypatch.delenv("MONGODB_URL", raising=False)
    monkeypatch.delenv("MONGO_URL", raising=False)
    audit_path = tmp_path / "audit.json"

    def run(argv):
        return bf.main(list(argv) + ["--audit-path", str(audit_path)])

    def read_audit():
        assert audit_path.exists(), "the audit file must be written on EVERY path"
        with open(audit_path, encoding="ascii") as fh:
            return _json.load(fh)

    return catalog, products, variants, run, read_audit


def test_backfill_main_dry_run_writes_nothing_exit_0(bf_main_world):
    catalog, _products, _variants, run, read_audit = bf_main_world
    assert run([]) == 0
    assert catalog.write_calls == []
    audit = read_audit()
    assert audit["outcome"] == "DRY-RUN-COMPLETE"
    assert audit["mode"] == "DRY-RUN"
    assert audit["target_count"] == 53
    assert audit["modified_count"] == 0


def test_backfill_main_apply_without_deploy_flag_refused_exit_6(bf_main_world):
    """P1 (deploy ordering): --apply alone is REFUSED before any connection --
    if the 53 rows gain a sku while the OLD online_catalog.py is deployed,
    sellable_online flips True->False for the 27 live Ray-Ban Meta SKUs and
    the oversell alarm goes silent."""
    catalog, _products, _variants, run, read_audit = bf_main_world
    assert run(["--apply"]) == 6
    assert catalog.write_calls == []
    audit = read_audit()
    assert audit["outcome"] == "ABORTED-DEPLOY-GATE"
    assert audit["modified_count"] == 0


def test_backfill_main_apply_with_deploy_flag_completes_exit_0(bf_main_world):
    catalog, _products, _variants, run, read_audit = bf_main_world
    assert run(["--apply", "--code-is-deployed"]) == 0
    audit = read_audit()
    assert audit["outcome"] == "COMPLETE"
    assert audit["modified_count"] == 53
    assert len(audit["modified_ids"]) == 53
    # Every target really carries its parent_sku now; the strays are untouched.
    assert catalog.count_documents(bf.TARGET_PREDICATE) == 0
    assert catalog.find_one({"id": "PIM-00"})["sku"] == "FRSKU00"
    assert catalog.find_one({"id": "CAT-ONLY-0"})["sku"] == "SGSTRAY0"


def test_backfill_main_fingerprint_mismatch_exit_3_zero_writes(
    bf_main_world, monkeypatch
):
    catalog, _products, _variants, run, read_audit = bf_main_world
    # Point the uri at a WRONG db name -- the fake client hands back the same
    # collections under that name, so ONLY the db-name assertion trips.
    monkeypatch.setenv("MONGO_PUBLIC_URL", "mongodb://h:1/wrong_db")
    assert run(["--apply", "--code-is-deployed"]) == 3
    assert catalog.write_calls == []
    audit = read_audit()
    assert audit["outcome"] == "ABORTED-FINGERPRINT"
    assert audit["modified_count"] == 0


def test_backfill_main_gate_failure_exits_4_zero_writes(bf_main_world):
    """THE ordering guarantee the reworded gate test above defers to: a gate
    failure inside main() aborts BEFORE apply_repairs -- zero writes."""
    catalog, products, _variants, run, read_audit = bf_main_world
    products.rows[0]["sku"] = "DISAGREES"  # breaks G5 for PIM-00
    assert run(["--apply", "--code-is-deployed"]) == 4
    assert catalog.write_calls == []
    audit = read_audit()
    assert audit["outcome"] == "ABORTED-GATE-G5"
    assert audit["modified_count"] == 0


class _BfRefusingColl(_BfColl):
    """update_one reports modified_count=0 for ONE id (a row a concurrent
    writer stole), everything else behaves normally."""

    refuse_id = "PIM-52"

    def update_one(self, filter, update, *_a, **_k):  # noqa: A002
        if (filter or {}).get("id") == self.refuse_id:
            self.write_calls.append((filter, update))
            return type("obj", (object,), {"modified_count": 0})()
        return super().update_one(filter, update)


def test_backfill_main_write_shortfall_exit_5_outcome_partial(bf_main_world):
    catalog, _products, _variants, run, read_audit = bf_main_world
    refusing = _BfRefusingColl([dict(r) for r in catalog.rows])
    _BfFakeMongoClient._colls["catalog_products"] = refusing
    assert run(["--apply", "--code-is-deployed"]) == 5
    audit = read_audit()
    assert audit["outcome"] == "PARTIAL@PIM-51"
    assert audit["modified_count"] == 52
    assert "PIM-52" not in audit["modified_ids"]


class _BfRaisingColl(_BfColl):
    """update_one RAISES on the Nth call -- the realistic live failure is a
    transient pymongo AutoReconnect / NetworkTimeout partway through 53 writes
    over the Railway proxy, NOT a duplicate key (G3 proves 0 intersection)."""

    raise_on_call = 3

    def __init__(self, rows=None, index_info=None):
        super().__init__(rows, index_info)
        self.update_calls = 0

    def update_one(self, filter, update, *_a, **_k):  # noqa: A002
        self.update_calls += 1
        if self.update_calls == self.raise_on_call:
            raise RuntimeError("AutoReconnect: connection pool paused")
        return super().update_one(filter, update)


def test_backfill_main_write_raising_mid_loop_audits_the_ids_that_LANDED(
    bf_main_world,
):
    """R3-1: THE AUDIT FILE MUST NOT REPORT ZERO WRITES AFTER A PARTIAL APPLY.

    `ids, n = apply_repairs(...)` never completes its assignment when the callee
    raises mid-loop, so the audit-writing `finally` used to serialise the
    untouched initial values: rows P0/P1 WERE written, yet the audit said
    modified_ids [] / modified_count 0 / outcome ABORTED-EXCEPTION -- flatly
    contradicting the module docstring's promise of "the ORDERED ids actually
    modified, always". Progress is now recorded INSIDE the write loop."""
    catalog, _products, _variants, run, read_audit = bf_main_world
    raising = _BfRaisingColl([dict(r) for r in catalog.rows])
    _BfFakeMongoClient._colls["catalog_products"] = raising

    assert run(["--apply", "--code-is-deployed"]) == 5

    audit = read_audit()
    # The 3rd write raised; the first TWO landed and the audit NAMES them.
    assert audit["modified_ids"] == ["PIM-00", "PIM-01"], audit["modified_ids"]
    assert audit["modified_count"] == 2
    assert audit["outcome"] == "PARTIAL-EXCEPTION@PIM-01", audit["outcome"]
    assert "AutoReconnect" in str(audit["error"])
    # ...and the audit is not lying in the other direction either: those two
    # rows really do carry their sku now, and the failed one does not.
    assert raising.find_one({"id": "PIM-00"})["sku"] == "FRSKU00"
    assert raising.find_one({"id": "PIM-01"})["sku"] == "FRSKU01"
    assert "sku" not in raising.find_one({"id": "PIM-02"})


def test_backfill_apply_repairs_records_progress_into_the_live_audit_dict():
    """Unit-level companion: the audit dict is updated DURING the loop, so a
    caller that never gets the return value still has the landed ids."""
    catalog, products, variants = _bf_world(3)
    targets = bf.select_targets(catalog)
    bf.run_gates(targets, catalog, products, variants)
    audit: Dict[str, Any] = {"modified_ids": [], "modified_count": 0}
    bf.apply_repairs(catalog, targets, audit=audit)
    assert audit["modified_ids"] == ["PIM-0", "PIM-1", "PIM-2"]
    assert audit["modified_count"] == 3
