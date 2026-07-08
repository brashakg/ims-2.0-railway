"""
IMS 2.0 - Rotating refresh-token tests (2026-07 token hardening)
=================================================================

Access tokens are now short-lived (45 min default) and the 8h working session
survives via a SINGLE-USE rotating refresh token persisted server-side
(api/services/refresh_tokens.py). These tests lock the security contract:

  1. /auth/login returns an access+refresh PAIR (additive fields) and the JWT
     carries the session anchors (sess_start + sid).
  2. /auth/refresh with a refresh token ROTATES it: the old one dies, a new
     pair comes back (refresh_mode "rotating").
  3. Reusing a consumed refresh token (outside the multi-tab grace) trips the
     stolen-token CANARY: the whole chain is revoked and later refreshes 401.
  4. The multi-tab grace window lets two tabs race one rotation without
     nuking the session.
  5. The ABSOLUTE 8h cap holds: no chain of refreshes extends a session past
     first-login + REFRESH_ABSOLUTE_HOURS, and access-token exp is capped at
     the session end.
  6. DEPRECATED legacy refresh (a still-valid ACCESS token in `token`) keeps
     working for the deploy window, is flagged refresh_mode "legacy_access",
     and upgrades the caller to a rotating pair. Pre-deploy 8h tokens stay
     valid until natural expiry (JWT validation path unchanged).
  7. A disabled user is blocked at refresh (rotating mode) and the chain is
     revoked.
  8. /auth/logout revokes the refresh chain AND still blacklists the access
     token (existing behaviour unaffected).
  9. /auth/switch-store preserves the session anchors so a store switch can't
     mint an uncapped token or orphan the chain from logout revocation.

Style follows test_force_password_change.py: real FastAPI app via TestClient,
in-memory fake user repository patched onto api.dependencies.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

from api.routers import auth as auth_module  # noqa: E402
from api.routers.auth import (  # noqa: E402
    create_access_token,
    decode_token,
    hash_password,
)
from api.services import refresh_tokens as rt_module  # noqa: E402
from api.services.refresh_tokens import (  # noqa: E402
    hash_refresh_token,
    refresh_token_store,
)


class _FakeUserCollection:
    """Just enough of a Mongo collection for login + refresh: find_one (by
    username/email/user_id/_id) and update_one ($set)."""

    def __init__(self, docs):
        self.docs = docs

    def find_one(self, flt):
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in flt.items()):
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
    def __init__(self, docs):
        self.collection = _FakeUserCollection(docs)

    def find_by_id(self, user_id):
        return self.collection.find_one({"user_id": user_id})


def _make_user(**overrides):
    user = {
        "user_id": "u-rot-1",
        "_id": "u-rot-1",
        "username": "rotstaff",
        "email": "rotstaff@bettervision.in",
        "full_name": "Rotation Staff",
        "password_hash": hash_password("Temp@1234"),
        "roles": ["SALES_STAFF"],
        "store_ids": ["BV-TEST-01", "BV-TEST-02"],
        "is_active": True,
    }
    user.update(overrides)
    return user


@pytest.fixture
def patched_repo(monkeypatch):
    def _install(docs):
        repo = _FakeUserRepo(docs)
        monkeypatch.setattr(
            "api.dependencies.get_user_repository", lambda: repo, raising=True
        )
        return repo

    return _install


def _login(client, username="rotstaff", password="Temp@1234"):
    r = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert r.status_code == 200, r.text
    return r.json()


def _backdate_session(refresh_token_plain: str, hours: float) -> None:
    """Rewind a refresh token's session so the absolute cap can be tested
    without sleeping. Touches BOTH storages (Mongo on CI, mem locally)."""
    h = hash_refresh_token(refresh_token_plain)
    new_start = datetime.utcnow() - timedelta(hours=hours)
    new_end = new_start + timedelta(hours=rt_module.REFRESH_ABSOLUTE_HOURS)
    update = {"absolute_session_start": new_start, "expires_at": new_end}
    coll = refresh_token_store._coll()
    if coll is not None:
        coll.update_many({"token_hash": h}, {"$set": dict(update)})
    doc = refresh_token_store._mem.get(h)
    if doc is not None:
        doc.update(update)


# ============================================================================
# 1. Login returns the pair + session anchors
# ============================================================================


def test_login_returns_rotating_pair_and_session_claims(client, patched_repo):
    patched_repo([_make_user()])
    body = _login(client)

    # Additive pair fields.
    assert body["access_token"]
    assert body["refresh_token"], "login must return a rotating refresh token"
    assert body["expires_in"] == auth_module.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    assert (
        body["refresh_expires_in"] == auth_module.REFRESH_ABSOLUTE_HOURS * 3600
    )
    # Response back-compat: the pre-existing fields are all still present.
    assert body["token_type"] == "bearer"
    assert body["user"]["username"] == "rotstaff"
    assert body["user"]["store_ids"] == ["BV-TEST-01", "BV-TEST-02"]

    # JWT anchors: sess_start (absolute cap) + sid (chain id for logout).
    claims = decode_token(body["access_token"])
    assert isinstance(claims.get("sess_start"), int)
    assert claims.get("sid")
    # Store-scoped claims unchanged.
    assert claims["active_store_id"] == "BV-TEST-01"
    assert claims["store_ids"] == ["BV-TEST-01", "BV-TEST-02"]


# ============================================================================
# 2 + 3. Rotation is single-use; reuse revokes the whole chain (canary)
# ============================================================================


def test_refresh_rotates_and_reuse_revokes_chain(client, patched_repo, monkeypatch):
    # Grace 0 so the reuse canary fires immediately (no multi-tab tolerance).
    monkeypatch.setattr(rt_module, "REUSE_GRACE_SECONDS", 0)
    patched_repo([_make_user()])
    body = _login(client)
    old_refresh = body["refresh_token"]

    # First refresh: rotates.
    r1 = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": old_refresh, "token": body["access_token"]},
    )
    assert r1.status_code == 200, r1.text
    b1 = r1.json()
    assert b1["refresh_mode"] == "rotating"
    assert b1["access_token"]
    assert b1["refresh_token"] and b1["refresh_token"] != old_refresh
    # Same chain: sid is preserved across the rotation.
    assert decode_token(b1["access_token"])["sid"] == decode_token(
        body["access_token"]
    )["sid"]

    # Reusing the consumed token = stolen-token canary -> 401...
    r2 = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert r2.status_code == 401, r2.text

    # ...and the WHOLE chain is dead: the freshly rotated token 401s too.
    r3 = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": b1["refresh_token"]}
    )
    assert r3.status_code == 401, r3.text


def test_multi_tab_grace_allows_racing_double_refresh(client, patched_repo):
    """Two tabs firing the same proactive refresh within the grace window must
    BOTH survive (default grace 60s) instead of nuking the session."""
    patched_repo([_make_user()])
    body = _login(client)

    r1 = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert r1.status_code == 200, r1.text
    r2 = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert r2.status_code == 200, r2.text
    # Both tabs hold live-looking pairs in the SAME chain.
    assert r2.json()["refresh_mode"] == "rotating"


def test_refresh_with_unknown_refresh_token_401(client, patched_repo):
    patched_repo([_make_user()])
    r = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": "not-a-real-token"}
    )
    assert r.status_code == 401, r.text


# ============================================================================
# 5. Absolute 8h cap
# ============================================================================


def test_absolute_cap_blocks_refresh_after_8h(client, patched_repo):
    """Even a perfectly valid, never-reused refresh token dies at the absolute
    cap -- continuous refreshing cannot extend the session."""
    patched_repo([_make_user()])
    body = _login(client)
    _backdate_session(body["refresh_token"], hours=rt_module.REFRESH_ABSOLUTE_HOURS + 1)

    r = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert r.status_code == 401, r.text


def test_access_token_exp_capped_at_session_end(client, patched_repo):
    """A refresh near the session end mints an access token that expires AT the
    cap, not 45 minutes past it -- and the cap survives further rotation.
    Budget: 10 min of session left (generous so slow local runs with Mongo
    connection stalls can't eat the window), still far below the 45-min access
    lifetime, so the assertion only passes if the cap really applied."""
    patched_repo([_make_user()])
    body = _login(client)
    # 10 minutes of session left.
    budget_sec = 600
    _backdate_session(
        body["refresh_token"],
        hours=rt_module.REFRESH_ABSOLUTE_HOURS - (budget_sec / 3600.0),
    )

    r = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert r.status_code == 200, r.text
    b = r.json()
    assert 0 < b["expires_in"] <= budget_sec, "access exp must be capped at session end"
    assert b["expires_in"] < auth_module.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    assert 0 < b["refresh_expires_in"] <= budget_sec

    # The rotated refresh token INHERITS the backdated session start: one more
    # rotation still cannot escape the cap.
    r2 = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": b["refresh_token"]}
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["expires_in"] <= budget_sec


# ============================================================================
# 6. Legacy access-token refresh (DEPRECATED back-compat) + pre-deploy tokens
# ============================================================================


def _predeploy_token(user, minutes=480):
    """A token exactly as the PRE-hardening backend minted it: 8h exp, no
    sess_start, no sid."""
    return create_access_token(
        {
            "user_id": user["user_id"],
            "username": user["username"],
            "roles": user["roles"],
            "store_ids": user["store_ids"],
            "active_store_id": user["store_ids"][0],
            "must_change_password": False,
            "module_access": {},
        },
        expires_delta=timedelta(minutes=minutes),
    )


def test_predeploy_8h_token_still_valid_on_me(client, patched_repo):
    """Tokens issued BEFORE the deploy keep working until natural expiry --
    the JWT validation path is unchanged for them."""
    user = _make_user()
    patched_repo([user])
    token = _predeploy_token(user)
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    assert me.json()["username"] == "rotstaff"


def test_legacy_access_token_refresh_still_works_and_is_flagged(
    client, patched_repo
):
    """A mid-shift user through the deploy: their old 8h access token refreshes
    via the legacy contract and receives the NEW rotating pair."""
    user = _make_user()
    patched_repo([user])
    old_token = _predeploy_token(user)

    r = client.post("/api/v1/auth/refresh", json={"token": old_token})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["refresh_mode"] == "legacy_access"
    assert b["access_token"]
    assert b["refresh_token"], "legacy refresh must upgrade to a rotating pair"

    claims = decode_token(b["access_token"])
    # Session start derived from the old token's 8h exp: the upgraded session
    # ends exactly when the old token would have.
    assert isinstance(claims.get("sess_start"), int)
    assert claims.get("sid")
    # Their remaining budget is at most the old token's remaining life.
    assert b["expires_in"] <= auth_module.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    assert b["refresh_expires_in"] <= 480 * 60

    # The OLD token itself is NOT revoked by refreshing (no surprise logout).
    me = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {old_token}"}
    )
    assert me.status_code == 200, me.text


def test_legacy_refresh_of_new_token_cannot_extend_past_cap(
    client, patched_repo
):
    """Chaining LEGACY refreshes on post-deploy tokens must not escape the
    absolute cap: sess_start is carried into every re-issued token."""
    patched_repo([_make_user()])
    body = _login(client)

    r = client.post("/api/v1/auth/refresh", json={"token": body["access_token"]})
    assert r.status_code == 200, r.text
    reissued = r.json()["access_token"]
    assert (
        decode_token(reissued)["sess_start"]
        == decode_token(body["access_token"])["sess_start"]
    )


def test_refresh_requires_some_token(client, patched_repo):
    patched_repo([_make_user()])
    r = client.post("/api/v1/auth/refresh", json={})
    assert r.status_code == 400, r.text


# ============================================================================
# 7. Live-record enforcement on the rotating path
# ============================================================================


def test_disabled_user_blocked_at_rotating_refresh(client, patched_repo):
    user = _make_user()
    repo = patched_repo([user])
    body = _login(client)
    repo.collection.docs[0]["is_active"] = False  # admin disables mid-session

    r = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert r.status_code == 403, r.text


def test_rotating_refresh_picks_up_role_change(client, patched_repo):
    user = _make_user(roles=["STORE_MANAGER"])
    repo = patched_repo([user])
    body = _login(client)
    repo.collection.docs[0]["roles"] = ["SALES_STAFF"]  # downgraded

    r = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert r.status_code == 200, r.text
    assert decode_token(r.json()["access_token"])["roles"] == ["SALES_STAFF"]


def test_rotating_refresh_preserves_active_store_from_access_token(
    client, patched_repo
):
    """Claim continuity: the (rider) access token's active_store_id survives
    the rotation -- store-scoped token claims unchanged."""
    patched_repo([_make_user()])
    body = _login(client)

    # Switch to the second store, then refresh with the switched token riding.
    sw = client.post(
        "/api/v1/auth/switch-store/BV-TEST-02",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert sw.status_code == 200, sw.text
    switched_token = sw.json()["access_token"]

    r = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": body["refresh_token"], "token": switched_token},
    )
    assert r.status_code == 200, r.text
    assert decode_token(r.json()["access_token"])["active_store_id"] == "BV-TEST-02"


# ============================================================================
# 8. Logout revokes the chain; blacklist behaviour unaffected
# ============================================================================


def test_logout_revokes_chain_and_blacklists_access(client, patched_repo):
    patched_repo([_make_user()])
    body = _login(client)
    headers = {"Authorization": f"Bearer {body['access_token']}"}

    out = client.post("/api/v1/auth/logout", headers=headers)
    assert out.status_code == 200, out.text

    # Existing behaviour: the access token is blacklisted.
    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 401, me.text

    # New behaviour: the refresh chain is revoked -- the session cannot be
    # resurrected with the leftover refresh token.
    r = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert r.status_code == 401, r.text


# ============================================================================
# 9. switch-store preserves the session anchors
# ============================================================================


def test_switch_store_preserves_session_anchors(client, patched_repo):
    patched_repo([_make_user()])
    body = _login(client)
    orig = decode_token(body["access_token"])

    sw = client.post(
        "/api/v1/auth/switch-store/BV-TEST-02",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert sw.status_code == 200, sw.text
    switched = decode_token(sw.json()["access_token"])
    assert switched["active_store_id"] == "BV-TEST-02"
    assert switched["sess_start"] == orig["sess_start"]
    assert switched["sid"] == orig["sid"]
