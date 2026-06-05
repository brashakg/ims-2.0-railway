"""
IMS 2.0 - Customers Router
===========================
Customer and patient management endpoints
"""

from fastapi import APIRouter, HTTPException, Depends, Query, Path, Body
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Any, Dict, List, Optional
from datetime import date, datetime
import uuid
import re
from .auth import get_current_user, require_roles

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
_GSTIN_RE = re.compile(
    r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$"
)

# Basic e-mail sanity check (no external dependency).
# Deliberately permissive -- just ensures there is an @, a dot-separated domain,
# and no whitespace. Full RFC-5321 compliance is intentionally out of scope.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Allowed customer types.
# India-specific customer-create rules (enabled per product decision):
#  - mobile must be a valid Indian mobile: 10 digits with a leading 6-9
#  - a B2B customer must carry a valid GSTIN (Indian B2B invoicing requires it)
_VALID_CUSTOMER_TYPES = {"B2C", "B2B"}


from ..dependencies import (
    get_customer_repository,
    get_audit_repository,
    validate_store_access,
)
from ..services.phone import normalize_indian_mobile

router = APIRouter()


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
            acct = _all_in(c.get("name"), c.get("mobile"), c.get("phone"), c.get("email"))
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
    name: str
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


class CustomerCreate(BaseModel):
    customer_type: str = "B2C"  # B2C, B2B
    name: str = Field(..., min_length=2)
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
        """Restrict to the known set {B2C, B2B}; reject unknown values early."""
        if v not in _VALID_CUSTOMER_TYPES:
            raise ValueError(
                f"customer_type must be one of {sorted(_VALID_CUSTOMER_TYPES)}, got '{v}'"
            )
        return v

    @field_validator("gstin", mode="after")
    @classmethod
    def validate_gstin(cls, v):
        """Validate 15-char Indian GSTIN FORMAT when a non-empty value is supplied.

        Absent/blank is accepted at this (format-only) layer; B2C may omit GSTIN
        entirely. PRESENCE of a GSTIN for B2B customers is enforced separately by
        the b2b_requires_gstin model validator.
        """
        if v and not _GSTIN_RE.match(v):
            raise ValueError(
                "GSTIN must be a valid 15-character Indian GSTIN "
                "(e.g. 27AAPFU0939F1ZV)"
            )
        return v

    @field_validator("email", mode="after")
    @classmethod
    def validate_email(cls, v):
        """Basic email format check when a non-empty value is provided.

        Uses a simple local regex -- the email-validator package is NOT a
        dependency and must not be added here.
        """
        if v and not _EMAIL_RE.match(v):
            raise ValueError("email must be a valid email address (e.g. a@b.com)")
        return v

    @field_validator("dob", mode="after")
    @classmethod
    def dob_not_future(cls, v):
        """Reject a DOB that is in the future -- a birthdate cannot be tomorrow."""
        if v is not None and v > date.today():
            raise ValueError("Date of birth cannot be in the future")
        return v

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
    # POS-4: per-customer credit limit (khata). 0 = no limit (unlimited).
    # B2B accounts typically carry a non-zero limit; B2C defaults to 0.
    credit_limit: Optional[float] = Field(default=None, ge=0)

    @field_validator("name", mode="before")
    @classmethod
    def sanitize_name_update(cls, v):
        return _sanitize_text(v) if isinstance(v, str) else v


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.get("")
async def list_customers(
    search: Optional[str] = Query(None),
    customer_type: Optional[str] = Query(None),
    store_id: Optional[str] = Query(
        None,
        description=(
            "Filter customers by store. SUPERADMIN/ADMIN can pass any store_id "
            "to scope the view (used by the topbar store-switcher). Lower roles "
            "ignore this and always get their own active store."
        ),
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
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
    """
    repo = get_customer_repository()

    if repo is not None:
        # Build filter
        filter_dict: Dict[str, Any] = {}
        if customer_type:
            filter_dict["customer_type"] = customer_type

        # Determine the effective store filter
        user_roles = current_user.get("roles", [])
        is_hq = any(r in user_roles for r in ["SUPERADMIN", "ADMIN", "AREA_MANAGER"])
        if is_hq:
            # HQ roles: honour explicit ?store_id, otherwise no scope.
            effective_store = store_id
        else:
            # Store-level roles: always pinned to active_store_id.
            effective_store = current_user.get("active_store_id")

        if effective_store:
            # Match either home_store_id (seed/old) or preferred_store_id
            # (TechCherry import + future inserts).
            filter_dict["$or"] = [
                {"home_store_id": effective_store},
                {"preferred_store_id": effective_store},
            ]

        # If search provided, use search method (also respects store filter)
        if search:
            customers = _annotate_customer_matches(
                repo.search_customers(search, effective_store), search
            )
        else:
            customers = repo.find_many(filter_dict, skip=skip, limit=limit)

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
        # Check if mobile already exists
        existing = repo.find_by_mobile(customer.mobile)
        if existing is not None:
            raise HTTPException(
                status_code=400, detail="Customer with this mobile already exists"
            )

        # Prepare customer data
        customer_data = {
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
                datetime.utcnow().isoformat() if customer.data_consent else None
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

        # Add default patient (self) if no patients provided
        if customer.patients:
            for p in customer.patients:
                customer_data["patients"].append(
                    {
                        "patient_id": str(uuid.uuid4()),
                        "name": p.name,
                        "mobile": p.mobile,
                        "dob": p.dob.isoformat() if p.dob else None,
                        "anniversary": (
                            p.anniversary.isoformat() if p.anniversary else None
                        ),
                        # Honor the caller-supplied relation; fall back to the
                        # name-heuristic only when the field is absent/blank.
                        "relation": p.relation or (
                            "Self" if p.name == customer.name else "Other"
                        ),
                    }
                )
        else:
            # Add self as default patient
            customer_data["patients"].append(
                {
                    "patient_id": str(uuid.uuid4()),
                    "name": customer.name,
                    "mobile": customer.mobile,
                    "relation": "Self",
                }
            )

        created = repo.create(customer_data)
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
        if customer:
            return customer

    raise HTTPException(status_code=404, detail="Customer not found")


@router.get("/{customer_id}")
async def get_customer(
    customer_id: str = Path(..., description="Customer ID"),
    current_user: dict = Depends(get_current_user),
):
    """Get customer by ID"""
    repo = get_customer_repository()

    if repo is not None:
        customer = repo.find_by_id(customer_id)
        if customer:
            return customer

    raise HTTPException(status_code=404, detail="Customer not found")


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
        existing = repo.find_by_id(customer_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Customer not found")

        update_data = customer.model_dump(exclude_unset=True)

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

        # Handle patients additively
        if "patients" in update_data:
            incoming = update_data.pop("patients") or []
            current_patients = list(existing.get("patients") or [])
            seen_keys = {
                (
                    (p.get("name") or "").strip().lower(),
                    (p.get("mobile") or "").strip(),
                )
                for p in current_patients
            }
            for p in incoming:
                # `p` is a dict (PatientCreate dumped)
                name = (p.get("name") or "").strip()
                mobile = (p.get("mobile") or "").strip()
                if not name:
                    continue
                key = (name.lower(), mobile)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                current_patients.append(
                    {
                        "patient_id": str(uuid.uuid4()),
                        "name": name,
                        "mobile": mobile or None,
                        "dob": (
                            p["dob"].isoformat()
                            if isinstance(p.get("dob"), date)
                            else p.get("dob")
                        ),
                        "anniversary": (
                            p["anniversary"].isoformat()
                            if isinstance(p.get("anniversary"), date)
                            else p.get("anniversary")
                        ),
                        "relation": p.get("relation") or "Other",
                    }
                )
            update_data["patients"] = current_patients

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
        existing = repo.find_by_id(customer_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Customer not found")

        patient_data = {
            "patient_id": str(uuid.uuid4()),
            "name": patient.name,
            "mobile": patient.mobile,
            "dob": patient.dob.isoformat() if patient.dob else None,
            "anniversary": (
                patient.anniversary.isoformat() if patient.anniversary else None
            ),
            "relation": patient.relation or "Family",
        }

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

    customer = repo.find_by_id(customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")

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
    return {"orders": orders}


def _ar_outstanding(customer_id: str, customer_doc: Optional[dict]) -> float:
    """Sum of CREDIT-tendered amounts still unpaid for a customer.

    Scans the customer's orders for any payment row with method=CREDIT and
    accumulates the credited amounts. Subtracts any subsequent real-money
    payments that reduced balance_due to approximate the true AR balance.

    Fail-soft: returns 0.0 if the DB or order collection is unavailable.
    """
    try:
        from ..dependencies import get_order_repository

        order_repo = get_order_repository()
        if order_repo is None:
            return 0.0

        # Match by customer_id (also by phone for TechCherry imports)
        phone = None
        if customer_doc is not None:
            phone = customer_doc.get("phone") or customer_doc.get("mobile")
        or_clauses: list = [{"customer_id": customer_id}]
        if phone:
            or_clauses.append({"customer_phone": phone})

        orders = order_repo.find_many(
            {
                "$or": or_clauses,
                "status": {"$nin": ["CANCELLED"]},
            },
            sort=[("created_at", -1)],
            limit=500,
        )
        total = 0.0
        for order in orders or []:
            # balance_due on a CREDIT order is the outstanding amount the
            # customer still owes. We sum balance_due only when the order
            # has at least one CREDIT payment (meaning it was deliberately
            # put on account), and the payment_status is not PAID.
            pstatus = order.get("payment_status", "")
            if pstatus == "PAID":
                continue
            has_credit = any(
                p.get("method") == "CREDIT"
                for p in (order.get("payments") or [])
            )
            if has_credit:
                total += float(order.get("balance_due") or 0)
        return round(total, 2)
    except Exception:  # noqa: BLE001
        return 0.0


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
        "credit_limit": 50000.0,  // 0 = unlimited
        "ar_outstanding": 12500.0,
        "ar_available": 37500.0,  // limit - outstanding; null when unlimited
        "limit_exceeded": false
      }

    Fail-soft: all amounts default to 0 if the DB is unavailable.
    """
    repo = get_customer_repository()
    customer = repo.find_by_id(customer_id) if repo is not None else None
    if repo is not None and customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")

    credit_limit = float((customer or {}).get("credit_limit") or 0)
    ar_outstanding = _ar_outstanding(customer_id, customer)
    ar_available = None if credit_limit == 0 else round(credit_limit - ar_outstanding, 2)
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


@router.post("/{customer_id}/loyalty/add")
async def add_loyalty_points(
    customer_id: str = Path(..., description="Customer ID"),
    points: int = Query(..., ge=1, description="Loyalty points to add"),
    current_user: dict = Depends(require_roles(*_CREDIT_ROLES)),
):
    """Add loyalty points to customer"""
    repo = get_customer_repository()

    if repo is not None:
        existing = repo.find_by_id(customer_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Customer not found")

        if repo.add_loyalty_points(customer_id, points):
            return {
                "message": f"Added {points} loyalty points",
                "new_total": existing.get("loyalty_points", 0) + points,
            }

        raise HTTPException(status_code=500, detail="Failed to add loyalty points")

    return {"message": f"Added {points} loyalty points"}


@router.post("/{customer_id}/store-credit/add")
async def add_store_credit(
    customer_id: str,
    amount: float = Query(..., gt=0),
    current_user: dict = Depends(require_roles(*_CREDIT_ROLES)),
):
    """Add store credit to customer"""
    repo = get_customer_repository()

    if repo is not None:
        existing = repo.find_by_id(customer_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Customer not found")

        if repo.add_store_credit(customer_id, amount):
            return {
                "message": f"Added store credit: {amount}",
                "new_total": existing.get("store_credit", 0) + amount,
            }

        raise HTTPException(status_code=500, detail="Failed to add store credit")


# ============================================================================
# STORE-CREDIT / CREDIT-NOTE LEDGER (auditable history per customer)
# ============================================================================


class StoreCreditEntryRequest(BaseModel):
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


def _post_credit_entry(
    customer_id: str, entry_type: str, body: StoreCreditEntryRequest, current_user: dict
):
    from ..services import store_credit_ledger as scl

    repo = get_customer_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database not available")
    existing = repo.find_by_id(customer_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Customer not found")

    et = (entry_type or "").upper()

    # ------------------------------------------------------------------
    # REDEEM (debit) -> ATOMIC guarded decrement, no double-spend.
    # ------------------------------------------------------------------
    # The bug being fixed: the old code recomputed balance_after from a STALE
    # pre-read snapshot, so two concurrent redeems both read the same balance,
    # both passed make_entry's Python check, and both wrote an absolute
    # store_credit value -- the second clobbering the first and effectively
    # spending the same credit twice. Now the spend is a single conditional
    # decrement filtered on store_credit >= amount (atomic in Mongo); the
    # returned balance is read from the POST-update document, never a snapshot.
    if et == scl.REDEEMED:
        try:
            amt = round(float(body.amount), 2)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="amount must be a number")
        if amt <= 0:
            raise HTTPException(status_code=400, detail="amount must be greater than 0")

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
            # Collection can't do a conditional update (minimal stand-in). Fall
            # back to the validated read-modify-write path below.
            new_balance = None
        else:
            # Authoritative post-update balance from the decremented document.
            new_balance = float((debited or {}).get("store_credit", 0) or 0)

        if new_balance is not None:
            # Build the ledger row whose balance_after matches the atomic result.
            entry = scl.make_entry(
                customer_id=customer_id,
                entry_type=scl.REDEEMED,
                amount=amt,
                current_balance=new_balance + amt,  # pre-debit balance
                reason=body.reason or "",
                ref=body.ref,
                store_id=current_user.get("active_store_id"),
                user_id=current_user.get("user_id"),
            )
            entry["balance_after"] = round(new_balance, 2)
            coll = _ledger_coll()
            if coll is not None:
                try:
                    coll.insert_one(dict(entry))
                except Exception:  # noqa: BLE001
                    pass
            entry.pop("_id", None)
            return {"entry": entry, "balance": entry["balance_after"]}
        # else: fall through to the legacy snapshot path (no-atomic fallback).

    # ------------------------------------------------------------------
    # ISSUE / ADJUST (credit), or no-atomic REDEEM fallback.
    # A credit cannot overspend, so the snapshot path is safe for it.
    # ------------------------------------------------------------------
    balance = _current_credit_balance(customer_id, existing)
    try:
        entry = scl.make_entry(
            customer_id=customer_id,
            entry_type=entry_type,
            amount=body.amount,
            current_balance=balance,
            reason=body.reason or "",
            ref=body.ref,
            store_id=current_user.get("active_store_id"),
            user_id=current_user.get("user_id"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    coll = _ledger_coll()
    if coll is not None:
        coll.insert_one(dict(entry))
    # Keep the legacy number in sync so the rest of the app stays correct.
    try:
        repo.update(customer_id, {"store_credit": entry["balance_after"]})
    except Exception:  # noqa: BLE001
        pass
    entry.pop("_id", None)
    return {"entry": entry, "balance": entry["balance_after"]}


@router.post("/{customer_id}/store-credit/issue")
async def issue_store_credit(
    customer_id: str,
    body: StoreCreditEntryRequest,
    current_user: dict = Depends(require_roles(*_CREDIT_ROLES)),
):
    """Issue store credit (e.g. a credit note from a return). Appends a ledger
    entry and updates the running balance."""
    return _post_credit_entry(customer_id, "ISSUED", body, current_user)


@router.post("/{customer_id}/store-credit/redeem")
async def redeem_store_credit(
    customer_id: str,
    body: StoreCreditEntryRequest,
    current_user: dict = Depends(require_roles(*_CREDIT_ROLES)),
):
    """Redeem store credit. Rejected if it exceeds the current balance."""
    return _post_credit_entry(customer_id, "REDEEMED", body, current_user)


@router.get("/{customer_id}/store-credit/ledger")
async def get_store_credit_ledger(
    customer_id: str, current_user: dict = Depends(get_current_user)
):
    """Full credit-note ledger for a customer + current balance, newest first."""
    repo = get_customer_repository()
    existing = repo.find_by_id(customer_id) if repo is not None else None
    coll = _ledger_coll()
    entries = []
    if coll is not None:
        entries = list(
            coll.find({"customer_id": customer_id}, {"_id": 0}).sort("created_at", -1)
        )
    balance = _current_credit_balance(customer_id, existing)
    return {"customer_id": customer_id, "balance": balance, "entries": entries}
