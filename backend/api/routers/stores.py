"""
IMS 2.0 - Stores Router
========================
Store management endpoints
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import List, Optional
import uuid

from .auth import get_current_user
from ..dependencies import (
    get_store_repository,
    get_user_repository,
    get_order_repository,
    validate_store_access,
)
from ..services import org_validation as ov

router = APIRouter()

# Store outlet types.
STORE_TYPES = ("HQ", "RETAIL", "WAREHOUSE")
# Canonical product categories a store can stock/sell (constrains the free-text
# enabled_categories list). Mirrors the catalog category enum.
STORE_CATEGORIES = (
    "FRAME",
    "OPTICAL_LENS",
    "CONTACT_LENS",
    "COLORED_CONTACT_LENS",
    "READING_GLASSES",
    "SUNGLASS",
    "WATCH",
    "SMARTWATCH",
    "WALL_CLOCK",
    "HEARING_AID",
    "ACCESSORIES",
    "SERVICES",
)


# ============================================================================
# SCHEMAS
# ============================================================================


class StoreCreate(BaseModel):
    store_code: str = Field(..., min_length=2, max_length=10)
    store_name: str
    brand: str  # BETTER_VISION, WIZOPT
    entity_id: str  # REQUIRED -- every store belongs to a legal entity (PAN)
    address: str
    city: str
    state: str  # state name (display / back-compat)
    state_code: Optional[str] = None  # 2-digit GST code; used to derive GSTIN
    pincode: str
    phone: str
    email: Optional[str] = None
    whatsapp: Optional[str] = None
    # GSTIN is DERIVED from the entity by state; any supplied value is ignored.
    gstin: Optional[str] = None
    enabled_categories: List[str] = []
    # --- geo-fence (roles 4-7 must log in within radius of these coords) ---
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    geofence_radius_m: Optional[int] = Field(default=500, ge=50, le=20000)
    # --- operational ---
    locality: Optional[str] = None
    landmark: Optional[str] = None
    store_type: Optional[str] = "RETAIL"
    region: Optional[str] = None
    opening_date: Optional[str] = None
    manager_user_id: Optional[str] = None
    working_hours: Optional[str] = None  # e.g. "10:00-20:00"
    weekly_off: Optional[str] = None  # e.g. "TUESDAY"
    upi_vpa: Optional[str] = None
    cost_center: Optional[str] = None
    # --- per-store invoice identity (overrides entity defaults slightly) ---
    invoice_prefix: Optional[str] = None
    invoice_header: Optional[str] = None
    invoice_footer: Optional[str] = None
    invoice_terms: Optional[str] = None


class StoreUpdate(BaseModel):
    store_name: Optional[str] = None
    brand: Optional[str] = None
    entity_id: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    state_code: Optional[str] = None
    pincode: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    whatsapp: Optional[str] = None
    enabled_categories: Optional[List[str]] = None
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    geofence_radius_m: Optional[int] = Field(default=None, ge=50, le=20000)
    locality: Optional[str] = None
    landmark: Optional[str] = None
    store_type: Optional[str] = None
    region: Optional[str] = None
    opening_date: Optional[str] = None
    manager_user_id: Optional[str] = None
    working_hours: Optional[str] = None
    weekly_off: Optional[str] = None
    upi_vpa: Optional[str] = None
    cost_center: Optional[str] = None
    invoice_prefix: Optional[str] = None
    invoice_header: Optional[str] = None
    invoice_footer: Optional[str] = None
    invoice_terms: Optional[str] = None
    is_active: Optional[bool] = None


# ============================================================================
# HELPERS
# ============================================================================


def _get_db():
    try:
        from database.connection import get_db

        return get_db().db
    except Exception:
        return None


def _state_code_for(
    state_code: Optional[str], state_name: Optional[str]
) -> Optional[str]:
    """Best-effort 2-digit GST state code from an explicit code, abbreviation,
    or full state name (e.g. '20' / 'JH' / 'Jharkhand' -> '20')."""
    for candidate in (state_code, state_name):
        if candidate:
            code = ov.normalize_state_code(candidate)
            if code in ov.INDIAN_STATE_CODES:
                return code
    return None


def _derive_store_gstin(
    db, entity_id: Optional[str], state_code: Optional[str]
) -> Optional[str]:
    """Resolve the GSTIN a store bills under = its entity's GSTIN for the store's
    state (single source of truth). Falls back to the entity's primary GSTIN."""
    if db is None or not entity_id:
        return None
    try:
        entity = db.get_collection("entities").find_one(
            {"entity_id": entity_id}, {"_id": 0, "gstins": 1}
        )
    except Exception:
        return None
    if not entity:
        return None
    match = ov.resolve_gstin_for_state(entity.get("gstins") or [], state_code)
    if match:
        return match.get("gstin")
    for g in entity.get("gstins") or []:
        if isinstance(g, dict) and g.get("is_primary"):
            return g.get("gstin")
    return None


def _validate_store_payload(data: dict) -> None:
    """Block (HTTP 400) on malformed store fields. Validates only present keys."""
    if data.get("pincode") and not ov.validate_pincode(data["pincode"]):
        raise HTTPException(status_code=400, detail="Invalid PIN code (6 digits)")
    if data.get("phone") and not ov.validate_phone(data["phone"]):
        raise HTTPException(status_code=400, detail="Invalid phone number")
    if data.get("whatsapp") and not ov.validate_phone(data["whatsapp"]):
        raise HTTPException(status_code=400, detail="Invalid WhatsApp number")
    sc = data.get("state_code")
    if sc and sc not in ov.INDIAN_STATE_CODES:
        raise HTTPException(status_code=400, detail=f"Unknown state code: {sc}")
    st = data.get("store_type")
    if st and st not in STORE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid store_type. Allowed: {', '.join(STORE_TYPES)}",
        )
    bad = [
        c for c in (data.get("enabled_categories") or []) if c not in STORE_CATEGORIES
    ]
    if bad:
        raise HTTPException(
            status_code=400, detail=f"Unknown categories: {', '.join(bad)}"
        )


def _store_active_dependents(db, store_id: str) -> Optional[str]:
    """Human description if a store still has stock / open orders / staff, so
    deactivation can be blocked. Fail-soft."""
    if db is None:
        return None
    for coll in ("stock", "stock_units"):
        try:
            n = db.get_collection(coll).count_documents(
                {
                    "store_id": store_id,
                    "status": {"$nin": ["SOLD", "RETURNED", "SCRAPPED"]},
                }
            )
            if n:
                return f"{n} on-hand stock unit(s)"
        except Exception:
            pass
    try:
        n = db.get_collection("orders").count_documents(
            {
                "store_id": store_id,
                "status": {"$nin": ["DELIVERED", "CANCELLED", "COMPLETED", "REFUNDED"]},
            }
        )
        if n:
            return f"{n} open order(s)"
    except Exception:
        pass
    try:
        n = db.get_collection("users").count_documents(
            {
                "is_active": {"$ne": False},
                "$or": [{"store_ids": store_id}, {"home_store_id": store_id}],
            }
        )
        if n:
            return f"{n} assigned staff member(s)"
    except Exception:
        pass
    return None


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.get("/summary")
async def get_store_summary(current_user: dict = Depends(get_current_user)):
    """Get summary of all stores by brand"""
    repo = get_store_repository()

    if repo is not None:
        summary = repo.get_store_summary()
        return {"summary": summary}

    return {"summary": {}}


@router.get("")
async def list_stores(
    brand: Optional[str] = Query(None),
    city: Optional[str] = Query(None),
    active_only: bool = Query(True),
    current_user: dict = Depends(get_current_user),
):
    """List stores with optional filtering"""
    repo = get_store_repository()

    if repo is not None:
        filter_dict = {}
        if brand:
            filter_dict["brand"] = brand
        if city:
            filter_dict["city"] = city

        if active_only:
            stores = repo.find_active(filter_dict if filter_dict else None)
        else:
            stores = repo.find_many(filter_dict, sort=[("brand", 1), ("store_name", 1)])

        return {"stores": stores, "total": len(stores)}

    return {"stores": [], "total": 0}


@router.post("", status_code=201)
async def create_store(
    store: StoreCreate, current_user: dict = Depends(get_current_user)
):
    """Create a new store (SUPERADMIN/ADMIN only)"""
    # RBAC: Only admins can create stores
    if not any(
        role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN"]
    ):
        raise HTTPException(status_code=403, detail="Only admins can create stores")

    repo = get_store_repository()
    db = _get_db()

    # A store must belong to a known legal entity (so its GSTIN + payroll
    # filings always resolve). Verified when the DB is available.
    entity = None
    if db is not None:
        try:
            entity = db.get_collection("entities").find_one(
                {"entity_id": store.entity_id}
            )
        except Exception:
            entity = None
        if entity is None:
            raise HTTPException(
                status_code=400,
                detail="entity_id does not match a known legal entity",
            )

    _validate_store_payload(store.model_dump())

    if repo is not None:
        # Check if store code exists
        if repo.find_by_code(store.store_code):
            raise HTTPException(status_code=400, detail="Store code already exists")

        state_code = _state_code_for(store.state_code, store.state)
        derived_gstin = _derive_store_gstin(db, store.entity_id, state_code)

        # Persist every supplied field, then stamp derived / server values.
        store_data = store.model_dump()
        store_data.update(
            {
                "state_code": state_code,
                "gstin": derived_gstin,  # single source of truth = entity
                "is_active": True,
                "is_hq": (store.store_type == "HQ"),
                "created_by": current_user.get("user_id"),
            }
        )

        created = repo.create(store_data)
        if created:
            return {
                "store_id": created["store_id"],
                "store_code": created["store_code"],
                "gstin": derived_gstin,
                "message": "Store created",
            }

        raise HTTPException(status_code=500, detail="Failed to create store")

    return {"store_id": str(uuid.uuid4()), "message": "Store created"}


@router.get("/{store_id}")
async def get_store(store_id: str, current_user: dict = Depends(get_current_user)):
    """Get store by ID"""
    # Store-scope: a non-HQ user may only read a store they're assigned to.
    validate_store_access(store_id, current_user)
    repo = get_store_repository()

    if repo is not None:
        store = repo.find_by_id(store_id)
        if store is not None:
            return store
        raise HTTPException(status_code=404, detail="Store not found")

    return {"store_id": store_id}


@router.get("/{store_id}/stats")
async def get_store_stats(
    store_id: str, current_user: dict = Depends(get_current_user)
):
    """Quick store KPIs — order count + revenue + staff count. The
    frontend's storeApi.getStoreStats was 404'ing (no such route).
    Two-segment path, so it isn't shadowed by GET /{store_id}."""
    validate_store_access(store_id, current_user)  # cross-store read guard
    order_repo = get_order_repository()
    user_repo = get_user_repository()
    stats = {
        "store_id": store_id,
        "total_orders": 0,
        "total_revenue": 0.0,
        "staff_count": 0,
    }
    try:
        if order_repo is not None:
            orders = order_repo.find_many({"store_id": store_id}) or []
            stats["total_orders"] = len(orders)
            stats["total_revenue"] = round(
                sum(float(o.get("grand_total") or 0) for o in orders), 2
            )
    except Exception:
        pass
    try:
        if user_repo is not None:
            # Users whose store access includes this store
            staff = user_repo.find_many({"store_ids": store_id}) or []
            stats["staff_count"] = len(staff)
    except Exception:
        pass
    return stats


@router.get("/{store_id}/users")
async def get_store_users(
    store_id: str,
    roles: Optional[str] = Query(
        None,
        description=(
            "Optional CSV of role names to filter to "
            "(e.g. 'SALES_CASHIER,SALES_STAFF,STORE_MANAGER,OPTICIAN'). "
            "Case-insensitive, whitespace tolerant."
        ),
    ),
    active_only: bool = Query(
        True,
        description="If true (default), excludes users with is_active=false.",
    ),
    current_user: dict = Depends(get_current_user),
):
    """List users **explicitly assigned** to this store.

    Match criteria: ``store_ids`` contains store_id, OR ``home_store_id`` equals
    store_id. We deliberately do NOT match ``active_store_id`` -- that was a
    bug where a logged-in SUPERADMIN/ADMIN (or any cross-store role) viewing
    this store would appear here even though they are not assigned to it, and
    show up in the POS salesperson picker. ``active_store_id`` reflects the
    user's currently-selected store context, not a permanent assignment.

    Defaults to active users only. Pass ``active_only=false`` for admin
    deactivated-staff views. Pass ``roles=...`` to narrow to specific role(s);
    the POS salesperson picker passes the sales-attributable role set so it
    never shows accountants/optometrists/superadmins.
    """
    validate_store_access(store_id, current_user)  # cross-store read guard (staff PII)
    user_repo = get_user_repository()
    if user_repo is None:
        return {"users": [], "total": 0}

    query: dict = {
        "$or": [
            {"store_ids": store_id},
            {"home_store_id": store_id},
        ]
    }
    if active_only:
        # Treat a missing is_active flag as "active" (legacy docs); explicit
        # False excludes.
        query["is_active"] = {"$ne": False}
    if roles:
        role_list = [r.strip().upper() for r in roles.split(",") if r.strip()]
        if role_list:
            query["roles"] = {"$in": role_list}

    users = user_repo.find_many(query) or []
    safe = []
    for u in users:
        u.pop("password", None)
        u.pop("password_hash", None)
        u.pop("_id", None)
        safe.append(u)
    return {"users": safe, "total": len(safe)}


@router.put("/{store_id}")
async def update_store(
    store_id: str, store: StoreUpdate, current_user: dict = Depends(get_current_user)
):
    """Update store details"""
    # SYSTEM_INTENT section 11: store configuration is HQ-only. This was open to
    # any authenticated user, letting a cashier rewrite/disable any store's
    # geo-fence. Restrict writes to Admin/Superadmin.
    if not any(r in current_user.get("roles", []) for r in ("SUPERADMIN", "ADMIN")):
        raise HTTPException(
            status_code=403,
            detail="Store configuration is restricted to HQ (Admin/Superadmin)",
        )
    repo = get_store_repository()

    if repo is not None:
        existing = repo.find_by_id(store_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Store not found")

        update_data = store.model_dump(exclude_unset=True)
        _validate_store_payload(update_data)
        db = _get_db()

        # Integrity: block deactivation while the store still holds stock, has
        # open orders, or has staff assigned to it.
        if update_data.get("is_active") is False:
            dep = _store_active_dependents(db, store_id)
            if dep:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Cannot deactivate store: it still has {dep}. "
                        "Clear/transfer those first."
                    ),
                )

        # Re-derive the store GSTIN whenever its entity or state changes, so the
        # store always bills under the correct registration.
        if any(k in update_data for k in ("entity_id", "state", "state_code")):
            entity_id = update_data.get("entity_id") or existing.get("entity_id")
            state_code = _state_code_for(
                update_data.get("state_code") or existing.get("state_code"),
                update_data.get("state") or existing.get("state"),
            )
            update_data["state_code"] = state_code
            update_data["gstin"] = _derive_store_gstin(db, entity_id, state_code)

        update_data["updated_by"] = current_user.get("user_id")

        if repo.update(store_id, update_data):
            return {"store_id": store_id, "message": "Store updated"}

        raise HTTPException(status_code=500, detail="Failed to update store")

    return {"message": "Store updated"}


@router.delete("/{store_id}")
async def delete_store(store_id: str, current_user: dict = Depends(get_current_user)):
    """Soft-delete a store (is_active=False). Frontend adminStoreApi.deleteStore
    was 404'ing. Hard delete is intentionally not exposed — stores carry
    historical orders/inventory that must remain referenceable."""
    roles = current_user.get("roles") or []
    if not any(r in {"SUPERADMIN", "ADMIN"} for r in roles):
        raise HTTPException(status_code=403, detail="SUPERADMIN/ADMIN required")
    repo = get_store_repository()
    if repo is None:
        return {"store_id": store_id, "message": "Store deactivated"}
    existing = repo.find_by_id(store_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Store not found")
    dep = _store_active_dependents(_get_db(), store_id)
    if dep:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot deactivate store: it still has {dep}. "
                "Clear/transfer those first."
            ),
        )
    repo.update(
        store_id,
        {
            "is_active": False,
            "deactivated_at": __import__("datetime").datetime.now().isoformat(),
            "deactivated_by": current_user.get("user_id"),
        },
    )
    return {"store_id": store_id, "message": "Store deactivated"}


@router.post("/{store_id}/categories/{category}")
async def enable_category(
    store_id: str, category: str, current_user: dict = Depends(get_current_user)
):
    """Enable a category for the store"""
    # SYSTEM_INTENT section 11: store configuration is HQ-only (Admin/Superadmin).
    if not any(r in current_user.get("roles", []) for r in ("SUPERADMIN", "ADMIN")):
        raise HTTPException(
            status_code=403,
            detail="Store configuration is restricted to HQ (Admin/Superadmin)",
        )
    repo = get_store_repository()

    if repo is not None:
        existing = repo.find_by_id(store_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Store not found")

        if repo.enable_category(store_id, category):
            return {"message": f"Category {category} enabled"}

        raise HTTPException(status_code=500, detail="Failed to enable category")

    return {"message": f"Category {category} enabled"}


@router.delete("/{store_id}/categories/{category}")
async def disable_category(
    store_id: str, category: str, current_user: dict = Depends(get_current_user)
):
    """Disable a category for the store"""
    # SYSTEM_INTENT section 11: store configuration is HQ-only (Admin/Superadmin).
    if not any(r in current_user.get("roles", []) for r in ("SUPERADMIN", "ADMIN")):
        raise HTTPException(
            status_code=403,
            detail="Store configuration is restricted to HQ (Admin/Superadmin)",
        )
    repo = get_store_repository()

    if repo is not None:
        existing = repo.find_by_id(store_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Store not found")

        if repo.disable_category(store_id, category):
            return {"message": f"Category {category} disabled"}

        raise HTTPException(status_code=500, detail="Failed to disable category")

    return {"message": f"Category {category} disabled"}
