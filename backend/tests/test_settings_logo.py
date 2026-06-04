"""
IMS 2.0 - Company logo upload + serve
=====================================
The Settings -> Company Profile "Upload new logo" button was a dead
"coming soon" stub backed by a placeholder endpoint that returned a fake
"/images/logo.png" URL. This wires it to a real upload:

  POST /api/v1/settings/business/logo        -> stores image, returns logo_url
  GET  /api/v1/settings/business/logo/{id}   -> streams the stored image

These tests drive the real router against the in-memory file store
(InMemoryFileStore) so they pass with or without a live Mongo.
"""
from __future__ import annotations

import pytest


# A 1x1 transparent PNG (valid minimal image payload).
_PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000154a24f7d0000000049454e44ae42"
    "6082"
)


@pytest.fixture
def fake_file_store():
    """Swap in the in-memory file store for the duration of a test so the
    logo round-trip works without a real GridFS / Mongo."""
    from api.services.file_store import set_file_store, InMemoryFileStore

    store = InMemoryFileStore()
    set_file_store(store)
    yield store
    set_file_store(None)


def _upload(client, headers, *, content=_PNG_1x1, filename="logo.png", mime="image/png"):
    return client.post(
        "/api/v1/settings/business/logo",
        headers=headers,
        files={"file": (filename, content, mime)},
    )


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


def test_admin_can_upload_logo_and_get_url(client, auth_headers, fake_file_store):
    r = _upload(client, auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mime_type"] == "image/png"
    assert body["size_bytes"] == len(_PNG_1x1)
    file_id = body["file_id"]
    assert file_id
    # The returned URL points at the serve endpoint.
    assert body["logo_url"] == f"/api/v1/settings/business/logo/{file_id}"
    # Back-compat alias retained.
    assert body["url"] == body["logo_url"]
    # The blob actually landed in the store.
    assert fake_file_store.get(file_id) is not None


def test_uploaded_logo_can_be_served_back(client, auth_headers, fake_file_store):
    file_id = _upload(client, auth_headers).json()["file_id"]
    got = client.get(
        f"/api/v1/settings/business/logo/{file_id}", headers=auth_headers
    )
    assert got.status_code == 200, got.text
    assert got.content == _PNG_1x1
    assert got.headers["content-type"].startswith("image/png")


def test_staff_cannot_upload_logo(client, staff_headers, fake_file_store):
    r = _upload(client, staff_headers)
    assert r.status_code == 403, r.text


def test_unauthenticated_upload_rejected(client, fake_file_store):
    r = _upload(client, {})
    assert r.status_code in (401, 403), r.text


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_non_image_mime_rejected(client, auth_headers, fake_file_store):
    r = _upload(
        client,
        auth_headers,
        content=b"%PDF-1.4 not an image",
        filename="brochure.pdf",
        mime="application/pdf",
    )
    assert r.status_code == 415, r.text


def test_empty_file_rejected(client, auth_headers, fake_file_store):
    r = _upload(client, auth_headers, content=b"")
    # Starlette/UploadFile treats a zero-byte part as empty -> our 400 guard
    # (some stacks 422 the missing part). Either is an honest reject.
    assert r.status_code in (400, 422), r.text


def test_oversized_logo_rejected(client, auth_headers, fake_file_store):
    big = b"\x89PNG" + b"0" * (5 * 1024 * 1024 + 1)
    r = _upload(client, auth_headers, content=big, filename="huge.png", mime="image/png")
    assert r.status_code == 413, r.text


# ---------------------------------------------------------------------------
# Serve edge cases
# ---------------------------------------------------------------------------


def test_serve_missing_logo_is_404(client, auth_headers, fake_file_store):
    got = client.get(
        "/api/v1/settings/business/logo/does-not-exist", headers=auth_headers
    )
    assert got.status_code == 404, got.text
