"""
IMS 2.0 - Customer self-service portal (PUBLIC)
===============================================
Customers verify by phone OTP, then read ONLY their own prescriptions. Mounted
outside the staff JWT family. No staff data is ever reachable here: the token is
scope-locked to "customer_portal" and every read is bound to the token's
customer_id.
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from ..dependencies import get_customer_repository, get_prescription_repository, get_db
from ..services.customer_portal_auth import (
    MAX_OTP_ATTEMPTS,
    OTP_TTL_MINUTES,
    decode_customer_token,
    generate_otp,
    hash_otp,
    issue_customer_token,
    otp_expired,
    verify_otp,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Generic response — never reveals whether a phone is registered (anti-enumeration).
_GENERIC_OTP_MSG = {"message": "If this number is registered, an OTP has been sent."}


class OtpRequest(BaseModel):
    phone: str


class OtpVerify(BaseModel):
    phone: str
    otp: str


def _norm_phone(phone: str) -> str:
    """Keep digits only; use the last 10 (Indian mobile) for matching."""
    digits = re.sub(r"\D", "", phone or "")
    return digits[-10:] if len(digits) >= 10 else digits


def _otp_coll():
    db = get_db()
    if db is None or not getattr(db, "is_connected", True):
        return None
    try:
        return db.get_collection("customer_otp")
    except Exception:  # noqa: BLE001
        return None


def _sanitize_rx(rx: dict) -> dict:
    """Return ONLY patient-facing prescription fields — never internal ids,
    audit trails, store/staff internals, or redo metadata."""
    keep = [
        "prescription_id", "prescription_date", "test_date", "created_at",
        "right_eye_sph", "right_eye_cyl", "right_eye_axis", "right_eye_add",
        "left_eye_sph", "left_eye_cyl", "left_eye_axis", "left_eye_add",
        "pd", "optometrist_name", "validity_months", "remarks",
        # tolerate alternative key shapes
        "od", "os", "right", "left",
    ]
    return {k: rx.get(k) for k in keep if k in rx}


@router.post("/request-otp")
async def request_otp(body: OtpRequest):
    """Send an OTP if the phone belongs to a known customer. Always returns the
    same generic message (no account enumeration)."""
    phone = _norm_phone(body.phone)
    repo = get_customer_repository()
    if repo is None or len(phone) < 10:
        return _GENERIC_OTP_MSG

    customer = repo.find_by_mobile(phone)
    if customer is None:
        return _GENERIC_OTP_MSG  # do not reveal non-existence

    otp = generate_otp()
    coll = _otp_coll()
    if coll is not None:
        try:
            coll.update_one(
                {"phone": phone},
                {"$set": {
                    "phone": phone,
                    "otp_hash": hash_otp(otp, phone),
                    "expires_at": (datetime.utcnow() + timedelta(minutes=OTP_TTL_MINUTES)).isoformat(),
                    "attempts": 0,
                    "created_at": datetime.utcnow().isoformat(),
                }},
                upsert=True,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[CUSTOMER_PORTAL] otp store failed: {e}")

    # Deliver via the existing fail-soft provider (DISPATCH_MODE-gated).
    try:
        from agents.providers import send_whatsapp

        send_whatsapp(phone, f"Your Better Vision OTP is {otp}. Valid for {OTP_TTL_MINUTES} minutes.")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[CUSTOMER_PORTAL] OTP send skipped: {e}")

    return _GENERIC_OTP_MSG


@router.post("/verify-otp")
async def verify_otp_endpoint(body: OtpVerify):
    """Verify the OTP and return a short-lived, scope-locked customer token."""
    phone = _norm_phone(body.phone)
    coll = _otp_coll()
    if coll is None:
        raise HTTPException(status_code=503, detail="Service unavailable")

    rec = coll.find_one({"phone": phone})
    if rec is None:
        raise HTTPException(status_code=400, detail="Invalid or expired code")
    if otp_expired(rec.get("expires_at")):
        coll.delete_one({"phone": phone})
        raise HTTPException(status_code=400, detail="Invalid or expired code")
    if int(rec.get("attempts", 0) or 0) >= MAX_OTP_ATTEMPTS:
        coll.delete_one({"phone": phone})
        raise HTTPException(status_code=429, detail="Too many attempts; request a new code")

    if not verify_otp(body.otp, phone, rec.get("otp_hash")):
        coll.update_one({"phone": phone}, {"$inc": {"attempts": 1}})
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    repo = get_customer_repository()
    customer = repo.find_by_mobile(phone) if repo is not None else None
    if customer is None:
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    coll.delete_one({"phone": phone})  # single-use
    cid = customer.get("customer_id")
    token = issue_customer_token(cid, phone)
    return {"token": token, "customer": {"customer_id": cid, "name": customer.get("name")}}


def _require_customer(authorization: Optional[str]) -> dict:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    payload = decode_customer_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return payload


@router.get("/prescriptions")
async def my_prescriptions(authorization: Optional[str] = Header(None)):
    """The authenticated customer's own prescriptions (sanitized, read-only)."""
    payload = _require_customer(authorization)
    cid = payload.get("sub")
    repo = get_prescription_repository()
    if repo is None or not cid:
        return {"prescriptions": []}
    try:
        rxs = repo.find_by_customer(cid) or []
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[CUSTOMER_PORTAL] rx fetch failed: {e}")
        rxs = []
    return {"prescriptions": [_sanitize_rx(r) for r in rxs]}


@router.get("/me")
async def me(authorization: Optional[str] = Header(None)):
    """Basic profile for the authenticated customer (name + phone only)."""
    payload = _require_customer(authorization)
    return {"customer_id": payload.get("sub"), "phone": payload.get("phone")}
