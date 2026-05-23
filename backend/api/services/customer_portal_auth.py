"""
IMS 2.0 - Customer self-service portal auth
===========================================
A separate, narrow auth surface for CUSTOMERS (not staff) to view their own
prescriptions: phone -> OTP -> a short-lived, scoped token. The token carries
scope="customer_portal" so it can NEVER be confused with a staff JWT, and the
portal endpoints only ever read the token-bound customer's own data.

Pure helpers here (OTP hashing, token mint/verify) so the security logic is
unit-tested directly.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

import jwt

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
ALGORITHM = "HS256"
CUSTOMER_SCOPE = "customer_portal"  # MUST differ from staff tokens (no scope)
OTP_TTL_MINUTES = 10
TOKEN_TTL_MINUTES = 30
MAX_OTP_ATTEMPTS = 5


def generate_otp() -> str:
    """A 6-digit numeric OTP (cryptographically random)."""
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(otp: str, phone: str) -> str:
    """Salt the OTP with the phone + server secret so the stored value is
    useless if the collection leaks, and is bound to the phone."""
    msg = f"{phone}:{otp}".encode()
    return hashlib.sha256(msg + SECRET_KEY.encode()).hexdigest()


def verify_otp(otp: str, phone: str, stored_hash: Optional[str]) -> bool:
    """Constant-time compare against the stored hash."""
    if not stored_hash:
        return False
    return hmac.compare_digest(hash_otp(otp, phone), stored_hash)


def issue_customer_token(customer_id: str, phone: str, minutes: int = TOKEN_TTL_MINUTES) -> str:
    payload = {
        "sub": customer_id,
        "phone": phone,
        "scope": CUSTOMER_SCOPE,
        "exp": datetime.utcnow() + timedelta(minutes=minutes),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_customer_token(token: Optional[str]) -> Optional[dict]:
    """Decode + REQUIRE the customer_portal scope. A staff token (which has no
    such scope) returns None, so staff JWTs can't reach customer endpoints and
    vice-versa."""
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except Exception:  # noqa: BLE001 (expired/invalid/malformed)
        return None
    if payload.get("scope") != CUSTOMER_SCOPE or not payload.get("sub"):
        return None
    return payload


def otp_expired(expires_at: Optional[str], now: Optional[datetime] = None) -> bool:
    now = now or datetime.utcnow()
    if not expires_at:
        return True
    try:
        return now > datetime.fromisoformat(str(expires_at).replace("Z", ""))
    except ValueError:
        return True
