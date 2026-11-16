"""D70 (#1424): a discount cap filled in by the role standard follows the role.

Codex round 1 (2026-11-15): Sales Staff created without an override stored 10;
changing the role to Accountant kept 10 as an effective override (the person
could still give 10% discount as an Accountant). Decision D70: a cap set by the
role default is MARKED when stored and follows the new role's standard on a
role change; an unmarked cap (legacy or typed) is kept unless the editor
explicitly picks the new role's standard. Intentional overrides are never
rewritten silently, and a concurrent deliberate edit is never overwritten.

Also pinned here: the permissions editor gets the role-standard table from the
server (Admin 100, Accountant+Sales 10 -- max over roles, never the highest
role's preset row) and GET /users pages in a stable order so the screen can
read every page.

Real UserRepository on a mongomock collection (the compare-and-set is the real
update_one filter), real users router over HTTP.
"""

from __future__ import annotations

import mongomock

from api.routers import users
from api.services.role_caps import ROLE_DISCOUNT_CAPS
from database.repositories.user_repository import UserRepository
from test_user_role_guards import _SUPER, _client_with_audit

_NEW = {
    "email": "d70@example.com",
    "password": "Welcome@123",
    "full_name": "D Seventy",
    "store_ids": ["S1"],
}


def _repo():
    return UserRepository(mongomock.MongoClient().db.users)


def _create(c, username, roles, cap=None):
    body = dict(_NEW, username=username, email=f"{username}@example.com", roles=roles)
    if cap is not None:
        body["discount_cap"] = cap
    r = c.post("/api/v1/users", json=body)
    assert r.status_code == 201, r.text
    return r.json()["user_id"]


def _stored(repo, uid):
    d = repo.find_by_id(uid)
    return d.get("discount_cap"), d.get("discount_cap_is_role_default")


def test_create_marks_a_role_default_cap_and_not_a_typed_one(monkeypatch):
    repo = _repo()
    c, _ = _client_with_audit(repo, _SUPER, monkeypatch)
    assert _stored(repo, _create(c, "auto", ["SALES_STAFF"])) == (10.0, True)
    assert _stored(repo, _create(c, "typed", ["SALES_STAFF"], cap=15)) == (15.0, False)


def test_role_default_cap_follows_the_new_role_and_retry_is_stable(monkeypatch):
    repo = _repo()
    c, _ = _client_with_audit(repo, _SUPER, monkeypatch)
    uid = _create(c, "auto", ["SALES_STAFF"])
    for _ in range(2):  # identical retry / lost acknowledgement replay
        r = c.put(f"/api/v1/users/{uid}", json={"roles": ["ACCOUNTANT"]})
        assert r.status_code == 200, r.text
        assert _stored(repo, uid) == (0.0, True)
        detail = c.get(f"/api/v1/users/{uid}").json()
        assert detail["effective_discount_cap"] == 0.0
        assert detail["discount_cap_is_role_default"] is True
    # And back up: Store Manager standard.
    assert c.put(f"/api/v1/users/{uid}", json={"roles": ["STORE_MANAGER"]}).status_code == 200
    assert _stored(repo, uid) == (20.0, True)


def test_typed_and_legacy_caps_are_kept_on_a_role_change(monkeypatch):
    repo = _repo()
    c, _ = _client_with_audit(repo, _SUPER, monkeypatch)
    typed = _create(c, "typed", ["SALES_STAFF"], cap=15)
    repo.collection.insert_one({"user_id": "legacy", "username": "legacy", "email": "l@example.com",
                                "roles": ["SALES_STAFF"], "store_ids": ["S1"], "discount_cap": 10.0,
                                "is_active": True})  # written by older code: no marker
    for uid, cap in ((typed, 15.0), ("legacy", 10.0)):
        assert c.put(f"/api/v1/users/{uid}", json={"roles": ["ACCOUNTANT"]}).status_code == 200
        d = repo.find_by_id(uid)
        assert d["discount_cap"] == cap
        assert not d.get("discount_cap_is_role_default")


def test_editor_can_switch_a_kept_cap_to_the_role_standard(monkeypatch):
    repo = _repo()
    c, _ = _client_with_audit(repo, _SUPER, monkeypatch)
    uid = _create(c, "typed", ["SALES_STAFF"], cap=15)
    r = c.put(f"/api/v1/users/{uid}", json={"roles": ["ACCOUNTANT"], "discount_cap_follow_role": True})
    assert r.status_code == 200, r.text
    assert _stored(repo, uid) == (0.0, True)
    # Sending both a cap and "use the standard" is ambiguous: refused, nothing written.
    r = c.put(f"/api/v1/users/{uid}", json={"discount_cap": 5, "discount_cap_follow_role": True})
    assert r.status_code == 422
    assert _stored(repo, uid) == (0.0, True)


def test_a_typed_cap_edit_unmarks_so_the_next_role_change_keeps_it(monkeypatch):
    repo = _repo()
    c, _ = _client_with_audit(repo, _SUPER, monkeypatch)
    uid = _create(c, "auto", ["SALES_STAFF"])
    assert c.put(f"/api/v1/users/{uid}", json={"discount_cap": 12}).status_code == 200
    assert _stored(repo, uid) == (12.0, False)
    assert c.put(f"/api/v1/users/{uid}", json={"roles": ["ACCOUNTANT"]}).status_code == 200
    assert _stored(repo, uid) == (12.0, False)


def test_concurrent_deliberate_edit_is_not_overwritten(monkeypatch):
    """Request A read the row while it was still a marked default; request B
    then typed 15. A's role change must not replace B's 15 with A's standard."""
    repo = _repo()
    c, _ = _client_with_audit(repo, _SUPER, monkeypatch)
    uid = _create(c, "auto", ["SALES_STAFF"])
    stale = repo.find_by_id(uid)  # A's read
    assert c.put(f"/api/v1/users/{uid}", json={"discount_cap": 15}).status_code == 200  # B
    real_find = repo.find_by_id
    monkeypatch.setattr(repo, "find_by_id", lambda i: dict(stale) if i == uid else real_find(i))
    r = c.put(f"/api/v1/users/{uid}", json={"roles": ["ACCOUNTANT"]})
    assert r.status_code == 409, r.text
    d = real_find(uid)
    assert (d["discount_cap"], d["discount_cap_is_role_default"], d["roles"]) == (
        15.0, False, ["SALES_STAFF"])


def test_editor_options_carry_the_role_standard_table(monkeypatch):
    c, _ = _client_with_audit(_repo(), _SUPER, monkeypatch)
    opts = c.get("/api/v1/users/permissions/options").json()
    assert opts["role_discount_caps"] == ROLE_DISCOUNT_CAPS
    caps = opts["role_discount_caps"]
    assert max(caps.get(r, 0) for r in ["ACCOUNTANT", "SALES_STAFF"]) == 10
    assert caps["ADMIN"] == 100


def test_list_pages_are_complete_and_disjoint(monkeypatch):
    repo = _repo()
    repo.collection.insert_many([
        {"user_id": f"u{i:03d}", "username": f"u{i:03d}", "roles": ["SALES_STAFF"],
         "store_ids": ["S1"], "is_active": True} for i in range(130)
    ])
    c, _ = _client_with_audit(repo, _SUPER, monkeypatch)
    seen = []
    for skip in (0, 100, 200):
        rows = c.get("/api/v1/users", params={"skip": skip, "limit": 100}).json()
        seen += [r["user_id"] for r in rows]
    assert len(seen) == 130 and len(set(seen)) == 130


def test_projection_carries_the_marker():
    assert "discount_cap_is_role_default" in users._SANITIZED_USER_FIELDS
