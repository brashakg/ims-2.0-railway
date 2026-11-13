"""
Editing a user's username and email (User Management > Edit user)
=================================================================
Owner report 2026-11-13: changing a user's username and email showed
"saved successfully" but nothing changed. PUT /users/{id} dropped both fields
silently (UserUpdate had neither, and pydantic ignores unknown keys), and the
frontend never sent them either.

These pin the server side: both fields persist (trimmed + lower-cased, the form
login looks them up in), a value another user already holds is refused
case-insensitively, re-sending the user's own value is fine, and a malformed
email is rejected instead of silently ignored.
"""

from __future__ import annotations

import re

from test_user_role_guards import _ADMIN, _FakeColl, _FakeUserRepo, _client


class _CiColl(_FakeColl):
    """The fake collection, taught the anchored case-insensitive regex lookup
    the router issues for ANY field (username as well as email)."""

    def find_one(self, query):
        for field, q in query.items():
            if isinstance(q, dict) and "$regex" in q:
                pat = re.compile(q["$regex"], re.IGNORECASE)
                for d in self._docs.values():
                    if d.get(field) and pat.match(d[field]):
                        return dict(d)
                return None
        return super().find_one(query)


def _repo():
    repo = _FakeUserRepo(
        [
            {"user_id": "u1", "username": "ravi", "email": "ravi@example.com",
             "full_name": "Ravi", "roles": ["SALES_STAFF"], "store_ids": ["S1"],
             "is_active": True},
            {"user_id": "u2", "username": "sunita", "email": "sunita@example.com",
             "full_name": "Sunita", "roles": ["SALES_STAFF"], "store_ids": ["S1"],
             "is_active": True},
        ]
    )
    repo.collection = _CiColl(repo._docs)
    return repo


def test_username_and_email_edit_is_saved(monkeypatch):
    repo = _repo()
    c = _client(repo, _ADMIN, monkeypatch)
    r = c.put("/api/v1/users/u1",
              json={"username": "  Ravi.Kumar ", "email": "Ravi.Kumar@Example.com"})
    assert r.status_code == 200, r.text
    doc = repo.find_by_id("u1")
    assert doc["username"] == "ravi.kumar"
    assert doc["email"] == "ravi.kumar@example.com"


def test_username_held_by_another_user_is_refused(monkeypatch):
    repo = _repo()
    c = _client(repo, _ADMIN, monkeypatch)
    r = c.put("/api/v1/users/u1", json={"username": "SUNITA"})
    assert r.status_code == 400, r.text
    assert "username" in r.text.lower()
    assert repo.find_by_id("u1")["username"] == "ravi"


def test_email_held_by_another_user_is_refused(monkeypatch):
    repo = _repo()
    c = _client(repo, _ADMIN, monkeypatch)
    r = c.put("/api/v1/users/u1", json={"email": "Sunita@Example.com"})
    assert r.status_code == 400, r.text
    assert "email" in r.text.lower()
    assert repo.find_by_id("u1")["email"] == "ravi@example.com"


def test_resending_own_username_and_email_is_not_a_clash(monkeypatch):
    repo = _repo()
    c = _client(repo, _ADMIN, monkeypatch)
    r = c.put("/api/v1/users/u1",
              json={"username": "Ravi", "email": "RAVI@example.com", "full_name": "Ravi K"})
    assert r.status_code == 200, r.text
    doc = repo.find_by_id("u1")
    assert (doc["username"], doc["email"], doc["full_name"]) == (
        "ravi", "ravi@example.com", "Ravi K")


def test_malformed_email_or_short_username_is_rejected(monkeypatch):
    repo = _repo()
    c = _client(repo, _ADMIN, monkeypatch)
    assert c.put("/api/v1/users/u1", json={"email": "not-an-email"}).status_code == 422
    assert c.put("/api/v1/users/u1", json={"username": "ab"}).status_code == 422
    assert c.put("/api/v1/users/u1", json={"username": "has space"}).status_code == 422
    doc = repo.find_by_id("u1")
    assert (doc["username"], doc["email"]) == ("ravi", "ravi@example.com")


def test_an_unrelated_edit_never_rechecks_an_existing_legacy_login(monkeypatch):
    """The edit form always re-sends the username and email it loaded. A user
    stored with a legacy value (an email the strict check would refuse, or a
    mixed-case username) must still be editable, and is not rewritten."""
    repo = _repo()
    repo._docs["u1"].update({"username": "Ravi K", "email": "Ravi@Shop.local"})
    c = _client(repo, _ADMIN, monkeypatch)
    r = c.put("/api/v1/users/u1",
              json={"username": "Ravi K", "email": "Ravi@Shop.local", "phone": "9876543210"})
    assert r.status_code == 200, r.text
    assert "username" not in repo.last_update and "email" not in repo.last_update
    doc = repo.find_by_id("u1")
    assert (doc["username"], doc["email"]) == ("Ravi K", "Ravi@Shop.local")


def test_a_case_only_twin_is_left_alone_not_a_500(monkeypatch):
    """Create only refused exact duplicates, so "Ravi" and "ravi" can coexist.
    Editing "Ravi" while re-sending its own name must not try to write "ravi"
    (that hits the unique index and answers 500)."""
    repo = _repo()
    repo._docs["u1"]["username"] = "Ravi"
    repo._docs["u3"] = {"user_id": "u3", "username": "ravi", "email": "r3@example.com",
                        "roles": ["SALES_STAFF"], "store_ids": ["S1"], "is_active": True}
    c = _client(repo, _ADMIN, monkeypatch)
    r = c.put("/api/v1/users/u1", json={"username": "ravi", "full_name": "Ravi One"})
    assert r.status_code == 200, r.text
    assert "username" not in repo.last_update
    assert repo.find_by_id("u1")["username"] == "Ravi"


def test_a_login_rename_leaves_an_audit_row(monkeypatch):
    import api.routers.users as users_mod

    rows = []

    class _Audit:
        def create(self, row):
            rows.append(row)
            return row

    monkeypatch.setattr(users_mod, "get_audit_repository", lambda: _Audit())
    repo = _repo()
    c = _client(repo, _ADMIN, monkeypatch)
    assert c.put("/api/v1/users/u1", json={"email": "ravi.new@example.com"}).status_code == 200
    login_rows = [r for r in rows if r.get("action") == "USER_LOGIN_CHANGED"]
    assert len(login_rows) == 1
    assert login_rows[0]["email"] == {"from": "ravi@example.com", "to": "ravi.new@example.com"}
    assert login_rows[0]["target_user_id"] == "u1"
    # a plain edit leaves no login row
    rows.clear()
    assert c.put("/api/v1/users/u1", json={"full_name": "R"}).status_code == 200
    assert not [r for r in rows if r.get("action") == "USER_LOGIN_CHANGED"]
