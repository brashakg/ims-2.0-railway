"""
IMS 2.0 - Store create: store_id IS the human store_code (not a uuid)
=====================================================================
Root-cause guard for the "raw UUID leaks into the UI" bug. A store created via
the Organization screen used to get a random uuid as its store_id (with the
human code in a separate store_code field). But the ENTIRE app keys off store_id
as the human code (users[].store_ids reference stores by code, store-scope checks
compare these strings, the topbar pill renders activeStoreId AS the code, invoice
prefixes derive from it). So store_id MUST equal the validated, uppercased
store_code.

Locks:
  * create assigns store_id == store_code (uppercased), NOT a uuid.
  * a malformed / uuid-shaped store_code is rejected (400).
  * a duplicate store_code is rejected (409).

Uses the TestClient + fake-repo harness (mirrors test_user_role_guards.py) so it
runs with no DB.
"""

from __future__ import annotations

import os
import re
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import stores  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)


# ---------------------------------------------------------------------------
# Fake store repo: mirrors the real create() id-assignment contract -- if the
# caller does NOT supply store_id, BaseRepository.create() would mint a uuid.
# The router must set store_id == store_code so this branch is never taken.
# ---------------------------------------------------------------------------


class _FakeStoreRepo:
    def __init__(self, seed=None):
        self._docs = {}  # store_id -> doc
        if seed:
            for doc in seed:
                self._docs[doc["store_id"]] = dict(doc)

    def find_by_code(self, store_code):
        for d in self._docs.values():
            if d.get("store_code") == store_code:
                return dict(d)
        return None

    def find_by_id(self, store_id):
        d = self._docs.get(store_id)
        return dict(d) if d else None

    def create(self, store_data):
        import uuid

        doc = dict(store_data)
        # Replicate BaseRepository.create(): auto-mint a uuid id ONLY if the
        # caller didn't provide one. The fix means the router always provides it.
        if "store_id" not in doc:
            doc["store_id"] = str(uuid.uuid4())
        doc["_id"] = doc["store_id"]
        self._docs[doc["store_id"]] = doc
        return dict(doc)


class _FakeColl:
    def __init__(self, docs):
        self._docs = docs

    def find_one(self, query, *args, **kwargs):
        for d in self._docs:
            if all(d.get(k) == v for k, v in query.items()):
                return dict(d)
        return None


class _FakeDB:
    """Minimal db with an entities collection so the router's entity check
    passes."""

    def __init__(self, entities):
        self._entities = _FakeColl(entities)

    def get_collection(self, name):
        if name == "entities":
            return self._entities
        return _FakeColl([])


_SUPER = {"user_id": "su-1", "roles": ["SUPERADMIN"], "store_ids": []}

_ENTITY = {
    "entity_id": "ent_abc123",
    "gstins": [{"gstin": "20AAPFU0939F1ZV", "state_code": "20", "is_primary": True}],
}

_BASE = {
    "store_name": "Better Vision Bokaro",
    "brand": "BETTER_VISION",
    "entity_id": "ent_abc123",
    "address": "Main Road",
    "city": "Bokaro",
    "state": "Jharkhand",
    "state_code": "20",
    "pincode": "827001",
    "phone": "9876543210",
}


def _client(repo, db, monkeypatch):
    monkeypatch.setattr(stores, "get_store_repository", lambda: repo)
    monkeypatch.setattr(stores, "_get_db", lambda: db)
    app = FastAPI()
    app.include_router(stores.router, prefix="/api/v1/stores")

    async def _u():
        return dict(_SUPER)

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


def test_store_id_equals_uppercased_store_code(monkeypatch):
    repo = _FakeStoreRepo()
    c = _client(repo, _FakeDB([_ENTITY]), monkeypatch)
    r = c.post("/api/v1/stores", json=dict(_BASE, store_code="bv-bok-01"))
    assert r.status_code == 201, r.text
    body = r.json()
    # store_id IS the human code, uppercased -- NOT a uuid
    assert body["store_id"] == "BV-BOK-01"
    assert body["store_code"] == "BV-BOK-01"
    assert not _UUID_RE.match(body["store_id"])
    # persisted doc agrees, and store_id never auto-minted to a uuid
    saved = repo.find_by_id("BV-BOK-01")
    assert saved is not None
    assert saved["store_id"] == saved["store_code"] == "BV-BOK-01"


def test_duplicate_store_code_rejected_409(monkeypatch):
    repo = _FakeStoreRepo()
    c = _client(repo, _FakeDB([_ENTITY]), monkeypatch)
    first = c.post("/api/v1/stores", json=dict(_BASE, store_code="BV-BOK-01"))
    assert first.status_code == 201, first.text
    dup = c.post("/api/v1/stores", json=dict(_BASE, store_code="bv-bok-01"))
    assert dup.status_code == 409, dup.text
    assert "already exists" in dup.json()["detail"]


def test_uuid_shaped_store_code_rejected_400(monkeypatch):
    repo = _FakeStoreRepo()
    c = _client(repo, _FakeDB([_ENTITY]), monkeypatch)
    r = c.post(
        "/api/v1/stores",
        json=dict(_BASE, store_code="4dc49c44"),  # not a human code (no hyphen->ok len but)
    )
    # "4dc49c44" starts with a digit -> rejected by the store-code rule
    assert r.status_code == 400, r.text
    assert "store code" in r.json()["detail"].lower()


def test_blank_store_code_rejected(monkeypatch):
    repo = _FakeStoreRepo()
    c = _client(repo, _FakeDB([_ENTITY]), monkeypatch)
    # pydantic min_length=2 rejects an empty string at the schema layer (422)
    r = c.post("/api/v1/stores", json=dict(_BASE, store_code=""))
    assert r.status_code in (400, 422), r.text
