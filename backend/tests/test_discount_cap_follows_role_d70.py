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
from database.repositories.audit_repository import AuditRepository
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


def test_a_refused_change_is_marked_not_applied_and_cannot_be_reverted_to(monkeypatch):
    """Round 2: A's PERMISSIONS_UPDATE row (cap 10 -> 0) is written before its
    compare-and-set loses. The trail must say it never landed, and Revert must
    not re-apply that phantom state over B's real 15."""
    repo = _repo()
    audit = AuditRepository(mongomock.MongoClient().db.audit_logs)
    c, _ = _client_with_audit(repo, _SUPER, monkeypatch, audit_repo=audit)
    uid = _create(c, "auto", ["SALES_STAFF"])
    stale = repo.find_by_id(uid)
    assert c.put(f"/api/v1/users/{uid}", json={"discount_cap": 15}).status_code == 200
    real_find = repo.find_by_id
    monkeypatch.setattr(repo, "find_by_id", lambda i: dict(stale) if i == uid else real_find(i))
    assert c.put(f"/api/v1/users/{uid}", json={"roles": ["ACCOUNTANT"]}).status_code == 409
    monkeypatch.setattr(repo, "find_by_id", real_find)
    hist = c.get(f"/api/v1/users/{uid}/permissions").json()["history"]
    phantom = [h for h in hist if h["action"] == "PERMISSIONS_UPDATE"
               and h["permissions_after"]["discount_cap"] == 0.0]
    assert len(phantom) == 1 and phantom[0].get("not_applied") is True
    aborted = [h for h in hist if h["action"] == "PERMISSIONS_UPDATE_ABORTED"]
    assert len(aborted) == 1 and aborted[0]["aborts_log_id"] == phantom[0]["log_id"]
    assert aborted[0]["permissions_after"]["discount_cap"] == 10.0  # nothing changed
    # The real change (10 -> 15) stays a live, revertible row.
    assert not [h for h in hist if h.get("not_applied") and h is not phantom[0]]
    r = c.post(f"/api/v1/users/{uid}/permissions/revert", json={"audit_log_id": phantom[0]["log_id"]})
    assert r.status_code == 409, r.text
    assert _stored(repo, uid) == (15.0, False)


def test_role_side_doors_move_a_marked_cap_with_an_audit_row(monkeypatch):
    """Round 2: POST/DELETE /users/{id}/roles/{role} and assign-store with a
    role changed roles without D70. Codex's exact sequence: Sales Staff (10,
    marked) + ACCOUNTANT, then - SALES_STAFF must leave an Accountant at 0."""
    repo = _repo()
    c, audit = _client_with_audit(repo, _SUPER, monkeypatch)
    uid = _create(c, "auto", ["SALES_STAFF"])
    assert c.post(f"/api/v1/users/{uid}/roles/ACCOUNTANT").status_code == 200
    assert _stored(repo, uid) == (10.0, True)
    assert c.delete(f"/api/v1/users/{uid}/roles/SALES_STAFF").status_code == 200
    d = repo.find_by_id(uid)
    assert (d["roles"], d["discount_cap"], d["discount_cap_is_role_default"]) == (
        ["ACCOUNTANT"], 0.0, True)
    moves = [(r["permissions_before"]["discount_cap"], r["permissions_after"]["discount_cap"])
             for r in audit.rows if r["action"] == "PERMISSIONS_UPDATE"]
    assert moves == [(10.0, 0.0)]
    r = c.post(f"/api/v1/users/{uid}/assign-store", json={"store_id": "S2", "role": "STORE_MANAGER"})
    assert r.status_code == 200, r.text
    d = repo.find_by_id(uid)
    assert ("STORE_MANAGER" in d["roles"], d["discount_cap"], "S2" in d["store_ids"]) == (
        True, 20.0, True)
    # A typed cap is still kept through the side door.
    typed = _create(c, "typed", ["SALES_STAFF"], cap=15)
    assert c.post(f"/api/v1/users/{typed}/roles/ACCOUNTANT").status_code == 200
    assert c.delete(f"/api/v1/users/{typed}/roles/SALES_STAFF").status_code == 200
    assert _stored(repo, typed) == (15.0, False)


def test_list_read_failure_is_an_error_not_an_empty_page(monkeypatch):
    """Round 2: find_many swallowed a Mongo error into [] -> 200 [] -> the
    screen took it as the last page / 'No users found'."""
    from pymongo.errors import ServerSelectionTimeoutError

    repo = _repo()
    repo.collection.insert_one({"user_id": "u1", "username": "u1", "roles": ["SALES_STAFF"],
                                "store_ids": ["S1"], "is_active": True})
    c, _ = _client_with_audit(repo, _SUPER, monkeypatch)

    def boom(*a, **k):
        raise ServerSelectionTimeoutError("down")

    monkeypatch.setattr(repo.collection, "find", boom)
    for skip in (0, 100):
        r = c.get("/api/v1/users", params={"skip": skip, "limit": 100})
        assert r.status_code == 503, (skip, r.status_code, r.text)
    monkeypatch.setattr(users, "get_user_repository", lambda: None)
    assert c.get("/api/v1/users").status_code == 503


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


# --- Round 3 (independent reviewers, 2026-11-16) ---------------------------


def _stale_reads(monkeypatch, repo, uid, stale):
    real_find = repo.find_by_id
    monkeypatch.setattr(repo, "find_by_id", lambda i: dict(stale) if i == uid else real_find(i))
    return real_find


def test_two_stale_role_changes_cannot_mix_roles_and_cap(monkeypatch):
    """A adds STORE_MANAGER (moves the marked cap 10 -> 20); B, from the same
    stale read, adds WORKSHOP_STAFF (no cap move). B must not write its roles
    over A's: otherwise a Sales Staff keeps the Store Manager's 20, marked as
    the role standard."""
    repo = _repo()
    c, _ = _client_with_audit(repo, _SUPER, monkeypatch)
    uid = _create(c, "auto", ["SALES_STAFF"])
    stale = repo.find_by_id(uid)
    real_find = _stale_reads(monkeypatch, repo, uid, stale)
    assert c.post(f"/api/v1/users/{uid}/roles/STORE_MANAGER").status_code == 200  # A
    r = c.post(f"/api/v1/users/{uid}/roles/WORKSHOP_STAFF")  # B, stale
    assert r.status_code == 409, r.text
    d = real_find(uid)
    assert (d["roles"], d["discount_cap"], d["discount_cap_is_role_default"]) == (
        ["SALES_STAFF", "STORE_MANAGER"], 20.0, True)
    # Same race through the PUT door.
    r = c.put(f"/api/v1/users/{uid}", json={"roles": ["SALES_STAFF", "WORKSHOP_STAFF"]})
    assert r.status_code == 409, r.text
    assert real_find(uid)["roles"] == ["SALES_STAFF", "STORE_MANAGER"]


def _ack_lost_after_commit(monkeypatch, repo):
    """update_one commits, then the reply is lost (driver raises)."""
    from pymongo.errors import AutoReconnect

    real = repo.collection.update_one

    def lossy(*a, **k):
        real(*a, **k)
        raise AutoReconnect("reply lost after commit")

    monkeypatch.setattr(repo.collection, "update_one", lossy)
    return real


def _history(c, uid):
    return c.get(f"/api/v1/users/{uid}/permissions").json()["history"]


def test_lost_ack_on_a_landed_role_change_is_not_marked_not_applied(monkeypatch):
    repo = _repo()
    audit = AuditRepository(mongomock.MongoClient().db.audit_logs)
    c, _ = _client_with_audit(repo, _SUPER, monkeypatch, audit_repo=audit)
    uid = _create(c, "auto", ["SALES_STAFF"])
    real = _ack_lost_after_commit(monkeypatch, repo)
    r = c.put(f"/api/v1/users/{uid}", json={"roles": ["ACCOUNTANT"]})
    assert r.status_code == 200, r.text
    monkeypatch.setattr(repo.collection, "update_one", real)
    assert _stored(repo, uid) == (0.0, True)
    hist = _history(c, uid)
    moved = [h for h in hist if h["action"] == "PERMISSIONS_UPDATE"
             and h["permissions_after"]["discount_cap"] == 0.0]
    assert len(moved) == 1 and not moved[0].get("not_applied")
    assert not [h for h in hist if h["action"] == "PERMISSIONS_UPDATE_ABORTED"]


def _two_cap_edits(c, uid):
    """Cap 12 then 15; returns the log id of the 12 row (the revert target)."""
    for cap in (12, 15):
        assert c.put(f"/api/v1/users/{uid}", json={"discount_cap": cap}).status_code == 200
    return [h for h in _history(c, uid)
            if h["permissions_after"].get("discount_cap") == 12.0][0]["log_id"]


def test_a_failed_revert_is_marked_not_applied(monkeypatch):
    """The revert row is written before the write; a write that never lands
    must not leave it standing as a real, revertible state."""
    repo = _repo()
    audit = AuditRepository(mongomock.MongoClient().db.audit_logs)
    c, _ = _client_with_audit(repo, _SUPER, monkeypatch, audit_repo=audit)
    uid = _create(c, "auto", ["SALES_STAFF"])
    target = _two_cap_edits(c, uid)
    monkeypatch.setattr(repo, "update", lambda *a, **k: False)  # nothing lands
    r = c.post(f"/api/v1/users/{uid}/permissions/revert", json={"audit_log_id": target})
    assert r.status_code == 500, r.text
    assert _stored(repo, uid) == (15.0, False)
    hist = _history(c, uid)
    rev = [h for h in hist if h["action"] == "PERMISSIONS_REVERT"]
    assert len(rev) == 1 and rev[0].get("not_applied") is True


def test_lost_ack_on_a_landed_revert_is_success(monkeypatch):
    repo = _repo()
    audit = AuditRepository(mongomock.MongoClient().db.audit_logs)
    c, _ = _client_with_audit(repo, _SUPER, monkeypatch, audit_repo=audit)
    uid = _create(c, "auto", ["SALES_STAFF"])
    target = _two_cap_edits(c, uid)
    real = _ack_lost_after_commit(monkeypatch, repo)
    r = c.post(f"/api/v1/users/{uid}/permissions/revert", json={"audit_log_id": target})
    assert r.status_code == 200, r.text
    monkeypatch.setattr(repo.collection, "update_one", real)
    assert _stored(repo, uid) == (12.0, False)
    hist = _history(c, uid)
    assert not [h for h in hist if h.get("not_applied")]
