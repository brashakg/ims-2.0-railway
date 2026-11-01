"""The same model typed differently is noticed on Add Product (owner 2026-11-01).

"RB 4345 / 0RB4345 / RB-4345 / only 4345 are the same product." The identity
key already folds separators, the Luxottica 0 and the brand typed in front;
it does NOT fold the brand's own model prefix against bare digits, so prod
held 7 Ray-Ban twins (RB4340 / 4340 ...) plus 0840 / 0840S. The key and the
exact-duplicate 409 are unchanged; this adds:

  * similar_model_query + same_model_spelling: one anchored regex (brand,
    any letters, the digit core, a sunglass's optional trailing S) and a
    prefix fit (equal, one empty, or the shorter (2+) ends the longer).
  * find_similar_products -> similar_models (red when same colour + size).
  * POST/PUT /products check_near_duplicates -> 409 NEAR_DUPLICATE, answered
    by distinct_from (reason required, who/when stamped by the server).
  * the draft-review warning "same model written differently".

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest backend/tests/test_same_model_spelling.py -q
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import api.dependencies as deps  # noqa: E402
from api.routers import catalog as cat  # noqa: E402
from api.routers import product_master as pm_router  # noqa: E402
from api.routers import products as prod_router  # noqa: E402
from api.services import product_master as pm  # noqa: E402
from database.repositories.product_repository import ProductRepository  # noqa: E402
from strict_fakes import StrictCollection, StrictDB  # noqa: E402

_ADMIN = {"user_id": "u-admin", "username": "admin", "roles": ["ADMIN"]}
_PRIYA = {"user_id": "u-priya", "username": "priya", "roles": ["CATALOG_MANAGER"]}


# ---------------------------------------------------------------------------
# A tiny products collection: find($regex)/limit/sort, find_one, distinct.
# ---------------------------------------------------------------------------


class _Cursor(list):
    def sort(self, key, direction=1):
        super().sort(key=lambda d: str(d.get(key) or ""), reverse=direction < 0)
        return self

    def limit(self, n):
        return _Cursor(self[: int(n)])


class _Coll:
    def __init__(self, docs=()):
        self.docs = [dict(d) for d in docs]

    @staticmethod
    def _hit(doc, query):
        import re

        for k, cond in (query or {}).items():
            val = doc.get(k)
            if isinstance(cond, dict) and "$regex" in cond:
                if not isinstance(val, str) or not re.search(cond["$regex"], val):
                    return False
            elif val != cond:
                return False
        return True

    def find(self, query=None):
        return _Cursor(dict(d) for d in self.docs if self._hit(d, query))

    def find_one(self, query):
        return next((dict(d) for d in self.docs if self._hit(d, query)), None)

    def distinct(self, key, query=None):
        return sorted({d.get(key) for d in self.docs if self._hit(d, query) and d.get(key)})


def _row(model, colour="601/M3", size="50", *, brand="Ray-Ban", category="SUNGLASS", pid=None, **extra):
    attrs = {"brand_name": brand, "model_no": model, "colour_code": colour}
    if size:
        attrs["lens_size"] = size
    key = pm.compute_identity_key(*pm.identity_parts(attrs, category))
    return {
        "product_id": pid or f"P-{model}-{colour}-{size}",
        "sku": f"SG-{brand}-{model}-{colour}-{size}".upper(),
        "category": category,
        "brand": brand,
        "model": model,
        "attributes": attrs,
        "identity_key": key,
        "is_active": True,
        **extra,
    }


def _found(typed, stored, category="SUNGLASS", brand="Ray-Ban", stored_brand=None, stored_category=None):
    """The rule end to end: the stored row is found under the typed model."""
    coll = _Coll([_row(stored, brand=stored_brand or brand, category=stored_category or category)])
    return bool(pm.similar_model_rows(coll, category, brand, typed))


# ---------------------------------------------------------------------------
# 1. The rule (design table, as the critique corrected it)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "typed,stored",
    [
        ("RB4345", "4345"),
        ("0RB4345", "4345"),
        ("RB-4345", "4345"),
        ("Ray-Ban RB4345", "4345"),
        ("4345", "RB4345"),
        ("XL OO9417", "OO9417"),  # the Oakley Holbrook XL is OO9417
        ("RAYBANRB4345", "RB4345"),  # a glued brand #1271 leaves whole
        ("0840", "0840S"),  # a sunglass's trailing S
        ("0840S", "0840"),
    ],
)
def test_the_same_model_in_another_spelling_is_found(typed, stored):
    brand = "Oakley" if "OO" in typed else "Ray-Ban"
    assert _found(typed, stored, brand=brand)


def test_carduc_against_bare_digits_is_found_as_a_soft_warning():
    assert _found("CARDUC 3016/S", "3016/S", brand="Carrera")


@pytest.mark.parametrize(
    "typed,stored,brand",
    [
        ("RB4345", "RJ4345", "Ray-Ban"),  # Ray-Ban Junior
        ("MARC 1106", "MJ 1106", "Marc Jacobs"),
        ("SPLL123", "SPLN123", "Police"),
        ("rb21", "rb213", "Ray-Ban"),  # the trailing | still guards
        ("J4345", "RJ4345", "Ray-Ban"),  # a one-letter prefix never fits a longer one
        ("RB4345", "RB4345", "Ray-Ban"),  # the same spelling is a colour chip, not "similar"
    ],
)
def test_different_models_stay_apart(typed, stored, brand):
    assert not _found(typed, stored, brand=brand)


def test_never_across_brands_or_categories():
    assert not _found("2283", "2283", brand="Ray-Ban", stored_brand="Versace")
    assert not _found("RB2283", "2283", brand="Ray-Ban", stored_brand="Versace")
    assert not _found("RB4345", "4345", stored_category="FRAME")


def test_the_trailing_s_folds_for_sunglasses_only():
    assert not _found("0840", "0840S", category="FRAME")
    assert _found("RB4345", "4345", category="FRAME")


def test_no_rule_where_the_code_shape_does_not_hold():
    assert pm.similar_model_query("SUNGLASS", "Ray-Ban", "AVIARA") is None  # no digit
    assert pm.similar_model_query("OPTICAL_LENS", "Essilor", "1.56") is None
    assert pm.similar_model_query("CONTACT_LENS", "Acuvue", "Oasys 1 Day") is None
    assert pm.similar_model_query("SUNGLASS", "", "RB4345") is None


def test_typed_text_never_reaches_the_regex_raw():
    q = pm.similar_model_query("SUNGLASS", "Ray-Ban", "RB 4345.*")
    assert q["identity_key"]["$regex"] == r"^rayban\|[a-z]*4345s?\|"


def test_an_archived_twin_or_a_discarded_draft_raises_nothing():
    archived = _row("RB4340", deleted_at="2026-11-01")
    discarded = _row("RB4340", colour="BLK", discarded_draft=True, is_active=False, catalog_status="DRAFT")
    coll = _Coll([archived, discarded])
    assert pm.similar_model_rows(coll, "SUNGLASS", "Ray-Ban", "4340") == []


# ---------------------------------------------------------------------------
# 2. GET /products/similar -> similar_models
# ---------------------------------------------------------------------------


def _typed(model, colour="601/M3", size="50"):
    return {"brand_name": "Ray-Ban", "model_no": model, "colour_code": colour, "lens_size": size}


def test_similar_models_red_for_a_same_colour_and_size_twin():
    twin = _row("RB4340", created_by_name="priya", created_at="2026-09-12T10:00:00")
    other = _row("RB4340", colour="710/51")
    own = _row("4340", colour="710/51")
    hit = pm.find_similar_products(_Coll([twin, other, own]), category="SG", attributes=_typed("4340"))
    assert [s["sku"] for s in hit["siblings"]] == [own["sku"]]  # same spelling = colour chips
    [group] = hit["similar_models"]
    assert group["model"] == "RB4340" and group["brand"] == "Ray-Ban"
    assert group["same_colour_size"] is True
    assert [p["sku"] for p in group["products"]] == [twin["sku"], other["sku"]]  # the twin first
    assert group["products"][0]["created_by_name"] == "priya"
    assert group["products"][0]["created_at"] == "2026-09-12T10:00:00"


def test_similar_models_amber_when_only_the_model_matches():
    hit = pm.find_similar_products(
        _Coll([_row("RB4340", colour="710/51")]), category="SG", attributes=_typed("4340")
    )
    assert hit["similar_models"][0]["same_colour_size"] is False


def test_a_missing_size_on_either_side_counts_as_the_same_size():
    sizeless = _row("RB4340", size=None)
    assert sizeless["identity_key"] == "rayban|rb4340|601m3"
    hit = pm.find_similar_products(_Coll([sizeless]), category="SG", attributes=_typed("4340"))
    assert hit["similar_models"][0]["same_colour_size"] is True


def test_similar_models_cap_twelve_summaries_in_all():
    rows = [_row("RB4340", colour=f"C{i}") for i in range(10)] + [
        _row("R4340", colour=f"C{i}") for i in range(10)
    ]
    hit = pm.find_similar_products(_Coll(rows), category="SG", attributes=_typed("4340"))
    assert sum(len(g["products"]) for g in hit["similar_models"]) == 12


def test_similar_models_fail_soft_when_the_catalogue_cannot_be_read():
    class Boom(_Coll):
        def find(self, query=None):
            raise RuntimeError("down")

    hit = pm.find_similar_products(Boom(), category="SG", attributes=_typed("4340"))
    assert hit["similar_models"] == []


def test_a_pair_marked_different_is_hidden_both_ways():
    old = _row("RB4340", pid="P-OLD")
    new = _row("4340", pid="P-NEW", distinct_from=[{"product_id": "P-OLD", "reason": "Junior / kids version"}])
    coll = _Coll([old, new])
    # Editing the new one: the old one is not "similar" any more.
    seen = pm.find_similar_products(coll, category="SG", attributes=_typed("4340"), self_id="P-NEW")
    assert seen["similar_models"] == []
    # Editing the old one: the answer on the new one hides it too.
    seen = pm.find_similar_products(coll, category="SG", attributes=_typed("RB4340"), self_id="P-OLD")
    assert seen["similar_models"] == []
    # Without the answer it shows.
    coll.docs[1].pop("distinct_from")
    seen = pm.find_similar_products(coll, category="SG", attributes=_typed("RB4340"), self_id="P-OLD")
    assert [p["product_id"] for p in seen["similar_models"][0]["products"]] == ["P-NEW"]


def test_the_similar_route_reads_self_id_apart_from_the_attributes(monkeypatch):
    seen = {}

    def fake(coll, *, category, attributes, self_id=None):
        seen.update(category=category, attributes=attributes, self_id=self_id)
        return {}

    monkeypatch.setattr(pm, "find_similar_products", fake)

    class Req:
        query_params = {"category": "SG", "model_no": "4340", "self_id": "P-1"}

    asyncio.run(prod_router.get_similar_products(Req(), category="SG", current_user=_ADMIN))
    assert seen == {"category": "SG", "attributes": {"model_no": "4340"}, "self_id": "P-1"}


# ---------------------------------------------------------------------------
# 3. The SERVER-side save check (POST / PUT /products)
# ---------------------------------------------------------------------------


@pytest.fixture
def door(monkeypatch):
    db = StrictDB()
    repo = ProductRepository(StrictCollection("products"))
    monkeypatch.setattr(prod_router, "get_product_repository", lambda: repo)
    monkeypatch.setattr(deps, "get_db", lambda: db)
    monkeypatch.setattr(deps, "get_product_repository", lambda: repo)
    monkeypatch.setattr(pm_router, "get_product_repository", lambda: repo)
    monkeypatch.setattr(pm_router, "get_db", lambda: db)
    monkeypatch.setattr(deps, "get_audit_repository", lambda: None)
    monkeypatch.setattr(pm, "mirror_enabled", lambda: False)

    def _post(model, user=_ADMIN, colour="601/M3", **over):
        body = {"category": "SUNGLASS", "brand": "Ray-Ban", "model": model, "color": colour,
                "mrp": 9000.0, "offer_price": 9000.0, "attributes": {"lens_size": "50"}}
        body.update(over)
        out = asyncio.run(prod_router.create_product(prod_router.ProductCreate(**body), user))
        return repo.find_by_id(out["product_id"])

    _post.repo = repo
    return _post


def _near(door, model, **over):
    with pytest.raises(HTTPException) as exc:
        door(model, **over)
    assert exc.value.status_code == 409, exc.value.detail
    assert exc.value.detail["code"] == "NEAR_DUPLICATE"
    return exc.value.detail["existing"]


def test_the_save_check_answers_409_near_duplicate_only_when_asked(door):
    first = door("RB4340")
    existing = _near(door, "4340", check_near_duplicates=True)
    assert existing["product_id"] == first["product_id"] and existing["model"] == "RB4340"
    assert existing["created_by_name"] == "admin"


def test_without_the_flag_the_save_goes_ahead_with_its_own_key(door):
    """No-block rule pinned: CSV import, clone and PO lines never send it."""
    door("RB4345")
    second = door("4345")
    assert second["identity_key"] == "rayban|4345|601m3|50"


def test_another_colour_never_stops_the_save(door):
    door("RB4340")
    assert door("4340", colour="710/51", check_near_duplicates=True)["product_id"]


def test_an_exact_twin_stays_the_doors_own_409(door):
    door("RB4340")
    door("4340")  # an older twin, saved before the check
    # Typed again as 4340: it IS the 4340 product -- the hard 409, not "maybe".
    with pytest.raises(HTTPException) as exc:
        door("4340", check_near_duplicates=True)
    assert exc.value.status_code == 409 and exc.value.detail["code"] == "DUPLICATE_PRODUCT"


def test_its_a_different_product_saves_with_a_server_stamped_answer(door):
    first = door("RB4340")
    saved = door(
        "4340",
        user=_PRIYA,
        check_near_duplicates=True,
        distinct_from=[{"product_id": first["product_id"], "reason": "  Junior /  kids version ",
                        "by": "forged", "at": "1999-01-01"}],
    )
    [answer] = saved["distinct_from"]
    assert answer["product_id"] == first["product_id"] and answer["sku"] == first["sku"]
    assert answer["reason"] == "Junior / kids version"
    assert answer["by"] == "u-priya" and answer["by_name"] == "priya"
    assert answer["at"] and not answer["at"].startswith("1999")


def test_the_answer_needs_a_reason_and_a_real_product(door):
    first = door("RB4340")
    with pytest.raises(Exception):  # pydantic: a reason is required
        door("4340", distinct_from=[{"product_id": first["product_id"], "reason": ""}])
    with pytest.raises(HTTPException) as exc:
        door("4340", distinct_from=[{"product_id": first["product_id"], "reason": "   "}])
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        door("4340", distinct_from=[{"product_id": "P-NOPE", "reason": "Different lens"}])
    assert exc.value.status_code == 422


def _put(pid, user=_ADMIN, **fields):
    return asyncio.run(prod_router.update_product(pid, prod_router.ProductUpdate(**fields), user))


def test_an_edit_asks_only_when_it_moves_the_identity(door):
    door("RB4340")
    twin = door("4340")  # an older twin, saved before the check
    # A price edit on it, re-sending the same attributes, never asks.
    _put(twin["product_id"], attributes=dict(twin["attributes"]), mrp=9500.0, check_near_duplicates=True)
    assert door.repo.find_by_id(twin["product_id"])["mrp"] == 9500.0
    door("RB4340", colour="C7")
    other = door("4340", colour="710/51")
    attrs = dict(other["attributes"])
    # A colour edit onto a colour only RB4340 has does.
    with pytest.raises(HTTPException) as exc:
        _put(other["product_id"], attributes=dict(attrs, colour_code="C7"), check_near_duplicates=True)
    assert exc.value.detail["code"] == "NEAR_DUPLICATE"
    # Without the flag (any other editor) it saves.
    _put(other["product_id"], attributes=dict(attrs, colour_code="C7"))
    assert door.repo.find_by_id(other["product_id"])["identity_key"] == "rayban|4340|c7|50"


def test_an_edit_answer_joins_the_stored_ones_and_is_never_stored_raw(door):
    first = door("RB4340")
    other = door("4340", colour="710/51")
    _put(other["product_id"], attributes=dict(other["attributes"], colour_code="601/M3"),
         check_near_duplicates=True,
         distinct_from=[{"product_id": first["product_id"], "reason": "Different lens"}])
    stored = door.repo.find_by_id(other["product_id"])
    assert "check_near_duplicates" not in stored
    assert [(d["product_id"], d["reason"], d["by"]) for d in stored["distinct_from"]] == [
        (first["product_id"], "Different lens", "u-admin")]


# ---------------------------------------------------------------------------
# 4. Draft review: "same model written differently"
# ---------------------------------------------------------------------------


def _draft(model, pid="CAT-1", **extra):
    return {"product_id": pid, "category": "SUNGLASS",
            "attributes": {"brand_name": "Ray-Ban", "model_no": model}, **extra}


def test_draft_review_names_the_same_model_written_differently(door):
    first = door("RB4340")
    warned = cat._promote_duplicate_warnings(door.repo, _draft("4340"))
    assert [(w["sku"], w["reason"]) for w in warned] == [
        (first["sku"], "same model written differently: RB4340 / 4340")]


def test_draft_review_reason_is_never_crowded_out(door):
    for c in ("C1", "C2", "C3", "C4", "C5", "C6"):
        door("4340", colour=c)
    twin = door("RB4340")
    warned = cat._promote_duplicate_warnings(door.repo, _draft("4340"))
    assert sum(w["reason"] == "same brand + model" for w in warned) == 5
    assert [w["sku"] for w in warned if w["reason"].startswith("same model written")] == [twin["sku"]]


def test_draft_review_skips_a_pair_marked_different_both_ways(door):
    same = door("4340", colour="C9")
    # The same spelling ("same brand + model") is skipped too once answered.
    door.repo.collection.update_one({"product_id": same["product_id"]},
                                    {"$set": {"distinct_from": [{"product_id": "CAT-1", "reason": "x"}]}})
    first = door("RB4340")
    assert cat._promote_duplicate_warnings(
        door.repo, _draft("4340", distinct_from=[{"product_id": first["product_id"], "reason": "x"}])) == []
    door.repo.collection.update_one({"product_id": first["product_id"]},
                                    {"$set": {"distinct_from": [{"product_id": "CAT-1", "reason": "x"}]}})
    assert cat._promote_duplicate_warnings(door.repo, _draft("4340")) == []


# ---------------------------------------------------------------------------
# 5. Replay: the prod catalogue as measured 2026-11-01 (keys only, read-only)
# ---------------------------------------------------------------------------

_EXPECTED_PROD_TWINS = {
    ("2132-M", "RB2132-M"), ("2283", "RB2283"), ("3548-N", "RB3548-N"), ("3738I", "RB3738I"),
    ("3759I", "RB3759I"), ("4340", "RB4340"), ("8089", "RB8089"), ("0840", "0840S"),
}


def test_replay_of_the_prod_catalogue_catches_the_8_twins_and_nothing_else():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                        "same_model_replay_2026_11_01.json")
    with open(path, encoding="utf-8") as fh:
        rows = [
            {"product_id": f"P{i}", "category": c, "brand": b, "model": m, "identity_key": k,
             "attributes": {"brand_name": b, "model_no": m}}
            for i, (c, b, m, k) in enumerate(json.load(fh))
        ]
    assert len(rows) == 635
    coll = _Coll(rows)
    red = set()
    for r in rows:
        for hit in pm.similar_model_rows(coll, r["category"], r["brand"], r["model"], me=r):
            if pm.same_colour_and_size(r["identity_key"], hit["identity_key"]):
                red.add(tuple(sorted((r["model"], hit["model"]))))
    assert red == {tuple(sorted(p)) for p in _EXPECTED_PROD_TWINS}
