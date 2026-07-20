"""
IMS 2.0 - Marketing match-key hashing (Phase 0 identity spine)
==============================================================
The ONE place that turns a customer's raw PII (phone / email) into the
SHA-256 "match keys" that Google Customer Match and Meta Custom Audiences
consume. Nothing raw ever leaves IMS -- every ad-audience export hashes here
first (roadmap: "never raw PII leaves IMS -- hash first").

Normalisation contract (must match what Google/Meta expect, or the platforms
silently fail to match the hash):

  Phone -> E.164 with the +91 country code, then SHA-256 hex.
           We coalesce the phone||mobile dual-field quirk and reuse the single
           canonical Indian-mobile normaliser (services.phone.normalize_indian_mobile)
           so the hashed number is identical to the stored surface form. Bad /
           foreign / blank numbers fail SOFT (return None) -- never raise, so one
           malformed row can't blow up a whole export.

  Email -> trim + lowercase, then SHA-256 hex. (Google additionally strips
           gmail dots; we deliberately do NOT -- the plain lowercase+trim form
           is the common denominator both platforms accept, and dot-stripping a
           non-gmail address would corrupt the match. Kept simple + provider-safe.)

Owner decision honoured here: customers are mobile-primary, but an EMAIL-ONLY
contact is a VALID marketing contact. build_match_keys therefore emits whatever
keys it can (phone and/or email); contact_tier() labels the row so the caller
can tag email-only contacts and auto-upgrade them the moment a phone appears
(there is no stored state -- keys are recomputed from the live customer doc on
every export, so a later-added phone upgrades the tier for free).

DARK: this module performs NO network I/O. It only computes hashes.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional

from api.services.phone import normalize_indian_mobile

# India dial code. Match keys are E.164 (+<cc><national number>).
_INDIA_DIAL_CODE = "91"

# Order in which we coalesce the customer's phone fields. `mobile` is the
# canonical, always-normalised create-door field; `phone` is a legacy/import
# fallback some docs still carry.
_PHONE_FIELDS = ("mobile", "phone")


def normalize_email(email: Optional[str]) -> Optional[str]:
    """Trim + lowercase an email. Returns None for None/blank input.

    No format validation here (the customer create-door already validates on
    write); this is purely the canonicalisation Google/Meta hash against.
    """
    if email is None:
        return None
    cleaned = str(email).strip().lower()
    return cleaned or None


def to_e164_india(mobile: Optional[str]) -> Optional[str]:
    """Return the +91E.164 form of an Indian mobile, or None (fail-soft).

    Wraps normalize_indian_mobile: a blank value yields None, and an INVALID
    non-empty value (which the normaliser raises on) is swallowed to None so a
    single bad row never aborts an export.
    """
    try:
        bare = normalize_indian_mobile(mobile)
    except ValueError:
        return None
    if not bare:
        return None
    return f"+{_INDIA_DIAL_CODE}{bare}"


def sha256_hex(value: str) -> str:
    """Lowercase hex SHA-256 of the UTF-8 bytes of `value`."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_email(email: Optional[str]) -> Optional[str]:
    """SHA-256 hex of the normalised email, or None when there is no email."""
    normalised = normalize_email(email)
    return sha256_hex(normalised) if normalised else None


def hash_phone_e164(mobile: Optional[str]) -> Optional[str]:
    """SHA-256 hex of the +91 E.164 phone, or None when there is no valid phone."""
    e164 = to_e164_india(mobile)
    return sha256_hex(e164) if e164 else None


def coalesce_mobile(customer: Dict[str, Any]) -> Optional[str]:
    """Pick the customer's phone from the mobile||phone dual fields (mobile wins)."""
    for field in _PHONE_FIELDS:
        value = customer.get(field)
        if value:
            return value
    return None


def build_match_keys(customer: Dict[str, Any]) -> Dict[str, str]:
    """Build the hashed match-key dict for one customer document.

    Returns a dict with only the keys that could be produced:
        {"phone_sha256": "...", "email_sha256": "..."}
    An empty dict means the customer has NO usable contact identifier and cannot
    be exported to any ad audience.
    """
    keys: Dict[str, str] = {}
    phone_hash = hash_phone_e164(coalesce_mobile(customer))
    if phone_hash:
        keys["phone_sha256"] = phone_hash
    email_hash = hash_email(customer.get("email"))
    if email_hash:
        keys["email_sha256"] = email_hash
    return keys


def contact_tier(match_keys: Dict[str, str]) -> str:
    """Label a match-key dict by which identifiers it carries.

    PHONE_AND_EMAIL | PHONE_ONLY | EMAIL_ONLY | NONE. EMAIL_ONLY is a valid,
    exportable tier (owner decision) -- the caller tags it so it can be
    auto-upgraded when a phone is later captured.
    """
    has_phone = "phone_sha256" in match_keys
    has_email = "email_sha256" in match_keys
    if has_phone and has_email:
        return "PHONE_AND_EMAIL"
    if has_phone:
        return "PHONE_ONLY"
    if has_email:
        return "EMAIL_ONLY"
    return "NONE"
