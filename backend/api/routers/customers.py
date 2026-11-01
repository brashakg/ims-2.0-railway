"""
IMS 2.0 - Customers Router
===========================
Customer and patient management endpoints
"""

from api.utils.event_time import utc_now_iso

from fastapi import APIRouter, HTTPException, Depends, Query, Path, Body, Header
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Any, Dict, List, Optional
from datetime import date, datetime
import uuid
import logging
import re
from .auth import get_current_user, require_roles
from ..utils.ist import ist_day_start_utc, now_ist

# Roles allowed to mint customer monetary value (store credit, loyalty points).
# Defined above the endpoints so it can gate the add/issue/redeem routes.
_CREDIT_ROLES = ("ACCOUNTANT", "STORE_MANAGER", "AREA_MANAGER", "ADMIN")


def _sanitize_text(value: str) -> str:
    """Strip HTML tags and dangerous characters from user input."""
    if not value:
        return value
    # Remove HTML tags
    clean = re.sub(r"<[^>]+>", "", value)
    # Remove control characters except newline/tab
    clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", clean)
    return clean.strip()


# 15-character Indian GSTIN format.
# Pattern: 2-digit state code + 5-letter PAN prefix + 4-digit PAN year + 1 PAN check
#          + 1 entity number + 'Z' + 1 check character.
_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$")

# Basic e-mail sanity check (no external dependency).
# Deliberately permissive -- just ensures there is an @, a dot-separated domain,
# and no whitespace. Full RFC-5321 compliance is intentionally out of scope.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Allowed customer types.
# India-specific customer-create rules (enabled per product decision):
#  - mobile must be a valid Indian mobile: 10 digits with a leading 6-9
#  - a B2B customer must carry a valid GSTIN (Indian B2B invoicing requires it)
_VALID_CUSTOMER_TYPES = {"B2C", "B2B"}


async def _email_validation_stamp(email, source: str) -> Optional[dict]:
    """Validate-on-capture (MSG91 email validation) - FLAG, NEVER BLOCK.

    THE one implementation of the capture rule: the create door and the
    update door both call this and $set whatever comes back under
    `email_validation`. House pattern is flag-and-hold: an invalid/risky
    verdict is STAMPED on the doc for a human to see, and the write proceeds
    regardless - customer capture is never blocked by a deliverability guess.

    Dark-safe and spend-safe: agents.providers.validate_email returns None
    (no network call, nothing billed) unless DISPATCH_MODE is armed AND MSG91
    creds exist - so an unarmed deploy stamps nothing and the stored doc is
    byte-identical to before this feature. Never raises.
    """
    if not email:
        return None
    try:
        from agents.providers import validate_email

        verdict = await validate_email(email)
    except Exception:  # noqa: BLE001 - capture must never block on validation
        return None
    if not verdict:
        return None
    return {
        "status": verdict.get("status"),
        "raw": verdict.get("raw"),
        "checked_at": utc_now_iso(),
        "source": source,
    }


# ---------------------------------------------------------------------------
# Shared field validators -- the ONE place customer field rules live, so the
# CREATE model and the UPDATE model can never drift apart again (the audit
# flagged PUT /customers as skipping every validation POST /customers enforces).
# Each helper is the exact rule CustomerCreate used; both models call them.
# ---------------------------------------------------------------------------


def _check_gstin(v):
    """Validate 15-char Indian GSTIN FORMAT when a non-empty value is supplied.
    Absent/blank passes here (presence for B2B is enforced separately)."""
    if v and not _GSTIN_RE.match(v):
        raise ValueError(
            "GSTIN must be a valid 15-character Indian GSTIN (e.g. 27AAPFU0939F1ZV)"
        )
    return v


def _check_email(v):
    """Basic email FORMAT check when a non-empty value is provided."""
    if v and not _EMAIL_RE.match(v):
        raise ValueError("email must be a valid email address (e.g. a@b.com)")
    return v


def _check_customer_type(v):
    """Restrict to the known set {B2C, B2B}; reject unknown values early.
    None passes (UPDATE leaves the field unchanged when omitted)."""
    if v is not None and v not in _VALID_CUSTOMER_TYPES:
        raise ValueError(
            f"customer_type must be one of {sorted(_VALID_CUSTOMER_TYPES)}, got '{v}'"
        )
    return v


def _check_dob_not_future(v):
    """Reject a DOB that is in the future -- a birthdate cannot be tomorrow."""
    if v is not None and v > date.today():
        raise ValueError("Date of birth cannot be in the future")
    return v


from ..dependencies import (
    get_customer_repository,
    get_audit_repository,
    no_shop_scope_for,
    store_in_view,
    validate_store_access,
    can_access_store_scoped,
)
from ..services.cost_mask import mask_cost_list
from ..services.phone import normalize_indian_mobile


def _customer_store_id(doc):
    """The store a customer belongs to, for object-level scope checks.

    Reads EVERY key a customer can be homed on, because the write doors do not
    agree: customer_service._build_skeleton deliberately stamps home_store_id,
    preferred_store_id, primary_store_id AND store_ids together, its docstring
    recording that a skeleton homed only on primary_store_id was invisible to
    the native lists (the walkout bug). can_access_store_scoped treats an
    UNRESOLVED store as out of scope for every store-level role, so a key
    missing from this list is not a loose check - it is a customer their own
    shop can no longer open.

    Measured on production before widening: all 779 customers resolve on the
    first three keys alone (768 carry store_id), so this changes nothing today.
    It is here so that a row written through the other path later cannot
    silently disappear.
    """
    if not isinstance(doc, dict):
        return None
    direct = (
        doc.get("home_store_id")
        or doc.get("preferred_store_id")
        or doc.get("store_id")
        or doc.get("primary_store_id")
    )
    if direct:
        return direct
    ids = doc.get("store_ids")
    return ids[0] if isinstance(ids, list) and ids else None


def customer_in_scope(doc, current_user) -> bool:
    """THE store-ownership rule for a customer record.

    Do NOT re-implement this check inline. It was copied wrong repeatedly
    before it was single-sourced, and an audit of this file found THIRTEEN of
    nineteen per-customer doors with no ownership check at all - four of them
    moving money. The asymmetry that gave it away: reading the store-credit
    LEDGER was scoped, while adding, issuing and redeeming credit against it
    were not.

    Note that resolvers elsewhere (crm.query_customers_by_store,
    prescriptions, customer_repository.search_customers) still read their own
    key subsets for LIST queries. Those are query filters, not object-level
    permission checks; this predicate is the one that answers "may this user
    touch THIS customer"."""
    return can_access_store_scoped(_customer_store_id(doc), current_user)


def _scoped_customer_or_404(customer_id, current_user, *, write: bool = False):
    """Fetch a customer by id, enforcing OBJECT-LEVEL store scope.

    Closes the cross-store IDOR where any logged-in user could read/edit another
    store's customer by guessing an id (the list view was scoped; the single-
    record reads behind it were not). Cross-store roles (SUPERADMIN/ADMIN/
    AREA_MANAGER) always pass. Out-of-scope READ -> 404 (the id looks 'not
    found', so we don't even confirm it exists in another store); out-of-scope
    WRITE -> 403. Returns the customer doc."""
    repo = get_customer_repository()
    doc = repo.find_by_id(customer_id) if repo is not None else None
    if not doc:
        raise HTTPException(status_code=404, detail="Customer not found")
    if not customer_in_scope(doc, current_user):
        if write:
            raise HTTPException(
                status_code=403, detail="No access to this customer's store"
            )
        raise HTTPException(status_code=404, detail="Customer not found")
    return doc

router = APIRouter()
logger = logging.getLogger(__name__)


def _audit_customer(
    action: str,
    entity_id: Optional[str],
    current_user: dict,
    *,
    before_state: Optional[dict] = None,
    after_state: Optional[dict] = None,
    detail: Optional[dict] = None,
) -> None:
    """Best-effort domain audit for a customer action -> append-only audit_logs.

    "Audit Everything" (SYSTEM_INTENT): customer create / update / mobile-edit /
    patient-add were previously invisible in the Activity Log. This records a
    rich row (source="domain") with optional before/after state so a reviewer
    sees exactly what changed. FAIL-SOFT: any audit failure is swallowed -- it
    must never undo or 500 the customer write that triggered it. ``timestamp``
    is stamped explicitly because the Activity Log sorts + range-filters on it
    (BaseRepository only sets created_at/updated_at).
    """
    try:
        audit_repo = get_audit_repository()
        if audit_repo is None:
            return
        row = {
            "action": action,
            "entity_type": "CUSTOMER",
            "entity_id": entity_id,
            "store_id": current_user.get("active_store_id"),
            "user_id": current_user.get("user_id"),
            "user_name": current_user.get("full_name") or current_user.get("username"),
            "timestamp": datetime.utcnow(),
            "severity": "INFO",
            "source": "domain",
        }
        if before_state is not None:
            row["before_state"] = before_state
        if after_state is not None:
            row["after_state"] = after_state
        if detail is not None:
            row["detail"] = detail
        audit_repo.create(row)
    except Exception:  # noqa: BLE001 - audit must never break the business write
        pass


def _annotate_customer_matches(customers: List[Dict], query: str) -> List[Dict]:
    """Attach a ``match`` field to each search result so the UI can label
    account-holder vs family-member (patient) hits *authoritatively*, computed
    with the SAME token/field semantics as the query (base_repository.search):
    every whitespace token must appear somewhere across the entity's fields.

    Shape (additive / non-breaking):
        "match": {"account": bool, "matched_patient_ids": [str, ...]}

    The frontend (utils/customerSearchHits) prefers this when present and falls
    back to a client-side heuristic when absent (old/un-annotated responses)."""
    tokens = [t.lower() for t in (query or "").split() if t]

    def _all_in(*vals: Any) -> bool:
        if not tokens:
            return True
        joined = " ".join(str(v).lower() for v in vals if v)
        return all(t in joined for t in tokens)

    for c in customers or []:
        # No query -> browse mode: the account, never spurious patient rows.
        if not tokens:
            c["match"] = {"account": True, "matched_patient_ids": []}
            continue
        try:
            acct = _all_in(
                c.get("name"), c.get("mobile"), c.get("phone"), c.get("email")
            )
            matched_pids = [
                p.get("patient_id") or p.get("id") or p.get("name")
                for p in (c.get("patients") or [])
                if _all_in(p.get("name"), p.get("mobile"))
            ]
            c["match"] = {
                "account": bool(acct),
                "matched_patient_ids": [pid for pid in matched_pids if pid],
            }
        except Exception:  # noqa: BLE001 - labeling must never break search
            c["match"] = {"account": True, "matched_patient_ids": []}
    return customers


# ============================================================================
# Note: All customer data comes from the database (or seeded mock database)
# ============================================================================


# ============================================================================
# SCHEMAS
# ============================================================================


class PatientCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    mobile: Optional[str] = None
    dob: Optional[date] = None
    anniversary: Optional[date] = None
    relation: Optional[str] = None

    @field_validator("mobile", mode="before")
    @classmethod
    def normalize_patient_mobile(cls, v):
        """A family member's phone is OPTIONAL; when given, store the canonical
        bare 10-digit form (so it matches the account holder's + search/dedup)."""
        return normalize_indian_mobile(v)

    @field_validator("dob", mode="after")
    @classmethod
    def dob_not_future(cls, v):
        """Reject a DOB that is in the future -- a birthdate cannot be tomorrow."""
        if v is not None and v > date.today():
            raise ValueError("Date of birth cannot be in the future")
        return v


# Address is stored in TWO shapes that must never drift: a structured
# `billing_address` dict (what the GST place-of-supply / invoice logic reads --
# orders.py resolves CGST/SGST vs IGST off billing_address.state/.state_code)
# and flat top-level `address`/`city`/`state`/`pincode` fields (what the
# Customers detail + edit screen reads/writes). The create door only ever wrote
# the structured dict; the edit door only ever wrote the flat `address` string.
# So a created customer showed a blank address on the edit screen, and an edited
# address never reached the GST reader. This helper keeps both shapes in sync on
# every write so every screen -- and the invoice -- sees the same address.
_ADDRESS_FLAT_KEYS = ("address", "city", "state", "pincode")


def _sync_address_representations(data: dict, existing: Optional[dict] = None) -> None:
    """Reconcile the structured `billing_address` dict and the flat top-level
    address fields inside `data` (mutated in place).

    Merge order (later wins): existing billing_address -> supplied
    billing_address -> supplied flat fields. The merged structured dict is
    written back to BOTH `billing_address` and the flat top-level keys, so a
    reader of either shape sees the current value. No-op when the write carries
    no address content at all (so an unrelated edit never stamps an empty
    address)."""
    ba_in = data.get("billing_address")
    flat_sent = any(k in data for k in _ADDRESS_FLAT_KEYS)
    if not isinstance(ba_in, dict) and not flat_sent:
        return  # nothing address-related in this write

    merged: dict = {}
    existing_ba = (existing or {}).get("billing_address")
    if isinstance(existing_ba, dict):
        merged.update({k: v for k, v in existing_ba.items() if v is not None})
    if isinstance(ba_in, dict):
        merged.update({k: v for k, v in ba_in.items() if v is not None})
    for k in _ADDRESS_FLAT_KEYS:
        if k in data and data[k] is not None:
            merged[k] = data[k]

    if not merged:
        return

    data["billing_address"] = merged
    for k in _ADDRESS_FLAT_KEYS:
        if merged.get(k) is not None:
            data[k] = merged[k]


class CustomerCreate(BaseModel):
    customer_type: str = "B2C"  # B2C, B2B
    name: str = Field(..., min_length=2, max_length=120)
    # Normalized to the canonical bare 10-digit form via the shared phone util,
    # so +91 / 0 / spaces / dashes that staff actually type are accepted and
    # stored identically to a bare number (was a raw pattern that 422'd them and
    # could split the same customer across collections). Still required + still
    # ultimately ^[6-9]\d{9}$.
    mobile: str
    email: Optional[str] = None
    dob: Optional[date] = None
    anniversary: Optional[date] = None
    gstin: Optional[str] = None
    billing_address: Optional[dict] = None
    # Marketing opt-in defaults to True so the engine can include them in
    # birthday / Rx-expiry / WhatsApp campaigns. Operators flip this off
    # only when the customer explicitly declines on the spot.
    marketing_consent: bool = True
    # DPDP Act 2023: record that the customer agreed to us storing + using their
    # personal data. Distinct from marketing_consent (which only governs
    # promotional messages). Defaults True (the operator ticks it at the counter
    # after telling the customer); the SERVER stamps the timestamp + the policy
    # version actually shown, so consent is provable. `data_consent_text_version`
    # ties the agreement to whatever wording was live (editable under Marketing).
    data_consent: bool = True
    data_consent_text_version: Optional[str] = None
    patients: List[PatientCreate] = []

    @field_validator("name", mode="before")
    @classmethod
    def sanitize_name(cls, v):
        return _sanitize_text(v) if isinstance(v, str) else v

    @field_validator("mobile", mode="before")
    @classmethod
    def normalize_mobile(cls, v):
        """Accept +91 / 0 / spaced input; store the canonical bare 10-digit form.
        Required field -> a blank/None value raises (mobile is mandatory here)."""
        norm = normalize_indian_mobile(v)
        if not norm:
            raise ValueError("mobile is required (10-digit Indian mobile, 6-9)")
        return norm

    @field_validator("customer_type", mode="after")
    @classmethod
    def validate_customer_type(cls, v):
        return _check_customer_type(v)

    @field_validator("gstin", mode="after")
    @classmethod
    def validate_gstin(cls, v):
        return _check_gstin(v)

    @field_validator("email", mode="after")
    @classmethod
    def validate_email(cls, v):
        return _check_email(v)

    @field_validator("dob", mode="after")
    @classmethod
    def dob_not_future(cls, v):
        return _check_dob_not_future(v)

    @model_validator(mode="after")
    def b2b_requires_gstin(self):
        """A B2B customer must carry a GSTIN -- Indian B2B invoicing requires it.
        Format is already checked by validate_gstin; this enforces PRESENCE for
        B2B (B2C remains free to omit it)."""
        if self.customer_type == "B2B" and not (self.gstin and self.gstin.strip()):
            raise ValueError("A B2B customer requires a valid GSTIN")
        return self


class CustomerUpdate(BaseModel):
    # Editable fields when an existing customer is amended (e.g. operator
    # captures a DOB or marketing opt-out the customer didn't give on the
    # original visit). `patients`, when supplied, is APPENDED — never used
    # to replace the existing patient list (see endpoint logic).
    name: Optional[str] = None
    email: Optional[str] = None
    # `phone`/`mobile` + flat `address` are accepted because the edit form
    # (and TechCherry-imported docs) use these top-level keys. Before this,
    # CustomerUpdate had neither, so phone/address edits were silently
    # dropped (model_dump excluded the unknown fields) and the change never
    # persisted even though the request returned 200.
    phone: Optional[str] = None
    mobile: Optional[str] = None
    address: Optional[str] = None
    dob: Optional[date] = None
    anniversary: Optional[date] = None
    customer_type: Optional[str] = None
    gstin: Optional[str] = None
    billing_address: Optional[dict] = None
    marketing_consent: Optional[bool] = None
    patients: Optional[List[PatientCreate]] = None
    # POS-4: per-customer credit limit (khata). 0 or unset = no limit of
    # their own, so the chain default applies (effective_credit_limit).
    credit_limit: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False, strict=True)
    # F39 note: `tags` are deliberately NOT a field here. The generic
    # PUT /{customer_id} is only AUTHENTICATED -- exposing tags on this model
    # would let any role set them, bypassing the STORE_MANAGER+ gate, the
    # _clean_tag PII strip, and the suggest->approve workflow. Tags are written
    # ONLY via the governed PATCH /{customer_id}/tags endpoint.

    @field_validator("name", mode="before")
    @classmethod
    def sanitize_name_update(cls, v):
        return _sanitize_text(v) if isinstance(v, str) else v

    # Reuse CustomerCreate's exact field rules so the EDIT door no longer skips
    # the validation the CREATE door enforces (audit P1). All fields are
    # OPTIONAL on update: a None/omitted value passes the helper unchanged so a
    # partial edit that doesn't touch the field is never rejected.
    @field_validator("mobile", "phone", mode="before")
    @classmethod
    def normalize_mobile_update(cls, v):
        """Normalize an edited mobile/phone to the canonical bare 10-digit form
        (accepts +91 / 0 / spaced input); blank/None stays None. A present but
        INVALID number now 422s instead of silently persisting (was unvalidated)."""
        return normalize_indian_mobile(v)

    @field_validator("customer_type", mode="after")
    @classmethod
    def validate_customer_type_update(cls, v):
        return _check_customer_type(v)

    @field_validator("gstin", mode="after")
    @classmethod
    def validate_gstin_update(cls, v):
        return _check_gstin(v)

    @field_validator("email", mode="after")
    @classmethod
    def validate_email_update(cls, v):
        return _check_email(v)

    @field_validator("dob", mode="after")
    @classmethod
    def dob_not_future_update(cls, v):
        return _check_dob_not_future(v)


# ---------------------------------------------------------------------------
# Channel segregation (unification step-4)
# ---------------------------------------------------------------------------
# Online buyers and in-store customers live in the ONE `customers` collection
# (parity -- a person who shops both is a single mobile-deduped record). They are
# told apart not by a separate silo but by an ORIGIN tag: the canonical
# ensure_customer skeleton stamps channel='ONLINE'/source='ONLINE' on a
# Shopify-created record, and every online-joined customer (new skeleton OR
# pre-step-5) carries a `shopify_customer_id`. Any of those three marks a row as
# online-origin; their ABSENCE marks an in-store/staff-entered (POS/CLINIC/
# WALKOUT) customer. This builder turns a ?channel= value into the Mongo clause
# that segregates the two.

# Aliases the FE/segments may pass for the in-store side.
_ONLINE_CHANNEL_VALUES = {"ONLINE", "SHOPIFY", "ECOM", "ECOMMERCE", "WEB"}
_STORE_CHANNEL_VALUES = {"STORE", "WALKIN", "WALK-IN", "WALK_IN", "POS", "OFFLINE", "INSTORE", "IN-STORE"}


def _online_origin_or() -> List[Dict[str, Any]]:
    """The OR-branches that identify an online-origin customer (any one matches)."""
    return [
        {"channel": "ONLINE"},
        {"source": "ONLINE"},
        # Legacy Shopify-joined rows predate the channel/source tag but always
        # carry the Shopify linkage id.
        {"shopify_customer_id": {"$exists": True, "$nin": [None, ""]}},
    ]


def _row_is_online_origin(row: Dict[str, Any]) -> bool:
    """True when a customer DOC is online-origin (mirrors `_online_origin_or`):
    channel/source == 'ONLINE' OR a non-empty shopify_customer_id."""
    if str(row.get("channel") or "").upper() == "ONLINE":
        return True
    if str(row.get("source") or "").upper() == "ONLINE":
        return True
    sid = row.get("shopify_customer_id")
    return bool(sid) and sid not in (None, "")


def _row_matches_channel(row: Dict[str, Any], channel: Optional[str]) -> bool:
    """In-Python equivalent of `_build_channel_clause` for the search path (which
    returns repo rows, not a Mongo cursor). Unknown channel -> keep the row."""
    if not channel:
        return True
    val = str(channel).strip().upper()
    if val in _ONLINE_CHANNEL_VALUES:
        return _row_is_online_origin(row)
    if val in _STORE_CHANNEL_VALUES:
        return not _row_is_online_origin(row)
    return True


def _build_channel_clause(channel: Optional[str]) -> Optional[Dict[str, Any]]:
    """Return a Mongo filter fragment segregating online-origin vs in-store
    customers, or None when no recognised channel was requested (no filtering).

    - channel in {ONLINE, SHOPIFY, ...}  -> only online-origin rows ($or of the
      three online markers).
    - channel in {STORE, WALKIN, POS, ...} -> only in-store rows (NONE of the
      three online markers -> a $nor).
    Unknown / blank values return None (caller leaves the list unfiltered) so a
    stray query value can never silently empty the customer list.
    """
    if not channel:
        return None
    val = str(channel).strip().upper()
    if val in _ONLINE_CHANNEL_VALUES:
        return {"$or": _online_origin_or()}
    if val in _STORE_CHANNEL_VALUES:
        return {"$nor": _online_origin_or()}
    return None


# Order statuses excluded from the customer order-count / lifetime-spend stats:
# never-real (CANCELLED / DRAFT) and fully-refunded sales are not spend. Matches
# the dashboard_widgets convention. HISTORICAL (imported, settled pre-IMS) IS
# counted -- it is genuine past spend.
_CUSTOMER_STATS_EXCLUDED_STATUSES = {"CANCELLED", "DRAFT", "REFUNDED"}


def _attach_order_stats(rows: List[Dict[str, Any]], current_user: dict) -> None:
    """OS-026: the Online-customers screen renders 'Orders' / 'Lifetime' columns
    from fields (orders_count / total_spent) that NOTHING ever writes to customer
    docs -- every online shopper showed 0 orders / Rs 0 forever. Compute them
    server-side for the page being returned: one projected scan of the `orders`
    collection over the page's customer ids, summed in Python (portable across
    real Mongo and the seeded mock -- no aggregation pipeline needed for a page
    of <=500 customers). RESPONSE-ONLY enrichment: nothing is persisted.

    CROSS-STORE CALLERS ONLY (adversarial review, PR #947): the sum spans the
    WHOLE orders collection -- all stores and channels, which is exactly what
    makes it a truthful lifetime figure -- and store-pinned roles must never
    see cross-store money. A store-bounded caller therefore gets NO enrichment:
    the FE renders its honest em-dash for the absent fields, which is truthful,
    unlike a partial per-store sum wearing a 'Lifetime' label. (The consuming
    Online-customers screen is HQ-gated anyway.)

    Fail-soft: any error leaves the rows unchanged (the FE then renders its
    honest em-dash fallback, never a fake zero)."""
    from ..dependencies import user_store_scope

    is_cross, _allowed = user_store_scope(current_user)
    if not is_cross:
        return
    ids = [str(r.get("customer_id") or "") for r in rows if isinstance(r, dict)]
    ids = [i for i in ids if i]
    if not ids:
        return
    try:
        from ..dependencies import get_db

        db = get_db()
        if db is None or not getattr(db, "is_connected", True):
            return
        coll = db.get_collection("orders")
        if coll is None:
            return
        stats: Dict[str, Dict[str, float]] = {}
        cursor = coll.find(
            {"customer_id": {"$in": ids}},
            {"customer_id": 1, "grand_total": 1, "status": 1, "_id": 0},
        )
        for od in cursor:
            if not isinstance(od, dict):
                continue
            cid = str(od.get("customer_id") or "")
            if not cid:
                continue
            if str(od.get("status") or "").upper() in _CUSTOMER_STATS_EXCLUDED_STATUSES:
                continue
            entry = stats.setdefault(cid, {"n": 0.0, "sum": 0.0})
            entry["n"] += 1
            try:
                entry["sum"] += float(od.get("grand_total") or 0.0)
            except (TypeError, ValueError):
                pass
        for r in rows:
            if not isinstance(r, dict):
                continue
            entry = stats.get(str(r.get("customer_id") or ""), {"n": 0.0, "sum": 0.0})
            # A truthful zero: the ids were checked against the whole orders
            # collection, so 'no orders found' genuinely means zero orders.
            r["orders_count"] = int(entry["n"])
            r["total_spent"] = round(float(entry["sum"]), 2)
    except Exception:  # noqa: BLE001 - enrichment must never break the list
        pass


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.get("")
async def list_customers(
    search: Optional[str] = Query(None),
    customer_type: Optional[str] = Query(None),
    exclude_marketing: bool = Query(
        False,
        description=(
            "Additive tier filter (owner model 2026-07-20). When true, MARKETING-"
            "tier records (email-only online contacts with no phone -- no loyalty / "
            "no WhatsApp) are excluded, leaving only FULL customers. POS pickers "
            "pass this so a marketing contact never appears in a billing search. "
            "Default false keeps every existing list unchanged (nothing excluded)."
        ),
    ),
    channel: Optional[str] = Query(
        None,
        description=(
            "Segregate by ORIGIN channel (unification step-4). 'ONLINE' (aliases "
            "SHOPIFY/ECOM/WEB) returns only online-origin buyers; 'STORE' (aliases "
            "WALKIN/POS/OFFLINE) returns only in-store/staff-entered customers. "
            "Online and in-store customers live in the ONE customers collection -- "
            "this tag, not a separate silo, is how they are told apart. An online "
            "buyer later served in-store keeps channel=ONLINE (their origin) but "
            "still shows in-store activity via their orders. Omit to see everyone. "
            "An unrecognised value is ignored (never silently empties the list)."
        ),
    ),
    store_id: Optional[str] = Query(
        None,
        description=(
            "Filter customers by store. SUPERADMIN/ADMIN can pass any store_id "
            "to scope the view (used by the topbar store-switcher). Lower roles "
            "ignore this and always get their own active store."
        ),
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    current_user: dict = Depends(get_current_user),
):
    """List customers with optional filtering.

    Store scoping rules:
      - SUPERADMIN/ADMIN/AREA_MANAGER: see ALL customers by default; can
        narrow to a single store via ?store_id=<>. This is the path the
        topbar store-switcher uses.
      - Lower roles: always pinned to their own active_store_id; the
        ?store_id query param is ignored.

    The store filter matches BOTH `home_store_id` (legacy field) AND
    `preferred_store_id` (newer field used by TechCherry-imported
    customers). Before May 2026 only home_store_id was checked, which
    silently hid the 5,022 TechCherry-imported customers from /customers
    even when filtered by BV-PUN-01.

    Channel segregation (unification step-4): pass ?channel=ONLINE to see only
    online-origin (Shopify) buyers, or ?channel=STORE to exclude them. Online
    customers are unified into the SAME collection but stay identifiable by the
    canonical channel/source/shopify_customer_id tag -- this filter exposes that
    segregation to every list/segment built on /customers.
    """
    repo = get_customer_repository()

    if repo is not None:
        # Build filter
        filter_dict: Dict[str, Any] = {}
        if customer_type:
            filter_dict["customer_type"] = customer_type
        # Additive tier filter: exclude email-only MARKETING contacts when asked.
        # ``$ne`` also keeps docs where the field is ABSENT (normal customers +
        # FULL upgrades never carry it), so only true MARKETING rows drop out.
        if exclude_marketing:
            filter_dict["contact_tier"] = {"$ne": "MARKETING"}

        # Determine the effective store filter
        user_roles = current_user.get("roles", [])
        is_hq = any(r in user_roles for r in ["SUPERADMIN", "ADMIN", "AREA_MANAGER"])
        # R4 (owner ruling 2026-10-12): a non-admin login with NO shop -- an
        # AREA_MANAGER too -- sees no shop's customers (naming one is a 403);
        # it used to read every shop's customer list.
        no_shop = no_shop_scope_for(store_id, current_user)
        if no_shop:
            effective_store = no_shop
        elif is_hq:
            # HQ roles: honour explicit ?store_id, otherwise no scope.
            effective_store = store_id
        else:
            # Store-level roles: always pinned to their own shop (the active
            # one, else the first own shop: store_in_view).
            effective_store = store_in_view(None, current_user)

        store_clause: Optional[Dict[str, Any]] = None
        if effective_store:
            # Match either home_store_id (seed/old) or preferred_store_id
            # (TechCherry import + future inserts).
            store_clause = {
                "$or": [
                    {"home_store_id": effective_store},
                    {"preferred_store_id": effective_store},
                ]
            }

        # Channel segregation (step-4): online-origin vs in-store, by tag.
        channel_clause = _build_channel_clause(channel)

        # Compose the store + channel clauses. Both can carry their own top-level
        # boolean operator ($or for store, $or/$nor for channel), so when BOTH are
        # present we AND them under $and rather than letting one overwrite the
        # other's key. (Before this, only the store $or existed.)
        sub_clauses = [c for c in (store_clause, channel_clause) if c]
        if len(sub_clauses) == 2:
            filter_dict["$and"] = sub_clauses
        elif sub_clauses:
            filter_dict.update(sub_clauses[0])

        # If search provided, use search method (also respects store filter).
        # The search path can't compose the $and, so the channel tag is applied
        # post-hoc to the search results to keep segregation consistent.
        if search:
            rows = repo.search_customers(search, effective_store)
            if channel_clause is not None:
                rows = [r for r in rows if _row_matches_channel(r, channel)]
            if exclude_marketing:
                # Mirror the Mongo $ne filter for the search path (repo rows, not a
                # cursor): drop only rows explicitly tagged MARKETING.
                rows = [
                    r
                    for r in rows
                    if str(r.get("contact_tier") or "").upper() != "MARKETING"
                ]
            customers = _annotate_customer_matches(rows, search)
        else:
            customers = repo.find_many(filter_dict, skip=skip, limit=limit)

        # OS-026: the online-segment list drives the Online-customers screen's
        # Orders / Lifetime columns -- fields nothing persists. Enrich the page
        # server-side (response-only). Scoped to ?channel=ONLINE so the general
        # CRM list keeps its exact prior shape and cost; inside, cross-store
        # callers only (store-pinned roles never see cross-store money).
        if channel and str(channel).strip().upper() in _ONLINE_CHANNEL_VALUES:
            _attach_order_stats(customers, current_user)

        from ..utils.pagination import paginate

        total = repo.count(filter_dict) if not search else len(customers)
        page = (skip // limit) + 1 if limit > 0 else 1
        result = paginate(customers, page=page, page_size=limit, total=total)
        # Keep backward compat: also include "customers" key
        result["customers"] = result["data"]
        return result

    # No database available - return empty
    return {"customers": [], "total": 0}


@router.post("", status_code=201)
async def create_customer(
    customer: CustomerCreate, current_user: dict = Depends(get_current_user)
):
    """Create a new customer"""
    repo = get_customer_repository()

    if repo is not None:
        # Dedup + create live in ONE place: services.customer_service.ensure_customer
        # (strict=True below). It refuses a top-level duplicate mobile/email, a
        # number that is already someone's FAMILY MEMBER (owner ruling 2026-09-04),
        # and a race-lost insert -- each as a 409, never a 500. This router used
        # to carry its own copy of that logic; a second copy is how every drift
        # bug in this repo started.

        # Prepare customer data
        customer_data = {
            # Minted here (the repo keeps a supplied id) so this dict IS the
            # created record after the insert, whatever repo double is bound.
            "customer_id": str(uuid.uuid4()),
            "customer_type": customer.customer_type,
            "name": customer.name,
            "mobile": customer.mobile,
            # Canonical alias for the phone concept (imported customers store it
            # under `phone`). Written alongside `mobile` so new customers are
            # discoverable by either key and we can later drop the read-side $or.
            "phone": customer.mobile,
            "email": customer.email,
            "dob": customer.dob.isoformat() if customer.dob else None,
            "anniversary": (
                customer.anniversary.isoformat() if customer.anniversary else None
            ),
            "gstin": customer.gstin,
            "billing_address": customer.billing_address,
            "marketing_consent": customer.marketing_consent,
            # DPDP consent — server-stamped so it's provable (who/when/what text).
            "data_consent": customer.data_consent,
            "data_consent_at": (
                utc_now_iso() if customer.data_consent else None
            ),
            "data_consent_text_version": customer.data_consent_text_version,
            "home_store_id": current_user.get("active_store_id"),
            # Canonical alias for the store reference (imported customers store
            # it under `preferred_store_id`); mirrors `home_store_id`.
            "preferred_store_id": current_user.get("active_store_id"),
            "loyalty_points": 0,
            "store_credit": 0,
            "total_purchases": 0,
            "is_active": True,
            "patients": [],
        }

        # Mirror the structured billing_address onto flat top-level fields so the
        # Customers detail + edit screen (which reads flat `address`/`city`/...)
        # shows a created customer's address instead of a blank.
        _sync_address_representations(customer_data)

        # Add the supplied family members (if any) -- minted by the ONE row
        # builder every member door uses (customer_service.make_patient_row).
        from ..services.customer_service import make_patient_row

        for p in customer.patients:
            customer_data["patients"].append(
                make_patient_row(
                    name=p.name,
                    mobile=p.mobile,
                    dob=p.dob,
                    anniversary=p.anniversary,
                    relation=p.relation,
                )
            )

        # BILL-TO-MEMBER P1 (council 2026-06-19): every account must carry >=1
        # member, and exactly one is the Primary (the account holder, a REAL
        # member row -- never the legacy patient_id==customer_id special case).
        # ensure_primary_member seeds a Primary from the account name/mobile when
        # no members were provided, and otherwise flags the Self/first member as
        # is_primary + sets the root primary_patient_id pointer. This is the
        # SAME helper order-create auto-resolve and the backfill migration use,
        # so the invariant is enforced identically on every write path.
        from api.services.member_billing import ensure_primary_member

        ensure_primary_member(customer_data)

        # Email validate-on-capture: flag, never block (dark = no stamp).
        stamp = await _email_validation_stamp(customer.email, "on_capture")
        if stamp:
            customer_data["email_validation"] = stamp

        from ..services.customer_service import CustomerConflict, ensure_customer

        try:
            created_id, _ = ensure_customer(
                None,
                mobile=customer.mobile,
                name=customer.name,
                store_id=current_user.get("active_store_id"),
                source="POS",
                doc=customer_data,
                strict=True,
                repo=repo,
            )
        except CustomerConflict as exc:
            raise HTTPException(status_code=409, detail=exc.detail)
        if not created_id:
            # ensure_customer returns None only when the repository is
            # unavailable; a conflict raised above and a race re-found the
            # winner. Fail loud, and do it HERE so `created` below is never
            # Optional -- the CI lint gate (pylint E1136) rightly refused the
            # subscripts on a value it could prove might be None.
            raise HTTPException(status_code=500, detail="Failed to create customer")
        created = customer_data
        if created:
            _audit_customer(
                "CUSTOMER_CREATED",
                created.get("customer_id"),
                current_user,
                after_state={
                    "name": created.get("name"),
                    "mobile": created.get("mobile"),
                    "customer_type": created.get("customer_type"),
                    "email": created.get("email"),
                },
            )
            return {
                "customer_id": created["customer_id"],
                "name": created["name"],
                "patients": created.get("patients", []),
            }

        raise HTTPException(status_code=500, detail="Failed to create customer")

    # Stub response
    return {"customer_id": str(uuid.uuid4()), "name": customer.name}


@router.post("/validate-emails")
async def validate_customer_emails(
    limit: int = Query(200, ge=1, le=1000),
    current_user: dict = Depends(require_roles("ADMIN")),
):
    """Batch email validation over the existing customer list (MSG91).

    Owner-triggered cleanup of the imported order-history emails: checks up
    to `limit` customers that have an email but no `email_validation` stamp
    yet, and stamps each with the verdict (flag only - nothing is deleted or
    blocked, whatever the verdict). Re-running continues where it left off
    (stamped docs are skipped), so the whole list can be cleaned in capped,
    cost-bounded passes.

    DARK UNTIL ARMED, SPENDING NOTHING: validations are billed per address,
    so this refuses honestly (ran=false + the reason) unless DISPATCH_MODE
    is armed AND MSG91 creds exist - the same single gate the on-capture
    check reads (agents.providers.email_validation_armed).
    """
    from agents.providers import email_validation_armed, validate_email

    armed, reason = email_validation_armed()
    if not armed:
        return {
            "ran": False,
            "reason": reason,
            "checked": 0,
            "remaining_unchecked": None,
        }

    repo = get_customer_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Customer storage unavailable")

    flt = {
        "email": {"$nin": [None, ""]},
        "email_validation": {"$exists": False},
    }
    counts = {"valid": 0, "invalid": 0, "risky": 0, "unknown": 0, "skipped": 0}
    checked = 0
    now = utc_now_iso()
    for doc in repo.collection.find(flt, {"customer_id": 1, "email": 1}).limit(limit):
        verdict = await validate_email(doc.get("email"))
        if not verdict:
            # Unparseable address or a provider hiccup - do not stamp a guess.
            counts["skipped"] += 1
            continue
        status = verdict.get("status") or "unknown"
        counts[status] = counts.get(status, 0) + 1
        repo.collection.update_one(
            {"customer_id": doc.get("customer_id")},
            {
                "$set": {
                    "email_validation": {
                        "status": status,
                        "raw": verdict.get("raw"),
                        "checked_at": now,
                        "source": "batch",
                    }
                }
            },
        )
        checked += 1
    try:
        remaining = repo.collection.count_documents(flt)
    except Exception:  # noqa: BLE001
        remaining = None
    _audit_customer(
        "CUSTOMER_EMAILS_VALIDATED",
        None,
        current_user,
        detail={"checked": checked, "counts": counts},
    )
    return {
        "ran": True,
        "reason": "armed",
        "checked": checked,
        "counts": counts,
        "remaining_unchecked": remaining,
    }


@router.get("/search")
async def search_customers(
    q: str = Query(..., min_length=3), current_user: dict = Depends(get_current_user)
):
    """Search customers by name, mobile, or email"""
    repo = get_customer_repository()

    if repo is not None:
        customers = _annotate_customer_matches(
            repo.search_customers(q, current_user.get("active_store_id")), q
        )
        return {"customers": customers}

    # No database available - return empty
    return {"customers": []}


@router.get("/search/phone")
async def search_customer_by_phone(
    phone: str = Query(...), current_user: dict = Depends(get_current_user)
):
    """Search customers by phone number — partial match.

    Returns ``{customers: [...]}`` so the same response shape works for
    every caller. Legacy callers that read the bare object (single
    customer) keep working via the `customer` key for the first hit.
    """
    repo = get_customer_repository()

    if repo is None:
        return {"customers": [], "customer": None}

    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if not digits:
        return {"customers": [], "customer": None}

    # Try exact first (fast path), then fall back to a digit-prefix regex
    # so partial typing ("9876") surfaces matches as the user types.
    # find_by_mobile ORs phone+mobile; the partial search must too, since
    # TechCherry-imported docs carry the number under `phone`.
    exact = repo.find_by_mobile(digits)
    if exact:
        _annotate_customer_matches([exact], digits)
        return {"customers": [exact], "customer": exact}

    # Also search nested family members (patients[].mobile) so a patient's own
    # number surfaces their parent account -- otherwise clinic/POS phone lookup
    # silently misses anyone billed under a relative's account. (Patient
    # sub-docs only ever store `mobile`; top-level `phone` is the TechCherry
    # import field.)
    matches = _annotate_customer_matches(
        repo.search(digits, ["mobile", "phone", "patients.mobile"]), digits
    )
    return {
        "customers": matches,
        "customer": matches[0] if matches else None,
    }


@router.get("/mobile/{mobile}")
async def get_customer_by_mobile(
    mobile: str = Path(..., description="Mobile number"),
    current_user: dict = Depends(get_current_user),
):
    """Get customer by mobile number"""
    repo = get_customer_repository()

    if repo is not None:
        customer = repo.find_by_mobile(mobile)
        # Object-level store scope: an out-of-scope match falls through to 404
        # so a store user can't enumerate another store's customers by phone.
        if customer and can_access_store_scoped(
            _customer_store_id(customer), current_user
        ):
            return customer

    raise HTTPException(status_code=404, detail="Customer not found")


@router.get("/consent/pending-purge")
async def list_pending_purge(
    days_overdue: int = Query(
        0,
        ge=0,
        description=(
            "Return only customers whose erasure was requested >= this many "
            "days ago. 0 = all pending. 30 = overdue by 30 days."
        ),
    ),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(require_roles("ADMIN")),
):
    """List customers with `pending_erasure=True` for the retention review job.

    DPDP does not mandate an automated purge (erasure timing depends on the
    legal basis per purpose); this endpoint gives ADMIN a concrete work-list
    so nothing falls through the cracks. The retention window per purpose is
    returned alongside each customer so the reviewer can decide whether the
    data may still be retained on a separate legal basis.
    """
    repo = get_customer_repository()
    if repo is None:
        return {
            "customers": [],
            "total": 0,
            "retention_windows_days": _PURPOSE_RETENTION_DAYS,
        }

    query: Dict[str, Any] = {"pending_erasure": True}
    if days_overdue > 0:
        from datetime import timedelta

        cutoff = (datetime.utcnow() - timedelta(days=days_overdue)).isoformat()
        query["erasure_requested_at"] = {"$lte": cutoff}

    try:
        rows = list(
            repo.collection.find(
                query,
                {
                    "_id": 0,
                    "customer_id": 1,
                    "name": 1,
                    "mobile": 1,
                    "home_store_id": 1,
                    "erasure_requested_at": 1,
                },
                limit=limit,
            )
        )
    except Exception:  # noqa: BLE001
        rows = []

    return {
        "customers": rows,
        "total": len(rows),
        "retention_windows_days": _PURPOSE_RETENTION_DAYS,
    }


@router.get("/{customer_id}")
async def get_customer(
    customer_id: str = Path(..., description="Customer ID"),
    current_user: dict = Depends(get_current_user),
):
    """Get customer by ID"""
    return _scoped_customer_or_404(customer_id, current_user)


@router.put("/{customer_id}")
async def update_customer(
    customer_id: str = Path(..., description="Customer ID"),
    customer: CustomerUpdate = Body(...),
    current_user: dict = Depends(get_current_user),
):
    """Update customer details.

    Supplied `patients` are APPENDED to the existing list (de-duped on
    name + mobile), not replaced. The clinical flow sends a single
    patient per visit; we don't want a re-edit to wipe siblings off the
    customer record.
    """
    repo = get_customer_repository()

    if repo is not None:
        # Object-level store scope (cross-store IDOR fix): out-of-scope -> 403.
        existing = _scoped_customer_or_404(customer_id, current_user, write=True)

        update_data = customer.model_dump(exclude_unset=True)
        # Defense-in-depth: `tags` are governed by PATCH /{id}/tags (STORE_MANAGER+
        # + PII strip + suggest->approve). Never let them be written through this
        # AUTHENTICATED generic update, even if a client smuggles the field in.
        update_data.pop("tags", None)

        # Serialize date fields to ISO strings — Mongo + downstream JSON
        # consumers prefer strings over datetime objects on this doc.
        for key in ("dob", "anniversary"):
            if key in update_data and update_data[key] is not None:
                v = update_data[key]
                update_data[key] = v.isoformat() if hasattr(v, "isoformat") else v

        # Keep phone/mobile in sync: TechCherry-imported docs read `phone`,
        # natively-created docs read `mobile`. Whichever the edit form sends,
        # mirror it onto the other so every reader sees the update.
        if update_data.get("phone") and not update_data.get("mobile"):
            update_data["mobile"] = update_data["phone"]
        elif update_data.get("mobile") and not update_data.get("phone"):
            update_data["phone"] = update_data["mobile"]

        # GATE: credit_limit (khata) is a monetary control. The audit found a
        # cashier could raise a customer's credit ceiling through this only-
        # AUTHENTICATED edit. Only ADMIN/SUPERADMIN may change this ceiling.
        if "credit_limit" in update_data:
            roles = current_user.get("roles", []) or []
            if not any(r in roles for r in ("ADMIN", "SUPERADMIN")):
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "Setting a customer credit limit (khata) requires a "
                        "Admin or Superadmin role."
                    ),
                )

        # Duplicate-mobile guard: a changed mobile must not collide with ANOTHER
        # customer (was unguarded -- a dup write surfaced as a 500). Same-customer
        # (idempotent re-save of the same number) is allowed.
        new_mobile_for_dup = update_data.get("mobile") or update_data.get("phone")
        if new_mobile_for_dup:
            other = repo.find_by_mobile(new_mobile_for_dup)
            other_id = (other or {}).get("customer_id") if other else None
            if other and other_id and other_id != customer_id:
                raise HTTPException(
                    status_code=409,
                    detail="Another customer already uses this mobile number",
                )
            # ...nor with a FAMILY MEMBER on another account (same rule, same
            # implementation as the create door -- an edit must not open the
            # side door the create door closed). Own account excluded: the
            # holder's Self row carries their own number.
            from ..services.customer_service import family_member_conflict

            family = family_member_conflict(
                repo, new_mobile_for_dup, exclude_customer_id=customer_id
            )
            if family:
                raise HTTPException(status_code=409, detail=family)

        # B2B-requires-GSTIN on the MERGED state: enforce presence using the
        # effective customer_type + gstin after this edit is applied, so you
        # cannot flip a customer to B2B (or save a B2B) without a GSTIN. Format
        # is already checked by the field validator; this enforces PRESENCE.
        eff_type = update_data.get("customer_type", existing.get("customer_type"))
        eff_gstin = update_data.get("gstin", existing.get("gstin"))
        if eff_type == "B2B" and not (eff_gstin and str(eff_gstin).strip()):
            raise HTTPException(
                status_code=422, detail="A B2B customer requires a valid GSTIN"
            )

        # Handle patients additively
        if "patients" in update_data:
            from ..services.customer_service import (
                household_conflict,
                make_patient_row,
                own_account_conflict,
            )

            incoming = update_data.pop("patients") or []
            current_patients = list(existing.get("patients") or [])
            seen_keys = {
                (
                    (p.get("name") or "").strip().lower(),
                    (p.get("mobile") or "").strip(),
                )
                for p in current_patients
            }
            # (submitted index, row) for the rows that will actually be appended.
            to_add = []
            for idx, p in enumerate(incoming):
                # `p` is a dict (PatientCreate dumped)
                name = (p.get("name") or "").strip()
                mobile = (p.get("mobile") or "").strip()
                if not name:
                    continue
                key = (name.lower(), mobile)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                to_add.append(
                    (
                        idx,
                        make_patient_row(
                            name=name,
                            mobile=mobile or None,
                            dob=p.get("dob"),
                            anniversary=p.get("anniversary"),
                            relation=p.get("relation"),
                        ),
                    )
                )
            # REVERSE family-member guard (owner ruling 2026-09-04): a row being
            # ADDED must not carry a number that is already someone's own account
            # -- same rule as the create door and POST .../patients; this account
            # exempt (the holder's Self row). Only rows actually being appended
            # are checked, so the clinical per-visit re-send of an existing
            # member never trips on a legacy split. All-or-nothing: one bad row
            # appends none. ONE HOUSEHOLD: nor a number already a member on a
            # DIFFERENT account (this account and its own number exempt).
            own = own_account_conflict(repo, to_add, exclude_customer_id=customer_id)
            if own:
                raise HTTPException(status_code=409, detail=own)
            house = household_conflict(
                repo,
                to_add,
                exclude_customer_id=customer_id,
                own_mobile=existing.get("mobile") or existing.get("phone"),
            )
            if house:
                raise HTTPException(status_code=409, detail=house)
            current_patients.extend(row for _, row in to_add)
            update_data["patients"] = current_patients

        # Keep the structured billing_address and the flat top-level address
        # fields in sync. When the edit form sends only a flat `address` string,
        # fold it into billing_address (preserving the existing city/state/
        # pincode) so the GST place-of-supply reader stays current; when it sends
        # a structured dict, mirror it back to the flat fields the screen reads.
        _sync_address_representations(update_data, existing)

        # Email validate-on-capture on a CHANGED address: flag, never block
        # (dark = no stamp; same one rule as the create door).
        new_email = update_data.get("email")
        if new_email and new_email != existing.get("email"):
            stamp = await _email_validation_stamp(new_email, "on_capture")
            if stamp:
                update_data["email_validation"] = stamp

        if not update_data:
            return {"message": "No changes", "customer_id": customer_id}

        # Detect a mobile-number change for a dedicated audit action. The owner
        # specifically wanted mobile-number edits visible in the Activity Log.
        old_mobile = existing.get("mobile") or existing.get("phone")
        new_mobile = update_data.get("mobile") or update_data.get("phone")
        mobile_changed = bool(new_mobile) and str(new_mobile) != str(old_mobile or "")

        if repo.update(customer_id, update_data):
            if mobile_changed:
                _audit_customer(
                    "MOBILE_NUMBER_CHANGED",
                    customer_id,
                    current_user,
                    before_state={"mobile": old_mobile},
                    after_state={"mobile": new_mobile},
                )
            else:
                _audit_customer(
                    "CUSTOMER_UPDATED",
                    customer_id,
                    current_user,
                    detail={"fields": sorted(update_data.keys())},
                )
            return {"message": "Customer updated", "customer_id": customer_id}

        raise HTTPException(status_code=500, detail="Failed to update customer")

    return {"message": "Customer updated"}


@router.post("/{customer_id}/patients")
async def add_patient(
    customer_id: str = Path(..., description="Customer ID"),
    patient: PatientCreate = Body(...),
    current_user: dict = Depends(get_current_user),
):
    """Add a patient to customer"""
    repo = get_customer_repository()

    if repo is not None:
        # Object-level store scope (cross-store IDOR fix): out-of-scope -> 403.
        existing = _scoped_customer_or_404(customer_id, current_user, write=True)

        # Family-member dedup -- same (name, mobile) key the additive PUT uses.
        # Adding the same family member twice (e.g. a double-tap or a re-saved
        # clinical visit) returns the EXISTING patient instead of duplicating
        # the row, so the customer's patient list can't accumulate clones.
        new_name = (patient.name or "").strip()
        new_mobile = (patient.mobile or "").strip()
        new_key = (new_name.lower(), new_mobile)
        for p in existing.get("patients") or []:
            key = ((p.get("name") or "").strip().lower(), (p.get("mobile") or "").strip())
            if new_name and key == new_key:
                return {
                    "patient_id": p.get("patient_id"),
                    "name": p.get("name"),
                    "deduped": True,
                }

        from ..services.customer_service import (
            household_conflict,
            make_patient_row,
            own_account_conflict,
        )

        patient_data = make_patient_row(
            name=patient.name,
            mobile=patient.mobile,
            dob=patient.dob,
            anniversary=patient.anniversary,
            relation=patient.relation,
        )

        # REVERSE family-member guard: the number must not already be someone's
        # own account (same rule as the create door's patients[] and the PUT
        # append; this account exempt -- the holder's Self row). ONE HOUSEHOLD:
        # nor already a family member on a DIFFERENT account.
        own = own_account_conflict(repo, [(0, patient_data)], exclude_customer_id=customer_id)
        if own:
            raise HTTPException(status_code=409, detail=own)
        house = household_conflict(
            repo,
            [(0, patient_data)],
            exclude_customer_id=customer_id,
            own_mobile=existing.get("mobile") or existing.get("phone"),
        )
        if house:
            raise HTTPException(status_code=409, detail=house)

        if repo.add_patient(customer_id, patient_data):
            _audit_customer(
                "CUSTOMER_PATIENT_ADDED",
                customer_id,
                current_user,
                after_state={
                    "patient_id": patient_data["patient_id"],
                    "name": patient_data.get("name"),
                    "relation": patient_data.get("relation"),
                },
            )
            return {"patient_id": patient_data["patient_id"], "name": patient.name}

        raise HTTPException(status_code=500, detail="Failed to add patient")

    return {"patient_id": str(uuid.uuid4())}


@router.post("/{customer_id}/patients/{patient_id}/promote", status_code=201)
async def promote_patient_to_own_account(
    customer_id: str = Path(..., description="Account the family member is on"),
    patient_id: str = Path(..., description="Family member (patients[].patient_id)"),
    current_user: dict = Depends(get_current_user),
):
    """Promote a family member to their OWN top-level customer account.

    The counterpart of the family-member guard on POST /customers: when a
    create is refused because the number already belongs to a family member,
    staff press Promote here instead of being stuck at the counter. Carries the
    member's prescriptions + eye tests (re-pointed at the new account; the
    patient_id stays the same) and removes the row from the parent account.
    Orders are NOT moved (a bill stays with the account that paid). Same gate as
    creating a customer; store-scoped like every customer write."""
    repo = get_customer_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Customer database unavailable")
    parent = _scoped_customer_or_404(customer_id, current_user, write=True)

    from ..dependencies import get_db
    from ..services.customer_service import CustomerConflict, promote_patient

    try:
        promoted = promote_patient(get_db(), repo, parent, patient_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except CustomerConflict as exc:
        raise HTTPException(status_code=409, detail=exc.detail)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "PROMOTE_INCOMPLETE",
                "customer_id": getattr(exc, "customer_id", None),
                "message": str(exc),
            },
        )

    new_id = promoted.get("customer_id")
    _audit_customer(
        "CUSTOMER_PROMOTED",
        new_id,
        current_user,
        detail={
            "from_customer_id": customer_id,
            "patient_id": patient_id,
            "carried": promoted.get("carried"),
        },
    )
    _audit_customer(
        "CUSTOMER_PATIENT_PROMOTED_OUT",
        customer_id,
        current_user,
        detail={"patient_id": patient_id, "to_customer_id": new_id},
    )
    return {
        "customer_id": new_id,
        "name": promoted.get("name"),
        "mobile": promoted.get("mobile"),
        "phone": promoted.get("phone"),
        "customer_type": promoted.get("customer_type"),
        "patients": promoted.get("patients", []),
        "primary_patient_id": promoted.get("primary_patient_id"),
        "promoted_from": promoted.get("promoted_from"),
        "carried": promoted.get("carried", {}),
    }


@router.get("/{customer_id}/orders")
async def get_customer_orders(
    customer_id: str = Path(..., description="Customer ID"),
    current_user: dict = Depends(get_current_user),
):
    """Get orders for a customer.

    Imported (TechCherry) orders carry only `customer_phone`, never a
    `customer_id`, while natively-created orders link by `customer_id`. We
    load the customer first, then match orders on customer_id OR the
    customer's phone (read from `phone`/`mobile`) so both data sources
    surface.
    """
    repo = get_customer_repository()
    if repo is None:
        return {"orders": []}

    # Object-level store scope (cross-store IDOR fix): out-of-scope -> 404.
    customer = _scoped_customer_or_404(customer_id, current_user)

    from ..dependencies import get_order_repository

    order_repo = get_order_repository()
    if order_repo is None:
        return {"orders": []}

    phone = customer.get("phone") or customer.get("mobile")
    or_clauses: List[dict] = [{"customer_id": customer_id}]
    if phone:
        or_clauses.append({"customer_phone": phone})

    orders = order_repo.find_many(
        {"$or": or_clauses}, sort=[("created_at", -1)], limit=500
    )
    # A line's cost_at_sale goes to the roles that see product cost only
    # (owner rulings D7 / 2026-10-12), as on GET /orders.
    return {"orders": mask_cost_list(orders, current_user, "product")}


def effective_credit_limit(customer_doc: Optional[dict], *, strict: bool = False) -> float:
    """The credit limit that applies to a customer (owner ruling 2026-10-08):
    their own limit when they have one, else the chain default from
    Settings > Operational Rules (Rs 1,50,000 unless changed there). Read by
    the till's CREDIT check (orders/payments.py) and the credit summary."""
    own = float((customer_doc or {}).get("credit_limit") or 0)
    if strict and (own < 0 or not own < float("inf")):
        raise HTTPException(status_code=503, detail="Customer credit limit could not be verified.")
    if own > 0:
        return own
    from .settings import chain_default_credit_limit

    return chain_default_credit_limit(strict=True) if strict else chain_default_credit_limit()


def _ar_outstanding(customer_id: str, customer_doc: Optional[dict]) -> float:
    """The same uncapped, fail-closed exposure used by credit approval."""
    from ..dependencies import get_order_repository
    from ..services.credit_limit import outstanding_credit

    repo = get_order_repository()
    if repo is None:
        raise HTTPException(503, "Outstanding credit could not be verified. Please retry.")
    return outstanding_credit(repo.collection, customer_id, customer_doc or {})


@router.get("/{customer_id}/credit-summary")
async def get_customer_credit_summary(
    customer_id: str = Path(..., description="Customer ID"),
    current_user: dict = Depends(get_current_user),
):
    """Return the credit-limit (khata) configuration and current AR outstanding
    for a customer.

    Response shape:
      {
        "customer_id": "...",
        "credit_limit": 50000.0,  // the limit in force (effective_credit_limit)
        "ar_outstanding": 12500.0,
        "ar_available": 37500.0,  // limit - outstanding
        "limit_exceeded": false
      }

    Unreadable balances refuse the preview rather than displaying available credit.
    """
    from ..services.credit_limit import is_walkin

    repo = get_customer_repository()
    if repo is None and not is_walkin(customer_id):
        raise HTTPException(503, "Customer credit limit could not be verified. Please retry.")
    # Object-level store scope (cross-store IDOR fix): out-of-scope -> 404.
    customer = (
        _scoped_customer_or_404(customer_id, current_user)
        if repo is not None and not is_walkin(customer_id) else None
    )

    credit_limit = effective_credit_limit(customer, strict=True)
    ar_outstanding = 0.0 if is_walkin(customer_id) else _ar_outstanding(customer_id, customer)
    ar_available = (
        None if credit_limit == 0 else round(credit_limit - ar_outstanding, 2)
    )
    limit_exceeded = bool(credit_limit > 0 and ar_outstanding > credit_limit)

    return {
        "customer_id": customer_id,
        "credit_limit": credit_limit,
        "ar_outstanding": ar_outstanding,
        "ar_available": ar_available,
        "limit_exceeded": limit_exceeded,
    }


@router.get("/{customer_id}/prescriptions")
async def get_customer_prescriptions(
    customer_id: str = Path(..., description="Customer ID"),
    current_user: dict = Depends(get_current_user),
):
    """Get prescriptions for a customer"""
    # This will be implemented when we connect PrescriptionRepository
    return {"prescriptions": []}


@router.post("/{customer_id}/store-credit/add")
async def add_store_credit(
    customer_id: str,
    amount: float = Query(..., gt=0),
    current_user: dict = Depends(require_roles(*_CREDIT_ROLES)),
):
    """Add store credit to customer.

    LEGACY DOOR, now routed through the ledger: it used to $inc the bare
    customer.store_credit with NO ledger row, so credit added here was
    invisible to the audit trail and silently broke the invariant
    issued - redeemed == balance. Same roles, same response keys -- it is now
    literally the /issue door (which also gives it the store-scope check)."""
    out = _post_credit_entry(
        customer_id,
        "ISSUED",
        StoreCreditEntryRequest(amount=amount, reason="manual add (legacy /add door)"),
        current_user,
    )
    return {
        "message": f"Added store credit: {amount}",
        "new_total": out["balance"],
    }


# ============================================================================
# STORE-CREDIT / CREDIT-NOTE LEDGER (auditable history per customer)
# ============================================================================


class StoreCreditEntryRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    amount: float
    reason: Optional[str] = ""
    ref: Optional[str] = None  # e.g. originating return_id / order_id


def _ledger_coll():
    from ..dependencies import get_db

    db = get_db()
    if db is None or not getattr(db, "is_connected", True):
        return None
    try:
        return db.get_collection("credit_note_ledger")
    except Exception:  # noqa: BLE001
        return None


def _current_credit_balance(customer_id: str, customer_doc: Optional[dict]) -> float:
    """Authoritative running balance = `customer.store_credit`.

    BOTH mutation paths keep that field current: an ISSUE/ADJUST syncs it to the
    entry's balance_after, and a REDEEM decrements it atomically
    (try_debit_store_credit). The ledger is the AUDIT TRAIL, not the source of
    truth -- `compute_balance` sums only the ledger DELTAS, which silently drops
    any pre-ledger legacy balance: a customer who had store_credit set before
    their first ledger entry would see it vanish from the displayed/issuable
    balance, while redeem still enforced against the full `store_credit` (a
    display-vs-redeem divergence on real customer money). So trust the synced
    field; fall back to the ledger delta-sum only when the customer doc isn't
    available (e.g. a minimal/mock collection in tests)."""
    from ..services import store_credit_ledger as scl

    if customer_doc is not None and customer_doc.get("store_credit") is not None:
        return float(customer_doc.get("store_credit") or 0)
    coll = _ledger_coll()
    if coll is not None:
        entries = list(coll.find({"customer_id": customer_id}, {"_id": 0}))
        if entries:
            return scl.compute_balance(entries)
    return float((customer_doc or {}).get("store_credit", 0) or 0)


def redeem_store_credit_atomic(
    customer_id: str,
    amount: float,
    *,
    reason: str = "",
    ref: Optional[str] = None,
    store_id: Optional[str] = None,
    user_id: Optional[str] = None,
    guard_ref: Optional[str] = None,
) -> dict:
    """THE one store-credit spend door: atomic guarded decrement + ledger row.

    ``guard_ref`` (the manual door's per-form key, money review 12): the
    debit lands at most once -- guarded by the ref in the customer's
    store_credit_refs, pushed in the same write -- and is the ledger row's
    entry_id. A re-press of the same form returns the row it wrote; the same
    key with a different amount is refused (409). A ledger row that cannot be
    written after a guarded debit is an unconfirmed answer (503), finished by
    the next press with the same key -- never swallowed.

    Extracted from _post_credit_entry's REDEEM branch so the POS tender
    (orders.add_payment, method=STORE_CREDIT) and the /store-credit/redeem
    route spend through the SAME implementation -- a second copy of this rule
    is this repo's dominant defect class. Raises HTTPException (400/404/503);
    on success returns {"entry": <ledger row>, "balance": <post-debit>}.

    AUTHORITY NOTE: no store-scope authz HERE, deliberately -- each caller owns
    it. The HTTP route scopes via _scoped_customer_or_404 (a customer id in a
    URL is not authority to spend that customer's money); orders.add_payment
    takes the customer from the ORDER document (never the request body) after
    validate_store_access on the order's store -- which also allows the
    legitimate cross-branch case where a customer redeems at a store that is
    not their home store.

    The bug this mechanism fixed: the old code recomputed balance_after from a
    STALE pre-read snapshot, so two concurrent redeems both read the same
    balance, both passed make_entry's Python check, and both wrote an absolute
    store_credit value -- the second clobbering the first and effectively
    spending the same credit twice. Now the spend is a single conditional
    decrement filtered on store_credit >= amount (atomic in Mongo, via
    repo.try_debit_store_credit -> money_guard.debit, the same engine behind
    vouchers.redeem_voucher_atomic); the returned balance is read from the
    POST-update document, never a snapshot.
    """
    from ..services import store_credit_ledger as scl

    repo = get_customer_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database not available")
    existing = repo.find_by_id(customer_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Customer not found")

    try:
        amt = round(float(amount), 2)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="amount must be a number")
    if amt <= 0:
        raise HTTPException(status_code=400, detail="amount must be greater than 0")

    coll = _ledger_coll()
    if guard_ref:
        replay = _redeem_replay(repo, coll, customer_id, amt, guard_ref, reason, ref, store_id, user_id)
        if replay is not None:
            return replay
        debited = repo.try_debit_store_credit(customer_id, amt, guard_ref=guard_ref)
        if debited is None:
            # A concurrent press with the same key may have won the guard.
            replay = _redeem_replay(repo, coll, customer_id, amt, guard_ref, reason, ref, store_id, user_id)
            if replay is not None:
                return replay
    else:
        debited = repo.try_debit_store_credit(customer_id, amt)
    if debited is None:
        # No document matched the store_credit >= amount guard -> insufficient
        # (or a concurrent redeem won the race for the last rupees).
        available = _current_credit_balance(customer_id, existing)
        raise HTTPException(
            status_code=400,
            detail=(
                f"insufficient store credit: requested {amt:.2f}, "
                f"available {available:.2f}"
            ),
        )
    if debited == getattr(repo, "DEBIT_NO_ATOMIC", "__no_atomic__"):
        # DOUBLE-SPEND GUARD (security): the collection cannot perform a
        # conditional (atomic) decrement. Taking the old snapshot
        # read-modify-write fallback here let two concurrent redeems both
        # pass and spend the same credit twice. A REDEEM (debit) is a
        # double-spend risk, so REJECT with 503 rather than fall back to the
        # non-atomic path. (ISSUE/ADJUST credits keep the snapshot path
        # in _post_credit_entry -- a credit cannot overspend.)
        raise HTTPException(
            status_code=503,
            detail=(
                "Store-credit redemption is temporarily unavailable: the "
                "atomic debit path is not available, and a non-atomic "
                "decrement could double-spend the balance. Please retry."
            ),
        )
    # Authoritative post-update balance from the decremented document.
    new_balance = float((debited or {}).get("store_credit", 0) or 0)
    # Build the ledger row whose balance_after matches the atomic result.
    entry = scl.make_entry(
        customer_id=customer_id,
        entry_type=scl.REDEEMED,
        amount=amt,
        current_balance=new_balance + amt,  # pre-debit balance
        reason=reason or "",
        ref=ref,
        store_id=store_id,
        user_id=user_id,
    )
    entry["balance_after"] = round(new_balance, 2)
    if guard_ref:
        entry["entry_id"] = guard_ref
        entry["source"] = "MANUAL_REDEEM"
    if coll is not None:
        try:
            coll.insert_one(dict(entry))
        except Exception as exc:  # noqa: BLE001
            logger.error("[CUSTOMERS] redeem %s debited but its ledger row was NOT written: %s",
                         entry.get("entry_id"), exc)
            if guard_ref:
                raise HTTPException(status_code=503, detail=REDEEM_UNCONFIRMED_MESSAGE) from exc
    entry.pop("_id", None)
    return {"entry": entry, "balance": entry["balance_after"]}


REDEEM_UNCONFIRMED_MESSAGE = (
    "The redemption could not be confirmed - it may already be taken from the balance. Do NOT "
    "start a new one: press Confirm again on this same form, or reload and check the balance."
)


def _redeem_replay(repo, coll, customer_id, amt, guard_ref, reason, ref, store_id, user_id) -> Optional[dict]:
    """The same form's redeem pressed again: its row (the same amount, else
    409), or -- the debit landed but the row was never written -- the row
    written now. None when this key never debited."""
    from ..services import store_credit_ledger as scl

    try:
        prior = coll.find_one({"entry_id": guard_ref}) if coll is not None else None
        landed = _guard_landed(repo, customer_id, guard_ref)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=REDEEM_UNCONFIRMED_MESSAGE) from exc
    balance = _current_credit_balance(customer_id, repo.find_by_id(customer_id))
    if prior is not None:
        sent = abs(float(prior.get("amount") or prior.get("delta") or 0))
        if abs(sent - amt) > 0.005:
            raise HTTPException(status_code=409, detail=(
                f"This form already took Rs {sent:.2f} - close it and check the balance."))
        prior = dict(prior)
        prior.pop("_id", None)
        return {"entry": prior, "balance": balance, "replayed": True}
    if landed is None:
        raise HTTPException(status_code=503, detail=REDEEM_UNCONFIRMED_MESSAGE)
    if not landed:
        return None
    entry = scl.make_entry(customer_id=customer_id, entry_type=scl.REDEEMED, amount=amt,
                           current_balance=balance + amt, reason=reason or "", ref=ref,
                           store_id=store_id, user_id=user_id)
    entry.update(entry_id=guard_ref, source="MANUAL_REDEEM", balance_after=round(balance, 2),
                 written_on_replay=True)
    try:
        coll.insert_one(dict(entry))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=REDEEM_UNCONFIRMED_MESSAGE) from exc
    entry.pop("_id", None)
    return {"entry": entry, "balance": balance, "replayed": True}


def store_credit_balance_unready(repo, customer_id: str, exclude_entry_id: Optional[str] = None) -> bool:
    """True when the customer's store_credit field is missing / null but the
    ledger already holds rows for them (``exclude_entry_id``: the row the
    caller itself is writing): a legacy balance scripts/
    backfill_store_credit_field.py has not set yet. Starting such a field at 0
    would silently drop that balance, so the doors refuse instead (deploy
    order: the backfill runs BEFORE PR #1264). An unreadable ledger reads as
    unready too -- never a guess."""
    coll = getattr(repo, "collection", None)
    if coll is None:
        return False
    doc = coll.find_one({"customer_id": customer_id}) or {}
    if doc.get("store_credit") is not None:
        return False
    ledger = _ledger_coll()
    if ledger is None:
        return True
    # Only rows that MOVED money count: a GST-only document (zero / no delta,
    # a superadmin note doc) or a row not yet applied never made a balance.
    flt: Dict[str, Any] = {"customer_id": customer_id, "delta": {"$nin": [0, 0.0, None]},
                           "applied": {"$ne": False}}
    if exclude_entry_id:
        flt["entry_id"] = {"$ne": exclude_entry_id}
    return ledger.find_one(flt) is not None


# --------------------------------------------------------------------------- #
# THE posting of a balance-moving ledger row (PR #1264, design in the PR body).
# Production Mongo is a standalone: no multi-document transaction, so the row
# and the balance move are two writes made safe as a resumable state machine:
#
#   ISSUING (applied False) --balance moved--> final type (ISSUED / ADJUSTED),
#                           |                  applied True
#                           +--never moved---> VOID (its credit-note number is
#                                              written off)
#
# The move is ONE atomic $inc guarded by the row's entry_id in the customer's
# store_credit_refs (pushed in the same write), so it applies at most once and
# "did it land?" is always answerable by reading the guard -- an unclear write
# (landed, then raised) is resolved by that read, never by void-and-retry.
# reconcile_pending_credits finishes or voids every row left ISSUING past
# PENDING_TIMEOUT_SECONDS, consistently with the guard; it runs on the
# scheduler and before any posting for the same customer.
# --------------------------------------------------------------------------- #
STORE_CREDIT_REFS = "store_credit_refs"
PENDING = "ISSUING"
VOID = "VOID"
PENDING_TIMEOUT_SECONDS = 600
POSTED, REFUSED, UNCLEAR = "POSTED", "REFUSED", "UNCLEAR"
# Flags post_ledger_credit sets on the caller's entry (popped by the caller):
# REFUSED before any row was written (the caller writes a taken number off
# without reading the ledger again); the row was not written by this press.
NOT_WRITTEN, RESUMED = "_not_written", "_resumed"


def _guard_landed(repo, customer_id: str, entry_id: str) -> Optional[bool]:
    """Did this row's balance move land? Read from the guard. None when it
    cannot be read (the row stays pending for the reconciler)."""
    coll = getattr(repo, "collection", None)
    if coll is None:
        return False
    try:
        return coll.find_one({"customer_id": customer_id, STORE_CREDIT_REFS: entry_id}) is not None
    except Exception as exc:  # noqa: BLE001
        logger.error("[CUSTOMERS] credit guard %s unreadable: %s", entry_id, exc)
        return None


def _month_of(value: Any) -> str:
    from ..utils.ist import ist_date_str_from_stored

    return (ist_date_str_from_stored(value) or "")[:7] if value else ""


def _finish_dates(row: Dict[str, Any]) -> Dict[str, Any]:
    """THE redate rule for every finish (press, reconciler, heal): a credit
    note finished in a later month than it was written, or into a month that
    is locked, is dated the day it was finished -- never filed back into a
    month already reported. {} when its own date stands."""
    now = utc_now_iso()
    month = _month_of(row.get("created_at"))
    locked = False
    if month:
        try:
            from .finance import is_period_locked
            from ..dependencies import get_db

            conn = get_db()
            raw = getattr(conn, "db", conn)
            locked = bool(raw is not None and is_period_locked(raw, int(month[5:7]), int(month[:4])))
        except Exception:  # noqa: BLE001 -- unreadable lock: the finish date is the safe one
            locked = True
    if month and (month != _month_of(now) or locked):
        logger.warning("[CUSTOMERS] credit row %s finished after its month: dated %s (for the CA)",
                       row.get("entry_id"), now)
        out = {"created_at": now, "redated_from": row.get("created_at")}

        def _fy(m: str) -> int:
            return int(m[:4]) if int(m[5:7]) >= 4 else int(m[:4]) - 1

        if _fy(month) != _fy(_month_of(now)):
            out["redated_cross_fy"] = True
            _audit_credit_event("CREDIT_NOTE_REDATED_CROSS_FY", row)
        return out
    return {}


def _finish_credit_row(ledger, row: Dict[str, Any], *, redate: bool = False) -> bool:
    """ISSUING -> final type, applied: matches only an ISSUING row, never a
    VOID one (that is a deliberate heal in _void_credit_row only). Dated by
    THE redate rule (_finish_dates). ``redate``: the reconciler's finish -- a
    counter return's credit settled without its return record then puts a
    P1 task in front of the shop and Accounts. True when the finish matched."""
    # No final type on the row: money given (ADJUSTED), never a GST credit
    # note filed by default.
    sets: Dict[str, Any] = {"type": row.get("final_type") or "ADJUSTED", "applied": True, **_finish_dates(row)}
    matched = False
    for _ in range(2):  # left ISSUING if both fail: the reconciler finishes it
        try:
            res = ledger.update_one({"entry_id": row["entry_id"], "type": PENDING}, {"$set": sets})
            matched = bool(getattr(res, "matched_count", getattr(res, "modified_count", 1)))
            break
        except Exception as exc:  # noqa: BLE001
            logger.error("[CUSTOMERS] credit row %s not finished: %s", row.get("entry_id"), exc)
    if matched and row.get("undo_on_void") and redate:
        # The reconciler finished a counter return's credit whose return was
        # never recorded (its press could not confirm the credit): the credit
        # stands; a human must complete the return record.
        _audit_credit_event("RETURN_CREDIT_SETTLED_WITHOUT_RECORD", row)
        _task_credit_settled_without_record(row)
        _keep_return_hold(row)
    return matched


def _keep_return_hold(row: Dict[str, Any]) -> None:
    """A counter return's credit landed: its refund hold is the record of
    that refund until the return doc exists -- kept (re-created when it aged
    out), so the cap and the Rs 2,000 tier never forget a paid credit."""
    spec = row.get("undo_on_void") or {}
    try:
        from .returns import _keep_order_refund_hold

        _keep_order_refund_hold(spec.get("order_id"), spec.get("return_id"), amount=spec.get("net_amount"))
    except Exception as exc:  # noqa: BLE001
        logger.error("[CUSTOMERS] refund hold for %s not re-kept: %s", row.get("entry_id"), exc)


def _task_credit_settled_without_record(row: Dict[str, Any]) -> None:
    """The P1 task a landed-but-unrecorded counter-return credit raises (to
    the shop and Accounts), deduped per credit line. Fail-soft."""
    spec = row.get("undo_on_void") or {}
    try:
        from ..dependencies import get_task_repository
        from ..services.task_triggers import create_system_task

        lines = ", ".join(f"{l.get('item_id') or l.get('product_id')} x{l.get('qty')}" for l in spec.get("lines") or [])
        tenders = ", ".join(f"{t.get('method')} Rs {float(t.get('amount') or 0):.2f}"
                            for t in spec.get("refund_tenders") or [])
        cash = sum(float(t.get("amount") or 0) for t in spec.get("refund_tenders") or [] if t.get("method") == "CASH")
        create_system_task(
            get_task_repository(),
            title=f"Return {spec.get('return_id')} (order {spec.get('order_id')}): store credit given, return NOT recorded",
            description=(
                f"The store credit of Rs {float(row.get('delta') or 0):.2f} for return {spec.get('return_id')} on "
                f"order {spec.get('order_id')} landed on the customer's balance after the counter could not "
                f"confirm it, but the return itself was never recorded. Credit note {row.get('note_number') or '-'}. "
                f"Lines: {lines or '-'}. Refund tenders: {tenders or '-'} (cash leg Rs {cash:.2f}). "
                "Do this: complete the return record and put the goods back in stock; check whether the cash "
                "leg was paid. Never redo the return - the credit already stands. Finish it with "
                f"Returns > Complete record ({spec.get('return_id')})."
            ),
            priority="P1",
            category="Accounts",
            store_id=row.get("store_id"),
            dedupe_ref=f"return_credit_settled:{row.get('entry_id')}",
            extra={"payload": {"order_id": spec.get("order_id"), "return_id": spec.get("return_id"),
                               "entry_id": row.get("entry_id"),
                               "action": f"/api/v1/returns/{spec.get('return_id')}/complete-record"}},
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("[CUSTOMERS] settled-without-record task not raised for %s: %s", row.get("entry_id"), exc)


def _audit_credit_event(action: str, row: Dict[str, Any]) -> None:
    logger.error("[CUSTOMERS] %s: %s (order %s)", action, row.get("entry_id"), row.get("order_id"))
    try:
        from ..dependencies import get_audit_repository

        audit = get_audit_repository()
        if audit is not None:
            audit.create({"action": action, "entity_type": "store_credit", "entity_id": row.get("entry_id"),
                          "store_id": row.get("store_id"), "user_id": "system",
                          "details": {"order_id": row.get("order_id"), "ref": row.get("ref"),
                                      "note_number": row.get("note_number"), "delta": row.get("delta")},
                          "created_at": utc_now_iso()})
    except Exception as exc:  # noqa: BLE001
        logger.error("[CUSTOMERS] %s not audited: %s", action, exc)


def _heal_voided_row(ledger, row: Dict[str, Any], why: str, *, unrecorded: bool = False) -> None:
    """A balance move that landed on a line already VOID: the money moved, so
    the line is real -- made final again (dated by THE redate rule), and the
    write-off of its number is recorded as reinstated for the CA.
    ``unrecorded`` (the reconciler's heal): a counter return's credit that
    landed without its return record raises the P1 task and keeps the hold."""
    try:
        ledger.update_one({"entry_id": row["entry_id"], "type": VOID},
                          {"$set": {"type": row.get("final_type") or "ADJUSTED", "applied": True,
                                    "delta": row.get("delta"), "healed_from_void": True, **_finish_dates(row)}})
    except Exception as exc:  # noqa: BLE001
        logger.error("[CUSTOMERS] credit row %s not healed: %s", row.get("entry_id"), exc)
        return
    logger.warning("[CUSTOMERS] credit row %s healed: %s", row.get("entry_id"), why)
    if row.get("note_number"):
        _audit_credit_event("CREDIT_NOTE_NUMBER_REINSTATED", row)
    if unrecorded and row.get("undo_on_void"):
        _audit_credit_event("RETURN_CREDIT_SETTLED_WITHOUT_RECORD", row)
        _task_credit_settled_without_record(row)
        _keep_return_hold(row)


def _void_credit_row(repo, ledger, row: Dict[str, Any], why: str, *, undo: bool = False) -> bool:
    """VOID a row whose balance never moved -- compare-and-set on exactly the
    pending row that was read, so a second pass (or a press finishing it)
    can never void it twice. Its delta is zeroed (kept in void_delta: an
    older build summing every delta never counts it). Then the guard is read
    once more: a move that landed after all heals the row; an unreadable
    guard puts it back to ISSUING (same pending_at) for the next pass. Only a
    void that stands writes the number off -- and, with ``undo`` (the
    reconciler: the claims' one owner), gives a counter return's claims
    back. True when the row is now VOID."""
    eid = row["entry_id"]
    try:
        res = ledger.update_one(
            {"entry_id": eid, "type": PENDING, "applied": False, "pending_at": row.get("pending_at")},
            {"$set": {"type": VOID, "void_reason": why, "delta": 0.0, "void_delta": row.get("delta")}})
    except Exception as exc:  # noqa: BLE001 -- applied False: still never counted
        logger.error("[CUSTOMERS] credit row %s not voided: %s", eid, exc)
        return False
    if int(getattr(res, "modified_count", getattr(res, "matched_count", 0)) or 0) != 1:
        return False  # another pass or the press settled it first
    landed = _guard_landed(repo, row.get("customer_id"), eid)
    if landed is None:
        try:
            ledger.update_one({"entry_id": eid, "type": VOID},
                              {"$set": {"type": PENDING, "pending_at": row.get("pending_at"),
                                        "delta": row.get("delta")},
                               "$unset": {"void_reason": "", "void_delta": ""}})
        except Exception as exc:  # noqa: BLE001
            logger.error("[CUSTOMERS] credit row %s left VOID with an unreadable guard: %s", eid, exc)
        return False
    if landed:
        _heal_voided_row(ledger, row, "its balance move landed after all", unrecorded=undo)
        return False
    if row.get("note_number"):
        from .returns import _record_unused_credit_note

        _record_unused_credit_note(row["note_number"], {"order_id": row.get("order_id"), "store_id": row.get("store_id")},
                                   row.get("ref") or eid, {"user_id": "system"}, why)
    if undo and row.get("undo_on_void"):
        from .returns import _undo_return_claims

        _undo_return_claims(row["undo_on_void"])
    return True


def _settle_credit_row(repo, ledger, row: Dict[str, Any]) -> str:
    cid, eid = row.get("customer_id"), row["entry_id"]
    try:
        moved: Optional[bool] = apply_store_credit_delta(
            repo, cid, float(row.get("delta") or 0), guard_ref=eid,
            balance_after=row.get("balance_after"), entry_id=eid)
    except Exception as exc:  # noqa: BLE001 -- unclear: only a landed guard decides here
        logger.error("[CUSTOMERS] balance move for %s raised: %s", eid, exc)
        # A raised write with no guard yet may still be in flight: never
        # void now -- the reconciler reads the guard once it has resolved.
        moved = True if _guard_landed(repo, cid, eid) else None
    if moved is None:
        return UNCLEAR
    if moved:
        if not _finish_credit_row(ledger, row):
            # The finish matched nothing: a reconciler voided the line while
            # this press's move was landing. The money moved, so the line is
            # made real again -- reported truthfully, never a silent POSTED.
            try:
                now_row = ledger.find_one({"entry_id": eid}) or {}
            except Exception:  # noqa: BLE001
                return UNCLEAR
            if now_row.get("type") == VOID:
                _heal_voided_row(ledger, {**row, **{k: v for k, v in now_row.items() if k == "pending_at"}},
                                 "the press's move landed after a reconciler voided it")
                if row.get("undo_on_void"):
                    # The reconciler gave this return's claims and hold back
                    # when it voided the line; the money moved, so take them
                    # again (the press records the return).
                    from .returns import _reassert_return_claims

                    _reassert_return_claims(row)
            elif not now_row.get("applied") and now_row.get("type") not in (PENDING, None):
                # A legacy unapplied line (written before ISSUING existed,
                # e.g. ADJUSTED applied False): the move landed, mark it.
                try:
                    ledger.update_one({"entry_id": eid, "type": now_row.get("type"), "applied": False},
                                      {"$set": {"applied": True}})
                except Exception:  # noqa: BLE001 -- the guard holds the move: the balance is right
                    logger.error("[CUSTOMERS] legacy credit row %s not marked applied", eid)
            elif not now_row.get("applied"):
                return UNCLEAR
        return POSTED
    if _void_credit_row(repo, ledger, row, "balance not moved"):
        return REFUSED
    # The VOID did not stand (another pass settled the line, the guard could
    # not be read, or the move landed after all): never a refusal -- its
    # caller would give back claims the line's own owner gives back again.
    try:
        now_row = ledger.find_one({"entry_id": eid}) or {}
    except Exception:  # noqa: BLE001
        return UNCLEAR
    if now_row.get("applied") and now_row.get("type") not in (PENDING, VOID):
        return POSTED
    return UNCLEAR


def post_ledger_credit(repo, entry: Dict[str, Any], *, ledger=None) -> str:
    """THE posting door for every balance-moving ledger row (returns, the
    manual credit door, the advance refund, the superadmin credit note, the
    Shopify refund re-drive, referral rewards). Resumable and idempotent on
    entry_id: pressing it again for the same row finishes it, never posts
    twice. Returns POSTED (the balance carries it), REFUSED (it never moved;
    the row is VOID) or UNCLEAR (it may have moved: the row stays ISSUING and
    the reconciler settles it -- callers must never report UNCLEAR as
    'nothing recorded')."""
    ledger = ledger if ledger is not None else _ledger_coll()
    if ledger is None:
        entry[NOT_WRITTEN] = True
        return REFUSED
    eid, cid = entry["entry_id"], entry.get("customer_id")
    reconcile_pending_credits(repo, customer_id=cid, ledger=ledger)
    try:
        row = ledger.find_one({"entry_id": eid})
    except Exception as exc:  # noqa: BLE001 -- nothing written yet: a clean refusal
        logger.error("[CUSTOMERS] credit row %s unreadable before posting: %s", eid, exc)
        entry[NOT_WRITTEN] = True
        return REFUSED
    if row is not None:
        entry[RESUMED] = True  # a row this press did not write: its caller reports it as stored
    else:
        row = dict(entry, type=PENDING, final_type=entry.get("type"), applied=False, pending_at=utc_now_iso())
        row.pop(NOT_WRITTEN, None)
        row.pop(RESUMED, None)
        try:
            ledger.insert_one({**row, "_id": eid})
        except Exception as exc:  # noqa: BLE001 -- a concurrent press wrote it first, or the write failed
            mine = row.get("pending_at")
            try:
                row = ledger.find_one({"entry_id": eid})
            except Exception:  # noqa: BLE001 -- the insert may have landed: never a refusal
                logger.error("[CUSTOMERS] credit row %s: insert raised and cannot be read back: %s", eid, exc)
                return UNCLEAR
            if row is None:
                logger.error("[CUSTOMERS] credit row %s not written: %s", eid, exc)
                entry[NOT_WRITTEN] = True
                return REFUSED
            if row.get("pending_at") != mine:
                entry[RESUMED] = True
    row = dict(row)
    row.pop("_id", None)
    kind = row.get("type")
    # A final row (or a pre-#1264 row with no 'applied' key at all) carries
    # its balance already: only an explicit applied False is ever moved.
    if kind not in (PENDING, VOID) and row.get("applied") is not False:
        return POSTED
    if kind == VOID:
        if row.get("note_number"):
            return REFUSED  # its number was written off: a new note takes a new one
        delta = row.get("void_delta") if row.get("void_delta") is not None else row.get("delta")
        pending_at = utc_now_iso()
        try:
            res = ledger.update_one({"entry_id": eid, "type": VOID},
                                    {"$set": {"type": PENDING, "pending_at": pending_at, "delta": delta}})
        except Exception as exc:  # noqa: BLE001 -- nothing moved: a clean refusal
            logger.error("[CUSTOMERS] voided credit row %s not revived: %s", eid, exc)
            return REFUSED
        if not int(getattr(res, "matched_count", getattr(res, "modified_count", 1)) or 0):
            return REFUSED  # another press revived (or settled) it first
        row.update(type=PENDING, pending_at=pending_at, delta=delta)
    return _settle_credit_row(repo, ledger, row)


def reconcile_pending_credits(repo=None, *, customer_id: Optional[str] = None, ledger=None,
                              older_than_s: int = PENDING_TIMEOUT_SECONDS) -> Dict[str, int]:
    """Finish (guard landed) or void (it never did) every row left ISSUING
    longer than any press runs. Never touches a younger row: that press may
    still be writing. Fail-soft: an unreadable guard leaves the row for the
    next pass. ponytail: an index on {type, pending_at} if the ledger grows."""
    from datetime import timedelta, timezone as _tz

    out = {"finished": 0, "voided": 0}
    ledger = ledger if ledger is not None else _ledger_coll()
    repo = repo if repo is not None else get_customer_repository()
    if ledger is None or repo is None:
        return out
    cutoff = (datetime.now(_tz.utc) - timedelta(seconds=older_than_s)).isoformat()
    flt: Dict[str, Any] = {"type": PENDING, "pending_at": {"$lt": cutoff}}
    if customer_id:
        flt["customer_id"] = customer_id
    try:
        stale = list(ledger.find(flt))
    except Exception as exc:  # noqa: BLE001
        logger.error("[CUSTOMERS] pending credits unreadable: %s", exc)
        return out
    for row in stale:
        landed = _guard_landed(repo, row.get("customer_id"), row.get("entry_id"))
        if landed is None:
            continue
        if landed:
            _finish_credit_row(ledger, row, redate=True)
            out["finished"] += 1
        elif _void_credit_row(repo, ledger, row, "never applied (stale)", undo=True):
            out["voided"] += 1
    if out["finished"] or out["voided"]:
        logger.warning("[CUSTOMERS] pending credits settled: %s", out)
    return out


async def scheduled_credit_reconcile() -> None:
    """Scheduler tick (agents/scheduler): blocking pymongo off the event loop."""
    import asyncio

    await asyncio.to_thread(reconcile_pending_credits)


def apply_store_credit_delta(
    repo, customer_id: str, delta: float, *, guard_ref: Optional[str] = None,
    balance_after: Optional[float] = None, entry_id: Optional[str] = None,
) -> bool:
    """THE way customer.store_credit moves: ONE atomic $inc of the delta --
    never a read-then-$set, which overwrote a concurrent door's $inc (500 + a
    manual 200 racing an advance refund's 1000 read 700, not 1700).

    A missing or null store_credit starts from 0 (Mongo refuses $inc on null)
    and is logged -- but ONLY when the ledger holds nothing else for the
    customer: a legacy balance living only in the ledger is the backfill
    script's to set (store_credit_balance_unready -> logged, False), never
    seeded here (which double counted) nor dropped by a silent 0. ``guard_ref`` (the
    ledger row's entry_id, post_ledger_credit) makes the move apply once: the
    $inc lands only while the ref is not yet in the customer's
    store_credit_refs, and pushes it in the same write.

    True when the balance moved (or, with guard_ref, had already moved). A
    repository with no raw collection (test doubles only) gets balance_after."""
    coll = getattr(repo, "collection", None)
    if coll is None:
        repo.update(customer_id, {"store_credit": balance_after})
        return True
    if store_credit_balance_unready(repo, customer_id, exclude_entry_id=entry_id or guard_ref):
        logger.error("[CUSTOMERS] %s has ledger history but no store_credit field: run "
                     "scripts/backfill_store_credit_field.py; balance NOT moved", customer_id)
        return False
    seeded = coll.update_one({"customer_id": customer_id, "store_credit": None},
                             {"$set": {"store_credit": 0.0}})
    if getattr(seeded, "matched_count", getattr(seeded, "modified_count", 0)):
        logger.warning("[CUSTOMERS] %s had no store_credit field; starting it at 0 "
                       "(run scripts/backfill_store_credit_field.py for legacy balances)", customer_id)
    flt: Dict[str, Any] = {"customer_id": customer_id}
    upd: Dict[str, Any] = {"$inc": {"store_credit": float(delta)}}
    if guard_ref:
        flt[STORE_CREDIT_REFS] = {"$ne": guard_ref}
        upd["$push"] = {STORE_CREDIT_REFS: guard_ref}
    res = coll.update_one(flt, upd)
    if getattr(res, "matched_count", getattr(res, "modified_count", 0)):
        return True
    if guard_ref:
        return coll.find_one({"customer_id": customer_id, STORE_CREDIT_REFS: guard_ref}) is not None
    return False


def _post_credit_entry(
    customer_id: str, entry_type: str, body: StoreCreditEntryRequest, current_user: dict,
    idempotency_key: Optional[str] = None,
):
    from ..services import store_credit_ledger as scl

    repo = get_customer_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database not available")
    # Object-level store scope, WRITE flavour (cross-store IDOR): the ledger
    # GET already scoped, but issue/redeem -- the doors that MOVE the money --
    # trusted the raw id. Out-of-scope -> 403 (write) / 404 (read), same rule.
    existing = _scoped_customer_or_404(customer_id, current_user, write=True)

    et = (entry_type or "").upper()

    # ------------------------------------------------------------------
    # REDEEM (debit) -> the ONE atomic spend door above.
    # ------------------------------------------------------------------
    key = (idempotency_key or "").strip() if isinstance(idempotency_key, str) else ""
    if et == scl.REDEEMED:
        return redeem_store_credit_atomic(
            customer_id,
            body.amount,
            reason=body.reason or "",
            ref=body.ref,
            store_id=current_user.get("active_store_id"),
            user_id=current_user.get("user_id"),
            guard_ref=f"MANUAL-REDEEM:{customer_id}:{key[:80]}" if key else None,
        )

    # ------------------------------------------------------------------
    # ISSUE / ADJUST (credit). A credit cannot overspend, so the snapshot
    # path is safe for it; a REDEEM never reaches here (returned above).
    # ------------------------------------------------------------------
    if store_credit_balance_unready(repo, customer_id):
        raise HTTPException(
            status_code=503,
            detail="This customer's store-credit balance has not been set up yet - nothing was recorded. Ask an admin.",
        )
    balance = _current_credit_balance(customer_id, existing)
    if et == scl.ISSUED and not (body.amount > 0):
        raise HTTPException(status_code=400, detail="amount must be greater than 0")
    try:
        entry = scl.make_entry(
            customer_id=customer_id,
            # A goodwill / manual credit is money given, not a supply
            # reversed: ADJUSTED, which the GST returns never file (only a
            # return or a superadmin credit note issues a credit note).
            entry_type=scl.ADJUSTED if et == scl.ISSUED else entry_type,
            amount=body.amount,
            current_balance=balance,
            reason=body.reason or "",
            ref=body.ref,
            store_id=current_user.get("active_store_id"),
            user_id=current_user.get("user_id"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    entry["source"] = "MANUAL_CREDIT"
    ledger = _ledger_coll()
    if key:
        # One line per form: a re-press, a double click or a replay of the same
        # form resumes THIS line (THE posting door's guard), never pays twice --
        # and the key is bound to what the form sent: another amount or reason
        # under it is refused, never paid as the old line.
        entry["entry_id"] = f"MANUAL:{customer_id}:{key[:80]}"
        try:
            prior = ledger.find_one({"entry_id": entry["entry_id"]}) if ledger is not None else None
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=(
                "Could not check this form's earlier press - nothing was given. Try again.")) from exc
        if prior is not None:
            sent = abs(float(prior.get("amount") or 0))
            if abs(sent - round(float(body.amount), 2)) > 0.005 or (prior.get("reason") or "") != (body.reason or ""):
                raise HTTPException(status_code=409, detail=(
                    f"This form already sent Rs {sent:.2f} - close it and check the balance."))

    status = post_ledger_credit(repo, entry)
    entry.pop(NOT_WRITTEN, None)
    entry.pop(RESUMED, None)
    if status != POSTED:
        raise HTTPException(
            status_code=503,
            detail=("The store credit could not be recorded just now - nothing was given. Try again."
                    if status == REFUSED else
                    "The store credit could not be confirmed - it may already be credited. Do NOT press "
                    "again: it settles by itself within 10 minutes. Check the customer's balance then."),
        )
    # The answer is what is STORED (a resumed line keeps its own figures) and
    # the customer's real balance, never this press's snapshot.
    try:
        stored = (ledger.find_one({"entry_id": entry["entry_id"]}) if ledger is not None else None) or entry
        balance = _current_credit_balance(customer_id, repo.find_by_id(customer_id))
    except Exception:  # noqa: BLE001 -- it posted; the snapshot is the best answer left
        stored, balance = entry, entry["balance_after"]
    stored = dict(stored)
    stored.pop("_id", None)
    return {"entry": stored, "balance": balance}


@router.post("/{customer_id}/store-credit/issue")
async def issue_store_credit(
    customer_id: str,
    body: StoreCreditEntryRequest,
    current_user: dict = Depends(require_roles(*_CREDIT_ROLES)),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Issue store credit (e.g. a credit note from a return). Appends a ledger
    entry and updates the running balance. ``Idempotency-Key``: one per open
    form, so a replay resumes the same line."""
    return _post_credit_entry(customer_id, "ISSUED", body, current_user, idempotency_key)


@router.post("/{customer_id}/store-credit/redeem")
async def redeem_store_credit(
    customer_id: str,
    body: StoreCreditEntryRequest,
    current_user: dict = Depends(require_roles(*_CREDIT_ROLES)),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Redeem store credit. Rejected if it exceeds the current balance.
    ``Idempotency-Key``: one per open form -- a re-press never debits twice."""
    return _post_credit_entry(customer_id, "REDEEMED", body, current_user, idempotency_key)


@router.get("/{customer_id}/store-credit/ledger")
async def get_store_credit_ledger(
    customer_id: str, current_user: dict = Depends(get_current_user)
):
    """Full credit-note ledger for a customer + current balance, newest first."""
    repo = get_customer_repository()
    # Object-level store scope (cross-store IDOR fix): out-of-scope -> 404.
    existing = (
        _scoped_customer_or_404(customer_id, current_user) if repo is not None else None
    )
    coll = _ledger_coll()
    entries = []
    if coll is not None:
        entries = list(
            coll.find({"customer_id": customer_id}, {"_id": 0}).sort("created_at", -1)
        )
    balance = _current_credit_balance(customer_id, existing)
    return {"customer_id": customer_id, "balance": balance, "entries": entries}


# ============================================================================
# DPDP ACT 2023 — CONSENT LEDGER (per-customer, append-only)
# ============================================================================
# DPDP requires: (a) provable per-purpose consent capture, (b) a withdrawal
# path, (c) purpose-scoped retention / erasure.  The `data_consent` flag on
# the customer doc is the live "currently consenting?" gate; this ledger is the
# immutable audit trail that proves *when* each grant or withdrawal happened,
# *what* the customer agreed to, and *which* text version was shown.
#
# Purposes (DPDP §7 "specified purpose"):
#   SERVICE_DELIVERY   - core service: prescriptions, orders, store records
#   MARKETING          - promotional messages, birthday/Rx reminders
#   RX_HISTORY         - retaining prescription history for clinical use
#   ANALYTICS          - anonymised usage analytics and business reporting
#   AD_AUDIENCE        - sharing HASHED identifiers with Google/Meta for ad
#                        audience matching + suppression (third-party sharing).
#                        DISTINCT + more sensitive than MARKETING: it discloses
#                        (hashed) personal data to a processor outside IMS, so
#                        DPDP requires it be a SEPARATE, EXPLICIT opt-in -- it is
#                        deliberately NOT part of the default consent grant.
#
# Retention windows (mapped to purposes; after consent withdrawn the record
# should be erased/anonymised within this window, unless a legal basis overrides):
#   SERVICE_DELIVERY   - 3 years  (consumer protection + tax record obligation)
#   MARKETING          - 0 days   (immediate — no legal basis once withdrawn)
#   RX_HISTORY         - 5 years  (clinical best practice; 7 for minors)
#   ANALYTICS          - 30 days  (anonymise within 30 days of withdrawal)
#   AD_AUDIENCE        - 0 days   (stop sharing immediately; the audience-export
#                                  suppression list carries the delete request)
# ============================================================================

_ALL_PURPOSES = frozenset(
    {"SERVICE_DELIVERY", "MARKETING", "RX_HISTORY", "ANALYTICS", "AD_AUDIENCE"}
)

# The purposes captured by a default "grant all" at the counter. AD_AUDIENCE
# (third-party ad-platform sharing) is intentionally EXCLUDED: it must be an
# explicit, separate opt-in, never bundled into the standard consent tick.
_DEFAULT_GRANT_PURPOSES = frozenset(_ALL_PURPOSES - {"AD_AUDIENCE"})

# Versioned wording specific to the AD_AUDIENCE third-party-sharing opt-in. The
# grant/withdraw flow already accepts an arbitrary text_version; this is the
# canonical default shown when capturing ad-audience consent so the agreement is
# always tied to exact wording (roadmap: "versioned consent-text").
AD_AUDIENCE_CONSENT_VERSION = "ad_audience_v1"
AD_AUDIENCE_CONSENT_TEXT = (
    "I agree that Better Vision / WizOpt may share a secure, irreversibly "
    "hashed form of my phone number and/or email with Google and Meta so that "
    "I see more relevant ads and am not shown ads to re-acquire me as a new "
    "customer. My raw contact details are never shared. I can withdraw this at "
    "any time, after which sharing stops and a deletion request is sent."
)

_PURPOSE_RETENTION_DAYS: Dict[str, int] = {
    "SERVICE_DELIVERY": 1095,  # 3 years
    "MARKETING": 0,
    "RX_HISTORY": 1825,  # 5 years
    "ANALYTICS": 30,
    "AD_AUDIENCE": 0,
}


class ConsentGrantRequest(BaseModel):
    """Body for granting (or re-granting after an update) DPDP consent."""

    purposes: List[str] = Field(
        default_factory=lambda: sorted(_DEFAULT_GRANT_PURPOSES),
        description=(
            "Purposes being consented to. Defaults to the four standard purposes "
            "(SERVICE_DELIVERY, MARKETING, RX_HISTORY, ANALYTICS). AD_AUDIENCE "
            "(sharing hashed identifiers with Google/Meta) is a valid purpose but "
            "is NEVER a default -- pass it explicitly to record that separate opt-in."
        ),
    )
    text_version: Optional[str] = Field(
        None,
        description=(
            "Version string of the consent wording shown to the customer. "
            "Should match the `version` returned by GET /marketing/consent-text."
        ),
    )
    channel: str = Field(
        "COUNTER",
        description="Where consent was obtained: COUNTER | WHATSAPP | EMAIL | SMS | PORTAL",
    )

    @field_validator("purposes", mode="after")
    @classmethod
    def validate_purposes(cls, v):
        bad = set(v) - _ALL_PURPOSES
        if bad:
            raise ValueError(
                f"Unknown purposes: {sorted(bad)}. " f"Valid: {sorted(_ALL_PURPOSES)}"
            )
        if not v:
            raise ValueError("At least one purpose must be specified")
        return v

    @field_validator("channel", mode="after")
    @classmethod
    def validate_channel(cls, v):
        valid = {"COUNTER", "WHATSAPP", "EMAIL", "SMS", "PORTAL"}
        if v.upper() not in valid:
            raise ValueError(f"channel must be one of {sorted(valid)}")
        return v.upper()


class ConsentWithdrawRequest(BaseModel):
    """Body for withdrawing DPDP consent (all or specific purposes)."""

    purposes: Optional[List[str]] = Field(
        None,
        description=(
            "Purposes to withdraw. Omit or pass null to withdraw ALL purposes "
            "(full erasure request). To withdraw only marketing consent pass "
            '["MARKETING"].'
        ),
    )
    reason: Optional[str] = Field(None, max_length=500)

    @field_validator("purposes", mode="after")
    @classmethod
    def validate_purposes(cls, v):
        if v is not None:
            bad = set(v) - _ALL_PURPOSES
            if bad:
                raise ValueError(f"Unknown purposes: {sorted(bad)}")
            if not v:
                raise ValueError(
                    "Provide at least one purpose, or omit to withdraw all"
                )
        return v


def _consent_ledger_coll():
    """Return the `dpdp_consent_ledger` Mongo collection, or None (fail-soft)."""
    from ..dependencies import get_db

    db = get_db()
    if db is None or not getattr(db, "is_connected", True):
        return None
    try:
        return db.get_collection("dpdp_consent_ledger")
    except Exception:  # noqa: BLE001
        return None


def _append_consent_event(
    customer_id: str,
    event_type: str,  # "GRANTED" | "WITHDRAWN" | "UPDATED"
    purposes: List[str],
    current_user: dict,
    *,
    text_version: Optional[str] = None,
    channel: Optional[str] = None,
    reason: Optional[str] = None,
) -> dict:
    """Append one row to the consent ledger (fail-soft: swallows DB errors)."""
    entry = {
        "ledger_id": str(uuid.uuid4()),
        "customer_id": customer_id,
        "event_type": event_type,
        "purposes": sorted(purposes),
        "text_version": text_version,
        "channel": channel,
        "reason": reason,
        "actor_id": current_user.get("user_id"),
        "actor_role": (current_user.get("roles") or [""])[0],
        "store_id": current_user.get("active_store_id"),
        "created_at": utc_now_iso(),
    }
    coll = _consent_ledger_coll()
    if coll is not None:
        try:
            coll.insert_one(dict(entry))
        except Exception:  # noqa: BLE001
            pass
    entry.pop("_id", None)
    return entry


def _active_purposes_from_ledger(customer_id: str) -> List[str]:
    """Derive the current set of consented purposes by replaying the ledger.

    Algorithm: walk events newest-first; for each purpose track the LATEST
    event. A purpose is active if its latest event is GRANTED (or UPDATED).
    """
    coll = _consent_ledger_coll()
    if coll is None:
        return []
    try:
        rows = list(
            coll.find({"customer_id": customer_id}, {"_id": 0}).sort("created_at", -1)
        )
    except Exception:  # noqa: BLE001
        return []

    seen: Dict[str, str] = {}  # purpose -> latest event_type
    for row in rows:
        et = row.get("event_type", "")
        for purpose in row.get("purposes") or []:
            if purpose not in seen:
                seen[purpose] = et

    return sorted(p for p, et in seen.items() if et != "WITHDRAWN")


@router.get("/{customer_id}/consent/ledger")
async def get_consent_ledger(
    customer_id: str = Path(..., description="Customer ID"),
    current_user: dict = Depends(get_current_user),
):
    """Full DPDP consent history for a customer — newest first.

    Returns every GRANTED, WITHDRAWN, and UPDATED event with the actor,
    purposes, and text version. Any authenticated user can view this for
    their store's customers; ADMIN can view all.
    """
    repo = get_customer_repository()
    if repo is not None:
        # Consent is a legal record about a person; the id alone is not authority.
        _scoped_customer_or_404(customer_id, current_user, write=True)

    coll = _consent_ledger_coll()
    entries = []
    if coll is not None:
        try:
            entries = list(
                coll.find({"customer_id": customer_id}, {"_id": 0}).sort(
                    "created_at", -1
                )
            )
        except Exception:  # noqa: BLE001
            pass

    active_purposes = _active_purposes_from_ledger(customer_id)
    return {
        "customer_id": customer_id,
        "active_purposes": active_purposes,
        "entries": entries,
    }


@router.post("/{customer_id}/consent")
async def grant_consent(
    customer_id: str = Path(..., description="Customer ID"),
    body: ConsentGrantRequest = Body(...),
    current_user: dict = Depends(get_current_user),
):
    """Record a DPDP consent grant (or re-grant after a policy update).

    - Appends a GRANTED row to the consent ledger.
    - Syncs `data_consent=True` and `data_consent_text_version` on the
      customer document so the live gate stays consistent.
    - Any authenticated operator can record consent (needed at point of sale).
    """
    repo = get_customer_repository()
    if repo is not None:
        # Consent is a legal record about a person; the id alone is not authority.
        _scoped_customer_or_404(customer_id, current_user, write=True)

    entry = _append_consent_event(
        customer_id,
        "GRANTED",
        body.purposes,
        current_user,
        text_version=body.text_version,
        channel=body.channel,
    )

    # Sync the live consent flag on the customer document.
    if repo is not None:
        try:
            repo.update(
                customer_id,
                {
                    "data_consent": True,
                    "data_consent_at": entry["created_at"],
                    "data_consent_text_version": body.text_version,
                },
            )
        except Exception:  # noqa: BLE001
            pass

    _audit_customer(
        "CONSENT_GRANTED",
        customer_id,
        current_user,
        detail={"purposes": body.purposes, "text_version": body.text_version},
    )
    return {
        "message": "Consent granted",
        "entry": entry,
        "active_purposes": _active_purposes_from_ledger(customer_id),
    }


@router.post("/{customer_id}/consent/withdraw")
async def withdraw_consent(
    customer_id: str = Path(..., description="Customer ID"),
    body: ConsentWithdrawRequest = Body(...),
    current_user: dict = Depends(get_current_user),
):
    """Withdraw DPDP consent — all or specific purposes.

    DPDP Act 2023 s.6(4): data principal may withdraw consent at any time.
    - Appends a WITHDRAWN row to the consent ledger.
    - When ALL purposes are withdrawn: sets `data_consent=False` on the
      customer document and flags `pending_erasure=True` + `erasure_requested_at`
      so the retention job knows this customer needs review.
    - Partial withdrawal (e.g. MARKETING only): only that purpose is marked
      withdrawn; `data_consent` stays True for the remaining consented purposes.
    """
    repo = get_customer_repository()
    customer = (
        _scoped_customer_or_404(customer_id, current_user) if repo is not None else None
    )
    if repo is not None and customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")

    purposes_to_withdraw = list(body.purposes) if body.purposes else list(_ALL_PURPOSES)

    entry = _append_consent_event(
        customer_id,
        "WITHDRAWN",
        purposes_to_withdraw,
        current_user,
        reason=body.reason,
    )

    # Determine remaining active purposes after this withdrawal.
    active_after = _active_purposes_from_ledger(customer_id)
    all_withdrawn = len(active_after) == 0

    # Sync customer doc flags.
    if repo is not None:
        update_fields: Dict[str, Any] = {}
        if all_withdrawn:
            update_fields["data_consent"] = False
            update_fields["pending_erasure"] = True
            update_fields["erasure_requested_at"] = entry["created_at"]
        # When marketing is withdrawn, also flip marketing_consent for
        # consistency with the MEGAPHONE gate.
        if "MARKETING" in purposes_to_withdraw:
            update_fields["marketing_consent"] = False
        if update_fields:
            try:
                repo.update(customer_id, update_fields)
            except Exception:  # noqa: BLE001
                pass

    _audit_customer(
        "CONSENT_WITHDRAWN",
        customer_id,
        current_user,
        detail={
            "purposes": purposes_to_withdraw,
            "all_withdrawn": all_withdrawn,
            "reason": body.reason,
        },
    )
    response = {
        "message": "Consent withdrawn",
        "entry": entry,
        "active_purposes": active_after,
        "all_withdrawn": all_withdrawn,
    }
    if all_withdrawn:
        response["note"] = (
            "All consent withdrawn. Customer flagged `pending_erasure=True`. "
            "Review data-retention obligations before erasing records."
        )
    return response


# ============================================================================
# F39 - Customer tags (manager-approved; feed the NBA daily call list)
# Staff SUGGEST tags; STORE_MANAGER+ APPROVES (DECISIONS s3). Approved tags land
# on customers.tags[] and feed next-day NBA scoring (VIP slot + sub-headlines).
# Tags must not carry PII (phone / email / GSTIN) -- stripped on write.
# ============================================================================

_TAG_MANAGER_ROLES = ("STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN")
_TAG_SUGGEST_ROLES = (
    "SALES_STAFF", "SALES_CASHIER", "STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN",
)
_MAX_TAGS = 10
_MAX_TAG_LEN = 50
_MAX_SUGGESTIONS_PER_DAY = 5

# PII patterns a tag may NOT contain. A tag that matches ANY is rejected.
_PHONE_IN_TEXT_RE = re.compile(r"\d{10}")
_EMAIL_IN_TEXT_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_GSTIN_IN_TEXT_RE = re.compile(r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]", re.IGNORECASE)


def _clean_tag(tag: str) -> Optional[str]:
    """Return a sanitised tag, or None if it is empty or carries PII. The whole
    tag is rejected (not partially masked) so PII never lands on the record."""
    if not isinstance(tag, str):
        return None
    t = _sanitize_text(tag)[: _MAX_TAG_LEN].strip()
    if not t:
        return None
    if _PHONE_IN_TEXT_RE.search(t) or _EMAIL_IN_TEXT_RE.search(t) or _GSTIN_IN_TEXT_RE.search(t):
        return None
    return t


def _tag_suggestions_coll():
    """tag_suggestions collection via the seeded DB wrapper (robust under tests +
    CI -- the same accessor the credit-note ledger uses). None when DB absent."""
    from ..dependencies import get_db

    db = get_db()
    if db is None or not getattr(db, "is_connected", True):
        return None
    try:
        return db.get_collection("tag_suggestions")
    except Exception:  # noqa: BLE001
        return None


class TagsUpdate(BaseModel):
    tags: List[str] = Field(default_factory=list)


class TagSuggestBody(BaseModel):
    tag: str = Field(..., min_length=1, max_length=_MAX_TAG_LEN)


@router.patch("/{customer_id}/tags")
async def set_customer_tags(
    customer_id: str = Path(..., description="Customer ID"),
    body: TagsUpdate = Body(...),
    current_user: dict = Depends(require_roles(*_TAG_MANAGER_ROLES)),
):
    """Replace a customer's tags (STORE_MANAGER+). PII-bearing tags (phone /
    email / GSTIN) are stripped; max 10 tags, each max 50 chars. Single-doc
    update + audit."""
    repo = get_customer_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    customer = _scoped_customer_or_404(customer_id, current_user, write=True)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    cleaned: List[str] = []
    for raw in (body.tags or []):
        c = _clean_tag(raw)
        if c and c not in cleaned:
            cleaned.append(c)
        if len(cleaned) >= _MAX_TAGS:
            break

    before = list(customer.get("tags") or [])
    try:
        repo.update(customer_id, {"tags": cleaned})
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="Failed to persist tags")

    _audit_customer(
        "CUSTOMER_TAGS_SET",
        customer_id,
        current_user,
        before_state={"tags": before},
        after_state={"tags": cleaned},
    )
    return {"customer_id": customer_id, "tags": cleaned}


@router.post("/{customer_id}/tags/suggest")
async def suggest_customer_tag(
    customer_id: str = Path(..., description="Customer ID"),
    body: TagSuggestBody = Body(...),
    current_user: dict = Depends(require_roles(*_TAG_SUGGEST_ROLES)),
):
    """Staff suggest a tag; it is written PENDING and is invisible to NBA until a
    manager approves it. Rate-limited to 5 suggestions per user per day. PII tags
    are rejected (422)."""
    repo = get_customer_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    _scoped_customer_or_404(customer_id, current_user, write=True)

    cleaned = _clean_tag(body.tag)
    if not cleaned:
        raise HTTPException(status_code=422, detail="Tag is empty or contains personal data (phone/email/GSTIN)")

    user_id = current_user.get("user_id") or current_user.get("id")
    sug_coll = _tag_suggestions_coll()
    if sug_coll is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    # Rate-limit: max 5 PENDING-or-not suggestions by this user since today's start.
    # IST (TZ-P3): "per day" means the IST business day. suggested_at is stored as
    # a naive-UTC ISO string, so the window bound must be the naive-UTC instant of
    # IST-midnight (ist_day_start_utc) -- both sides stay on ONE frame (naive UTC)
    # while the day boundary itself is the IST calendar day.
    today_start = ist_day_start_utc().isoformat()
    try:
        recent = sug_coll.count_documents(
            {"suggested_by": user_id, "suggested_at": {"$gte": today_start}}
        )
    except Exception:  # noqa: BLE001
        recent = 0
    if recent >= _MAX_SUGGESTIONS_PER_DAY:
        raise HTTPException(status_code=429, detail="Daily tag-suggestion limit reached")

    # IST day in the human-readable id (the stored timestamp stays naive UTC).
    suggestion_id = f"TAGSUG-{now_ist().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"
    sug = {
        "suggestion_id": suggestion_id,
        "customer_id": customer_id,
        "tag": cleaned,
        "status": "pending",
        "suggested_by": user_id,
        "suggested_at": utc_now_iso(),
        "reviewed_by": None,
        "reviewed_at": None,
        "store_id": current_user.get("active_store_id"),
    }
    try:
        sug_coll.insert_one(sug)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="Failed to persist suggestion")

    return {"suggestion_id": suggestion_id, "tag": cleaned, "status": "pending"}


@router.get("/{customer_id}/tags/suggestions")
async def list_tag_suggestions(
    customer_id: str = Path(..., description="Customer ID"),
    current_user: dict = Depends(require_roles(*_TAG_MANAGER_ROLES)),
):
    """List PENDING tag suggestions for a customer (STORE_MANAGER+)."""
    # A tag can carry PII (a phone number, a note about the person), so the
    # suggestions on a customer are as store-scoped as the customer.
    _scoped_customer_or_404(customer_id, current_user)
    sug_coll = _tag_suggestions_coll()
    if sug_coll is None:
        return {"customer_id": customer_id, "suggestions": []}
    try:
        rows = list(sug_coll.find(
            {"customer_id": customer_id, "status": "pending"}, {"_id": 0}
        ))
    except Exception:  # noqa: BLE001
        rows = []
    return {"customer_id": customer_id, "suggestions": rows}


@router.post("/{customer_id}/tags/suggestions/{suggestion_id}/approve")
async def approve_tag_suggestion(
    customer_id: str = Path(..., description="Customer ID"),
    suggestion_id: str = Path(..., description="Suggestion ID"),
    current_user: dict = Depends(require_roles(*_TAG_MANAGER_ROLES)),
):
    """Approve a pending suggestion: mark it approved (single-doc update) then
    add the tag to customers.tags via $addToSet (idempotent single-doc update).
    Two sequential single-document writes -- no cross-collection transaction."""
    _scoped_customer_or_404(customer_id, current_user, write=True)
    repo = get_customer_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    sug_coll = _tag_suggestions_coll()
    if sug_coll is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    sug = sug_coll.find_one_and_update(
        {"suggestion_id": suggestion_id, "customer_id": customer_id, "status": "pending"},
        {"$set": {
            "status": "approved",
            "reviewed_by": current_user.get("user_id") or current_user.get("id"),
            "reviewed_at": utc_now_iso(),
        }},
    )
    if not sug:
        raise HTTPException(status_code=404, detail="Pending suggestion not found")

    tag = _clean_tag(sug.get("tag", ""))
    if tag:
        try:
            repo.collection.update_one(
                {"customer_id": customer_id}, {"$addToSet": {"tags": tag}}
            )
        except Exception:  # noqa: BLE001
            pass
    _audit_customer(
        "CUSTOMER_TAG_APPROVED", customer_id, current_user,
        after_state={"tag": tag, "suggestion_id": suggestion_id},
    )
    return {"ok": True, "tag": tag}


@router.post("/{customer_id}/tags/suggestions/{suggestion_id}/reject")
async def reject_tag_suggestion(
    customer_id: str = Path(..., description="Customer ID"),
    suggestion_id: str = Path(..., description="Suggestion ID"),
    current_user: dict = Depends(require_roles(*_TAG_MANAGER_ROLES)),
):
    """Reject a pending suggestion (single-doc update). The tag never lands on
    the customer record."""
    _scoped_customer_or_404(customer_id, current_user, write=True)
    repo = get_customer_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    sug_coll = _tag_suggestions_coll()
    if sug_coll is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    sug = sug_coll.find_one_and_update(
        {"suggestion_id": suggestion_id, "customer_id": customer_id, "status": "pending"},
        {"$set": {
            "status": "rejected",
            "reviewed_by": current_user.get("user_id") or current_user.get("id"),
            "reviewed_at": utc_now_iso(),
        }},
    )
    if not sug:
        raise HTTPException(status_code=404, detail="Pending suggestion not found")
    _audit_customer(
        "CUSTOMER_TAG_REJECTED", customer_id, current_user,
        after_state={"suggestion_id": suggestion_id},
    )
    return {"ok": True}
