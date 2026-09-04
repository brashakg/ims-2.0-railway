"""
IMS 2.0 - Login-device enrolment + approval (owner rulings 2026-09-02)
=======================================================================
Staff sign-in is restricted to SUPERADMIN-approved devices (see
api/services/device_gate.py -- DARK until DEVICE_GATE_MODE is armed).

This router is INCLUDED BY auth.py under its own prefix, so everything here
lives at /api/v1/auth/devices/* -- the device gate is part of authentication,
and mounting it from auth.py keeps main.py untouched.

Route map (POLICY rows in api/services/rbac_policy.py):
  POST /auth/devices/enroll/options     PUBLIC*  challenge for create()
  POST /auth/devices/enroll             PUBLIC*  submit a PENDING request
  POST /auth/devices/assertion-options  PUBLIC   challenge for get()
  GET  /auth/devices                    SUPERADMIN  list (approval screen)
  POST /auth/devices/{id}/approve       SUPERADMIN
  POST /auth/devices/{id}/revoke        SUPERADMIN  (also rejects a PENDING row)

* "PUBLIC" = no session token, but BOTH enrolment routes demand a valid
  ENROLMENT TICKET -- a 10-minute purpose-scoped JWT that /auth/login mints
  only after verifying a correct password on a gated account. Nobody can
  enrol (or spam PENDING rows) without first proving a real password, and
  the ticket is refused as a session token by get_current_user.

Approval is SUPERADMIN-ONLY by owner ruling: an ADMIN is exempt from the
gate but cannot enrol anybody's device. Do not widen this -- the answer to
"we need a second approver" is a second SUPERADMIN account.
"""

from __future__ import annotations

import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.services import device_gate

# Imported at the TOP deliberately. auth.py includes this router at its
# BOTTOM (after these names are defined), so the normal import order --
# main -> auth -> devices -> (auth already in sys.modules) -- is safe. If
# anything ever imports THIS module before auth, the include in auth.py
# raises a loud ImportError instead of silently registering half a router.
from .auth import (  # noqa: E402
    _audit_auth_event,
    _client_ip,
    get_current_user as _get_current_user,
    require_roles,
)

router = APIRouter()

_superadmin = require_roles("SUPERADMIN")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class EnrollOptionsRequest(BaseModel):
    enroll_ticket: str


class EnrollRequest(BaseModel):
    enroll_ticket: str
    challenge_id: str
    credential_id: str = Field(..., min_length=1)
    client_data_json: str
    # SPKI DER from AuthenticatorAttestationResponse.getPublicKey(), base64.
    public_key_spki: str = Field(..., min_length=1)
    public_key_alg: Optional[int] = None  # COSE alg (-7 ES256 / -257 RS256)
    # Raw getAuthenticatorData() bytes (base64) -- only used to read the
    # backup-eligible flag; optional for older browsers.
    authenticator_data: Optional[str] = None
    device_name: str = Field(..., min_length=1, max_length=80)
    platform: Optional[str] = None


def _require_ticket(ticket: str) -> dict:
    claims = device_gate.read_enroll_ticket(ticket)
    if claims is None:
        raise HTTPException(
            status_code=401,
            detail="Enrolment ticket invalid or expired. Sign in again to restart device registration.",
        )
    return claims


# ---------------------------------------------------------------------------
# Pre-auth: enrolment + login-assertion challenges
# ---------------------------------------------------------------------------


@router.post("/enroll/options")
async def enroll_options(request: EnrollOptionsRequest):
    """Server half of navigator.credentials.create(): challenge + RP info.
    Ticket-gated (a correct password was just presented)."""
    claims = _require_ticket(request.enroll_ticket)
    options = device_gate.new_challenge()
    options["user_handle"] = secrets.token_urlsafe(16)
    options["username"] = claims.get("username") or ""
    return options


@router.post("/enroll")
async def enroll_device(request: EnrollRequest, req: Request = None):
    """Record a PENDING device. Enrolment is an ACT, never a side effect of a
    login -- a human typed a device name and a SUPERADMIN must still approve."""
    claims = _require_ticket(request.enroll_ticket)
    doc, reason = device_gate.create_enrolment(
        ticket_claims=claims,
        challenge_id=request.challenge_id,
        credential_id=request.credential_id,
        client_data_json=request.client_data_json,
        public_key_spki=request.public_key_spki,
        public_key_alg=request.public_key_alg,
        authenticator_data=request.authenticator_data,
        device_name=request.device_name,
        platform=request.platform,
    )
    if doc is None:
        raise HTTPException(status_code=400, detail=f"Enrolment failed: {reason}")
    _audit_auth_event(
        action="device_enroll_requested",
        user_id=claims.get("user_id"),
        username=claims.get("username"),
        ip_address=_client_ip(req),
        severity="INFO",
        detail=f"{doc['device_id']} '{doc['device_name']}'",
    )
    return {
        "device_id": doc["device_id"],
        "status": doc["status"],
        "message": "Device registered. Awaiting SUPERADMIN approval.",
    }


@router.post("/enroll-ticket")
async def enroll_ticket_for_current_session(current_user: dict = Depends(_get_current_user)):
    """AUTHENTICATED pre-arming path. While the gate is off (dark) or in log
    mode, staff still sign in normally -- this mints an enrolment ticket for
    the CURRENT session so store devices can be registered and approved
    BEFORE DEVICE_GATE_MODE=enforce is armed. Without it, the only way to
    get a ticket is an enforce-mode rejection, i.e. arming the gate would
    block every till at once on day one. The ticket is identical to the one
    a rejected login mints (same purpose scoping, same 10-minute life)."""
    return {
        "enroll_ticket": device_gate.mint_enroll_ticket(
            current_user.get("user_id") or "",
            current_user.get("username") or "",
        )
    }


@router.post("/assertion-options")
async def assertion_options():
    """Fresh single-use challenge for navigator.credentials.get() before a
    gated login. Deliberately anonymous: it reveals nothing (no credential
    ids are served -- the browser keeps those) and a challenge is just a
    TTL'd random value in the shared cache."""
    return device_gate.new_challenge()


# ---------------------------------------------------------------------------
# SUPERADMIN management (the phone-browser approval screen)
# ---------------------------------------------------------------------------

@router.get("")
async def list_devices(current_user: dict = Depends(_superadmin)):
    """All device rows (pending first) + the live gate mode, so the approval
    screen can say plainly whether the gate is OFF (dark), LOG, or ENFORCE."""
    return {"mode": device_gate.gate_mode(), "devices": device_gate.list_devices()}


@router.post("/{device_id}/approve")
async def approve_device(
    device_id: str,
    req: Request = None,
    current_user: dict = Depends(_superadmin),
):
    doc = device_gate.set_device_status(device_id, "APPROVED", current_user)
    if doc is None:
        raise HTTPException(status_code=404, detail="Device not found")
    _audit_auth_event(
        action="device_approved",
        user_id=current_user.get("user_id"),
        username=current_user.get("username"),
        ip_address=_client_ip(req),
        severity="INFO",
        detail=f"{device_id} '{doc.get('device_name')}'",
    )
    return doc


@router.post("/{device_id}/revoke")
async def revoke_device(
    device_id: str,
    req: Request = None,
    current_user: dict = Depends(_superadmin),
):
    doc = device_gate.set_device_status(device_id, "REVOKED", current_user)
    if doc is None:
        raise HTTPException(status_code=404, detail="Device not found")
    _audit_auth_event(
        action="device_revoked",
        user_id=current_user.get("user_id"),
        username=current_user.get("username"),
        ip_address=_client_ip(req),
        severity="WARNING",
        detail=f"{device_id} '{doc.get('device_name')}'",
    )
    return doc
