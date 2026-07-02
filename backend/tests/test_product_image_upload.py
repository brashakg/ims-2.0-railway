"""
Tests for the product-image upload/serve endpoints on the products router
(POST /api/v1/products/image, GET /api/v1/products/image/{file_id}).

Covers, over a TestClient with an in-memory file store (no live Mongo/GridFS):
  * RBAC: both routes are catalogued in rbac_policy.POLICY (upload = catalog
    roles, serve = PUBLIC) and a SALES_STAFF caller is 403'd on upload.
  * Happy path: a small PNG uploads -> 201 with {file_id, url, ...}; the url
    points at the serve endpoint and streams the same bytes back with the
    image mime.
  * Guards: a non-image mime -> 400; an empty body -> 400; nothing persisted.

Run: JWT_SECRET_KEY=test python -m pytest \
       backend/tests/test_product_image_upload.py -q
"""

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from api.services import rbac_policy as rbac  # noqa: E402
from api.services.file_store import InMemoryFileStore, set_file_store  # noqa: E402

_UPLOAD_PATH = "/api/v1/products/image"
_CATALOG_SET = {"ADMIN", "CATALOG_MANAGER"}

# A tiny valid-enough PNG header + a few bytes (the endpoint trusts the declared
# content-type, not the magic bytes -- it only needs SOME bytes to persist).
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def mem_store():
    """Swap the module-level file store for an in-memory one so the upload/serve
    round-trip works without a real GridFS. Restored after the test."""
    store = InMemoryFileStore()
    set_file_store(store)
    yield store
    set_file_store(None)


# ---------------------------------------------------------------------------
# RBAC catalogue + role gate
# ---------------------------------------------------------------------------


def test_upload_route_catalogued_with_catalog_roles():
    entry = rbac.policy_for("POST", _UPLOAD_PATH)
    assert entry is not None, "upload route not catalogued in rbac_policy"
    assert set(entry["allowed"]) == _CATALOG_SET


def test_serve_route_catalogued_public():
    entry = rbac.policy_for("GET", _UPLOAD_PATH + "/abc123")
    assert entry is not None, "serve route not catalogued in rbac_policy"
    assert entry["allowed"] == rbac.PUBLIC
    # The literal /image must resolve to its own POST entry, not be shadowed by
    # the /{product_id} catch-all.
    assert str(entry["path"]).endswith("/image/{file_id}")


def test_check_access_allows_catalog_denies_others():
    for role in ("SUPERADMIN", "ADMIN", "CATALOG_MANAGER"):
        assert rbac.check_access("POST", _UPLOAD_PATH, [role]) is True, role
    for role in ("SALES_STAFF", "CASHIER", "OPTOMETRIST", "WORKSHOP_STAFF"):
        assert rbac.check_access("POST", _UPLOAD_PATH, [role]) is False, role


def test_upload_rbac_denied_below_catalog_roles(client, staff_headers, mem_store):
    """SALES_STAFF is outside the catalog set -> 403 before the handler runs."""
    files = {"file": ("x.png", io.BytesIO(_PNG_BYTES), "image/png")}
    r = client.post(_UPLOAD_PATH, headers=staff_headers, files=files)
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# Happy path: upload -> serve round-trip
# ---------------------------------------------------------------------------


def test_upload_returns_url_and_serves_same_bytes(client, auth_headers, mem_store):
    files = {"file": ("photo.png", io.BytesIO(_PNG_BYTES), "image/png")}
    r = client.post(_UPLOAD_PATH, headers=auth_headers, files=files)
    assert r.status_code == 201, r.text
    body = r.json()

    assert body["file_id"]
    assert body["url"] == f"/api/v1/products/image/{body['file_id']}"
    assert body["content_type"] == "image/png"
    assert body["size"] == len(_PNG_BYTES)

    # The returned url serves the identical bytes back (PUBLIC, no auth header).
    served = client.get(body["url"])
    assert served.status_code == 200, served.text
    assert served.content == _PNG_BYTES
    assert served.headers["content-type"].startswith("image/png")


def test_serve_unknown_id_is_404(client, mem_store):
    r = client.get(_UPLOAD_PATH + "/does-not-exist")
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def test_upload_rejects_non_image(client, auth_headers, mem_store):
    files = {"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")}
    r = client.post(_UPLOAD_PATH, headers=auth_headers, files=files)
    assert r.status_code == 400, r.text
    # A PDF is in the shared ALLOWED_MIME_TYPES but is NOT a product image.
    files = {"file": ("doc.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")}
    r = client.post(_UPLOAD_PATH, headers=auth_headers, files=files)
    assert r.status_code == 400, r.text


def test_upload_rejects_empty_body(client, auth_headers, mem_store):
    files = {"file": ("empty.png", io.BytesIO(b""), "image/png")}
    r = client.post(_UPLOAD_PATH, headers=auth_headers, files=files)
    assert r.status_code == 400, r.text
