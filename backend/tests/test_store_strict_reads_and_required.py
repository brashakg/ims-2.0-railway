"""
#1434 round 1 (Codex P2) and decision D73, driven through the REAL
StoreRepository over a mongomock collection (no fake repository):

* P2: a database error while READING the store list is not an empty install.
  The repository used to swallow it and answer [], so GET /stores said
  200 {stores: [], total: 0} and the picker/Organization showed 0 stores.
  Now 503, both active-only and inactive-inclusive; a real empty list stays 200.
* Two creates of one code at once: the loser gets 409, never 500.
* Create with no repository: 503, never a false "Store created".
* D73: a NEW store needs store name, state, city, PIN, phone and address
  (blank / whitespace refused, 422 naming the field). An edit of an older
  store with blanks is accepted; it may not blank a field that has a value.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

import mongomock  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pymongo.errors import AutoReconnect  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import stores  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from database.repositories.store_repository import StoreRepository  # noqa: E402

ENTITY = {"entity_id": "ent_1", "name": "BV", "gstins": []}
BODY = {
    "entity_id": "ent_1", "store_code": "BV-NEW-01", "store_name": "New Shop",
    "brand": "BETTER_VISION", "address": "Main Road", "city": "Pune",
    "state": "Maharashtra", "state_code": "27", "pincode": "411001",
    "phone": "9000014341",
}


class _Db:
    def __init__(self, mdb):
        self._mdb = mdb

    def get_collection(self, name):
        return self._mdb[name]


@pytest.fixture
def env(monkeypatch):
    mdb = mongomock.MongoClient().db
    mdb.entities.insert_one(dict(ENTITY))
    repo = StoreRepository(mdb.stores)
    monkeypatch.setattr(stores, "get_store_repository", lambda: repo)
    monkeypatch.setattr(stores, "_get_db", lambda: _Db(mdb))
    app = FastAPI()
    app.include_router(stores.router, prefix="/api/v1/stores")
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "u1", "roles": ["ADMIN"], "active_store_id": None, "store_ids": [],
    }
    return TestClient(app), mdb, repo


def _break_reads(monkeypatch, coll):
    def boom(*_a, **_k):
        raise AutoReconnect("store read unavailable")
    monkeypatch.setattr(type(coll), "find", boom)


@pytest.mark.parametrize("path", ["/api/v1/stores", "/api/v1/stores?active_only=false"])
def test_read_failure_is_503_not_empty(env, monkeypatch, path):
    client, mdb, _ = env
    mdb.stores.insert_one({"_id": "BV-A", "store_id": "BV-A", "is_active": True, "brand": "B", "store_name": "A"})
    _break_reads(monkeypatch, mdb.stores)
    r = client.get(path)
    assert r.status_code == 503, r.text
    assert "stores" not in r.json()


def test_real_empty_list_stays_200(env):
    client, _, _ = env
    r = client.get("/api/v1/stores")
    assert r.status_code == 200 and r.json() == {"stores": [], "total": 0}


def test_fail_soft_default_unchanged_for_other_callers(env, monkeypatch):
    _, mdb, repo = env
    _break_reads(monkeypatch, mdb.stores)
    assert repo.find_many({}) == []
    with pytest.raises(AutoReconnect):
        repo.find_many({}, strict=True)


def test_concurrent_duplicate_insert_is_409(env, monkeypatch):
    client, mdb, repo = env
    # Both requests passed the duplicate READ; the other one inserted first.
    monkeypatch.setattr(repo, "find_by_code", lambda _c: None)
    monkeypatch.setattr(repo, "find_by_id", lambda _i: None)
    mdb.stores.insert_one({"_id": "BV-NEW-01", "store_id": "BV-NEW-01", "store_code": "BV-NEW-01"})
    r = client.post("/api/v1/stores", json=BODY)
    assert r.status_code == 409, r.text
    assert mdb.stores.count_documents({"store_id": "BV-NEW-01"}) == 1


def test_identical_retry_after_create_is_409_and_one_row(env):
    client, mdb, _ = env
    assert client.post("/api/v1/stores", json=BODY).status_code == 201
    assert client.post("/api/v1/stores", json=BODY).status_code == 409
    assert mdb.stores.count_documents({"store_id": "BV-NEW-01"}) == 1


def test_create_without_repository_is_503(env, monkeypatch):
    client, _, _ = env
    monkeypatch.setattr(stores, "get_store_repository", lambda: None)
    r = client.post("/api/v1/stores", json=BODY)
    assert r.status_code == 503, r.text


def test_entity_read_failure_is_503_not_unknown_entity(env, monkeypatch):
    client, mdb, _ = env

    def boom(*_a, **_k):
        raise AutoReconnect("entity read unavailable")
    monkeypatch.setattr(type(mdb.entities), "find_one", boom)
    r = client.post("/api/v1/stores", json=BODY)
    assert r.status_code == 503, r.text


@pytest.mark.parametrize("field,label", [
    ("store_name", "Store name"), ("state", "State"), ("city", "City"),
    ("pincode", "PIN code"), ("phone", "Phone"), ("address", "Address"),
])
@pytest.mark.parametrize("blank", ["", "   "])
def test_d73_new_store_blank_required_field_is_422(env, field, label, blank):
    client, mdb, _ = env
    r = client.post("/api/v1/stores", json={**BODY, field: blank})
    assert r.status_code == 422, r.text
    assert label in r.json()["detail"]
    assert mdb.stores.count_documents({}) == 0


def test_d73_legacy_blanks_edit_ok_but_never_blank_a_filled_field(env):
    client, mdb, _ = env
    mdb.stores.insert_one({
        "_id": "BV-OLD-01", "store_id": "BV-OLD-01", "store_code": "BV-OLD-01",
        "store_name": "Old", "state": "Jharkhand", "address": "", "city": "",
        "pincode": "", "phone": "", "is_active": True,
    })
    assert client.put("/api/v1/stores/BV-OLD-01", json={"store_name": "Old edited"}).status_code == 200
    # re-sending the existing blanks (what the form does) is fine
    assert client.put("/api/v1/stores/BV-OLD-01", json={"store_name": "Old 2", "phone": "", "address": ""}).status_code == 200
    for field, label in (("state", "State"), ("store_name", "Store name")):
        r = client.put("/api/v1/stores/BV-OLD-01", json={field: "  "})
        assert r.status_code == 422 and label in r.json()["detail"], r.text
    r = client.put("/api/v1/stores/BV-OLD-01", json={"state": None})
    assert r.status_code == 422, r.text
    doc = mdb.stores.find_one({"_id": "BV-OLD-01"})
    assert doc["state"] == "Jharkhand" and doc["store_name"] == "Old 2"
