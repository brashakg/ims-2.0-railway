"""
IMS 2.0 - Stores Router
========================
Store management endpoints
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import List, Optional

from .auth import get_current_user, require_roles
from ..dependencies import (
    get_store_repository,
    get_user_repository,
    get_order_repository,
    get_db,
    validate_store_access,
)
from ..services import org_validation as ov

router = APIRouter()

# Store outlet types. ONLINE is the storefront-fulfilment store type (WizOpt
# multi-storefront Phase 0): an ONLINE store (e.g. BV-ONLINE-01) is where an
# online storefront's orders are fulfilled from. Additive only -- existing HQ /
# RETAIL / WAREHOUSE stores are completely unaffected.
STORE_TYPES = ("HQ", "RETAIL", "WAREHOUSE", "ONLINE")
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


def _count(db, collection: str, query: dict) -> int:
    """Mock-safe count_documents; 0 on any error / missing collection."""
    try:
        coll = db.get_collection(collection)
        if coll is None:
            return 0
        return coll.count_documents(query)
    except Exception:
        return 0


@router.get("/go-live-checklist")
async def go_live_checklist(current_user: dict = Depends(require_roles("ADMIN"))):
    """Go-live readiness checklist. Aggregates the real prerequisites for the
    first live sale into one payload so the owner sees what's done vs still
    missing, each with a 'do this next' route. Read-only; never mutates.

    Checks (each: key, label, status PASS/WARN/FAIL, count, hint, route):
      * stores      — at least one active store exists (GST invoice + geo-fence
                      login both need a store on file).
      * store_gstin — every active store has a GSTIN (B2B/GST invoice needs it).
      * staff       — at least one active non-admin login exists to run a till.
      * products    — catalog has active products to sell.
      * tax_codes   — products carry an HSN + GST rate (deep check is the
                      tax-code audit report; here we just flag blanks).
      * invoice     — invoice numbering settings are configured.
    A FAIL is a hard blocker; WARN is 'should fix'; PASS is ready.
    """
    db = get_db()
    checks = []

    def add(key, label, status, count, hint, route):
        checks.append(
            {
                "key": key,
                "label": label,
                "status": status,
                "count": count,
                "hint": hint,
                "route": route,
            }
        )

    if db is None or not getattr(db, "is_connected", True):
        # No DB reachable — report unknown rather than a false green.
        return {
            "ready": False,
            "checks": [
                {
                    "key": "database",
                    "label": "Database connection",
                    "status": "FAIL",
                    "count": 0,
                    "hint": "Backend can't reach the database. Check the server.",
                    "route": None,
                }
            ],
            "summary": {"pass": 0, "warn": 0, "fail": 1, "total": 1},
        }

    # 1. Active stores
    active_stores = _count(db, "stores", {"is_active": {"$ne": False}})
    add(
        "stores",
        "Stores set up",
        "PASS" if active_stores > 0 else "FAIL",
        active_stores,
        (
            "Add at least one store — GST invoices and staff geo-fenced login both "
            "need a store on file."
            if active_stores == 0
            else f"{active_stores} active store(s)."
        ),
        "/settings?tab=stores",
    )

    # 2. Stores missing a GSTIN
    stores_no_gstin = _count(
        db,
        "stores",
        {
            "is_active": {"$ne": False},
            "$or": [{"gstin": {"$in": [None, ""]}}, {"gstin": {"$exists": False}}],
        },
    )
    if active_stores > 0:
        add(
            "store_gstin",
            "Store GSTIN on file",
            "PASS" if stores_no_gstin == 0 else "WARN",
            stores_no_gstin,
            (
                "Every store has a GSTIN."
                if stores_no_gstin == 0
                else f"{stores_no_gstin} store(s) have no GSTIN — required on a GST tax invoice."
            ),
            "/settings?tab=stores",
        )

    # 3. Staff logins (active, non-superadmin/admin — someone to run a till)
    staff = _count(
        db,
        "users",
        {"is_active": {"$ne": False}, "roles": {"$nin": ["SUPERADMIN", "ADMIN"]}},
    )
    add(
        "staff",
        "Staff logins created",
        "PASS" if staff > 0 else "WARN",
        staff,
        (
            "Create store staff logins (cashier / optometrist / manager) so they can "
            "sign in and bill."
            if staff == 0
            else f"{staff} staff login(s)."
        ),
        "/settings?tab=users",
    )

    # 4. Active products
    products = _count(db, "products", {"is_active": {"$ne": False}})
    add(
        "products",
        "Products loaded",
        "PASS" if products > 0 else "FAIL",
        products,
        (
            "Load your catalog — there's nothing to sell yet."
            if products == 0
            else f"{products} active product(s)."
        ),
        "/catalog/add",
    )

    # 5. Products missing a tax code (blank HSN or no GST rate)
    if products > 0:
        bad_tax = _count(
            db,
            "products",
            {
                "is_active": {"$ne": False},
                "$or": [
                    {"hsn_code": {"$in": [None, ""]}},
                    {"hsn_code": {"$exists": False}},
                    {"gst_rate": {"$in": [None]}},
                    {"gst_rate": {"$exists": False}},
                ],
            },
        )
        add(
            "tax_codes",
            "Product tax codes set",
            "PASS" if bad_tax == 0 else "WARN",
            bad_tax,
            (
                "All products have an HSN + GST rate. Run the Tax-Code Audit to "
                "confirm they're correct."
                if bad_tax == 0
                else f"{bad_tax} product(s) have a blank HSN or GST rate — fix before billing."
            ),
            "/reports?tab=inventory",
        )

    # 6. Invoice numbering configured
    has_invoice = _count(db, "invoice_settings", {}) > 0
    add(
        "invoice",
        "Invoice numbering configured",
        "PASS" if has_invoice else "WARN",
        1 if has_invoice else 0,
        (
            "Invoice prefix + starting number are set."
            if has_invoice
            else "Set your invoice prefix and starting number before the first bill."
        ),
        "/settings?tab=tax-invoice",
    )

    n_pass = sum(1 for c in checks if c["status"] == "PASS")
    n_warn = sum(1 for c in checks if c["status"] == "WARN")
    n_fail = sum(1 for c in checks if c["status"] == "FAIL")
    return {
        "ready": n_fail == 0,
        "checks": checks,
        "summary": {
            "pass": n_pass,
            "warn": n_warn,
            "fail": n_fail,
            "total": len(checks),
        },
    }


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

    # The store CODE is the store's identity. Every consumer -- users[].store_ids,
    # store-scope checks, the topbar store pill, order/invoice store context --
    # keys off store_id, and the whole app was built assuming store_id IS the
    # human code (BV-BOK-01, ...). So we make store_id == the validated, uppercased
    # store_code rather than letting the repo mint a random uuid (which leaked into
    # the UI and broke assignment-by-code). Reject a malformed code up front.
    code = ov.normalize_store_code(store.store_code)
    if not ov.validate_store_code(code):
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid store code. Use a short human code like 'BV-BOK-01' "
                "(2-10 chars, letters/digits/hyphens, starting with a letter)."
            ),
        )

    if repo is not None:
        # store_code is now the unique identifier -- reject a duplicate (409).
        # Check both store_code (legacy) and store_id (== code going forward) so a
        # collision can't slip in either way.
        if repo.find_by_code(code) or repo.find_by_id(code):
            raise HTTPException(
                status_code=409, detail=f"Store code '{code}' already exists"
            )

        state_code = _state_code_for(store.state_code, store.state)
        derived_gstin = _derive_store_gstin(db, store.entity_id, state_code)

        # Persist every supplied field, then stamp derived / server values.
        store_data = store.model_dump()
        store_data.update(
            {
                "store_code": code,
                # store_id == store_code (NOT a uuid). Set explicitly so the repo
                # does not auto-generate a uuid in BaseRepository.create().
                "store_id": code,
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

    # No repo (DB unreachable). Still echo the human code as the store_id so the
    # convention holds even in mock mode -- never invent a uuid.
    return {"store_id": code, "store_code": code, "message": "Store created"}


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
            orders = order_repo.find_many({"store_id": store_id}, limit=0) or []
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
