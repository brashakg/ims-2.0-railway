"""
IMS 2.0 - SSO into the e-commerce (online store / BVI) admin
============================================================
Mints a SHORT-LIVED, audience-scoped RS256 exchange token so an authorised IMS
user can jump into the online-store admin already logged in -- WITHOUT sharing
IMS's own session token or its HS256 secret.

Security design (from the BVI council):
- RS256: IMS signs with a PRIVATE key; BVI verifies with the matching PUBLIC
  key. BVI therefore cannot forge IMS tokens (asymmetric).
- Audience/issuer scoped: aud="bvi", iss="ims", scope="ecommerce", a unique jti,
  and a 5-minute expiry. It is NOT the raw IMS JWT.
- Deny-by-default roles: only SUPERADMIN / ADMIN / CATALOG_MANAGER may enter the
  online-store admin; everyone else (POS / clinical / cashier / accountant /
  store + area managers) is denied.
- BVI maps the token's EMAIL to an EXISTING online-store user (no account
  creation).

Fail-soft: if no signing key is configured the mint returns None and the caller
returns 503, so nothing breaks before the key is set on Railway.
"""

import base64
import os
import time
import uuid
from typing import Optional

# Deny-by-default IMS-role -> BVI-role map. Keys not present here are refused.
IMS_TO_BVI_ROLE = {
    "SUPERADMIN": "ADMIN",
    "ADMIN": "ADMIN",
    "CATALOG_MANAGER": "CATALOG_MANAGER",
}

_DEFAULT_TTL = 300  # 5 minutes


def mapped_bvi_role(roles) -> Optional[str]:
    """The BVI role for the FIRST allowed IMS role, or None if none are allowed."""
    for r in roles or []:
        if r in IMS_TO_BVI_ROLE:
            return IMS_TO_BVI_ROLE[r]
    return None


def _private_key() -> Optional[str]:
    """RS256 private key PEM from env. Accepts a raw PEM (newlines or \\n-escaped)
    or a base64-encoded PEM (ECOMMERCE_SSO_PRIVATE_KEY_B64)."""
    pem = os.getenv("ECOMMERCE_SSO_PRIVATE_KEY")
    if pem:
        return pem.replace("\\n", "\n")
    b64 = os.getenv("ECOMMERCE_SSO_PRIVATE_KEY_B64")
    if b64:
        try:
            return base64.b64decode(b64).decode("utf-8")
        except Exception:
            return None
    return None


def sso_configured() -> bool:
    return _private_key() is not None


def build_claims(user: dict, bvi_role: str, ttl_seconds: int = _DEFAULT_TTL) -> dict:
    """The exchange-token claims. Pure + testable."""
    now = int(time.time())
    return {
        "sub": user.get("user_id") or user.get("id") or "",
        "email": (user.get("email") or "").strip().lower(),
        "name": user.get("username") or user.get("name") or "",
        "role": bvi_role,
        "aud": "bvi",
        "iss": "ims",
        "scope": "ecommerce",
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + max(60, int(ttl_seconds)),
    }


def mint_sso_token(user: dict, ttl_seconds: int = _DEFAULT_TTL) -> Optional[str]:
    """Mint the RS256 exchange token for `user` (needs user_id, email, username,
    roles). Returns None if the role isn't allowed or no signing key is set."""
    role = mapped_bvi_role(user.get("roles"))
    if role is None:
        return None
    key = _private_key()
    if not key:
        return None
    try:
        import jwt  # PyJWT (RS256 needs the `cryptography` extra, already present)

        return jwt.encode(build_claims(user, role, ttl_seconds), key, algorithm="RS256")
    except Exception:
        return None
