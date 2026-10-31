"""
Tests for the "Share collection as PDF" feature (catalogue PDF + temp collections).

Three layers:
  1. PURE service unit tests (no DB): the row builder's include_details / include_mrp
     toggles, the reportlab render returning a valid %PDF, slug + validity clamp.
  2. Router wiring via the shared TestClient (works WITHOUT a DB): auth gate, the
     empty-selection PDF, the 400 for a missing selection, and the rbac_policy rows.
  3. DB-connected tests (SKIPPED when no live Mongo): PDF from a real collection,
     the product_ids path, temp-collection validity clamp, Shopify-sync exclusion,
     listability, and the TTL index.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_catalogue_pdf.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from datetime import datetime, timedelta, timezone  # noqa: E402

import pytest  # noqa: E402

from api.services import catalogue_pdf as pdf  # noqa: E402
from api.services import rbac_policy as rbac  # noqa: E402
from api.routers.catalogue_pdf import _clamp_validity_days, TEMP_MAX_DAYS  # noqa: E402


PDF_URL = "/api/v1/catalogue/pdf"
TEMP_URL = "/api/v1/catalogue/temp-collections"


def _sample_product():
    return {
        "product_id": "P-1",
        "sku": "SKU-1",
        "brand": "Ray-Ban",
        "name": "Aviator Classic",
        "category": "SUNGLASS",
        "pricing": {"mrp": 8000, "offer_price": 6400},
        "attributes": {"shape": "Aviator", "frame_material": "Metal"},
        "description": "Timeless teardrop sunglasses with a gold frame.",
        "images": ["https://example.test/aviator.jpg"],
    }


# ===========================================================================
# Layer 1 -- PURE service unit tests (no DB, always run)
# ===========================================================================


def test_build_rows_always_has_brand_name_image():
    rows = pdf.build_product_rows([_sample_product()])
    assert len(rows) == 1
    r = rows[0]
    assert r["brand"] == "Ray-Ban"
    assert r["name"] == "Aviator Classic"
    assert r["image"] == "https://example.test/aviator.jpg"


def test_include_mrp_toggle_changes_content():
    on = pdf.build_product_rows([_sample_product()], include_mrp=True)[0]
    off = pdf.build_product_rows([_sample_product()], include_mrp=False)[0]
    # ON -> MRP + a genuine offer are present.
    assert on["mrp"] == 8000
    assert on["offer_price"] == 6400
    # OFF -> no price keys at all.
    assert "mrp" not in off
    assert "offer_price" not in off


def test_offer_only_shown_when_below_mrp():
    p = _sample_product()
    p["pricing"] = {"mrp": 5000, "offer_price": 5000}  # not a real discount
    r = pdf.build_product_rows([p], include_mrp=True)[0]
    assert r["mrp"] == 5000
    assert "offer_price" not in r


def test_include_details_toggle_changes_content():
    on = pdf.build_product_rows([_sample_product()], include_details=True)[0]
    off = pdf.build_product_rows([_sample_product()], include_details=False)[0]
    assert "details" in on
    labels = {label for label, _ in on["details"]}
    assert "Category" in labels and "Shape" in labels and "Material" in labels
    assert on.get("description")
    # OFF -> no detail block or description.
    assert "details" not in off
    assert "description" not in off


def test_render_pdf_returns_valid_pdf_bytes():
    rows = pdf.build_product_rows([_sample_product()], include_details=True)
    data = pdf.render_catalogue_pdf(
        title="Test Catalogue", brand_name="Better Vision", rows=rows,
        include_details=True, include_mrp=True,
    )
    assert isinstance(data, bytes)
    assert data.startswith(b"%PDF")
    assert len(data) > 800


def test_render_empty_pdf_is_valid():
    data = pdf.render_catalogue_pdf(
        title="Empty", brand_name="Better Vision", rows=[],
    )
    assert data.startswith(b"%PDF")


def test_render_survives_broken_image_bytes():
    rows = pdf.build_product_rows([_sample_product()])
    # A non-image byte blob must degrade to a placeholder, never raise.
    data = pdf.render_catalogue_pdf(
        title="Broken img", brand_name="BV", rows=rows,
        image_bytes={0: b"not-an-image"},
    )
    assert data.startswith(b"%PDF")


def test_slugify_filename():
    assert pdf.slugify_filename("Ray-Ban Sunglasses!") == "ray-ban-sunglasses"
    assert pdf.slugify_filename("") == "catalogue"
    assert pdf.slugify_filename("   ") == "catalogue"


def test_validity_days_clamped_to_max_7():
    assert _clamp_validity_days(30) == TEMP_MAX_DAYS == 7
    assert _clamp_validity_days(7) == 7
    assert _clamp_validity_days(3) == 3
    assert _clamp_validity_days(0) == 1
    assert _clamp_validity_days(-5) == 1
    assert _clamp_validity_days(None) == 7


# ===========================================================================
# Layer 2 -- router wiring (works WITHOUT a DB)
# ===========================================================================


def test_pdf_requires_auth(client):
    r = client.post(PDF_URL, json={"product_ids": ["x"]})
    assert r.status_code == 401


def test_pdf_missing_selection_is_400(client, auth_headers):
    r = client.post(PDF_URL, json={}, headers=auth_headers)
    assert r.status_code == 400


def test_pdf_empty_selection_returns_valid_pdf(client, auth_headers):
    # An unknown/empty product_ids set resolves to no products -> a valid,
    # single-cover 'no products' PDF (never a 500). Works with or without a DB.
    r = client.post(
        PDF_URL, json={"product_ids": ["does-not-exist"]}, headers=auth_headers
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")
    assert "attachment" in r.headers.get("content-disposition", "")
    assert r.content.startswith(b"%PDF")


def test_rbac_pdf_authenticated_allows_staff_denies_anon():
    # AUTHENTICATED posture: any logged-in role passes; an empty role set fails.
    assert rbac.check_access("POST", PDF_URL, ["SALES_STAFF"]) is True
    assert rbac.check_access("POST", PDF_URL, ["CATALOG_MANAGER"]) is True
    assert rbac.check_access("POST", PDF_URL, []) is False


def test_rbac_temp_collection_routes_catalogued():
    for method, path in [
        ("POST", TEMP_URL),
        ("GET", TEMP_URL),
        ("DELETE", "/api/v1/catalogue/temp-collections/abc"),
    ]:
        entry = rbac.policy_for(method, path)
        assert entry is not None, (method, path)
        assert entry["allowed"] == rbac.AUTHENTICATED


# ===========================================================================
# Layer 3 -- DB-connected (SKIPPED when no live Mongo)
# ===========================================================================


def _mongo():
    """Underlying pymongo Database when connected, else None."""
    try:
        from database.connection import get_db

        conn = get_db()
        if conn and getattr(conn, "is_connected", False):
            return getattr(conn, "db", None)
    except Exception:  # noqa: BLE001
        return None
    return None


def _seed_products(db, n=3):
    docs = []
    for i in range(n):
        docs.append({
            "product_id": "ZZQA-P%d" % i,
            "sku": "ZZQA-SKU-%d" % i,
            "brand": "TestBrand",
            "name": "Test Frame %d" % i,
            "category": "FRAME",
            "pricing": {"mrp": 1000 + i, "offer_price": 800 + i},
            "attributes": {"shape": "Round"},
        })
    db["catalog_products"].insert_many([dict(d) for d in docs])
    return docs


def test_pdf_from_collection_with_products(client, auth_headers):
    db = _mongo()
    if db is None:
        pytest.skip("no live Mongo")
    docs = _seed_products(db, 3)
    from database.repositories import EcomCollectionRepository

    repo = EcomCollectionRepository(db["ecom_collections"])
    coll = repo.create({
        "title": "ZZ QA Frames",
        "handle": "zzqa-frames-pdf",
        "collection_type": "CUSTOM",
        "products": [{"sku": d["sku"], "position": i} for i, d in enumerate(docs)],
    })
    r = client.post(
        PDF_URL,
        json={"collection_id": coll["collection_id"], "include_details": True, "include_mrp": True},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF")


def test_pdf_unknown_collection_is_404(client, auth_headers):
    db = _mongo()
    if db is None:
        pytest.skip("no live Mongo")
    r = client.post(
        PDF_URL, json={"collection_id": "no-such-collection"}, headers=auth_headers
    )
    assert r.status_code == 404


def test_pdf_from_product_ids(client, auth_headers):
    db = _mongo()
    if db is None:
        pytest.skip("no live Mongo")
    docs = _seed_products(db, 2)
    r = client.post(
        PDF_URL,
        json={"product_ids": [d["product_id"] for d in docs]},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF")


def test_temp_collection_create_clamps_validity_and_lists(client, auth_headers):
    db = _mongo()
    if db is None:
        pytest.skip("no live Mongo")
    docs = _seed_products(db, 2)
    r = client.post(
        TEMP_URL,
        json={
            "name": "ZZ QA Shared Set",
            "product_ids": [d["product_id"] for d in docs],
            "validity_days": 30,  # over the max -> clamped to 7
        },
        headers=auth_headers,
    )
    assert r.status_code == 201
    body = r.json()["collection"]
    cid = body["collection_id"]
    assert body["is_temporary"] is True
    # expires_at is at most ~7 days out (clamp honoured).
    exp = datetime.fromisoformat(body["expires_at"])
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    assert exp <= datetime.now(timezone.utc) + timedelta(days=TEMP_MAX_DAYS, minutes=5)

    # It is listable...
    lst = client.get(TEMP_URL, headers=auth_headers)
    assert lst.status_code == 200
    assert any(c["collection_id"] == cid for c in lst.json()["collections"])

    # ...and PDF-generatable via the collection_id path.
    pdf_res = client.post(PDF_URL, json={"collection_id": cid}, headers=auth_headers)
    assert pdf_res.status_code == 200
    assert pdf_res.content.startswith(b"%PDF")


def test_temp_collection_excluded_from_shopify_push(client, auth_headers):
    db = _mongo()
    if db is None:
        pytest.skip("no live Mongo")
    docs = _seed_products(db, 1)
    created = client.post(
        TEMP_URL,
        json={"name": "ZZ QA No-Sync", "product_ids": [docs[0]["product_id"]]},
        headers=auth_headers,
    ).json()["collection"]
    cid = created["collection_id"]

    # The stored doc must carry the structural sync-exclusion markers.
    doc = db["ecom_collections"].find_one({"collection_id": cid})
    assert doc["is_temporary"] is True
    assert doc["sync_to_shopify"] is False
    assert doc["published"] is False

    # The push route (SUPERADMIN auth_headers) refuses to push a temp collection.
    push = client.post(
        "/api/v1/online-store/push/collection/%s" % cid, headers=auth_headers
    )
    assert push.status_code == 400


def test_temp_collection_ttl_index_created():
    db = _mongo()
    if db is None:
        pytest.skip("no live Mongo")
    from database.connection import get_db

    get_db().ensure_indexes()  # idempotent
    info = db["ecom_collections"].index_information()
    ttl = info.get("ttl_temp_collection_expires_at")
    assert ttl is not None
    assert ttl.get("expireAfterSeconds") == 0
    assert ttl.get("partialFilterExpression") == {"is_temporary": True}
