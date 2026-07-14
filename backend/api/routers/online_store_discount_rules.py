"""
IMS 2.0 - Online Store : Discount-Rule Router  (rebuild of BVI DiscountRule; DARK)
==================================================================================
Owner-editable CRUD for ``ecom_discount_rules`` -- the automatic ONLINE storefront
discount rules the online discount engine reads (api/services/
online_discount_engine.py). Rebuilds BVI's Postgres ``DiscountRule`` admin surface
inside IMS.

SCOPE = ONLINE / STOREFRONT ONLY. A rule sets what the WEBSITE shows; it NEVER
changes in-store POS pricing or the in-store discount caps. On any rule change the
router fires a FAIL-SOFT bulk recompute (``recompute_all`` scoped to the rule's
category) so the catalog's stored online prices catch up -- still DARK (the prices
only reach Shopify behind the existing Phase-5 write-gates).

Mounted at /api/v1/online-store/discount-rules. ROLE GATE (router-level +
rbac_policy.POLICY, kept in lock-step): SUPERADMIN / ADMIN / CATALOG_MANAGER
(SUPERADMIN auto-granted by require_roles). SALES_STAFF etc. are 403.

Routes:
  GET    /                list (filter category / brand / active)
  POST   /                create (unique category+brand+sub_brand -> 409 on dup)
  POST   /recompute       bulk-recompute online prices (optionally category-scoped)
  GET    /{rule_id}       fetch one
  PUT    /{rule_id}       update (only provided fields patched)
  DELETE /{rule_id}       delete

FAIL-SOFT: no DB -> reads return empty, writes 503. Recompute never raises into the
CRUD path. No emojis (Windows cp1252).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import require_roles
from ..services import online_discount_engine as engine
from ..services.product_master import resolve_category

router = APIRouter()

# Roles allowed into the discount-rule surface. SUPERADMIN is auto-granted by
# require_roles; it is listed in the POLICY rows. Keep in lock-step with
# rbac_policy.POLICY for every route below. (DESIGN_MANAGER is intentionally
# EXCLUDED -- pricing is not a design-queue concern.)
_RULE_ROLES = ("ADMIN", "CATALOG_MANAGER")

RULES_COLLECTION = engine.RULES_COLLECTION


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# DB helpers (fail-soft; mirror routers/online_store_collections.py)
# ---------------------------------------------------------------------------


def _get_db():
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and getattr(conn, "is_connected", False):
            return conn.db
    except Exception:  # noqa: BLE001
        pass
    return None


def _coll(db):
    return db[RULES_COLLECTION]


def _clean(doc: Optional[Dict]) -> Optional[Dict]:
    if isinstance(doc, dict):
        doc.pop("_id", None)
    return doc


def _norm_key(category: str, brand: Optional[str], sub_brand: Optional[str]) -> Dict[str, str]:
    """The natural-key filter used for the duplicate check + idempotent identity
    (category upper, brand/sub_brand lower) -- matches the migration script."""
    return {
        "category": (category or "").strip().upper(),
        "brand": (brand or "").strip().lower(),
        "sub_brand": (sub_brand or "").strip().lower(),
    }


# ---------------------------------------------------------------------------
# Pydantic payloads
# ---------------------------------------------------------------------------


class RuleCreate(BaseModel):
    category: str = Field(..., description="Product category (IMS canonical or an alias)")
    brand: Optional[str] = Field(None, description="Narrow to a brand (optional)")
    sub_brand: Optional[str] = Field(None, description="Narrow to a sub-brand (optional)")
    discount_percentage: float = Field(..., ge=0, le=100, description="% off MRP")
    active: bool = Field(True)
    priority: int = Field(0, description="Higher wins when two rules tie on specificity")


class RuleUpdate(BaseModel):
    """All fields optional -- only provided keys are patched."""

    category: Optional[str] = None
    brand: Optional[str] = None
    sub_brand: Optional[str] = None
    discount_percentage: Optional[float] = Field(None, ge=0, le=100)
    active: Optional[bool] = None
    priority: Optional[int] = None


class RecomputeRequest(BaseModel):
    category: Optional[str] = Field(
        None, description="Limit the recompute to one category (optional; else all)"
    )


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_category(category: str) -> str:
    canonical = resolve_category(category)
    if canonical is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown product category '{category}'.",
        )
    return canonical


def _trigger_recompute(db, category: Optional[str]) -> Dict[str, Any]:
    """Fire a FAIL-SOFT bulk recompute of stored online prices after a rule change.
    Scoped to the affected category (the minimal correct set) so a single edit need
    not sweep the whole catalog. Never raises into the CRUD path."""
    try:
        rule_filter = {"category": category} if category else None
        return engine.recompute_all(db, rule_filter)
    except Exception as exc:  # noqa: BLE001 -- recompute must never fail a rule edit
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.get("")
async def list_rules(
    category: Optional[str] = Query(None),
    brand: Optional[str] = Query(None),
    active: Optional[bool] = Query(None),
    current_user: dict = Depends(require_roles(*_RULE_ROLES)),
) -> Dict:
    """List discount rules (filtered, fail-soft). No DB -> empty list, never 500."""
    db = _get_db()
    if db is None:
        return {"rules": [], "count": 0, "db_connected": False}
    query: Dict[str, Any] = {}
    if category:
        query["category"] = _validate_category(category)
    if brand:
        query["brand"] = {"$regex": f"^{brand.strip()}$", "$options": "i"}
    if active is not None:
        query["active"] = active
    try:
        rows = [_clean(r) for r in _coll(db).find(query)]
    except Exception:  # noqa: BLE001
        rows = []
    # Most specific + highest priority first (matches the engine's winner order).
    rows.sort(
        key=lambda r: (
            bool((r or {}).get("sub_brand")),
            bool((r or {}).get("brand")),
            float((r or {}).get("priority") or 0),
        ),
        reverse=True,
    )
    return {"rules": rows, "count": len(rows), "db_connected": True}


@router.post("", status_code=201)
async def create_rule(
    payload: RuleCreate,
    current_user: dict = Depends(require_roles(*_RULE_ROLES)),
) -> Dict:
    """Create a discount rule (PUSH-DARK -- stored in IMS only). The
    (category, brand, sub_brand) triple must be unique -> a duplicate is a 409."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Discount-rule store unavailable")

    category = _validate_category(payload.category)
    brand = (payload.brand or "").strip() or None
    sub_brand = (payload.sub_brand or "").strip() or None

    key = _norm_key(category, brand, sub_brand)
    try:
        if _coll(db).find_one(key) is not None:
            raise HTTPException(
                status_code=409,
                detail="A rule for this category/brand/sub-brand already exists.",
            )
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 -- read blip: fall through to the insert
        pass

    rule_id = f"rule_{uuid.uuid4().hex[:12]}"
    doc: Dict[str, Any] = {
        "rule_id": rule_id,
        "id": rule_id,
        # Store category UPPER-canonical + brand/sub_brand normalised for the
        # natural key; the engine matches case-insensitively regardless.
        "category": key["category"],
        "brand": key["brand"] or None,
        "sub_brand": key["sub_brand"] or None,
        "discount_percentage": float(payload.discount_percentage),
        "active": bool(payload.active),
        "priority": int(payload.priority),
        "source": "ims_manual",
        "created_by": current_user.get("user_id"),
        "created_at": _now(),
        "updated_at": _now(),
    }
    try:
        _coll(db).insert_one(dict(doc))
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ == "DuplicateKeyError":
            raise HTTPException(
                status_code=409,
                detail="A rule for this category/brand/sub-brand already exists.",
            ) from exc
        raise HTTPException(status_code=500, detail="Failed to create rule") from exc

    recompute = _trigger_recompute(db, category)
    return {"rule": _clean(doc), "recompute": recompute}


@router.post("/recompute")
async def recompute_rules(
    payload: RecomputeRequest,
    current_user: dict = Depends(require_roles(*_RULE_ROLES)),
) -> Dict:
    """Manually re-apply the rules to stored catalog online prices (fail-soft).
    Optionally scoped to one category; else the whole catalog. Still DARK -- prices
    only reach Shopify behind the Phase-5 push gates."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Discount-rule store unavailable")
    category = _validate_category(payload.category) if payload.category else None
    return {"recompute": _trigger_recompute(db, category)}


@router.get("/{rule_id}")
async def get_rule(
    rule_id: str,
    current_user: dict = Depends(require_roles(*_RULE_ROLES)),
) -> Dict:
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Discount-rule store unavailable")
    doc = _coll(db).find_one({"rule_id": rule_id})
    if doc is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"rule": _clean(doc)}


@router.put("/{rule_id}")
async def update_rule(
    rule_id: str,
    payload: RuleUpdate,
    current_user: dict = Depends(require_roles(*_RULE_ROLES)),
) -> Dict:
    """Patch a rule; only provided fields change. A change to category/brand/
    sub_brand is re-validated + re-checked for a duplicate. Fires a scoped
    recompute for the affected category(ies)."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Discount-rule store unavailable")
    existing = _coll(db).find_one({"rule_id": rule_id})
    if existing is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    existing = _clean(existing)

    data = payload.model_dump(exclude_none=True)
    if not data:
        return {"rule": existing, "recompute": {"ok": True, "products": 0}}

    old_category = existing.get("category")
    new_category = old_category
    updates: Dict[str, Any] = {}
    if "category" in data:
        new_category = _validate_category(data["category"])
        updates["category"] = new_category
    if "brand" in data:
        updates["brand"] = (data["brand"] or "").strip().lower() or None
    if "sub_brand" in data:
        updates["sub_brand"] = (data["sub_brand"] or "").strip().lower() or None
    if "discount_percentage" in data:
        updates["discount_percentage"] = float(data["discount_percentage"])
    if "active" in data:
        updates["active"] = bool(data["active"])
    if "priority" in data:
        updates["priority"] = int(data["priority"])

    # Re-check the natural key if any identity field changed.
    if any(k in updates for k in ("category", "brand", "sub_brand")):
        merged = {**existing, **updates}
        key = _norm_key(merged.get("category"), merged.get("brand"), merged.get("sub_brand"))
        dup = _coll(db).find_one({**key, "rule_id": {"$ne": rule_id}})
        if dup is not None:
            raise HTTPException(
                status_code=409,
                detail="Another rule for this category/brand/sub-brand already exists.",
            )
        # Keep the stored category UPPER-canonical for the natural key.
        updates["category"] = key["category"]

    updates["updated_at"] = _now()
    try:
        _coll(db).update_one({"rule_id": rule_id}, {"$set": updates})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="Failed to update rule") from exc

    doc = _clean(_coll(db).find_one({"rule_id": rule_id}))
    # Recompute both the old and (if changed) the new category so a moved rule
    # cleans up prices on both sides.
    recompute = _trigger_recompute(db, new_category)
    if old_category and old_category != new_category:
        _trigger_recompute(db, old_category)
    return {"rule": doc, "recompute": recompute}


@router.delete("/{rule_id}")
async def delete_rule(
    rule_id: str,
    current_user: dict = Depends(require_roles(*_RULE_ROLES)),
) -> Dict:
    """Delete a rule and recompute the affected category's online prices (so the
    products the rule was discounting revert to MRP / any broader rule)."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Discount-rule store unavailable")
    existing = _clean(_coll(db).find_one({"rule_id": rule_id}))
    if existing is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    try:
        _coll(db).delete_one({"rule_id": rule_id})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="Failed to delete rule") from exc
    recompute = _trigger_recompute(db, existing.get("category"))
    return {"deleted": True, "rule_id": rule_id, "recompute": recompute}
