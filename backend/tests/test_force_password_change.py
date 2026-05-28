"""
IMS 2.0 - Force-change-password-on-first-login tests
=====================================================

An admin creates a staff user with a TEMPORARY password (or resets one); the
user must be forced to change it on first login. The backend surfaces the
`must_change_password` flag so the frontend can gate the app, and clears the
flag once the user changes their password.

Covered behaviour:
  1. /auth/login returns `must_change_password: true` for a flagged user and
     embeds it in the JWT.
  2. /auth/login returns `must_change_password: false` (default) for a normal
     user, even when the field is absent from the DB record.
  3. /auth/change-password verifies the current password, updates the hash, and
     CLEARS `must_change_password` on the user doc.
  4. After the change, a fresh /auth/login no longer forces the change.
  5. /auth/me echoes the flag from the token (survives a refresh).
  6. The JWT minted by /auth/refresh preserves the flag.

These exercise the real FastAPI app via TestClient with an in-memory fake user
repository (no external DB), patched onto api.dependencies.get_user_repository
(both auth.py and users.py import it lazily from there).
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Ensure the app can import (auth.py raises if JWT_SECRET_KEY is unset).
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

from api.routers.auth import hash_password, decode_token  # noqa: E402


class _FakeUserCollection:
    """Just enough of a Mongo collection for login + change-password:
    find_one (by username/email/user_id/_id) and update_one ($set)."""

    def __init__(self, docs):
        # docs: list of user dicts
        self.docs = docs

    def find_one(self, flt):
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in flt.items()):
                # Return the live object so update_one mutations are visible
                # (matches how the router reads then writes by _id).
                return doc
        return None

    def update_one(self, flt, upd):
        doc = self.find_one(flt)

        class _R:
            matched_count = 1 if doc else 0
            modified_count = 0

        if doc is not None and "$set" in upd:
            doc.update(upd["$set"])
            _R.modified_count = 1
        return _R()


class _FakeUserRepo:
    """Mirrors the slice of UserRepository the auth/users routers use."""

    def __init__(self, docs):
        self.collection = _FakeUserCollection(docs)

    def find_by_id(self, user_id):
        return self.collection.find_one({"user_id": user_id})


def _make_user(**overrides):
    user = {
        "user_id": "u-staff-1",
        "_id": "u-staff-1",
        "username": "newstaff",
        "email": "newstaff@bettervision.in",
        "full_name": "New Staff",
        "password_hash": hash_password("Temp@1234"),
        "roles": ["SALES_STAFF"],
        "store_ids": ["BV-TEST-01"],
        "is_active": True,
    }
    user.update(overrides)
    return user


@pytest.fixture
def patched_repo(monkeypatch):
    """Install a fresh fake user repo for each test and return it so the test
    can inspect the doc after mutations."""
    repo_holder = {}

    def _install(docs):
        repo = _FakeUserRepo(docs)
        repo_holder["repo"] = repo
        monkeypatch.setattr(
            "api.dependencies.get_user_repository", lambda: repo, raising=True
        )
        return repo

    return _install


# ============================================================================
# Login surfaces the flag
# ============================================================================


def test_login_surfaces_must_change_password_true(client, patched_repo):
    """A flagged user gets must_change_password=true in the response AND token."""
    user = _make_user(must_change_password=True)
    patched_repo([user])

    r = client.post(
        "/api/v1/auth/login",
        json={"username": "newstaff", "password": "Temp@1234"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["must_change_password"] is True

    # The JWT must also carry the flag so it survives a refresh.
    claims = decode_token(body["access_token"])
    assert claims.get("must_change_password") is True


def test_login_defaults_flag_false_when_absent(client, patched_repo):
    """A normal user without the field set logs in with the flag defaulted to
    false -- the gate must NOT trip for existing users."""
    user = _make_user()  # no must_change_password key
    assert "must_change_password" not in user
    patched_repo([user])

    r = client.post(
        "/api/v1/auth/login",
        json={"username": "newstaff", "password": "Temp@1234"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["must_change_password"] is False
    claims = decode_token(body["access_token"])
    assert claims.get("must_change_password") is False


# ============================================================================
# change-password clears the flag
# ============================================================================


def test_change_password_clears_flag_and_unblocks(client, patched_repo):
    """After a successful change-password, the flag is cleared on the doc and a
    fresh login no longer forces the change."""
    user = _make_user(must_change_password=True)
    repo = patched_repo([user])

    # Log in with the temporary password to obtain a token.
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "newstaff", "password": "Temp@1234"},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    assert login.json()["user"]["must_change_password"] is True

    # Change the password.
    chg = client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "Temp@1234", "new_password": "Brand@New99"},
    )
    assert chg.status_code == 200, chg.text

    # The flag is now cleared on the persisted doc...
    assert repo.collection.docs[0]["must_change_password"] is False
    # ...and the password hash actually changed (new password verifies).
    from api.routers.auth import verify_password

    assert verify_password("Brand@New99", repo.collection.docs[0]["password_hash"])

    # A fresh login with the new password no longer forces the change.
    relogin = client.post(
        "/api/v1/auth/login",
        json={"username": "newstaff", "password": "Brand@New99"},
    )
    assert relogin.status_code == 200, relogin.text
    assert relogin.json()["user"]["must_change_password"] is False


def test_change_password_rejects_wrong_current(client, patched_repo):
    """A wrong current password is rejected and the flag stays set (still
    blocked)."""
    user = _make_user(must_change_password=True)
    repo = patched_repo([user])
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "newstaff", "password": "Temp@1234"},
    )
    token = login.json()["access_token"]

    chg = client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "WrongOld@1", "new_password": "Brand@New99"},
    )
    assert chg.status_code == 400, chg.text
    # Flag untouched -> user is still forced to change on next login.
    assert repo.collection.docs[0].get("must_change_password") is True


# ============================================================================
# /auth/me + /auth/refresh propagate the flag
# ============================================================================


def test_me_echoes_flag_from_token(client, patched_repo):
    user = _make_user(must_change_password=True)
    patched_repo([user])
    token = client.post(
        "/api/v1/auth/login",
        json={"username": "newstaff", "password": "Temp@1234"},
    ).json()["access_token"]

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    assert me.json().get("must_change_password") is True


def test_refresh_preserves_flag(client, patched_repo):
    user = _make_user(must_change_password=True)
    patched_repo([user])
    token = client.post(
        "/api/v1/auth/login",
        json={"username": "newstaff", "password": "Temp@1234"},
    ).json()["access_token"]

    refreshed = client.post("/api/v1/auth/refresh", json={"token": token})
    assert refreshed.status_code == 200, refreshed.text
    new_token = refreshed.json()["access_token"]
    assert decode_token(new_token).get("must_change_password") is True
