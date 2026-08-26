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
    """Unit-level lock on the helper the panel executed to prove the P0.

    UPDATED 2026-08-13 (owner ruling): sanitize_user is now an ALLOW-list and
    takes the VIEWER. The statutory IDs are ADMIN / SUPERADMIN only, so this
    test asserts the admin tier -- the manager tier is asserted below.
    """
    doc = {
        "user_id": "u1",
        "username": "u1",
        "aadhaar_no": "111122223333",
        **_PIN_MATERIAL,
    }
    out = users_router.sanitize_user(dict(doc), _ADMIN)
    for field in _CREDENTIAL_FIELDS:
        assert field not in out, f"{field} survived sanitize_user"
    assert out["user_id"] == "u1"
    # An ADMIN still gets the statutory IDs: HR has to type PAN / UAN / ESIC in
    # before payroll can run, so dropping them for everybody would break the
    # screen that captures them.
    assert out["aadhaar_no"] == "111122223333"


def test_sanitize_user_withholds_statutory_ids_from_the_manager_tier():
    """The other half of the same rule (owner ruling 2026-08-13). Full endpoint
    coverage for all five routes is in tests/test_salary_aggregate_leak.py."""
    doc = {
        "user_id": "u1",
        "username": "u1",
        "aadhaar_no": "111122223333",
        "pan_no": "ABCDE1234F",
        **_PIN_MATERIAL,
    }
    for actor in (_MANAGER_A, _AREA, None):
        out = users_router.sanitize_user(dict(doc), actor)
        assert out["user_id"] == "u1", "over-stripped: the record lost its id"
        assert "aadhaar_no" not in out
        assert "pan_no" not in out


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
    excluded = jarvis_router._excluded_fields_for("users")
    for field in _CREDENTIAL_FIELDS + _GOVT_ID_FIELD_NAMES:
        assert field in excluded, f"{field} is still readable via jarvis/data"


@pytest.mark.parametrize("collection", ["salary_config", "payslips"])
def test_jarvis_payroll_collections_exclude_statutory_ids(collection):
    """The IDs must not simply move one collection to the left -- these are on
    the same queryable allow-list and carry pan / uan / esi / bank fields."""
    assert collection in jarvis_router._JARVIS_QUERYABLE_COLLECTIONS
    excluded = jarvis_router._excluded_fields_for(collection)
    for field in (
        "pan",
        "uan",
        "esi_ip_number",
        "bank_account_no",
        "bank_account",
        "bank_ifsc",
    ):
        assert field in excluded, f"{field} readable on {collection}"


def test_jarvis_exclusions_are_global_so_a_new_collection_cannot_default_open():
    """Fail-safe by construction: the rule is by FIELD NAME across every
    collection, so allow-listing a new collection tomorrow cannot expose them."""
    for collection in ("orders", "a_collection_added_next_year"):
        excluded = jarvis_router._excluded_fields_for(collection)
        assert "approval_pin_hash" in excluded
        assert "bank_account_no" in excluded


def test_jarvis_builds_a_zeroed_projection():
    """The exclusion list must actually reach pymongo as a projection."""
    projection = {"_id": 0}
    for excluded in jarvis_router._excluded_fields_for("users"):
        projection[excluded] = 0
    assert projection["_id"] == 0
    assert projection["approval_pin_hash"] == 0
    assert projection["aadhaar_no"] == 0
    # All-exclusion projections are legal Mongo (no inclusion/exclusion mixing).
    assert set(projection.values()) == {0}


def test_jarvis_users_is_still_queryable():
    """The fix must be a projection, not a removal -- the browser still works."""
    assert "users" in jarvis_router._JARVIS_QUERYABLE_COLLECTIONS


# ---------------------------------------------------------------------------
# ... and the projection must not be invertible through the filter/sort inputs
# ---------------------------------------------------------------------------
# `total` is a count over the caller's filter and is computed BEFORE the
# projection, so a $regex prefix walk on an excluded field reads the hidden
# bytes back one character at a time. A field we refuse to return must be a
# field we refuse to be interrogated about.


def _jarvis_client(actor):
    app = FastAPI()
    app.include_router(jarvis_router.router, prefix="/api/v1/jarvis")

    async def _u():
        return dict(actor)

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


_JARVIS_SUPERADMIN = {
    "user_id": "su-j",
    "username": "su_j",
    "roles": ["SUPERADMIN"],
    "store_ids": ["S1"],
    "active_store_id": "S1",
}


@pytest.mark.parametrize(
    "field", ["approval_pin_hash", "aadhaar_no", "password_hash", "bank_account_no"]
)
def test_jarvis_rejects_a_filter_on_an_excluded_field(field):
    c = _jarvis_client(_JARVIS_SUPERADMIN)
    r = c.get(f"/api/v1/jarvis/data/users?filter_field={field}&filter_value=x")
    assert r.status_code == 400, r.text
    assert "not queryable" in r.text


@pytest.mark.parametrize("field", ["approval_pin_hash", "pan"])
def test_jarvis_rejects_a_sort_on_an_excluded_field(field):
    c = _jarvis_client(_JARVIS_SUPERADMIN)
    r = c.get(f"/api/v1/jarvis/data/users?sort_by={field}")
    assert r.status_code == 400, r.text


def test_jarvis_rejects_a_dotted_path_into_an_excluded_field():
    c = _jarvis_client(_JARVIS_SUPERADMIN)
    r = c.get(
        "/api/v1/jarvis/data/users?filter_field=pin_attempts.count&filter_value=1"
    )
    assert r.status_code == 400, r.text


def test_jarvis_rejects_an_operator_shaped_field_name():
    c = _jarvis_client(_JARVIS_SUPERADMIN)
    r = c.get("/api/v1/jarvis/data/users?filter_field=$where&filter_value=1")
    assert r.status_code == 400, r.text


@pytest.mark.parametrize(
    "value",
    [
        '{"$regex": "^abc"}',
        '{"$ne": null}',
        '{"$where": "1"}',
        '[1, 2]',
    ],
)
def test_jarvis_rejects_an_operator_dict_as_a_filter_value(value):
    """_coerce_mongo_value is a bare json.loads, so an operator would land LIVE
    -- rebuilding the count oracle and opening $where/$expr/$function."""
    c = _jarvis_client(_JARVIS_SUPERADMIN)
    r = c.get(
        "/api/v1/jarvis/data/users",
        params={"filter_field": "username", "filter_value": value},
    )
    assert r.status_code == 400, r.text
    assert "scalar" in r.text


def test_jarvis_still_allows_an_ordinary_scalar_filter():
    """The guard must not break the browser's legitimate use."""
    c = _jarvis_client(_JARVIS_SUPERADMIN)
    r = c.get("/api/v1/jarvis/data/users?filter_field=username&filter_value=admin")
    assert r.status_code == 200, r.text
    assert r.json()["collection"] == "users"


@pytest.mark.parametrize(
    "query",
    [
        "filter_field=approval_pin_hash&filter_value=x",
        "sort_by=aadhaar_no",
        "filter_field=username&filter_value=%7B%22%24regex%22%3A%22a%22%7D",
    ],
)
def test_jarvis_rejects_before_it_touches_the_database(query, monkeypatch):
    """ORDERING LOCK. The validation must run BEFORE the collection handle is
    resolved, so a refused query is refused identically whether or not Mongo is
    reachable. This is not hypothetical: with the check placed after the
    `col is None` early-return, a DB-less process answered 200 to exactly these
    queries -- caught only because the full-suite run had a different connection
    state than the single-file run."""
    monkeypatch.setattr(jarvis_router, "get_db_collection", lambda _c: None)
    c = _jarvis_client(_JARVIS_SUPERADMIN)
    r = c.get(f"/api/v1/jarvis/data/users?{query}")
    assert r.status_code == 400, r.text


# ===========================================================================
# Fourth door: the shared GridFS bucket
# ===========================================================================
# ONE bucket holds product images, company logos, GRN attachments, expense
# bills, task attachments AND employee Aadhaar/PAN/UAN/ESIC scans, so a file_id
# is a bearer capability over the whole bucket. GET /settings/business/logo/
# {file_id} took the id straight from the URL and called fs.get(file_id) with no
# require_kind, so any authenticated user holding an hr-uploaded file_id could
# stream a colleague's statutory-ID scan -- bypassing hr.py's ADMIN-only gate.


def _logo_client(store, actor=None):
    monkey_actor = actor or {
        "user_id": "cash-1",
        "roles": ["SALES_CASHIER"],
        "store_ids": ["S1"],
        "active_store_id": "S1",
    }
    app = FastAPI()
    app.include_router(settings_router.router, prefix="/api/v1/settings")

    async def _u():
        return dict(monkey_actor)

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


def test_employee_id_scan_file_id_404s_from_the_logo_route(monkeypatch):
    """The exact P0: an hr-uploaded employee document must NOT stream here."""
    from api.services import file_store as fs_module

    store = fs_module.InMemoryFileStore()
    # hr.py stamps employee_id/doc_type and NO kind (hr.py's document upload).
    hr_file_id = store.put(
        content=b"AADHAAR-SCAN-BYTES",
        filename="aadhaar.jpg",
        mime_type="image/jpeg",
        metadata={"employee_id": "emp-a", "doc_type": "AADHAAR"},
    )
    monkeypatch.setattr(fs_module, "get_file_store", lambda: store)

    c = _logo_client(store)
    r = c.get(f"/api/v1/settings/business/logo/{hr_file_id}")
    assert r.status_code == 404, r.text
    assert b"AADHAAR-SCAN-BYTES" not in r.content


def test_a_real_logo_still_streams_from_the_logo_route(monkeypatch):
    """Guard against over-tightening: the logo must still render."""
    from api.services import file_store as fs_module

    store = fs_module.InMemoryFileStore()
    logo_id = store.put(
        content=b"LOGO-BYTES",
        filename="logo.png",
        mime_type="image/png",
        metadata={"kind": "business_logo", "uploaded_by": "admin-1"},
    )
    monkeypatch.setattr(fs_module, "get_file_store", lambda: store)

    c = _logo_client(store)
    r = c.get(f"/api/v1/settings/business/logo/{logo_id}")
    assert r.status_code == 200, r.text
    assert r.content == b"LOGO-BYTES"


@pytest.mark.parametrize(
    "metadata",
    [
        {"kind": "product_image"},
        {"kind": "grn_attachment"},
        {"kind": "expense_bill"},
        {},
    ],
)
def test_no_other_kind_streams_from_the_logo_route(metadata, monkeypatch):
    from api.services import file_store as fs_module

    store = fs_module.InMemoryFileStore()
    fid = store.put(
        content=b"OTHER-KIND",
        filename="x.bin",
        mime_type="application/octet-stream",
        metadata=metadata,
    )
    monkeypatch.setattr(fs_module, "get_file_store", lambda: store)
    c = _logo_client(store)
    assert c.get(f"/api/v1/settings/business/logo/{fid}").status_code == 404


def test_any_kind_sentinel_is_an_explicit_opt_out():
    """The deliberate-unscoped path stays available for serves whose file_id
    comes from an already-authorised record."""
    from api.services import file_store as fs_module

    store = fs_module.InMemoryFileStore()
    fid = store.put(
        content=b"B",
        filename="f",
        mime_type="text/plain",
        metadata={"employee_id": "e1"},
    )
    assert store.get(fid, require_kind=fs_module.ANY_KIND) is not None
    assert store.get(fid, require_kind="business_logo") is None


# ---------------------------------------------------------------------------
# STRUCTURAL guard for the class: no NEW unscoped serve can appear silently
# ---------------------------------------------------------------------------
# Every audited exception below takes its file_id from a record the caller has
# already been authorised to read, so the record IS the authorisation. Any call
# site not listed here must pass require_kind, or this test fails.
#
# EVERY entry below was re-derived from what the code ACTUALLY does, at
# file:line, after the security panel proved the previous version of this table
# was wrong. The earlier entries for tasks.py and vendors.py claimed the file_id
# was "read off the record" -- it is not: both ACCEPT it from the request body
# (TaskCreate.attachment_file_id, GRNCreate.attachment_file_id) and validated it
# for EXISTENCE only. That made this allow-list a laundering step rather than an
# authorisation, so both are now fixed and removed from the table.
#
# The remaining four are safe for one specific, checkable reason: the file_id is
# MINTED SERVER-SIDE by store.put() inside the same handler and written onto the
# record there. It is never accepted from a request, so there is no id for an
# attacker to substitute.
#
# The value is the expected NUMBER of unscoped reads in that file. A count, not
# a boolean, so appending a NEW unscoped door to an already-listed file fails
# this test instead of inheriting the exemption.
_AUDITED_UNSCOPED_FILE_GETS = {
    # file -> (expected unscoped reads, the line that MINTS the id server-side)
    "admin_catalog.py": (1, "file_id = store.put(...) then job['file_id'] = it"),
    "expenses.py": (1, "file_id = store.put(...) then expense['bill_file_id'] = it"),
    "handoffs.py": (1, "file_id = fs.put(...) then handoff['file']['file_id'] = it"),
    "hr.py": (1, "file_id = store.put(...) then doc_record['file_id'] = it"),
}

# Floor on DETECTED CALL SITES (not modules scanned). If a refactor makes the
# detector stop seeing file-store reads, this trips instead of the suite going
# quietly green -- an earlier version asserted on "modules containing the string
# get_file_store", which cannot notice detection collapsing.
#
# Set to the EXACT current count, not a round number below it: the panel noted
# that a floor of 8 against 10 real sites left two sites of slack, i.e. the
# detector could go blind to two doors and still pass. Raising this when a
# legitimate new read is added is the intended friction.
_MIN_DETECTED_FILE_GETS = 10


def scan_file_store_reads(root):
    """Scan a tree for FileStore.get call sites.

    Returns ``(detected, unscoped_by_file)``. Extracted from the test body so
    the guard can be POINTED AT A FIXTURE and proven to fail -- the previous
    "ANY_KIND is unscoped" test ast.parsed a string and re-implemented these
    isinstance checks inline, so deleting the real ANY_KIND arm turned nothing
    red. A guard whose own test never calls it is the exact failure mode this
    guard exists to prevent, recursing.
    """
    import ast
    import pathlib

    root = pathlib.Path(root)
    unscoped_by_file = {}
    detected = 0

    for path in sorted(root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "get_file_store" not in source:
            continue
        tree = ast.parse(source)

        # 1. Resolve HANDLES, not spellings. Any name bound -- directly or
        #    transitively -- to a get_file_store() result is a file-store
        #    handle, however it is spelled. Iterate to a fixed point so
        #    `s = get_file_store(); alias = s` is followed too.
        handles = set()
        changed = True
        while changed:
            changed = False
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign):
                    continue
                value = node.value
                is_handle = False
                if isinstance(value, ast.Call):
                    callee = value.func
                    name = getattr(callee, "id", None) or getattr(callee, "attr", None)
                    is_handle = name == "get_file_store"
                elif isinstance(value, ast.Name):
                    is_handle = value.id in handles
                if not is_handle:
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id not in handles:
                        handles.add(target.id)
                        changed = True

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "get":
                continue
            # 2. A read on a handle counts however the handle is spelled --
            #    including the un-named `get_file_store().get(...)` chain.
            receiver = func.value
            on_handle = isinstance(receiver, ast.Name) and receiver.id in handles
            if not on_handle and isinstance(receiver, ast.Call):
                callee = receiver.func
                name = getattr(callee, "id", None) or getattr(callee, "attr", None)
                on_handle = name == "get_file_store"
            if not on_handle:
                continue
            # 3. NO filtering on how the ARGUMENT is spelled. The previous
            #    version required the literal token "file_id" in the first
            #    argument, so `fid = file_id; fs.get(fid)` walked straight
            #    past it -- the panel restored the exact round-4 P0 in that
            #    spelling and this test still passed. FileStore.get has one
            #    purpose, so every call on a handle is a read of the bucket.
            detected += 1
            # 3b. Check the VALUE, not the presence of the token. The previous
            #    version accepted ANY require_kind= keyword, so the panel
            #    restored the round-4 P0 as
            #    `fs.get(file_id, require_kind=ANY_KIND)` and this test passed --
            #    while file_store treats ANY_KIND and None as fully unscoped. A
            #    scoped read is one whose value is neither.
            scoped = False
            for kw in node.keywords:
                if kw.arg != "require_kind":
                    continue
                value = kw.value
                unscoped_value = (
                    isinstance(value, ast.Constant) and value.value is None
                ) or (isinstance(value, ast.Name) and value.id == "ANY_KIND") or (
                    isinstance(value, ast.Attribute) and value.attr == "ANY_KIND"
                )
                scoped = not unscoped_value
            if scoped:
                continue
            unscoped_by_file.setdefault(path.name, []).append(node.lineno)

    return detected, unscoped_by_file


def test_every_file_store_get_is_kind_scoped_or_audited():
    """The structural fix for the class. Rounds 1-3 each shipped with one
    unremembered door; this makes the next one fail CI instead."""
    import pathlib

    api_root = pathlib.Path(__file__).resolve().parents[1] / "api"
    offenders = []
    detected, unscoped_by_file = scan_file_store_reads(api_root)

    # 4. The floor is on DETECTED CALL SITES, so detection collapsing trips the
    #    test instead of silently emptying it.
    assert detected >= _MIN_DETECTED_FILE_GETS, (
        f"file-store detector found only {detected} call sites "
        f"(expected >= {_MIN_DETECTED_FILE_GETS}) -- detection has regressed, "
        "so this guard would pass vacuously"
    )

    for filename, lines in sorted(unscoped_by_file.items()):
        audited = _AUDITED_UNSCOPED_FILE_GETS.get(filename)
        if audited is None:
            offenders.append(f"{filename}:{lines} (not audited)")
            continue
        expected, _reason = audited
        if len(lines) != expected:
            offenders.append(
                f"{filename}: {len(lines)} unscoped reads at {lines}, "
                f"audit records {expected}"
            )

    assert not offenders, (
        "UNSCOPED file-store read(s): {}\n"
        "Pass require_kind=\"<the kind stamped at upload>\". ANY_KIND is NOT a "
        "way to silence this check -- file_store treats it as a fully unscoped "
        "read of a bucket that also holds employee Aadhaar/PAN scans, and this "
        "guard counts it as unscoped. It is legitimate ONLY when the file_id "
        "was minted server-side by store.put() in the same handler and read "
        "back off the record (never accepted from the request); in that case "
        "add a PROVEN entry to _AUDITED_UNSCOPED_FILE_GETS naming the minting "
        "line.".format(offenders)
    )


# ===========================================================================
# documents[].file_id must not be handed to a require_manager caller
# ===========================================================================
# hr.py's docstring claims "the bytes are only reachable through the RBAC-gated
# download endpoint". sanitize_user never touched `documents`, so the GridFS
# handle rode out whole on every users read -- and (with the logo door above)
# that handle was all an attacker needed.

_EMPLOYEE_WITH_DOCS = dict(
    _STORE_A_EMPLOYEE,
    documents=[
        {
            "doc_id": "d1",
            "doc_type": "AADHAAR",
            "file_id": "GRIDFS-HANDLE-0001",
            "filename": "aadhaar.jpg",
            "content_type": "image/jpeg",
            "size": 1234,
            "uploaded_at": "2026-08-01T00:00:00",
            "uploaded_by": "admin-1",
        }
    ],
)


@pytest.mark.parametrize(
    "path",
    ["/api/v1/users/emp-a", "/api/v1/users/store/S1", "/api/v1/users"],
)
def test_no_users_route_emits_a_document_file_id(path, monkeypatch):
    repo = _FakeUserRepo(seed=[_EMPLOYEE_WITH_DOCS, _MANAGER_A])
    c, _ = _users_client(_ADMIN, monkeypatch, repo=repo)
    r = c.get(path)
    assert r.status_code == 200, r.text
    assert "GRIDFS-HANDLE-0001" not in r.text
    assert '"file_id"' not in r.text


def test_document_metadata_still_travels_so_the_list_ui_works(monkeypatch):
    repo = _FakeUserRepo(seed=[_EMPLOYEE_WITH_DOCS, _MANAGER_A])
    c, _ = _users_client(_ADMIN, monkeypatch, repo=repo)
    docs = c.get("/api/v1/users/emp-a").json()["documents"]
    assert len(docs) == 1
    assert docs[0]["doc_id"] == "d1"
    assert docs[0]["doc_type"] == "AADHAAR"
    assert docs[0]["filename"] == "aadhaar.jpg"
    assert "file_id" not in docs[0]


# ===========================================================================
# The chair's two-call theft: attach someone else's file_id to your own task
# ===========================================================================
# POST /tasks and GET /tasks/{task_id}/file are both AUTHENTICATED, and the
# attachment file_id is supplied by the CALLER. The create path validated only
# that the id EXISTED -- and the bucket is shared, so "it exists" is equally
# true of a GRN supplier invoice or an employee Aadhaar scan. As SALES_STAFF:
# POST 201 -> GET 200 -> victim's bytes, with the victim's REAL filename in
# Content-Disposition even when a harmless attachment_filename was declared.

_THIEF = {
    "user_id": "thief-1",
    "username": "thief",
    "roles": ["SALES_STAFF"],
    "store_ids": ["S1"],
    "active_store_id": "S1",
}

_VICTIM_FILES = {
    "grn_supplier_invoice": (
        {"kind": "grn_document", "uploaded_by": "acct-1", "store_id": "S1"},
        "essilor_invoice_aug.pdf",
        b"SUPPLIER-INVOICE cost-price Rs.1420/unit Essilor terms 90d",
    ),
    "employee_aadhaar_scan": (
        {"employee_id": "emp-a", "doc_type": "AADHAAR"},
        "rekha_aadhaar.jpg",
        b"AADHAAR-SCAN-RAW-BYTES-1234-5678-9012",
    ),
    "another_users_task_file": (
        {"kind": "task_attachment", "uploaded_by": "someone-else"},
        "their_private_note.pdf",
        b"ANOTHER-USERS-TASK-ATTACHMENT",
    ),
}


def _tasks_client(actor, store, monkeypatch, repo=None):
    from api.routers import tasks as tasks_router

    monkeypatch.setattr(tasks_router, "get_file_store", lambda: store)
    app = FastAPI()
    app.include_router(tasks_router.router, prefix="/api/v1/tasks")

    async def _u():
        return dict(actor)

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


@pytest.mark.parametrize("victim", sorted(_VICTIM_FILES))
def test_sales_staff_cannot_launder_a_foreign_file_id_through_a_task(
    victim, monkeypatch
):
    """The chair's exact sequence: POST /tasks declaring the victim's file_id
    with a harmless filename, then GET the task's file."""
    from api.services import file_store as fs_module

    metadata, real_filename, secret = _VICTIM_FILES[victim]
    store = fs_module.InMemoryFileStore()
    victim_id = store.put(
        content=secret,
        filename=real_filename,
        mime_type="application/pdf",
        metadata=metadata,
    )
    c = _tasks_client(_THIEF, store, monkeypatch)

    created = c.post(
        "/api/v1/tasks",
        json={
            "title": "harmless looking task",
            "assigned_to": "thief-1",
            "due_at": "2026-12-31T00:00:00",
            "attachment_file_id": victim_id,
            "attachment_filename": "harmless.jpg",
            "attachment_mime": "image/jpeg",
        },
    )
    # The theft must die at the FIRST call: the id is refused at attach time.
    assert created.status_code == 400, created.text
    assert secret not in created.content
    assert real_filename not in created.text


def test_the_victims_filename_never_appears_in_body_or_headers(monkeypatch):
    """Even if a foreign id were somehow persisted on a task, the download must
    not stream it -- and must not disclose the real filename via
    Content-Disposition, which is how the chair identified the stolen file."""
    from api.routers import tasks as tasks_router
    from api.services import file_store as fs_module

    metadata, real_filename, secret = _VICTIM_FILES["grn_supplier_invoice"]
    store = fs_module.InMemoryFileStore()
    victim_id = store.put(
        content=secret,
        filename=real_filename,
        mime_type="application/pdf",
        metadata=metadata,
    )

    class _Repo:
        def find_by_id(self, task_id):
            return {
                "task_id": task_id,
                "store_id": "S1",
                "attachment": {"file_id": victim_id, "filename": "harmless.jpg"},
            }

    monkeypatch.setattr(tasks_router, "get_file_store", lambda: store)
    monkeypatch.setattr(tasks_router, "get_task_repository", lambda: _Repo())
    app = FastAPI()
    app.include_router(tasks_router.router, prefix="/api/v1/tasks")

    async def _u():
        return dict(_THIEF)

    app.dependency_overrides[get_current_user] = _u
    c = TestClient(app)

    r = c.get("/api/v1/tasks/T-1/file")
    assert r.status_code == 404, r.text
    assert secret not in r.content
    assert real_filename not in r.text
    for header_value in r.headers.values():
        assert real_filename not in header_value


def test_a_task_attachment_you_uploaded_yourself_still_works(monkeypatch):
    """Guard against trading the P0 for an outage: the legitimate flow -- upload
    then attach then download -- must be unaffected."""
    from api.routers import tasks as tasks_router
    from api.services import file_store as fs_module

    store = fs_module.InMemoryFileStore()
    mine = store.put(
        content=b"MY-OWN-ATTACHMENT",
        filename="mine.pdf",
        mime_type="application/pdf",
        metadata={"kind": "task_attachment", "uploaded_by": _THIEF["user_id"]},
    )

    class _Repo:
        def find_by_id(self, task_id):
            return {
                "task_id": task_id,
                "store_id": "S1",
                "attachment": {"file_id": mine, "filename": "mine.pdf"},
            }

    monkeypatch.setattr(tasks_router, "get_file_store", lambda: store)
    monkeypatch.setattr(tasks_router, "get_task_repository", lambda: _Repo())
    app = FastAPI()
    app.include_router(tasks_router.router, prefix="/api/v1/tasks")

    async def _u():
        return dict(_THIEF)

    app.dependency_overrides[get_current_user] = _u
    c = TestClient(app)

    r = c.get("/api/v1/tasks/T-1/file")
    assert r.status_code == 200, r.text
    assert r.content == b"MY-OWN-ATTACHMENT"


def _grn_doc_client(store, grn_doc, monkeypatch):
    """Mini app over the REAL GRN download route."""
    from api.routers import vendors as vendors_router

    class _GrnRepo:
        def find_one(self, _q):
            return grn_doc

    monkeypatch.setattr(vendors_router, "get_file_store", lambda: store)
    monkeypatch.setattr(vendors_router, "get_grn_repository", lambda: _GrnRepo())
    app = FastAPI()
    app.include_router(vendors_router.router, prefix="/api/v1/vendors")

    async def _u():
        return {
            "user_id": "acct-9",
            "roles": ["ACCOUNTANT"],
            "store_ids": ["S1"],
            "active_store_id": "S1",
        }

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


def test_grn_document_route_refuses_a_foreign_blob(monkeypatch):
    """Route-level: even a legitimately-entitled ACCOUNTANT must not be able to
    stream a non-GRN blob through the GRN download door."""
    from api.services import file_store as fs_module

    store = fs_module.InMemoryFileStore()
    aadhaar = store.put(
        content=b"AADHAAR-SCAN-RAW-BYTES",
        filename="rekha_aadhaar.jpg",
        mime_type="image/jpeg",
        metadata={"employee_id": "emp-a", "doc_type": "AADHAAR"},
    )
    c = _grn_doc_client(
        store,
        {"grn_id": "G-1", "store_id": "S1", "attachment_file_id": aadhaar},
        monkeypatch,
    )
    r = c.get("/api/v1/vendors/grn/G-1/document")
    assert r.status_code == 404, r.text
    assert b"AADHAAR-SCAN-RAW-BYTES" not in r.content
    assert "rekha_aadhaar.jpg" not in r.text
    for header_value in r.headers.values():
        assert "rekha_aadhaar.jpg" not in header_value


# ---------------------------------------------------------------------------
# The chair's cross-store, cross-entity GRN laundering (round 5)
# ---------------------------------------------------------------------------
# Checking only the KIND still let a caller launder another STORE's document:
# the download is scoped by the GRN RECORD, so binding a victim's grn_document
# file_id to a GRN in your own store walks it past that scope. VICTIM:
# ACCOUNTANT at BV-RANCHI-01. THIEF: STORE_MANAGER at WO-JSR-01 -- different
# store AND different legal entity. Front door 404s; the laundered door did not.


def _grn_items(vendors_router):
    # Ruling 14 (tallied) is a receiving-workflow precondition, not this file's
    # subject: every test here is about WHO may receive and WHOSE document may
    # be bound. Tick the line so the request reaches the permission/attachment
    # checks these tests exist to exercise.
    return [
        vendors_router.GRNItemCreate(
            product_id="P1",
            received_qty=5,
            accepted_qty=5,
            rejected_qty=0,
            tallied=True,
        )
    ]


def _grn_create_ok(monkeypatch, store, thief):
    """Drive the REAL _create_grn_impl with everything except the attachment
    authorisation stubbed permissive, so only that gate can reject."""
    import asyncio

    from api.routers import vendors as vendors_router

    monkeypatch.setattr(vendors_router, "get_file_store", lambda: store)

    class _PoRepo:
        """Receivable PO delivering to the CALLER's own store, so the store bind
        is the only thing that can reject -- the thief legitimately owns the GRN
        they are creating; it is the DOCUMENT that belongs to another store."""

        def find_by_id(self, po_id):
            return {
                "po_id": po_id,
                "po_number": "PO-TEST-1",
                "vendor_id": "V1",
                "vendor_name": "Acme Optics",
                "status": "SENT",
                "delivery_store_id": thief["active_store_id"],
                "items": [{"product_id": "P1", "quantity": 5}],
            }

        def find_one(self, _q):
            return self.find_by_id("PO1")

        def update(self, *_a, **_k):
            return True

    class _GrnRepo:
        def __init__(self):
            self.created = None

        def find_one(self, _q):
            return None

        def create(self, doc):
            self.created = doc
            return doc

        def find_many(self, *_a, **_k):
            return []

    grn_repo = _GrnRepo()
    monkeypatch.setattr(vendors_router, "get_grn_repository", lambda: grn_repo)
    monkeypatch.setattr(vendors_router, "get_purchase_order_repository", lambda: _PoRepo())
    return grn_repo


def test_thief_cannot_bind_another_stores_grn_document(monkeypatch):
    """CALL 1 of the chair's two-call theft must fail: binding the victim's
    file_id to a GRN in the thief's own store is refused."""
    import asyncio

    from api.routers import vendors as vendors_router
    from api.services import file_store as fs_module

    store = fs_module.InMemoryFileStore()
    victim_id = store.put(
        content=b"SUPPLIER-INVOICE cost-price Rs.1420/unit Essilor terms 90d",
        filename="essilor_ranchi_aug26.pdf",
        mime_type="application/pdf",
        metadata={
            "kind": "grn_document",
            "store_id": "BV-RANCHI-01",
            "uploaded_by": "acct-ranchi",
        },
    )
    thief = {
        "user_id": "sm-jsr",
        "roles": ["STORE_MANAGER"],
        "store_ids": ["WO-JSR-01"],
        "active_store_id": "WO-JSR-01",
    }
    _grn_create_ok(monkeypatch, store, thief)

    body = vendors_router.GRNCreate(
        po_id="PO1",
        vendor_invoice_no="INV-1",
        items=_grn_items(vendors_router),
        attachment_file_id=victim_id,
        attachment_filename="harmless.pdf",
    )
    with pytest.raises(Exception) as exc:
        asyncio.run(vendors_router._create_grn_impl(body, thief))
    detail = getattr(exc.value, "detail", {})
    assert getattr(exc.value, "status_code", None) == 400
    assert detail.get("code") == "ATTACHMENT_INVALID"
    # The rejection must not disclose WHY (kind vs store vs forged) -- one
    # message for all, so it is not an ownership oracle over the bucket.
    assert "store" not in str(detail).lower()
    assert "essilor_ranchi_aug26.pdf" not in str(detail)


def test_own_store_grn_document_still_binds(monkeypatch):
    """Guard against trading the fix for an outage: receiving must still work
    when the document was uploaded for the store the goods land in."""
    import asyncio

    from api.routers import vendors as vendors_router
    from api.services import file_store as fs_module

    store = fs_module.InMemoryFileStore()
    mine = store.put(
        content=b"REAL-GRN-DOC",
        filename="inv.pdf",
        mime_type="application/pdf",
        metadata={
            "kind": "grn_document",
            "store_id": "WO-JSR-01",
            "uploaded_by": "sm-jsr",
        },
    )
    actor = {
        "user_id": "sm-jsr",
        "roles": ["STORE_MANAGER"],
        "store_ids": ["WO-JSR-01"],
        "active_store_id": "WO-JSR-01",
    }
    _grn_create_ok(monkeypatch, store, actor)
    body = vendors_router.GRNCreate(
        po_id="PO1",
        vendor_invoice_no="INV-2",
        items=_grn_items(vendors_router),
        attachment_file_id=mine,
    )
    # Must get PAST the attachment gate. Anything later (empty items, numbering)
    # is not this test's concern -- only that ATTACHMENT_INVALID is not raised.
    try:
        asyncio.run(vendors_router._create_grn_impl(body, actor))
    except Exception as exc:  # noqa: BLE001
        detail = getattr(exc, "detail", {})
        code = detail.get("code") if isinstance(detail, dict) else None
        assert code != "ATTACHMENT_INVALID", f"own-store attachment refused: {detail}"


def test_delivery_challan_subtype_does_not_bypass_the_attachment_gate(monkeypatch):
    """MF1: both checks used to sit under `if not is_dc`, while persistence
    wrote attachment_file_id unconditionally -- so ONE extra JSON field,
    grn_subtype="DELIVERY_CHALLAN", re-opened the round-5 theft verbatim."""
    import asyncio

    from api.routers import vendors as vendors_router
    from api.services import file_store as fs_module

    store = fs_module.InMemoryFileStore()
    victim = store.put(
        content=b"SUPPLIER-INVOICE cost-price Rs.1420/unit Essilor terms 90d",
        filename="essilor_ranchi_aug26.pdf",
        mime_type="application/pdf",
        metadata={
            "kind": "grn_document",
            "store_id": "BV-RANCHI-01",
            "uploaded_by": "acct-ranchi",
        },
    )
    thief = {
        "user_id": "sm-jsr",
        "roles": ["STORE_MANAGER"],
        "store_ids": ["WO-JSR-01"],
        "active_store_id": "WO-JSR-01",
    }
    _grn_create_ok(monkeypatch, store, thief)
    body = vendors_router.GRNCreate(
        po_id="PO1",
        vendor_invoice_no="INV-DC",
        items=_grn_items(vendors_router),
        attachment_file_id=victim,
        attachment_filename="harmless.pdf",
        grn_subtype="DELIVERY_CHALLAN",
        dc_number="DC-1",
        dc_date="2026-08-01",
    )
    with pytest.raises(Exception) as exc:
        asyncio.run(vendors_router._create_grn_impl(body, thief))
    assert getattr(exc.value, "detail", {}).get("code") == "ATTACHMENT_INVALID"


def test_delivery_challan_without_an_attachment_still_creates(monkeypatch):
    """Positive control for MF1: a DC is exempt from the REQUIREMENT to attach,
    and tightening the gate must not break that."""
    import asyncio

    from api.routers import vendors as vendors_router
    from api.services import file_store as fs_module

    store = fs_module.InMemoryFileStore()
    actor = {
        "user_id": "sm-jsr",
        "roles": ["STORE_MANAGER"],
        "store_ids": ["WO-JSR-01"],
        "active_store_id": "WO-JSR-01",
    }
    repo = _grn_create_ok(monkeypatch, store, actor)
    body = vendors_router.GRNCreate(
        po_id="PO1",
        vendor_invoice_no="INV-DC2",
        items=_grn_items(vendors_router),
        grn_subtype="DELIVERY_CHALLAN",
        dc_number="DC-2",
        dc_date="2026-08-01",
    )
    asyncio.run(vendors_router._create_grn_impl(body, actor))
    assert repo.created is not None, "a DC with no attachment must still create"
    assert repo.created.get("attachment_file_id") is None


@pytest.mark.parametrize(
    "label,actor,blob_store,po_store,expected_grn_store",
    [
        (
            "ADMIN active at A receiving store B's PO",
            {"user_id": "adm", "roles": ["ADMIN"], "store_ids": [],
             "active_store_id": "BV-RANCHI-01"},
            "BV-RANCHI-01", "WO-JSR-01", "WO-JSR-01",
        ),
        (
            "AREA_MANAGER spanning both stores",
            {"user_id": "am", "roles": ["AREA_MANAGER"],
             "store_ids": ["WO-JSR-01", "BV-RANCHI-01"],
             "active_store_id": "WO-JSR-01"},
            "WO-JSR-01", "BV-RANCHI-01", "BV-RANCHI-01",
        ),
        (
            "HQ ADMIN with no active store",
            {"user_id": "hq", "roles": ["ADMIN"], "store_ids": [],
             "active_store_id": None},
            None, "WO-JSR-01", "WO-JSR-01",
        ),
    ],
)
def test_cross_store_receiving_is_not_blocked_by_the_attachment_bind(
    label, actor, blob_store, po_store, expected_grn_store, monkeypatch
):
    """MF2: my round-6 bind compared the blob's stamp to the POST-re-point
    store, so an ADMIN active at A receiving store B's PO was 400'd -- and
    re-uploading reproduced the same stamp, an unescapable loop. No GRN means
    no stock, no payable, no reconciliation. test_po_store_boundary states the
    intent: the GRN must book to the PO's store."""
    import asyncio

    from api.routers import vendors as vendors_router
    from api.services import file_store as fs_module

    store = fs_module.InMemoryFileStore()
    meta = {"kind": "grn_document", "uploaded_by": actor["user_id"]}
    if blob_store is not None:
        meta["store_id"] = blob_store
    fid = store.put(
        content=b"doc", filename="inv.pdf", mime_type="application/pdf", metadata=meta
    )

    from api.routers import vendors as vr

    monkeypatch.setattr(vr, "get_file_store", lambda: store)

    class _PoRepo:
        def find_by_id(self, po_id):
            return {
                "po_id": po_id, "po_number": "PO-1", "vendor_id": "V1",
                "vendor_name": "Acme", "status": "SENT",
                "delivery_store_id": po_store,
                "items": [{"product_id": "P1", "quantity": 5}],
            }

        def find_one(self, _q):
            return self.find_by_id("PO1")

        def update(self, *_a, **_k):
            return True

    class _GrnRepo:
        created = None

        def find_one(self, _q):
            return None

        def create(self, doc):
            _GrnRepo.created = doc
            return doc

        def find_many(self, *_a, **_k):
            return []

    _GrnRepo.created = None
    monkeypatch.setattr(vr, "get_grn_repository", lambda: _GrnRepo())
    monkeypatch.setattr(vr, "get_purchase_order_repository", lambda: _PoRepo())

    body = vendors_router.GRNCreate(
        po_id="PO1", vendor_invoice_no="INV-X",
        items=_grn_items(vendors_router), attachment_file_id=fid,
    )
    asyncio.run(vendors_router._create_grn_impl(body, actor))
    assert _GrnRepo.created is not None, f"{label}: receiving was blocked"
    assert _GrnRepo.created.get("store_id") == expected_grn_store


def test_same_store_foreign_kind_cannot_be_bound_to_a_grn(monkeypatch):
    """The KIND check must have its own behavioural cover, not just the store
    bind: a task_attachment carries a store_id too (tasks.py stamps it), so a
    colleague's same-store task file would satisfy the store bind alone."""
    import asyncio

    from api.routers import vendors as vendors_router
    from api.services import file_store as fs_module

    store = fs_module.InMemoryFileStore()
    same_store_task_file = store.put(
        content=b"A COLLEAGUE'S PRIVATE TASK ATTACHMENT",
        filename="private_note.pdf",
        mime_type="application/pdf",
        metadata={
            "kind": "task_attachment",
            "store_id": "WO-JSR-01",
            "uploaded_by": "someone-else",
        },
    )
    actor = {
        "user_id": "sm-jsr",
        "roles": ["STORE_MANAGER"],
        "store_ids": ["WO-JSR-01"],
        "active_store_id": "WO-JSR-01",
    }
    _grn_create_ok(monkeypatch, store, actor)
    body = vendors_router.GRNCreate(
        po_id="PO1",
        vendor_invoice_no="INV-4",
        items=_grn_items(vendors_router),
        attachment_file_id=same_store_task_file,
    )
    with pytest.raises(Exception) as exc:
        asyncio.run(vendors_router._create_grn_impl(body, actor))
    assert getattr(exc.value, "detail", {}).get("code") == "ATTACHMENT_INVALID"


def test_grn_attachment_without_a_store_stamp_is_refused(monkeypatch):
    """Fail CLOSED on an odd blob: no store_id means refuse, not allow."""
    import asyncio

    from api.routers import vendors as vendors_router
    from api.services import file_store as fs_module

    store = fs_module.InMemoryFileStore()
    unstamped = store.put(
        content=b"X",
        filename="x.pdf",
        mime_type="application/pdf",
        metadata={"kind": "grn_document", "uploaded_by": "someone"},
    )
    actor = {
        "user_id": "sm-jsr",
        "roles": ["STORE_MANAGER"],
        "store_ids": ["WO-JSR-01"],
        "active_store_id": "WO-JSR-01",
    }
    _grn_create_ok(monkeypatch, store, actor)
    body = vendors_router.GRNCreate(
        po_id="PO1",
        vendor_invoice_no="INV-3",
        items=_grn_items(vendors_router),
        attachment_file_id=unstamped,
    )
    with pytest.raises(Exception) as exc:
        asyncio.run(vendors_router._create_grn_impl(body, actor))
    detail = getattr(exc.value, "detail", {})
    assert detail.get("code") == "ATTACHMENT_INVALID"


def test_omitting_require_kind_raises_at_runtime():
    """THE spelling-proof control. A static guard has to enumerate spellings --
    the panel evaded the previous one with 11 of 16 (`fid = file_id`, a handle
    passed as a parameter, a handle on self., an aliased import, a walrus, a
    tuple-unpack, getattr(fs, "get"), a module without the literal string...).
    A required keyword-only argument does not care how the call is spelled."""
    import inspect

    from api.services import file_store as fs_module

    for impl in (fs_module.FileStore, fs_module.InMemoryFileStore, fs_module.GridFSFileStore):
        param = inspect.signature(impl.get).parameters["require_kind"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY, impl
        assert param.default is inspect.Parameter.empty, (
            f"{impl.__name__}.get gives require_kind a default -- omitting it "
            "must be a TypeError, not a silent unscoped read"
        )

    store = fs_module.InMemoryFileStore()
    fid = store.put(
        content=b"B", filename="f", mime_type="text/plain", metadata={"kind": "x"}
    )
    # NOTE: pylint does NOT reliably flag a missing require_kind at a real call
    # site -- get_file_store() is typed Optional[FileStore], so astroid cannot
    # resolve the receiver and never emits E1125. An earlier round claimed CI
    # lint as a third enforcement layer; that claim was FALSE and is retracted.
    # There are exactly TWO layers: the runtime signature and the AST guard.
    # The local disables below are only for these deliberate negative calls.
    with pytest.raises(TypeError):
        store.get(fid)  # pylint: disable=missing-kwoa
    # Every evasion spelling routes through the same signature.
    handle = store
    alias = handle
    fid2 = fid
    with pytest.raises(TypeError):
        alias.get(fid2)  # pylint: disable=missing-kwoa
    with pytest.raises(TypeError):
        getattr(alias, "get")(fid2)  # pylint: disable=missing-kwoa


@pytest.mark.parametrize(
    "spelling",
    [
        "fs.get(file_id)",
        "fs.get(file_id, require_kind=ANY_KIND)",
        "fs.get(file_id, require_kind=None)",
        "fs.get(file_id, require_kind=file_store.ANY_KIND)",
        "fid = file_id\n    return fs.get(fid)",
        "return get_file_store().get(file_id)",
    ],
)
def test_guard_reports_an_unscoped_read(spelling, tmp_path):
    """Drives the REAL guard against a fixture module.

    The previous version of this test ast.parsed a STRING and re-implemented
    the isinstance checks inline, so the panel deleted the ANY_KIND arm from
    the guard and the whole suite still passed -- 104 green, nothing red. This
    one calls scan_file_store_reads, so removing any arm makes it fail."""
    module = tmp_path / "zz_probe.py"
    module.write_text(
        "from api.services.file_store import get_file_store, ANY_KIND\n\n\n"
        "def _probe(file_id):\n"
        "    fs = get_file_store()\n"
        f"    {spelling}\n",
        encoding="utf-8",
    )
    detected, unscoped = scan_file_store_reads(tmp_path)
    assert detected == 1, f"guard did not detect the call site: {spelling}"
    assert "zz_probe.py" in unscoped, (
        f"guard treated this as SCOPED, it is not: {spelling}"
    )


def test_guard_accepts_a_genuinely_scoped_read(tmp_path):
    """The negative control: a real kind must NOT be reported, or the guard
    would be trivially satisfied by flagging everything."""
    module = tmp_path / "zz_ok.py"
    module.write_text(
        "from api.services.file_store import get_file_store\n\n\n"
        "def _ok(file_id):\n"
        "    fs = get_file_store()\n"
        '    return fs.get(file_id, require_kind="business_logo")\n',
        encoding="utf-8",
    )
    detected, unscoped = scan_file_store_reads(tmp_path)
    assert detected == 1
    assert unscoped == {}, f"a kind-scoped read was wrongly flagged: {unscoped}"


def test_grn_document_route_still_serves_a_real_grn_document(monkeypatch):
    """Guard against trading the fix for an outage -- 4 of these are live."""
    from api.services import file_store as fs_module

    store = fs_module.InMemoryFileStore()
    real_grn = store.put(
        content=b"REAL-GRN-DOC",
        filename="grn.pdf",
        mime_type="application/pdf",
        metadata={"kind": "grn_document", "uploaded_by": "acct-1"},
    )
    c = _grn_doc_client(
        store,
        {"grn_id": "G-1", "store_id": "S1", "attachment_file_id": real_grn},
        monkeypatch,
    )
    r = c.get("/api/v1/vendors/grn/G-1/document")
    assert r.status_code == 200, r.text
    assert r.content == b"REAL-GRN-DOC"


def test_catalogue_pdf_no_longer_falls_back_to_an_unscoped_read(monkeypatch):
    """The explicit unscoped RETRY pulled an Aadhaar scan into a customer-facing
    PDF once the kind-scoped read returned None."""
    from api.services import catalogue_pdf
    from api.services import file_store as fs_module

    store = fs_module.InMemoryFileStore()
    aadhaar = store.put(
        content=b"AADHAAR-SCAN-RAW-BYTES-IN-A-PDF",
        filename="rekha_aadhaar.jpg",
        mime_type="image/jpeg",
        metadata={"employee_id": "emp-a", "doc_type": "AADHAAR"},
    )
    monkeypatch.setattr(fs_module, "get_file_store", lambda: store)

    got = catalogue_pdf._fetch_local_bytes(f"/api/v1/products/image/{aadhaar}")
    assert got is None, "an unscoped blob was pulled into the catalogue PDF"

    real_image = store.put(
        content=b"PRODUCT-IMAGE-BYTES",
        filename="frame.jpg",
        mime_type="image/jpeg",
        metadata={"kind": "product_image"},
    )
    assert (
        catalogue_pdf._fetch_local_bytes(f"/api/v1/products/image/{real_image}")
        == b"PRODUCT-IMAGE-BYTES"
    )


def test_file_store_exposes_metadata_for_authorisation():
    """Existence is not authorisation -- handlers need the kind/owner to decide."""
    from api.services import file_store as fs_module

    store = fs_module.InMemoryFileStore()
    fid = store.put(
        content=b"B",
        filename="f",
        mime_type="text/plain",
        metadata={"kind": "task_attachment", "uploaded_by": "u1"},
    )
    meta = store.get_metadata(fid)
    assert meta["kind"] == "task_attachment"
    assert meta["uploaded_by"] == "u1"
    assert store.get_metadata("no-such-id") is None


def test_safe_documents_is_an_allow_list():
    out = users_router._safe_documents(
        [{"doc_id": "d1", "file_id": "H", "a_new_field_next_year": "leak"}]
    )
    assert out == [{"doc_id": "d1"}]
    # Junk shapes never raise.
    assert users_router._safe_documents(None) == []
    assert users_router._safe_documents("nope") == []
    assert users_router._safe_documents([None, 1]) == []
