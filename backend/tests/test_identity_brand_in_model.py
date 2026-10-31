"""A brand typed inside the model number never hides a duplicate.

Prod 10-24 (read-only): 7 products had the brand typed in model_no
('CARRERA 3001/S'), so their identity_key was 'carrera|carrera3001s|086ha|54'
while the same frame re-entered as '3001/S' keyed 'carrera|3001s|086ha|54' --
the unique index never fired and two duplicate pairs were saved. The model part
of every identity comparison now reads through ONE rule
(product_master.model_without_brand): the key, the similar-products lookup, the
model scan and the families. SKUs are never minted from it (existing SKUs never
change) and model_no is stored as typed.

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest backend/tests/test_identity_brand_in_model.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import api.dependencies as deps  # noqa: E402
from api.routers import product_master as pm_router  # noqa: E402
from api.routers import products as prod_router  # noqa: E402
from api.services import product_master as pm  # noqa: E402
from database.repositories.product_repository import ProductRepository  # noqa: E402
from strict_fakes import StrictCollection, StrictDB  # noqa: E402

_ADMIN = {"user_id": "u-admin", "username": "admin", "roles": ["ADMIN"]}
K = pm.compute_identity_key


@pytest.fixture
def door(monkeypatch):
    """The FORM create door (POST /products core) over in-memory fakes."""
    db = StrictDB()
    repo = ProductRepository(StrictCollection("products"))
    monkeypatch.setattr(prod_router, "get_product_repository", lambda: repo)
    monkeypatch.setattr(deps, "get_db", lambda: db)
    monkeypatch.setattr(pm_router, "get_product_repository", lambda: repo)
    monkeypatch.setattr(pm_router, "get_db", lambda: db)
    monkeypatch.setattr(deps, "get_audit_repository", lambda: None)
    monkeypatch.setattr(pm, "mirror_enabled", lambda: False)

    def _create(model, **over):
        body = {"category": "SUNGLASS", "brand": "Carrera", "model": model, "color": "086HA",
                "mrp": 9000.0, "offer_price": 9000.0, "attributes": {"lens_size": "54"}}
        body.update(over)
        return prod_router._create_via_canonical_door(
            prod_router.ProductCreate(**body), _ADMIN, source="FORM")

    _create.repo = repo
    return _create


def _refused(door, model, **over):
    with pytest.raises(HTTPException) as exc:
        door(model, **over)
    assert exc.value.status_code == 409
    return exc.value.detail["existing"]


def test_reentry_without_the_brand_is_refused_as_a_duplicate(door):
    first = door("CARRERA 3001/S")
    assert first["identity_key"] == "carrera|3001s|086ha|54"
    assert _refused(door, "3001/S")["sku"] == first["sku"]
    # ...and with the brand glued on, or spelled in another case.
    assert _refused(door, "Carrera3001/S")["sku"] == first["sku"]


def test_the_brand_typed_second_is_refused_too(door):
    first = door("3001/S")
    assert _refused(door, "CARRERA 3001/S")["sku"] == first["sku"]


def test_similar_products_finds_the_brand_in_model_twin(door):
    first = door("CARRERA 3001/S")
    typed = {"brand_name": "Carrera", "model_no": "3001/S", "colour_code": "086HA", "lens_size": "54"}
    hit = pm.find_similar_products(door.repo.collection, category="SUNGLASS", attributes=typed)
    assert (hit["exact_match"] or {}).get("sku") == first["sku"]
    # Another colour of the same model is a sibling, not lost.
    other = pm.find_similar_products(door.repo.collection, category="SUNGLASS",
                                     attributes=dict(typed, colour_code="807"))
    assert other["exact_match"] is None
    assert [s["sku"] for s in other["siblings"]] == [first["sku"]]


def test_model_no_is_stored_as_typed_and_the_sku_is_unchanged(door):
    first = door("CARRERA 3001/S")
    assert first["attributes"]["model_no"] == "CARRERA 3001/S"
    # The SKU minter never reads the stripped model: it is what it always was.
    assert first["sku"] == "SG-CARRERA-CARRERA3001S-086HA-54"
    assert pm.build_sku("SG", {"brand_name": "Carrera", "model_no": "CARRERA 3001/S",
                               "colour_code": "086HA", "lens_size": "54"}) == first["sku"]


def test_an_edit_never_moves_an_existing_sku(door):
    first = door("CARRERA 3001/S")
    patch = {"attributes": dict(first["attributes"], model_no="3001/S")}
    pm.rekey_on_edit(first, patch, door.repo)
    # Same product by the rule: no re-key, and the SKU never moves.
    assert "sku" not in patch and patch.get("identity_key", first["identity_key"]) == first["identity_key"]


def _sg(model, **attrs):
    return {"category": "SUNGLASS", "attributes": {"brand_name": attrs.pop("brand", "Boss"), "model_no": model,
                                                   "colour_code": "C2", "lens_size": "52", **attrs}}


def test_the_sub_brand_never_moves_the_key():
    """B1: only the BRAND folds. The same frame typed with and without its
    optional sub-brand is one product; a sub-brand word in the model stays."""
    k = pm.identity_key_of
    assert k(_sg("META WAYFARER", brand="Ray-Ban", subbrand="Meta")) == k(_sg("META WAYFARER", brand="Ray-Ban"))
    assert k(_sg("BOSS ORANGE 0123", subbrand="Orange")) == "boss|orange0123|c2|52"
    assert k(_sg("BOSS ORANGE 0123", subbrand="Orange")) != k(_sg("BOSS 0123", subbrand="Orange"))
    assert k(_sg("OX8046", brand="Oakley", subbrand="OX")) != k(_sg("OY8046", brand="Oakley", subbrand="OX"))


def test_the_fold_takes_the_brand_and_never_a_real_model():
    """N7: the brand typed in front folds; a model that IS the brand, or a word
    that merely starts with it, never does."""
    fold = pm.model_without_brand
    assert fold("Carrera", "CARRERA 3001/S") == fold("Carrera", "Carrera3001/S") == "3001s"
    assert fold("Ray-Ban", "RAY-BAN 0RB 4350") == "rb4350"  # Luxottica 0 dropped after the brand
    assert fold("Titan", "Titan") == "titan"
    assert fold("Titan", "TITANIUM12") == "titanium12"
    assert fold("Essilor", "Crizal") == "crizal"


def test_a_variant_edit_that_drops_the_brand_is_no_model_change():
    """N1: CARRERA 3001/S -> 3001/S on a variant is the same model."""
    before = {"brand_name": "Carrera", "model_no": "CARRERA 3001/S"}
    assert pm.model_field_changes(before, dict(before, model_no="3001/S")) == {}
    assert pm.model_field_changes(before, dict(before, model_no="3002/S")) == {"model_no": "3002/S"}
    variant = {"variant_of": "P-LEAD", "brand": "Carrera", "model": "CARRERA 3001/S", "category": "SUNGLASS",
               "attributes": before}
    pm.assert_variant_model_fields(variant, dict(before, model_no="3001/S"), model="3001/S", brand="Carrera")
    with pytest.raises(pm.ProductMasterError):
        pm.assert_variant_model_fields(variant, None, model="3002/S")


def test_promote_warns_about_the_brand_in_model_twin(door):
    """N6: the import review's duplicate warning reads the same rule."""
    from api.routers import catalog as cat

    first = door("CARRERA 3001/S")
    doc = {"category": "SUNGLASS", "attributes": {"brand_name": "Carrera", "model_no": "3001/S"}}
    warned = cat._promote_duplicate_warnings(door.repo, doc)
    assert [w["sku"] for w in warned] == [first["sku"]]


def test_restore_refuses_while_a_live_row_is_the_same_product(door, monkeypatch):
    """S3: an archived twin that gave its key up never comes back as a live
    duplicate; with no live twin it comes back keyed again."""
    from api.routers import catalog as cat

    kept = door("3001/S")
    door.repo.collection.insert_one({
        "product_id": "P-ARCH", "sku": "SGCARRERACARRERA3001S086HA", "category": "SUNGLASS",
        "brand": "Carrera", "model": "CARRERA 3001/S", "is_active": False, "deleted_at": "2026-10-24",
        "attributes": {"brand_name": "Carrera", "model_no": "CARRERA 3001/S", "colour_code": "086HA",
                       "lens_size": "54"}})
    archived = door.repo.find_by_id("P-ARCH")
    assert kept["sku"] in cat._restore_refusal(door.repo, archived)
    # The kept one archived too: the twin may come back, keyed by the rule.
    door.repo.collection.update_one({"product_id": kept["product_id"]},
                                    {"$set": {"deleted_at": "x"}, "$unset": {"identity_key": ""}})
    assert cat._restore_refusal(door.repo, archived) is None
    monkeypatch.setattr(deps, "get_product_repository", lambda: door.repo)
    cat._restore_product(None, archived, _ADMIN, on_website=False)
    back = door.repo.find_by_id("P-ARCH")
    assert back["deleted_at"] is None and back["identity_key"] == "carrera|3001s|086ha|54"


def test_the_model_scan_and_families_read_the_same_rule():
    q = pm.model_family_query("SUNGLASS", "Carrera", "CARRERA 3001/S")
    assert q["identity_key"]["$regex"].startswith("^carrera\\|3001s\\|")
    a = {"category": "SUNGLASS", "attributes": {"brand_name": "Carrera", "model_no": "CARRERA 3001/S"}}
    b = {"category": "SUNGLASS", "attributes": {"brand_name": "Carrera", "model_no": "3001/S"}}
    assert pm.family_key(a) == pm.family_key(b)


# ---------------------------------------------------------------------------
# scripts/fix_brand_in_model.py over a seeded fake (the prod shape of 10-24)
# ---------------------------------------------------------------------------


def _row(pid, sku, model, colour, size, key, **extra):
    attrs = {"brand_name": "Carrera", "model_no": model, "colour_code": colour}
    if size:
        attrs["lens_size"] = size
    return {"product_id": pid, "sku": sku, "category": "SUNGLASS", "brand": "Carrera", "model": model,
            "is_active": True, "identity_key": key, "attributes": attrs, **extra}


def _seeded():
    db = StrictDB()
    db.seed("products", [
        # Pair 1: the OLD-format SKU is live on Shopify (a variant id): it survives,
        # the new-format copy is archived and gives up the key.
        _row("p-old1", "SGCARRERACARRERA3001S086HA", "CARRERA 3001/S", "086HA", "54",
             "carrera|carrera3001s|086ha|54", created_at="2026-07-01T00:00:00"),
        _row("p-new1", "SG-CARRERA-3001S-086HA-54", "3001/S", "086HA", "54", "carrera|3001s|086ha|54",
             created_at="2026-10-01T00:00:00"),
        # Pair 2: neither online; the new one has the photos -> the old one goes.
        _row("p-old2", "SGCARRERACARRERAFLAGLAB16KY21V", "CARRERA FLAGLAB16", "KY21V", "99",
             "carrera|carreraflaglab16|ky21v|99"),
        _row("p-new2", "SG-CARRERA-FLAGLAB16-KY21V-99", "FLAGLAB16", "KY21V", "99", "carrera|flaglab16|ky21v|99"),
        # Pair 3: the new copy has NO eye size, so its key never collided. It has
        # the price and photos: it survives and takes lens size 99.
        _row("p-old3", "SGCARRERACARRERAFLAGLAB16FG486", "CARRERA FLAGLAB16", "FG486", "99",
             "carrera|carreraflaglab16|fg486|99"),
        _row("p-new3", "SG-CARRERA-FLAGLAB16-FG486", "FLAGLAB16", "FG486", None, "carrera|flaglab16|fg486",
             mrp=12000.0),
        # Pair 4: both on Shopify -> nothing archived, a human merges it.
        _row("p-old4", "SGCARRERACARRERA5001807", "CARRERA 5001", "807", "50", "carrera|carrera5001|807|50",
             ecom={"shopify_product_id": "gid://1"}),
        _row("p-new4", "SG-CARRERA-5001-807-50", "5001", "807", "50", "carrera|5001|807|50",
             ecom={"shopify_product_id": "gid://2"}),
        # Brand in model, no twin to collide with: cleaned + re-keyed.
        _row("p-solo", "SGCARRERACARRERA1001S807", "CARRERA 1001/S", "807", "52", "carrera|carrera1001s|807|52",
             pim_product_id="t-solo"),
        # A clean row: untouched.
        _row("p-ok", "SG-CARRERA-8895-807-54", "8895", "807", "54", "carrera|8895|807|54"),
    ])
    db.seed("catalog_products", [
        {"id": "p-new1", "sku": "SG-CARRERA-3001S-086HA-54", "is_active": True, "attributes": {"model_no": "3001/S"}},
        {"id": "p-old1", "sku": "SGCARRERACARRERA3001S086HA", "attributes": {"model_no": "CARRERA 3001/S"}},
        {"id": "t-solo", "sku": "SGCARRERACARRERA1001S807", "attributes": {"model_no": "CARRERA 1001/S"}},
        {"id": "p-new3", "sku": "SG-CARRERA-FLAGLAB16-FG486", "attributes": {"model_no": "FLAGLAB16"}},
    ])
    db.seed("catalog_variants", [{"parent_product_id": "p-old1", "sku": "SGCARRERACARRERA3001S086HA",
                                  "shopify_variant_id": "gid://v1"}])
    db.seed("product_images", [{"product_id": "p-new2"}, {"product_id": "p-new2"}, {"product_id": "p-new3"}])
    for name in ("stock_units", "purchase_orders", "grns", "orders"):
        db.seed(name, [])
    return db


def _snapshot(db):
    import copy

    return {n: copy.deepcopy(db[n].docs) for n in ("products", "catalog_products")}


def _pairs(p):
    return {e["key"]: e for e in p["pairs"]}


def test_script_dry_run_finds_every_pair_and_picks_survivors_by_the_rule():
    from scripts import fix_brand_in_model as fix

    db = _seeded()
    before = _snapshot(db)
    p = fix.plan(db)
    assert sorted(c["doc"]["product_id"] for c in p["changes"]) == ["p-old1", "p-old2", "p-old3", "p-old4", "p-solo"]
    solo = next(c for c in p["changes"] if c["doc"]["product_id"] == "p-solo")
    assert (solo["cleaned"], solo["new_key"]) == ("1001/S", "carrera|1001s|807|52")
    pairs = _pairs(p)
    assert sorted(pairs) == ["carrera|3001s|086ha|54", "carrera|5001|807|50",
                             "carrera|flaglab16|fg486|99", "carrera|flaglab16|ky21v|99"]
    verdict = {k: (e["survivor"]["sku"], e["loser"]["sku"], bool(e["blockers"])) for k, e in pairs.items()}
    assert verdict["carrera|3001s|086ha|54"] == ("SGCARRERACARRERA3001S086HA", "SG-CARRERA-3001S-086HA-54", False)
    assert verdict["carrera|flaglab16|ky21v|99"] == (
        "SG-CARRERA-FLAGLAB16-KY21V-99", "SGCARRERACARRERAFLAGLAB16KY21V", False)
    assert verdict["carrera|flaglab16|fg486|99"] == (
        "SG-CARRERA-FLAGLAB16-FG486", "SGCARRERACARRERAFLAGLAB16FG486", False)
    assert pairs["carrera|flaglab16|fg486|99"]["fill_size"] == "99"
    assert verdict["carrera|5001|807|50"][2] is True  # the other copy is on Shopify
    fix.report(p)
    assert _snapshot(db) == before  # a dry run writes nothing


def test_script_never_archives_a_sku_on_shopify():
    from scripts import fix_brand_in_model as fix

    db = _seeded()
    # The live old copy loses on every other count: it still survives.
    db["product_images"].insert_many([{"product_id": "p-new1"} for _ in range(5)])
    assert _pairs(fix.plan(db))["carrera|3001s|086ha|54"]["survivor"]["sku"] == "SGCARRERACARRERA3001S086HA"
    # Both live (a variant id on the new copy too): nothing is archived.
    db2 = _seeded()
    db2["catalog_variants"].insert_one({"parent_product_id": "p-new1", "shopify_variant_id": "gid://v2"})
    e2 = _pairs(fix.plan(db2))["carrera|3001s|086ha|54"]
    assert e2["blockers"] and "Shopify" in e2["blockers"][0]


def test_script_apply_archives_first_then_rekeys_and_reverts():
    from scripts import fix_brand_in_model as fix

    db = _seeded()
    before = _snapshot(db)
    stats = fix.apply(db, fix.plan(db), "run-1")
    # Pair 2's survivor needed no write: only real writes are counted.
    assert stats == {"archived": 3, "rekeyed": 3, "skipped": 1, "failed": 0}
    rows = {d["product_id"]: d for d in db["products"].docs}
    # Pair 1: the new-format copy archived and off the key; the live one takes it, cleaned.
    assert rows["p-new1"]["is_active"] is False and rows["p-new1"]["deleted_at"]
    assert rows["p-new1"]["active_before_archive"] is True and "identity_key" not in rows["p-new1"]
    assert db["catalog_products"].find_one({"id": "p-new1"})["is_active"] is False
    assert rows["p-old1"]["identity_key"] == "carrera|3001s|086ha|54"
    assert rows["p-old1"]["attributes"]["model_no"] == "3001/S" and rows["p-old1"]["is_active"] is True
    assert db["catalog_products"].find_one({"id": "p-old1"})["attributes"]["model_no"] == "3001/S"
    # Pair 2: the old copy archived, keeping its old key.
    assert rows["p-old2"]["deleted_at"] and rows["p-old2"]["identity_key"] == "carrera|carreraflaglab16|ky21v|99"
    # Pair 3: the sizeless survivor takes lens size 99 and the sized key.
    assert rows["p-old3"]["deleted_at"]
    assert rows["p-new3"]["attributes"]["lens_size"] == "99"
    assert rows["p-new3"]["identity_key"] == "carrera|flaglab16|fg486|99"
    assert db["catalog_products"].find_one({"id": "p-new3"})["attributes"]["lens_size"] == "99"
    # Pair 4 and the clean row untouched; the solo row cleaned (spine + twin).
    for pid in ("p-old4", "p-new4", "p-ok"):
        assert rows[pid] == next(d for d in before["products"] if d["product_id"] == pid)
    assert rows["p-solo"]["identity_key"] == "carrera|1001s|807|52"
    assert rows["p-solo"]["attributes"]["model_no"] == "1001/S" and rows["p-solo"]["model"] == "1001/S"
    assert db["catalog_products"].find_one({"id": "t-solo"})["attributes"]["model_no"] == "1001/S"
    # SKUs never move; no key is held twice; every written key is the rule's own.
    assert [d["sku"] for d in db["products"].docs] == [d["sku"] for d in before["products"]]
    keys = [d["identity_key"] for d in db["products"].docs if d.get("identity_key")]
    assert len(keys) == len(set(keys))
    for pid in ("p-old1", "p-new3", "p-solo"):
        assert pm.identity_key_of(rows[pid]) == rows[pid]["identity_key"]

    assert fix.revert(db, "run-1") > 0
    assert _snapshot(db) == before
    assert fix.revert(db, "run-1") == 0  # a second revert is a no-op


def test_script_an_archived_row_never_survives_and_an_archived_key_holder_is_resolved():
    """B2 + re-review: an archived copy loses whatever else it has (the report
    says ARCHIVED). The prod Flag Lab 16 case -- the live brand-in-model row,
    its new-format twin archived by hand still holding the key -- is the safest
    pair of all: the archived twin gives the key up, the live row takes it, and
    Restore of the twin is then refused."""
    from api.routers import catalog as cat
    from scripts import fix_brand_in_model as fix

    db = _seeded()
    db["products"].update_one({"product_id": "p-new2"}, {"$set": {"deleted_at": "2026-10-01", "is_active": False}})
    p = fix.plan(db)
    e = _pairs(p)["carrera|flaglab16|ky21v|99"]
    assert e["survivor"]["sku"] == "SGCARRERACARRERAFLAGLAB16KY21V"  # despite the archived row's photos
    assert e["blockers"] == [] and fix._why(e["fl"]).startswith("ARCHIVED")
    fix.apply(db, p, "run-arch")
    rows = {d["product_id"]: d for d in db["products"].docs}
    assert "identity_key" not in rows["p-new2"]
    assert rows["p-new2"]["deleted_at"] == "2026-10-01"  # left archived as it was
    assert rows["p-old2"]["identity_key"] == "carrera|flaglab16|ky21v|99" and not rows["p-old2"].get("deleted_at")
    assert rows["p-old2"]["attributes"]["model_no"] == "FLAGLAB16"
    repo = ProductRepository(db["products"])
    assert "SGCARRERACARRERAFLAGLAB16KY21V" in cat._restore_refusal(repo, rows["p-new2"])


def test_script_one_row_matched_twice_is_report_only_and_untouched():
    """B3: a size-less frame vs brand-in-model rows of sizes 52 and 54."""
    from scripts import fix_brand_in_model as fix

    db = _seeded()
    db["products"].insert_one(_row("p-old5", "SGCARRERACARRERAFLAGLAB16FG48652", "CARRERA FLAGLAB16", "FG486",
                                   "52", "carrera|carreraflaglab16|fg486|52"))
    before = _snapshot(db)
    p = fix.plan(db)
    group = [e for e in p["pairs"] if (e.get("other") or {}).get("product_id") == "p-new3"]
    assert len(group) == 2 and all(e["blockers"] for e in group)
    fix.apply(db, p, "run-b3")
    for pid in ("p-old3", "p-old5", "p-new3"):
        assert db["products"].find_one({"product_id": pid}) == next(
            d for d in before["products"] if d["product_id"] == pid)


def test_script_any_reference_or_unreadable_collection_blocks_the_archive():
    """S2: returns, transfers, workshop jobs, bills, aliases... name a product;
    a collection that cannot be read refuses rather than guesses."""
    from scripts import fix_brand_in_model as fix

    # Pair 1's loser (the live copy survives as the one on Shopify).
    db = _seeded()
    db.seed("returns", [{"return_id": "R1", "items": [{"product_id": "p-new1"}]}])
    e = _pairs(fix.plan(db))["carrera|3001s|086ha|54"]
    assert e["loser"]["product_id"] == "p-new1" and e["blockers"] == ["has returns rows"]

    db = _seeded()
    db.seed("vendor_sku_aliases", [{"vendor_sku": "V-1", "sku": "SG-CARRERA-3001S-086HA-54"}])
    assert _pairs(fix.plan(db))["carrera|3001s|086ha|54"]["blockers"] == ["has vendor_sku_aliases rows"]

    db = _seeded()

    class _Broken(StrictCollection):
        def count_documents(self, *a, **k):
            raise RuntimeError("down")

    db._collections["workshop_jobs"] = _Broken("workshop_jobs")
    blockers = _pairs(fix.plan(db))["carrera|flaglab16|ky21v|99"]["blockers"]
    assert blockers == ["has workshop_jobs unreadable (RuntimeError)"]


# ---------------------------------------------------------------------------
# Re-review of 591c73df (the reviewer's probes as tests)
# ---------------------------------------------------------------------------


def test_script_a_sizeless_twin_next_to_another_eye_size_is_report_only():
    """BLOCK 1: a size-less copy may be the 52, not the 99: never guessed."""
    from scripts import fix_brand_in_model as fix

    db = _seeded()
    db["products"].insert_one(_row("p-c52", "SG-CARRERA-FLAGLAB16-FG486-52", "FLAGLAB16", "FG486", "52",
                                   "carrera|flaglab16|fg486|52"))
    db.seed("stock_units", [{"product_id": "p-new3", "sku": "SG-CARRERA-FLAGLAB16-FG486"}])
    before = _snapshot(db)
    p = fix.plan(db)
    e = _pairs(p)["carrera|flaglab16|fg486|99"]
    assert e["blockers"] and "SG-CARRERA-FLAGLAB16-FG486-52" in e["blockers"][-1]
    fix.apply(db, p, "run-b1")
    for pid in ("p-old3", "p-new3", "p-c52"):
        assert db["products"].find_one({"product_id": pid}) == next(
            d for d in before["products"] if d["product_id"] == pid)


def _arch(door, pid, sku, model, **extra):
    door.repo.collection.insert_one({
        "product_id": pid, "sku": sku, "category": "SUNGLASS", "brand": "Carrera", "model": model,
        "is_active": False, "deleted_at": "2026-10-24",
        "attributes": {"brand_name": "Carrera", "model_no": model, "colour_code": "086HA", "lens_size": "54"},
        **extra})
    return door.repo.find_by_id(pid)


def test_restore_never_brings_back_a_second_copy_whoever_is_archived(door, monkeypatch):
    """BLOCK 2: both twins archived (the survivor by hand, keeping its key):
    the keyless one is refused -- restore the one holding the key -- and after
    the survivor is back, it is refused as a live duplicate."""
    from api.routers import catalog as cat

    kept = door("3001/S")
    loser = _arch(door, "P-ARCH", "SGX", "CARRERA 3001/S")
    door.repo.collection.update_one({"product_id": kept["product_id"]},
                                    {"$set": {"deleted_at": "x", "is_active": False}})
    monkeypatch.setattr(deps, "get_product_repository", lambda: door.repo)
    why = cat._restore_refusal(door.repo, loser)
    assert why and "archived as " + kept["sku"] in why
    survivor = door.repo.find_by_id(kept["product_id"])
    assert cat._restore_refusal(door.repo, survivor) is None
    cat._restore_product(None, survivor, _ADMIN, on_website=False)
    assert "already in the catalogue as " + kept["sku"] in cat._restore_refusal(door.repo, loser)


def test_restore_refuses_a_sizeless_twin_of_a_frame_catalogued_by_eye_size(door):
    """Restore runs the create door's own rule: EYE_SIZE_NEEDED too."""
    from api.routers import catalog as cat

    door("3001/S")
    door.repo.collection.insert_one({
        "product_id": "P-SL", "sku": "SGSL", "category": "SUNGLASS", "identity_key": "carrera|3001s|086ha",
        "brand": "Carrera", "model": "3001/S", "is_active": False, "deleted_at": "2026-10-24",
        "attributes": {"brand_name": "Carrera", "model_no": "3001/S", "colour_code": "086HA"}})
    why = cat._restore_refusal(door.repo, door.repo.find_by_id("P-SL"))
    assert why and why.startswith("Not restored")


def test_an_identity_edit_of_a_stale_row_is_checked_against_its_stored_key(door):
    """BLOCK 3: a row still on the brand-in-model key, edited to drop the brand,
    is re-keyed -- and refused, its twin already holds that key."""
    door("3001/S")
    door.repo.collection.insert_one({
        "product_id": "P-X", "sku": "SGXX", "category": "SUNGLASS", "identity_key": "carrera|carrera3001s|086ha|54",
        "brand": "Carrera", "model": "CARRERA 3001/S", "color": "086HA", "is_active": True,
        "attributes": {"brand_name": "Carrera", "model_no": "CARRERA 3001/S", "colour_code": "086HA",
                       "lens_size": "54"}})
    cur = door.repo.find_by_id("P-X")
    patch = {"attributes": {**cur["attributes"], "model_no": "3001/S"}, "model": "3001/S"}
    with pytest.raises(pm.ProductMasterError) as exc:
        pm.rekey_on_edit(cur, patch, door.repo)
    assert exc.value.code == "DUPLICATE_PRODUCT"


def test_script_a_rerun_after_apply_finds_nothing_left():
    from scripts import fix_brand_in_model as fix

    db = _seeded()
    for pid in ("p-old4", "p-new4"):  # the Shopify-blocked pair: a human's
        db["products"].delete_one({"product_id": pid})
    first = fix.plan(db)
    assert fix.unresolved(first) == 0
    fix.apply(db, first, "run-1")
    again = fix.plan(db)
    assert again["changes"] == [] and again["pairs"] == [] and fix.unresolved(again) == 0


def test_script_prints_the_undo_before_writing_and_stops_on_a_failure(monkeypatch, capsys):
    import pymongo
    from scripts import fix_brand_in_model as fix

    db = _seeded()
    monkeypatch.setenv("MONGO_URL", "mongodb://fake")
    monkeypatch.setattr(pymongo, "MongoClient", lambda *a, **k: {"ims_2_0": db})
    real = db["products"].update_one

    def boom(filt, update, *a, **k):
        if "identity_key" in (update.get("$set") or {}):
            raise RuntimeError("net drop")
        return real(filt, update, *a, **k)

    monkeypatch.setattr(db["products"], "update_one", boom)
    assert fix.main(["--apply"]) == 1
    out = capsys.readouterr().out
    rid = out.split("APPLYING run ", 1)[1].split(" ", 1)[0]
    assert out.index("APPLYING run") < out.index("FAILED at pair")
    assert f"--revert --run-id {rid}" in out.split("FAILED at", 1)[1]
    monkeypatch.setattr(db["products"], "update_one", real)
    assert fix.revert(db, rid) > 0
    assert db["products"].find_one({"product_id": "p-new1"})["identity_key"] == "carrera|3001s|086ha|54"


def _mongo_seeded():
    import mongomock

    db = mongomock.MongoClient()["ims_2_0"]
    db["products"].create_index("identity_key", unique=True, sparse=True)
    src = _seeded()
    for name in ("products", "catalog_products", "catalog_variants", "product_images"):
        if src[name].docs:
            db[name].insert_many([dict(d) for d in src[name].docs])
    return db


def test_revert_keeps_a_later_edit_goes_on_past_a_refused_row_and_drops_the_cache(monkeypatch):
    from scripts import fix_brand_in_model as fix

    calls = []
    monkeypatch.setattr(fix, "_invalidate_lists", lambda: calls.append(1))
    db = _mongo_seeded()
    fix.apply(db, fix.plan(db), "run-r")
    # A later edit of the cleaned solo row is never overwritten.
    db["products"].update_one({"product_id": "p-solo"}, {"$set": {"attributes.model_no": "1001 S"}})
    # The survivor moves off K and a new product takes K: the loser cannot get it back.
    k = "carrera|3001s|086ha|54"
    db["products"].update_one({"product_id": "p-old1"}, {"$set": {"identity_key": "carrera|3001s|086hb|54"}})
    db["products"].insert_one(_row("p-fresh", "SG-FRESH", "3001/S", "086HA", "54", k))
    assert fix.revert(db, "run-r") > 0 and len(calls) == 2
    solo = db["products"].find_one({"product_id": "p-solo"})
    assert solo["attributes"]["model_no"] == "1001 S"  # the edit stands ...
    # ... and the write it was part of is left WHOLE: no old key beside a newer model.
    assert solo["identity_key"] == "carrera|1001s|807|52"
    assert "identity_key" not in db["products"].find_one({"product_id": "p-new1"})  # refused, reported
    assert not db["products"].find_one({"product_id": "p-old2"}).get("deleted_at")  # the rest went on


def test_the_form_hint_is_the_keys_own_fold():
    """One rule: GET /products/similar says brand_in_model exactly when the
    key folds the brand away -- glued only before a digit, as often as typed."""
    sg = "SUNGLASS"
    yes = [("Carrera", "CARRERA 3001/S"), ("Carrera", "Carrera3001/S"), ("Carrera", "CARRERA CARRERA 3001/S"),
           ("CK", "CK 19500"), ("CK", "CK19500")]
    no = [("Carrera", "3001/S"), ("Prada", "PRADAX01"), ("Ray-Ban", "RAYBANRB2140"), ("Titan", "TITANIUM12"),
          ("Carrera", "CARRERA")]
    for brand, model in yes:
        assert pm.brand_in_model({"brand_name": brand, "model_no": model}, sg), model
    for brand, model in no:
        assert not pm.brand_in_model({"brand_name": brand, "model_no": model}, sg), model
    assert pm.model_without_brand("Carrera", "CARRERA CARRERA 3001/S") == pm.model_without_brand(
        "Carrera", "3001/S")
    hit = pm.find_similar_products(None, category=sg, attributes={"brand_name": "Carrera", "model_no": "CARRERA 1"})
    assert hit["brand_in_model"] is True


def test_the_key_backfill_never_writes_a_key_another_row_holds():
    from scripts import backfill_dedupe_prep as dd

    coll = StrictCollection("products", [
        {"product_id": "P1", "sku": "A", "category": "SUNGLASS", "identity_key": "carrera|3001s|086ha|54",
         "attributes": {"brand_name": "Carrera", "model_no": "3001/S", "colour_code": "086HA", "lens_size": "54"}},
        {"product_id": "P2", "sku": "B", "category": "SUNGLASS", "deleted_at": "x",
         "attributes": {"brand_name": "Carrera", "model_no": "CARRERA 3001/S", "colour_code": "086HA",
                        "lens_size": "54"}},
    ])
    stats = dd.run_dedupe(coll, apply=True)
    assert stats["identity_held"] == 1 and stats["identity_backfilled"] == 0
    assert "identity_key" not in coll.find_one({"product_id": "P2"})


def test_the_identity_migration_calls_an_archived_twin_expected_not_a_collision():
    from scripts import migrate_identity_key_tighten as mig

    coll = StrictCollection("products", [
        {"product_id": "P1", "sku": "A", "category": "SUNGLASS", "identity_key": "carrera|3001s|086ha|54",
         "attributes": {"brand_name": "Carrera", "model_no": "3001/S", "colour_code": "086HA", "lens_size": "54"}},
        {"product_id": "P2", "sku": "B", "category": "SUNGLASS", "deleted_at": "x",
         "attributes": {"brand_name": "Carrera", "model_no": "CARRERA 3001/S", "colour_code": "086HA",
                        "lens_size": "54"}},
    ])
    stats = mig.run(coll, apply=False)
    assert stats["collisions"] == 0 and stats["archived_twin"] == 1



# ---------------------------------------------------------------------------
# Round 5 (final)
# ---------------------------------------------------------------------------


def _bulk(door, monkeypatch, ids, apply, twins=None):
    import asyncio

    from api.routers import catalog as cat

    twins = twins or {}
    monkeypatch.setattr(deps, "get_product_repository", lambda: door.repo)
    monkeypatch.setattr(cat, "_live_skus", lambda skus: set())
    monkeypatch.setattr(cat, "_twins_or_503",
                        lambda repo, spines: [twins.get((s or {}).get("product_id")) for s in spines])
    monkeypatch.setattr(cat, "_get_catalog_product", lambda pid: dict(twins[pid]) if pid in twins else None)
    # The Restore door also reads the permanent-delete state (#1273): an
    # empty store of it (this fixture's get_db is the database itself).
    monkeypatch.setattr(cat, "_get_db", lambda: StrictDB())
    body = cat.BulkProductIds(product_ids=ids, apply=apply)
    return asyncio.run(cat.bulk_restore_products(body, _ADMIN))["results"]


def test_bulk_restore_of_both_twins_brings_back_one(door, monkeypatch):
    """The endpoint asks the rule per row: in one body, the twin holding the
    key comes back, the other is refused -- dry run and apply alike."""
    kept = door("3001/S")
    _arch(door, "P-ARCH", "SGX", "CARRERA 3001/S")
    door.repo.collection.update_one({"product_id": kept["product_id"]},
                                    {"$set": {"deleted_at": "x", "is_active": False}})
    dry = {r["product_id"]: r for r in _bulk(door, monkeypatch, ["P-ARCH", kept["product_id"]], False)}
    assert dry["P-ARCH"]["status"] == "refused" and "archived as" in dry["P-ARCH"]["reason"]
    assert dry[kept["product_id"]]["status"] == "ready"
    done = {r["product_id"]: r for r in _bulk(door, monkeypatch, [kept["product_id"], "P-ARCH"], True)}
    assert done[kept["product_id"]]["status"] == "done"
    assert done["P-ARCH"]["status"] == "refused"
    assert "already in the catalogue as " + kept["sku"] in done["P-ARCH"]["reason"]
    assert door.repo.find_by_id("P-ARCH")["deleted_at"]


def test_restore_refuses_a_copy_whose_barcode_a_live_product_has(door):
    """The create door's whole rule: the barcode is asked too."""
    from api.routers import catalog as cat

    live = door("3001/S")
    door.repo.collection.update_one({"product_id": live["product_id"]}, {"$set": {"barcode": "8056597054324"}})
    other = _arch(door, "P-BC", "SGBC", "5001", barcode="8056597054324")
    why = cat._restore_refusal(door.repo, other)
    assert why and live["sku"] in why


def test_script_a_failed_pair_stops_the_run_and_writes_nothing_after_it(monkeypatch):
    from scripts import fix_brand_in_model as fix

    db = _seeded()
    before = _snapshot(db)
    p = fix.plan(db)
    first = next(e for e in p["pairs"] if e.get("other") and not e["blockers"])
    real = db["products"].update_one

    def boom(filt, update, *a, **k):
        if filt.get("product_id") == first["survivor"]["product_id"]:
            raise RuntimeError("net drop")
        return real(filt, update, *a, **k)

    monkeypatch.setattr(db["products"], "update_one", boom)
    stats = fix.apply(db, p, "run-stop")
    assert stats["failed"] == 1
    later = [e for e in p["pairs"] if e is not first and e.get("other") and not e["blockers"]]
    touched = {first["survivor"]["product_id"], first["loser"]["product_id"]}
    for pid in {d["product_id"] for d in before["products"]} - touched:
        assert db["products"].find_one({"product_id": pid}) == next(
            d for d in before["products"] if d["product_id"] == pid), pid
    assert later  # there was work after the failure, and none of it ran
    rows = db[fix.BACKUPS].docs
    assert rows and all(r["filter"].get("product_id") in touched or r["collection"] == "catalog_products"
                        for r in rows)
    assert all(r["filter"].get("id") in touched or r["collection"] == "products" for r in rows)


def test_script_cleans_the_twins_flat_model_too():
    from scripts import fix_brand_in_model as fix

    db = _seeded()
    db["catalog_products"].update_one({"id": "t-solo"}, {"$set": {"model": "CARRERA 1001/S"}})
    fix.apply(db, fix.plan(db), "run-tw")
    assert db["catalog_products"].find_one({"id": "t-solo"})["model"] == "1001/S"
    fix.revert(db, "run-tw")
    assert db["catalog_products"].find_one({"id": "t-solo"})["model"] == "CARRERA 1001/S"



# ---------------------------------------------------------------------------
# Follow-up (fix/restore-barcode-probe): the barcode half of the one rule
# ---------------------------------------------------------------------------

_CODE = "8056597054324"


def test_restore_reads_the_gtin_attribute_as_the_barcode(door):
    """Restore asks the barcode the way Add product does (assert_gtin_free):
    the code may sit in attributes.gtin, not the legacy products.barcode."""
    from api.routers import catalog as cat

    live = door("3001/S")
    door.repo.collection.update_one({"product_id": live["product_id"]}, {"$set": {"barcode": _CODE}})
    arch = _arch(door, "P-GT", "SGGT", "5002")
    door.repo.collection.update_one({"product_id": "P-GT"}, {"$set": {"attributes.gtin": _CODE}})
    why = cat._restore_refusal(door.repo, door.repo.find_by_id("P-GT"))
    assert why and live["sku"] in why and arch



def test_restore_counts_only_live_barcode_holders(door, monkeypatch):
    """Two ARCHIVED copies sharing an EAN never block each other: the one
    holding the key comes back; the other is then refused by the per-row
    check (now a live holder) -- no deadlock."""
    from api.routers import catalog as cat

    kept = door("3001/S")
    door.repo.collection.update_one({"product_id": kept["product_id"]},
                                    {"$set": {"deleted_at": "x", "is_active": False, "barcode": _CODE}})
    _arch(door, "P-TWIN", "SGTWIN", "CARRERA 3001/S", barcode=_CODE)
    survivor = door.repo.find_by_id(kept["product_id"])
    assert cat._restore_refusal(door.repo, survivor) is None
    monkeypatch.setattr(deps, "get_product_repository", lambda: door.repo)
    cat._restore_product(None, survivor, _ADMIN, on_website=False)
    assert cat._restore_refusal(door.repo, door.repo.find_by_id("P-TWIN"))


def _with_twins(door, twins):
    db = StrictDB()
    db.seed("catalog_products", twins)
    door.repo.collection.database = db  # find_twin_by_barcode reads the raw db
    return db


def test_restore_barcode_check_reads_twins_live_only_and_never_its_own(door):
    from api.routers import catalog as cat

    arch = _arch(door, "P-OWN", "SGOWN", "5003", barcode=_CODE)
    # Its own twin (keyed on its id) holds the code -- LIVE, so only the
    # own-twin rule can let it pass: no clash.
    _with_twins(door, [{"id": "P-OWN", "sku": "SGOWN", "attributes": {"gtin": _CODE}}])
    assert cat._restore_refusal(door.repo, arch) is None
    # Another product's ARCHIVED twin: no clash; a LIVE one: refused.
    _with_twins(door, [{"id": "T-OLD", "sku": "SGOLD", "attributes": {"gtin": _CODE}, "deleted_at": "x"}])
    assert cat._restore_refusal(door.repo, arch) is None
    _with_twins(door, [{"id": "T-LIVE", "sku": "SGLIVE", "attributes": {"gtin": _CODE}}])
    assert "SGLIVE" in cat._restore_refusal(door.repo, arch)


def test_restore_checks_each_barcode_field(door):
    """SHOULD 1: products.barcode AND attributes.gtin, each distinct value."""
    from api.routers import catalog as cat

    live = door("3001/S")
    door.repo.collection.update_one({"product_id": live["product_id"]}, {"$set": {"barcode": "4006381333931"}})
    _arch(door, "P-TWO", "SGTWO", "5004", barcode=_CODE)
    door.repo.collection.update_one({"product_id": "P-TWO"}, {"$set": {"attributes.gtin": "4006381333931"}})
    why = cat._restore_refusal(door.repo, door.repo.find_by_id("P-TWO"))
    assert why and live["sku"] in why


def test_restore_refuses_when_the_barcode_cannot_be_read(door):
    """SHOULD 2: a read failure is a plain refusal -- never a 500, never a pass."""
    from api.routers import catalog as cat

    arch = _arch(door, "P-RD", "SGRD", "5005", barcode=_CODE)
    real = door.repo.collection.find_one

    def flaky(flt=None, *a, **k):
        if "$or" in (flt or {}):
            raise RuntimeError("down")
        return real(flt, *a, **k)

    door.repo.collection.find_one = flaky
    try:
        assert cat._restore_refusal(door.repo, arch) == (
            "Not restored: could not check the barcode right now, try again.")
    finally:
        door.repo.collection.find_one = real

    class _Down:
        def __getitem__(self, name):
            raise RuntimeError("down")

    door.repo.collection.database = _Down()
    assert "could not check the barcode" in cat._restore_refusal(door.repo, arch)


def test_restore_checks_the_barcode_of_keyless_rows_and_spineless_twins(door):
    """SHOULD 3: no identity key (or no spine at all) still asks the barcode."""
    from api.routers import catalog as cat

    live = door("3001/S")
    door.repo.collection.update_one({"product_id": live["product_id"]}, {"$set": {"barcode": _CODE}})
    door.repo.collection.insert_one({"product_id": "P-KL", "sku": "SGKL", "category": "SUNGLASS",
                                     "deleted_at": "x", "barcode": _CODE, "attributes": {}})
    assert live["sku"] in cat._restore_refusal(door.repo, door.repo.find_by_id("P-KL"))
    twin = {"id": "T-ONLY", "sku": "BVI-9", "deleted_at": "x", "attributes": {"gtin": _CODE}}
    assert live["sku"] in cat._restore_refusal(door.repo, None, twin)



def test_bulk_restore_refuses_a_spineless_twin_whose_gtin_a_live_product_has(door, monkeypatch):
    """The endpoint hands the twin to the rule: an archived import with no
    billing row, carrying a code a live product holds, stays archived."""
    live = door("3001/S")
    door.repo.collection.update_one({"product_id": live["product_id"]}, {"$set": {"barcode": _CODE}})
    twin = {"id": "T-SPINELESS", "sku": "BVI-7", "deleted_at": "x", "attributes": {"gtin": _CODE}}
    for apply in (False, True):
        [row] = _bulk(door, monkeypatch, ["T-SPINELESS"], apply, twins={"T-SPINELESS": twin})
        assert row["status"] == "refused" and live["sku"] in row["reason"], (apply, row)
    # A SPINE id whose twin carries the code: dry run and apply give one answer.
    _arch(door, "P-SP", "SGSP", "5007")
    sp_twin = {"id": "P-SP", "sku": "SGSP", "deleted_at": "x", "attributes": {"gtin": _CODE}}
    for apply in (False, True):
        [row] = _bulk(door, monkeypatch, ["P-SP"], apply, twins={"P-SP": sp_twin})
        assert row["status"] == "refused" and live["sku"] in row["reason"], (apply, row)


def test_restore_checks_the_codes_of_the_spine_and_its_twin_alike(door):
    """The union of every code the row and its twin carry; a stored value that
    is not text (a list) is skipped, never a 500."""
    from api.routers import catalog as cat

    live = door("3001/S")
    door.repo.collection.update_one({"product_id": live["product_id"]}, {"$set": {"barcode": _CODE}})
    spine = _arch(door, "P-UN", "SGUN", "5006", barcode=["not", "a", "code"])
    twin = {"id": "P-UN", "sku": "SGUN", "deleted_at": "x", "gtin": _CODE}
    assert live["sku"] in cat._restore_refusal(door.repo, spine, twin)
    assert cat._restore_refusal(door.repo, spine, None) is None



def test_restore_never_refuses_on_a_code_the_website_would_not_publish(door):
    """One rule with the push (sanitise_gtin): a junk stub two rows share, or a
    restricted in-store code (GS1 20-29), is no public barcode -- never a clash."""
    from api.routers import catalog as cat
    from api.services.gtin import sanitise_gtin

    for code in ("92939293", "2000000000015"):
        assert sanitise_gtin(code) is None, code
        live = door.repo.collection.find_one({"sku": "SG-CARRERA-3001S-086HA-54"}) or door("3001/S")
        door.repo.collection.update_one({"product_id": live["product_id"]}, {"$set": {"barcode": code}})
        pid = "P-J" + code
        _arch(door, pid, "SG" + code, "6" + code[:3], barcode=code)
        twin = {"id": pid, "sku": "SG" + code, "deleted_at": "x", "attributes": {"gtin": code}}
        assert cat._restore_refusal(door.repo, door.repo.find_by_id(pid), twin) is None, code


def test_restore_reads_an_int_upc_that_lost_its_leading_zero(door):
    """Follow-up of #1284: 036000291452 stored as the INT 36000291452 is the
    same UPC; it is zero-padded (ints only) before the push's rule reads it."""
    from api.routers import catalog as cat

    live = door("3001/S")
    door.repo.collection.update_one({"product_id": live["product_id"]}, {"$set": {"barcode": "036000291452"}})
    _arch(door, "P-INT", "SGINT", "5006", barcode=36000291452)
    why = cat._restore_refusal(door.repo, door.repo.find_by_id("P-INT"))
    assert why and live["sku"] in why


def test_restore_never_pads_a_restricted_gtin8_into_a_public_code(door):
    """A GTIN-8 in 20-29 is in-store only however it is stored: padding it to
    12 digits would launder it into a 'valid' 000020000004."""
    from api.routers import catalog as cat
    from api.services.gtin import sanitise_gtin

    assert sanitise_gtin("000020000004") == "000020000004"  # the laundered spelling IS valid
    live = door("3001/S")
    door.repo.collection.update_one({"product_id": live["product_id"]}, {"$set": {"barcode": "000020000004"}})
    _arch(door, "P-G8", "SGG8", "5007", barcode=20000004)
    assert cat._restore_refusal(door.repo, door.repo.find_by_id("P-G8")) is None
