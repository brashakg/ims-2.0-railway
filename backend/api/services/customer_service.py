"""
IMS 2.0 -- canonical ``ensure_customer`` service (unification step-5)
=====================================================================
ONE place every customer-entry door funnels through to turn "a person showed up
(at least a mobile)" into a single, de-duplicated customer record.

WHY THIS EXISTS
---------------
Before this, the find-or-create logic was copy-pasted across doors that quietly
DISAGREED (audit 2026-06-10, customer-entry divergence matrix):
  * walkouts.``_ensure_customer`` -- a local phone regex (rejected +91, accepted a
    0-leading non-mobile), a ``cust-``+hex8 id, a skeleton MISSING consent/is_active,
    and store keys (``primary_store_id``/``store_ids``) that the store-scoped customer
    lists never read -> the walk-in was invisible in those lists.
  * online_order_mapper.``_match_or_create_customer`` -- a uuid id, ONLINE channel,
    a different skeleton again (no consent/loyalty/patients), homed to the online
    bucket, plus the step-2 ``raw_phone`` audit field.
Same human entered at two doors -> potentially TWO records under two surface forms.

WHAT THIS GUARANTEES
--------------------
  * DEDUP FIRST. The mobile is normalized via the ONE canonical normalizer
    (``api.services.phone.normalize_indian_mobile``) and an existing customer is
    looked up by it BEFORE any create. ``+91 98...`` / ``098...`` / ``98765 43210``
    all collapse to the same bare 10-digit key, so the same person resolves to ONE
    record no matter which door they entered.
  * LENIENT create. Owner decision: STRICT entry is for PRODUCTS, not customers -- a
    walk-in with just a mobile is a valid customer. So a mobile alone is enough; any
    PROVIDED email/GSTIN/DOB is validated with the SAME helpers the canonical
    POST /customers uses (so a bad email/GSTIN is still rejected), but nothing extra
    is demanded.
  * ONE canonical skeleton shape (see ``_build_skeleton``) -- consistent across doors.
  * CONCURRENCY-SAFE. find -> create -> on a racing duplicate-mobile insert, RE-FIND
    and return the winner's id (never double-create under a race).
  * CONSENT AS-IS. Per owner, a MISSING consent flag means consented; this service
    does NOT add a consent gate and does NOT flip an existing flag.
  * NO online loyalty, NO comms (owner). This service only resolves identity.

PUBLIC API
----------
    ensure_customer(db, *, mobile, name=None, store_id=None, source, **extra)
        -> (customer_id: Optional[str], created: bool)

    ``customer_id`` is None ONLY when there is nothing to key on (blank mobile) or
    the customer repo is unreachable -- the caller decides whether to proceed with a
    null link (walkout) or fall through (online). NEVER raises on a DB/repo error;
    DOES raise ValueError on a genuinely invalid PROVIDED email/GSTIN/DOB (the door
    surfaces that as a clean 4xx) -- mobile validity is handled leniently (an
    unparseable mobile yields ``(None, False)``, matching the doors' prior behaviour
    of skipping the link rather than 500-ing a walk-in).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from .phone import normalize_indian_mobile

logger = logging.getLogger(__name__)

# Canonical source tags. Kept as a frozenset so a typo'd source from a future door
# is caught loudly rather than silently persisting an un-groupable tag.
VALID_SOURCES = frozenset({"POS", "CLINIC", "WALKOUT", "ONLINE"})

# Channel that each source belongs to. Only ONLINE is a non-store channel; the rest
# are in-store / staff-entered. Mirrors what online_order_mapper stamped ("ONLINE").
_SOURCE_CHANNEL = {
    "POS": "STORE",
    "CLINIC": "STORE",
    "WALKOUT": "STORE",
    "ONLINE": "ONLINE",
}


def _get_repo():
    """Resolve the CustomerRepository the same way every door does, so this service
    sees exactly the data the doors see. Returns None when the DB is unavailable
    (the caller then proceeds with a null customer link / fall-through)."""
    try:
        from ..dependencies import get_customer_repository

        return get_customer_repository()
    except Exception:  # noqa: BLE001 -- fail-soft: any import/DB error -> no repo
        logger.debug("[ENSURE_CUSTOMER] customer repo unavailable", exc_info=True)
        return None


def _validate_extras(extra: Dict[str, Any]) -> Dict[str, Any]:
    """Validate any PROVIDED email / gstin / dob with the SAME helpers the canonical
    POST /customers create model uses, so the lenient skeleton can never store a
    malformed email/GSTIN or a future DOB. Absent fields pass untouched (lenient).

    Raises ValueError (surfaced by the door as a 4xx) on a genuinely bad value.
    Returns the (possibly normalized) subset to merge onto the skeleton.
    """
    # Import lazily to avoid a router<->service import cycle at module load.
    from ..routers.customers import (
        _check_dob_not_future,
        _check_email,
        _check_gstin,
    )

    out: Dict[str, Any] = {}
    if "email" in extra and extra["email"] is not None:
        out["email"] = _check_email(extra["email"])
    if "gstin" in extra and extra["gstin"] is not None:
        out["gstin"] = _check_gstin(extra["gstin"])
    if "dob" in extra and extra["dob"] is not None:
        dob = extra["dob"]
        _check_dob_not_future(dob)
        # Persist a date as ISO string to match the canonical create path.
        out["dob"] = dob.isoformat() if hasattr(dob, "isoformat") else dob
    return out


def _build_skeleton(
    *,
    mobile: str,
    raw_phone: Optional[str],
    name: Optional[str],
    store_id: Optional[str],
    source: str,
    validated_extra: Dict[str, Any],
) -> Dict[str, Any]:
    """The ONE canonical minimal-customer shape every door creates.

    Stores the number under BOTH ``mobile`` and ``phone`` (the repo's find_by_mobile
    ORs the two; writing both keeps the new doc discoverable by either and consistent
    with the canonical POST /customers). Store reference is written under BOTH the
    native key (``home_store_id``/``preferred_store_id``) AND the import-style keys
    (``primary_store_id``/``store_ids``) so the doc is visible in EVERY store-scoped
    customer list regardless of which key that list filters on (the walkout bug:
    a skeleton homed only on primary_store_id was invisible in the native lists).

    Consent is left ABSENT (owner: missing == consented) -- NO consent gate added.
    """
    now = datetime.now(timezone.utc).isoformat()
    customer_id = str(uuid.uuid4())
    skeleton: Dict[str, Any] = {
        "customer_id": customer_id,
        "name": name or "Customer",
        "mobile": mobile,
        "phone": mobile,
        # Original buyer-supplied phone kept verbatim for traceability (step-2). Only
        # set when it differs / is provided -- defaults to the bare form a staffer typed.
        "raw_phone": raw_phone if raw_phone is not None else mobile,
        "customer_type": "B2C",
        "source": source,
        "channel": _SOURCE_CHANNEL.get(source, "STORE"),
        # Store reference under every key a customer list might filter on.
        "home_store_id": store_id,
        "preferred_store_id": store_id,
        "primary_store_id": store_id,
        "store_ids": [store_id] if store_id else [],
        "is_active": True,
        "loyalty_points": 0,
        "store_credit": 0.0,
        "total_purchases": 0,
        "patients": [],
        "created_at": now,
        "updated_at": now,
    }
    skeleton.update(validated_extra)
    return skeleton


def _refind(repo, mobile: str, email: Optional[str]) -> Optional[str]:
    """Re-read by mobile then email after a racing/duplicate insert. Returns the
    winner's customer_id or None. Never raises."""
    try:
        if mobile:
            found = repo.find_by_mobile(mobile)
            if found and found.get("customer_id"):
                return found.get("customer_id")
        if email:
            finder = getattr(repo, "find_by_email", None)
            if callable(finder):
                found = finder(email)
                if found and found.get("customer_id"):
                    return found.get("customer_id")
    except Exception:  # noqa: BLE001
        logger.debug("[ENSURE_CUSTOMER] re-find after race failed", exc_info=True)
    return None


def ensure_customer(
    db,
    *,
    mobile: Optional[str],
    name: Optional[str] = None,
    store_id: Optional[str] = None,
    source: str,
    **extra: Any,
) -> Tuple[Optional[str], bool]:
    """Resolve (or create) the ONE canonical customer for a person entering at any
    door. See the module docstring for the full contract.

    Args:
        db: the database handle (accepted for caller symmetry / future direct-index
            use). The repo is resolved via the shared accessor so this service sees
            the same data the doors do; ``db`` being None does not by itself prevent
            resolution.
        mobile: human-entered phone (any surface form). Normalized internally.
        name: optional display name for a NEW record (ignored when matching existing).
        store_id: optional store to home a NEW record to.
        source: one of VALID_SOURCES (POS | CLINIC | WALKOUT | ONLINE).
        **extra: optional ``email`` / ``gstin`` / ``dob`` (validated when provided),
            and an optional ``raw_phone`` (the verbatim input; defaults to the
            normalized mobile).

    Returns:
        (customer_id, created):
          * (id, False) -- matched an existing customer by normalized mobile.
          * (new_id, True) -- created a fresh skeleton.
          * (id, False) -- a racing create lost; the winner's id is returned.
          * (None, False) -- nothing to key on (blank/unparseable mobile) OR the
            repo is unreachable OR the create failed. Caller decides what to do.

    Raises:
        ValueError -- only when a PROVIDED email/gstin/dob is malformed (mirrors the
        canonical create validators). Mobile is handled leniently (never raises).
    """
    if source not in VALID_SOURCES:
        raise ValueError(
            f"ensure_customer source must be one of {sorted(VALID_SOURCES)}, got '{source}'"
        )

    # --- normalize the dedup key (lenient: blank/junk -> no link, never raises) ----
    raw_phone = extra.pop("raw_phone", None)
    if raw_phone is None and mobile is not None:
        raw_phone = str(mobile)
    try:
        norm_mobile = normalize_indian_mobile(mobile)
    except ValueError:
        # An unparseable mobile is NOT a hard error here -- the doors historically
        # just skipped the customer link rather than 500 a walk-in. Mirror that.
        norm_mobile = None
    if not norm_mobile:
        return (None, False)

    repo = _get_repo()
    if repo is None:
        return (None, False)

    # --- DEDUP FIRST: an existing customer by normalized mobile wins ---------------
    try:
        existing = repo.find_by_mobile(norm_mobile)
    except Exception:  # noqa: BLE001 -- fail-soft read
        logger.debug("[ENSURE_CUSTOMER] find_by_mobile failed", exc_info=True)
        existing = None
    if existing and existing.get("customer_id"):
        return (existing.get("customer_id"), False)

    # --- validate any provided email/gstin/dob (raises on bad value) ---------------
    validated_extra = _validate_extras(extra)
    email = validated_extra.get("email")

    # --- create the canonical skeleton --------------------------------------------
    skeleton = _build_skeleton(
        mobile=norm_mobile,
        raw_phone=raw_phone,
        name=name,
        store_id=store_id,
        source=source,
        validated_extra=validated_extra,
    )
    try:
        created = repo.create(skeleton)
        if created and created.get("customer_id"):
            return (created.get("customer_id"), True)
        # repo.create returned falsy (no exception): fall through to a re-find in
        # case a concurrent writer already inserted the same mobile.
    except Exception:  # noqa: BLE001 -- e.g. DuplicateKey on a unique-mobile index
        logger.debug("[ENSURE_CUSTOMER] create raced/failed; re-finding", exc_info=True)

    # --- a racing create won the unique-mobile guard: return the survivor ----------
    won = _refind(repo, norm_mobile, email)
    if won:
        return (won, False)
    return (None, False)
