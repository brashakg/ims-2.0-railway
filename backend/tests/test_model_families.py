"""
G1 -- ONE MODEL, its colours and sizes as variants (owner 2026-10-18)
====================================================================
Same model number in other colours / eye sizes = variants of one product.
Each keeps its own spine (SKU, barcode, price, stock, GST); the family is one
link -- spine variant_of = the LEAD (first catalogued: created_at, tie SKU),
twin ecom.variant_of, catalog_variants row parent_product_id -- carrying
option_color 'Gold (001)' and option_size on EVERY row, the lead's own too.

Pinned here (mongomock; no network, no Shopify):
  * link_to_family / unlink_from_family / promote_new_lead write all three
    halves, refuse chains / other models / own live listings, and the lead's
    row keeps no mrp copy (its price is the lead itself)
  * create with variant_of; a new item of an existing model is auto-linked
    to its lead; 'This is a different model' (family_opt_out) is not; a lens
    (no colours) is never linked
  * model-level edits: refused on a variant, reach every variant from the lead
  * the registry marks each field's level in ONE place (all_category_specs)
  * the grouped Catalogue list: one row per model, filters match variants,
    sort order kept, total = models
  * the till's SKU lookup of a variant is unchanged (its own SKU and price)
  * a collection member that is a variant resolves to its model's listing
  * the migration: dry-run writes nothing; apply links a 15-item colour+size
    model, a colour-only model, strips a brand typed into the model, joins
    spelling variants, resolves shape/gender to the lead, keeps blank sizes
    blank and reports them; a second plan links nothing; revert restores every
    document exactly; SKUs, barcodes and stock_units never move

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest backend/tests/test_model_families.py -q
"""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

mongomock = pytest.importorskip("mongomock")

from api import dependencies as deps  # noqa: E402
from api.routers import products as products_mod  # noqa: E402
from api.services import product_master as pm  # noqa: E402
from database.repositories.product_repository import ProductRepository  # noqa: E402

ADMIN = {"user_id": "u-admin", "username": "admin", "roles": ["ADMIN"]}
T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)

_MIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "scripts",
    "migrate_model_families.py",
)


def _migration():
    spec = importlib.util.spec_from_file_location("migrate_model_families", _MIG_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Conn:
    """The dependencies.get_db() shape over a mongomock database."""

    is_connected = True

    def __init__(self, db):
        self.db = db

    def get_collection(self, name):
        return self.db[name]

    def __getitem__(self, name):
        return self.db[name]


def _frame(pid, colour_code, colour_name=None, size=None, *, minutes=0, model="RB2140", brand="Ray-Ban",
           shape=None, gender=None, mrp=5000, live=False, category="SUNGLASS", **attrs):
    a = {"brand_name": brand, "model_no": model, "colour_code": colour_code}
    if colour_name:
        a["frame_color"] = colour_name
    if size:
        a["lens_size"] = size
    if shape:
        a["shape"] = shape
    if gender:
        a["gender"] = gender
    a.update(attrs)
    spine = {
        "product_id": pid,
        "pim_product_id": "twin-" + pid,
        "sku": f"SG-{pm.normalise_identity_component(brand).upper()}-{pm.normalise_identity_component(model).upper()}-{colour_code}-{size or ''}",
        "category": category,
        "brand": brand,
        "model": model,
        "mrp": mrp,
        "offer_price": mrp,
        "attributes": a,
        "is_active": True,
        "created_at": T0 + timedelta(minutes=minutes),
    }
    spine["identity_key"] = pm.compute_identity_key(*pm.identity_parts(a, category))
    twin = {"id": "twin-" + pid, "sku": spine["sku"], "attributes": dict(a), "ecom": {"status": "DRAFT", "locally_modified": True}}
    if live:
        twin["ecom"]["shopify_product_id"] = "gid://shopify/Product/" + pid
    row = {"sku": spine["sku"], "parent_product_id": "twin-" + pid, "parent_sku": spine["sku"]}
    return spine, twin, row


@pytest.fixture
def db(monkeypatch):
    client = mongomock.MongoClient()
    raw = client["ims_test_families"]
    conn = _Conn(raw)
    monkeypatch.setattr(deps, "get_db", lambda: conn)
    monkeypatch.setattr(products_mod, "get_product_repository", lambda: ProductRepository(raw["products"]))
    return raw


def _seed(db, *items):
    for spine, twin, row in items:
        db["products"].insert_one(copy.deepcopy(spine))
        db["catalog_products"].insert_one(copy.deepcopy(twin))
        db["catalog_variants"].insert_one(copy.deepcopy(row))


def _p(db, pid):
    return db["products"].find_one({"product_id": pid})


def _row(db, pid):
    return db["catalog_variants"].find_one({"sku": _p(db, pid)["sku"]})


def _twin(db, pid):
    return db["catalog_products"].find_one({"id": "twin-" + pid})


# ---------------------------------------------------------------------------
# link / unlink / promote
# ---------------------------------------------------------------------------


def test_link_writes_spine_twin_and_row_with_colour_and_size(db):
    _seed(db, _frame("L", "001", "Gold", "52"), _frame("C", "002", "Black", "55", minutes=5, mrp=6500))
    out = pm.link_to_family(_Conn(db), "C", "L", actor="u1")
    assert out["changed"] is True
    assert _p(db, "C")["variant_of"] == "L"
    link = _twin(db, "C")["ecom"]
    assert link["variant_of"] == {"product_id": "L", "twin_id": "twin-L", "sku": _p(db, "L")["sku"]}
    assert link["locally_modified"] is False  # a variant owns no listing
    child = _row(db, "C")
    assert child["parent_product_id"] == "twin-L"
    assert (child["option_color"], child["option_size"], child["mrp"]) == ("Black (002)", "55", 6500)
    lead = _row(db, "L")
    assert (lead["option_color"], lead["option_size"]) == ("Gold (001)", "52")
    assert lead["parent_product_id"] == "twin-L" and "mrp" not in lead  # one holder: the lead
    # the SKUs never move
    assert _p(db, "C")["sku"].endswith("-002-55")
    # again = no-op
    assert pm.link_to_family(_Conn(db), "C", "L")["changed"] is False


@pytest.mark.parametrize(
    "child,parent,expect",
    [
        ("C", "C", 422),  # itself
        ("O", "L", 422),  # another model number
        ("C", "X", 404),  # unknown
        ("LIVE", "L", 409),  # own live listing: the website step (G2)
    ],
)
def test_link_refusals(db, child, parent, expect):
    _seed(
        db,
        _frame("L", "001"),
        _frame("C", "002", minutes=1),
        _frame("O", "003", model="RB3025", minutes=2),
        _frame("LIVE", "004", minutes=3, live=True),
    )
    with pytest.raises(pm.ProductMasterError) as e:
        pm.link_to_family(_Conn(db), child, parent)
    assert e.value.status == expect
    assert _p(db, child) is None or not _p(db, child).get("variant_of")


def test_no_chains_either_way(db):
    _seed(db, _frame("L", "001"), _frame("C", "002", minutes=1), _frame("D", "003", minutes=2))
    pm.link_to_family(_Conn(db), "C", "L")
    with pytest.raises(pm.ProductMasterError):
        pm.link_to_family(_Conn(db), "D", "C")  # the parent is a variant
    with pytest.raises(pm.ProductMasterError):
        pm.link_to_family(_Conn(db), "L", "D")  # the child leads variants


def test_unlink_makes_it_its_own_model_and_opts_out(db):
    _seed(db, _frame("L", "001", "Gold"), _frame("C", "002", "Black", minutes=1))
    pm.link_to_family(_Conn(db), "C", "L")
    pm.unlink_from_family(_Conn(db), "C", actor="u1")
    c = _p(db, "C")
    assert "variant_of" not in c and c["family_opt_out"] is True
    assert "variant_of" not in _twin(db, "C")["ecom"]
    row = _row(db, "C")
    assert row["parent_product_id"] == "twin-C" and "mrp" not in row
    # a product of its own again: neither row carries options (one-item listings)
    assert "option_color" not in row and "option_color" not in _row(db, "L")
    # and the create door / migration never link it back
    assert pm.find_family_lead(db["products"], c, exclude_id="L") is None


def test_unlink_refused_while_on_the_models_live_listing(db):
    _seed(db, _frame("L", "001"), _frame("C", "002", minutes=1))
    pm.link_to_family(_Conn(db), "C", "L")
    db["catalog_variants"].update_one({"sku": _p(db, "C")["sku"]}, {"$set": {"shopify_variant_id": "gid://v/1"}})
    with pytest.raises(pm.ProductMasterError) as e:
        pm.unlink_from_family(_Conn(db), "C")
    assert e.value.status == 409


def test_archived_lead_hands_the_model_to_the_next_first_catalogued(db):
    _seed(db, _frame("L", "001"), _frame("B", "002", minutes=9), _frame("A", "003", minutes=3))
    pm.link_to_family(_Conn(db), "B", "L")
    pm.link_to_family(_Conn(db), "A", "L")
    db["products"].update_one({"product_id": "L"}, {"$set": {"deleted_at": "2026-10-18T00:00:00Z"}})
    assert pm.promote_new_lead(_Conn(db), "L") == "A"  # earliest created_at
    assert "variant_of" not in _p(db, "A")
    assert _p(db, "B")["variant_of"] == "A" and _p(db, "L")["variant_of"] == "A"
    assert _row(db, "B")["parent_product_id"] == "twin-A"
    assert _row(db, "A")["parent_product_id"] == "twin-A" and "mrp" not in _row(db, "A")
    assert _twin(db, "L")["ecom"]["variant_of"]["product_id"] == "A"


def test_lead_tie_breaks_on_sku():
    a = {"product_id": "x", "sku": "B", "created_at": T0}
    b = {"product_id": "y", "sku": "A", "created_at": T0}
    assert pm.lead_of([a, b])["product_id"] == "y"


def test_same_colour_and_size_are_told_apart_by_the_lens():
    a, _, _ = _frame("a", "001", "Gold", lens_colour="Green", polarization="Yes")
    b, _, _ = _frame("b", "001", "Gold", lens_colour="Grey", minutes=1)
    b["sku"] += "-2"  # the minted SKU of a second lens of one colour
    values, clashes = pm.family_option_values([a, b])
    assert values[a["sku"]]["option_color"] == "Gold (001) / Green Polarized"
    assert values[b["sku"]]["option_color"] == "Gold (001) / Grey"
    assert clashes == []


# ---------------------------------------------------------------------------
# the create door
# ---------------------------------------------------------------------------


def _create(db, attrs, category="SUNGLASS", **kw):
    mrp = kw.pop("mrp", 5000)
    return pm.create_product(
        category=category,
        attributes=attrs,
        mrp=mrp,
        # Offer = MRP unless a test says otherwise (an offer deeper than the
        # item's cap is refused -- pricing_caps.offer_write_problem).
        offer_price=kw.pop("offer_price", mrp),
        actor="u-admin",
        actor_name="admin",
        product_repo=ProductRepository(db["products"]),
        db=_Conn(db),
        **kw,
    )


def test_a_new_colour_of_an_existing_model_is_linked_automatically(db):
    lead = _create(db, {"brand_name": "Ray-Ban", "model_no": "RB2140", "colour_code": "901", "lens_size": "50"})
    assert "variant_of" not in lead
    # alone, its row is as before: no options (the push of a single item is unchanged)
    assert "option_color" not in (db["catalog_variants"].find_one({"sku": lead["sku"]}) or {})
    child = _create(db, {"brand_name": "Ray-Ban", "model_no": "RB 2140", "colour_code": "902", "lens_size": "54"}, mrp=6100)
    assert child["variant_of"] == lead["product_id"]
    twin = db["catalog_products"].find_one({"id": child["pim_product_id"]})
    assert twin["ecom"]["variant_of"]["product_id"] == lead["product_id"]
    row = db["catalog_variants"].find_one({"sku": child["sku"]})
    assert row["parent_product_id"] == lead["pim_product_id"]
    assert (row["option_color"], row["option_size"], row["mrp"]) == ("902", "54", 6100)
    lead_row = db["catalog_variants"].find_one({"sku": lead["sku"]})
    assert (lead_row["option_color"], lead_row["option_size"]) == ("901", "50")


def test_different_model_opt_out_and_explicit_variant_of(db):
    lead = _create(db, {"brand_name": "Oakley", "model_no": "OO9208", "colour_code": "01"})
    own = _create(db, {"brand_name": "Oakley", "model_no": "OO9208", "colour_code": "02"}, family_opt_out=True)
    assert "variant_of" not in own and own["family_opt_out"] is True
    explicit = _create(db, {"brand_name": "Oakley", "model_no": "OO9208", "colour_code": "03"}, variant_of=lead["product_id"])
    assert explicit["variant_of"] == lead["product_id"]
    with pytest.raises(pm.ProductMasterError) as e:
        _create(db, {"brand_name": "Oakley", "model_no": "OO9999", "colour_code": "04"}, variant_of=lead["product_id"])
    assert e.value.status == 422  # another model number is not a variant


def test_a_lens_is_never_put_in_a_model(db):
    first = _create(db, {"brand_name": "Essilor", "subbrand": "Crizal", "index": "1.56", "coating": "HC"}, category="OPTICAL_LENS")
    second = _create(db, {"brand_name": "Essilor", "subbrand": "Crizal", "index": "1.67", "coating": "HC"}, category="OPTICAL_LENS")
    assert "variant_of" not in second and first["product_id"] != second["product_id"]


def test_the_create_api_forwards_variant_of_and_the_opt_out():
    body = products_mod.ProductCreate(
        category="SUNGLASS", brand="Ray-Ban", model="RB2140", mrp=100, offer_price=100,
        variant_of="lead-1", family_opt_out=True
    )
    payload = products_mod._canonical_door_payload(body)
    assert payload["variant_of"] == "lead-1" and payload["family_opt_out"] is True


# ---------------------------------------------------------------------------
# model-level fields
# ---------------------------------------------------------------------------


def test_registry_marks_each_fields_level_in_one_place():
    frame = next(s for s in pm.all_category_specs() if s["code"] == "SUNGLASS")
    levels = {f["name"]: f["level"] for f in frame["fields"]}
    assert frame["has_families"] is True
    assert set(frame["model_fields"]) == {"brand_name", "model_no", "model_name", "shape", "gender"}
    # owner correction 2026-10-18: these differ inside a model
    for k in ("frame_material", "lens_material", "lens_colour", "tint", "polarization", "colour_code", "lens_size"):
        assert levels[k] == "variant"
    lens = next(s for s in pm.all_category_specs() if s["code"] == "OPTICAL_LENS")
    assert lens["has_families"] is False


def test_model_fields_are_edited_on_the_model_and_reach_every_variant(db):
    repo = ProductRepository(db["products"])
    lead = _create(db, {"brand_name": "Ray-Ban", "model_no": "RB4165", "colour_code": "601", "shape": "Square"})
    child = _create(db, {"brand_name": "Ray-Ban", "model_no": "RB4165", "colour_code": "710", "shape": "Square"})
    assert child["variant_of"] == lead["product_id"]
    with pytest.raises(pm.ProductMasterError) as e:
        pm.update_product(product_id=child["product_id"], patch={"attributes": {**child["attributes"], "shape": "Round"}},
                          actor="u1", product_repo=repo, db=_Conn(db))
    assert e.value.status == 422 and e.value.field == "shape"
    # a variant field on the variant is its own
    pm.update_product(product_id=child["product_id"], patch={"attributes": {**child["attributes"], "lens_colour": "Brown"}},
                      actor="u1", product_repo=repo, db=_Conn(db))
    out = pm.update_product(product_id=lead["product_id"], patch={"attributes": {**lead["attributes"], "shape": "Round"}},
                            actor="u1", product_repo=repo, db=_Conn(db))
    assert out["family_not_updated"] == []
    assert _p(db, child["product_id"])["attributes"]["shape"] == "Round"
    assert _p(db, child["product_id"])["attributes"]["lens_colour"] == "Brown"
    assert _p(db, child["product_id"])["sku"] == child["sku"]


# ---------------------------------------------------------------------------
# the grouped Catalogue list
# ---------------------------------------------------------------------------

_DEFAULTS = {
    "category": None, "brand": None, "search": None, "match": None, "tag": None,
    "created_by": None, "store_id": None, "skip": 0, "limit": 50, "is_active": None,
    "photo": None, "sort": None, "frame_color": None, "lens_colour": None, "gender": None,
    "shape": None, "frame_type": None, "frame_material": None, "updated_by": None,
    "date_on": None, "date_from": None, "date_to": None, "flag": None, "group": None,
}


@pytest.fixture
def list_cache(monkeypatch):
    import api.services.cache as cache_mod

    class _NoCache:
        TTL_MEDIUM = 300

        def get(self, k):
            return None

        def set(self, k, v, ttl=0):
            pass

        def delete_pattern(self, p):
            pass

    monkeypatch.setattr(cache_mod, "cache", _NoCache())


def _list(**kw):
    params = dict(_DEFAULTS, current_user=ADMIN)
    params.update(kw)
    return asyncio.run(products_mod.list_products(**params))


def test_grouped_list_one_row_per_model_filters_match_variants(db, list_cache):
    _seed(
        db,
        _frame("L", "001", "Gold", "52", minutes=0),
        _frame("C1", "002", "Black", "52", minutes=1),
        _frame("C2", "003", "Black", "55", minutes=2),
        _frame("S", "777", "Red", model="RB3025", minutes=3),
    )
    pm.link_to_family(_Conn(db), "C1", "L")
    pm.link_to_family(_Conn(db), "C2", "L")
    out = _list(sort="oldest", is_active="true", group="model")
    assert [p["product_id"] for p in out["products"]] == ["L", "S"]
    assert out["total_count"] == 2
    fam = out["products"][0]["family"]
    assert fam["count"] == 3 and [m["color"] for m in fam["members"]] == ["Gold (001)", "Black (002)", "Black (003)"]
    assert "family" not in out["products"][1]
    # a filter matching only variants still shows the model (headed by a match)
    out = _list(sort="oldest", is_active="true", group="model", frame_color="black")
    assert [p["product_id"] for p in out["products"]] == ["C1"]
    assert out["products"][0]["family"]["lead_id"] == "L"
    # newest first: the model sits where its newest match sorts
    out = _list(sort="newest", is_active="true", group="model")
    assert [p["product_id"] for p in out["products"]] == ["S", "L"]
    # without group the list is exactly as before
    out = _list(sort="oldest", is_active="true")
    assert [p["product_id"] for p in out["products"]] == ["L", "C1", "C2", "S"]


def test_the_till_lookup_of_a_variant_is_its_own_sku_and_price(db):
    _seed(db, _frame("L", "001", mrp=5000), _frame("C", "002", minutes=1, mrp=6500))
    pm.link_to_family(_Conn(db), "C", "L")
    sku = _p(db, "C")["sku"]
    got = asyncio.run(products_mod.get_product_by_sku(sku, current_user=ADMIN))
    got = got.get("product", got) if isinstance(got, dict) else got
    assert got["sku"] == sku and got["mrp"] == 6500 and got["product_id"] == "C"


def test_a_collection_member_variant_resolves_to_its_models_listing(db):
    from api.services.shopify_push.collections import _member_product_gids

    _seed(db, _frame("L", "001", live=True), _frame("C", "002", minutes=1))
    pm.link_to_family(_Conn(db), "C", "L")
    coll = {"collection_type": "CUSTOM", "products": [
        {"sku": _p(db, "C")["sku"], "position": 0}, {"sku": _p(db, "L")["sku"], "position": 1}]}
    gids, skipped = _member_product_gids(db, coll)
    assert gids == ["gid://shopify/Product/L"] and skipped == 0


def test_similar_lookup_names_the_model_a_new_item_joins(db):
    _seed(db, _frame("L", "001", "Gold", "52"), _frame("C", "002", "Black", "55", minutes=1))
    hint = pm.find_similar_products(
        db["products"], category="SUNGLASS", attributes={"brand_name": "Ray-Ban", "model_no": "RB2140", "colour_code": "009"}
    )["family"]
    assert hint["lead_id"] == "L" and hint["count"] == 2
    assert hint["chips"] == ["Gold (001) 52", "Black (002) 55"]


# ---------------------------------------------------------------------------
# the migration
# ---------------------------------------------------------------------------


def _mig_world(db):
    items = []
    # a 15-item colour + size model (5 colours x 3 eye sizes), the lead third
    n = 0
    for c, name in (("001", "Gold"), ("002", "Black"), ("003", "Havana"), ("004", "Silver"), ("005", "Blue")):
        for size in ("52", "55", "58"):
            n += 1
            items.append(_frame(f"F{n:02d}", c, name, size, minutes=10 + n, model="RB3025",
                                shape="Aviator" if n != 7 else "Pilot", gender="Unisex"))
    items[2][0]["created_at"] = T0  # F03 is the first catalogued
    # a colour-only model (no eye sizes)
    items += [_frame(f"W{i}", c, minutes=40 + i, model="RB4105") for i, c in enumerate(("601", "710", "902"))]
    # a model typed with the brand in front, and the same model typed clean
    items += [
        _frame("CA1", "807", minutes=50, brand="Carrera", model="CARRERA CA PRW 5/S/IN"),
        _frame("CA2", "086", minutes=51, brand="Carrera", model="CA PRW 5/S/IN"),
    ]
    # spelling variants of one model number
    items += [
        _frame("PL1", "807", minutes=60, brand="Polaroid", model="PLD 4162/S"),
        _frame("PL2", "003", minutes=61, brand="Polaroid", model="PLD4162/S", shape="Round"),
    ]
    # an item with blank eye size inside a sized model
    items += [_frame("F99", "006", "Green", None, minutes=99, model="RB3025")]
    # a single, an opted-out item, a live listing, a lens
    items += [_frame("SOLO", "100", model="RB9999", minutes=70)]
    out = _frame("OUT", "101", model="RB4105", minutes=71)
    out[0]["family_opt_out"] = True
    items += [out, _frame("LIVE", "102", model="RB4105", minutes=72, live=True)]
    # a model whose LEAD is on the website: linked in IMS only, no row moves
    items += [_frame("WL", "201", model="RB4200", minutes=80, live=True),
              _frame("WC", "202", model="RB4200", minutes=81, shape="Round")]
    _seed(db, *items)
    db["catalog_variants"].update_one({"sku": _p(db, "WL")["sku"]}, {"$set": {"discounted_price": 4500.0, "shopify_variant_id": "gid://v/WL"}})
    # F99 has no catalog_variants row yet: the run creates one (revert removes it)
    db["catalog_variants"].delete_one({"sku": _p(db, "F99")["sku"]})
    db["stock_units"].insert_many([{"unit_id": f"u{i}", "product_id": "F01", "sku": "x"} for i in range(3)])


def _snapshot(db):
    return {c: sorted((copy.deepcopy(d) for d in db[c].find({})), key=lambda d: str(d.get("sku") or d.get("unit_id") or d.get("id")))
            for c in ("products", "catalog_products", "catalog_variants", "stock_units")}


def test_migration_dry_run_apply_and_revert(db, capsys):
    mig = _migration()
    _mig_world(db)
    before = _snapshot(db)
    report = mig.plan(db)
    mig.print_report(report, "DRY-RUN")
    assert _snapshot(db) == before  # a dry run writes nothing
    fams = {f["key"].split("|")[2]: f for f in report["families"]}
    big = fams["rb3025"]
    assert big["members"] == 16 and big["lead_id"] == "F03" and len(big["link"]) == 15
    assert big["blank_sizes"] == [_p(db, "F99")["sku"]]
    assert len(fams["rb4105"]["link"]) == 2  # the opted-out and the live one stay out
    assert any(s["reason"] == "own live website listing" for s in report["skipped"])
    # the brand typed into the model groups with the clean spelling, unrewritten
    assert fams["caprw5sin"]["members"] == 2 and fams["pld4162s"]["members"] == 2
    assert [b["sku"] for b in report["brand_in_model"]] == [_p(db, "CA1")["sku"]]
    assert fams["rb4200"]["lead_on_website"] is True and fams["rb4200"]["link"] == ["WC"]
    # the live lead's blank shape is a website change: left for G2, reported
    assert [c["sku"] for c in report["model_field_left_live"]] == [_p(db, "WL")["sku"]]
    changes = {c["sku"]: c["set"] for c in report["model_field_changes"]}
    assert changes[_p(db, "F07")["sku"]] == {"shape": "Aviator"}  # the lead's value
    assert _p(db, "PL2")["sku"] not in changes  # model numbers are never rewritten
    assert "SOLO" not in str(report["families"])

    result = mig.apply(db, report)
    assert result["linked"] == 15 + 2 + 1 + 1 + 1
    assert all(_p(db, f"F{n:02d}").get("variant_of") == "F03" for n in range(1, 16) if n != 3)
    assert _p(db, "CA2")["variant_of"] == "CA1"  # first catalogued leads
    assert _p(db, "CA1")["attributes"]["model_no"] == "CARRERA CA PRW 5/S/IN"
    assert _p(db, "PL2")["attributes"]["model_no"] == "PLD4162/S"
    assert _p(db, "F07")["attributes"]["shape"] == "Aviator"
    assert _p(db, "PL1")["attributes"]["shape"] == "Round"  # the lead's blank: the model's value
    assert _p(db, "F05")["attributes"]["frame_color"] == "Black"  # variant fields: untouched
    lead_row = _row(db, "F03")
    assert (lead_row["option_color"], lead_row["option_size"]) == ("Gold (001)", "58")
    assert _row(db, "F99")["option_size"] == "" and _row(db, "F99")["parent_product_id"] == "twin-F03"
    assert _row(db, "W1")["option_size"] == "" and _row(db, "W1")["option_color"] == "710"
    # the live model: linked in IMS, rows exactly as they were, its twin untouched
    assert _p(db, "WC")["variant_of"] == "WL"
    assert "shape" not in _p(db, "WL")["attributes"] and "shape" not in _twin(db, "WL")["attributes"]
    after = _snapshot(db)
    for pid in ("WL", "WC"):
        sku = _p(db, pid)["sku"]
        assert [r for r in after["catalog_variants"] if r["sku"] == sku] == [r for r in before["catalog_variants"] if r["sku"] == sku]
    # SKUs, identity keys, barcodes and stock never move
    assert [d["sku"] for d in after["products"]] == [d["sku"] for d in before["products"]]
    assert [d.get("identity_key") for d in after["products"]] == [d.get("identity_key") for d in before["products"]]
    assert after["stock_units"] == before["stock_units"]

    again = mig.plan(db)
    assert sum(len(f["link"]) for f in again["families"]) == 0 and again["model_field_changes"] == []

    mig.revert(db)
    assert _snapshot(db) == before  # exactly as it was


# ---------------------------------------------------------------------------
# review round 1 (independent review of #1226)
# ---------------------------------------------------------------------------


def _key(brand, model_no, category="SUNGLASS", **attrs):
    return pm.family_key({"category": category, "attributes": {"brand_name": brand, "model_no": model_no, **attrs}})


def test_family_key_folds_a_brand_typed_into_the_model_number():
    one = {_key("Carrera", "CARRERA 1001/S"), _key("Carrera", "CARRERA1001/S"), _key("CARRERA", "1001/S"),
           _key("Carrera", "carrera-1001-s")}
    assert one == {("SUNGLASS", "carrera", "1001s")}
    # no false merges: another brand, another model, a model that IS the brand
    assert _key("Polaroid", "1001/S") != _key("Carrera", "1001/S")
    assert _key("Oakley", "OO9208") != _key("Oakley", "OO9208A")
    assert _key("Carrera", "CARRERA") == ("SUNGLASS", "carrera", "carrera")
    # with no separator the brand folds only before a digit: a glued word is
    # never cut (#1271 review; the identity key reads the same rule)
    assert _key("Carrera", "CARRERA1001")[2] == "1001"
    assert _key("Carrera", "CARRERACA1001")[2] == "carreraca1001"
    assert _key("Ray-Ban", "RAYBANRB2140")[2] == "raybanrb2140"
    assert _key("Titan", "TITANIUM 12")[2] == "titanium12"
    assert _key("Titan", "Titanium12")[2] == "titanium12"
    assert _key("Titan", "TITAN 12")[2] == "12"
    # the model NUMBER only: a smart glass named by its model name is no family
    assert _key("Ray-Ban", "", category="SMARTGLASSES", model_name="Meta Wayfarer") is None
    assert _key("Ray-Ban", "RW4006", category="SMARTGLASSES", model_name="Meta Wayfarer") == (
        "SMARTGLASSES", "rayban", "rw4006")


def test_a_new_item_typed_without_the_brand_prefix_joins_the_model(db):
    lead = _create(db, {"brand_name": "Carrera", "model_no": "CARRERA 1001/S", "colour_code": "807", "shape": "Square"})
    child = _create(db, {"brand_name": "Carrera", "model_no": "1001/S", "colour_code": "086", "shape": "Round"})
    assert child["variant_of"] == lead["product_id"]
    assert _p(db, child["product_id"])["attributes"]["model_no"] == "1001/S"  # stored as typed
    assert _p(db, child["product_id"])["attributes"]["shape"] == "Square"  # the model's


def test_link_refuses_archived_products(db):
    _seed(db, _frame("L", "001"), _frame("C", "002", minutes=1))
    db["products"].update_one({"product_id": "C"}, {"$set": {"deleted_at": "2026-10-18T00:00:00Z"}})
    with pytest.raises(pm.ProductMasterError) as e:
        pm.link_to_family(_Conn(db), "C", "L")
    assert e.value.status == 422 and "archived" in e.value.message


def _put(pid, **fields):
    return asyncio.run(products_mod.update_product(pid, products_mod.ProductUpdate(**fields), current_user=ADMIN))


def test_put_products_runs_the_variant_guard_on_every_field(db):
    # the variant stores its model fields in other spellings than the form sends
    lead = _frame("L", "601", model="RB4165", shape="Square")
    child = _frame("C", "710", model="rb4165", brand="RAY-BAN", minutes=1, shape="Square", model_name="JUSTIN")
    child[0]["variant_of"] = "L"
    _seed(db, lead, child)
    child = child[0]
    form = {**child["attributes"], "brand_name": "Ray-Ban", "model_no": "RB4165", "model_name": "Justin"}
    # a price-only save of the whole form: the same values canonically -> saved
    out = _put("C", attributes=form, brand="Ray-Ban", model="RB4165", mrp=6100.0, offer_price=6100.0)
    assert out["message"] == "Product updated" and _p(db, "C")["mrp"] == 6100.0
    for bad in (
        {"attributes": {**form, "shape": "Round"}},
        {"brand": "Oakley"},
        {"model": "RB4166"},
    ):
        with pytest.raises(products_mod.HTTPException) as e:
            _put("C", **bad)
        assert e.value.status_code == 422, bad
    assert _p(db, "C")["attributes"]["shape"] == "Square"


def test_grouped_list_pages_models_and_the_archived_view_shows_archived_members(db, list_cache):
    _seed(db, _frame("L", "001", minutes=0), _frame("C1", "002", minutes=1), _frame("C2", "003", minutes=2),
          _frame("S", "777", model="RB3025", minutes=3))
    pm.link_to_family(_Conn(db), "C1", "L")
    pm.link_to_family(_Conn(db), "C2", "L")
    out = _list(sort="oldest", is_active="true", group="model", skip=0, limit=1)
    assert [p["product_id"] for p in out["products"]] == ["L"] and out["total_count"] == 2
    out = _list(sort="oldest", is_active="true", group="model", skip=1, limit=1)
    assert [p["product_id"] for p in out["products"]] == ["S"] and out["total_count"] == 2
    db["products"].update_one({"product_id": "C2"}, {"$set": {"deleted_at": "2026-10-18T00:00:00Z", "is_active": False}})
    out = _list(sort="oldest", is_active="true", group="model")
    assert [m["product_id"] for m in out["products"][0]["family"]["members"]] == ["L", "C1"]
    out = _list(sort="oldest", group="model", flag="archived")
    assert [p["product_id"] for p in out["products"]] == ["C2"]
    assert "family" not in out["products"][0]  # only its archived members: itself


# ---------------------------------------------------------------------------
# review round 2
# ---------------------------------------------------------------------------


class _Unreadable:
    """A catalogue whose catalog_products read fails."""

    def __init__(self, db):
        self._db = db

    def __getitem__(self, name):
        if name == "catalog_products":
            class _Boom:
                def find_one(self, *a, **k):
                    raise RuntimeError("catalogue down")
            return _Boom()
        return self._db[name]

    def get_collection(self, name):
        return self[name]


def test_has_listing_fails_closed_when_the_catalogue_cannot_be_read(db):
    lead, _, _ = _frame("L", "001")
    assert pm.has_listing(_Conn(db), lead) is False  # readable, no twin: not on Shopify
    assert pm.has_listing(_Unreadable(db), lead) is True
    assert pm.has_listing(None, lead) is True


def test_a_live_lead_is_never_relabelled_by_an_attribute_edit(db):
    repo = ProductRepository(db["products"])
    _seed(db, _frame("L", "001", "Gold"), _frame("C", "002", "Black", minutes=1))
    pm.link_to_family(_Conn(db), "C", "L")  # linked before the model went live
    db["catalog_products"].update_one({"id": "twin-L"}, {"$set": {"ecom.shopify_product_id": "gid://shopify/Product/L"}})
    before = {pid: _row(db, pid) for pid in ("L", "C")}
    # a colour name edit on the variant AND on the lead would relabel their rows
    for pid, name in (("C", "Navy"), ("L", "Rose Gold")):
        doc = _p(db, pid)
        pm.update_product(product_id=pid, patch={"attributes": {**doc["attributes"], "frame_color": name}},
                          actor="u1", product_repo=repo, db=_Conn(db))
        assert _p(db, pid)["attributes"]["frame_color"] == name  # the edit itself saved
    for pid in ("L", "C"):
        assert {k: _row(db, pid).get(k) for k in ("option_color", "option_size")} ==             {k: before[pid].get(k) for k in ("option_color", "option_size")}, pid


def test_a_row_that_moves_listing_drops_the_old_listings_shopify_ids(db):
    _seed(db, _frame("L", "001"), _frame("A", "002", minutes=1), _frame("B", "003", minutes=2))
    pm.link_to_family(_Conn(db), "A", "L")
    pm.link_to_family(_Conn(db), "B", "L")
    for pid in ("A", "B"):  # as if they had been variants on L's listing
        db["catalog_variants"].update_one({"sku": _p(db, pid)["sku"]}, {"$set": {
            "shopify_variant_id": "gid://v/" + pid, "shopify_inventory_item_id": "gid://i/" + pid,
            "discounted_price": 4000.0}})
    db["products"].update_one({"product_id": "L"}, {"$set": {"deleted_at": "2026-10-18T00:00:00Z"}})
    assert pm.promote_new_lead(_Conn(db), "L") == "A"
    for pid in ("A", "B"):
        row = _row(db, pid)
        assert row["parent_product_id"] == "twin-A"
        for f in ("shopify_variant_id", "shopify_inventory_item_id", "discounted_price"):
            assert f not in row, (pid, f)
