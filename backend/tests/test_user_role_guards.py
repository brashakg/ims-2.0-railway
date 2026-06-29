"""
IMS 2.0 - User-management privilege-escalation + validation guards
==================================================================
Locks the SECURITY-SENSITIVE contract of the users router that previously lived
only in the React UI (and was therefore bypassable by hitting the API):

  * role-assignment ceiling: an actor can never create/promote a user to a role
    ABOVE their own highest level (no self- or other-escalation to SUPERADMIN).
  * role/enum validation: junk roles are rejected with 422/400.
  * email/phone format + case-insensitive duplicate email.
  * password floor (8) + bcrypt 72-byte ceiling.
  * deny-only module_access (a True/grant entry is stripped, junk keys dropped).
  * last-admin / last-role guards on deactivate + role removal.
  * a non-SUPERADMIN cannot modify/deactivate a higher-ranked account.

Mirrors the TestClient + fake-repo harness in test_module_rbac.py.
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
from api.services import user_roles  # noqa: E402


# ---------------------------------------------------------------------------
# Fake repo (adds find_by_role / add_role / remove_role / add_store over the
# slice test_module_rbac.py uses, so the admin-count + role mutations work).
# ---------------------------------------------------------------------------


class _FakeColl:
    """Minimal collection shim so _find_by_email_ci's regex path (the real
    Mongo behaviour) is exercised. Supports the case-insensitive email regex
    lookup the router issues."""

    def __init__(self, docs):
        self._docs = docs

    def find_one(self, query):
        email_q = query.get("email")
        if isinstance(email_q, dict) and "$regex" in email_q:
            import re as _re

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
        self.last_update = None
        self.collection = _FakeColl(self._docs)

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

    def add_role(self, user_id, role):
        d = self._docs.get(user_id)
        if d is None:
            return False
        d.setdefault("roles", [])
        if role not in d["roles"]:
            d["roles"].append(role)
        return True

    def remove_role(self, user_id, role):
        d = self._docs.get(user_id)
        if d is None:
            return False
        d["roles"] = [r for r in d.get("roles", []) if r != role]
        return True

    def add_store(self, user_id, store_id):
        d = self._docs.get(user_id)
        if d is None:
            return False
        d.setdefault("store_ids", [])
        if store_id not in d["store_ids"]:
            d["store_ids"].append(store_id)
        return True


def _client(repo, actor, monkeypatch):
    monkeypatch.setattr(users, "get_user_repository", lambda: repo)
    app = FastAPI()
    app.include_router(users.router, prefix="/api/v1/users")

    async def _u():
        return dict(actor)

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


_ADMIN = {"user_id": "admin-1", "roles": ["ADMIN"], "store_ids": ["S1"]}
_SUPER = {"user_id": "su-1", "roles": ["SUPERADMIN"], "store_ids": ["S1"]}
_AREA = {"user_id": "am-1", "roles": ["AREA_MANAGER"], "store_ids": ["S1"]}

_BASE = {
    "username": "newbie",
    "email": "newbie@example.com",
    "password": "Welcome@123",
    "full_name": "New Bie",
    "store_ids": ["S1"],
}


# ===========================================================================
# Pure helper unit tests (no HTTP)
# ===========================================================================


def test_role_set_matches_rbac_policy():
    # user_roles.VALID_ROLES must stay in sync with the owned rbac_policy table
    # (the 11 operational roles) plus the read-only INVESTOR role.
    from api.services import rbac_policy

    assert set(rbac_policy.ALL_ROLES) | {"INVESTOR"} == set(user_roles.VALID_ROLES)


# ---------------------------------------------------------------------------
# SALES_CASHIER -> SALES_STAFF merge (backlog #12)
# ---------------------------------------------------------------------------


def test_sales_cashier_is_a_recognized_but_deprecated_alias():
    # Still recognized (an existing user/token carrying it is NOT rejected) ...
    assert "SALES_CASHIER" in user_roles.VALID_ROLES
    assert user_roles.validate_roles(["SALES_CASHIER"])[0] is True
    # ... but it maps to the survivor and is NOT assignable to new users.
    assert user_roles.DEPRECATED_ROLE_ALIASES["SALES_CASHIER"] == "SALES_STAFF"
    assert "SALES_CASHIER" not in user_roles.ASSIGNABLE_ROLES
    assert "SALES_STAFF" in user_roles.ASSIGNABLE_ROLES


def test_normalize_role_maps_cashier_to_staff():
    assert user_roles.normalize_role("SALES_CASHIER") == "SALES_STAFF"
    # Non-deprecated roles pass through untouched.
    assert user_roles.normalize_role("STORE_MANAGER") == "STORE_MANAGER"
    assert user_roles.normalize_role("SALES_STAFF") == "SALES_STAFF"


def test_normalize_roles_dedupes_after_merge():
    # A user holding BOTH the alias and the survivor collapses to one SALES_STAFF.
    assert user_roles.normalize_roles(["SALES_CASHIER", "SALES_STAFF"]) == ["SALES_STAFF"]
    assert user_roles.normalize_roles(["SALES_CASHIER"]) == ["SALES_STAFF"]
    assert user_roles.normalize_roles(
        ["STORE_MANAGER", "SALES_CASHIER"]
    ) == ["STORE_MANAGER", "SALES_STAFF"]
    assert user_roles.normalize_roles([]) == []
    assert user_roles.normalize_roles(None) == []


def test_decode_token_normalizes_sales_cashier_to_sales_staff():
    # An existing JWT still carrying SALES_CASHIER must be treated as SALES_STAFF
    # by every consumer (require_roles, middleware) so the user is NOT locked out.
    import jwt as _jwt
    from datetime import datetime, timedelta
    from api.routers.auth import decode_token, SECRET_KEY, ALGORITHM

    token = _jwt.encode(
        {
            "user_id": "legacy-1",
            "username": "legacy",
            "roles": ["SALES_CASHIER"],
            "exp": datetime.utcnow() + timedelta(hours=1),
        },
        SECRET_KEY,
        algorithm=ALGORITHM,
    )
    payload = decode_token(token)
    assert payload["roles"] == ["SALES_STAFF"]


def test_create_user_with_sales_cashier_persists_sales_staff(monkeypatch):
    # Passing the deprecated role to user-create is accepted (not rejected) but
    # silently stored as the survivor -- no NEW user ends up with SALES_CASHIER.
    repo = _FakeUserRepo()
    c = _client(repo, _ADMIN, monkeypatch)
    r = c.post("/api/v1/users", json=dict(_BASE, roles=["SALES_CASHIER"]))
    assert r.status_code == 201, r.text
    assert repo.find_by_username("newbie")["roles"] == ["SALES_STAFF"]


def test_add_sales_cashier_role_grants_sales_staff(monkeypatch):
    repo = _seed_one(["OPTOMETRIST"])
    c = _client(repo, _ADMIN, monkeypatch)
    r = c.post("/api/v1/users/u1/roles/SALES_CASHIER")
    assert r.status_code == 200, r.text
    roles = repo.find_by_id("u1")["roles"]
    assert "SALES_STAFF" in roles
    assert "SALES_CASHIER" not in roles


def test_can_assign_blocks_escalation():
    ok, bad = user_roles.can_assign_roles(["ADMIN"], ["SUPERADMIN"])
    assert ok is False and bad == "SUPERADMIN"


def test_can_assign_allows_equal_or_lower():
    assert user_roles.can_assign_roles(["ADMIN"], ["ADMIN", "STORE_MANAGER"])[0]
    assert user_roles.can_assign_roles(["AREA_MANAGER"], ["STORE_MANAGER"])[0]


def test_superadmin_can_assign_anything():
    assert user_roles.can_assign_roles(["SUPERADMIN"], ["SUPERADMIN"])[0]


def test_area_manager_cannot_mint_admin():
    ok, bad = user_roles.can_assign_roles(["AREA_MANAGER"], ["ADMIN"])
    assert ok is False and bad == "ADMIN"


def test_sanitize_module_access_strips_grants_and_junk():
    out = user_roles.sanitize_module_access(
        {"reports": False, "pos": True, "bogus": False, "hr": False}
    )
    assert out == {"reports": False, "hr": False}


def test_sanitize_module_access_none_passthrough():
    assert user_roles.sanitize_module_access(None) is None


def test_password_byte_limit():
    assert user_roles.password_within_bcrypt_limit("a" * 72)
    assert not user_roles.password_within_bcrypt_limit("a" * 73)
    # multibyte (ASCII source via chr): U+20AC is 3 bytes in UTF-8, so 24 chars
    # = 72 bytes (ok) and 25 chars = 75 bytes (over) -- even though both are
    # under the 72-CHAR schema cap.
    three_byte = chr(0x20AC)
    assert user_roles.password_within_bcrypt_limit(three_byte * 24)
    assert not user_roles.password_within_bcrypt_limit(three_byte * 25)


# ===========================================================================
# create_user privilege escalation
# ===========================================================================


def test_admin_cannot_create_superadmin(monkeypatch):
    repo = _FakeUserRepo()
    c = _client(repo, _ADMIN, monkeypatch)
    r = c.post("/api/v1/users", json=dict(_BASE, roles=["SUPERADMIN"]))
    assert r.status_code == 403, r.text
    assert "above your own level" in r.json()["detail"]
    # nothing persisted
    assert repo.find_by_username("newbie") is None


def test_area_manager_cannot_create_admin(monkeypatch):
    repo = _FakeUserRepo()
    c = _client(repo, _AREA, monkeypatch)
    r = c.post("/api/v1/users", json=dict(_BASE, roles=["ADMIN"]))
    assert r.status_code == 403, r.text


def test_superadmin_can_create_admin(monkeypatch):
    repo = _FakeUserRepo()
    c = _client(repo, _SUPER, monkeypatch)
    r = c.post("/api/v1/users", json=dict(_BASE, roles=["ADMIN"]))
    assert r.status_code == 201, r.text


def test_admin_can_create_store_manager(monkeypatch):
    repo = _FakeUserRepo()
    c = _client(repo, _ADMIN, monkeypatch)
    r = c.post("/api/v1/users", json=dict(_BASE, roles=["STORE_MANAGER"]))
    assert r.status_code == 201, r.text


# ===========================================================================
# create_user field validation
# ===========================================================================


def test_create_rejects_unknown_role(monkeypatch):
    repo = _FakeUserRepo()
    c = _client(repo, _SUPER, monkeypatch)
    r = c.post("/api/v1/users", json=dict(_BASE, roles=["SUPER_ADMIN"]))  # typo
    assert r.status_code == 422, r.text


def test_create_rejects_empty_roles(monkeypatch):
    repo = _FakeUserRepo()
    c = _client(repo, _SUPER, monkeypatch)
    r = c.post("/api/v1/users", json=dict(_BASE, roles=[]))
    assert r.status_code == 422, r.text


def test_create_rejects_bad_phone(monkeypatch):
    repo = _FakeUserRepo()
    c = _client(repo, _SUPER, monkeypatch)
    r = c.post(
        "/api/v1/users",
        json=dict(_BASE, roles=["SALES_STAFF"], phone="12345"),
    )
    assert r.status_code == 422, r.text


def test_create_accepts_and_normalizes_phone(monkeypatch):
    repo = _FakeUserRepo()
    c = _client(repo, _SUPER, monkeypatch)
    r = c.post(
        "/api/v1/users",
        json=dict(_BASE, roles=["SALES_STAFF"], phone="+91 98765-43210"),
    )
    assert r.status_code == 201, r.text
    # Normalized to the canonical bare 10-digit form (91 prefix + punctuation
    # stripped), matching customers.py / validatePhone.
    assert repo.find_by_username("newbie")["phone"] == "9876543210"


def test_create_rejects_overlong_password(monkeypatch):
    repo = _FakeUserRepo()
    c = _client(repo, _SUPER, monkeypatch)
    r = c.post(
        "/api/v1/users",
        json=dict(_BASE, roles=["SALES_STAFF"], password="A1!" + "a" * 70),
    )
    assert r.status_code == 422, r.text  # schema max_length=72


def test_create_duplicate_email_case_insensitive(monkeypatch):
    repo = _FakeUserRepo(
        [
            {
                "user_id": "u0",
                "username": "first",
                "email": "dup@example.com",
                "roles": ["SALES_STAFF"],
                "is_active": True,
            }
        ]
    )
    c = _client(repo, _SUPER, monkeypatch)
    r = c.post(
        "/api/v1/users",
        json=dict(_BASE, roles=["SALES_STAFF"], email="DUP@example.com"),
    )
    assert r.status_code == 400, r.text
    assert "Email already exists" in r.json()["detail"]


def test_create_must_change_password_honoured(monkeypatch):
    # The duplicate-key bug forced this True regardless of input; now the field
    # governs. Passing False provisions a permanent password.
    repo = _FakeUserRepo()
    c = _client(repo, _SUPER, monkeypatch)
    r = c.post(
        "/api/v1/users",
        json=dict(_BASE, roles=["SALES_STAFF"], must_change_password=False),
    )
    assert r.status_code == 201, r.text
    assert repo.find_by_username("newbie")["must_change_password"] is False


def test_create_must_change_password_defaults_true(monkeypatch):
    repo = _FakeUserRepo()
    c = _client(repo, _SUPER, monkeypatch)
    r = c.post("/api/v1/users", json=dict(_BASE, roles=["SALES_STAFF"]))
    assert r.status_code == 201, r.text
    assert repo.find_by_username("newbie")["must_change_password"] is True


# ===========================================================================
# update_user / add_role escalation
# ===========================================================================


def _seed_one(roles, user_id="u1", active=True):
    return _FakeUserRepo(
        [
            {
                "user_id": user_id,
                "username": user_id,
                "email": f"{user_id}@example.com",
                "full_name": user_id,
                "roles": list(roles),
                "store_ids": ["S1"],
                "is_active": active,
            }
        ]
    )


def test_admin_cannot_promote_user_to_superadmin_via_update(monkeypatch):
    repo = _seed_one(["SALES_STAFF"])
    c = _client(repo, _ADMIN, monkeypatch)
    r = c.put("/api/v1/users/u1", json={"roles": ["SUPERADMIN"]})
    assert r.status_code == 403, r.text
    assert repo.find_by_id("u1")["roles"] == ["SALES_STAFF"]  # unchanged


def test_admin_cannot_self_escalate_via_add_role(monkeypatch):
    # actor IS u1 (an ADMIN) trying to grant themselves SUPERADMIN.
    repo = _seed_one(["ADMIN"], user_id="admin-1")
    c = _client(repo, _ADMIN, monkeypatch)
    r = c.post("/api/v1/users/admin-1/roles/SUPERADMIN")
    assert r.status_code == 403, r.text
    assert "SUPERADMIN" not in repo.find_by_id("admin-1")["roles"]


def test_add_role_rejects_unknown_role(monkeypatch):
    repo = _seed_one(["SALES_STAFF"])
    c = _client(repo, _SUPER, monkeypatch)
    r = c.post("/api/v1/users/u1/roles/WIZARD")
    assert r.status_code == 400, r.text


def test_admin_can_add_allowed_role(monkeypatch):
    repo = _seed_one(["SALES_STAFF"])
    c = _client(repo, _ADMIN, monkeypatch)
    r = c.post("/api/v1/users/u1/roles/STORE_MANAGER")
    assert r.status_code == 200, r.text
    assert "STORE_MANAGER" in repo.find_by_id("u1")["roles"]


def test_admin_cannot_modify_higher_ranked_user(monkeypatch):
    repo = _seed_one(["SUPERADMIN"])
    c = _client(repo, _ADMIN, monkeypatch)
    r = c.put("/api/v1/users/u1", json={"full_name": "Hacked"})
    assert r.status_code == 403, r.text
    assert repo.find_by_id("u1")["full_name"] == "u1"


def test_superadmin_can_modify_anyone(monkeypatch):
    repo = _seed_one(["SUPERADMIN"])
    c = _client(repo, _SUPER, monkeypatch)
    r = c.put("/api/v1/users/u1", json={"full_name": "Renamed"})
    assert r.status_code == 200, r.text


def test_update_module_access_sanitized_on_write(monkeypatch):
    repo = _seed_one(["STORE_MANAGER"])
    c = _client(repo, _SUPER, monkeypatch)
    r = c.put(
        "/api/v1/users/u1",
        json={"module_access": {"pos": True, "finance": False, "junk": False}},
    )
    assert r.status_code == 200, r.text
    assert repo.find_by_id("u1")["module_access"] == {"finance": False}


def test_assign_store_role_escalation_blocked(monkeypatch):
    repo = _seed_one(["SALES_STAFF"])
    c = _client(repo, _ADMIN, monkeypatch)
    r = c.post(
        "/api/v1/users/u1/assign-store",
        json={"store_id": "S2", "role": "SUPERADMIN"},
    )
    assert r.status_code == 403, r.text
    # the store must NOT be added either (guard runs before any write)
    assert "S2" not in repo.find_by_id("u1")["store_ids"]


# ===========================================================================
# last-admin / last-role / self guards
# ===========================================================================


def test_cannot_deactivate_self(monkeypatch):
    repo = _seed_one(["ADMIN"], user_id="admin-1")
    # add a second admin so last-admin guard isn't the one that fires
    repo.create(
        {"user_id": "admin-2", "roles": ["ADMIN"], "is_active": True, "username": "a2"}
    )
    c = _client(repo, _ADMIN, monkeypatch)
    r = c.delete("/api/v1/users/admin-1")
    assert r.status_code == 400, r.text
    assert "yourself" in r.json()["detail"]


def test_cannot_deactivate_last_admin(monkeypatch):
    # Only one admin in the system; a SUPERADMIN actor (different user) tries to
    # deactivate it -> blocked.
    repo = _seed_one(["ADMIN"], user_id="only-admin")
    c = _client(repo, _SUPER, monkeypatch)
    r = c.delete("/api/v1/users/only-admin")
    assert r.status_code == 400, r.text
    assert "last active admin" in r.json()["detail"]
    assert repo.find_by_id("only-admin")["is_active"] is True


def test_can_deactivate_admin_when_another_exists(monkeypatch):
    repo = _seed_one(["ADMIN"], user_id="admin-a")
    repo.create(
        {"user_id": "admin-b", "roles": ["ADMIN"], "is_active": True, "username": "b"}
    )
    c = _client(repo, _SUPER, monkeypatch)
    r = c.delete("/api/v1/users/admin-a")
    assert r.status_code == 200, r.text
    assert repo.find_by_id("admin-a")["is_active"] is False


def test_admin_cannot_deactivate_superadmin(monkeypatch):
    repo = _seed_one(["SUPERADMIN"], user_id="su-target")
    c = _client(repo, _ADMIN, monkeypatch)
    r = c.delete("/api/v1/users/su-target")
    assert r.status_code == 403, r.text


def test_cannot_remove_users_only_role(monkeypatch):
    repo = _seed_one(["SALES_STAFF"])
    c = _client(repo, _SUPER, monkeypatch)
    r = c.delete("/api/v1/users/u1/roles/SALES_STAFF")
    assert r.status_code == 400, r.text
    assert "only role" in r.json()["detail"]


def test_cannot_remove_last_admin_role(monkeypatch):
    # user has ADMIN + STORE_MANAGER; removing ADMIN would leave zero admins.
    repo = _seed_one(["ADMIN", "STORE_MANAGER"], user_id="only-admin")
    c = _client(repo, _SUPER, monkeypatch)
    r = c.delete("/api/v1/users/only-admin/roles/ADMIN")
    assert r.status_code == 400, r.text
    assert "last active admin" in r.json()["detail"]


def test_can_remove_role_when_multiple_present(monkeypatch):
    repo = _seed_one(["STORE_MANAGER", "SALES_STAFF"])
    c = _client(repo, _SUPER, monkeypatch)
    r = c.delete("/api/v1/users/u1/roles/SALES_STAFF")
    assert r.status_code == 200, r.text
    assert repo.find_by_id("u1")["roles"] == ["STORE_MANAGER"]


def test_admin_cannot_remove_superadmin_role(monkeypatch):
    repo = _seed_one(["SUPERADMIN", "ADMIN"], user_id="su")
    c = _client(repo, _ADMIN, monkeypatch)
    r = c.delete("/api/v1/users/su/roles/SUPERADMIN")
    assert r.status_code == 403, r.text


# ===========================================================================
# reset-password floor
# ===========================================================================


def test_reset_password_rejects_short(monkeypatch):
    repo = _seed_one(["SALES_STAFF"])
    c = _client(repo, _SUPER, monkeypatch)
    r = c.post("/api/v1/users/u1/reset-password", json={"new_password": "abc12"})
    assert r.status_code == 422, r.text  # below 8


def test_reset_password_ok(monkeypatch):
    repo = _seed_one(["SALES_STAFF"])
    c = _client(repo, _SUPER, monkeypatch)
    r = c.post(
        "/api/v1/users/u1/reset-password", json={"new_password": "NewPass@123"}
    )
    assert r.status_code == 200, r.text
    assert repo.find_by_id("u1")["must_change_password"] is True


# ===========================================================================
# reset-to-TEMPORARY-password (server-generated, shown once, force-change)
# ===========================================================================


class _FakeAuditRepo:
    """Captures the rows written by audit_repo.create so a test can assert the
    PASSWORD_RESET row was written WITHOUT any plaintext temp value."""

    def __init__(self):
        self.rows = []

    def create(self, row):
        self.rows.append(dict(row))
        return dict(row)


def _client_with_audit(repo, actor, monkeypatch, audit_repo=None):
    """Like _client, but also wires a capturing audit repo so the audit row can
    be asserted. Returns (TestClient, audit_repo)."""
    audit_repo = audit_repo or _FakeAuditRepo()
    monkeypatch.setattr(users, "get_user_repository", lambda: repo)
    monkeypatch.setattr(users, "get_audit_repository", lambda: audit_repo)
    app = FastAPI()
    app.include_router(users.router, prefix="/api/v1/users")

    async def _u():
        return dict(actor)

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app), audit_repo


def test_generate_temp_password_is_strong_and_readable():
    # Pure helper: length floor, unambiguous alphabet, high uniqueness.
    p = user_roles.generate_temp_password()
    assert len(p) == 12
    # No visually-ambiguous chars (0/O, 1/l/I) -- safe to dictate over the phone.
    assert not (set(p) & set("0O1lI"))
    assert len(p.encode("utf-8")) <= user_roles.BCRYPT_MAX_BYTES
    # The floor is enforced even when a smaller length is requested.
    assert len(user_roles.generate_temp_password(4)) == 8
    # Two draws virtually never collide (cryptographic RNG, ~69 bits entropy).
    assert user_roles.generate_temp_password() != user_roles.generate_temp_password()


def test_reset_generates_temp_sets_flag_and_returns_once(monkeypatch):
    # The SECURE default: no password supplied -> server generates a temp,
    # force-flags must_change_password, returns the temp ONCE, and the stored
    # hash verifies against the returned temp (the user could log in with it).
    from api.routers.auth import verify_password

    repo = _seed_one(["SALES_STAFF"])
    c, audit = _client_with_audit(repo, _SUPER, monkeypatch)
    r = c.post("/api/v1/users/u1/reset-password", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    temp = body["temporary_password"]
    assert isinstance(temp, str) and len(temp) >= 8
    assert body["must_change_password"] is True
    assert body["username"] == "u1"

    stored = repo.find_by_id("u1")
    assert stored["must_change_password"] is True
    # The plaintext temp is NEVER persisted -- only its bcrypt hash, which the
    # returned temp verifies against (so the user can log in with it).
    assert "password" not in stored or stored.get("password") != temp
    assert verify_password(temp, stored["password_hash"]) is True


def test_reset_with_no_body_also_generates_temp(monkeypatch):
    # Calling with an empty/absent body still works (new_password is optional).
    repo = _seed_one(["SALES_STAFF"])
    c, _ = _client_with_audit(repo, _SUPER, monkeypatch)
    r = c.post("/api/v1/users/u1/reset-password")
    assert r.status_code == 200, r.text
    assert r.json().get("temporary_password")


def test_reset_audit_row_written_without_temp_value(monkeypatch):
    # An immutable PASSWORD_RESET audit row is written naming actor + target, and
    # the temp plaintext appears NOWHERE in that row.
    repo = _seed_one(["SALES_STAFF"])
    c, audit = _client_with_audit(repo, _SUPER, monkeypatch)
    r = c.post("/api/v1/users/u1/reset-password", json={})
    assert r.status_code == 200, r.text
    temp = r.json()["temporary_password"]

    reset_rows = [a for a in audit.rows if a.get("action") == "PASSWORD_RESET"]
    assert len(reset_rows) == 1
    row = reset_rows[0]
    assert row["target_user_id"] == "u1"
    assert row["user_id"] == _SUPER["user_id"]
    # The temp must not leak into ANY audit field.
    assert temp not in repr(row)


def test_admin_cannot_reset_superadmin(monkeypatch):
    # ROLE-ESCALATION GUARD: an ADMIN may not reset a SUPERADMIN -> 403, and the
    # target's password is untouched.
    repo = _seed_one(["SUPERADMIN"], user_id="su-target")
    c, _ = _client_with_audit(repo, _ADMIN, monkeypatch)
    before = dict(repo.find_by_id("su-target"))
    r = c.post("/api/v1/users/su-target/reset-password", json={})
    assert r.status_code == 403, r.text
    assert repo.find_by_id("su-target") == before  # unchanged


def test_superadmin_can_reset_admin(monkeypatch):
    # A SUPERADMIN may reset anyone, including an ADMIN.
    repo = _seed_one(["ADMIN"], user_id="admin-target")
    c, _ = _client_with_audit(repo, _SUPER, monkeypatch)
    r = c.post("/api/v1/users/admin-target/reset-password", json={})
    assert r.status_code == 200, r.text
    assert r.json().get("temporary_password")


def test_admin_can_reset_lower_role(monkeypatch):
    # An ADMIN may reset a user at or below their level (a SALES_STAFF).
    repo = _seed_one(["SALES_STAFF"])
    c, _ = _client_with_audit(repo, _ADMIN, monkeypatch)
    r = c.post("/api/v1/users/u1/reset-password", json={})
    assert r.status_code == 200, r.text


def test_below_role_caller_cannot_reset(monkeypatch):
    # A SALES_STAFF actor is not ADMIN/SUPERADMIN -> require_admin 403s before any
    # reset logic runs.
    repo = _seed_one(["SALES_STAFF"])
    actor = {"user_id": "ss-1", "roles": ["SALES_STAFF"], "store_ids": ["S1"]}
    c, _ = _client_with_audit(repo, actor, monkeypatch)
    r = c.post("/api/v1/users/u1/reset-password", json={})
    assert r.status_code == 403, r.text


def test_reset_disabled_user_is_allowed(monkeypatch):
    # Edge: resetting a DISABLED user is allowed (re-enabling is a separate op).
    repo = _seed_one(["SALES_STAFF"], active=False)
    c, _ = _client_with_audit(repo, _SUPER, monkeypatch)
    r = c.post("/api/v1/users/u1/reset-password", json={})
    assert r.status_code == 200, r.text
    assert r.json().get("temporary_password")
    # The reset does NOT silently re-enable the account.
    assert repo.find_by_id("u1")["is_active"] is False


def test_supplied_password_path_does_not_echo_plaintext(monkeypatch):
    # Backward-compat: an explicit new_password still works but its plaintext is
    # NEVER returned (no disclosure oracle).
    repo = _seed_one(["SALES_STAFF"])
    c, _ = _client_with_audit(repo, _SUPER, monkeypatch)
    r = c.post(
        "/api/v1/users/u1/reset-password", json={"new_password": "Chosen@123"}
    )
    assert r.status_code == 200, r.text
    assert "temporary_password" not in r.json()
    assert repo.find_by_id("u1")["must_change_password"] is True


def test_reset_missing_user_404(monkeypatch):
    repo = _seed_one(["SALES_STAFF"])
    c, _ = _client_with_audit(repo, _SUPER, monkeypatch)
    r = c.post("/api/v1/users/nope/reset-password", json={})
    assert r.status_code == 404, r.text
