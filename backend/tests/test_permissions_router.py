"""
IMS 2.0 - Per-user permissions: router escalation guard + discount_cap clamp +
audit no-log-no-commit + revert-through-guard
==============================================================================
Mirrors the TestClient + fake-repo harness in test_user_role_guards.py, adding a
fake audit repo so the no-log-no-commit contract is exercised.

Run: ``JWT_SECRET_KEY=test python -m pytest backend/tests/test_permissions_router.py -q``
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import users  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


class _FakeColl:
    def __init__(self, docs):
        self._docs = docs

    def find_one(self, query):
        import re as _re

        email_q = query.get("email")
        if isinstance(email_q, dict) and "$regex" in email_q:
            pat = _re.compile(email_q["$regex"], _re.IGNORECASE)
            for d in self._docs.values():
                if d.get("email") and pat.match(d["email"]):
                    return dict(d)
            return None
        for d in self._docs.values():
            if all(d.get(k) == v for k, v in query.items()):
                return dict(d)
        return None


class _FakeUserRepo:
    def __init__(self, seed=None):
        self._docs = {}
        if seed:
            for doc in seed:
                self._docs[doc["user_id"]] = dict(doc)
        self.collection = _FakeColl(self._docs)
        self._next = 0

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

    def find_by_role(self, role, store_id=None):
        return [
            dict(d)
            for d in self._docs.values()
            if role in (d.get("roles") or []) and d.get("is_active", True)
        ]

    def create(self, user_data):
        doc = dict(user_data)
        self._next += 1
        doc.setdefault("user_id", f"u-new-{self._next}")
        self._docs[doc["user_id"]] = doc
        return dict(doc)

    def update(self, user_id, update_data):
        d = self._docs.get(user_id)
        if d is None:
            return False
        d.update(update_data)
        return True


class _FakeAuditRepo:
    """Records rows; ``fail`` makes create() return None (audit failure path)."""

    def __init__(self, fail=False):
        self.rows = []
        self.fail = fail
        self._n = 0

    def create(self, row):
        if self.fail:
            return None
        self._n += 1
        r = dict(row)
        r["log_id"] = f"audit-{self._n}"
        self.rows.append(r)
        return r

    def find_many(self, flt, sort=None, limit=None):
        out = [
            dict(r)
            for r in self.rows
            if all(r.get(k) == v for k, v in flt.items())
        ]
        out.reverse()  # newest-first (matches sort=[("timestamp", -1)])
        return out


def _client(repo, audit_repo, actor, monkeypatch):
    monkeypatch.setattr(users, "get_user_repository", lambda: repo)
    monkeypatch.setattr(users, "get_audit_repository", lambda: audit_repo)
    app = FastAPI()
    app.include_router(users.router, prefix="/api/v1/users")

    async def _u():
        return dict(actor)

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


_ADMIN = {"user_id": "admin-1", "username": "admin", "roles": ["ADMIN"], "store_ids": ["S1"]}
_SM = {
    "user_id": "sm-1",
    "username": "sm",
    "roles": ["STORE_MANAGER"],
    "store_ids": ["S1"],
}

_BASE = {
    "username": "newbie",
    "email": "newbie@example.com",
    "password": "Welcome@123",
    "full_name": "New Bie",
    "store_ids": ["S1"],
    "roles": ["SALES_CASHIER"],
}


# ---------------------------------------------------------------------------
# CREATE: permissions persisted (dark when absent) + audit written.
# ---------------------------------------------------------------------------

def test_create_without_permissions_is_dark_no_perm_audit(monkeypatch):
    repo = _FakeUserRepo()
    audit = _FakeAuditRepo()
    c = _client(repo, audit, _ADMIN, monkeypatch)
    r = c.post("/api/v1/users", json=dict(_BASE))
    assert r.status_code == 201, r.text
    created = repo.find_by_id(r.json()["user_id"])
    # DARK: permissions defaults to {} and NO permission-audit row is written
    # for a plain account create.
    assert created["permissions"] == {}
    assert audit.rows == []


def test_create_with_grant_persists_and_audits(monkeypatch):
    repo = _FakeUserRepo()
    audit = _FakeAuditRepo()
    c = _client(repo, audit, _ADMIN, monkeypatch)
    body = dict(_BASE)
    body["permissions"] = {"grant": {"reports:read": True}, "deny": {"returns:write": True}}
    r = c.post("/api/v1/users", json=body)
    assert r.status_code == 201, r.text
    created = repo.find_by_id(r.json()["user_id"])
    assert created["permissions"] == {
        "grant": {"reports:read": True},
        "deny": {"returns:write": True},
    }
    assert len(audit.rows) == 1
    row = audit.rows[0]
    assert row["action"] == "PERMISSIONS_CREATE"
    assert row["permissions_after"]["permissions"]["grant"] == {"reports:read": True}


# ---------------------------------------------------------------------------
# ESCALATION GUARD (pure helper level -- the honest level: only ADMIN/SUPERADMIN
# reach the create/update endpoints, and they can grant everything grantable, so
# the 403 path is exercised against the guard helper a lower-privilege create
# path would call). _guard_permission_grants + grantable_capabilities_for.
# ---------------------------------------------------------------------------

def test_guard_rejects_grant_actor_cannot_make():
    from fastapi import HTTPException

    # A STORE_MANAGER cannot reach finance:write -> the guard rejects a grant of
    # it (the per-user analogue of the role ceiling).
    actor = {"roles": ["STORE_MANAGER"]}
    perms = {"grant": {"products:write": True}}
    try:
        users._guard_permission_grants(actor, perms)
        raise AssertionError("expected HTTPException(403)")
    except HTTPException as e:
        assert e.status_code == 403
        assert "products:write" in e.detail


def test_guard_allows_grant_actor_holds():
    # A STORE_MANAGER CAN reach orders:write -> granting it is allowed (no raise).
    users._guard_permission_grants({"roles": ["STORE_MANAGER"]}, {"grant": {"orders:write": True}})


def test_clamp_rejects_discount_above_actor_cap():
    from fastapi import HTTPException

    # STORE_MANAGER cap is 20; clamping a 100 request raises 403.
    try:
        users._clamp_discount_cap({"roles": ["STORE_MANAGER"]}, 100, ["SALES_STAFF"])
        raise AssertionError("expected HTTPException(403)")
    except HTTPException as e:
        assert e.status_code == 403
        assert "discount cap" in e.detail.lower()


def test_clamp_allows_discount_within_actor_cap():
    # STORE_MANAGER (cap 20) setting 15 on a junior is fine.
    assert users._clamp_discount_cap({"roles": ["STORE_MANAGER"]}, 15, ["SALES_STAFF"]) == 15
    # ADMIN (cap 100) unconstrained.
    assert users._clamp_discount_cap({"roles": ["ADMIN"]}, 80, ["SALES_STAFF"]) == 80


def test_jarvis_grant_is_sanitized_away_not_persisted(monkeypatch):
    # jarvis is ungrantable -> sanitized OUT (so the guard sees an empty grant
    # and it persists as {}). The capability never lands on the user.
    repo = _FakeUserRepo()
    audit = _FakeAuditRepo()
    c = _client(repo, audit, _ADMIN, monkeypatch)
    body = dict(_BASE)
    body["permissions"] = {"grant": {"jarvis:read": True}}
    r = c.post("/api/v1/users", json=body)
    assert r.status_code == 201, r.text
    created = repo.find_by_id(r.json()["user_id"])
    assert created["permissions"] == {}


# ---------------------------------------------------------------------------
# DISCOUNT-CAP CLAMP retrofit (the live bug).
# ---------------------------------------------------------------------------

def test_admin_can_set_high_discount_cap(monkeypatch):
    # ADMIN has a 100 cap, so granting 50 is fine.
    repo = _FakeUserRepo()
    audit = _FakeAuditRepo()
    c = _client(repo, audit, _ADMIN, monkeypatch)
    body = dict(_BASE)
    body["discount_cap"] = 50
    r = c.post("/api/v1/users", json=body)
    assert r.status_code == 201, r.text
    created = repo.find_by_id(r.json()["user_id"])
    assert created["discount_cap"] == 50


# ---------------------------------------------------------------------------
# UPDATE: permissions edit guarded + audited; unrelated edit stays dark.
# ---------------------------------------------------------------------------

def test_update_unrelated_field_writes_no_permission_audit(monkeypatch):
    repo = _FakeUserRepo(
        seed=[{"user_id": "t1", "username": "t", "roles": ["SALES_STAFF"], "store_ids": ["S1"], "permissions": {}}]
    )
    audit = _FakeAuditRepo()
    c = _client(repo, audit, _ADMIN, monkeypatch)
    r = c.put("/api/v1/users/t1", json={"phone": "9876543210"})
    assert r.status_code == 200, r.text
    assert audit.rows == []  # DARK: a phone edit is not a permission change


def test_update_permissions_audited(monkeypatch):
    repo = _FakeUserRepo(
        seed=[{"user_id": "t1", "username": "t", "roles": ["SALES_STAFF"], "store_ids": ["S1"], "permissions": {}}]
    )
    audit = _FakeAuditRepo()
    c = _client(repo, audit, _ADMIN, monkeypatch)
    r = c.put("/api/v1/users/t1", json={"permissions": {"deny": {"orders:write": True}}})
    assert r.status_code == 200, r.text
    assert len(audit.rows) == 1
    assert audit.rows[0]["action"] == "PERMISSIONS_UPDATE"
    assert repo.find_by_id("t1")["permissions"] == {"deny": {"orders:write": True}}


# ---------------------------------------------------------------------------
# NO-LOG-NO-COMMIT: audit failure aborts the permission write.
# ---------------------------------------------------------------------------

def test_audit_failure_aborts_update(monkeypatch):
    repo = _FakeUserRepo(
        seed=[{"user_id": "t1", "username": "t", "roles": ["SALES_STAFF"], "store_ids": ["S1"], "permissions": {}}]
    )
    audit = _FakeAuditRepo(fail=True)  # create() returns None
    c = _client(repo, audit, _ADMIN, monkeypatch)
    r = c.put("/api/v1/users/t1", json={"permissions": {"deny": {"orders:write": True}}})
    assert r.status_code == 500, r.text
    # The permission change must NOT have committed.
    assert repo.find_by_id("t1")["permissions"] == {}


# ---------------------------------------------------------------------------
# REVERT through the same guard.
# ---------------------------------------------------------------------------

def test_revert_reapplies_prior_snapshot_through_guard(monkeypatch):
    repo = _FakeUserRepo(
        seed=[{"user_id": "t1", "username": "t", "roles": ["SALES_STAFF"], "store_ids": ["S1"], "permissions": {}}]
    )
    audit = _FakeAuditRepo()
    c = _client(repo, audit, _ADMIN, monkeypatch)
    # 1) set a deny (creates audit-1 with after = {deny orders:write})
    c.put("/api/v1/users/t1", json={"permissions": {"deny": {"orders:write": True}}})
    # 2) change to a grant (audit-2)
    c.put("/api/v1/users/t1", json={"permissions": {"grant": {"reports:read": True}}})
    assert repo.find_by_id("t1")["permissions"] == {"grant": {"reports:read": True}}
    # 3) revert to audit-1's after-state (the deny)
    r = c.post("/api/v1/users/t1/permissions/revert", json={"audit_log_id": "audit-1"})
    assert r.status_code == 200, r.text
    assert repo.find_by_id("t1")["permissions"] == {"deny": {"orders:write": True}}
    # A REVERT audit row was written.
    assert any(row["action"] == "PERMISSIONS_REVERT" for row in audit.rows)


def test_revert_reruns_sanitize_so_ungrantable_cannot_be_laundered(monkeypatch):
    # A forged historical snapshot that granted an UNGRANTABLE capability
    # (jarvis:read) cannot be laundered back in via revert -- the revert re-runs
    # sanitize, which strips it. The user ends up with the grant removed (NOT
    # jarvis:read restored).
    repo = _FakeUserRepo(
        seed=[{"user_id": "t1", "username": "t", "roles": ["SALES_STAFF"], "store_ids": ["S1"], "permissions": {}}]
    )
    audit = _FakeAuditRepo()
    audit.rows.append(
        {
            "log_id": "audit-hi",
            "action": "PERMISSIONS_UPDATE",
            "entity_type": "user_permissions",
            "target_user_id": "t1",
            "permissions_after": {
                "permissions": {"grant": {"jarvis:read": True}},
                "module_access": {},
                "discount_cap": None,
            },
        }
    )
    c = _client(repo, audit, _ADMIN, monkeypatch)
    r = c.post("/api/v1/users/t1/permissions/revert", json={"audit_log_id": "audit-hi"})
    assert r.status_code == 200, r.text
    # jarvis:read was stripped by sanitize on the revert path -- not restored.
    assert repo.find_by_id("t1")["permissions"] == {}


def test_revert_guard_helper_blocks_above_level():
    # The escalation guard itself (re-run on revert) rejects a grant the actor
    # cannot make -- proving revert routes through the SAME guard, not a bypass.
    from fastapi import HTTPException

    try:
        users._guard_permission_grants(
            {"roles": ["STORE_MANAGER"]}, {"grant": {"products:write": True}}
        )
        raise AssertionError("expected HTTPException(403)")
    except HTTPException as e:
        assert e.status_code == 403


# ---------------------------------------------------------------------------
# OPTIONS endpoint returns sentence-shaped deltas.
# ---------------------------------------------------------------------------

def test_permission_options_returns_sentences(monkeypatch):
    repo = _FakeUserRepo()
    audit = _FakeAuditRepo()
    c = _client(repo, audit, _ADMIN, monkeypatch)
    r = c.get("/api/v1/users/permissions/options")
    assert r.status_code == 200, r.text
    data = r.json()
    assert "role_deltas" in data and "grantable" in data
    sc = data["role_deltas"]["SALES_CASHIER"]["commonOverrides"]
    assert any("Allow" in row["label"] for row in sc)
