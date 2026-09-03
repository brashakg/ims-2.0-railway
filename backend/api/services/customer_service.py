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

FAMILY-MEMBER GUARD (owner ruling 2026-09-04: "block it outright")
------------------------------------------------------------------
A person may already be in the system as a FAMILY MEMBER (``patients[]``) on
someone else's account, with their own mobile. Creating a top-level customer
with that number splits their prescriptions and purchase history across two
records -- and the shops run Rx reminders off that history. So this ONE
function also checks ``patients.mobile`` before any create:
  * strict door (POST /customers, a human at the counter): raises
    ``CustomerConflict`` whose ``.detail`` is the 409 body the UI acts on
    (promote-to-own-account or open the existing account);
  * lenient doors (walkout / online / Shopify sync, unattended): resolve to the
    account that already holds the person -- a MATCH, never a create.
POST /customers used to carry its own copy of find-or-create; it now calls
this function (``strict=True``, ``doc=<its full record>``) so the guard cannot
be bypassed by any door.

PUBLIC API
----------
    ensure_customer(db, *, mobile, name=None, store_id=None, source,
                    doc=None, strict=False, **extra)
        -> (customer_id: Optional[str], created: bool)
    family_member_conflict(repo, mobile, exclude_customer_id=None) -> Optional[dict]
    promote_patient(db, repo, parent, patient_id) -> dict
    CustomerConflict (exception; ``.detail`` is the 409 body)

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


class CustomerConflict(Exception):
    """A create was refused because the number/email already identifies someone.
    ``detail`` is exactly what the HTTP door returns as the 409 body: a plain
    string for a top-level duplicate (unchanged wording), or a dict with a
    ``code`` for the family-member case so the UI can offer promote / open."""

    def __init__(self, detail):
        super().__init__(detail if isinstance(detail, str) else detail.get("message", ""))
        self.detail = detail


FAMILY_MEMBER_CODE = "MOBILE_BELONGS_TO_FAMILY_MEMBER"


def family_member_conflict(repo, mobile: str, exclude_customer_id: Optional[str] = None):
    """THE family-member rule. Returns the 409 body when ``mobile`` is already a
    family member's number on an account (other than ``exclude_customer_id``),
    else None. The body carries only what the UI needs to act -- the holder's
    id + name and the member's id/name/relation -- never the holder's record."""
    if not mobile:
        return None
    finder = getattr(repo, "find_by_patient_mobile", None)
    if not callable(finder):
        return None
    try:
        holder = finder(mobile)
    except Exception:  # noqa: BLE001 -- fail-soft read, like find_by_mobile
        logger.debug("[ENSURE_CUSTOMER] find_by_patient_mobile failed", exc_info=True)
        return None
    if not holder or not holder.get("customer_id"):
        return None
    if exclude_customer_id and holder.get("customer_id") == exclude_customer_id:
        return None
    member = next(
        (p for p in (holder.get("patients") or []) if p and p.get("mobile") == mobile),
        {},
    )
    holder_name = holder.get("name") or "an existing customer"
    member_name = member.get("name") or "a family member"
    return {
        "code": FAMILY_MEMBER_CODE,
        "message": (
            f"This number belongs to {member_name}, a family member on "
            f"{holder_name}'s account"
        ),
        "customer_id": holder.get("customer_id"),
        "account_holder_name": holder_name,
        "patient_id": member.get("patient_id"),
        "patient_name": member_name,
        "relation": member.get("relation"),
    }


def _get_repo(db=None):
    """Resolve the CustomerRepository this service reads/writes through.

    Prefers the DB HANDLE the door passed in -- the doors patch their own
    ``get_db`` in tests and resolve their repos from that same handle, so honouring
    it here keeps this service looking at EXACTLY the data the door sees (and keeps
    the doors' existing test wiring working without re-patching a second accessor).
    Falls back to the shared ``dependencies.get_customer_repository`` accessor when
    no usable handle is supplied. Returns None when the DB is unavailable (the
    caller then proceeds with a null customer link / fall-through)."""
    # 1) Build straight off the passed handle when it's a connected DB exposing the
    #    customers collection (matches dependencies.get_customer_repository's shape).
    if db is not None and getattr(db, "is_connected", False):
        try:
            from database.repositories.customer_repository import CustomerRepository

            return CustomerRepository(db.customers)
        except Exception:  # noqa: BLE001 -- fall through to the shared accessor
            logger.debug("[ENSURE_CUSTOMER] repo-from-handle failed", exc_info=True)
    # 2) Shared accessor (its own get_db()).
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
    doc: Optional[Dict[str, Any]] = None,
    strict: bool = False,
    repo=None,
    **extra: Any,
) -> Tuple[Optional[str], bool]:
    """Resolve (or create) the ONE canonical customer for a person entering at any
    door. See the module docstring for the full contract.

    Args:
        db: the database handle the door already holds. When it's a connected DB
            the customer repo is built straight off it (so this service sees exactly
            the data the door sees, and the doors' test wiring keeps working); a
            None/unusable handle falls back to the shared customer-repo accessor.
        mobile: human-entered phone (any surface form). Normalized internally.
        name: optional display name for a NEW record (ignored when matching existing).
        store_id: optional store to home a NEW record to.
        source: one of VALID_SOURCES (POS | CLINIC | WALKOUT | ONLINE).
        doc: the FULL record to insert instead of the lenient skeleton (the human
            create door passes its validated CustomerCreate record; its mobile must
            already be the canonical form). Mutated by the repo on insert.
        strict: the human-at-the-counter contract. An existing top-level match, a
            family-member match, an email duplicate or a race-lost insert RAISES
            ``CustomerConflict`` (the door maps it to 409) instead of resolving.
        **extra: optional ``email`` / ``gstin`` / ``dob`` (validated when provided),
            and an optional ``raw_phone`` (the verbatim input; defaults to the
            normalized mobile).

    Returns:
        (customer_id, created):
          * (id, False) -- matched an existing customer by normalized mobile, OR
            the account that already holds this number as a FAMILY MEMBER.
          * (new_id, True) -- created a fresh record.
          * (id, False) -- a racing create lost; the winner's id is returned.
          * (None, False) -- nothing to key on (blank/unparseable mobile) OR the
            repo is unreachable OR the create failed. Caller decides what to do.

    Raises:
        CustomerConflict -- strict only (see above).
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

    # A door that already holds its repository passes it (POST /customers), so the
    # service writes through exactly the handle that door -- and its tests -- see.
    repo = repo if repo is not None else _get_repo(db)
    if repo is None:
        return (None, False)

    # --- DEDUP FIRST: an existing customer by normalized mobile wins ---------------
    try:
        existing = repo.find_by_mobile(norm_mobile)
    except Exception:  # noqa: BLE001 -- fail-soft read
        logger.debug("[ENSURE_CUSTOMER] find_by_mobile failed", exc_info=True)
        existing = None
    if existing and existing.get("customer_id"):
        if strict:
            raise CustomerConflict("Customer with this mobile already exists")
        return (existing.get("customer_id"), False)

    # --- FAMILY-MEMBER GUARD: the number is already someone's family member -------
    # Strict door: refuse with the actionable body. Lenient doors: the person IS in
    # the system -- resolve to the account holding them, create nothing.
    family = family_member_conflict(repo, norm_mobile)
    if family:
        if strict:
            raise CustomerConflict(family)
        return (family["customer_id"], False)

    # --- validate any provided email/gstin/dob (raises on bad value) ---------------
    validated_extra = _validate_extras(extra)
    email = validated_extra.get("email") or (doc or {}).get("email")

    # Strict door only: an email already on file is a duplicate too (the lenient
    # doors deliberately never dedup on email -- families share addresses).
    if strict and email:
        try:
            dup_email = repo.find_by_email(email)
        except Exception:  # noqa: BLE001
            dup_email = None
        if dup_email is not None:
            raise CustomerConflict("Customer with this email already exists")

    # --- create: the door's full record, else the canonical skeleton --------------
    skeleton = doc if doc is not None else _build_skeleton(
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
    # (strict: that is a duplicate -> 409, never the old 500)
    won = _refind(repo, norm_mobile, email)
    if won:
        if strict:
            raise CustomerConflict("Customer with this mobile already exists")
        return (won, False)
    return (None, False)


# ---------------------------------------------------------------------------
# Promote a family member to their own account
# ---------------------------------------------------------------------------

# Collections keyed to a family member (patient_id) that carry a customer_id
# pointer. These are re-pointed at the promoted account. NOT re-pointed: orders
# (a bill belongs to the account that paid; it still names the member via its
# own patient_id) and handoffs (workflow items, not history).
PATIENT_KEYED_COLLECTIONS = ("prescriptions", "eye_tests", "eye_test_queue")


def promote_patient(db, repo, parent: Dict[str, Any], patient_id: str) -> Dict[str, Any]:
    """Take family member ``patient_id`` OUT of ``parent`` and make them a
    top-level customer, carrying their clinical history.

    ORDERING (why): create the new account FIRST, re-point the patient-keyed
    records SECOND, remove the member row from the parent LAST. The person is
    never in NEITHER place: a failure before the create changes nothing; a
    failure after it leaves them in BOTH places -- the same recoverable state
    the read-only repair script (scripts/family_member_split_report.py) already
    lists -- and the promoted account is returned in the error so nothing is
    hidden. The member keeps the SAME patient_id on the new account, which is
    what keeps every prescription/eye-test link intact.

    Raises ValueError (door -> 400) when the member cannot be promoted, and
    CustomerConflict (door -> 409) when the member's number already has its own
    top-level account (an existing split: owner decides per case, no auto-merge).
    Raises RuntimeError with ``.customer_id`` set when a step AFTER the create
    fails (door -> 500 carrying the id)."""
    from .member_billing import build_primary_member

    member = next(
        (
            p
            for p in (parent.get("patients") or [])
            if p and str(p.get("patient_id") or "") == str(patient_id)
        ),
        None,
    )
    if member is None:
        raise LookupError("Family member not found on this account")
    parent_id = parent.get("customer_id")
    if member.get("is_primary") or patient_id == parent.get("primary_patient_id"):
        raise ValueError("The account holder already owns this account and cannot be promoted")
    mobile = normalize_indian_mobile(member.get("mobile"))
    if not mobile:
        raise ValueError(
            "This family member has no mobile number; add one before promoting them"
        )
    other = repo.find_by_mobile(mobile)
    if other and other.get("customer_id"):
        raise CustomerConflict(
            {
                "code": "MOBILE_ALREADY_OWN_ACCOUNT",
                "message": "This family member already has their own customer account",
                "customer_id": other.get("customer_id"),
            }
        )

    now = datetime.now(timezone.utc).isoformat()
    primary = build_primary_member(
        name=member.get("name") or "Customer",
        mobile=mobile,
        patient_id=patient_id,  # STABLE: keeps the Rx / eye-test links
        relation="Self",
    )
    for k in ("dob", "anniversary"):
        if member.get(k) is not None:
            primary[k] = member.get(k)
    new_doc: Dict[str, Any] = {
        "customer_id": str(uuid.uuid4()),
        "customer_type": "B2C",
        "name": primary["name"],
        "mobile": mobile,
        "phone": mobile,
        "email": None,
        "dob": member.get("dob"),
        "anniversary": member.get("anniversary"),
        "gstin": None,
        "billing_address": None,
        # Consent was given by the account holder when this person's data was
        # recorded on the family account; it travels with the person.
        "marketing_consent": parent.get("marketing_consent", True),
        "data_consent": parent.get("data_consent"),
        "data_consent_at": parent.get("data_consent_at"),
        "data_consent_text_version": parent.get("data_consent_text_version"),
        "home_store_id": parent.get("home_store_id") or parent.get("preferred_store_id"),
        "preferred_store_id": parent.get("preferred_store_id") or parent.get("home_store_id"),
        "loyalty_points": 0,
        "store_credit": 0,
        "total_purchases": 0,
        "is_active": True,
        "patients": [primary],
        "primary_patient_id": patient_id,
        "promoted_from": {"customer_id": parent_id, "patient_id": patient_id, "at": now},
    }

    # 1) CREATE the own account (the only step with no prior side effect).
    created = repo.create(new_doc)
    if not created or not created.get("customer_id"):
        won = repo.find_by_mobile(mobile)
        raise CustomerConflict(
            {
                "code": "MOBILE_ALREADY_OWN_ACCOUNT",
                "message": "This family member already has their own customer account",
                "customer_id": (won or {}).get("customer_id"),
            }
        )
    new_id = created["customer_id"]

    try:
        # 2) RE-POINT every patient-keyed record at the promoted account.
        carried: Dict[str, int] = {}
        for name in PATIENT_KEYED_COLLECTIONS:
            coll = db.get_collection(name) if db is not None else None
            if coll is None:
                carried[name] = 0
                continue
            res = coll.update_many(
                {"patient_id": patient_id},
                {"$set": {"customer_id": new_id, "promoted_from_customer_id": parent_id}},
            )
            carried[name] = int(getattr(res, "modified_count", 0) or 0)
        # 3) REMOVE the member row from the parent (last: the only removal).
        if not repo.pull_patient(parent_id, patient_id):
            raise RuntimeError("family member row was not removed from the parent account")
    except Exception as exc:  # noqa: BLE001
        err = RuntimeError(
            f"Own account {new_id} was created but the move did not finish: {exc}. "
            "The person is now in BOTH places; run scripts/family_member_split_report.py"
        )
        err.customer_id = new_id  # type: ignore[attr-defined]
        raise err from exc

    out = dict(created)
    out["carried"] = carried
    return out
