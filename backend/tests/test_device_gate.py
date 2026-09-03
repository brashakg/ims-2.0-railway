# ============================================================================
# Approved-device login gate (owner rulings 2026-09-02) - regression locks
# ============================================================================
# Staff sign in only on SUPERADMIN-approved devices. The suite proves, with a
# REAL WebAuthn verifier path (real EC keys, real signatures - no mocked
# crypto), that:
#
#   * the gate is DARK by default (DEVICE_GATE_MODE unset = login unchanged);
#   * ADMIN and SUPERADMIN are NEVER gated - the owner cannot be locked out
#     of the very screen that approves devices (the invariant);
#   * a device rejection never touches the rate limiter (no lockout oracle);
#   * the full enrol -> pending-still-blocked -> approve -> sign-in flow works,
#     approval being SUPERADMIN-only (an ADMIN cannot approve);
#   * challenges are single-use, wrong keys fail, revoked devices fail;
#   * the enrolment ticket is NOT a session token.
#
# No DB required: the user repo and the device collection are strict fakes.

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.serialization import (  # noqa: E402
    Encoding,
    PublicFormat,
)

from api import dependencies as api_dependencies  # noqa: E402
from api.routers import auth as auth_router  # noqa: E402
from api.routers.auth import LoginRateLimiter, create_access_token, hash_password  # noqa: E402
from api.services import device_gate  # noqa: E402
from strict_fakes import StrictCollection  # noqa: E402

RP_ID = "ims.test"
# One bcrypt hash shared by every fake user (bcrypt is slow; the PASSWORD is
# what each test presents, so this supplies no answers).
PASSWORD = "correct-horse-9"
_HASH = hash_password(PASSWORD)

USERS = [
    {
        "user_id": "u-staff",
        "username": "priya",
        "password_hash": _HASH,
        "roles": ["SALES_STAFF"],
        "store_ids": ["ST-01"],
        "is_active": True,
    },
    {
        "user_id": "u-admin",
        "username": "director",
        "password_hash": _HASH,
        "roles": ["ADMIN"],
        "store_ids": [],
        "is_active": True,
    },
    {
        "user_id": "u-owner",
        "username": "avinash",
        "password_hash": _HASH,
        "roles": ["SUPERADMIN"],
        "store_ids": [],
        "is_active": True,
    },
]


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@pytest.fixture()
def harness(monkeypatch):
    """TestClient over the real auth router (devices sub-router included),
    fake user repo + fake device collection, fresh rate limiter."""
    user_coll = StrictCollection("users", [dict(u) for u in USERS])
    repo = SimpleNamespace(collection=user_coll)
    monkeypatch.setattr(api_dependencies, "get_user_repository", lambda: repo)

    device_coll = StrictCollection("login_devices")
    monkeypatch.setattr(device_gate, "_collection", lambda: device_coll)

    limiter = LoginRateLimiter()
    monkeypatch.setattr(auth_router, "_login_limiter", limiter)

    monkeypatch.setenv("DEVICE_GATE_RP_ID", RP_ID)
    monkeypatch.delenv("DEVICE_GATE_MODE", raising=False)
    monkeypatch.delenv("DEVICE_GATE_ORIGINS", raising=False)

    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/v1/auth")
    client = TestClient(app)
    return SimpleNamespace(
        client=client, limiter=limiter, devices=device_coll, users=user_coll
    )


def _login(client, username, password=PASSWORD, assertion=None):
    body = {"username": username, "password": password}
    if assertion is not None:
        body["device_assertion"] = assertion
    return client.post("/api/v1/auth/login", json=body)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _auth_data(rp_id: str = RP_ID, flags: int = 0x01) -> bytes:
    return hashlib.sha256(rp_id.encode()).digest() + bytes([flags]) + b"\x00" * 4


class FakeAuthenticator:
    """A P-256 platform authenticator: real keys, real DER ECDSA signatures -
    exactly the bytes a browser would deliver, so the verifier under test is
    the production verifier, unmocked."""

    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = _b64url(os.urandom(16))

    def spki_b64(self) -> str:
        return _b64(
            self.key.public_key().public_bytes(
                Encoding.DER, PublicFormat.SubjectPublicKeyInfo
            )
        )

    def _client_data(self, ceremony: str, challenge_b64url: str) -> bytes:
        return json.dumps(
            {
                "type": ceremony,
                "challenge": challenge_b64url,
                "origin": f"https://{RP_ID}",
            }
        ).encode()

    def enroll_payload(self, ticket: str, options: dict, name="Front till") -> dict:
        cdj = self._client_data("webauthn.create", options["challenge"])
        return {
            "enroll_ticket": ticket,
            "challenge_id": options["challenge_id"],
            "credential_id": self.credential_id,
            "client_data_json": _b64(cdj),
            "public_key_spki": self.spki_b64(),
            "public_key_alg": -7,
            "authenticator_data": _b64(_auth_data()),
            "device_name": name,
            "platform": "test-suite",
        }

    def assertion(self, options: dict, key=None) -> dict:
        cdj = self._client_data("webauthn.get", options["challenge"])
        auth_data = _auth_data()
        signed = auth_data + hashlib.sha256(cdj).digest()
        signature = (key or self.key).sign(signed, ec.ECDSA(hashes.SHA256()))
        return {
            "challenge_id": options["challenge_id"],
            "credential_id": self.credential_id,
            "client_data_json": _b64(cdj),
            "authenticator_data": _b64(auth_data),
            "signature": _b64(signature),
        }


def _challenge(client) -> dict:
    r = client.post("/api/v1/auth/devices/assertion-options")
    assert r.status_code == 200, r.text
    return r.json()


def _superadmin_headers():
    tok = create_access_token(
        {
            "user_id": "u-owner",
            "username": "avinash",
            "roles": ["SUPERADMIN"],
            "store_ids": [],
            "active_store_id": None,
        }
    )
    return {"Authorization": f"Bearer {tok}"}


def _admin_headers():
    tok = create_access_token(
        {
            "user_id": "u-admin",
            "username": "director",
            "roles": ["ADMIN"],
            "store_ids": [],
            "active_store_id": None,
        }
    )
    return {"Authorization": f"Bearer {tok}"}


def _enrolled_approved_device(h) -> FakeAuthenticator:
    """Drive the REAL enrolment + approval path and return the authenticator."""
    r = _login(h.client, "priya")
    assert r.status_code == 403
    ticket = r.json()["detail"]["enroll_ticket"]
    opts = h.client.post(
        "/api/v1/auth/devices/enroll/options", json={"enroll_ticket": ticket}
    ).json()
    dev = FakeAuthenticator()
    r = h.client.post(
        "/api/v1/auth/devices/enroll", json=dev.enroll_payload(ticket, opts)
    )
    assert r.status_code == 200, r.text
    device_id = r.json()["device_id"]
    r = h.client.post(
        f"/api/v1/auth/devices/{device_id}/approve", headers=_superadmin_headers()
    )
    assert r.status_code == 200, r.text
    dev.device_id = device_id
    return dev


# ---------------------------------------------------------------------------
# Dark by default
# ---------------------------------------------------------------------------


def test_gate_off_by_default_staff_login_unchanged(harness):
    """DEVICE_GATE_MODE unset -> the gate does not exist. This is the dark
    launch: deploying this code changes nothing until the env is armed."""
    r = _login(harness.client, "priya")
    assert r.status_code == 200, r.text
    assert r.json()["access_token"]


def test_log_mode_reports_but_never_blocks(harness, monkeypatch):
    monkeypatch.setenv("DEVICE_GATE_MODE", "log")
    r = _login(harness.client, "priya")
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Enforce mode
# ---------------------------------------------------------------------------


def test_enforce_blocks_staff_without_device(harness, monkeypatch):
    monkeypatch.setenv("DEVICE_GATE_MODE", "enforce")
    r = _login(harness.client, "priya")
    assert r.status_code == 403, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "DEVICE_NOT_APPROVED"
    assert detail["enroll_ticket"]


def test_admin_and_superadmin_are_never_device_gated(harness, monkeypatch):
    """THE INVARIANT. The owner at 9pm on a brand-new phone, shop full of
    customers, zero enrolled devices anywhere: he signs in. So does any
    ADMIN. If this test fails the design is wrong - do not weaken it."""
    monkeypatch.setenv("DEVICE_GATE_MODE", "enforce")
    for username in ("avinash", "director"):
        r = _login(harness.client, username)
        assert r.status_code == 200, f"{username} was device-gated: {r.text}"
        assert r.json()["access_token"]


def test_exemption_holds_even_when_device_store_is_down(harness, monkeypatch):
    """The exemption must run BEFORE any device-store work: a dead Mongo (or a
    broken device collection) must not take the owner's login with it."""
    monkeypatch.setenv("DEVICE_GATE_MODE", "enforce")

    def _boom():  # pragma: no cover - must never be reached for exempt roles
        raise AssertionError("device store touched for an exempt role")

    monkeypatch.setattr(device_gate, "_collection", _boom)
    r = _login(harness.client, "avinash")
    assert r.status_code == 200, r.text


def test_device_rejection_never_touches_the_rate_limiter(harness, monkeypatch):
    """A device rejection means the password was CORRECT. Recording it as a
    failure would let anyone replaying a colleague's known password from an
    unapproved device manufacture a lockout on the till - so the limiter
    must see NOTHING."""
    monkeypatch.setenv("DEVICE_GATE_MODE", "enforce")
    for _ in range(12):  # far past every failure threshold
        assert _login(harness.client, "priya").status_code == 403
    assert harness.limiter.check("testclient", "priya") is None, (
        "device rejections leaked into the rate limiter"
    )
    # check() itself seeds empty lists via its cleanup; what must NOT exist is
    # any RECORDED row (success or failure) from the 12 rejected logins.
    recorded = [row for rows in harness.limiter._attempts.values() for row in rows]
    assert recorded == [], f"limiter recorded device rejections: {recorded}"
    # ...and once a device IS approved, the same person signs straight in.
    dev = _enrolled_approved_device(harness)
    r = _login(harness.client, "priya", assertion=dev.assertion(_challenge(harness.client)))
    assert r.status_code == 200, r.text


def test_wrong_password_is_still_a_plain_401_and_still_counted(harness, monkeypatch):
    """The gate must not run before password verification: a wrong password
    stays an indistinguishable 401 and still feeds the limiter."""
    monkeypatch.setenv("DEVICE_GATE_MODE", "enforce")
    r = _login(harness.client, "priya", password="wrong-password-1")
    assert r.status_code == 401
    assert "device" not in r.text.lower()
    pair_rows = harness.limiter._attempts.get("pair:testclient|priya", [])
    assert any(not ok for _, ok in pair_rows), "failure was not recorded"


# ---------------------------------------------------------------------------
# The full phone-approval flow
# ---------------------------------------------------------------------------


def test_full_enrol_approve_sign_in_flow(harness, monkeypatch):
    monkeypatch.setenv("DEVICE_GATE_MODE", "enforce")
    client = harness.client

    # 1. Staff login on a new till -> blocked, but with an enrolment ticket.
    r = _login(client, "priya")
    assert r.status_code == 403
    ticket = r.json()["detail"]["enroll_ticket"]

    # 2. Register the device (real key, real create ceremony).
    opts = client.post(
        "/api/v1/auth/devices/enroll/options", json={"enroll_ticket": ticket}
    ).json()
    assert opts["rp_id"] == RP_ID
    dev = FakeAuthenticator()
    r = client.post("/api/v1/auth/devices/enroll", json=dev.enroll_payload(ticket, opts))
    assert r.status_code == 200, r.text
    device_id = r.json()["device_id"]
    assert r.json()["status"] == "PENDING"

    # 3. PENDING is not APPROVED: a valid signature still does not sign in.
    r = _login(client, "priya", assertion=dev.assertion(_challenge(client)))
    assert r.status_code == 403, "a merely-PENDING device was allowed to sign in"

    # 4. An ADMIN cannot approve (exempt from the gate, but not an approver).
    r = client.post(
        f"/api/v1/auth/devices/{device_id}/approve", headers=_admin_headers()
    )
    assert r.status_code == 403, "an ADMIN approved a device - SUPERADMIN only"

    # 5. The owner, from his phone browser, approves.
    r = client.post(
        f"/api/v1/auth/devices/{device_id}/approve", headers=_superadmin_headers()
    )
    assert r.status_code == 200, r.text

    # 6. Staff sign in on the approved device.
    r = _login(client, "priya", assertion=dev.assertion(_challenge(client)))
    assert r.status_code == 200, r.text
    assert r.json()["access_token"]

    # 7. Revoke -> blocked again, even with a fresh valid signature.
    r = client.post(
        f"/api/v1/auth/devices/{device_id}/revoke", headers=_superadmin_headers()
    )
    assert r.status_code == 200
    r = _login(client, "priya", assertion=dev.assertion(_challenge(client)))
    assert r.status_code == 403, "a REVOKED device still signed in"


def test_owner_sees_pending_first_and_gate_mode(harness, monkeypatch):
    monkeypatch.setenv("DEVICE_GATE_MODE", "enforce")
    _enrolled_approved_device(harness)
    r = harness.client.get("/api/v1/auth/devices", headers=_superadmin_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "enforce"
    assert len(body["devices"]) == 1
    assert body["devices"][0]["status"] == "APPROVED"
    assert "public_key_spki" not in body["devices"][0]
    # The list itself is SUPERADMIN-only too.
    assert (
        harness.client.get("/api/v1/auth/devices", headers=_admin_headers()).status_code
        == 403
    )


# ---------------------------------------------------------------------------
# Cryptographic strength of the identifier
# ---------------------------------------------------------------------------


def test_assertion_signed_with_the_wrong_key_is_rejected(harness, monkeypatch):
    """Knowing the credential id (it is not a secret) is worthless without
    the private key: a signature from a different key must fail."""
    monkeypatch.setenv("DEVICE_GATE_MODE", "enforce")
    dev = _enrolled_approved_device(harness)
    imposter_key = ec.generate_private_key(ec.SECP256R1())
    r = _login(
        harness.client,
        "priya",
        assertion=dev.assertion(_challenge(harness.client), key=imposter_key),
    )
    assert r.status_code == 403, "a forged signature was accepted"


def test_challenge_is_single_use(harness, monkeypatch):
    """A captured login cannot be replayed: the second presentation of the
    same signed challenge must fail."""
    monkeypatch.setenv("DEVICE_GATE_MODE", "enforce")
    dev = _enrolled_approved_device(harness)
    assertion = dev.assertion(_challenge(harness.client))
    assert _login(harness.client, "priya", assertion=assertion).status_code == 200
    assert _login(harness.client, "priya", assertion=assertion).status_code == 403, (
        "a replayed assertion was accepted"
    )


def test_unknown_credential_is_rejected(harness, monkeypatch):
    monkeypatch.setenv("DEVICE_GATE_MODE", "enforce")
    dev = FakeAuthenticator()  # never enrolled
    r = _login(harness.client, "priya", assertion=dev.assertion(_challenge(harness.client)))
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# The enrolment ticket is not a session
# ---------------------------------------------------------------------------


def test_enroll_ticket_is_refused_as_an_access_token(harness, monkeypatch):
    """The ticket proves 'correct password, wants to enrol' - nothing more.
    If it worked on AUTHENTICATED routes, a device-REJECTED login would
    still yield 10 minutes of API access, bypassing the gate that minted it."""
    monkeypatch.setenv("DEVICE_GATE_MODE", "enforce")
    r = _login(harness.client, "priya")
    ticket = r.json()["detail"]["enroll_ticket"]
    me = harness.client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {ticket}"}
    )
    assert me.status_code == 401, "an enrolment ticket was accepted as a session"


def test_enrolment_requires_a_valid_ticket(harness, monkeypatch):
    """No ticket, no enrolment - an ACCESS token is not a ticket either, so
    anonymous (or merely-authenticated) spam cannot create PENDING rows."""
    monkeypatch.setenv("DEVICE_GATE_MODE", "enforce")
    r = harness.client.post(
        "/api/v1/auth/devices/enroll/options",
        json={"enroll_ticket": _superadmin_headers()["Authorization"].split()[1]},
    )
    assert r.status_code == 401
    r = harness.client.post(
        "/api/v1/auth/devices/enroll/options", json={"enroll_ticket": "garbage"}
    )
    assert r.status_code == 401


def test_pending_requests_per_user_are_capped(harness, monkeypatch):
    """A valid-password staffer must not be able to flood the owner's phone
    with hundreds of PENDING rows."""
    monkeypatch.setenv("DEVICE_GATE_MODE", "enforce")
    client = harness.client
    ticket = _login(client, "priya").json()["detail"]["enroll_ticket"]
    for i in range(3):
        opts = client.post(
            "/api/v1/auth/devices/enroll/options", json={"enroll_ticket": ticket}
        ).json()
        r = client.post(
            "/api/v1/auth/devices/enroll",
            json=FakeAuthenticator().enroll_payload(ticket, opts, name=f"dev {i}"),
        )
        assert r.status_code == 200, r.text
    opts = client.post(
        "/api/v1/auth/devices/enroll/options", json={"enroll_ticket": ticket}
    ).json()
    r = client.post(
        "/api/v1/auth/devices/enroll",
        json=FakeAuthenticator().enroll_payload(ticket, opts, name="dev 4"),
    )
    assert r.status_code == 400
    assert "pending" in r.json()["detail"].lower()


def test_pre_arming_enroll_ticket_route(harness, monkeypatch):
    """Rollout viability: while the gate is DARK, a signed-in user can mint a
    ticket and register the current device - so every till can be enrolled
    and approved BEFORE enforce mode is armed. Anonymous callers get 401
    (the route must not be a ticket faucet)."""
    client = harness.client
    # Anonymous -> 401.
    assert client.post("/api/v1/auth/devices/enroll-ticket").status_code == 401
    # Signed-in staff (gate off - normal login) -> ticket that actually works.
    token = _login(client, "priya").json()["access_token"]
    r = client.post(
        "/api/v1/auth/devices/enroll-ticket",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    ticket = r.json()["enroll_ticket"]
    opts = client.post(
        "/api/v1/auth/devices/enroll/options", json={"enroll_ticket": ticket}
    )
    assert opts.status_code == 200, opts.text
    dev = FakeAuthenticator()
    r = client.post(
        "/api/v1/auth/devices/enroll", json=dev.enroll_payload(ticket, opts.json())
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "PENDING"
