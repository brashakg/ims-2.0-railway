"""
IMS 2.0 - Approved-device login gate (owner rulings 2026-09-02)
================================================================
Staff may sign in ONLY on devices a SUPERADMIN has approved. ADMIN and
SUPERADMIN are NEVER device-gated (the invariant the whole design rests on:
if the gate could lock the owner out of the screen that unlocks everyone
else, the design is wrong). Approval happens from the owner's phone browser
via the Settings -> Devices screen; that IS the break-glass.

DARK BY DEFAULT -- DEVICE_GATE_MODE env (same discipline as DISPATCH_MODE in
backend/agents/providers.py):

    DEVICE_GATE_MODE=off      (default) gate entirely inert; login unchanged.
    DEVICE_GATE_MODE=log      verify + log what WOULD be blocked; never block.
                              Use this to measure before arming (a clamp that
                              looks harmless on empty data may bite at go-live).
    DEVICE_GATE_MODE=enforce  block gated roles without an approved device.

Passkeys bind to the web origin (RP ID): enrol only on the app's FINAL
domain (app.uniparallel.com, live since 2026-09) -- a later domain move
invalidates every enrolled device. Arming is an OWNER decision, not a
deploy: with zero devices enrolled, enforce mode blocks every till at once.
Checklist: set DEVICE_GATE_RP_ID=app.uniparallel.com, optionally
DEVICE_GATE_ORIGINS (comma-separated allowed web origins), have staff enrol
via the pre-arming ticket route + owner approve, then DEVICE_GATE_MODE=log,
watch, then enforce.

DEVICE IDENTITY -- honest strength statement
--------------------------------------------
A device is identified by a WebAuthn PLATFORM PASSKEY (Windows Hello / Touch
ID / Android screen-lock). At login the browser signs a fresh server
challenge with a private key held by the device's authenticator; the server
verifies the signature against the public key captured at enrolment. This is
NOT a header or a localStorage secret: nothing copyable out of the browser
with F12 reproduces the signature, and replaying a captured login does not
work (single-use challenge).

What DEFEATS it (do not oversell):
  * Synced passkeys. iCloud Keychain / Google Password Manager can sync a
    passkey to the enrolling person's OTHER devices, defeating the physical
    binding. We record the authenticator's backup-eligible (BE) flag at
    enrolment and surface it on the approval screen so the owner can refuse
    a synced credential -- we do not hard-block it, because Apple platform
    passkeys are ALWAYS backup-eligible and a hard block would make iPads
    un-enrollable.
  * Physical access to an approved device (a staff member AT the till can
    obviously log in -- that is the point, not a flaw).
  * Enrolling a personal phone and getting it APPROVED. The approval screen
    shows who requested which device; the human pressing Approve is the
    control.
  * Authenticator sign-count is not enforced (platform authenticators
    commonly report 0), so a cloned credential is not auto-detected.

No new IP reader is introduced anywhere in this module (the repo already has
two; see auth._client_ip / main._extract_client_ip).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# The invariant (owner ruling 2026-09-02): ADMIN and SUPERADMIN are exempt
# from the gate. SUPERADMIN alone may APPROVE -- that asymmetry is enforced
# at the router (require_roles("SUPERADMIN")), not here.
EXEMPT_ROLES = frozenset({"ADMIN", "SUPERADMIN"})

_CHALLENGE_CACHE_PREFIX = "device_gate_challenge:"
_CHALLENGE_TTL_SECONDS = 300
_ENROLL_TICKET_MINUTES = 10
_MAX_PENDING_PER_USER = 3

COLLECTION = "login_devices"


class DeviceAssertion(BaseModel):
    """WebAuthn assertion riding along on POST /auth/login (all base64)."""

    challenge_id: str
    credential_id: str
    client_data_json: str
    authenticator_data: str
    signature: str


# ---------------------------------------------------------------------------
# Mode / role plumbing
# ---------------------------------------------------------------------------


def gate_mode() -> str:
    """off | log | enforce. Read per-call (a redeploy flips it; tests set env).
    Unknown values fall back to off LOUDLY, mirroring DISPATCH_MODE."""
    raw = (os.getenv("DEVICE_GATE_MODE") or "off").strip().lower()
    if raw in ("off", "log", "enforce"):
        return raw
    logger.warning("[DEVICE_GATE] unknown DEVICE_GATE_MODE=%r - treating as off", raw)
    return "off"


def is_exempt(roles: Optional[List[str]]) -> bool:
    """True when the account must NEVER be device-gated. Checked before any
    DB/crypto work so a broken device store cannot touch the owner's login."""
    return bool(EXEMPT_ROLES & set(roles or []))


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def _collection():
    """login_devices collection, or None when no DB (local dev / unit tests
    monkeypatch this)."""
    try:
        from database.connection import get_db

        db = get_db().db
    except Exception:  # noqa: BLE001
        return None
    if db is None:
        return None
    return db.get_collection(COLLECTION)


def _cache():
    from api.services.cache import cache

    return cache


# ---------------------------------------------------------------------------
# Challenges (single-use, TTL'd, shared cache so all 4 uvicorn workers agree)
# ---------------------------------------------------------------------------


def new_challenge() -> dict:
    """Mint a random challenge for a create() or get() ceremony."""
    challenge = secrets.token_bytes(32)
    challenge_id = secrets.token_urlsafe(16)
    _cache().set(
        _CHALLENGE_CACHE_PREFIX + challenge_id,
        base64.b64encode(challenge).decode(),
        ttl=_CHALLENGE_TTL_SECONDS,
    )
    return {
        "challenge_id": challenge_id,
        "challenge": _b64url(challenge),
        "rp_id": os.getenv("DEVICE_GATE_RP_ID") or "",
        "timeout_ms": 60000,
    }


def consume_challenge(challenge_id: str) -> Optional[bytes]:
    """Fetch AND delete a challenge (single use). None when absent/expired.
    ponytail: get+delete is not atomic across workers - the replay window is
    milliseconds wide and a replayed assertion still needs the private key;
    move to a Redis GETDEL if that ever matters."""
    if not challenge_id:
        return None
    key = _CHALLENGE_CACHE_PREFIX + challenge_id
    cache = _cache()
    stored = cache.get(key)
    if not stored:
        return None
    cache.delete(key)
    try:
        return base64.b64decode(stored)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Enrolment tickets -- proof that the enrolment request comes from somebody
# who just presented a CORRECT password (login mints one on a device
# rejection). Deliberately a JWT with a `purpose` claim: auth.get_current_user
# REFUSES purpose-bearing tokens, so a ticket can never be spent as a session.
# ---------------------------------------------------------------------------


def mint_enroll_ticket(user_id: str, username: str) -> str:
    from api.routers.auth import create_access_token  # lazy: avoid import cycle

    return create_access_token(
        {"purpose": "device_enroll", "user_id": user_id, "username": username},
        expires_delta=timedelta(minutes=_ENROLL_TICKET_MINUTES),
    )


def read_enroll_ticket(ticket: str) -> Optional[dict]:
    """Validated ticket claims, or None. Never raises."""
    if not ticket:
        return None
    try:
        from api.routers.auth import decode_token  # lazy: avoid import cycle

        claims = decode_token(ticket)
    except Exception:  # noqa: BLE001 (decode_token raises HTTPException)
        return None
    if claims.get("purpose") != "device_enroll":
        return None
    return claims


# ---------------------------------------------------------------------------
# WebAuthn plumbing (no CBOR: the browser's getPublicKey() hands us SPKI DER,
# so `cryptography` -- already a dependency -- covers everything).
# ---------------------------------------------------------------------------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64_any(value: str) -> bytes:
    """Decode standard or url-safe base64, padded or not."""
    pad = value + "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(pad)
    except Exception:  # noqa: BLE001
        return base64.b64decode(pad)


def parse_authenticator_flags(auth_data: bytes) -> dict:
    """{user_present, backup_eligible, backed_up} from authenticatorData."""
    if len(auth_data) < 37:
        return {}
    flags = auth_data[32]
    return {
        "user_present": bool(flags & 0x01),
        "backup_eligible": bool(flags & 0x08),
        "backed_up": bool(flags & 0x10),
    }


def _check_client_data(raw: bytes, expected_type: str, expected_challenge: bytes) -> Optional[str]:
    """None when clientDataJSON matches, else a reason string."""
    try:
        client_data = json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return "clientDataJSON unreadable"
    if client_data.get("type") != expected_type:
        return f"clientData type is not {expected_type}"
    if client_data.get("challenge") != _b64url(expected_challenge):
        return "challenge mismatch"
    allowed_origins = [
        o.strip()
        for o in (os.getenv("DEVICE_GATE_ORIGINS") or "").split(",")
        if o.strip()
    ]
    if allowed_origins and client_data.get("origin") not in allowed_origins:
        return "origin not allowed"
    return None


def _check_rp_id_hash(auth_data: bytes) -> Optional[str]:
    """authenticatorData[0:32] must equal sha256(DEVICE_GATE_RP_ID). Fails
    CLOSED when the env is unset -- an armed gate with no RP ID is a
    misconfiguration, and only NON-exempt roles ever reach this code."""
    rp_id = os.getenv("DEVICE_GATE_RP_ID") or ""
    if not rp_id:
        return "DEVICE_GATE_RP_ID is not configured"
    if len(auth_data) < 37:
        return "authenticatorData too short"
    if auth_data[:32] != hashlib.sha256(rp_id.encode()).digest():
        return "rpIdHash mismatch"
    return None


def verify_assertion(assertion: DeviceAssertion) -> Tuple[Optional[dict], str]:
    """Verify a login assertion. Returns (approved_device_doc, "ok") on
    success, else (None, reason). The reason is for logs/audit -- the client
    only ever sees the generic device-not-approved message."""
    challenge = consume_challenge(assertion.challenge_id)
    if challenge is None:
        return None, "challenge expired or already used"

    try:
        auth_data = _b64_any(assertion.authenticator_data)
        client_data_raw = _b64_any(assertion.client_data_json)
        signature = _b64_any(assertion.signature)
    except Exception:  # noqa: BLE001
        return None, "assertion fields not decodable"

    reason = _check_client_data(client_data_raw, "webauthn.get", challenge)
    if reason:
        return None, reason
    reason = _check_rp_id_hash(auth_data)
    if reason:
        return None, reason
    flags = parse_authenticator_flags(auth_data)
    if not flags.get("user_present"):
        return None, "user-present flag not set"

    coll = _collection()
    if coll is None:
        # Fail CLOSED: this only runs for NON-exempt roles in log/enforce
        # mode, and prod fails loud on a DB outage anyway (#726).
        return None, "device store unavailable"
    device = coll.find_one({"credential_id": assertion.credential_id})
    if device is None:
        return None, "unknown credential"

    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec, padding
        from cryptography.hazmat.primitives.serialization import load_der_public_key

        public_key = load_der_public_key(_b64_any(device["public_key_spki"]))
        signed = auth_data + hashlib.sha256(client_data_raw).digest()
        if isinstance(public_key, ec.EllipticCurvePublicKey):
            public_key.verify(signature, signed, ec.ECDSA(hashes.SHA256()))
        else:
            public_key.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
    except Exception:  # noqa: BLE001 - any crypto failure = bad signature
        return None, "signature invalid"

    if device.get("status") != "APPROVED":
        return None, f"device status is {device.get('status')}"
    return device, "ok"


# ---------------------------------------------------------------------------
# THE gate -- the single door auth.login calls
# ---------------------------------------------------------------------------


def check_login(roles: Optional[List[str]], assertion: Optional[DeviceAssertion]) -> Optional[str]:
    """None = allow the login. A string = block, with the reason (enforce
    mode only). Runs AFTER password verification in auth.login, so a device
    rejection never doubles as a password oracle beyond what the distinct
    401/403 statuses already reveal, and never feeds the rate limiter."""
    mode = gate_mode()
    if mode == "off":
        return None
    if is_exempt(roles):
        # INVARIANT: the owner (SUPERADMIN) and every ADMIN sign in from ANY
        # device, always -- no DB read, no crypto, nothing to go wrong.
        return None

    if assertion is None:
        device, reason = None, "no device assertion presented"
    else:
        device, reason = verify_assertion(assertion)

    if device is not None:
        return None
    if mode == "log":
        logger.warning(
            "[DEVICE_GATE] log mode: WOULD BLOCK this login (%s). "
            "Set DEVICE_GATE_MODE=enforce to arm.",
            reason,
        )
        return None
    return reason


# ---------------------------------------------------------------------------
# Enrolment + management
# ---------------------------------------------------------------------------


def create_enrolment(
    *,
    ticket_claims: dict,
    challenge_id: str,
    credential_id: str,
    client_data_json: str,
    public_key_spki: str,
    public_key_alg: Optional[int],
    authenticator_data: Optional[str],
    device_name: str,
    platform: Optional[str],
) -> Tuple[Optional[dict], str]:
    """Record a PENDING device request. Returns (doc, "ok") or (None, reason)."""
    challenge = consume_challenge(challenge_id)
    if challenge is None:
        return None, "challenge expired or already used"
    try:
        client_data_raw = _b64_any(client_data_json)
    except Exception:  # noqa: BLE001
        return None, "clientDataJSON not decodable"
    reason = _check_client_data(client_data_raw, "webauthn.create", challenge)
    if reason:
        return None, reason

    backup_eligible = None
    if authenticator_data:
        try:
            flags = parse_authenticator_flags(_b64_any(authenticator_data))
            backup_eligible = flags.get("backup_eligible")
        except Exception:  # noqa: BLE001
            backup_eligible = None

    coll = _collection()
    if coll is None:
        return None, "device store unavailable"
    if coll.find_one({"credential_id": credential_id}) is not None:
        return None, "credential already enrolled"
    user_id = ticket_claims.get("user_id") or ""
    pending = coll.count_documents(
        {"status": "PENDING", "requested_by.user_id": user_id}
    )
    if pending >= _MAX_PENDING_PER_USER:
        return None, "too many pending requests"

    doc = {
        "device_id": "dev_" + secrets.token_hex(8),
        "credential_id": credential_id,
        "public_key_spki": public_key_spki,
        "public_key_alg": public_key_alg,
        "status": "PENDING",
        "device_name": (device_name or "").strip()[:80] or "Unnamed device",
        "platform": (platform or "")[:200],
        "backup_eligible": backup_eligible,
        "requested_by": {
            "user_id": user_id,
            "username": ticket_claims.get("username") or "",
        },
        "requested_at": datetime.utcnow(),
        "approved_by": None,
        "approved_at": None,
        "revoked_by": None,
        "revoked_at": None,
    }
    coll.insert_one(doc)
    doc.pop("_id", None)
    return doc, "ok"


def list_devices() -> List[dict]:
    coll = _collection()
    if coll is None:
        return []
    out = []
    for doc in coll.find({}, {"_id": 0, "public_key_spki": 0}):
        out.append(doc)
    out.sort(
        key=lambda d: (
            {"PENDING": 0, "APPROVED": 1, "REVOKED": 2}.get(d.get("status"), 3),
            str(d.get("requested_at") or ""),
        )
    )
    return out


def set_device_status(device_id: str, status: str, actor: dict) -> Optional[dict]:
    """Approve or revoke. Returns the updated doc or None when not found."""
    coll = _collection()
    if coll is None:
        return None
    stamp = {"user_id": actor.get("user_id"), "username": actor.get("username")}
    if status == "APPROVED":
        update = {"status": "APPROVED", "approved_by": stamp, "approved_at": datetime.utcnow()}
    else:
        update = {"status": "REVOKED", "revoked_by": stamp, "revoked_at": datetime.utcnow()}
    res = coll.update_one({"device_id": device_id}, {"$set": update})
    if getattr(res, "matched_count", 0) == 0:
        return None
    return coll.find_one({"device_id": device_id}, {"_id": 0, "public_key_spki": 0})
