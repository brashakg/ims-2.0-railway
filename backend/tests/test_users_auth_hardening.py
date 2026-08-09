"""
IMS 2.0 - Users / auth / settings security hardening (F10, F17, F18)
=====================================================================
Regression locks for three audit findings:

  * F10 (P2, PII IDOR) -- GET /users/{user_id} and GET /users/store/{store_id}
    were gated by ``require_manager`` only, so a STORE_MANAGER pinned to store A
    could read employees of store B, HR PII (aadhaar_no / pan_no / uan_no ...)
    included. Both now go through the canonical store-scope helpers in
    ``api/dependencies.py`` (``validate_store_access`` for the explicit store id,
    ``user_store_scope`` + ``can_access_store_scoped`` for the target record).

  * F18 (P3) -- ``auth.get_current_user`` never re-checked the account behind a
    JWT, so a deactivated employee's existing token kept working until its
    natural expiry (up to 45 min). It now rejects a token whose live record says
    is_active=False, memoising only the ALLOW verdict and failing SOFT when the
    lookup cannot be made (a DB blip must not lock every user out).

  * F17 (P3) -- POST /settings/integrations/{type}/test let ANY authenticated
    user learn which integrations are configured/enabled and the DISPATCH_MODE.
    Now ADMIN/SUPERADMIN, matching every other integration route.

Mirrors the TestClient + fake-repo harness in test_user_role_guards.py /
test_module_rbac.py (no DB required).
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import Depends, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import dependencies as api_dependencies  # noqa: E402
from api.routers import auth as auth_router  # noqa: E402
from api.routers import settings as settings_router  # noqa: E402
from api.routers import users as users_router  # noqa: E402
from api.routers.auth import create_access_token, get_current_user  # noqa: E402
from api.services.cache import cache  # noqa: E402


# ===========================================================================
# Shared fakes
# ===========================================================================


class _FakeColl:
    """Collection shim. find_one accepts the optional PROJECTION second arg the
    live-account check passes (the real pymongo signature)."""

    def __init__(self, docs, raises=False):
        self._docs = docs
        self.raises = raises
        self.calls = 0

    def find_one(self, query, projection=None):
        self.calls += 1
        if self.raises:
            raise RuntimeError("simulated mongo outage")
        for d in self._docs.values():
            if all(d.get(k) == v for k, v in query.items()):
                return dict(d)
        return None


class _FakeUserRepo:
    def __init__(self, seed=None, raises=False):
        self._docs = {}
        for doc in seed or []:
            self._docs[doc["user_id"]] = dict(doc)
        self.collection = _FakeColl(self._docs, raises=raises)

    def find_by_id(self, user_id):
        d = self._docs.get(user_id)
        return dict(d) if d else None

    def find_by_store(self, store_id):
        return [
            dict(d)
            for d in self._docs.values()
            if store_id in (d.get("store_ids") or [])
        ]

    def find_by_role(self, role, store_id=None):
        return [
            dict(d)
            for d in self._docs.values()
            if role in (d.get("roles") or [])
            and (store_id is None or store_id in (d.get("store_ids") or []))
        ]

    def update(self, user_id, update_data):
        d = self._docs.get(user_id)
        if d is None:
            return False
        d.update(update_data)
        return True

    def set_active(self, user_id, value):
        self._docs[user_id]["is_active"] = value


# ---------------------------------------------------------------------------
# F10 fixtures: two stores, one employee each, both carrying HR PII
# ---------------------------------------------------------------------------

_PII_FIELDS = ("aadhaar_no", "pan_no", "uan_no")

_STORE_A_EMPLOYEE = {
    "user_id": "emp-a",
    "username": "emp_a",
    "full_name": "Employee A",
    "roles": ["SALES_STAFF"],
    "store_ids": ["S1"],
    "primary_store_id": "S1",
    "is_active": True,
    "aadhaar_no": "111122223333",
    "pan_no": "ABCDE1234F",
    "uan_no": "100200300400",
}

_STORE_B_EMPLOYEE = {
    "user_id": "emp-b",
    "username": "emp_b",
    "full_name": "Employee B",
    "roles": ["SALES_STAFF"],
    "store_ids": ["S2"],
    "primary_store_id": "S2",
    "is_active": True,
    "aadhaar_no": "999988887777",
    "pan_no": "ZYXWV9876K",
    "uan_no": "900800700600",
}

_MANAGER_A = {
    "user_id": "mgr-a",
    "username": "mgr_a",
    "roles": ["STORE_MANAGER"],
    "store_ids": ["S1"],
    "primary_store_id": "S1",
    "active_store_id": "S1",
    "is_active": True,
    "aadhaar_no": "121212121212",
}

_ADMIN = {"user_id": "admin-1", "roles": ["ADMIN"], "store_ids": ["S1"]}
_SUPER = {"user_id": "su-1", "roles": ["SUPERADMIN"], "store_ids": []}
_AREA = {
    "user_id": "am-1",
    "roles": ["AREA_MANAGER"],
    "store_ids": ["S1", "S2"],
    "active_store_id": "S1",
}


def _users_client(actor, monkeypatch, repo=None):
    """Mini app mounting ONLY the users router, with get_current_user overridden
    to `actor` -- so these tests exercise the router's own store-scope guard."""
    repo = repo or _FakeUserRepo(
        seed=[_STORE_A_EMPLOYEE, _STORE_B_EMPLOYEE, _MANAGER_A]
    )
    monkeypatch.setattr(users_router, "get_user_repository", lambda: repo)
    app = FastAPI()
    app.include_router(users_router.router, prefix="/api/v1/users")

    async def _u():
        return dict(actor)

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app), repo


# ===========================================================================
# F10 -- cross-store employee-PII IDOR
# ===========================================================================


def test_store_manager_cannot_list_users_of_another_store(monkeypatch):
    c, _ = _users_client(_MANAGER_A, monkeypatch)
    r = c.get("/api/v1/users/store/S2")
    assert r.status_code == 403, r.text


def test_store_manager_can_still_list_users_of_own_store(monkeypatch):
    c, _ = _users_client(_MANAGER_A, monkeypatch)
    r = c.get("/api/v1/users/store/S1")
    assert r.status_code == 200, r.text
    ids = {u["user_id"] for u in r.json()}
    assert ids == {"emp-a", "mgr-a"}
    assert "emp-b" not in ids


def test_store_manager_cannot_read_user_of_another_store(monkeypatch):
    c, _ = _users_client(_MANAGER_A, monkeypatch)
    r = c.get("/api/v1/users/emp-b")
    assert r.status_code == 403, r.text
    # Belt-and-braces: the PII must not appear anywhere in the rejection body.
    for field in _PII_FIELDS:
        assert _STORE_B_EMPLOYEE[field] not in r.text


def test_store_manager_can_still_read_user_of_own_store(monkeypatch):
    c, _ = _users_client(_MANAGER_A, monkeypatch)
    r = c.get("/api/v1/users/emp-a")
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == "emp-a"


def test_store_manager_can_always_read_their_own_record(monkeypatch):
    c, _ = _users_client(_MANAGER_A, monkeypatch)
    r = c.get("/api/v1/users/mgr-a")
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == "mgr-a"


@pytest.mark.parametrize("actor", [_ADMIN, _SUPER], ids=["ADMIN", "SUPERADMIN"])
def test_hq_roles_keep_cross_store_reach(actor, monkeypatch):
    c, _ = _users_client(actor, monkeypatch)
    assert c.get("/api/v1/users/store/S2").status_code == 200
    r = c.get("/api/v1/users/emp-b")
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == "emp-b"


def test_area_manager_reaches_its_own_stores_only(monkeypatch):
    c, _ = _users_client(_AREA, monkeypatch)
    # S2 is in the area manager's store_ids -> allowed.
    assert c.get("/api/v1/users/store/S2").status_code == 200
    assert c.get("/api/v1/users/emp-b").status_code == 200
    # A store outside their list is still refused.
    assert c.get("/api/v1/users/store/S9").status_code == 403


def test_user_with_no_store_is_hq_only(monkeypatch):
    orphan = {
        "user_id": "orphan-1",
        "username": "orphan",
        "roles": ["ACCOUNTANT"],
        "store_ids": [],
        "is_active": True,
        "pan_no": "QQQQQ1111Q",
    }
    repo = _FakeUserRepo(seed=[_MANAGER_A, orphan])
    c, _ = _users_client(_MANAGER_A, monkeypatch, repo=repo)
    assert c.get("/api/v1/users/orphan-1").status_code == 403
    c2, _ = _users_client(_ADMIN, monkeypatch, repo=repo)
    assert c2.get("/api/v1/users/orphan-1").status_code == 200


def test_scope_guard_reuses_the_canonical_dependency_helpers():
    """The fix must not invent a second store-scope rule -- it delegates to the
    same helpers every other store-scoped router uses."""
    assert users_router.validate_store_access is api_dependencies.validate_store_access
    assert users_router.user_store_scope is api_dependencies.user_store_scope
    assert (
        users_router.can_access_store_scoped
        is api_dependencies.can_access_store_scoped
    )


# ===========================================================================
# F18 -- a deactivated user's existing token must stop working
# ===========================================================================


def _auth_probe_client(repo, monkeypatch):
    """Mini app whose one route uses the REAL get_current_user."""
    monkeypatch.setattr(api_dependencies, "get_user_repository", lambda: repo)
    app = FastAPI()

    @app.get("/probe")
    async def _probe(current_user: dict = Depends(get_current_user)):
        return {"user_id": current_user.get("user_id")}

    return TestClient(app)


def _headers(user_id, roles=None):
    token = create_access_token(
        {
            "user_id": user_id,
            "username": user_id,
            "roles": roles or ["SALES_STAFF"],
            "store_ids": ["S1"],
            "active_store_id": "S1",
        }
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _clear_user_status_cache():
    """The live-account check memoises its ALLOW verdict in the process-global
    shared cache; clear the ids these tests use so they are order-independent."""
    ids = ("live-1", "sacked-1", "ghost-1", "blip-1", "emp-a", "mgr-a")
    for uid in ids:
        auth_router.invalidate_user_status(uid)
    yield
    for uid in ids:
        auth_router.invalidate_user_status(uid)


def test_active_user_token_still_works(monkeypatch):
    repo = _FakeUserRepo(seed=[{"user_id": "live-1", "is_active": True}])
    c = _auth_probe_client(repo, monkeypatch)
    r = c.get("/probe", headers=_headers("live-1"))
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == "live-1"


def test_deactivated_user_existing_token_is_rejected(monkeypatch):
    repo = _FakeUserRepo(seed=[{"user_id": "sacked-1", "is_active": False}])
    c = _auth_probe_client(repo, monkeypatch)
    r = c.get("/probe", headers=_headers("sacked-1"))
    assert r.status_code == 401, r.text
    assert "disabled" in r.json()["detail"].lower()


def test_deny_verdict_is_not_cached_so_reactivation_takes_effect(monkeypatch):
    repo = _FakeUserRepo(seed=[{"user_id": "sacked-1", "is_active": False}])
    c = _auth_probe_client(repo, monkeypatch)
    assert c.get("/probe", headers=_headers("sacked-1")).status_code == 401
    repo.set_active("sacked-1", True)
    # No invalidation call needed: the DENY verdict is never cached, so a
    # re-enabled account is never locked out by a stale entry.
    assert c.get("/probe", headers=_headers("sacked-1")).status_code == 200


def test_allow_verdict_is_memoised_and_invalidation_takes_effect(monkeypatch):
    """Performance contract: the ALLOW verdict costs ONE lookup, not one per
    request -- and users.py's invalidation hook makes a deactivation land on the
    very next request rather than after the TTL."""
    repo = _FakeUserRepo(seed=[{"user_id": "live-1", "is_active": True}])
    c = _auth_probe_client(repo, monkeypatch)
    assert c.get("/probe", headers=_headers("live-1")).status_code == 200
    lookups_after_first = repo.collection.calls
    assert lookups_after_first == 1
    assert c.get("/probe", headers=_headers("live-1")).status_code == 200
    assert repo.collection.calls == lookups_after_first  # served from cache

    repo.set_active("live-1", False)
    auth_router.invalidate_user_status("live-1")
    assert c.get("/probe", headers=_headers("live-1")).status_code == 401


def test_unknown_user_id_is_allowed_through_soft(monkeypatch):
    """Documented fail-soft: deactivation is a SOFT delete, so an ABSENT record
    means a token that was never a users row (tooling/tests), not a revocation."""
    repo = _FakeUserRepo(seed=[])
    c = _auth_probe_client(repo, monkeypatch)
    assert c.get("/probe", headers=_headers("ghost-1")).status_code == 200


def test_db_outage_does_not_lock_everyone_out(monkeypatch):
    """A Mongo blip must NOT turn this additive guard into a total lockout."""
    repo = _FakeUserRepo(seed=[{"user_id": "blip-1", "is_active": True}], raises=True)
    c = _auth_probe_client(repo, monkeypatch)
    assert c.get("/probe", headers=_headers("blip-1")).status_code == 200
    # ... and the failed lookup was NOT cached as an allow.
    assert cache.get(auth_router._user_status_cache_key("blip-1")) is None


def test_no_repository_is_allowed_through(monkeypatch):
    monkeypatch.setattr(api_dependencies, "get_user_repository", lambda: None)
    app = FastAPI()

    @app.get("/probe")
    async def _probe(current_user: dict = Depends(get_current_user)):
        return {"ok": True}

    c = TestClient(app)
    assert c.get("/probe", headers=_headers("ghost-1")).status_code == 200


def test_delete_user_invalidates_the_cached_allow_verdict(monkeypatch):
    """The users router must drop the memoised entry when it deactivates."""
    repo = _FakeUserRepo(
        seed=[
            dict(_STORE_A_EMPLOYEE),
            {"user_id": "admin-2", "roles": ["ADMIN"], "is_active": True},
        ]
    )
    c, _ = _users_client(
        {"user_id": "admin-9", "roles": ["ADMIN"], "store_ids": ["S1"]},
        monkeypatch,
        repo=repo,
    )
    key = auth_router._user_status_cache_key("emp-a")
    cache.set(key, 1, ttl=60)
    assert cache.get(key) is not None
    r = c.delete("/api/v1/users/emp-a")
    assert r.status_code == 200, r.text
    assert cache.get(key) is None


def test_update_user_toggling_is_active_invalidates(monkeypatch):
    repo = _FakeUserRepo(
        seed=[
            dict(_STORE_A_EMPLOYEE),
            {"user_id": "admin-2", "roles": ["ADMIN"], "is_active": True},
        ]
    )
    c, _ = _users_client(
        {"user_id": "admin-9", "roles": ["ADMIN"], "store_ids": ["S1"]},
        monkeypatch,
        repo=repo,
    )
    key = auth_router._user_status_cache_key("emp-a")
    cache.set(key, 1, ttl=60)
    r = c.put("/api/v1/users/emp-a", json={"is_active": False})
    assert r.status_code == 200, r.text
    assert cache.get(key) is None


# ===========================================================================
# F17 -- integration status probe must be ADMIN-gated
# ===========================================================================


def _settings_client(actor):
    app = FastAPI()
    app.include_router(settings_router.router, prefix="/api/v1/settings")

    async def _u():
        return dict(actor)

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


@pytest.mark.parametrize(
    "role", ["STORE_MANAGER", "SALES_STAFF", "OPTOMETRIST", "AREA_MANAGER", "ACCOUNTANT"]
)
def test_non_admin_cannot_probe_integration_status(role):
    c = _settings_client({"user_id": "u1", "roles": [role], "store_ids": ["S1"]})
    r = c.post("/api/v1/settings/integrations/whatsapp/test")
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("role", ["ADMIN", "SUPERADMIN"])
def test_admin_can_still_probe_integration_status(role):
    c = _settings_client({"user_id": "u1", "roles": [role], "store_ids": ["S1"]})
    r = c.post("/api/v1/settings/integrations/whatsapp/test")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["integration"] == "whatsapp"
    # Presence/mode only -- never a credential value.
    assert set(body) == {
        "status",
        "integration",
        "enabled",
        "dispatch_mode",
        "live",
        "message",
    }
    assert isinstance(body["enabled"], bool)
    assert isinstance(body["live"], bool)


def test_integration_probe_matches_the_other_integration_gates():
    """The probe's gate must be the same one the sibling integration routes use
    (require_roles("ADMIN")), not a bespoke check."""
    import inspect

    src = inspect.getsource(settings_router.test_integration)
    assert 'require_roles("ADMIN")' in src
