"""
L2-INTEGRATION-F02 - the Shiprocket password saved in the app reaches the login
================================================================================
Audit reproduction: POST /admin/integrations/shiprocket stores the password as
``fernet:<token>`` (correct, BUG-155), and shiprocket._creds_from_integrations
read the raw document - so the adapter logged in to Shiprocket with the
CIPHERTEXT as the password. Every booking / tracking call on app-saved
credentials failed to authenticate (the env-var path was unaffected).

The reader now goes through THE decrypting reader the other providers use
(nexus_providers._load_integration_config -> cred_crypto.decrypt_config). A
password that cannot be decrypted (the key changed since it was saved) is a
visible configuration error: nothing is sent, and a live booking FAILS rather
than passing as a dry run.

Both save doors (Admin and Settings) -> the real adapter -> a stub login. No
network, no real credentials; no secret appears in a log line.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

import mongomock  # noqa: E402
import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import admin as admin_router  # noqa: E402
from api.routers import settings as settings_router  # noqa: E402
from api.services import cred_crypto, shiprocket  # noqa: E402
from agents.nexus_providers import shiprocket_track_awb  # noqa: E402

ADMIN = {"user_id": "u-admin", "username": "admin", "roles": ["ADMIN"]}
EMAIL = "ops@example.test"
PW_1 = "Synthetic-Pass-One!"
PW_2 = "Synthetic-Pass-Two!"


class _Resp:
    def __init__(self, code, payload):
        self.status_code = code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


@pytest.fixture()
def env(monkeypatch):
    """A mongomock database both save doors write to, and a stub Shiprocket
    that records every login body it is sent."""
    db = mongomock.MongoClient()["shiprocket_saved_password"]
    coll = db.get_collection("integrations")
    monkeypatch.setattr(admin_router, "_get_integrations_collection", lambda: coll)
    monkeypatch.setattr(settings_router, "_get_settings_collection", lambda name: db.get_collection(name))
    monkeypatch.setattr(settings_router, "get_audit_repository", lambda: None, raising=False)
    monkeypatch.delenv("SHIPROCKET_EMAIL", raising=False)
    monkeypatch.delenv("SHIPROCKET_PASSWORD", raising=False)
    shiprocket._reset_token_cache()

    logins = []

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kwargs):
            if url.endswith("/auth/login"):
                logins.append(kwargs["json"])
                return _Resp(200, {"token": f"tok-{len(logins)}"})
            return _Resp(200, {"awb_code": "AWB777", "shipment_id": 5, "order_id": 9})

        async def get(self, url, **kwargs):
            return _Resp(200, {"tracking_data": {"shipment_track": [{"current_status": "In Transit"}]}})

    monkeypatch.setattr(shiprocket.httpx, "AsyncClient", _Client)
    yield db, coll, logins
    shiprocket._reset_token_cache()


def _save_admin(password):
    asyncio.run(
        admin_router.set_shiprocket_config(
            admin_router.ShiprocketConfig(email=EMAIL, password=password), current_user=ADMIN
        )
    )


def _save_settings(password):
    asyncio.run(
        settings_router.update_integration(
            "shiprocket",
            settings_router.IntegrationConfig(
                integration_type="shiprocket",
                enabled=True,
                config={"email": EMAIL, "password": password},
            ),
            ADMIN,
        )
    )


@pytest.mark.parametrize("save", [_save_admin, _save_settings], ids=["admin", "settings"])
def test_saved_password_reaches_the_login_as_typed(env, save, caplog):
    db, coll, logins = env
    caplog.set_level(logging.DEBUG)
    save(PW_1)

    stored = coll.find_one({"type": "shiprocket"})["config"]["password"]
    assert stored.startswith("fernet:") and PW_1 not in stored  # encrypted at rest

    # The audit's exact check: the adapter's reader returned the stored value.
    creds = shiprocket._creds_from_integrations(db)
    assert creds["email"] == EMAIL
    assert creds["password"] == PW_1
    assert creds["password"] != stored
    assert shiprocket.credentials_present(db) is True

    res = asyncio.run(shiprocket.authenticate(db))
    assert res.ok is True and res.raw["token"] == "tok-1"
    assert logins == [{"email": EMAIL, "password": PW_1}]  # never the ciphertext

    # The background tracking poll (NEXUS) logs in through the same reader.
    polled = asyncio.run(shiprocket_track_awb(db, "AWB1"))
    assert polled.ok is True and polled.payload["latest_status"] == "In Transit"
    assert logins[-1] == {"email": EMAIL, "password": PW_1}

    logged = caplog.text
    assert PW_1 not in logged and stored not in logged


def test_a_changed_password_logs_in_again(env):
    """Rotation: a token got on the old password is not reused for the new one
    (the cache was keyed by e-mail alone)."""
    db, _coll, logins = env
    _save_admin(PW_1)
    assert asyncio.run(shiprocket.authenticate(db)).raw["token"] == "tok-1"
    assert asyncio.run(shiprocket.authenticate(db)).raw["token"] == "tok-1"  # cached
    assert len(logins) == 1

    _save_settings(PW_2)
    assert asyncio.run(shiprocket.authenticate(db)).raw["token"] == "tok-2"
    assert logins[-1] == {"email": EMAIL, "password": PW_2}
    assert len(logins) == 2


def test_a_password_that_cannot_be_decrypted_is_never_sent(env, monkeypatch, caplog):
    """Saved under another key (CREDENTIAL_ENCRYPTION_KEY rotated): decrypt
    hands the ciphertext back. That is a configuration error staff can see,
    not a password - and not a dry run either."""
    db, coll, logins = env
    caplog.set_level(logging.DEBUG)
    _save_admin(PW_1)
    stored = coll.find_one({"type": "shiprocket"})["config"]["password"]
    monkeypatch.setattr(cred_crypto, "decrypt_value", lambda value: value)  # the key no longer fits

    creds = shiprocket._creds_from_integrations(db)
    assert creds["password"] == "" and "Settings" in creds["error"]
    assert shiprocket.credentials_present(db) is False

    auth = asyncio.run(shiprocket.authenticate(db))
    assert (auth.ok, auth.status) == (False, "FAILED") and "Settings" in auth.error

    monkeypatch.setattr(shiprocket, "dispatch_mode", lambda: "live")
    booked = asyncio.run(
        shiprocket.create_shipment(
            {"order_id": "ORD-1", "items": [], "grand_total": 100.0}, {"pincode": "834001"}, db
        )
    )
    assert (booked.ok, booked.status, booked.awb) == (False, "FAILED", None)
    assert "Settings" in booked.error

    tracked = asyncio.run(shiprocket.track(awb="AWB1", db=db))
    assert (tracked.ok, tracked.status) == (False, "FAILED")

    polled = asyncio.run(shiprocket_track_awb(db, "AWB1"))  # the background tracking poll
    assert polled.ok is False and "Settings" in polled.error

    assert logins == []  # nothing was sent anywhere
    assert stored not in caplog.text and stored not in str(auth) + str(booked) + str(tracked)


def test_legacy_plaintext_and_env_credentials_still_work(env, monkeypatch):
    db, coll, logins = env
    coll.insert_one({"type": "shiprocket", "enabled": True,
                     "config": {"email": EMAIL, "password": PW_1}})  # a pre-BUG-155 row
    assert shiprocket._resolve_credentials(db) == {"email": EMAIL, "password": PW_1}

    monkeypatch.setenv("SHIPROCKET_EMAIL", "env@example.test")
    monkeypatch.setenv("SHIPROCKET_PASSWORD", PW_2)
    assert shiprocket._resolve_credentials(db) == {"email": "env@example.test", "password": PW_2}
    assert shiprocket._resolve_credentials(None) == {"email": "env@example.test", "password": PW_2}
