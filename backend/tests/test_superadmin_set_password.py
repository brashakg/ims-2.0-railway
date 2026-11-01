"""
SUPERADMIN sets any user's password (owner ask 2026-11-01)
==========================================================
POST /api/v1/users/{user_id}/set-password -- SUPERADMIN only. The user KEEPS
the password (no forced change) and every OTHER open login of theirs is signed
out: their old access tokens 401 on the next request and their refresh chains
are revoked. The actor's own session survives (also when setting their own
password). One PASSWORD_SET audit row; the password value is never stored,
echoed or audited.

End-to-end through the real app: real /auth/login, real tokens, real refresh.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import mongomock
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

from api.routers import users as users_module  # noqa: E402
from api.routers.auth import hash_password, verify_password  # noqa: E402

SUPER_PW = "Super@1234"
STAFF_PW = "Temp@1234"
NEW_PW = "Brand@New9"


class _Audit:
    def __init__(self):
        self.rows = []

    def create(self, row):
        self.rows.append(dict(row))
        return dict(row)


def _user(user_id, username, roles, password, **extra):
    doc = {
        "user_id": user_id,
        "username": username,
        "email": f"{username}@bettervision.in",
        "full_name": username.title(),
        "password_hash": hash_password(password),
        "roles": roles,
        "store_ids": ["BV-TEST-01"],
        "is_active": True,
    }
    doc.update(extra)
    return doc


@pytest.fixture
def world(monkeypatch):
    coll = mongomock.MongoClient().db.users
    coll.insert_many([
        _user("su-1", "boss", ["SUPERADMIN"], SUPER_PW),
        _user("su-2", "boss2", ["SUPERADMIN"], SUPER_PW),
        _user("ad-1", "admin1", ["ADMIN"], SUPER_PW),
        _user("st-1", "staff1", ["SALES_STAFF"], STAFF_PW, must_change_password=True),
    ])

    def _update(uid, data):
        return coll.update_one({"user_id": uid}, {"$set": data}).matched_count == 1

    repo = SimpleNamespace(
        collection=coll,
        find_by_id=lambda uid: coll.find_one({"user_id": uid}),
        update=_update,
    )
    audit = _Audit()
    monkeypatch.setattr("api.dependencies.get_user_repository", lambda: repo)
    monkeypatch.setattr(users_module, "get_user_repository", lambda: repo)
    monkeypatch.setattr(users_module, "get_audit_repository", lambda: audit)
    return SimpleNamespace(coll=coll, audit=audit)


def _login(client, username, password):
    return client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )


def _session(client, username, password):
    r = _login(client, username, password)
    assert r.status_code == 200, r.text
    body = r.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body


def _set(client, headers, target, password=NEW_PW):
    return client.post(
        f"/api/v1/users/{target}/set-password",
        json={"new_password": password},
        headers=headers,
    )


def _me(client, headers):
    return client.get("/api/v1/auth/me", headers=headers)


def _refresh(client, body):
    return client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": body["refresh_token"], "token": body["access_token"]},
    )


def test_superadmin_sets_password_user_keeps_it(client, world):
    boss, _ = _session(client, "boss", SUPER_PW)
    r = _set(client, boss, "st-1")
    assert r.status_code == 200, r.text
    assert NEW_PW not in r.text  # never echoed

    assert _login(client, "staff1", STAFF_PW).status_code == 401  # old one dead
    ok = _login(client, "staff1", NEW_PW)
    assert ok.status_code == 200, ok.text
    assert ok.json()["user"]["must_change_password"] is False  # no forced change

    doc = world.coll.find_one({"user_id": "st-1"})
    assert doc["must_change_password"] is False
    assert doc["password_set_by"] == "su-1"
    assert doc.get("password_changed_at")
    assert verify_password(NEW_PW, doc["password_hash"])
    assert NEW_PW not in repr(doc)


@pytest.mark.parametrize("target", ["su-2", "st-1"])
def test_superadmin_can_set_another_superadmin_and_a_disabled_user(
    client, world, target
):
    world.coll.update_one({"user_id": "st-1"}, {"$set": {"is_active": False}})
    boss, _ = _session(client, "boss", SUPER_PW)
    before = world.coll.find_one({"user_id": target})
    assert _set(client, boss, target).status_code == 200
    doc = world.coll.find_one({"user_id": target})
    assert verify_password(NEW_PW, doc["password_hash"])
    assert doc["password_hash"] != before["password_hash"]
    assert doc["must_change_password"] is False
    assert doc["token_version"] == int(before.get("token_version") or 0) + 1


@pytest.mark.parametrize("actor,pw", [("admin1", SUPER_PW), ("staff1", STAFF_PW)])
def test_non_superadmin_is_refused(client, world, actor, pw):
    headers, _ = _session(client, actor, pw)
    before = world.coll.find_one({"user_id": "st-1"})["password_hash"]
    r = _set(client, headers, "st-1")
    assert r.status_code == 403, r.text
    assert world.coll.find_one({"user_id": "st-1"})["password_hash"] == before


@pytest.mark.parametrize("bad", ["Short7!", "a" * 73, "é" * 40])
def test_too_short_or_too_long_is_refused_without_echo(client, world, bad):
    boss, _ = _session(client, "boss", SUPER_PW)
    before = world.coll.find_one({"user_id": "st-1"})["password_hash"]
    r = _set(client, boss, "st-1", bad)
    assert r.status_code in (400, 422), r.text
    assert bad not in r.text
    assert world.coll.find_one({"user_id": "st-1"})["password_hash"] == before


def test_targets_open_logins_are_signed_out_actor_survives(client, world):
    staff, staff_body = _session(client, "staff1", STAFF_PW)
    assert _me(client, staff).status_code == 200
    boss, boss_body = _session(client, "boss", SUPER_PW)

    assert _set(client, boss, "st-1").status_code == 200

    assert _me(client, staff).status_code == 401  # old access token rejected
    assert _refresh(client, staff_body).status_code == 401  # chain revoked
    legacy = client.post(
        "/api/v1/auth/refresh", json={"token": staff_body["access_token"]}
    )
    assert legacy.status_code == 401  # legacy refresh cannot resurrect it

    assert _me(client, boss).status_code == 200  # actor untouched
    assert _refresh(client, boss_body).status_code == 200

    fresh, _ = _session(client, "staff1", NEW_PW)  # a new login works
    assert _me(client, fresh).status_code == 200


def test_setting_own_password_keeps_this_session_signs_out_others(client, world):
    this, this_body = _session(client, "boss", SUPER_PW)
    other, other_body = _session(client, "boss", SUPER_PW)

    assert _set(client, this, "su-1").status_code == 200

    assert _me(client, this).status_code == 200
    renewed = _refresh(client, this_body)
    assert renewed.status_code == 200, renewed.text
    renewed_headers = {"Authorization": f"Bearer {renewed.json()['access_token']}"}
    assert _me(client, renewed_headers).status_code == 200

    assert _me(client, other).status_code == 401
    assert _refresh(client, other_body).status_code == 401


def test_one_audit_row_without_the_password(client, world):
    boss, _ = _session(client, "boss", SUPER_PW)
    assert _set(client, boss, "st-1").status_code == 200
    rows = [r for r in world.audit.rows if r.get("action") == "PASSWORD_SET"]
    assert len(rows) == 1
    row = rows[0]
    assert row["user_id"] == "su-1"
    assert row["target_user_id"] == "st-1"
    assert row.get("timestamp")
    assert NEW_PW not in repr(row)


def test_replaying_a_just_rotated_refresh_token_after_set_is_refused(client, world):
    # A token rotated < 60s ago sits in the multi-tab grace window; the set
    # must still kill it (and the whole chain).
    _, staff_body = _session(client, "staff1", STAFF_PW)
    r1 = _refresh(client, staff_body)
    assert r1.status_code == 200, r1.text
    boss, _ = _session(client, "boss", SUPER_PW)
    assert _set(client, boss, "st-1").status_code == 200

    assert _refresh(client, staff_body).status_code == 401  # grace replay
    assert _refresh(client, r1.json()).status_code == 401  # newest link too


@pytest.mark.parametrize("lookup_first", [False, True])
def test_refresh_racing_the_set_cannot_keep_the_chain_alive(
    client, world, monkeypatch, lookup_first
):
    # The set's writes land between consume() and issue() of a rotation:
    # whichever side of the live-user lookup they fall on, nothing minted from
    # that chain may outlive the set.
    from api.routers import auth as auth_module
    from api.services.refresh_tokens import refresh_token_store

    _, staff_body = _session(client, "staff1", STAFF_PW)
    real_lookup = auth_module._lookup_live_user

    def _set_effects():
        world.coll.update_one({"user_id": "st-1"}, {"$inc": {"token_version": 1}})
        refresh_token_store.revoke_user_families("st-1", reason="password_set")

    def racing_lookup(user_id, username):
        if lookup_first:
            live = real_lookup(user_id, username)
            _set_effects()
            return live
        _set_effects()
        return real_lookup(user_id, username)

    monkeypatch.setattr(auth_module, "_lookup_live_user", racing_lookup)
    raced = _refresh(client, staff_body)
    monkeypatch.setattr(auth_module, "_lookup_live_user", real_lookup)

    if raced.status_code == 200:
        body = raced.json()
        headers = {"Authorization": f"Bearer {body['access_token']}"}
        assert _me(client, headers).status_code == 401
        assert _refresh(client, body).status_code == 401
    else:
        assert raced.status_code == 401


def test_a_failed_password_write_is_not_reported_as_done(client, world, monkeypatch):
    staff, staff_body = _session(client, "staff1", STAFF_PW)
    boss, _ = _session(client, "boss", SUPER_PW)
    repo = users_module.get_user_repository()
    monkeypatch.setattr(repo, "update", lambda uid, data: False)

    r = _set(client, boss, "st-1")
    assert r.status_code == 500, r.text
    assert not [x for x in world.audit.rows if x.get("action") == "PASSWORD_SET"]
    assert _me(client, staff).status_code == 200  # nothing was signed out
    assert _refresh(client, staff_body).status_code == 200
