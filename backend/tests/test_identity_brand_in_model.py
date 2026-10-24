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
    assert stats == {"archived": 3, "rekeyed": 3, "skipped": 1}
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


def test_script_an_archived_row_never_survives():
    """B2: an archived copy loses whatever else it has, and the report says so."""
    from scripts import fix_brand_in_model as fix

    db = _seeded()
    db["products"].update_one({"product_id": "p-new2"}, {"$set": {"deleted_at": "2026-10-01", "is_active": False}})
    e = _pairs(fix.plan(db))["carrera|flaglab16|ky21v|99"]
    assert e["survivor"]["sku"] == "SGCARRERACARRERAFLAGLAB16KY21V"  # despite the archived row's photos
    assert e["blockers"] == ["already archived"]
    assert fix._why(e["fl"]).startswith("ARCHIVED")


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
