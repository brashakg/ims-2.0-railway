"""
APP-01: GET /stores with the database unreachable must SAY so (503), not
answer an empty list. The store picker offers the owner first-store setup on a
confirmed-empty list; an outage answered as [] would masquerade as a new
installation. Revert the 503 in list_stores and test_outage_is_503 goes red.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import stores  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


class _Repo:
    def __init__(self, docs):
        self.docs = docs

    def find_active(self, _filter=None):
        return [d for d in self.docs if d.get("is_active", True)]

    def find_many(self, _filter, sort=None):
        return list(self.docs)


def _client(repo, monkeypatch):
    monkeypatch.setattr(stores, "get_store_repository", lambda: repo)
    app = FastAPI()
    app.include_router(stores.router, prefix="/api/v1/stores")
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "u1",
        "roles": ["SUPERADMIN"],
        "active_store_id": None,
    }
    return TestClient(app)


def test_outage_is_503(monkeypatch):
    r = _client(None, monkeypatch).get("/api/v1/stores")
    assert r.status_code == 503, r.text
    assert "stores" not in r.json()


def test_empty_install_is_an_empty_list(monkeypatch):
    r = _client(_Repo([]), monkeypatch).get("/api/v1/stores")
    assert r.status_code == 200
    assert r.json() == {"stores": [], "total": 0}


def test_stores_listed(monkeypatch):
    r = _client(_Repo([{"store_id": "BV-BOK-02"}]), monkeypatch).get("/api/v1/stores")
    assert r.status_code == 200
    assert r.json()["total"] == 1
