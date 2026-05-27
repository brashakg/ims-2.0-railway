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
  and a 90-second expiry. It is NOT the raw IMS JWT.
- Deny-by-default roles: only SUPERADMIN / ADMIN / CATALOG_MANAGER may enter the
  online-store admin; everyone else (POS / clinical / cashier / accountant /
  store + area managers) is denied.
- BVI maps the token's EMAIL to an EXISTING online-store user (no account
  creation).
- jti single-use replay store: every minted token's jti is recorded in the
  `sso_jti` Mongo collection (TTL-indexed on `exp` so Mongo auto-purges expired
  rows). Since BVI cannot easily check IMS's Mongo (different DB, runtime),
  this protection lives at MINT time -- it guarantees the SAME jti cannot be
  re-minted within its lifetime. Replay of an issued token is bounded by the
  90s exp window enforced by the BVI verifier.

Fail-soft: if no signing key is configured the mint returns None and the caller
returns 503, so nothing breaks before the key is set on Railway.
"""

import base64
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

# Deny-by-default IMS-role -> BVI-role map. Keys not present here are refused.
IMS_TO_BVI_ROLE = {
    "SUPERADMIN": "ADMIN",
    "ADMIN": "ADMIN",
    "CATALOG_MANAGER": "CATALOG_MANAGER",
}

_DEFAULT_TTL = 90  # 90 seconds (Council Branch C: shortened from 300s)
JTI_COLLECTION = "sso_jti"


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


def claim_jti(db, jti: str, exp: int) -> bool:
    """Insert jti into the sso_jti collection; returns True if newly claimed,
    False if it has already been used.

    The collection has a TTL index on `exp` (epoch -> Date) so Mongo auto-purges
    expired rows; the protection is bounded by the token's exp window.

    Fail-soft: if db is None (no Mongo / startup race / dev mock), return True
    so SSO is not blocked when the DB is unavailable. Defence is layered:
    BVI's verifier still enforces aud/iss/scope/exp on the wire; this store is
    defence-in-depth against the SAME mint being re-issued with the same jti.
    """
    if db is None or not jti:
        return True
    try:
        coll = db[JTI_COLLECTION]
        # exp is epoch-seconds; Mongo TTL needs a real Date field.
        exp_dt = datetime.fromtimestamp(int(exp), tz=timezone.utc)
        coll.insert_one(
            {
                "_id": jti,
                "exp": exp_dt,
                "created_at": datetime.now(tz=timezone.utc),
            }
        )
        return True
    except Exception as exc:
        # DuplicateKeyError -> already used. Any other error -> fail-soft True.
        # We inspect the class name to avoid importing pymongo here.
        if exc.__class__.__name__ == "DuplicateKeyError":
            return False
        return True


def ensure_jti_indexes(db) -> bool:
    """Create the TTL index on sso_jti.exp. Idempotent; safe to call on every
    startup. Returns True on success, False if the DB is missing / the index
    cannot be created. Fail-soft -- the rest of SSO still works."""
    if db is None:
        return False
    try:
        coll = db[JTI_COLLECTION]
        # Mongo will auto-delete a doc when its `exp` Date passes (expireAfterSeconds=0).
        coll.create_index("exp", expireAfterSeconds=0, name="sso_jti_exp_ttl")
        return True
    except Exception:
        return False
