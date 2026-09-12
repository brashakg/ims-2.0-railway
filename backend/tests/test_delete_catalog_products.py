"""
Hard-deleting catalogue products so the same item can be re-added
================================================================
scripts/delete_catalog_products.py is the recorded, scoped version of the
2026-09-07 catalogue reset. It exists because the app's Delete button is a
SOFT delete and the `identity_key` unique index on `products` is not filtered
by `is_active`, so a soft-deleted product keeps owning its brand+model+colour
+size identity and Add-a-Product 409s on the re-add.

Discriminating power (each test goes red when its rule is removed):

  * the spine is found through ALL THREE arms -- shared product_id, the
    door-created `pim_product_id`, and the shared sku. Most live products are
    door-created, so dropping the pim arm strands every spine.
  * the blast radius is gathered under BOTH ids (twin id and the different
    spine product_id) and the sku, because catalog_variants /
    product_images / collection_products were written against one or the
    other over the years.
  * every refusal: on Shopify, on an order line, and a stock unit that is not
    plain AVAILABLE stock -- including an UNKNOWN status, which must refuse
    rather than be assumed safe.
  * refusals are batch-wide: one bad product stops the whole run, so a clean
    product beside it is NOT deleted.
  * the snapshot holds every document the delete removes (round-trip), and is
    written BEFORE the first delete.
  * a dry run writes nothing; --expect guards the count; a neighbouring
    product is never touched.

The fake DB below is deliberately its own thing rather than the app's
MockCollection: the script's order guard queries the DOTTED path
`items.product_id`, which MockCollection does not model (it would silently
match nothing and the guard would look like it worked while doing nothing).

No emoji (Windows cp1252).
"""

import copy
import json
import os
import sys
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))

import pytest  # noqa: E402

import delete_catalog_products as script  # noqa: E402


# ---------------------------------------------------------------------------
# A minimal fake Mongo: equality, $or, $in, and DOTTED paths into a list of
# sub-documents (what `items.product_id` means to a real server).
# ---------------------------------------------------------------------------

def _values_at(doc: Any, path: str) -> List[Any]:
    """Every value reachable at a dotted path, walking into lists the way
    Mongo does. Returns [] when the path is absent."""
    current: List[Any] = [doc]
    for segment in path.split("."):
        nxt: List[Any] = []
        for item in current:
            if isinstance(item, list):
                for element in item:
                    if isinstance(element, dict) and segment in element:
                        nxt.append(element[segment])
            elif isinstance(item, dict) and segment in item:
                nxt.append(item[segment])
        current = nxt
    return current


class FakeCollection:
    def __init__(self) -> None:
        self.docs: List[Dict[str, Any]] = []
        self._next_id = 1

    def insert_one(self, doc: Dict[str, Any]) -> None:
        stored = dict(doc)
        if "_id" not in stored:
            stored["_id"] = f"oid{self._next_id}"
            self._next_id += 1
        self.docs.append(stored)

    def _matches(self, doc: Dict[str, Any], query: Optional[Dict[str, Any]]) -> bool:
        if not query:
            return True
        for key, expected in query.items():
            if key == "$or":
                if not any(self._matches(doc, clause) for clause in expected):
                    return False
                continue
            actual = _values_at(doc, key)
            if isinstance(expected, dict) and "$in" in expected:
                if not any(value in expected["$in"] for value in actual):
                    return False
            elif expected not in actual:
                return False
        return True

    def find(self, query: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        return [copy.deepcopy(d) for d in self.docs if self._matches(d, query)]

    def find_one(self, query: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        found = self.find(query)
        return found[0] if found else None

    def count_documents(self, query: Optional[Dict[str, Any]] = None) -> int:
        return len(self.find(query))

    def delete_many(self, query: Optional[Dict[str, Any]] = None) -> Any:
        keep = [d for d in self.docs if not self._matches(d, query)]
        removed = len(self.docs) - len(keep)
        self.docs = keep
        return type("obj", (object,), {"deleted_count": removed})()


class FakeDB:
    def __init__(self) -> None:
        self.collections: Dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection())

    def seed(self, name: str, docs: List[Dict[str, Any]]) -> None:
        for doc in docs:
            self[name].insert_one(doc)

    def ids(self, name: str, field: str) -> List[Any]:
        return [d.get(field) for d in self[name].docs]


TWIN_ID = "prod_twin000001"
SPINE_ID = "prod_spine00001"
SKU = "FRM-1001"


def door_created_db() -> FakeDB:
    """The normal shape of a product added through Add-a-Product: the twin and
    the spine carry DIFFERENT ids, joined by the spine's pim_product_id, and
    share the sku. Children are split across both ids and the sku on purpose."""
    db = FakeDB()
    db.seed("catalog_products", [{"id": TWIN_ID, "sku": SKU, "brand": "Ray-Ban", "model": "RB2140"}])
    db.seed("products", [{"product_id": SPINE_ID, "pim_product_id": TWIN_ID, "sku": SKU}])
    db.seed(
        "catalog_variants",
        [
            {"variant_id": "V1", "parent_product_id": TWIN_ID},
            {"variant_id": "V2", "parent_sku": SKU},
            {"variant_id": "V3", "sku": SKU},
        ],
    )
    db.seed(
        "product_images",
        [{"image_id": "I1", "product_id": TWIN_ID}, {"image_id": "I2", "product_id": SPINE_ID}],
    )
    db.seed(
        "collection_products",
        [{"row": "C1", "product_id": SPINE_ID}, {"row": "C2", "sku": SKU}],
    )
    db.seed(
        "stock_units",
        [{"unit_id": "U1", "product_id": SPINE_ID, "status": "AVAILABLE"}],
    )
    return db


def add_bystander(db: FakeDB) -> None:
    """A second, unrelated product whose rows must survive every delete."""
    db.seed("catalog_products", [{"id": "prod_other", "sku": "FRM-9999", "brand": "Oakley"}])
    db.seed("products", [{"product_id": "prod_other", "sku": "FRM-9999"}])
    db.seed("catalog_variants", [{"variant_id": "VX", "parent_product_id": "prod_other"}])
    db.seed("product_images", [{"image_id": "IX", "product_id": "prod_other"}])
    db.seed("collection_products", [{"row": "CX", "product_id": "prod_other"}])
    db.seed("stock_units", [{"unit_id": "UX", "product_id": "prod_other", "status": "AVAILABLE"}])


def target_for(db: FakeDB, twin_id: str = TWIN_ID) -> Dict[str, Any]:
    twin = db["catalog_products"].find_one({"id": twin_id})
    assert twin is not None
    return script.collect_target(db, twin)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

_MONGO_VARS = ("MONGO_PUBLIC_URL", "MONGODB_URI", "MONGODB_URL", "MONGO_URL")


def _only(monkeypatch, **set_vars):
    """Clear every Mongo var, then set exactly the ones named."""
    for var in _MONGO_VARS:
        monkeypatch.delenv(var, raising=False)
    for var, value in set_vars.items():
        monkeypatch.setenv(var, value)


@pytest.mark.parametrize(
    "env,expected",
    [
        # The runbook is always launched FROM a developer machine through
        # `railway run --service ims-2.0-railway`, which injects
        # MONGO_PUBLIC_URL. The MongoDB service injects only
        # mongodb.railway.internal, which does not resolve off-platform -- so
        # a reachable address must win over an internal one, never the reverse.
        ({"MONGO_PUBLIC_URL": "public", "MONGODB_URL": "internal"}, "public"),
        ({"MONGO_PUBLIC_URL": "public", "MONGO_URL": "internal"}, "public"),
        ({"MONGODB_URI": "uri", "MONGODB_URL": "internal"}, "uri"),
        ({"MONGODB_URL": "only"}, "only"),
        ({"MONGO_URL": "only"}, "only"),
    ],
)
def test_connect_prefers_the_reachable_address(monkeypatch, env, expected):
    _only(monkeypatch, **env)
    seen = {}

    class _FakeClient:
        def __init__(self, url, **_kw):
            seen["url"] = url
            self.admin = self

        def command(self, _name):
            return {"ok": 1}

        def __getitem__(self, name):
            return {"db": name}

    monkeypatch.setitem(sys.modules, "pymongo", type("M", (), {"MongoClient": _FakeClient}))
    assert script.connect() is not None
    assert seen["url"] == expected


def test_connect_refuses_with_no_address_at_all(monkeypatch):
    _only(monkeypatch)
    assert script.connect() is None


def test_parse_csv_option_trims_and_drops_blanks():
    assert script.parse_csv_option(" a , b ,, c ") == ["a", "b", "c"]
    assert script.parse_csv_option("") == []
    assert script.parse_csv_option(None) == []


def test_shopify_id_only_when_actually_pushed():
    assert script.shopify_id_of({}) is None
    assert script.shopify_id_of({"ecom": {}}) is None
    assert script.shopify_id_of({"ecom": {"shopify_product_id": ""}}) is None
    assert script.shopify_id_of({"ecom": {"shopify_product_id": "gid://x/1"}}) == "gid://x/1"


def test_shopify_id_survives_a_non_dict_ecom():
    """A legacy / half-migrated row can carry a non-dict `ecom`; that must read
    as 'not on Shopify' rather than explode mid-run."""
    assert script.shopify_id_of({"ecom": "legacy"}) is None


@pytest.mark.parametrize("status", ["AVAILABLE", "available", "Available"])
def test_available_units_are_not_flagged(status):
    assert script.unavailable_units([{"status": status}]) == []


@pytest.mark.parametrize("status", ["SOLD", "RESERVED", "IN_TRANSIT", "", None])
def test_anything_but_available_is_flagged_including_unknown(status):
    """An unrecognised status must REFUSE, not be assumed safe."""
    assert len(script.unavailable_units([{"status": status}])) == 1


def test_product_label_falls_back_through_twin_then_spine():
    assert "Ray-Ban RB2140" in script.product_label({"brand": "Ray-Ban", "model": "RB2140"}, None)
    assert "Oakley" in script.product_label({}, {"brand": "Oakley", "sku": "S1"})
    assert "SKU S1" in script.product_label({}, {"brand": "Oakley", "sku": "S1"})
    assert "prod_x" in script.product_label({"id": "prod_x"}, None)


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

def test_a_clean_product_has_no_refusals():
    assert script.refusals_for(target_for(door_created_db())) == []


def test_a_product_on_shopify_is_refused():
    db = door_created_db()
    db["catalog_products"].docs[0]["ecom"] = {"shopify_product_id": "gid://shopify/Product/7"}
    reasons = script.refusals_for(target_for(db))
    assert len(reasons) == 1 and "Shopify" in reasons[0]


def test_a_product_on_an_order_line_is_refused():
    db = door_created_db()
    db.seed("orders", [{"order_id": "O1", "items": [{"product_id": SPINE_ID, "qty": 1}]}])
    reasons = script.refusals_for(target_for(db))
    assert len(reasons) == 1 and "order" in reasons[0]


def test_the_order_guard_matches_the_twin_id_too():
    """An order booked against the twin id (a convergence-era product shares
    it) must refuse just as loudly as one against the spine id."""
    db = door_created_db()
    db.seed("orders", [{"order_id": "O1", "items": [{"product_id": TWIN_ID}]}])
    assert script.refusals_for(target_for(db))


def test_an_unrelated_order_does_not_refuse():
    db = door_created_db()
    db.seed("orders", [{"order_id": "O1", "items": [{"product_id": "prod_somebody_else"}]}])
    assert script.refusals_for(target_for(db)) == []


def test_a_sold_stock_unit_is_refused():
    db = door_created_db()
    db.seed("stock_units", [{"unit_id": "U2", "product_id": SPINE_ID, "status": "SOLD"}])
    reasons = script.refusals_for(target_for(db))
    assert len(reasons) == 1 and "stock unit" in reasons[0]


# ---------------------------------------------------------------------------
# Resolution + blast radius
# ---------------------------------------------------------------------------

def test_spine_resolves_through_pim_product_id():
    """The door-created shape: the ids differ, only pim_product_id joins them.

    The spine's sku is deliberately DIFFERENT here so the sku arm cannot
    rescue the lookup -- otherwise this test passes with the pim arm deleted
    and pins nothing. Drift like this is exactly when the id lookup misses and
    the pim arm is the only join left."""
    db = door_created_db()
    db["products"].docs[0]["sku"] = "DRIFTED-SKU"
    twin = db["catalog_products"].find_one({"id": TWIN_ID})
    assert script.resolve_spine(db, twin)["product_id"] == SPINE_ID


def test_spine_resolves_for_the_ordinary_door_created_product():
    """The shape 71 of the 77 live products had on the 08-30 census: different
    ids, shared sku. Either the pim arm or the sku arm must find it."""
    db = door_created_db()
    twin = db["catalog_products"].find_one({"id": TWIN_ID})
    assert script.resolve_spine(db, twin)["product_id"] == SPINE_ID


def test_spine_resolves_through_a_shared_product_id():
    db = FakeDB()
    db.seed("catalog_products", [{"id": "shared1", "sku": "S"}])
    db.seed("products", [{"product_id": "shared1", "sku": "S"}])
    twin = db["catalog_products"].find_one({"id": "shared1"})
    assert script.resolve_spine(db, twin)["product_id"] == "shared1"


def test_spine_resolves_through_the_shared_sku_when_no_id_matches():
    db = FakeDB()
    db.seed("catalog_products", [{"id": "twin_only", "sku": "S"}])
    db.seed("products", [{"product_id": "elsewhere", "sku": "S"}])
    twin = db["catalog_products"].find_one({"id": "twin_only"})
    assert script.resolve_spine(db, twin)["product_id"] == "elsewhere"


def test_a_twin_with_no_spine_resolves_to_none():
    db = FakeDB()
    db.seed("catalog_products", [{"id": "twin_only", "sku": "S"}])
    twin = db["catalog_products"].find_one({"id": "twin_only"})
    assert script.resolve_spine(db, twin) is None


def test_blast_radius_gathers_both_ids_and_the_sku():
    target = target_for(door_created_db())
    assert sorted(target["ids"]) == sorted([TWIN_ID, SPINE_ID])
    assert {v["variant_id"] for v in target["variants"]} == {"V1", "V2", "V3"}
    assert {i["image_id"] for i in target["images"]} == {"I1", "I2"}
    assert {c["row"] for c in target["collection_rows"]} == {"C1", "C2"}
    assert {u["unit_id"] for u in target["stock_units"]} == {"U1"}


def test_blast_radius_excludes_a_neighbouring_product():
    db = door_created_db()
    add_bystander(db)
    target = target_for(db)
    assert "VX" not in {v["variant_id"] for v in target["variants"]}
    assert "IX" not in {i["image_id"] for i in target["images"]}


# ---------------------------------------------------------------------------
# Delete + snapshot
# ---------------------------------------------------------------------------

def test_delete_removes_the_product_and_all_its_rows():
    db = door_created_db()
    target = target_for(db)
    script.delete_target(db, target)
    for collection in (
        "catalog_products",
        "products",
        "catalog_variants",
        "product_images",
        "collection_products",
        "stock_units",
    ):
        assert db[collection].docs == [], collection


def test_delete_leaves_a_neighbouring_product_alone():
    db = door_created_db()
    add_bystander(db)
    script.delete_target(db, target_for(db))
    assert db.ids("catalog_products", "id") == ["prod_other"]
    assert db.ids("products", "product_id") == ["prod_other"]
    assert db.ids("catalog_variants", "variant_id") == ["VX"]
    assert db.ids("product_images", "image_id") == ["IX"]
    assert db.ids("collection_products", "row") == ["CX"]
    assert db.ids("stock_units", "unit_id") == ["UX"]


def test_snapshot_holds_every_document_the_delete_removes():
    """The undo file is only an undo file if it round-trips. Counted per
    collection against what delete_target actually reports removing."""
    db = door_created_db()
    target = target_for(db)
    snapshot = script.build_snapshot([target])
    removed = script.delete_target(db, target)
    for collection, count in removed.items():
        assert len(snapshot["documents"][collection]) == count, collection


def test_snapshot_skips_a_missing_spine_without_a_none_hole():
    db = FakeDB()
    db.seed("catalog_products", [{"id": "twin_only", "sku": "S"}])
    snapshot = script.build_snapshot([target_for(db, "twin_only")])
    assert snapshot["documents"]["products"] == []


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def test_find_twins_all_takes_the_whole_catalogue():
    db = door_created_db()
    add_bystander(db)
    found = script.find_twins(db, want_all=True, ids=[], skus=[])
    assert {t["id"] for t in found} == {TWIN_ID, "prod_other"}


def test_find_twins_by_id_and_by_sku():
    db = door_created_db()
    add_bystander(db)
    assert [t["id"] for t in script.find_twins(db, want_all=False, ids=[TWIN_ID], skus=[])] == [TWIN_ID]
    assert [t["id"] for t in script.find_twins(db, want_all=False, ids=[], skus=["FRM-9999"])] == ["prod_other"]


# ---------------------------------------------------------------------------
# main() -- the whole run
# ---------------------------------------------------------------------------

@pytest.fixture
def run(monkeypatch, tmp_path):
    """Run main() against a fake DB, with the snapshot landing in tmp_path."""

    def _run(db: FakeDB, argv: List[str]) -> int:
        monkeypatch.setattr(script, "connect", lambda: db)
        return script.main(argv + ["--snapshot-dir", str(tmp_path)])

    return _run


def _snapshot_files(tmp_path) -> List[Any]:
    return sorted(tmp_path.glob("catalog_delete_*.json"))


def test_dry_run_is_the_default_and_writes_nothing(run, tmp_path):
    db = door_created_db()
    assert run(db, ["--all", "--expect", "1"]) == 0
    assert len(db["catalog_products"].docs) == 1
    assert len(db["products"].docs) == 1
    assert _snapshot_files(tmp_path) == []
    assert db["audit_logs"].docs == []


def test_expect_mismatch_refuses_before_anything_is_read_out(run):
    db = door_created_db()
    add_bystander(db)
    assert run(db, ["--all", "--expect", "1", "--commit"]) == 1
    assert len(db["catalog_products"].docs) == 2


def test_all_requires_expect():
    assert script.main(["--all"]) == 1


def test_no_selector_is_refused():
    assert script.main([]) == 1


def test_all_cannot_be_combined_with_a_named_selection():
    assert script.main(["--all", "--expect", "1", "--sku", "FRM-1001"]) == 1


def test_commit_deletes_snapshots_and_audits(run, tmp_path):
    db = door_created_db()
    add_bystander(db)
    assert run(db, ["--sku", SKU, "--commit"]) == 0

    assert db.ids("catalog_products", "id") == ["prod_other"]
    assert db.ids("products", "product_id") == ["prod_other"]

    files = _snapshot_files(tmp_path)
    assert len(files) == 1
    saved = json.loads(files[0].read_text(encoding="utf-8"))
    assert [d["id"] for d in saved["documents"]["catalog_products"]] == [TWIN_ID]
    assert len(saved["documents"]["catalog_variants"]) == 3

    assert len(db["audit_logs"].docs) == 1
    row = db["audit_logs"].docs[0]
    assert row["action"] == script.AUDIT_ACTION
    assert row["context"]["snapshot"] == str(files[0])


def test_one_refused_product_stops_the_whole_batch(run, tmp_path):
    """The clean product beside a refused one must NOT be deleted -- a partial
    catalogue delete is the outcome this script exists to avoid."""
    db = door_created_db()
    add_bystander(db)
    db.seed("orders", [{"order_id": "O1", "items": [{"product_id": "prod_other"}]}])

    assert run(db, ["--all", "--expect", "2", "--commit"]) == 1
    assert len(db["catalog_products"].docs) == 2
    assert len(db["catalog_variants"].docs) == 4
    assert _snapshot_files(tmp_path) == []


def test_a_selection_that_matches_nothing_is_refused(run):
    assert run(door_created_db(), ["--sku", "NOPE", "--commit"]) == 1


def test_a_failed_snapshot_aborts_before_any_delete(monkeypatch):
    """No snapshot, no delete -- the undo file is a precondition, not a nicety."""
    db = door_created_db()
    monkeypatch.setattr(script, "connect", lambda: db)

    def _explode(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(script, "write_snapshot", _explode)
    assert script.main(["--all", "--expect", "1", "--commit"]) == 1
    assert len(db["catalog_products"].docs) == 1
    assert len(db["stock_units"].docs) == 1
