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
from api.routers import jarvis as jarvis_router  # noqa: E402
from api.routers import settings as settings_router  # noqa: E402
from api.routers import stores as stores_router  # noqa: E402
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
        flt = {"roles": role, "is_active": True}
        if store_id:
            flt["store_ids"] = store_id
        return [dict(d) for d in self._docs.values() if self._matches(d, flt)]

    def update(self, user_id, update_data):
        d = self._docs.get(user_id)
        if d is None:
            return False
        d.update(update_data)
        return True

    def set_active(self, user_id, value):
        self._docs[user_id]["is_active"] = value

    # NOTE: find_by_role / search_users / get_user_summary mirror the REAL
    # UserRepository exactly -- they take whatever the router passes as
    # `store_id` and drop it straight into a Mongo `store_ids` filter under an
    # `if store_id:` truthiness test. That is what makes a `{"$in": [...]}`
    # clause work end-to-end (and what makes `{"$in": []}` match NOTHING rather
    # than being treated as "no filter"), so these tests exercise the real
    # semantics instead of a friendlier fake.
    def search_users(self, q, store_id=None):
        ql = (q or "").lower()
        extra = {"store_ids": store_id} if store_id else {}
        return [
            dict(d)
            for d in self._docs.values()
            if any(
                ql in str(d.get(f, "")).lower()
                for f in ("full_name", "username", "email")
            )
            and self._matches(d, extra)
        ]

    def get_user_summary(self, store_id=None):
        flt = {"store_ids": store_id} if store_id else {}
        summary = {}
        for d in self._docs.values():
            if not self._matches(d, flt):
                continue
            for role in d.get("roles") or []:
                row = summary.setdefault(role, {"total": 0, "active": 0})
                row["total"] += 1
                if d.get("is_active"):
                    row["active"] += 1
        return summary

    # -- Mongo-ish matcher, enough for the queries the two routers actually
    # -- issue: scalar equality with array containment, $or, $ne and $in.
    @staticmethod
    def _match_value(doc_value, expected):
        if isinstance(expected, dict):
            if "$ne" in expected:
                return doc_value != expected["$ne"]
            if "$in" in expected:
                if isinstance(doc_value, list):
                    return any(v in expected["$in"] for v in doc_value)
                return doc_value in expected["$in"]
            return doc_value == expected
        if isinstance(doc_value, list):
            return expected in doc_value
        return doc_value == expected

    def _matches(self, doc, query):
        for key, expected in (query or {}).items():
            if key == "$or":
                if not any(self._matches(doc, sub) for sub in expected):
                    return False
                continue
            if not self._match_value(doc.get(key), expected):
                return False
        return True

    def find_many(self, query=None, skip=0, limit=100):
        out = [dict(d) for d in self._docs.values() if self._matches(d, query or {})]
        return out[skip : skip + limit]


# ---------------------------------------------------------------------------
# F10 fixtures: two stores, one employee each, both carrying HR PII
# ---------------------------------------------------------------------------

_PII_FIELDS = ("aadhaar_no", "pan_no", "uan_no")

# The maker-checker PIN material services/approvals.py writes ONTO the user
# document. It must never appear in any API response (P0).
_CREDENTIAL_FIELDS = (
    "password",
    "password_hash",
    "approval_pin_hash",
    "approval_pin_set_at",
    "pin_attempts",
)

_PIN_MATERIAL = {
    "password_hash": "$2b$12$notarealhashvaluefortestsonly000000000000000000000",
    "approval_pin_hash": "$2b$12$pinhashplaceholderforregressiontestonly0000000000",
    "approval_pin_set_at": "2026-08-01T00:00:00",
    "pin_attempts": {"count": 0, "window_start": "2026-08-01T00:00:00"},
}

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
    "pf_no": "PF0001",
    "esic_no": "ESIC0001",
    "bank_account_no": "50100123456789",
    **_PIN_MATERIAL,
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
    "full_name": "Manager A",
    "roles": ["STORE_MANAGER"],
    "store_ids": ["S1"],
    "primary_store_id": "S1",
    "active_store_id": "S1",
    "is_active": True,
    "aadhaar_no": "121212121212",
    # The manager is the interesting victim: their approval PIN authorises
    # discount overrides / JEs / petty cash, and the store roster below is
    # readable by every cashier at the store.
    **_PIN_MATERIAL,
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


# ===========================================================================
# P0 -- approval-PIN credential must never leave the API on a user document
# ===========================================================================
# services/approvals.py:233 writes `approval_pin_hash` (bcrypt of a 4-6 DIGIT
# maker-checker PIN) onto the users document, so it rode along in every raw
# user payload. A 10^4-10^6 keyspace is offline-crackable in seconds and the
# recovered PIN authorises discount overrides, journal entries, petty cash and
# vendor RMA. Two doors served it: the users router (sanitize_user) and the
# stores router's staff roster (its own pop-list) -- which is the one POS
# actually calls, and which ANY authenticated colleague at the store may read.


def _assert_no_credentials(payload_text, doc=None):
    """No credential field NAME and no PIN/password VALUE anywhere in a body."""
    for field in _CREDENTIAL_FIELDS:
        assert field not in payload_text, f"{field} leaked in response"
    for value in (doc or _PIN_MATERIAL).values():
        if isinstance(value, str) and value:
            assert value not in payload_text, "credential value leaked in response"


def test_sanitize_user_strips_the_approval_pin_material():
    """Unit-level lock on the helper the panel executed to prove the P0."""
    out = users_router.sanitize_user(
        {
            "user_id": "u1",
            "username": "u1",
            "aadhaar_no": "111122223333",
            **_PIN_MATERIAL,
        }
    )
    for field in _CREDENTIAL_FIELDS:
        assert field not in out, f"{field} survived sanitize_user"
    # Non-credential fields are untouched on this router.
    assert out["user_id"] == "u1"
    assert out["aadhaar_no"] == "111122223333"


def test_picker_user_is_an_allow_list_not_a_deny_list():
    """A deny-list makes every FUTURE field exposed by default -- which is how
    approval_pin_hash got out. picker_user must return ONLY the allow-list, so
    a field nobody has thought of yet is hidden by construction."""
    out = users_router.picker_user(
        {
            "user_id": "u1",
            "username": "u1",
            "full_name": "U One",
            "roles": ["SALES_STAFF"],
            "is_active": True,
            # everything below must NOT come out
            "aadhaar_no": "111122223333",
            "pan_no": "ABCDE1234F",
            "uan_no": "1",
            "pf_no": "2",
            "esic_no": "3",
            "bank_account_no": "4",
            "email": "u1@example.com",
            "phone": "9876543210",
            "discount_cap": 25.0,
            "must_change_password": True,
            "permissions": {"grant": {"orders:write": True}},
            "module_access": {"finance": False},
            "store_ids": ["S1"],
            "home_store_id": "S1",
            "a_field_invented_next_year": "leak me",
            **_PIN_MATERIAL,
        }
    )
    assert set(out) == {"user_id", "username", "full_name", "roles", "is_active"}
    assert out["full_name"] == "U One"
    assert out["roles"] == ["SALES_STAFF"]


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/users/mgr-a",
        "/api/v1/users/store/S1",
        "/api/v1/users",
        "/api/v1/users/search?q=mgr",
        "/api/v1/users/role/STORE_MANAGER",
    ],
)
def test_no_users_route_returns_the_approval_pin_hash(path, monkeypatch):
    """Every users-router read path that can surface a user document."""
    c, _ = _users_client(_ADMIN, monkeypatch)
    r = c.get(path)
    assert r.status_code == 200, r.text
    _assert_no_credentials(r.text)


def test_users_router_still_returns_the_fields_it_is_supposed_to(monkeypatch):
    """Guard against over-stripping: the P0 fix must not blank the record."""
    c, _ = _users_client(_ADMIN, monkeypatch)
    body = c.get("/api/v1/users/emp-a").json()
    assert body["user_id"] == "emp-a"
    assert body["full_name"] == "Employee A"
    assert body["roles"] == ["SALES_STAFF"]
    # Govt IDs are NOT stripped on this router (require_manager + F10 scope);
    # raising them to an HR/ADMIN bar is a tracked follow-up, not this fix.
    assert body["aadhaar_no"] == "111122223333"


# ---------------------------------------------------------------------------
# The door POS actually calls: GET /stores/{store_id}/users
# ---------------------------------------------------------------------------

_GOVT_ID_FIELD_NAMES = (
    "aadhaar_no",
    "pan_no",
    "uan_no",
    "pf_no",
    "esic_no",
    "bank_account_no",
)

_CASHIER_AT_S1 = {
    "user_id": "cash-1",
    "roles": ["SALES_CASHIER"],
    "store_ids": ["S1"],
    "active_store_id": "S1",
}


def _stores_client(actor, monkeypatch, repo=None):
    repo = repo or _FakeUserRepo(
        seed=[_STORE_A_EMPLOYEE, _STORE_B_EMPLOYEE, _MANAGER_A]
    )
    monkeypatch.setattr(stores_router, "get_user_repository", lambda: repo)
    app = FastAPI()
    app.include_router(stores_router.router, prefix="/api/v1/stores")

    async def _u():
        return dict(actor)

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app), repo


def test_store_roster_does_not_leak_the_managers_approval_pin_to_a_cashier(
    monkeypatch,
):
    """The exact attack the panel reproduced: a SALES_CASHIER at S1 reads the
    roster and used to receive the STORE_MANAGER's bcrypt approval-PIN hash."""
    c, _ = _stores_client(_CASHIER_AT_S1, monkeypatch)
    r = c.get("/api/v1/stores/S1/users")
    assert r.status_code == 200, r.text
    _assert_no_credentials(r.text)


def test_store_roster_does_not_leak_statutory_pii_to_a_cashier(monkeypatch):
    c, _ = _stores_client(_CASHIER_AT_S1, monkeypatch)
    r = c.get("/api/v1/stores/S1/users")
    assert r.status_code == 200, r.text
    for field in _GOVT_ID_FIELD_NAMES:
        assert field not in r.text, f"{field} leaked from the store roster"
    for value in ("111122223333", "ABCDE1234F", "121212121212", "50100123456789"):
        assert value not in r.text, "a statutory ID value leaked"


def test_store_roster_still_feeds_the_pos_picker_and_the_two_modals(monkeypatch):
    """POSLayout.tsx:1491, NewTaskModal.tsx:143 and WalkoutIntakeModal.tsx:121
    map over user_id / id / username / name / full_name / roles. All must
    survive the strip or the salesperson picker goes blank mid-shift."""
    c, _ = _stores_client(_CASHIER_AT_S1, monkeypatch)
    body = c.get("/api/v1/stores/S1/users").json()
    rows = {u["user_id"]: u for u in body["users"]}
    assert set(rows) == {"emp-a", "mgr-a"}
    assert body["total"] == 2
    for row in rows.values():
        assert row.get("user_id")
        assert row.get("full_name")
        assert isinstance(row.get("roles"), list) and row["roles"]
        assert row.get("username")
        assert row.get("is_active") is True


def test_store_roster_returns_only_the_allow_listed_keys(monkeypatch):
    """EVERY row, not just the first -- and nothing beyond the picker fields.
    Locks out must_change_password (who is on the temporary password) and
    discount_cap / permissions / module_access (whose override to target)."""
    c, _ = _stores_client(_CASHIER_AT_S1, monkeypatch)
    rows = c.get("/api/v1/stores/S1/users").json()["users"]
    assert len(rows) == 2
    for row in rows:
        assert set(row) == {
            "user_id",
            "username",
            "full_name",
            "roles",
            "is_active",
        }, f"unexpected keys on the picker row: {sorted(row)}"


def test_store_roster_role_filter_and_active_flag_still_work(monkeypatch):
    """The strip must not disturb the query behaviour the pickers depend on."""
    c, _ = _stores_client(_CASHIER_AT_S1, monkeypatch)
    body = c.get("/api/v1/stores/S1/users?roles=STORE_MANAGER").json()
    assert [u["user_id"] for u in body["users"]] == ["mgr-a"]


def test_store_roster_and_users_router_share_one_projection():
    """Two independently-maintained field lists are exactly how this P0 stayed
    open on both routers at once -- lock the single definition in place."""
    assert stores_router.picker_user is users_router.picker_user


# ===========================================================================
# A store-scoped manager with an EMPTY reach must see nothing, not everything
# ===========================================================================
# resolve_store_scope resolved to active_store_id, and the list routes applied
# the filter only `if store_id:` -- so a falsy scope meant NO FILTER and the
# whole org's roster came back with every statutory ID on it. Reachability is
# ordinary: UserCreate.store_ids defaults to [], primary_store_id falls back to
# None, and _default_active_store returns None for any role that is not
# SUPERADMIN/ADMIN/AREA_MANAGER, so login cannot repair it.

_STORELESS_MANAGER = {
    "user_id": "mgr-none",
    "roles": ["STORE_MANAGER"],
    "store_ids": [],
    "active_store_id": None,
}

_FAR_STORE_SUPERADMIN = {
    "user_id": "su-s9",
    "username": "su_s9",
    "full_name": "Superadmin S9",
    "roles": ["SUPERADMIN"],
    "store_ids": ["S9"],
    "primary_store_id": "S9",
    "is_active": True,
    "aadhaar_no": "555566667777",
    "pan_no": "QWERT5555Y",
    "uan_no": "555000111222",
    "pf_no": "PF9999",
    "esic_no": "ESIC9999",
    "bank_account_no": "50100999888777",
    **_PIN_MATERIAL,
}

_ALL_GOVT_ID_VALUES = (
    "111122223333",
    "ABCDE1234F",
    "555566667777",
    "QWERT5555Y",
    "50100999888777",
    "121212121212",
)


def _storeless_client(monkeypatch):
    repo = _FakeUserRepo(seed=[_STORE_A_EMPLOYEE, _FAR_STORE_SUPERADMIN, _MANAGER_A])
    return _users_client(_STORELESS_MANAGER, monkeypatch, repo=repo)


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/users",
        "/api/v1/users/search?q=em",
        "/api/v1/users/role/SUPERADMIN",
        "/api/v1/users/summary",
    ],
)
def test_storeless_manager_gets_no_rows_from_the_list_routes(path, monkeypatch):
    c, _ = _storeless_client(monkeypatch)
    r = c.get(path)
    # Empty result, NOT a 403 -- a misconfigured account must not be locked out.
    assert r.status_code == 200, r.text
    body = r.json()
    rows = body.get("users") if isinstance(body, dict) else body
    if isinstance(body, dict) and "summary" in body:
        assert body["summary"] == {}
    else:
        assert rows == [], f"{path} returned rows to a store-less manager"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/users",
        "/api/v1/users/search?q=em",
        "/api/v1/users/role/SUPERADMIN",
    ],
)
def test_storeless_manager_never_sees_a_statutory_id(path, monkeypatch):
    """The asset itself, not just the row count."""
    c, _ = _storeless_client(monkeypatch)
    r = c.get(path)
    assert r.status_code == 200, r.text
    for field in _GOVT_ID_FIELD_NAMES:
        assert field not in r.text, f"{field} leaked to a store-less manager"
    for value in _ALL_GOVT_ID_VALUES:
        assert value not in r.text, "a statutory ID value leaked"
    _assert_no_credentials(r.text)


def test_scoped_manager_still_sees_only_their_own_store(monkeypatch):
    repo = _FakeUserRepo(seed=[_STORE_A_EMPLOYEE, _FAR_STORE_SUPERADMIN, _MANAGER_A])
    c, _ = _users_client(_MANAGER_A, monkeypatch, repo=repo)
    ids = {u["user_id"] for u in c.get("/api/v1/users").json()}
    assert ids == {"emp-a", "mgr-a"}
    assert "su-s9" not in ids


def test_area_manager_sees_all_of_their_stores_not_just_the_active_one(
    monkeypatch,
):
    """BONUS regression: the old active_store_id resolution silently narrowed a
    multi-store AREA_MANAGER to ONE store."""
    repo = _FakeUserRepo(seed=[_STORE_A_EMPLOYEE, _STORE_B_EMPLOYEE, _MANAGER_A])
    c, _ = _users_client(_AREA, monkeypatch, repo=repo)  # stores S1+S2, active S1
    ids = {u["user_id"] for u in c.get("/api/v1/users").json()}
    assert ids == {"emp-a", "emp-b", "mgr-a"}


@pytest.mark.parametrize("actor", [_ADMIN, _SUPER], ids=["ADMIN", "SUPERADMIN"])
def test_hq_roles_keep_org_wide_list_reach(actor, monkeypatch):
    repo = _FakeUserRepo(seed=[_STORE_A_EMPLOYEE, _FAR_STORE_SUPERADMIN, _MANAGER_A])
    c, _ = _users_client(actor, monkeypatch, repo=repo)
    ids = {u["user_id"] for u in c.get("/api/v1/users").json()}
    assert ids == {"emp-a", "su-s9", "mgr-a"}


def test_explicit_store_id_is_still_authorised_on_the_list_route(monkeypatch):
    """The explicit-?store_id path must keep its 403, not fall back to scope."""
    c, _ = _users_client(_MANAGER_A, monkeypatch)
    assert c.get("/api/v1/users?store_id=S2").status_code == 403
    assert c.get("/api/v1/users?store_id=S1").status_code == 200


def test_store_scope_filter_reuses_the_canonical_helper():
    """Empty reach -> a filter that matches nothing, never 'no filter'."""
    assert users_router._store_scope_filter(_SUPER) is None
    assert users_router._store_scope_filter(_ADMIN) is None
    assert users_router._store_scope_filter(_STORELESS_MANAGER) == {"$in": []}
    assert users_router._store_scope_filter(_AREA) == {"$in": ["S1", "S2"]}


# ===========================================================================
# Third door: the Jarvis raw-collection browser
# ===========================================================================
# GET /api/v1/jarvis/data/users read the `users` collection with only {"_id": 0},
# so it returned approval_pin_hash / password_hash / statutory IDs for the whole
# org. SUPERADMIN-only, but a SUPERADMIN can be the MAKER in maker-checker, so
# they must never hold every manager's CHECKER credential.


def test_jarvis_users_collection_excludes_credentials_and_statutory_ids():
    excluded = jarvis_router._COLLECTION_FIELD_EXCLUSIONS["users"]
    for field in _CREDENTIAL_FIELDS + _GOVT_ID_FIELD_NAMES:
        assert field in excluded, f"{field} is still readable via jarvis/data"


def test_jarvis_builds_a_zeroed_projection_for_the_users_collection():
    """The exclusion list must actually reach pymongo as a projection."""
    projection = {"_id": 0}
    for excluded in jarvis_router._COLLECTION_FIELD_EXCLUSIONS.get("users", ()):
        projection[excluded] = 0
    assert projection["_id"] == 0
    assert projection["approval_pin_hash"] == 0
    assert projection["aadhaar_no"] == 0
    # A collection with no exclusions keeps the original shape.
    other = {"_id": 0}
    for excluded in jarvis_router._COLLECTION_FIELD_EXCLUSIONS.get("orders", ()):
        other[excluded] = 0
    assert other == {"_id": 0}


def test_jarvis_users_is_still_queryable():
    """The fix must be a projection, not a removal -- the browser still works."""
    assert "users" in jarvis_router._JARVIS_QUERYABLE_COLLECTIONS
