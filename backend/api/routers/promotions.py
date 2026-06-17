"""
IMS 2.0 - Promotions Router (F11 advanced promotions + F12 cross-category bundles)
==================================================================================

The CRUD + preview + audit surface for the dark-gated promo engine. The pure
money math lives in ``api/services/promo_engine.py`` (PR #677 + this build); this
router owns:

  * promo-rule CRUD (create / list / get / update / deactivate),
  * ``POST /promotions/evaluate`` -- a PURE preview (no side effects) the POS
    can call at the cart-review step to show "what would apply",
  * ``apply_promos_for_order`` -- an INTERNAL function called only by
    ``orders.create_order`` (behind ``PROMO_ENGINE_ENABLED``) that atomically
    increments each fired promo's ``uses_count`` (guarded find_one_and_update,
    same pattern as vouchers.redeem_voucher_atomic) and writes an immutable
    ``promo_applications`` audit row.

REVENUE SAFETY
--------------
The live POS integration is gated by the ``PROMO_ENGINE_ENABLED`` env flag,
which defaults OFF. When OFF, orders.create_order never calls this module and
order totals are byte-identical to the pre-promo path. The CRUD + evaluate
endpoints are always available (zero POS risk) so rules can be authored and
previewed before the flag is flipped per the locked rollout plan.

Collections:
  promo_rules         - the rule definitions (this router's CRUD target)
  promo_applications  - append-only audit of what fired on which order + margin

Mounted at /api/v1/promotions (see api/main.py).
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from pymongo import ReturnDocument

from .auth import get_current_user, require_roles
from ..services import promo_engine

logger = logging.getLogger(__name__)
router = APIRouter()

# Roles that may CREATE / EDIT / DEACTIVATE promo rules. ADMIN + SUPERADMIN are
# the owners; CATALOG_MANAGER may author rules (pricing decision visibility,
# F11/F12 RBAC). AREA_MANAGER / STORE_MANAGER manage their own store rules.
_PROMO_WRITE_ROLES = (
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
    "CATALOG_MANAGER",
)
# Roles that may READ promo rules / the evaluate preview. Adds the POS + report
# audiences (the POS evaluate preview is read-only).
_PROMO_READ_ROLES = (
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
    "CATALOG_MANAGER",
    "ACCOUNTANT",
    "SALES_CASHIER",
    "SALES_STAFF",
    "CASHIER",
)

# Server-side hard ceiling on any promo's discount percentage (F12 owner Q:
# "max bundle discount %" -> default 30%, DECISIONS sec.9 promo ceiling). An
# admin cannot author a rule above this; the per-line category/luxury caps in
# pricing_caps still bound the actual discount further at apply time.
PROMO_PCT_CEILING = float(os.getenv("PROMO_PCT_CEILING", "30") or "30")

_PROMO_TYPES = {"THRESHOLD", "BOGO", "COMBO", "SECOND_PAIR", "PERCENT"}
_REWARD_TYPES = {"PERCENT", "PERCENT_OFF", "FIXED_OFF"}


# ============================================================================
# DB + helpers
# ============================================================================
def _get_db():
    """Live pymongo Database or None (house pattern; fail-soft when DB-less)."""
    from database.connection import get_db

    return get_db().db


def _now_iso() -> str:
    return datetime.now().isoformat()


def _strip(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if doc:
        doc.pop("_id", None)
    return doc


def _enforce_store_scope(rule: Dict[str, Any], user: dict) -> None:
    """A rule carrying store_ids is restricted to those stores for non-HQ roles."""
    store_ids = rule.get("store_ids") or []
    if not store_ids:
        return
    roles = set(user.get("roles", []) or [])
    if roles & {"SUPERADMIN", "ADMIN", "AREA_MANAGER"}:
        return
    from ..dependencies import validate_store_access

    # Store manager must be able to access at least one of the rule's stores.
    for sid in store_ids:
        try:
            validate_store_access(sid, user)
            return
        except HTTPException:
            continue
    raise HTTPException(
        status_code=403, detail="You cannot manage a promo rule for these stores."
    )


def _audit(db, promo_id: str, action: str, user: dict,
           detail: Optional[Dict[str, Any]] = None) -> None:
    """Append an immutable promo_audit row (fail-soft)."""
    if db is None:
        return
    try:
        db.get_collection("promo_audit").insert_one(
            {
                "audit_id": f"PRA-{uuid.uuid4().hex[:10].upper()}",
                "promo_id": promo_id,
                "action": action,
                "actor": user.get("user_id", "unknown"),
                "detail": detail or {},
                "at": _now_iso(),
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("promo audit write failed (%s): %s", action, exc)


# ============================================================================
# Pydantic models
# ============================================================================
class ComboGroup(BaseModel):
    category: Optional[str] = None
    item_type: Optional[str] = None
    brand: Optional[str] = None


class PromoRuleCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    promo_type: str = Field(..., description="THRESHOLD|BOGO|COMBO|SECOND_PAIR|PERCENT")
    description: Optional[str] = None
    # reward
    reward_value: float = Field(0.0, ge=0, le=100, description="percent off (0-100)")
    max_discount_amount: Optional[float] = Field(None, ge=0)
    # stacking (EXCLUSIVE by default per DECISIONS sec.3 #11)
    stackable: bool = False
    priority: int = 0
    # triggers
    min_cart_value: Optional[float] = Field(None, ge=0)
    min_qty: Optional[int] = Field(None, ge=1)
    trigger_categories: Optional[List[str]] = None
    product_ids: Optional[List[str]] = None
    # BOGO
    buy_quantity: Optional[int] = Field(None, ge=1)
    get_quantity: Optional[int] = Field(None, ge=1)
    # COMBO cross-category bundle groups (all must be present in the cart)
    combo_groups: Optional[List[ComboGroup]] = None
    # CRM gating
    customer_tiers: Optional[List[str]] = None
    first_purchase_only: bool = False
    # scope + schedule
    store_ids: Optional[List[str]] = None
    active: bool = True
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None


class PromoRuleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=120)
    description: Optional[str] = None
    reward_value: Optional[float] = Field(None, ge=0, le=100)
    max_discount_amount: Optional[float] = Field(None, ge=0)
    stackable: Optional[bool] = None
    priority: Optional[int] = None
    min_cart_value: Optional[float] = Field(None, ge=0)
    min_qty: Optional[int] = Field(None, ge=1)
    trigger_categories: Optional[List[str]] = None
    product_ids: Optional[List[str]] = None
    buy_quantity: Optional[int] = Field(None, ge=1)
    get_quantity: Optional[int] = Field(None, ge=1)
    combo_groups: Optional[List[ComboGroup]] = None
    customer_tiers: Optional[List[str]] = None
    first_purchase_only: Optional[bool] = None
    store_ids: Optional[List[str]] = None
    active: Optional[bool] = None
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None


class EvaluateItem(BaseModel):
    product_id: Optional[str] = None
    product_name: Optional[str] = None
    brand: Optional[str] = None
    item_type: Optional[str] = None
    discount_category: Optional[str] = None
    category: Optional[str] = None
    quantity: int = 1
    unit_price: float = 0.0
    cost_at_sale: Optional[float] = None


class EvaluateRequest(BaseModel):
    items: List[EvaluateItem]
    customer_id: Optional[str] = None
    store_id: Optional[str] = None


# ============================================================================
# Rule validation + adapters
# ============================================================================
def _validate_rule(promo_type: str, reward_value: float,
                   data: Dict[str, Any]) -> None:
    pt = (promo_type or "").strip().upper()
    if pt not in _PROMO_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"promo_type must be one of {sorted(_PROMO_TYPES)}",
        )
    if reward_value is not None and reward_value > PROMO_PCT_CEILING + 1e-9:
        # SECOND_PAIR / BOGO can legitimately be 50/100 (half/free) -- those are
        # not a blanket cart discount, so the ceiling applies only to the
        # broad-cart kinds where a high % silently craters margin.
        if pt in ("THRESHOLD", "COMBO", "PERCENT"):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"reward_value {reward_value}% exceeds the {PROMO_PCT_CEILING}% "
                    f"promo ceiling for {pt} promos. Lower it or split into a "
                    f"targeted bundle."
                ),
            )
    if pt == "THRESHOLD" and not data.get("min_cart_value"):
        raise HTTPException(
            status_code=422, detail="THRESHOLD promo requires min_cart_value"
        )
    if pt == "BOGO":
        if not data.get("buy_quantity"):
            raise HTTPException(
                status_code=422, detail="BOGO promo requires buy_quantity"
            )
    if pt == "COMBO":
        groups = data.get("combo_groups") or []
        if len(groups) < 2:
            raise HTTPException(
                status_code=422,
                detail="COMBO (cross-category) promo requires >= 2 combo_groups",
            )


def _rule_active_now(rule: Dict[str, Any], today: Optional[date] = None) -> bool:
    """Is the rule active + within its schedule window today?"""
    if not rule.get("active", True):
        return False
    today = today or date.today()
    vf = rule.get("valid_from")
    vu = rule.get("valid_until")
    try:
        if vf and date.fromisoformat(str(vf)[:10]) > today:
            return False
        if vu and date.fromisoformat(str(vu)[:10]) < today:
            return False
    except (ValueError, TypeError):
        pass  # unparseable schedule -> treat as no constraint
    # Total uses cap (best-effort; the atomic guard at apply time is authoritative).
    cap = rule.get("max_uses_total")
    if cap is not None and int(rule.get("uses_count") or 0) >= int(cap):
        return False
    return True


def _rule_in_store(rule: Dict[str, Any], store_id: Optional[str]) -> bool:
    store_ids = rule.get("store_ids") or []
    if not store_ids:
        return True  # all stores
    return store_id in store_ids if store_id else True


def get_active_rules_for_store(db, store_id: Optional[str]) -> List[Dict[str, Any]]:
    """Read ACTIVE, in-window, store-matching promo rules. The single source the
    evaluate preview AND the order-create apply path both use, so the rule set is
    identical between preview and application."""
    if db is None:
        return []
    try:
        rules = list(db.get_collection("promo_rules").find({"active": True}))
    except Exception as exc:  # noqa: BLE001
        logger.warning("promo rule read failed: %s", exc)
        return []
    out = []
    for r in rules:
        r.pop("_id", None)
        if _rule_active_now(r) and _rule_in_store(r, store_id):
            out.append(r)
    return out


# ============================================================================
# CRUD endpoints
# ============================================================================
@router.get("")
@router.get("/")
async def list_promo_rules(
    store_id: Optional[str] = Query(None),
    active_only: bool = Query(False),
    limit: int = Query(200, le=1000),
    current_user: dict = Depends(require_roles(*_PROMO_READ_ROLES)),
):
    """List promo rules (newest first). active_only filters to currently-active."""
    db = _get_db()
    if db is None:
        return {"rules": [], "total": 0}
    query: Dict[str, Any] = {}
    if active_only:
        query["active"] = True
    docs = list(
        db.get_collection("promo_rules")
        .find(query)
        .sort("created_at", -1)
        .limit(limit)
    )
    for d in docs:
        d.pop("_id", None)
    if store_id:
        docs = [d for d in docs if _rule_in_store(d, store_id)]
    return {"rules": docs, "total": len(docs)}


@router.post("", status_code=201)
@router.post("/", status_code=201)
async def create_promo_rule(
    req: PromoRuleCreate,
    current_user: dict = Depends(require_roles(*_PROMO_WRITE_ROLES)),
):
    """Create a promo rule. ADMIN/SUPERADMIN/CATALOG_MANAGER/managers; store
    managers are scoped to their stores. The reward % is bounded by the server
    promo ceiling here, and again by the per-line category/luxury caps at apply
    time (the engine clamps, never breaches)."""
    data = req.model_dump()
    _validate_rule(req.promo_type, req.reward_value, data)
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    if req.store_ids:
        _enforce_store_scope({"store_ids": req.store_ids}, current_user)

    promo_id = f"PR-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
    doc = {
        "promo_id": promo_id,
        "name": req.name,
        "promo_type": req.promo_type.strip().upper(),
        "description": req.description,
        "reward_value": req.reward_value,
        "max_discount_amount": req.max_discount_amount,
        "stackable": req.stackable,
        "priority": req.priority,
        "min_cart_value": req.min_cart_value,
        "min_qty": req.min_qty,
        "trigger_categories": req.trigger_categories,
        "product_ids": req.product_ids,
        "buy_quantity": req.buy_quantity,
        "get_quantity": req.get_quantity,
        "combo_groups": [g.model_dump() for g in (req.combo_groups or [])] or None,
        "customer_tiers": req.customer_tiers,
        "first_purchase_only": req.first_purchase_only,
        "store_ids": req.store_ids,
        "active": req.active,
        "valid_from": req.valid_from,
        "valid_until": req.valid_until,
        "uses_count": 0,
        "created_by": current_user.get("user_id", "unknown"),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    db.get_collection("promo_rules").insert_one(dict(doc))
    _audit(db, promo_id, "CREATE_PROMO_RULE", current_user,
           {"name": req.name, "type": doc["promo_type"]})
    return {"message": "Promo rule created", "rule": _strip(dict(doc))}


@router.get("/{promo_id}")
async def get_promo_rule(
    promo_id: str,
    current_user: dict = Depends(require_roles(*_PROMO_READ_ROLES)),
):
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    doc = db.get_collection("promo_rules").find_one({"promo_id": promo_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Promo rule not found")
    return _strip(dict(doc))


@router.put("/{promo_id}")
async def update_promo_rule(
    promo_id: str,
    req: PromoRuleUpdate,
    current_user: dict = Depends(require_roles(*_PROMO_WRITE_ROLES)),
):
    """Update a promo rule. uses_count can never be set via the API. When the
    rule has already been used, mutating its money fields is blocked -- only a
    deactivate (active=False) is allowed -- so a live, partially-used promo's
    economics can't be rewritten under existing applications (F11 business rule)."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    doc = db.get_collection("promo_rules").find_one({"promo_id": promo_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Promo rule not found")
    doc.pop("_id", None)
    if doc.get("store_ids"):
        _enforce_store_scope(doc, current_user)

    updates = req.model_dump(exclude_unset=True, exclude_none=True)
    updates.pop("uses_count", None)  # never client-settable
    if "combo_groups" in updates and updates["combo_groups"] is not None:
        updates["combo_groups"] = [
            g if isinstance(g, dict) else g.model_dump()
            for g in updates["combo_groups"]
        ]
    if not updates:
        return _strip(dict(doc))

    used = int(doc.get("uses_count") or 0) > 0
    if used:
        # Only deactivation (active False) + cosmetic fields are allowed once used.
        money_fields = {
            "reward_value", "max_discount_amount", "stackable", "priority",
            "min_cart_value", "min_qty", "trigger_categories", "product_ids",
            "buy_quantity", "get_quantity", "combo_groups", "store_ids",
            "customer_tiers", "first_purchase_only",
        }
        if money_fields & set(updates.keys()):
            raise HTTPException(
                status_code=409,
                detail=(
                    "This promo has already been applied to orders; its economics "
                    "cannot be edited. Deactivate it and create a new rule instead."
                ),
            )
    # Re-validate if promo-type-relevant fields changed.
    merged = {**doc, **updates}
    _validate_rule(merged.get("promo_type", "PERCENT"),
                   merged.get("reward_value", 0.0), merged)

    updates["updated_at"] = _now_iso()
    db.get_collection("promo_rules").update_one(
        {"promo_id": promo_id}, {"$set": updates}
    )
    _audit(db, promo_id, "UPDATE_PROMO_RULE", current_user,
           {"fields": list(updates.keys())})
    fresh = db.get_collection("promo_rules").find_one({"promo_id": promo_id})
    return {"message": "Promo rule updated", "rule": _strip(fresh)}


@router.delete("/{promo_id}")
async def deactivate_promo_rule(
    promo_id: str,
    current_user: dict = Depends(require_roles(*_PROMO_WRITE_ROLES)),
):
    """Deactivate a promo rule (soft -- never hard-deleted so promo_applications
    keep referential integrity for the Offer Tally)."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    doc = db.get_collection("promo_rules").find_one({"promo_id": promo_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Promo rule not found")
    doc.pop("_id", None)
    if doc.get("store_ids"):
        _enforce_store_scope(doc, current_user)
    db.get_collection("promo_rules").update_one(
        {"promo_id": promo_id},
        {"$set": {"active": False, "updated_at": _now_iso()}},
    )
    _audit(db, promo_id, "DEACTIVATE_PROMO_RULE", current_user,
           {"name": doc.get("name")})
    return {"message": "Promo rule deactivated", "promo_id": promo_id}


# ============================================================================
# Pure preview (no side effects) -- POS cart-review hook
# ============================================================================
@router.post("/evaluate")
async def evaluate_cart_preview(
    req: EvaluateRequest,
    current_user: dict = Depends(require_roles(*_PROMO_READ_ROLES)),
):
    """PURE evaluation: given a cart, return the promos that WOULD apply +
    projected discount + margin impact. NO side effects (no uses_count $inc, no
    promo_applications write). Safe for the POS to call live as the cart changes.

    Note: this preview is gated to the same dark flag posture as create_order --
    it always RUNS the engine (so authors can preview rules before go-live), but
    it changes nothing, so it carries zero revenue risk regardless of the flag.
    """
    db = _get_db()
    store_id = req.store_id or current_user.get("active_store_id")
    rules = get_active_rules_for_store(db, store_id)
    cart = {"items": [it.model_dump() for it in req.items]}
    customer = None
    if db is not None and req.customer_id:
        try:
            customer = db.get_collection("customers").find_one(
                {"customer_id": req.customer_id}
            )
        except Exception:  # noqa: BLE001
            customer = None
    evaluation = promo_engine.evaluate_promos(cart, customer, None, rules)
    margin = promo_engine.estimate_margin_impact(cart, evaluation)
    return {
        "flag_enabled": promo_engine_enabled(),
        "evaluation": evaluation,
        "margin_impact": margin,
    }


# ============================================================================
# Internal apply path (called ONLY by orders.create_order, behind the flag)
# ============================================================================
def promo_engine_enabled() -> bool:
    """The dark gate. Default OFF -> orders.create_order behaves EXACTLY as
    today. Flip via the PROMO_ENGINE_ENABLED env var (true/1/yes/on)."""
    return (os.getenv("PROMO_ENGINE_ENABLED", "") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _empty_eval() -> Dict[str, Any]:
    return {
        "applied": False,
        "total_discount": 0.0,
        "applied_promos": [],
        "per_line_discount": {},
        "evaluation": None,
    }


def evaluate_for_order(
    db,
    *,
    store_id: Optional[str],
    customer_id: Optional[str],
    items: List[Dict[str, Any]],
    customer: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """PURE evaluate for the order-create path (NO DB writes). Reads the active
    rules, runs the cap-clamping engine, and returns:
        {"applied": bool, "total_discount": float, "applied_promos": [...],
         "per_line_discount": {line_id: rupees}, "evaluation": <raw engine dict>}

    The raw engine dict is carried through so commit_promo_application can write
    the audit row + margin without re-reading rules. Fail-soft: ANY error returns
    the empty (no-promo) result so a promo error can NEVER block the sale.
    """
    try:
        if db is None:
            return _empty_eval()
        rules = get_active_rules_for_store(db, store_id)
        if not rules:
            return _empty_eval()
        cart = {"items": items}
        evaluation = promo_engine.evaluate_promos(cart, customer, None, rules)
        if not evaluation.get("applied") or evaluation.get("total_discount", 0) <= 0:
            return _empty_eval()

        rules_by_id = {r.get("promo_id"): r for r in rules}
        names = evaluation.get("names") or {}
        breakdown = evaluation.get("breakdown") or {}
        applied_promos = []
        for pid in evaluation.get("fired") or []:
            rule = rules_by_id.get(pid) or {}
            applied_promos.append({
                "promo_id": pid,
                "promo_name": names.get(pid) or rule.get("name") or pid,
                "promo_type": rule.get("promo_type"),
                "discount_given": round(float(breakdown.get(pid, 0.0)), 2),
                "stackable": bool(rule.get("stackable")),
            })
        return {
            "applied": True,
            "total_discount": round(float(evaluation.get("total_discount", 0.0)), 2),
            "applied_promos": applied_promos,
            "per_line_discount": evaluation.get("per_line_discount") or {},
            "evaluation": evaluation,
        }
    except Exception as exc:  # noqa: BLE001 - never block a sale on a promo error
        logger.warning("[PROMO] evaluate_for_order failed (fail-soft): %s", exc)
        return _empty_eval()


def commit_promo_application(
    db,
    *,
    order_id: str,
    order_number: str,
    store_id: Optional[str],
    customer_id: Optional[str],
    cashier_id: Optional[str],
    items: List[Dict[str, Any]],
    evaluation: Dict[str, Any],
) -> None:
    """Commit a promo application AFTER the order persisted: atomically $inc each
    fired promo's uses_count (guarded find_one_and_update, so two concurrent POS
    terminals cannot overshoot max_uses_total) and write the immutable
    promo_applications audit row with the margin estimate.

    `evaluation` is the raw engine dict from evaluate_for_order. Fully fail-soft:
    any error logs + returns; the order is already saved (with applied_promos
    stamped), so the audit + uses-count are best-effort and never block the sale.

    Conservative on a uses-cap race: if the atomic guard fails for a promo
    (exhausted concurrently), that promo is simply NOT counted; the order keeps
    the discount it was already billed (we never claw back a completed sale --
    the audit row records what actually happened).
    """
    try:
        if db is None or not evaluation or not evaluation.get("applied"):
            return
        coll = db.get_collection("promo_rules")
        fired_ids = list(evaluation.get("fired") or [])
        names = evaluation.get("names") or {}
        breakdown = evaluation.get("breakdown") or {}
        counted: List[Dict[str, Any]] = []
        for pid in fired_ids:
            rule = coll.find_one({"promo_id": pid}) or {}
            cap = rule.get("max_uses_total")
            flt: Dict[str, Any] = {"promo_id": pid, "active": True}
            if cap is not None:
                flt["uses_count"] = {"$lt": int(cap)}
            try:
                updated = coll.find_one_and_update(
                    flt,
                    {"$inc": {"uses_count": 1},
                     "$set": {"updated_at": _now_iso()}},
                    return_document=ReturnDocument.AFTER,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[PROMO] uses_count guard failed for %s: %s", pid, exc)
                updated = None
            if updated is None:
                logger.info("[PROMO] %s exhausted at commit -> uses not counted", pid)
            counted.append({
                "promo_id": pid,
                "promo_name": names.get(pid) or rule.get("name") or pid,
                "promo_type": rule.get("promo_type"),
                "discount_given": round(float(breakdown.get(pid, 0.0)), 2),
                "stackable": bool(rule.get("stackable")),
                "uses_counted": updated is not None,
            })

        cart = {"items": items}
        margin = promo_engine.estimate_margin_impact(cart, evaluation)
        db.get_collection("promo_applications").insert_one({
            "promo_application_id": f"PA-{uuid.uuid4().hex[:12].upper()}",
            "order_id": order_id,
            "order_number": order_number,
            "store_id": store_id,
            "customer_id": customer_id,
            "cashier_id": cashier_id,
            "applied_at": _now_iso(),
            "applied_promos": counted,
            "total_discount_given": round(
                float(evaluation.get("total_discount", 0.0)), 2
            ),
            "raw_total_discount": round(
                float(evaluation.get("raw_total_discount", 0.0)), 2
            ),
            "per_line_discount": evaluation.get("per_line_discount") or {},
            "estimated_cogs": margin.get("estimated_cogs"),
            "net_margin_after_promo": margin.get("net_margin_after_promo"),
            "cogs_is_estimated": margin.get("cogs_is_estimated"),
            "channel": "POS",
        })
    except Exception as exc:  # noqa: BLE001 - audit is best-effort, never blocks
        logger.warning("[PROMO] commit_promo_application failed (fail-soft): %s", exc)
