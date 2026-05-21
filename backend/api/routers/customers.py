"""
IMS 2.0 - Customers Router
===========================
Customer and patient management endpoints
"""

from fastapi import APIRouter, HTTPException, Depends, Query, Path, Body
from pydantic import BaseModel, Field, field_validator
from typing import Any, Dict, List, Optional
from datetime import date
import uuid
import re
from .auth import get_current_user


def _sanitize_text(value: str) -> str:
    """Strip HTML tags and dangerous characters from user input."""
    if not value:
        return value
    # Remove HTML tags
    clean = re.sub(r"<[^>]+>", "", value)
    # Remove control characters except newline/tab
    clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", clean)
    return clean.strip()


from ..dependencies import get_customer_repository, validate_store_access

router = APIRouter()


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


class CustomerCreate(BaseModel):
    customer_type: str = "B2C"  # B2C, B2B
    name: str = Field(..., min_length=2)
    mobile: str = Field(..., pattern=r"^\d{10}$")
    email: Optional[str] = None
    dob: Optional[date] = None
    anniversary: Optional[date] = None
    gstin: Optional[str] = None
    billing_address: Optional[dict] = None
    # Marketing opt-in defaults to True so the engine can include them in
    # birthday / Rx-expiry / WhatsApp campaigns. Operators flip this off
    # only when the customer explicitly declines on the spot.
    marketing_consent: bool = True
    patients: List[PatientCreate] = []

    @field_validator("name", mode="before")
    @classmethod
    def sanitize_name(cls, v):
        return _sanitize_text(v) if isinstance(v, str) else v


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
        is_hq = any(
            r in user_roles for r in ["SUPERADMIN", "ADMIN", "AREA_MANAGER"]
        )
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
            customers = repo.search_customers(search, effective_store)
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
            "email": customer.email,
            "dob": customer.dob.isoformat() if customer.dob else None,
            "anniversary": (
                customer.anniversary.isoformat() if customer.anniversary else None
            ),
            "gstin": customer.gstin,
            "billing_address": customer.billing_address,
            "marketing_consent": customer.marketing_consent,
            "home_store_id": current_user.get("active_store_id"),
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
                        "relation": "Self" if p.name == customer.name else "Other",
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
        customers = repo.search_customers(q, current_user.get("active_store_id"))
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
    exact = repo.find_by_mobile(digits)
    if exact:
        return {"customers": [exact], "customer": exact}

    matches = repo.search(digits, ["mobile"])
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

        if repo.update(customer_id, update_data):
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
            return {"patient_id": patient_data["patient_id"], "name": patient.name}

        raise HTTPException(status_code=500, detail="Failed to add patient")

    return {"patient_id": str(uuid.uuid4())}


@router.get("/{customer_id}/orders")
async def get_customer_orders(
    customer_id: str = Path(..., description="Customer ID"),
    current_user: dict = Depends(get_current_user),
):
    """Get orders for a customer"""
    # This will be implemented when we connect OrderRepository
    return {"orders": []}


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
    current_user: dict = Depends(get_current_user),
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
    current_user: dict = Depends(get_current_user),
):
    """Add store credit to customer"""
    repo = get_customer_repository()

    if repo is not None:
        existing = repo.find_by_id(customer_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Customer not found")

        if repo.add_store_credit(customer_id, amount):
            return {
                "message": f"Added ₹{amount} store credit",
                "new_total": existing.get("store_credit", 0) + amount,
            }

        raise HTTPException(status_code=500, detail="Failed to add store credit")

    return {"message": f"Added ₹{amount} store credit"}
