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
    """catalog_products.sku_1 is unique+sparse: an explicit null would insert
    once then DuplicateKeyError forever, swallowed into a FAILED sync_status.
    So a skuless spine must be REFUSED -- but _write_mirror's contract is
    'NEVER raises' (it runs after the spine is already committed), so the
    refusal has to surface as a FAILED target, not an exception."""
    monkeypatch.setenv("PM_MIRROR_ENABLED", "1")
    assert pm.mirror_enabled() is True
    spine = {"pim_product_id": "PIM-1", "category": "FRAME", "sku": None}

    targets = pm._write_mirror(spine, catalog_repo=None, variant_repo=None, db=fake_db)
    by_name = {t.name: t for t in targets}
    assert by_name["catalog_products"].status == "FAILED"
    # Nothing was written -- no null-sku row to poison the unique index.
    assert fake_db.docs("catalog_products") == []


def test_stage_catalog_draft_records_FAILED_and_does_not_raise_without_sku(fake_db):
    """Same contract for the ALWAYS-ON staging writer: fail-soft FAILED target,
    no exception into the create, no skuless doc written."""
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
    """End-to-end shape of the push-sweep guard: `dirty_skus` is built from
    `doc.get("sku")` and re-checked with `doc.get("sku") in blocked_set`."""
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
def test_backfill_each_gate_aborts_with_zero_writes(gate, mutate):
    catalog, products, variants = _bf_world(3)
    mutate(catalog, products, variants)
    targets = bf.select_targets(catalog)
    with pytest.raises(bf.GateFailure) as ei:
        bf.run_gates(targets, catalog, products, variants)
    assert ei.value.gate == gate
    assert ei.value.offenders
    # Gates run BEFORE any write -- nothing was written.
    assert catalog.write_calls == []
    assert catalog.count_documents(bf.TARGET_PREDICATE) == len(targets)


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
