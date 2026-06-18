"""
IMS 2.0 - Admin Extras Router
==============================
Fills the /admin/discounts/* and /admin/system/* endpoint surface the
frontend's `adminDiscountApi` and `adminSystemApi` were written against
but which had no backend route (every call 404'd). Mounted at
/api/v1/admin alongside admin_catalog.

Discount config is Mongo-backed (collections: discount_rules,
role_discount_caps, tier_discounts, promo_codes). System endpoints are
thin: status is computed live, settings persist to a singleton doc,
audit-logs proxy the audit collection, and backup/export/import are
honest minimal stubs (full DB backup belongs to infra, not the app).
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
import uuid

from .admin import _require_admin_role

router = APIRouter(dependencies=[Depends(_require_admin_role)])


# ============================================================================
# DB HELPERS
# ============================================================================


def _coll(name: str):
    try:
        from database.connection import get_db

        db = get_db()
        if db and db.is_connected:
            return db.get_collection(name)
    except Exception:
        pass
    return None


def _scrub(doc: Optional[Dict]) -> Optional[Dict]:
    if doc is None:
        return None
    doc.pop("_id", None)
    return doc


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


# ============================================================================
# DISCOUNT RULES  (/admin/discounts/*)
# ============================================================================
# Mirrors the business rules in CLAUDE.md: category caps, role caps,
# tier (loyalty) discounts, and promo codes. The pricing engine should
# read these at quote time; this router is the admin CRUD surface.


@router.get("/discounts/rules")
async def get_discount_rules():
    """Category + brand caps. Returns the canonical caps from CLAUDE.md
    as defaults, overlaid with any DB overrides.

    Unification step-8: the cap NUMBERS are sourced from the single source of
    truth -- services/pricing_caps (the same constants the POS actually enforces
    and that GET /discounts/enforced-caps exposes) -- instead of a hand-copied
    duplicate that could silently drift from what POS bills. The output shape is
    preserved exactly (whole-number values, title-cased luxury brand display
    names) so this endpoint's response is byte-for-byte unchanged.
    """
    from ..services.pricing_caps import (
        CATEGORY_DISCOUNT_CAPS,
        LUXURY_BRAND_CAPS,
    )

    # Whole-number presentation (15, not 15.0) preserved for back-compat.
    def _whole(v: float):
        return int(v) if float(v).is_integer() else v

    # Title-cased display names preserved for the brand keys (the canonical
    # constants store them upper-cased; this maps back to the original casing).
    _BRAND_DISPLAY = {
        "CARTIER": "Cartier",
        "CHOPARD": "Chopard",
        "BVLGARI": "Bvlgari",
        "GUCCI": "Gucci",
        "PRADA": "Prada",
        "VERSACE": "Versace",
        "BURBERRY": "Burberry",
    }
    defaults = {
        "category_caps": {k: _whole(v) for k, v in CATEGORY_DISCOUNT_CAPS.items()},
        "luxury_brand_caps": {
            _BRAND_DISPLAY.get(k, k.title()): _whole(v)
            for k, v in LUXURY_BRAND_CAPS.items()
        },
    }
    coll = _coll("discount_rules")
    if coll is not None:
        doc = coll.find_one({"_id": "discount_rules"})
        if doc:
            doc.pop("_id", None)
            defaults.update(doc)
    return defaults


@router.get("/discounts/role-caps")
async def get_role_discount_caps():
    """Per-role maximum discount %."""
    defaults = {
        "SUPERADMIN": 100,
        "ADMIN": 100,
        "AREA_MANAGER": 25,
        "STORE_MANAGER": 20,
        # SALES_CASHIER merged into SALES_STAFF (backlog #12); both were 10%.
        "SALES_STAFF": 10,
    }
    coll = _coll("role_discount_caps")
    if coll is not None:
        for d in coll.find({}):
            role = d.get("role")
            if role:
                defaults[role] = d.get("max_discount", defaults.get(role, 0))
    return {"role_caps": defaults}


class RoleCapBody(BaseModel):
    role: str
    max_discount: float = Field(..., ge=0, le=100)


@router.post("/discounts/role-caps")
async def set_role_discount_cap(body: RoleCapBody):
    coll = _coll("role_discount_caps")
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    coll.update_one(
        {"role": body.role},
        {
            "$set": {
                "role": body.role,
                "max_discount": body.max_discount,
                "updated_at": _now_iso(),
            }
        },
        upsert=True,
    )
    return {"role": body.role, "max_discount": body.max_discount}


@router.get("/discounts/enforced-caps")
async def get_enforced_discount_caps():
    """The discount caps the POS ACTUALLY enforces, sourced from the live code
    constants -- NOT from any editable Settings collection.

    POS enforcement (orders.py) reads role caps from services.role_caps and
    category/luxury-brand caps from services.pricing_caps; those are set in code
    and are the single source of truth. This read-only endpoint exposes exactly
    those constants so the Settings screen can DISPLAY the real, enforced caps
    instead of an editable table that wrote to a collection the POS never reads.
    Changing a cap means changing the code constants (and a deploy), not a DB
    write -- hence there is no companion setter here.
    """
    from api.services.role_caps import ROLE_DISCOUNT_CAPS
    from api.services.pricing_caps import (
        CATEGORY_DISCOUNT_CAPS,
        LUXURY_BRAND_CAPS,
    )

    return {
        "source": "code_constants",
        "note": (
            "These caps are enforced from code (services/role_caps.py + "
            "services/pricing_caps.py). They are not editable here -- contact an "
            "administrator to change them in code."
        ),
        "role_caps": dict(ROLE_DISCOUNT_CAPS),
        "category_caps": dict(CATEGORY_DISCOUNT_CAPS),
        "luxury_brand_caps": dict(LUXURY_BRAND_CAPS),
    }


@router.get("/discounts/tier-discounts")
async def get_tier_discounts():
    """Loyalty-tier auto-discount %."""
    defaults = {"BRONZE": 0, "SILVER": 2, "GOLD": 5, "PLATINUM": 8}
    coll = _coll("tier_discounts")
    if coll is not None:
        for d in coll.find({}):
            tier = d.get("tier")
            if tier:
                defaults[tier] = d.get("discount", defaults.get(tier, 0))
    return {"tier_discounts": defaults}


class TierDiscountBody(BaseModel):
    tier: str
    discount: float = Field(..., ge=0, le=100)


@router.post("/discounts/tier-discounts")
async def set_tier_discount(body: TierDiscountBody):
    coll = _coll("tier_discounts")
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    coll.update_one(
        {"tier": body.tier},
        {
            "$set": {
                "tier": body.tier,
                "discount": body.discount,
                "updated_at": _now_iso(),
            }
        },
        upsert=True,
    )
    return {"tier": body.tier, "discount": body.discount}


class PromoCodeCreate(BaseModel):
    code: str
    discountType: str = Field(..., pattern="^(PERCENTAGE|FIXED)$")
    discountValue: float = Field(..., gt=0)
    minPurchase: Optional[float] = None
    maxDiscount: Optional[float] = None
    validFrom: str
    validTo: str
    usageLimit: Optional[int] = None
    categories: Optional[List[str]] = None


@router.get("/discounts/promo-codes")
async def get_promo_codes(active: Optional[bool] = Query(None)):
    coll = _coll("promo_codes")
    if coll is None:
        return {"promo_codes": []}
    q: Dict[str, Any] = {}
    if active is not None:
        q["is_active"] = active
    return {"promo_codes": [_scrub(d) for d in coll.find(q) if d]}


@router.post("/discounts/promo-codes", status_code=201)
async def create_promo_code(body: PromoCodeCreate):
    coll = _coll("promo_codes")
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    if coll.find_one({"code": body.code.upper()}):
        raise HTTPException(status_code=400, detail="Promo code already exists")
    code_id = str(uuid.uuid4())
    doc = {
        "_id": code_id,
        "code_id": code_id,
        "code": body.code.upper(),
        "discount_type": body.discountType,
        "discount_value": body.discountValue,
        "min_purchase": body.minPurchase,
        "max_discount": body.maxDiscount,
        "valid_from": body.validFrom,
        "valid_to": body.validTo,
        "usage_limit": body.usageLimit,
        "usage_count": 0,
        "categories": body.categories or [],
        "is_active": True,
        "created_at": _now_iso(),
    }
    coll.insert_one(doc)
    return _scrub(dict(doc))


@router.delete("/discounts/promo-codes/{code_id}")
async def delete_promo_code(code_id: str):
    coll = _coll("promo_codes")
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    if not coll.find_one({"$or": [{"code_id": code_id}, {"_id": code_id}]}):
        raise HTTPException(status_code=404, detail="Promo code not found")
    coll.update_one(
        {"$or": [{"code_id": code_id}, {"_id": code_id}]},
        {"$set": {"is_active": False, "updated_at": _now_iso()}},
    )
    return {"deactivated": True, "code_id": code_id}


# ============================================================================
# SYSTEM  (/admin/system/*)
# ============================================================================


@router.get("/system/status")
async def get_system_status():
    """Live health snapshot — DB connectivity + collection counts."""
    from database.connection import get_db

    status: Dict[str, Any] = {"api": "healthy", "checked_at": _now_iso()}
    try:
        db = get_db()
        connected = bool(db and db.is_connected)
        status["database"] = "connected" if connected else "disconnected"
        if connected:
            counts = {}
            for c in ("orders", "products", "customers", "users", "stores"):
                col = _coll(c)
                counts[c] = col.count_documents({}) if col is not None else 0
            status["collection_counts"] = counts
    except Exception as e:
        status["database"] = "error"
        status["error"] = str(e)
    return status


@router.get("/system/settings")
async def get_system_settings():
    coll = _coll("system_settings")
    defaults = {
        "round_off_enabled": True,
        "round_off_paise": 50,
        "auto_logout_minutes": 480,
        "low_stock_alert_enabled": True,
        "backup_reminder_days": 7,
    }
    if coll is not None:
        doc = coll.find_one({"_id": "system_settings"})
        if doc:
            doc.pop("_id", None)
            defaults.update(doc)
    return defaults


@router.put("/system/settings")
async def update_system_settings(settings: Dict[str, Any]):
    coll = _coll("system_settings")
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    settings = {k: v for k, v in settings.items() if k != "_id"}
    settings["updated_at"] = _now_iso()
    coll.update_one({"_id": "system_settings"}, {"$set": settings}, upsert=True)
    doc = coll.find_one({"_id": "system_settings"})
    return _scrub(doc) or {}


@router.get("/system/audit-logs")
async def get_system_audit_logs(
    userId: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    startDate: Optional[str] = Query(None),
    endDate: Optional[str] = Query(None),
):
    coll = _coll("audit_logs")
    if coll is None:
        return {"logs": [], "total": 0}
    q: Dict[str, Any] = {}
    if userId:
        q["user_id"] = userId
    if action:
        q["action"] = action
    if startDate or endDate:
        rng: Dict[str, Any] = {}
        if startDate:
            rng["$gte"] = startDate
        if endDate:
            rng["$lte"] = endDate
        q["created_at"] = rng
    docs = list(coll.find(q).sort("created_at", -1).limit(200))
    return {"logs": [_scrub(d) for d in docs if d], "total": len(docs)}


@router.get("/system/backups")
async def list_backups():
    """Backups are an infra concern (Railway/Mongo Atlas snapshots), not
    something the app performs. We surface any backup metadata recorded
    in the `backups` collection but never claim to take a live DB dump."""
    coll = _coll("backups")
    if coll is None:
        return {
            "backups": [],
            "note": "Backups are managed at the infrastructure layer.",
        }
    return {
        "backups": [
            _scrub(d) for d in coll.find({}).sort("created_at", -1).limit(50) if d
        ]
    }


@router.post("/system/backups", status_code=202)
async def create_backup():
    """Records a backup *request*. Actual snapshotting is done by the
    hosting provider; this just timestamps an operator-initiated request
    so it shows up in the list with a clear status."""
    coll = _coll("backups")
    backup_id = str(uuid.uuid4())
    rec = {
        "_id": backup_id,
        "backup_id": backup_id,
        "status": "requested",
        "requested_at": _now_iso(),
        "note": "Snapshot handled by hosting provider; this is a request marker.",
    }
    if coll is not None:
        coll.insert_one(rec)
    return _scrub(dict(rec))


# ============================================================================
# NOTE: backup restore/download + data export/import are intentionally NOT
# implemented as live operations — they require infra-level access. The
# frontend buttons should be hidden for now; if called, they return 501.
# ============================================================================


@router.post("/system/backups/{backup_id}/restore", status_code=501)
async def restore_backup(backup_id: str):
    raise HTTPException(
        status_code=501,
        detail="Restore is performed at the infrastructure layer, not via the app.",
    )


@router.get("/system/export/{export_type}", status_code=501)
async def export_data(export_type: str):
    raise HTTPException(
        status_code=501,
        detail="Bulk export not available via this endpoint yet. Use per-module CSV exports.",
    )
