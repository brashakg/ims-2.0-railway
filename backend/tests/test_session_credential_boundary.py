"""
One session test at every door that accepts a session token
===========================================================
Codex audit 2026-11-04, reproduced through the real app (real /auth/login,
real tokens, real refresh), no forged signatures:

  R17     a logged-out access token traded at legacy /auth/refresh for a new session
  R18     a device-enrolment ticket traded at legacy /auth/refresh for a session
  L1-F42  a logged-out token re-spelled (padding / unused base64 bits) is accepted again
  R24     the admin password-reset door leaves the user's open logins usable
  R25     the forced first-login password change is enforced by the screen only
  L1-F25  the WhatsApp inbox decodes the bearer itself and accepts a logged-out token
"""

from __future__ import annotations

import base64
import os
import sys
from types import SimpleNamespace

import mongomock
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

from api.routers import auth as auth_module  # noqa: E402
from api.routers import users as users_module  # noqa: E402
from api.routers import webhooks as webhooks_module  # noqa: E402
from api.routers.auth import hash_password  # noqa: E402
from api.services.device_gate import mint_enroll_ticket  # noqa: E402

PW = "Good@Pass1"
TEMP = "Temp@1234"
NEW = "Brand@New9"
_ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


class _Audit:
    def create(self, row):
        return dict(row)


def _user(user_id, username, roles, password, **extra):
    doc = {
        "user_id": user_id, "username": username, "full_name": username.title(),
        "email": f"{username}@example.test", "password_hash": hash_password(password),
        "roles": roles, "store_ids": ["BV-TEST-01"], "is_active": True,
    }
    doc.update(extra)
    return doc


@pytest.fixture
def world(monkeypatch):
    coll = mongomock.MongoClient().db.users
    coll.insert_many([
        _user("ad-1", "admin1", ["ADMIN"], PW),
        _user("sm-1", "manager1", ["STORE_MANAGER"], PW),
        _user("st-1", "staff1", ["SALES_STAFF"], PW),
        _user("nw-1", "newbie", ["SALES_STAFF"], TEMP, must_change_password=True),
    ])

    def _update(uid, data):
        return coll.update_one({"user_id": uid}, {"$set": data}).matched_count == 1

    repo = SimpleNamespace(
        collection=coll, find_by_id=lambda uid: coll.find_one({"user_id": uid}), update=_update
    )
    monkeypatch.setattr("api.dependencies.get_user_repository", lambda: repo)
    monkeypatch.setattr(users_module, "get_user_repository", lambda: repo)
    monkeypatch.setattr(users_module, "get_audit_repository", lambda: _Audit())
    monkeypatch.setattr(webhooks_module, "_get_wa_conversations_collection", lambda: None)
    return SimpleNamespace(coll=coll)


def _session(client, username, password=PW):
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    body = r.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body


def _me(client, token):
    return client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})


def _legacy_refresh(client, token):
    return client.post("/api/v1/auth/refresh", json={"token": token})


def _respellings(token):
    """Equivalent spellings of the SAME signature bytes: one padded form and the
    three variants of the unused low bits in the last character."""
    head, payload, sig = token.split(".")
    raw = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
    out = [f"{head}.{payload}.{sig}{'=' * (-len(sig) % 4)}"]
    idx = _ALPHA.index(sig[-1])
    unused = 6 - (len(raw) * 8) % 6  # bits of the last char that carry nothing
    for flip in range(1, 1 << unused):
        out.append(f"{head}.{payload}.{sig[:-1]}{_ALPHA[idx ^ flip]}")
    for t in out:
        s = t.split(".")[2]
        assert base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)) == raw
        assert t != token
    return out


# -- R17 / L1-F42: logout ends the session at every door, in every spelling --

def test_logged_out_token_cannot_be_refreshed_by_legacy_door(client, world):
    headers, body = _session(client, "staff1")
    token = body["access_token"]
    assert _me(client, token).status_code == 200
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
    assert _me(client, token).status_code == 401
    r = _legacy_refresh(client, token)
    assert r.status_code == 401, r.text
    assert "access_token" not in r.text


def test_legacy_refresh_refused_when_chain_ended_even_if_blacklist_missed(client, world):
    """Another worker took the logout and the shared cache lost the blacklist
    entry: the ended refresh chain (Mongo/store) still refuses the exchange."""
    headers, body = _session(client, "staff1")
    token = body["access_token"]
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
    auth_module._token_blacklist._revoked.clear()
    from api.services.cache import cache

    import hashlib

    cache.delete(auth_module.TokenBlacklist._PREFIX + hashlib.sha256(token.encode()).hexdigest())
    assert _legacy_refresh(client, token).status_code == 401


def test_live_token_legacy_refresh_still_works(client, world):
    _, body = _session(client, "staff1")
    r = _legacy_refresh(client, body["access_token"])
    assert r.status_code == 200, r.text
    assert _me(client, r.json()["access_token"]).status_code == 200


def test_respelled_logged_out_token_is_refused_everywhere(client, world):
    headers, body = _session(client, "manager1")
    token = body["access_token"]
    variants = _respellings(token)
    assert len(variants) == 4
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
    for v in variants:
        assert _me(client, v).status_code == 401, v[-6:]
        assert _legacy_refresh(client, v).status_code == 401
        assert client.get(
            "/api/v1/webhooks/whatsapp/conversations", headers={"Authorization": f"Bearer {v}"}
        ).status_code == 401


def test_only_the_issued_spelling_is_a_credential(client, world):
    _, body = _session(client, "manager1")
    assert _me(client, body["access_token"]).status_code == 200
    for v in _respellings(body["access_token"]):
        assert _me(client, v).status_code == 401


# -- R18: a purpose ticket is never a session at any door --------------------

def test_enrolment_ticket_cannot_become_a_session(client, world):
    ticket = mint_enroll_ticket("st-1", "staff1")
    assert _me(client, ticket).status_code == 401
    r = _legacy_refresh(client, ticket)
    assert r.status_code == 401, r.text
    assert "access_token" not in r.text
    assert client.get(
        "/api/v1/webhooks/whatsapp/conversations", headers={"Authorization": f"Bearer {ticket}"}
    ).status_code == 401


# -- R24: every password change/reset door ends the other logins -------------

def test_admin_reset_signs_out_the_users_open_logins(client, world):
    admin, _ = _session(client, "admin1")
    staff_headers, staff = _session(client, "staff1")
    r = client.post("/api/v1/users/st-1/reset-password", headers=admin)
    assert r.status_code == 200, r.text

    assert _me(client, staff["access_token"]).status_code == 401
    rot = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": staff["refresh_token"], "token": staff["access_token"]},
    )
    assert rot.status_code == 401, rot.text
    assert _legacy_refresh(client, staff["access_token"]).status_code == 401
    # The admin's own session is untouched.
    assert client.get("/api/v1/auth/me", headers=admin).status_code == 200


def test_admin_resetting_own_password_keeps_this_login(client, world):
    admin, _ = _session(client, "admin1")
    _, other = _session(client, "admin1")
    assert client.post("/api/v1/users/ad-1/reset-password", headers=admin).status_code == 200
    assert client.get("/api/v1/auth/me", headers=admin).status_code == 200
    assert _me(client, other["access_token"]).status_code == 401


def test_self_change_keeps_this_login_and_ends_the_others(client, world):
    here, _ = _session(client, "staff1")
    _, elsewhere = _session(client, "staff1")
    r = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": PW, "new_password": NEW},
        headers=here,
    )
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/auth/me", headers=here).status_code == 200
    assert _me(client, elsewhere["access_token"]).status_code == 401
    assert _legacy_refresh(client, elsewhere["access_token"]).status_code == 401


# -- R25: the temporary password gates the API, not only the screen ----------

def test_temporary_password_session_reaches_only_the_change_door(client, world):
    headers, body = _session(client, "newbie", TEMP)
    assert body["user"]["must_change_password"] is True
    blocked = client.post("/api/v1/auth/switch-store/BV-TEST-01", headers=headers)
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["code"] == "PASSWORD_CHANGE_REQUIRED"
    assert client.get(
        "/api/v1/webhooks/whatsapp/conversations", headers=headers
    ).status_code == 403
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200

    r = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": TEMP, "new_password": NEW},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    # Same login, flag now cleared on the live record: work is open.
    assert client.post("/api/v1/auth/switch-store/BV-TEST-01", headers=headers).status_code == 200


def test_temporary_password_session_can_still_sign_out(client, world):
    headers, _ = _session(client, "newbie", TEMP)
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200


# -- L1-F25: the WhatsApp inbox uses the shared session check -----------------

def test_whatsapp_inbox_refuses_a_logged_out_token(client, world):
    headers, _ = _session(client, "manager1")
    url = "/api/v1/webhooks/whatsapp/conversations"
    assert client.get(url, headers=headers).status_code == 200
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
    assert client.get(url, headers=headers).status_code == 401


def test_whatsapp_inbox_keeps_its_role_gate(client, world):
    headers, _ = _session(client, "staff1")
    assert client.get("/api/v1/webhooks/whatsapp/conversations", headers=headers).status_code == 403
