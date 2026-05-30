"""
IMS 2.0 - Per-user module access (deny-only RBAC override) persistence
======================================================================
Module access is a DENY-ONLY override LAYERED ON TOP of the role: a module key
set to False hides + route-blocks that module for the user even when their role
allows it. The role stays the ceiling -- this never grants access. These tests
lock the BACKEND contract:

  - create_user with module_access={"reports": False} PERSISTS it on the doc.
  - update_user with a new module_access OVERWRITES the stored value.
  - update_user WITHOUT module_access leaves the existing value UNCHANGED
    (no accidental wipe on an unrelated edit -- exclude_unset).
  - the value ROUND-TRIPS verbatim (shape + booleans preserved).
  - module_access is RETURNED in the auth/login payload + the JWT claims and
    surfaces on /auth/me, defaulting to {} for backward compatibility.

TestClient + a fake user repo + dependency/monkeypatch override, mirroring
test_pos_authz.py.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import users, auth  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


# ---------------------------------------------------------------------------
# Fake user repository -- in-memory, mirrors the slice of UserRepository the
# users router touches (find_by_username / find_by_email / find_by_id / create
# / update).
# ---------------------------------------------------------------------------


class _FakeUserRepo:
    def __init__(self, seed=None):
        # store keyed by user_id
        self._docs = {}
        if seed:
            for doc in seed:
                self._docs[doc["user_id"]] = dict(doc)
        self.last_update = None

    def find_by_username(self, username):
        for d in self._docs.values():
            if d.get("username") == username:
                return dict(d)
        return None

    def find_by_email(self, email):
        for d in self._docs.values():
            if d.get("email") == email:
                return dict(d)
        return None

    def find_by_id(self, user_id):
        d = self._docs.get(user_id)
        return dict(d) if d else None

    def create(self, user_data):
        # Real repo assigns a user_id; emulate it.
        doc = dict(user_data)
        doc.setdefault("user_id", "u-new")
        self._docs[doc["user_id"]] = doc
        return dict(doc)

    def update(self, user_id, update_data):
        d = self._docs.get(user_id)
        if d is None:
            return False
        d.update(update_data)
        self.last_update = dict(update_data)
        return True


def _admin_client(repo, monkeypatch):
    """An app with the users router, the repo monkeypatched in, and the current
    user forced to a SUPERADMIN (clears require_admin)."""
    monkeypatch.setattr(users, "get_user_repository", lambda: repo)
    app = FastAPI()
    app.include_router(users.router, prefix="/api/v1/users")

    async def _u():
        return {"user_id": "admin-1", "full_name": "Admin", "roles": ["SUPERADMIN"],
                "store_ids": ["S1"], "active_store_id": "S1"}

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


_BASE_CREATE = {
    "username": "denyme",
    "email": "denyme@example.com",
    "password": "Welcome@123",
    "full_name": "Deny Me",
    "roles": ["STORE_MANAGER"],
    "store_ids": ["S1"],
}


# --- create_user persists module_access ------------------------------------


def test_create_user_persists_module_access(monkeypatch):
    repo = _FakeUserRepo()
    c = _admin_client(repo, monkeypatch)
    body = dict(_BASE_CREATE, module_access={"reports": False})
    r = c.post("/api/v1/users", json=body)
    assert r.status_code == 201, r.text
    stored = repo.find_by_id("u-new")
    assert stored is not None
    assert stored["module_access"] == {"reports": False}


def test_create_user_without_module_access_defaults_to_empty_dict(monkeypatch):
    # Absent on create -> stored as {} (role defaults apply for every module),
    # never null, so downstream code always sees a dict.
    repo = _FakeUserRepo()
    c = _admin_client(repo, monkeypatch)
    r = c.post("/api/v1/users", json=dict(_BASE_CREATE))
    assert r.status_code == 201, r.text
    assert repo.find_by_id("u-new")["module_access"] == {}


def test_create_user_module_access_round_trips_multiple_keys(monkeypatch):
    repo = _FakeUserRepo()
    c = _admin_client(repo, monkeypatch)
    ma = {"reports": False, "finance": False, "pos": True}
    r = c.post("/api/v1/users", json=dict(_BASE_CREATE, module_access=ma))
    assert r.status_code == 201, r.text
    assert repo.find_by_id("u-new")["module_access"] == ma


# --- update_user updates module_access -------------------------------------


def _seed_repo(module_access=None):
    doc = {
        "user_id": "u1",
        "username": "existing",
        "email": "existing@example.com",
        "full_name": "Existing User",
        "roles": ["STORE_MANAGER"],
        "store_ids": ["S1"],
        "phone": "9990001111",
        "is_active": True,
    }
    if module_access is not None:
        doc["module_access"] = module_access
    return _FakeUserRepo([doc])


def test_update_user_sets_module_access(monkeypatch):
    repo = _seed_repo()
    c = _admin_client(repo, monkeypatch)
    r = c.put("/api/v1/users/u1", json={"module_access": {"finance": False}})
    assert r.status_code == 200, r.text
    assert repo.find_by_id("u1")["module_access"] == {"finance": False}


def test_update_user_overwrites_existing_module_access(monkeypatch):
    repo = _seed_repo({"reports": False})
    c = _admin_client(repo, monkeypatch)
    r = c.put("/api/v1/users/u1", json={"module_access": {"finance": False, "hr": False}})
    assert r.status_code == 200, r.text
    # New map fully replaces the old one (not merged).
    assert repo.find_by_id("u1")["module_access"] == {"finance": False, "hr": False}


def test_update_without_module_access_leaves_it_unchanged(monkeypatch):
    # The #1 safety property: editing an unrelated field (phone) must NOT wipe a
    # previously-granted module restriction.
    repo = _seed_repo({"reports": False})
    c = _admin_client(repo, monkeypatch)
    r = c.put("/api/v1/users/u1", json={"phone": "8887776666"})
    assert r.status_code == 200, r.text
    stored = repo.find_by_id("u1")
    assert stored["phone"] == "8887776666"
    assert stored["module_access"] == {"reports": False}  # untouched
    # exclude_unset means module_access wasn't even in the update payload.
    assert "module_access" not in (repo.last_update or {})


def test_update_can_clear_restrictions_with_empty_dict(monkeypatch):
    # Explicitly sending {} lifts all restrictions (role defaults restored).
    repo = _seed_repo({"reports": False, "finance": False})
    c = _admin_client(repo, monkeypatch)
    r = c.put("/api/v1/users/u1", json={"module_access": {}})
    assert r.status_code == 200, r.text
    assert repo.find_by_id("u1")["module_access"] == {}


# --- auth payload returns module_access ------------------------------------


def _login_client(db_user, monkeypatch):
    """An app with the auth router whose user lookup returns db_user."""
    repo = _FakeUserRepo([db_user]) if db_user else _FakeUserRepo()

    # Login reads user_repo.collection.find_one({...}); give the fake a tiny
    # collection shim that searches by username then email.
    class _Coll:
        def find_one(self, q):
            if "username" in q:
                return repo.find_by_username(q["username"])
            if "email" in q:
                return repo.find_by_email(q["email"])
            return None

    repo.collection = _Coll()
    monkeypatch.setattr(auth, "get_user_repository", lambda: repo, raising=False)
    # login imports get_user_repository lazily from ..dependencies; patch there.
    from api import dependencies as deps
    monkeypatch.setattr(deps, "get_user_repository", lambda: repo)

    app = FastAPI()
    app.include_router(auth.router, prefix="/api/v1/auth")
    return TestClient(app), repo


def _bcrypt_hash(pw):
    import bcrypt as _bc
    return _bc.hashpw(pw.encode(), _bc.gensalt(rounds=4)).decode()


def test_login_returns_module_access(monkeypatch):
    db_user = {
        "user_id": "u-login",
        "username": "loginme",
        "email": "loginme@example.com",
        "full_name": "Login Me",
        "password_hash": _bcrypt_hash("Secret123"),
        "roles": ["STORE_MANAGER"],
        "store_ids": ["S1"],
        "is_active": True,
        "module_access": {"reports": False},
    }
    client, _ = _login_client(db_user, monkeypatch)
    r = client.post("/api/v1/auth/login", json={"username": "loginme", "password": "Secret123"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["module_access"] == {"reports": False}

    # ...and it must be embedded in the JWT claims so every worker resolves it.
    import jwt
    claims = jwt.decode(body["access_token"], os.environ["JWT_SECRET_KEY"], algorithms=["HS256"])
    assert claims["module_access"] == {"reports": False}


def test_login_defaults_module_access_to_empty(monkeypatch):
    # Legacy user doc with no module_access -> login still returns {} (never
    # null, never "deny everything").
    db_user = {
        "user_id": "u-legacy",
        "username": "legacy",
        "email": "legacy@example.com",
        "full_name": "Legacy",
        "password_hash": _bcrypt_hash("Secret123"),
        "roles": ["SALES_STAFF"],
        "store_ids": ["S1"],
        "is_active": True,
    }
    client, _ = _login_client(db_user, monkeypatch)
    r = client.post("/api/v1/auth/login", json={"username": "legacy", "password": "Secret123"})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["module_access"] == {}


def test_me_returns_module_access_from_token(monkeypatch):
    # /auth/me returns the JWT payload verbatim; module_access carried in the
    # token surfaces directly.
    app = FastAPI()
    app.include_router(auth.router, prefix="/api/v1/auth")

    async def _u():
        return {"user_id": "u9", "username": "u9", "roles": ["STORE_MANAGER"],
                "store_ids": ["S1"], "active_store_id": "S1",
                "must_change_password": False, "module_access": {"finance": False}}

    app.dependency_overrides[get_current_user] = _u
    client = TestClient(app)
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 200, r.text
    assert r.json()["module_access"] == {"finance": False}
