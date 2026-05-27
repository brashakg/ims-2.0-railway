"""
IMS 2.0 - Customer Returns / Exchange / Credit-Note Router
==========================================================
Records customer-side returns, exchanges and store-credit notes against an
original sale. This is REVENUE-ADJACENT but it only RECORDS money movement -
no payment gateway is ever called. Refunds and collections are recorded as
amounts + a method; the actual settlement happens out-of-band at the till.

What each return_type does:
  - RETURN      -> record refund_amount = returned_value (to source / chosen method).
  - CREDIT_NOTE -> issue store credit for returned_value to the customer (ledger).
  - EXCHANGE    -> settle returned_value against replacement_items; record the
                   COLLECT / REFUND difference (REFUND may be issued as credit).

For every type, returned units in GOOD condition are put BACK into sellable
serialized stock (the `stock_units` collection - re-activate the original unit
or mint a fresh AVAILABLE one), idempotently + fail-soft. See restock_engine
for the resellable-vs-hold decision logic.

Conventions mirrored from the rest of the codebase:
  - get_current_user / require_roles for auth + RBAC.
  - Repository accessors from ..dependencies; fail-soft when DB is absent.
  - Store-credit reuses services.store_credit_ledger.make_entry + the same
    `credit_note_ledger` collection the customers router writes.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

from .auth import get_current_user, require_roles
from ..dependencies import (
    get_customer_repository,
    get_order_repository,
    get_product_repository,
    get_stock_repository,
)
from ..services import restock_engine
from ..services import returns_engine as engine
from ..services import store_credit_ledger as scl

logger = logging.getLogger(__name__)
router = APIRouter()

# Roles allowed to CREATE a return - mirrors the Returns nav item guard in
# frontend/src/components/shell/Rail.tsx. SUPERADMIN auto-passes.
_RETURN_ROLES = (
    "ADMIN",
    "STORE_MANAGER",
    "CASHIER",
    "SALES_CASHIER",
)

ReturnType = Literal["RETURN", "EXCHANGE", "CREDIT_NOTE"]
ItemCondition = Literal["GOOD", "OPENED", "DAMAGED"]


# ============================================================================
# SCHEMAS
# ============================================================================


class ReturnLine(BaseModel):
    order_item_id: Optional[str] = None
    product_id: Optional[str] = None
    product_name: str = ""
    sku: str = ""
    return_qty: float = Field(..., ge=0)
    unit_price: float = Field(..., ge=0)
    reason: Optional[str] = None
    condition: ItemCondition = "GOOD"
    # Whether this returned unit should go back into sellable stock. Defaults
    # True; only GOOD-condition units are ever restocked (see restock_engine).
    # The till can set this False to hold a GOOD-looking unit off the shelf.
    restock: bool = True
    notes: Optional[str] = None


class ReplacementLine(BaseModel):
    product_id: Optional[str] = None
    name: str = ""
    sku: str = ""
    quantity: float = Field(..., ge=0)
    unit_price: float = Field(..., ge=0)


class ReturnCreate(BaseModel):
    order_id: Optional[str] = None
    order_number: Optional[str] = None
    customer_id: Optional[str] = None
    store_id: Optional[str] = None
    return_type: ReturnType = "RETURN"
    items: List[ReturnLine] = Field(default_factory=list)
    replacement_items: List[ReplacementLine] = Field(default_factory=list)
    approval_note: Optional[str] = None
    refund_method: Optional[str] = None


# ============================================================================
# DB HELPERS (fail-soft - no DB must never 500)
# ============================================================================


def _get_db():
    """Raw MongoDB handle, or None when unavailable (mock / no-DB mode)."""
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and getattr(conn, "is_connected", False):
            return conn.db
    except Exception:  # noqa: BLE001
        pass
    return None


def _returns_coll():
    db = _get_db()
    if db is None:
        return None
    try:
        return db.get_collection("returns")
    except Exception:  # noqa: BLE001
        return None


def _ledger_coll():
    """Same collection the customers router uses for the credit-note ledger."""
    db = _get_db()
    if db is None:
        return None
    try:
        return db.get_collection("credit_note_ledger")
    except Exception:  # noqa: BLE001
        return None


def generate_return_id() -> str:
    """Short, human-friendly return id, e.g. RET-250523-AB12CD."""
    stamp = datetime.now().strftime("%y%m%d")
    return f"RET-{stamp}-{uuid.uuid4().hex[:6].upper()}"


def _resolve_order(create: ReturnCreate) -> Optional[Dict[str, Any]]:
    """Look up the original order by id or number. Fail-soft -> None."""
    repo = get_order_repository()
    if repo is None:
        return None
    try:
        if create.order_id:
            found = repo.find_by_id(create.order_id)
            if found:
                return found
        if create.order_number:
            return repo.find_by_order_number(create.order_number)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RETURNS] order lookup failed: %s", exc)
    return None


def _order_payment_method(order: Optional[Dict[str, Any]]) -> str:
    """Best-effort original payment method for defaulting a refund method."""
    if not order:
        return "SOURCE"
    for key in ("payment_method", "payment_mode", "paymentMode"):
        val = order.get(key)
        if val:
            return str(val)
    payments = order.get("payments") or []
    if isinstance(payments, list) and payments:
        first = payments[0] or {}
        val = first.get("method") or first.get("mode") or first.get("payment_method")
        if val:
            return str(val)
    return "SOURCE"


def _current_credit_balance(customer_id: str, customer_doc: Optional[dict]) -> float:
    """Ledger is authoritative once it has entries; otherwise bridge from the
    legacy customer.store_credit number (same rule as the customers router)."""
    coll = _ledger_coll()
    if coll is not None:
        try:
            entries = list(coll.find({"customer_id": customer_id}, {"_id": 0}))
            if entries:
                return scl.compute_balance(entries)
        except Exception:  # noqa: BLE001
            pass
    return float((customer_doc or {}).get("store_credit", 0) or 0)


def _issue_store_credit(
    customer_id: str,
    amount: float,
    reason: str,
    ref: str,
    current_user: dict,
) -> Optional[Dict[str, Any]]:
    """Append an ISSUED ledger entry + bump the customer's running balance.

    Reuses services.store_credit_ledger.make_entry and the same persistence
    shape as customers.py. Fully fail-soft: returns None (and never raises)
    when the DB is absent or anything goes wrong, so a return is still
    recorded even if the credit ledger write fails.
    """
    if not customer_id or amount <= 0:
        return None

    repo = get_customer_repository()
    customer_doc = None
    if repo is not None:
        try:
            customer_doc = repo.find_by_id(customer_id)
        except Exception:  # noqa: BLE001
            customer_doc = None

    balance = _current_credit_balance(customer_id, customer_doc)
    try:
        entry = scl.make_entry(
            customer_id=customer_id,
            entry_type=scl.ISSUED,
            amount=amount,
            current_balance=balance,
            reason=reason,
            ref=ref,
            store_id=current_user.get("active_store_id"),
            user_id=current_user.get("user_id"),
        )
    except ValueError as exc:
        logger.warning("[RETURNS] credit entry rejected: %s", exc)
        return None

    coll = _ledger_coll()
    if coll is not None:
        try:
            coll.insert_one(dict(entry))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RETURNS] credit ledger insert failed: %s", exc)
    # Keep the legacy customer.store_credit number in sync so the rest of the
    # app (POS redeem, customer card) sees the new balance.
    if repo is not None:
        try:
            repo.update(customer_id, {"store_credit": entry["balance_after"]})
        except Exception:  # noqa: BLE001
            pass
    entry.pop("_id", None)
    return entry


def _reactivate_original_unit(
    stock_repo: Any,
    product_id: str,
    store_id: Optional[str],
    order_id: Optional[str],
    used_ids: set,
) -> Optional[str]:
    """Find the original serialized unit sold for this product and flip it back
    to AVAILABLE. Returns its stock_id, or None when no candidate is found.

    Preference order (most-specific first):
      1. a unit for (product_id, store_id) tied to THIS order_id and not
         already AVAILABLE - i.e. the exact unit that was sold on this order;
      2. any non-AVAILABLE unit for (product_id, store_id).
    A unit already reactivated earlier in this same return (`used_ids`) is
    skipped so two returned units never collapse onto one stock row.
    """
    if stock_repo is None or not product_id:
        return None

    candidates: List[Dict[str, Any]] = []
    base = {"product_id": product_id}
    if store_id:
        base["store_id"] = store_id
    not_available = {"status": {"$ne": "AVAILABLE"}}

    try:
        if order_id:
            q = dict(base)
            q["order_id"] = order_id
            q.update(not_available)
            candidates = stock_repo.find_many(q) or []
        if not candidates:
            q = dict(base)
            q.update(not_available)
            candidates = stock_repo.find_many(q) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RETURNS] stock lookup failed: %s", exc)
        return None

    for unit in candidates:
        sid = unit.get("stock_id") or unit.get("_id")
        if not sid or sid in used_ids:
            continue
        try:
            ok = stock_repo.update(
                sid,
                {
                    "status": "AVAILABLE",
                    "returned_at": datetime.now().isoformat(),
                    "reserved_at": None,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RETURNS] stock reactivate failed: %s", exc)
            continue
        if ok:
            used_ids.add(sid)
            return str(sid)
    return None


def _restock_good_items(
    items: List[ReturnLine],
    store_id: Optional[str],
    return_id: str,
    order_id: Optional[str] = None,
    already_applied: bool = False,
) -> Dict[str, Any]:
    """Put GOOD-condition returned units BACK into sellable serialized stock.

    Real per-store on-hand is the serialized `stock` collection (one row per
    physical unit - see inventory.py + get_stock_repository), NOT the
    product-level `stock_quantity` aggregate. For each resellable unit we:
      * RE-ACTIVATE the original serialized unit (status -> AVAILABLE) if we can
        find the SOLD/RETURNED row that left when it was sold; OTHERWISE
      * MINT a fresh AVAILABLE unit {quantity:1, source_type:"RETURN",
        source_id:<return_id>}.

    Idempotent: pass already_applied=True (when the return doc was restocked
    before) and this is a no-op. Fully fail-soft: any stock-write failure is
    logged and leaves applied=False on the return so it can be retried, never
    breaking the return record. Damaged / opened / opted-out units are skipped
    and listed under `skipped`.

    Returns a dict with `restocked` (per-line summary for the return doc),
    `restock_stock_ids` (every stock row touched), `applied`, and `skipped`.
    """
    plan = restock_engine.plan_restock(
        [it.model_dump() for it in (items or [])],
        already_applied=already_applied,
    )

    result: Dict[str, Any] = {
        "restocked": [],
        "restock_stock_ids": [],
        "applied": False,
        "skipped": plan.get("skipped", []),
    }
    if plan.get("already_applied"):
        result["applied"] = True
        return result

    units = plan.get("units", [])
    if not units:
        # Nothing resellable -> trivially "applied" (no work left to retry).
        result["applied"] = True
        return result

    stock_repo = None
    try:
        stock_repo = get_stock_repository()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RETURNS] stock repo unavailable: %s", exc)

    # No DB / stock repo -> record intent only, leave applied=False to retry.
    if stock_repo is None:
        per_line: Dict[str, Dict[str, Any]] = {}
        for u in units:
            pid = u.get("product_id")
            row = per_line.setdefault(
                pid,
                {
                    "product_id": pid,
                    "sku": u.get("sku", ""),
                    "product_name": u.get("product_name", ""),
                    "quantity": 0,
                    "applied": False,
                },
            )
            row["quantity"] += 1
        result["restocked"] = list(per_line.values())
        result["applied"] = False
        return result

    # We have a stock repo - actually re-add each unit.
    used_ids: set = set()
    per_line_applied: Dict[str, Dict[str, Any]] = {}
    all_ok = True
    for u in units:
        pid = u.get("product_id")
        row = per_line_applied.setdefault(
            pid,
            {
                "product_id": pid,
                "sku": u.get("sku", ""),
                "product_name": u.get("product_name", ""),
                "quantity": 0,
                "reactivated": 0,
                "minted": 0,
                "applied": True,
            },
        )
        sid = _reactivate_original_unit(
            stock_repo, pid, store_id, order_id, used_ids
        )
        if sid:
            row["reactivated"] += 1
            row["quantity"] += 1
            result["restock_stock_ids"].append(sid)
            continue
        # Mint a fresh AVAILABLE serialized unit.
        try:
            created = stock_repo.create(
                {
                    "store_id": store_id,
                    "product_id": pid,
                    "quantity": 1,
                    "status": "AVAILABLE",
                    "source_type": "RETURN",
                    "source_id": return_id,
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RETURNS] stock mint failed: %s", exc)
            created = None
        if created:
            new_id = created.get("stock_id") or created.get("_id")
            row["minted"] += 1
            row["quantity"] += 1
            if new_id:
                result["restock_stock_ids"].append(str(new_id))
        else:
            all_ok = False
            row["applied"] = False

    result["restocked"] = list(per_line_applied.values())
    # Applied only if every unit landed somewhere; otherwise leave False so a
    # later retry can finish the job (the stock_ids already added are recorded).
    result["applied"] = all_ok
    return result


def _reason_summary(items: List[ReturnLine]) -> str:
    """Distinct reasons across the returned lines, comma-joined."""
    seen: List[str] = []
    for it in items or []:
        if it.return_qty > 0 and it.reason and it.reason not in seen:
            seen.append(it.reason)
    return ", ".join(seen)


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.post("", status_code=201)
@router.post("/", status_code=201)
async def create_return(
    body: ReturnCreate = Body(...),
    current_user: dict = Depends(require_roles(*_RETURN_ROLES)),
):
    """Create a customer return / exchange / credit-note.

    Money is RECORDED, never executed. Returns the new return_id plus the
    computed amounts for the chosen return_type.
    """
    active_lines = [it for it in body.items if it.return_qty > 0]
    if not active_lines:
        raise HTTPException(
            status_code=400, detail="Select at least one item to return"
        )

    store_id = body.store_id or current_user.get("active_store_id")

    # 1. Money math (pure engine; validates negatives).
    try:
        ret_value = engine.returned_value(
            [it.model_dump() for it in active_lines]
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    order = _resolve_order(body)
    customer_id = body.customer_id or (order or {}).get("customer_id")
    customer_name = (order or {}).get("customer_name") or (
        order or {}
    ).get("customerName") or ""

    return_id = generate_return_id()

    # 2. Type-specific recording.
    refund_amount: Optional[float] = None
    credit_amount: Optional[float] = None
    settlement: Optional[Dict[str, Any]] = None
    collect_amount: Optional[float] = None
    credit_entry: Optional[Dict[str, Any]] = None
    replacement_dump = [r.model_dump() for r in body.replacement_items]

    if body.return_type == "RETURN":
        refund_amount = ret_value
        refund_method = body.refund_method or _order_payment_method(order)

    elif body.return_type == "CREDIT_NOTE":
        credit_amount = ret_value
        refund_method = "STORE_CREDIT"
        credit_entry = _issue_store_credit(
            customer_id,
            ret_value,
            reason=f"Credit note for return {return_id}",
            ref=return_id,
            current_user=current_user,
        )

    else:  # EXCHANGE
        refund_method = body.refund_method or _order_payment_method(order)
        try:
            settlement = engine.exchange_settlement(ret_value, replacement_dump)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if settlement["direction"] == engine.COLLECT:
            collect_amount = settlement["difference"]
        elif settlement["direction"] == engine.REFUND:
            # Refund the difference as store credit (recorded, not executed).
            credit_amount = settlement["difference"]
            credit_entry = _issue_store_credit(
                customer_id,
                settlement["difference"],
                reason=f"Exchange refund for return {return_id}",
                ref=return_id,
                current_user=current_user,
            )

    # 3. Restock resellable (GOOD) units back into serialized stock (fail-soft).
    resolved_order_id = body.order_id or (order or {}).get("order_id")
    restock_result: Dict[str, Any] = {
        "restocked": [],
        "restock_stock_ids": [],
        "applied": False,
        "skipped": [],
    }
    try:
        restock_result = _restock_good_items(
            active_lines, store_id, return_id, order_id=resolved_order_id
        )
    except Exception as exc:  # noqa: BLE001
        # A stock-write failure must never break the return record - leave
        # applied=False so it can be retried later.
        logger.warning("[RETURNS] restock failed (recorded, not applied): %s", exc)
    restocked = restock_result.get("restocked", [])
    restock_applied = bool(restock_result.get("applied"))
    restock_stock_ids = restock_result.get("restock_stock_ids", [])

    # 4. Persist the return doc.
    now = datetime.now().isoformat()
    doc: Dict[str, Any] = {
        "return_id": return_id,
        "order_id": resolved_order_id,
        "order_number": body.order_number or (order or {}).get("order_number"),
        "customer_id": customer_id,
        "customer_name": customer_name,
        "store_id": store_id,
        "return_type": body.return_type,
        "items": [it.model_dump() for it in active_lines],
        "replacement_items": replacement_dump,
        "returned_value": ret_value,
        "refund_amount": refund_amount,
        "refund_method": refund_method,
        "credit_amount": credit_amount,
        "collect_amount": collect_amount,
        "settlement": settlement,
        "restocked": restocked,
        "restock_applied": restock_applied,
        "restock_stock_ids": restock_stock_ids,
        "credit_entry": credit_entry,
        "status": "COMPLETED",
        "reason_summary": _reason_summary(active_lines),
        "approval_note": body.approval_note or "",
        "created_by": current_user.get("user_id"),
        "created_by_name": current_user.get(
            "full_name", current_user.get("username", "")
        ),
        "created_at": now,
    }

    coll = _returns_coll()
    if coll is not None:
        try:
            coll.insert_one(dict(doc))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RETURNS] persist failed: %s", exc)

    # Audit log (optional, fail-soft).
    try:
        from ..dependencies import get_audit_repository

        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "RETURN_CREATED",
                    "entity_type": "return",
                    "entity_id": return_id,
                    "store_id": store_id,
                    "user_id": current_user.get("user_id"),
                    "details": {
                        "return_type": body.return_type,
                        "returned_value": ret_value,
                        "refund_amount": refund_amount,
                        "credit_amount": credit_amount,
                        "collect_amount": collect_amount,
                    },
                    "created_at": now,
                }
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RETURNS] audit log skipped: %s", exc)

    # 5. Response.
    message = {
        "RETURN": f"Refund of Rs {ret_value} recorded",
        "CREDIT_NOTE": f"Store credit of Rs {ret_value} issued",
        "EXCHANGE": "Exchange recorded",
    }[body.return_type]

    return {
        "return_id": return_id,
        "return_type": body.return_type,
        "returned_value": ret_value,
        "refund_amount": refund_amount,
        "refund_method": refund_method,
        "credit_amount": credit_amount,
        "collect_amount": collect_amount,
        "settlement": settlement,
        "restocked": restocked,
        "restock_applied": restock_applied,
        "restock_stock_ids": restock_stock_ids,
        "message": message,
    }


@router.get("")
@router.get("/")
async def list_returns(
    store_id: Optional[str] = Query(None),
    return_type: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
):
    """List returns, store-scoped, newest first.

    HQ roles (SUPERADMIN/ADMIN/AREA_MANAGER) may pass any store_id; lower
    roles are pinned to their active store.
    """
    coll = _returns_coll()
    if coll is None:
        return {"returns": [], "total": 0}

    roles = current_user.get("roles", []) or []
    is_hq = any(r in roles for r in ("SUPERADMIN", "ADMIN", "AREA_MANAGER"))
    effective_store = store_id if is_hq else current_user.get("active_store_id")

    query: Dict[str, Any] = {}
    if effective_store:
        query["store_id"] = effective_store
    if return_type:
        query["return_type"] = return_type

    try:
        total = coll.count_documents(query)
        cursor = (
            coll.find(query, {"_id": 0})
            .sort("created_at", -1)
            .skip(skip)
            .limit(limit)
        )
        returns = list(cursor)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RETURNS] list failed: %s", exc)
        return {"returns": [], "total": 0}

    return {"returns": returns, "total": total}


@router.get("/{return_id}")
async def get_return(
    return_id: str = Path(..., description="Return ID"),
    current_user: dict = Depends(get_current_user),
):
    """Get a single return by id."""
    coll = _returns_coll()
    if coll is None:
        raise HTTPException(status_code=404, detail="Return not found")
    try:
        doc = coll.find_one({"return_id": return_id}, {"_id": 0})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RETURNS] get failed: %s", exc)
        raise HTTPException(status_code=404, detail="Return not found")
    if not doc:
        raise HTTPException(status_code=404, detail="Return not found")
    return doc


@router.post("/{return_id}/restock")
async def retry_restock(
    return_id: str = Path(..., description="Return ID"),
    current_user: dict = Depends(require_roles(*_RETURN_ROLES)),
):
    """Re-run restock for a return whose units never made it back to stock.

    Idempotent: if the return was already restocked (restock_applied=True) this
    is a no-op. Otherwise it re-reads the saved return lines and re-activates /
    mints the serialized units, then persists restock_applied + the stock ids.
    Lets a transient DB failure during create() be retried without re-recording
    any money movement.
    """
    coll = _returns_coll()
    if coll is None:
        raise HTTPException(status_code=404, detail="Return not found")
    try:
        doc = coll.find_one({"return_id": return_id}, {"_id": 0})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RETURNS] restock retry lookup failed: %s", exc)
        raise HTTPException(status_code=404, detail="Return not found")
    if not doc:
        raise HTTPException(status_code=404, detail="Return not found")

    already = bool(doc.get("restock_applied"))
    if already:
        return {
            "return_id": return_id,
            "restock_applied": True,
            "restock_stock_ids": doc.get("restock_stock_ids", []),
            "message": "Already restocked",
        }

    lines = [ReturnLine(**it) for it in (doc.get("items") or [])]
    restock_result = _restock_good_items(
        lines,
        doc.get("store_id"),
        return_id,
        order_id=doc.get("order_id"),
        already_applied=False,
    )

    update = {
        "restocked": restock_result.get("restocked", []),
        "restock_applied": bool(restock_result.get("applied")),
        "restock_stock_ids": restock_result.get("restock_stock_ids", []),
    }
    try:
        coll.update_one({"return_id": return_id}, {"$set": update})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RETURNS] restock retry persist failed: %s", exc)

    return {
        "return_id": return_id,
        "restock_applied": update["restock_applied"],
        "restock_stock_ids": update["restock_stock_ids"],
        "message": (
            "Restock applied"
            if update["restock_applied"]
            else "Restock partial - retry again"
        ),
    }
