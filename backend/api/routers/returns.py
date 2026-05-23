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

For every type, returned units in GOOD condition are restocked (fail-soft).

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
)
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


def _restock_good_items(
    items: List[ReturnLine], store_id: Optional[str]
) -> List[Dict[str, Any]]:
    """RECORD which GOOD-condition returned units should go back on the shelf.

    Restock is intentionally recorded but NOT applied to live stock here. The
    authoritative per-store on-hand is the serialized `stock` collection (one
    row per physical unit - see inventory.py get_stock + POST /stock/add), NOT
    the product-level `stock_quantity` aggregate. Re-activating a sold unit's
    stock row (or minting a fresh AVAILABLE row) is a deliberate follow-up;
    writing a different field here would desync the reports from the real
    sellable stock. So we capture the intent on the return doc (applied=False)
    and never half-apply an inventory change. Damaged / opened units are not
    recorded.
    """
    restocked: List[Dict[str, Any]] = []
    good = [
        it
        for it in (items or [])
        if it.condition == "GOOD" and it.return_qty > 0 and it.product_id
    ]
    for it in good:
        qty = int(round(it.return_qty))
        if qty <= 0:
            continue
        restocked.append(
            {
                "product_id": it.product_id,
                "sku": it.sku,
                "product_name": it.product_name,
                "quantity": qty,
                "applied": False,
            }
        )
    return restocked


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

    # 3. Restock GOOD items (fail-soft).
    restocked = _restock_good_items(active_lines, store_id)

    # 4. Persist the return doc.
    now = datetime.now().isoformat()
    doc: Dict[str, Any] = {
        "return_id": return_id,
        "order_id": body.order_id or (order or {}).get("order_id"),
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
