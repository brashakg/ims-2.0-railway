"""
Dup-detect Phase 2 -- live "similar products" lookup (GET /products/similar).

Locks the council contract:
  * NORMALISER PARITY -- matching runs through THE same normaliser that builds
    the spine identity_key (product_master.normalise_identity_component /
    compute_identity_key): a query differing only by case / spacing / the
    folded punctuation (- / _ .) from a stored product MUST match, exactly as
    it would 409 at create time.
  * exact vs sibling classification: exact = same identity (colour + size when
    the identity includes it); siblings = same category+brand+model, any
    colour/size, exact excluded.
  * siblings capped at 12; model_colour_count reports the TRUE distinct total.
  * FAIL-SOFT: DB trouble -> the empty shape, never a raise/5xx.

Run: JWT_SECRET_KEY=test python -m pytest \
        backend/tests/test_products_similar.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("PM_MIRROR_ENABLED", "")  # mirror OFF for unit tests

from api.services import product_master as pm  # noqa: E402
from api.services import rbac_policy as rbac  # noqa: E402


# ---------------------------------------------------------------------------
# Fake products collection (find_one / find().sort().limit() / distinct)
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction=1):
        self._docs.sort(key=lambda d: str(d.get(key) or ""), reverse=direction < 0)
        return self

    def limit(self, n):
        self._docs = self._docs[: int(n)]
        return self

    def __iter__(self):
        return iter(dict(d) for d in self._docs)


class _FakeProductsColl:
    def __init__(self, docs=None):
        self.docs = [dict(d) for d in (docs or [])]

    def _matches(self, doc, query):
        import re as _re

        for k, cond in (query or {}).items():
            val = doc.get(k)
            if isinstance(cond, dict) and "$regex" in cond:
                if not isinstance(val, str) or not _re.search(cond["$regex"], val):
                    return False
            elif val != cond:
                return False
        return True

    def find_one(self, query):
        for d in self.docs:
            if self._matches(d, query):
                return dict(d)
        return None

    def find(self, query):
        return _Cursor([d for d in self.docs if self._matches(d, query)])

    def distinct(self, key, query=None):
        out = []
        for d in self.docs:
            if query is None or self._matches(d, query):
                v = d.get(key)
                if v is not None and v not in out:
                    out.append(v)
        return out


class _RaisingColl(_FakeProductsColl):
    """Every read explodes -- the fail-soft arm."""

    def find_one(self, query):  # noqa: ARG002
        raise RuntimeError("boom")

    def find(self, query):  # noqa: ARG002
        raise RuntimeError("boom")

    def distinct(self, key, query=None):  # noqa: ARG002
        raise RuntimeError("boom")


def _spine_doc(brand, model, colour, *, size=None, category="FRAME", sku=None):
    """Build a stored spine row THROUGH normalise_payload, so its identity_key
    is minted by the real create pipeline (not hand-written in the test)."""
    # model_name mirrors model_no so categories whose registry requires
    # model_name (e.g. ACCESSORIES) validate too; model_no wins for identity.
    attrs = {
        "brand_name": brand,
        "model_no": model,
        "model_name": model,
        "colour_code": colour,
    }
    if size is not None:
        attrs["size"] = size
    doc = pm.normalise_payload(
        category=category,
        attributes=attrs,
        mrp=5000.0,
        offer_price=4500.0,
        sku=sku or f"SKU-{brand}-{model}-{colour}-{size or ''}",
        cost_price=2000.0,
    )
    doc["product_id"] = f"P-{doc['sku']}"
    return doc


def _coll_raybans():
    return _FakeProductsColl(
        [
            _spine_doc("Ray-Ban", "RB-2140", "BLK"),
            _spine_doc("Ray-Ban", "RB-2140", "RED"),
            _spine_doc("Ray-Ban", "RB-2140", "GRN"),
            # different model of the same brand -- never a sibling
            _spine_doc("Ray-Ban", "RB-3025", "BLK"),
            # prefix trap: model "RB-21" must not match "RB-2140" rows
            _spine_doc("Ray-Ban", "RB-21", "BLK"),
        ]
    )


EMPTY = {"exact_match": None, "siblings": [], "model_colour_count": 0}


def _similar(coll, **kw):
    args = {
        "category": "FRAME",
        "brand": "Ray-Ban",
        "model": "RB-2140",
        "colour": None,
        "size": None,
    }
    args.update(kw)
    return pm.find_similar_products(coll, **args)


# ===========================================================================
# 1. Normaliser-identity parity (the council rule)
# ===========================================================================


def test_exact_match_across_case_space_and_dash_variants():
    coll = _coll_raybans()
    # stored as Ray-Ban / RB-2140 / BLK; queried with every folding variant
    res = _similar(coll, brand="  ray ban ", model="rb  2140", colour="blk")
    assert res["exact_match"] is not None
    assert res["exact_match"]["sku"] == "SKU-Ray-Ban-RB-2140-BLK-"
    # what the strip flags as exact is EXACTLY what the create door would 409:
    assert res["exact_match"]["identity_key"] == pm.compute_identity_key(
        "Ray-Ban", "RB-2140", "BLK"
    )


def test_sibling_match_uses_the_same_normaliser():
    coll = _coll_raybans()
    res = _similar(coll, brand="RAY_BAN", model="rb.2140", colour="NEWCOLOUR")
    assert res["exact_match"] is None
    assert {s["colour_code"] for s in res["siblings"]} == {"BLK", "RED", "GRN"}


def test_query_normalisation_is_the_identity_component_function():
    # Guard the reuse itself: the module-level normaliser exists and IS what
    # compute_identity_key folds with (no second implementation).
    assert pm.normalise_identity_component(" Ray-Ban ") == "ray ban"
    assert pm.compute_identity_key("Ray-Ban", "RB 2140", "BLK") == "|".join(
        [
            pm.normalise_identity_component("Ray-Ban"),
            pm.normalise_identity_component("RB 2140"),
            pm.normalise_identity_component("BLK"),
        ]
    )


# ===========================================================================
# 2. Exact vs sibling classification
# ===========================================================================


def test_exact_excluded_from_siblings():
    coll = _coll_raybans()
    res = _similar(coll, colour="BLK")
    assert res["exact_match"]["colour_code"] == "BLK"
    assert {s["colour_code"] for s in res["siblings"]} == {"RED", "GRN"}
    assert res["model_colour_count"] == 3


def test_no_colour_given_yields_siblings_only():
    coll = _coll_raybans()
    res = _similar(coll)  # colour=None -> identity "brand|model|" matches nothing
    assert res["exact_match"] is None
    assert len(res["siblings"]) == 3


def test_other_models_and_prefix_trap_excluded():
    coll = _coll_raybans()
    res = _similar(coll)
    skus = {s["sku"] for s in res["siblings"]}
    assert not any("RB-3025" in s for s in skus)
    # the trailing | delimiter: "rb 21|" must not prefix-match "rb 2140|..."
    res21 = _similar(coll, model="RB-21")
    assert {s["colour_code"] for s in res21["siblings"]} == {"BLK"}
    assert res21["model_colour_count"] == 1


def test_size_in_identity_when_present():
    coll = _FakeProductsColl(
        [
            _spine_doc("Acme", "M1", "BLK", size="52", category="ACCESSORIES"),
            _spine_doc("Acme", "M1", "BLK", size="54", category="ACCESSORIES"),
        ]
    )
    # same colour, size 52 -> exact = the 52 row; the 54 row is a sibling
    res = pm.find_similar_products(
        coll, category="ACC", brand="acme", model="m1", colour="blk", size="52"
    )
    assert res["exact_match"]["size"] == "52"
    assert len(res["siblings"]) == 1
    # sizeless query -> 3-part key -> no exact, both rows are siblings
    res2 = pm.find_similar_products(
        coll, category="ACC", brand="acme", model="m1", colour="blk"
    )
    assert res2["exact_match"] is None
    assert len(res2["siblings"]) == 2


def test_category_filters_siblings():
    coll = _FakeProductsColl(
        [
            _spine_doc("Acme", "M9", "BLK", category="FRAME"),
            _spine_doc("Acme", "M9", "RED", category="SUNGLASS"),
        ]
    )
    res = pm.find_similar_products(coll, category="FR", brand="Acme", model="M9")
    assert {s["colour_code"] for s in res["siblings"]} == {"BLK"}


# ===========================================================================
# 3. Cap + count
# ===========================================================================


def test_siblings_capped_at_12_but_count_is_true_total():
    docs = [_spine_doc("Acme", "CAP", f"C{i:02d}") for i in range(15)]
    coll = _FakeProductsColl(docs)
    res = pm.find_similar_products(coll, category="FRAME", brand="Acme", model="CAP")
    assert len(res["siblings"]) == 12
    assert res["model_colour_count"] == 15


def test_sibling_summary_shape():
    res = _similar(_coll_raybans())
    s = res["siblings"][0]
    for key in (
        "product_id",
        "sku",
        "name",
        "colour_code",
        "size",
        "mrp",
        "offer_price",
        "is_active",
        "image_url",
    ):
        assert key in s
    # The spine now carries an auto-minted SEO name (product_naming), so the
    # summary surfaces it verbatim rather than the old brand+model fallback.
    assert s["name"] == "Ray-Ban RB-2140 Eyeglasses - Blk"


# ===========================================================================
# 4. Fail-soft + guard rails
# ===========================================================================


def test_db_trouble_returns_empty_shape():
    assert pm.find_similar_products(
        _RaisingColl(), category="FRAME", brand="A", model="M1"
    ) == EMPTY


def test_no_collection_returns_empty_shape():
    assert pm.find_similar_products(
        None, category="FRAME", brand="A", model="M1"
    ) == EMPTY


def test_unknown_category_or_missing_identity_returns_empty():
    coll = _coll_raybans()
    assert _similar(coll, category="NOT_A_CATEGORY") == EMPTY
    assert _similar(coll, brand="") == EMPTY
    assert _similar(coll, model=" - ") == EMPTY  # folds to nothing


# ===========================================================================
# 5. Endpoint contract (auth + fail-soft 200) + rbac row
# ===========================================================================


def test_endpoint_requires_auth(client):
    resp = client.get("/api/v1/products/similar", params={"category": "FRAME"})
    assert resp.status_code == 401


def test_endpoint_returns_shape_for_any_authenticated_role(client, staff_headers):
    resp = client.get(
        "/api/v1/products/similar",
        params={"category": "FRAME", "brand": "NoSuchBrand", "model_no": "NOPE-1"},
        headers=staff_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"exact_match", "siblings", "model_colour_count"}
    assert body["exact_match"] is None
    assert body["siblings"] == []


def test_endpoint_literal_path_wins_over_product_id_catchall(client, staff_headers):
    # If /similar were registered below GET /products/{product_id}, this would
    # hit the catch-all and 404 ("product 'similar' not found"). The shape
    # assert above already proves routing, but lock the status here too.
    resp = client.get(
        "/api/v1/products/similar",
        params={"category": "FRAME", "brand": "x", "model_no": "yy"},
        headers=staff_headers,
    )
    assert resp.status_code == 200


def test_endpoint_round_trip_exact_and_sibling(client, auth_headers):
    """Create real products through the door, then query /similar with folded
    variants -- exact + sibling classification against the live DB. Skipped
    when no Mongo is reachable (local runs); CI provides mongo:7.0."""
    import pytest

    from database.connection import get_db

    db = get_db()
    if not (db and getattr(db, "is_connected", False)):
        pytest.skip("no database connected")

    for colour in ("BLK", "RED"):
        resp = client.post(
            "/api/v1/products",
            headers=auth_headers,
            json={
                "category": "FRAME",
                "brand": "Similar-Test",
                "model": "ST-100",
                "attributes": {
                    "brand_name": "Similar-Test",
                    "model_no": "ST-100",
                    "colour_code": colour,
                },
                "mrp": 1000.0,
                "offer_price": 900.0,
            },
        )
        assert resp.status_code in (200, 201), resp.text

    resp = client.get(
        "/api/v1/products/similar",
        params={
            "category": "FR",  # short code resolves via resolve_category
            "brand": "similar test",  # fold-variant of Similar-Test (dash -> space)
            "model_no": "st 100",  # dash folded to space
            "colour_code": "blk",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["exact_match"] is not None
    assert body["exact_match"]["colour_code"] == "BLK"
    assert [s["colour_code"] for s in body["siblings"]] == ["RED"]
    assert body["model_colour_count"] == 2


def test_rbac_policy_row_is_authenticated():
    entry = rbac.policy_for("GET", "/api/v1/products/similar")
    assert entry is not None
    assert entry["allowed"] == rbac.AUTHENTICATED
