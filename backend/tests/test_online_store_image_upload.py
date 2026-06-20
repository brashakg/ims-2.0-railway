"""
Tests for the BVI Phase 4a durable image-upload endpoint
(POST /api/v1/online-store/images/upload).

Covers, over a TestClient with no live Mongo:
  * RBAC: the route is catalogued in rbac_policy.POLICY with the ecom role set,
    the literal /upload out-ranks the {image_id} param route, and a SALES_STAFF
    caller is 403'd before the handler (no DB needed).
  * Happy path: a small PNG is uploaded -> object_storage.put (monkeypatched to
    return a deterministic url) -> 200 with {url, storage_backend, kind} AND an
    IMAGE_UPLOAD audit row is written.
  * Security guards: a rejected content-type -> 415; an oversize body -> 413; an
    empty body -> 400.

Run: JWT_SECRET_KEY=test python -m pytest \
       backend/tests/test_online_store_image_upload.py -q
"""

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from api.services import rbac_policy as rbac  # noqa: E402

_UPLOAD_PATH = "/api/v1/online-store/images/upload"
_ECOM_SET = {"ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"}

# A tiny valid-enough PNG header + a few bytes (the endpoint trusts the declared
# content-type, not the magic bytes -- it only needs SOME bytes to persist).
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


# ---------------------------------------------------------------------------
# RBAC catalogue + route resolution + live role gate
# ---------------------------------------------------------------------------


def test_upload_route_catalogued_with_ecom_roles():
    entry = rbac.policy_for("POST", _UPLOAD_PATH)
    assert entry is not None, "upload route not catalogued in rbac_policy"
    assert set(entry["allowed"]) == _ECOM_SET


def test_upload_literal_beats_image_id_param():
    """POST /images/upload must resolve to its own literal entry, NOT be shadowed
    by the bare /images/{image_id} param route (which is GET/PUT/DELETE anyway,
    but the literal-first matcher is what guarantees correctness)."""
    hit = rbac.policy_for("POST", _UPLOAD_PATH)
    assert hit is not None and str(hit["path"]).endswith("/images/upload")


def test_check_access_allows_ecom_denies_others():
    for role in ("SUPERADMIN", "ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER"):
        assert rbac.check_access("POST", _UPLOAD_PATH, [role]) is True, role
    for role in ("SALES_STAFF", "CASHIER", "OPTOMETRIST", "WORKSHOP_STAFF"):
        assert rbac.check_access("POST", _UPLOAD_PATH, [role]) is False, role


def test_upload_rbac_denied_below_allowed_roles(client, staff_headers):
    """SALES_STAFF is outside the ecom set -> 403 before the handler runs."""
    files = {"file": ("x.png", io.BytesIO(_PNG_BYTES), "image/png")}
    r = client.post(_UPLOAD_PATH, headers=staff_headers, files=files)
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# Storage + audit monkeypatch (no live Mongo, no real S3/disk write)
# ---------------------------------------------------------------------------


class _FakeStorage:
    name = "fake-s3"

    def __init__(self):
        self.calls = []

    def available(self) -> bool:
        return True

    def put(self, key: str, data: bytes, content_type: str = "image/png") -> str:
        self.calls.append((key, len(data), content_type))
        return f"https://cdn.example.test/{key}"


class _AuditSpy:
    def __init__(self):
        self.rows = []

    def create(self, doc):
        self.rows.append(doc)
        return doc


@pytest.fixture
def patched_storage_and_audit(monkeypatch):
    """Point object_storage.get_object_storage at a _FakeStorage and
    dependencies.get_audit_repository at an _AuditSpy so the upload route
    persists + audits without a real backend. Returns (storage, audit)."""
    from api.services import object_storage as obj_store
    from api import dependencies as deps

    storage = _FakeStorage()
    audit = _AuditSpy()
    monkeypatch.setattr(obj_store, "get_object_storage", lambda: storage)
    monkeypatch.setattr(deps, "get_audit_repository", lambda: audit)
    return storage, audit


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_upload_happy_path_returns_url_and_writes_audit(
    client, auth_headers, patched_storage_and_audit
):
    storage, audit = patched_storage_and_audit
    files = {"file": ("anything.png", io.BytesIO(_PNG_BYTES), "image/png")}
    data = {"product_id": "P1", "kind": "EDITED"}
    r = client.post(_UPLOAD_PATH, headers=auth_headers, files=files, data=data)
    assert r.status_code == 200, r.text
    body = r.json()

    # Returns the durable url + backend + the (validated) kind.
    assert body["url"].startswith("https://cdn.example.test/")
    assert body["storage_backend"] == "fake-s3"
    assert body["kind"] == "EDITED"
    assert body["content_type"] == "image/png"
    assert body["size"] == len(_PNG_BYTES)

    # The storage key is generated (uuid + .png), product-scoped, NOT the client
    # filename -> no path traversal, no "anything.png" leakage.
    assert len(storage.calls) == 1
    key, size, ct = storage.calls[0]
    assert key.startswith("P1/") and key.endswith(".png")
    assert "anything" not in key
    assert size == len(_PNG_BYTES) and ct == "image/png"

    # Exactly one IMAGE_UPLOAD audit row, metadata only (no bytes).
    upload_rows = [r for r in audit.rows if r.get("action") == "IMAGE_UPLOAD"]
    assert len(upload_rows) == 1
    details = upload_rows[0]["details"]
    assert details["product_id"] == "P1"
    assert details["kind"] == "EDITED"
    assert details["storage_backend"] == "fake-s3"
    assert details["size"] == len(_PNG_BYTES)
    assert details["content_type"] == "image/png"
    # The bytes themselves are NEVER in the audit row.
    assert "data" not in details and "bytes" not in details


def test_upload_defaults_kind_to_raw(client, auth_headers, patched_storage_and_audit):
    """No kind form field -> defaults to RAW."""
    files = {"file": ("p.jpg", io.BytesIO(b"\xff\xd8\xff" + b"0" * 32), "image/jpeg")}
    r = client.post(_UPLOAD_PATH, headers=auth_headers, files=files)
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "RAW"


# ---------------------------------------------------------------------------
# Security guards
# ---------------------------------------------------------------------------


def test_upload_rejects_disallowed_content_type(
    client, auth_headers, patched_storage_and_audit
):
    """A non-image (or non-allowlisted) content-type -> 415, and nothing is
    persisted or audited."""
    storage, audit = patched_storage_and_audit
    files = {"file": ("evil.svg", io.BytesIO(b"<svg/>"), "image/svg+xml")}
    r = client.post(_UPLOAD_PATH, headers=auth_headers, files=files)
    assert r.status_code == 415, r.text
    assert storage.calls == []
    assert [x for x in audit.rows if x.get("action") == "IMAGE_UPLOAD"] == []


def test_upload_rejects_plain_text(client, auth_headers, patched_storage_and_audit):
    files = {"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")}
    r = client.post(_UPLOAD_PATH, headers=auth_headers, files=files)
    assert r.status_code == 415, r.text


def test_upload_rejects_oversize(client, auth_headers, patched_storage_and_audit):
    """A body over the 10 MB cap -> 413, and nothing is persisted."""
    storage, _ = patched_storage_and_audit
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (10 * 1024 * 1024 + 1)
    files = {"file": ("big.png", io.BytesIO(big), "image/png")}
    r = client.post(_UPLOAD_PATH, headers=auth_headers, files=files)
    assert r.status_code == 413, r.text
    assert storage.calls == []


def test_upload_rejects_empty_body(client, auth_headers, patched_storage_and_audit):
    files = {"file": ("empty.png", io.BytesIO(b""), "image/png")}
    r = client.post(_UPLOAD_PATH, headers=auth_headers, files=files)
    assert r.status_code == 400, r.text


def test_upload_bad_kind_is_400(client, auth_headers, patched_storage_and_audit):
    """An unknown kind value is a 400 (the shared enum validator)."""
    files = {"file": ("p.png", io.BytesIO(_PNG_BYTES), "image/png")}
    r = client.post(
        _UPLOAD_PATH, headers=auth_headers, files=files, data={"kind": "BOGUS"}
    )
    assert r.status_code == 400, r.text
