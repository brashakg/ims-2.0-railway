"""
IMS 2.0 - Customer Self-Service Portal Router (PUBLIC)
======================================================
Customer-facing surface. Mounted OUTSIDE the JWT-protected family of routers
because real customers hit this WITHOUT an IMS user account.

Two flows, two different trust models (decided by the product owner):

  1. ORDER TRACKING -- PUBLIC tokenized link, no login.
     The order carries a long unguessable `tracking_token`
     (secrets.token_urlsafe). The token in the URL IS the credential, same
     shape Stripe / Linear / the existing vendor portal use. We return a
     SAFE subset of the order: status + timeline + expected delivery +
     items as "Brand Category" + store name/phone. NEVER cost, margin,
     salesperson, or any internal field.

  2. PRESCRIPTION (Rx) VIEWING -- OTP-gated, because an Rx is medical data.
     Customer enters their phone -> we mint a 6-digit OTP, store it HASHED
     with a short TTL + attempt counter, and send it as a TRANSACTIONAL
     message (OTP bypasses the MEGAPHONE promotional DND window). On
     verify we issue a short-lived signed view token scoped to the
     customer_id. GET /rx then returns only THAT customer's prescriptions.

Security posture (mirrors core philosophy -- Control over Convenience):
  - Never reveal whether a phone number exists (always generic success on
    request-otp). Blocks account enumeration.
  - OTP stored as SHA-256(otp + phone) -- the plaintext OTP never persists.
  - Attempt lockout after N wrong tries; TTL expiry independent of attempts.
  - Per-IP rate-limit on tracking lookups; per-phone rate-limit on OTP
    requests.
  - Everything fail-soft: missing DB / dep = clean empty/`503`, never a
    500 stack trace, never an accidental JWT requirement.

This router has NO auth dependency -- requests carry their own token/OTP.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import hashlib
import logging
import os
import secrets
import time

import jwt
from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================================
# DB helper (project convention)
# ============================================================================


def _get_db():
    from database.connection import get_db

    return get_db().db


# ============================================================================
# Config
# ============================================================================

# OTP lifetime + attempt budget. Short TTL keeps a leaked OTP useless fast.
_OTP_TTL_SECONDS = int(os.getenv("PORTAL_OTP_TTL_SECONDS", "300"))  # 5 min
_OTP_MAX_ATTEMPTS = int(os.getenv("PORTAL_OTP_MAX_ATTEMPTS", "5"))
# View token (issued after a successful OTP verify) lifetime.
_VIEW_TOKEN_TTL_MINUTES = int(os.getenv("PORTAL_VIEW_TOKEN_MINUTES", "15"))
# In DEBUG mode (non-prod / explicit opt-in) we return the OTP in the
# response so it can be tested without a live SMS gateway. NEVER enable in
# production -- it would let anyone read any customer's Rx.
_OTP_DEBUG = os.getenv("PORTAL_OTP_DEBUG", "").lower() in ("1", "true", "yes")

# Custom audience claim so a leaked IMS user JWT can't be replayed here and a
# portal view token can't be replayed against the main app.
_VIEW_TOKEN_AUDIENCE = "ims-portal-rx"


def _jwt_secret() -> str:
    """Reuse the app's JWT secret so we don't introduce a second key to
    manage. auth.py already fails fast at import if it's unset, so by the
    time this module runs it is guaranteed present -- but stay defensive."""
    return os.getenv("JWT_SECRET_KEY") or "dev-portal-secret"


# ============================================================================
# RATE LIMITS -- in-memory sliding windows (fail-soft, single-worker scope)
# ============================================================================
# These are best-effort throttles, not a security boundary. They cap a rogue
# script without punishing a human refreshing. Keyed in memory; on a
# multi-worker deploy each worker has its own bucket (acceptable -- the OTP
# attempt lockout in Mongo is the real brute-force guard).

_TRACK_RATE_LIMIT = int(os.getenv("PORTAL_TRACK_RATE_PER_MIN", "30"))
_OTP_REQ_RATE_LIMIT = int(os.getenv("PORTAL_OTP_RATE_PER_MIN", "5"))
_RATE_WINDOW = 60.0
_track_log: dict = defaultdict(list)
_otp_req_log: dict = defaultdict(list)


def _client_ip(request: Optional[Request]) -> str:
    if request is None:
        return "unknown"
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_rate(bucket: dict, key: str, limit: int, label: str) -> None:
    """Sliding-window rate check. Raises 429 when `key` is over budget.
    Fail-soft: any internal error here must not block the request."""
    try:
        now = time.time()
        cutoff = now - _RATE_WINDOW
        recent = [t for t in bucket[key] if t > cutoff]
        if len(recent) >= limit:
            raise HTTPException(
                status_code=429,
                detail=f"Too many {label} requests. Please wait a minute and try again.",
            )
        recent.append(now)
        bucket[key] = recent
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[PORTAL] rate-check error (%s): %s", label, exc)


# ============================================================================
# Customer-facing item label -- Python mirror of
# frontend/src/utils/receiptFormat.ts::describeForReceipt
# ("Ray-Ban Sunglass", "Zeiss Spectacle Lens"). Never leaks SKU/cost.
# ============================================================================

_CATEGORY_LABELS = {
    "FRAMES": "Spectacle Frame",
    "FRAME": "Spectacle Frame",
    "SPECTACLE_FRAME": "Spectacle Frame",
    "SPECTACLE_FRAMES": "Spectacle Frame",
    "SUNGLASSES": "Sunglass",
    "SUNGLASS": "Sunglass",
    "OPTICAL_LENS": "Spectacle Lens",
    "OPTICAL_LENSES": "Spectacle Lens",
    "SPECTACLE_LENS": "Spectacle Lens",
    "SPECTACLE_LENSES": "Spectacle Lens",
    "LENS": "Spectacle Lens",
    "LENSES": "Spectacle Lens",
    "CONTACT_LENS": "Contact Lens",
    "CONTACT_LENSES": "Contact Lens",
    "CONTACTLENS": "Contact Lens",
    "READING_GLASSES": "Reading Glasses",
    "READERS": "Reading Glasses",
    "WATCH": "Watch",
    "WATCHES": "Watch",
    "MECHANICAL_WATCH": "Watch",
    "QUARTZ_WATCH": "Watch",
    "SMART_WATCH": "Watch",
    "ACCESSORIES": "Accessory",
    "ACCESSORY": "Accessory",
    "CASE": "Accessory",
    "CLOTH": "Accessory",
    "SERVICE": "Service",
    "SERVICES": "Service",
    "REPAIR": "Service",
}


def _category_label(category: Optional[str]) -> str:
    if not category:
        return "Item"
    key = str(category).upper().replace("-", "_").replace(" ", "_")
    key = "_".join(p for p in key.split("_") if p)
    if key in _CATEGORY_LABELS:
        return _CATEGORY_LABELS[key]
    # Title-case the raw category so we never show UPPER_CASE to a customer.
    return " ".join(w[:1] + w[1:].lower() for w in key.split("_") if w) or "Item"


def _describe_for_customer(item: Dict[str, Any]) -> str:
    """"Brand Category" line; falls back to subbrand, then product name."""
    brand = (item.get("brand") or "").strip()
    cat = _category_label(item.get("category") or item.get("item_type"))
    if brand:
        return f"{brand} {cat}"
    sub = (item.get("subbrand") or "").strip()
    if sub:
        return f"{sub} {cat}"
    name = (item.get("product_name") or item.get("name") or "").strip()
    return name or cat


# ============================================================================
# ORDER TRACKING
# ============================================================================


def ensure_tracking_token(order_repo, order: Dict[str, Any]) -> str:
    """Return the order's tracking token, lazily minting + persisting one if
    missing. Backfill-safe: old orders created before this feature get a
    token the first time anyone looks them up. Fail-soft -- if the persist
    fails we still return the freshly minted token so the link works for
    this request (it just won't be queryable until a later write succeeds)."""
    token = order.get("tracking_token")
    if token:
        return token
    token = secrets.token_urlsafe(24)
    try:
        if order_repo is not None and order.get("order_id"):
            order_repo.update(order["order_id"], {"tracking_token": token})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[PORTAL] tracking-token backfill failed: %s", exc)
    return token


def _safe_status_history(order: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Project status_history to customer-safe fields only (status + when).
    We strip changed_by -- the customer doesn't need staff identities."""
    out: List[Dict[str, Any]] = []
    for entry in order.get("status_history") or []:
        if not isinstance(entry, dict):
            continue
        out.append(
            {
                "status": entry.get("status"),
                "timestamp": entry.get("timestamp") or entry.get("changed_at"),
            }
        )
    # If there's no recorded history at all, synthesize a single "placed"
    # row from created_at so the timeline never renders empty.
    if not out:
        created = order.get("created_at")
        status = order.get("status") or "DRAFT"
        out.append({"status": status, "timestamp": created})
    return out


def _store_public_contact(store_id: Optional[str]) -> Dict[str, Optional[str]]:
    """Resolve a store's customer-facing name + phone. Fail-soft -> blanks."""
    if not store_id:
        return {"name": None, "phone": None}
    try:
        from ..dependencies import get_store_repository

        repo = get_store_repository()
        if repo is None:
            return {"name": None, "phone": None}
        store = repo.find_by_id(store_id)
        if not store:
            return {"name": None, "phone": None}
        return {
            "name": store.get("store_name") or store.get("name"),
            "phone": store.get("phone") or store.get("contact_phone"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("[PORTAL] store contact lookup failed: %s", exc)
        return {"name": None, "phone": None}


def _public_order_view(order: Dict[str, Any]) -> Dict[str, Any]:
    """Build the SAFE subset returned to an unauthenticated tracker.

    Deliberately excludes: cost_at_sale, margin, unit_price, discounts,
    salesperson, customer_id, internal notes, payment amounts, lens
    reservations -- anything an outsider shouldn't see. Only what a customer
    needs to know "where is my order".
    """
    items = [
        {
            "description": _describe_for_customer(it),
            "quantity": it.get("quantity", 1),
        }
        for it in (order.get("items") or [])
    ]
    store = _store_public_contact(order.get("store_id"))
    return {
        "order_number": order.get("order_number"),
        "status": order.get("status"),
        "status_history": _safe_status_history(order),
        "expected_delivery": order.get("expected_delivery"),
        "delivery_priority": order.get("delivery_priority"),
        "placed_at": order.get("created_at"),
        "item_count": sum(
            int(it.get("quantity", 1) or 1) for it in (order.get("items") or [])
        ),
        "items": items,
        # First name only -- enough for "Hi Avinash" without leaking the
        # full record. We never echo the phone back to a public link.
        "customer_first_name": (order.get("customer_name") or "").strip().split(" ")[0]
        or None,
        "store_name": store["name"],
        "store_phone": store["phone"],
    }


@router.get("/track/{token}")
async def track_order(
    request: Request,
    token: str = Path(..., min_length=16, max_length=128),
):
    """PUBLIC. Look up an order by its tracking token and return a safe
    subset. 404 on any unknown / malformed token (same response either way
    blocks enumeration)."""
    _check_rate(_track_log, _client_ip(request), _TRACK_RATE_LIMIT, "tracking")

    try:
        from ..dependencies import get_order_repository

        repo = get_order_repository()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[PORTAL] order repo unavailable: %s", exc)
        repo = None

    if repo is None:
        raise HTTPException(status_code=404, detail="Order not found")

    order = repo.find_one({"tracking_token": token})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    return _public_order_view(order)


# ============================================================================
# Rx OTP FLOW
# ============================================================================


class RxOtpRequest(BaseModel):
    phone: str = Field(..., min_length=6, max_length=20)


class RxOtpVerify(BaseModel):
    phone: str = Field(..., min_length=6, max_length=20)
    otp: str = Field(..., min_length=4, max_length=8)


def _normalize_phone(phone: str) -> str:
    """Digits only, drop trunk 0, add 91 for bare 10-digit Indian numbers.
    Mirrors agents.providers._normalize_phone so OTP lookups match the form
    used when sending."""
    p = "".join(c for c in (phone or "") if c.isdigit())
    if not p:
        return ""
    if p.startswith("0"):
        p = p[1:]
    if not p.startswith("91") and len(p) == 10:
        p = "91" + p
    return p


def _hash_otp(otp: str, phone_norm: str) -> str:
    """Salt the OTP with the normalized phone so two customers with the same
    random OTP don't collide and a stolen hash isn't a bare 6-digit rainbow
    lookup."""
    return hashlib.sha256(f"{otp}:{phone_norm}".encode()).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _store_otp(db, phone_norm: str, customer_id: str, otp: str) -> None:
    """Persist a hashed OTP row to `otp_codes`. One active row per phone --
    we delete prior rows so a customer requesting a new OTP invalidates the
    old one (prevents an attacker racing two outstanding codes)."""
    coll = db.get_collection("otp_codes")
    try:
        coll.delete_many({"phone": phone_norm})
    except Exception:  # noqa: BLE001
        pass
    now = _utcnow()
    coll.insert_one(
        {
            "phone": phone_norm,
            "customer_id": customer_id,
            "otp_hash": _hash_otp(otp, phone_norm),
            "attempts": 0,
            "max_attempts": _OTP_MAX_ATTEMPTS,
            "created_at": now,
            "expires_at": now + timedelta(seconds=_OTP_TTL_SECONDS),
        }
    )


async def _send_otp_transactional(
    phone_norm: str, customer_id: str, customer_name: str, store_id: str, otp: str
) -> None:
    """Send the OTP as a TRANSACTIONAL message.

    An OTP is transactional, NOT promotional, so it MUST bypass the
    MEGAPHONE promotional DND window (9PM-9AM). We therefore call the raw
    provider send paths in agents.providers DIRECTLY -- those have no DND
    gate (DND lives in the MEGAPHONE scheduler, not the transport) -- and we
    also log the send to notification_logs with category='TRANSACTIONAL' for
    the audit trail. Fail-soft: any send error is logged, never raised (the
    endpoint still returns generic success so we don't leak existence).
    """
    message = (
        f"Your Better Vision verification code is {otp}. "
        f"It expires in {_OTP_TTL_SECONDS // 60} minutes. Do not share it with anyone."
    )

    # Audit log row (always written, even in DISPATCH_MODE=off) so there's a
    # record an OTP was generated. category='TRANSACTIONAL' marks it as
    # DND-exempt for any future reviewer. We NEVER persist the plaintext OTP.
    try:
        from ..services.notification_service import send_notification

        await send_notification(
            store_id=store_id or "",
            customer_id=customer_id,
            customer_phone=phone_norm,
            customer_name=customer_name or "",
            template_id="RX_PORTAL_OTP",
            channel="SMS",
            variables={"otp": "******"},
            category="TRANSACTIONAL",
            triggered_by="portal_rx_otp",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[PORTAL] OTP notification-log failed: %s", exc)

    # Actual transport. send_sms / send_whatsapp are themselves fail-soft and
    # honor DISPATCH_MODE (off -> SIMULATED). They do NOT apply any DND gate.
    try:
        from agents.providers import send_sms

        result = await send_sms(phone_norm, message)
        if getattr(result, "status", "") == "SIMULATED":
            # In off/test mode the SMS isn't really sent. Log the OTP so a
            # developer/operator can complete the flow during testing.
            logger.info(
                "[PORTAL] OTP for %s (SIMULATED dispatch): %s", phone_norm[-4:], otp
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[PORTAL] OTP SMS send failed: %s", exc)


def _generate_otp() -> str:
    """Cryptographically-random 6-digit code (000000-999999)."""
    return f"{secrets.randbelow(1_000_000):06d}"


@router.post("/rx/request-otp")
async def request_rx_otp(payload: RxOtpRequest, request: Request):
    """PUBLIC. Generate + send an OTP for Rx access.

    ALWAYS returns a generic success regardless of whether the phone maps to
    a real customer -- this is deliberate: revealing "no such customer" leaks
    who is/isn't in the database. Rate-limited per phone.
    """
    phone_norm = _normalize_phone(payload.phone)
    # Generic envelope used for every outcome (existing, not-existing, db-down)
    generic = {
        "ok": True,
        "message": "If this number is registered with us, a verification code has been sent.",
        "expires_in_seconds": _OTP_TTL_SECONDS,
    }

    if not phone_norm:
        # Don't even reveal "bad phone" beyond the generic message.
        return generic

    _check_rate(_otp_req_log, phone_norm, _OTP_REQ_RATE_LIMIT, "verification code")

    try:
        db = _get_db()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[PORTAL] db unavailable for OTP request: %s", exc)
        db = None
    if db is None:
        return generic

    # Look up the customer. find_by_mobile matches both `mobile` and `phone`
    # fields; we also try the normalized 91-prefixed and bare 10-digit forms.
    try:
        from ..dependencies import get_customer_repository

        cust_repo = get_customer_repository()
    except Exception:  # noqa: BLE001
        cust_repo = None

    customer = None
    if cust_repo is not None:
        for candidate in {phone_norm, phone_norm[-10:], payload.phone.strip()}:
            if not candidate:
                continue
            customer = cust_repo.find_by_mobile(candidate)
            if customer:
                break

    if not customer:
        # No such customer -- still return generic success (no enumeration).
        return generic

    otp = _generate_otp()
    try:
        _store_otp(
            db,
            phone_norm,
            customer.get("customer_id") or customer.get("id") or "",
            otp,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[PORTAL] OTP persist failed: %s", exc)
        return generic

    await _send_otp_transactional(
        phone_norm,
        customer.get("customer_id") or "",
        customer.get("name") or "",
        customer.get("store_id") or customer.get("home_store_id") or "",
        otp,
    )

    # In explicit debug mode, echo the OTP so automated/local testing can
    # complete the flow without a live SMS gateway. Guarded behind an env
    # var that must NEVER be set in production.
    if _OTP_DEBUG:
        return {**generic, "debug_otp": otp}
    return generic


def _issue_view_token(customer_id: str) -> str:
    """Mint a short-lived signed JWT scoped to one customer_id, with a
    portal-specific audience so it can't be replayed against the main app."""
    now = _utcnow()
    payload = {
        "sub": customer_id,
        "customer_id": customer_id,
        "scope": "portal_rx",
        "aud": _VIEW_TOKEN_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(minutes=_VIEW_TOKEN_TTL_MINUTES),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def _decode_view_token(token: str) -> Dict[str, Any]:
    """Decode + validate a portal view token. Raises 401 on any problem."""
    try:
        return jwt.decode(
            token,
            _jwt_secret(),
            algorithms=["HS256"],
            audience=_VIEW_TOKEN_AUDIENCE,
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401, detail="Your access link has expired. Please verify again."
        )
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=401, detail="Invalid access token")


@router.post("/rx/verify-otp")
async def verify_rx_otp(payload: RxOtpVerify):
    """PUBLIC. Verify an OTP and, on success, issue a short-lived view token
    scoped to the customer. Locks the row out after too many wrong tries."""
    phone_norm = _normalize_phone(payload.phone)
    if not phone_norm:
        raise HTTPException(status_code=400, detail="A valid phone number is required")

    try:
        db = _get_db()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[PORTAL] db unavailable for OTP verify: %s", exc)
        db = None
    if db is None:
        raise HTTPException(
            status_code=503, detail="Verification is temporarily unavailable"
        )

    coll = db.get_collection("otp_codes")
    row = coll.find_one({"phone": phone_norm})
    if not row:
        raise HTTPException(
            status_code=400,
            detail="No verification code found. Please request a new one.",
        )

    # Expiry check (independent of attempts).
    expires_at = row.get("expires_at")
    if isinstance(expires_at, datetime):
        # Mongo may return naive datetimes; treat them as UTC.
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if _utcnow() > expires_at:
            try:
                coll.delete_many({"phone": phone_norm})
            except Exception:  # noqa: BLE001
                pass
            raise HTTPException(
                status_code=400,
                detail="Your verification code has expired. Please request a new one.",
            )

    # Attempt lockout.
    attempts = int(row.get("attempts", 0) or 0)
    max_attempts = int(row.get("max_attempts", _OTP_MAX_ATTEMPTS) or _OTP_MAX_ATTEMPTS)
    if attempts >= max_attempts:
        try:
            coll.delete_many({"phone": phone_norm})
        except Exception:  # noqa: BLE001
            pass
        raise HTTPException(
            status_code=429,
            detail="Too many incorrect attempts. Please request a new code.",
        )

    expected_hash = row.get("otp_hash")
    provided = "".join(c for c in (payload.otp or "") if c.isdigit())
    if not provided or _hash_otp(provided, phone_norm) != expected_hash:
        # Wrong code -> bump attempts. Report remaining tries so the customer
        # knows when they're about to be locked out.
        remaining = max_attempts - (attempts + 1)
        try:
            coll.update_one({"phone": phone_norm}, {"$inc": {"attempts": 1}})
        except Exception:  # noqa: BLE001
            pass
        if remaining <= 0:
            try:
                coll.delete_many({"phone": phone_norm})
            except Exception:  # noqa: BLE001
                pass
            raise HTTPException(
                status_code=429,
                detail="Too many incorrect attempts. Please request a new code.",
            )
        raise HTTPException(
            status_code=400,
            detail=f"Incorrect code. {remaining} attempt(s) remaining.",
        )

    # Success -> consume the OTP (single-use) and mint a view token.
    customer_id = row.get("customer_id") or ""
    try:
        coll.delete_many({"phone": phone_norm})
    except Exception:  # noqa: BLE001
        pass

    if not customer_id:
        raise HTTPException(
            status_code=400, detail="We couldn't match this code to a customer."
        )

    token = _issue_view_token(customer_id)
    return {
        "ok": True,
        "view_token": token,
        "token_type": "bearer",
        "expires_in": _VIEW_TOKEN_TTL_MINUTES * 60,
    }


def _safe_prescription_view(rx: Dict[str, Any]) -> Dict[str, Any]:
    """Project a prescription to the read-only fields a customer should see.
    Keeps clinical values (SPH/CYL/AXIS/ADD) -- those ARE the customer's own
    data -- but drops internal optometrist IDs / audit columns."""
    return {
        "prescription_id": rx.get("prescription_id") or rx.get("id"),
        "prescription_number": rx.get("prescription_number"),
        "prescription_date": rx.get("prescription_date"),
        "expiry_date": rx.get("expiry_date"),
        "type": rx.get("type") or rx.get("prescription_type"),
        "right_eye": rx.get("right_eye") or rx.get("od"),
        "left_eye": rx.get("left_eye") or rx.get("os"),
        "pd": rx.get("pd") or rx.get("pupillary_distance"),
        "add_power": rx.get("add_power") or rx.get("add"),
        "notes": rx.get("remarks") or rx.get("notes"),
        "optometrist_name": rx.get("optometrist_name"),
        "store_name": rx.get("store_name"),
    }


@router.get("/rx")
async def get_my_prescriptions(request: Request):
    """OTP-GATED. Return the authenticated customer's prescriptions.

    Auth is the portal view token in the Authorization header
    (`Bearer <view_token>`), issued by /rx/verify-otp. The token is scoped to
    a single customer_id, so no other customer's data is reachable.
    """
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401, detail="Verification required to view prescriptions"
        )
    token = auth.split(" ", 1)[1].strip()
    claims = _decode_view_token(token)
    if claims.get("scope") != "portal_rx":
        raise HTTPException(status_code=401, detail="Invalid access token")
    customer_id = claims.get("customer_id") or claims.get("sub")
    if not customer_id:
        raise HTTPException(status_code=401, detail="Invalid access token")

    try:
        from ..dependencies import get_prescription_repository, get_customer_repository

        rx_repo = get_prescription_repository()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[PORTAL] prescription repo unavailable: %s", exc)
        rx_repo = None

    if rx_repo is None:
        return {"customer_id": customer_id, "prescriptions": [], "count": 0}

    rows = rx_repo.find_by_customer(customer_id) or []
    prescriptions = [_safe_prescription_view(r) for r in rows]

    # Customer display name (first name) for the greeting -- fail-soft.
    first_name = None
    try:
        cust_repo = get_customer_repository()
        if cust_repo is not None:
            cust = cust_repo.find_by_id(customer_id)
            if cust:
                first_name = (cust.get("name") or "").strip().split(" ")[0] or None
    except Exception:  # noqa: BLE001
        pass

    return {
        "customer_id": customer_id,
        "customer_first_name": first_name,
        "prescriptions": prescriptions,
        "count": len(prescriptions),
    }
