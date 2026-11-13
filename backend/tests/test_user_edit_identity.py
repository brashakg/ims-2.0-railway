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
