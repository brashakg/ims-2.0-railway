"""
Tests for the product-image background-removal edit endpoint on the products
router (POST /api/v1/products/image/{file_id}/edit).

The endpoint re-runs the deterministic cut-out pipeline (services/image_editor.py
-> Photoroom) on a previously-uploaded product image and persists the cleaned
result as a NEW image (the original is left untouched).

Covers, over a TestClient with an in-memory file store (no live Mongo/GridFS)
and the image editor fully monkeypatched (NO real Photoroom call):
  * RBAC: the route is catalogued in rbac_policy.POLICY (catalog roles) and a
    SALES_STAFF caller is 403'd.
  * Editor unavailable (no Photoroom key) -> 400 with the operator-facing
    "Settings -> Integrations" hint (provider internals never leak).
  * Happy path: an available editor -> 201 with a NEW {file_id, url}, and the
    result is stored via store.put with mime_type image/png (kind=product_image,
    edited_from stamp) while the original id is preserved.
  * A missing / wrong-kind file_id -> 404.

Run: JWT_SECRET_KEY=test python -m pytest \
       backend/tests/test_product_image_edit.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from api.routers import products as products_router  # noqa: E402
from api.services import rbac_policy as rbac  # noqa: E402
from api.services.file_store import InMemoryFileStore, set_file_store  # noqa: E402

_EDIT_PATH_TMPL = "/api/v1/products/image/{file_id}/edit"
_CATALOG_SET = {"ADMIN", "CATALOG_MANAGER"}

_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_EDITED_BYTES = b"PNGBYTES"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeEditor:
    """Stand-in for the configured image editor. `_available` toggles whether
    the provider is set up; `edit` returns fixed cleaned bytes."""

    name = "fake"

    def __init__(self, available: bool = True, raises: Exception | None = None):
        self._available = available
        self._raises = raises

    def available(self) -> bool:
        return self._available

    async def edit(self, raw: bytes, spec) -> bytes:  # noqa: ANN001
        if self._raises is not None:
            raise self._raises
        return _EDITED_BYTES


@pytest.fixture
def mem_store():
    """Swap the module-level file store for an in-memory one so the edit
    round-trip works without a real GridFS. Restored after the test."""
    store = InMemoryFileStore()
    set_file_store(store)
    yield store
    set_file_store(None)


def _seed_image(store: InMemoryFileStore) -> str:
    """Store a product image and return its file_id."""
    return store.put(
        content=_PNG_BYTES,
        filename="photo.png",
        mime_type="image/png",
        metadata={"kind": "product_image"},
    )


def _install_editor(monkeypatch, editor):
    """Route get_image_editor (looked up on the products router module) to a
    fake so no real Photoroom call happens."""
    monkeypatch.setattr(products_router, "get_image_editor", lambda: editor)


# ---------------------------------------------------------------------------
# RBAC catalogue + role gate
# ---------------------------------------------------------------------------


def test_edit_route_catalogued_with_catalog_roles():
    entry = rbac.policy_for("POST", _EDIT_PATH_TMPL.format(file_id="abc123"))
    assert entry is not None, "edit route not catalogued in rbac_policy"
    assert set(entry["allowed"]) == _CATALOG_SET
    assert str(entry["path"]).endswith("/image/{file_id}/edit")


def test_edit_check_access_allows_catalog_denies_others():
    path = _EDIT_PATH_TMPL.format(file_id="abc123")
    for role in ("SUPERADMIN", "ADMIN", "CATALOG_MANAGER"):
        assert rbac.check_access("POST", path, [role]) is True, role
    for role in ("SALES_STAFF", "CASHIER", "OPTOMETRIST", "WORKSHOP_STAFF"):
        assert rbac.check_access("POST", path, [role]) is False, role


def test_edit_rbac_denied_below_catalog_roles(client, staff_headers, mem_store):
    fid = _seed_image(mem_store)
    r = client.post(_EDIT_PATH_TMPL.format(file_id=fid), headers=staff_headers)
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# Editor unavailable -> 400 with the operator-facing hint (no provider leak)
# ---------------------------------------------------------------------------


def test_edit_editor_unavailable_returns_400_with_hint(
    client, auth_headers, mem_store, monkeypatch
):
    fid = _seed_image(mem_store)
    _install_editor(monkeypatch, _FakeEditor(available=False))
    r = client.post(_EDIT_PATH_TMPL.format(file_id=fid), headers=auth_headers)
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "Settings -> Integrations" in detail
    assert "Photoroom" in detail


# ---------------------------------------------------------------------------
# Happy path: available editor -> 201 + new file_id/url, stored as image/png
# ---------------------------------------------------------------------------


def test_edit_available_returns_201_and_stores_png(
    client, auth_headers, mem_store, monkeypatch
):
    fid = _seed_image(mem_store)
    _install_editor(monkeypatch, _FakeEditor(available=True))

    # Spy on store.put to assert the mime_type passed through.
    put_calls = []
    orig_put = mem_store.put

    def spy_put(**kwargs):
        put_calls.append(kwargs)
        return orig_put(**kwargs)

    monkeypatch.setattr(mem_store, "put", spy_put)

    r = client.post(_EDIT_PATH_TMPL.format(file_id=fid), headers=auth_headers)
    assert r.status_code == 201, r.text
    body = r.json()

    new_id = body["file_id"]
    assert new_id
    assert new_id != fid  # a NEW image; the original is preserved
    assert body["url"] == f"/api/v1/products/image/{new_id}"

    # store.put was called once, with the edited PNG bytes + image/png mime.
    assert len(put_calls) == 1
    call = put_calls[0]
    assert call["mime_type"] == "image/png"
    assert call["content"] == _EDITED_BYTES
    assert call["metadata"]["kind"] == "product_image"
    assert call["metadata"]["edited_from"] == fid

    # The original image is untouched, and the new id serves the edited bytes.
    assert mem_store.get(fid, require_kind="product_image")[0] == _PNG_BYTES
    served = client.get(body["url"])
    assert served.status_code == 200
    assert served.content == _EDITED_BYTES


def test_edit_provider_error_maps_to_502(
    client, auth_headers, mem_store, monkeypatch
):
    fid = _seed_image(mem_store)
    _install_editor(
        monkeypatch, _FakeEditor(available=True, raises=RuntimeError("boom"))
    )
    r = client.post(_EDIT_PATH_TMPL.format(file_id=fid), headers=auth_headers)
    assert r.status_code == 502, r.text
    assert "Background removal failed" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Missing / wrong-kind file_id -> 404
# ---------------------------------------------------------------------------


def test_edit_missing_id_is_404(client, auth_headers, mem_store, monkeypatch):
    _install_editor(monkeypatch, _FakeEditor(available=True))
    r = client.post(
        _EDIT_PATH_TMPL.format(file_id="does-not-exist"), headers=auth_headers
    )
    assert r.status_code == 404, r.text


def test_edit_wrong_kind_id_is_404(client, auth_headers, mem_store, monkeypatch):
    _install_editor(monkeypatch, _FakeEditor(available=True))
    grn_id = mem_store.put(
        content=b"SENSITIVE GRN INVOICE PDF",
        filename="grn.pdf",
        mime_type="application/pdf",
        metadata={"kind": "grn_attachment"},
    )
    r = client.post(_EDIT_PATH_TMPL.format(file_id=grn_id), headers=auth_headers)
    assert r.status_code == 404, r.text
