"""
IMS 2.0 - Online Store : ORDERS Router  (BVI Phase 3b -- online sales into IMS books)
=====================================================================================
The read + recovery control surface over the canonical IMS orders that the Phase-3b
mapper (api/services/online_order_mapper.py) creates from Shopify orders. Online
sales flow into the SAME `orders` collection as POS -- tagged channel='ONLINE' with
a GST tax invoice -- so Finance / P&L already count them. This router lets the
operator SEE those online orders and RE-RUN the mapper for a stuck one.

Mounted at /api/v1/online-store/orders:
  GET  /                          list IMS orders where channel='ONLINE'
                                  (filter by ?status= and ?date_from / ?date_to)
  POST /remap/{shopify_order_id}  SUPERADMIN/ADMIN re-run the mapper for one order
                                  from its last persisted webhook_inbox payload
                                  (recovers an order that failed to map / needs a
                                  status re-sync). Writes a chained audit_logs row.

ROLE GATE (router-level, in lock-step with rbac_policy.POLICY):
  * GET  list  -> ADMIN / SUPERADMIN / ACCOUNTANT (ACCOUNTANT reads the books).
  * POST remap -> ADMIN / SUPERADMIN ONLY (it mutates / re-creates an order).
  SUPERADMIN is auto-granted by require_roles, so it is not repeated in the tuples
  but IS listed in every POLICY row.

Everything is FAIL-SOFT: no DB -> the list returns an empty page (never a 500); a
remap with no stored payload is a 404; the mapper itself never raises. AUDIT
EVERYTHING: a remap writes a chained audit_logs row (fail-soft) so the owner has an
immutable record of every manual re-run (SYSTEM_INTENT: Audit Everything).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .auth import require_roles
from ..dependencies import user_store_scope

router = APIRouter()

# Reading the online order book is also for the ACCOUNTANT; mutating (remap) is not.
_READ_ROLES = ("ADMIN", "ACCOUNTANT")
_REMAP_ROLES = ("ADMIN",)

# Default / max page size for the list (keep a bound so a huge book can't be pulled
# in one shot).
_DEFAULT_LIMIT = 100
_MAX_LIMIT = 500


# ---------------------------------------------------------------------------
# DB helpers (fail-soft; mirror routers/online_store_push.py)
# ---------------------------------------------------------------------------


def _get_db():
    """Underlying DB object (real pymongo Database or seeded MockDatabase) when
    connected, else None."""
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and getattr(conn, "is_connected", False):
            return conn.db
    except Exception:  # noqa: BLE001
        pass
    return None


def _clean(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Strip the Mongo _id so the doc is JSON-serialisable."""
    if isinstance(doc, dict):
        doc.pop("_id", None)
    return doc


# ---------------------------------------------------------------------------
# GET / -- list online orders
# ---------------------------------------------------------------------------


@router.get("")
@router.get("/")
async def list_online_orders(
    status: Optional[str] = Query(
        None, description="Filter by IMS order status (e.g. CONFIRMED, CANCELLED)"
    ),
    date_from: Optional[str] = Query(
        None, description="ISO date/datetime lower bound on created_at"
    ),
    date_to: Optional[str] = Query(
        None, description="ISO date/datetime upper bound on created_at"
    ),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(require_roles(*_READ_ROLES)),
) -> Dict[str, Any]:
    """List IMS orders that originated from the online channel (channel='ONLINE'),
    newest first. Optional filters: ?status=, ?date_from=, ?date_to= (created_at).

    Fail-soft: no DB -> an empty page (never a 500). Returns a bounded page plus the
    total count so the UI can paginate.
    """
    db = _get_db()
    if db is None:
        return {
            "orders": [],
            "total": 0,
            "limit": limit,
            "offset": offset,
            "db_connected": False,
        }

    query: Dict[str, Any] = {"channel": "ONLINE"}

    # Store scope (IDOR fix): SUPERADMIN/ADMIN are cross-store and see every
    # store's online orders; a store-scoped ACCOUNTANT (or any non-HQ role) must
    # only see the online orders of the store(s) they actually manage -- without
    # this filter the role gate alone let them read ALL stores' online orders
    # (customer PII + revenue). Bound the query to the caller's store_ids.
    is_cross, allowed_stores = user_store_scope(current_user)
    if not is_cross:
        # An empty store set means a store-scoped caller with no resolvable
        # store: {$in: []} matches nothing, so they see an empty book (fail
        # closed) rather than leaking the all-store list.
        query["store_id"] = {"$in": sorted(allowed_stores)}

    if status:
        query["status"] = status
    created: Dict[str, Any] = {}
    if date_from:
        created["$gte"] = date_from
    if date_to:
        created["$lte"] = date_to
    if created:
        query["created_at"] = created

    try:
        coll = db.get_collection("orders")
        if coll is None:
            return {
                "orders": [],
                "total": 0,
                "limit": limit,
                "offset": offset,
                "db_connected": True,
            }
        total = int(coll.count_documents(query))
        cursor = coll.find(query).sort("created_at", -1).skip(offset).limit(limit)
        orders: List[Dict[str, Any]] = [_clean(d) for d in cursor]
    except Exception:  # noqa: BLE001 - reads degrade, never 500
        return {
            "orders": [],
            "total": 0,
            "limit": limit,
            "offset": offset,
            "db_connected": True,
        }

    return {
        "orders": orders,
        "total": total,
        "limit": limit,
        "offset": offset,
        "db_connected": True,
    }


# ---------------------------------------------------------------------------
# POST /remap/{shopify_order_id} -- re-run the mapper for one order
# ---------------------------------------------------------------------------


@router.post("/remap/{shopify_order_id}")
async def remap_online_order(
    shopify_order_id: str,
    current_user: dict = Depends(require_roles(*_REMAP_ROLES)),
) -> Dict[str, Any]:
    """Re-run the Shopify->IMS mapper for ONE order from its last persisted
    `webhook_inbox` payload. Recovers an order whose first mapping failed (or needs
    a status re-sync). Idempotent: the mapper's order-id guard means a re-run never
    creates a 2nd order -- it returns 'duplicate' + syncs the status.

    404 when no webhook_inbox payload is on file for this Shopify order id (nothing
    to replay). Writes a chained audit_logs row either way (fail-soft).
    """
    db = _get_db()
    if db is None:
        raise HTTPException(
            status_code=503, detail="Online Store orders unavailable (no DB)"
        )

    payload, webhook_id, topic = _load_last_shopify_payload(db, shopify_order_id)
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail=f"No webhook payload on file for Shopify order {shopify_order_id}",
        )

    try:
        from ..services.online_order_mapper import map_shopify_order

        result = map_shopify_order(payload, db, webhook_id=webhook_id, topic=topic)
    except Exception as exc:  # noqa: BLE001 - the mapper is fail-soft; belt-and-braces
        result = {"status": "error", "error": str(exc)}

    _write_remap_audit(shopify_order_id, result, current_user)
    return {"shopify_order_id": shopify_order_id, "result": result}


def _load_last_shopify_payload(db, shopify_order_id: str):
    """Find the most-recent webhook_inbox row whose payload is for this Shopify
    order id; return (payload, webhook_id, topic) or (None, None, None).

    Shopify's order id can land in webhook_inbox as a number or string, so match
    both. We scan the (TTL-bounded, recent) shopify rows newest-first and pick the
    first whose payload id matches -- portable across real Mongo + the in-memory
    mock (neither needs a numeric/string-coercing query)."""
    try:
        coll = db.get_collection("webhook_inbox")
        if coll is None:
            return None, None, None
        rows = list(coll.find({"vendor": "shopify"}).sort("received_at", -1).limit(200))
    except Exception:  # noqa: BLE001
        return None, None, None

    target = str(shopify_order_id).strip()
    for row in rows:
        payload = row.get("payload") if isinstance(row, dict) else None
        if not isinstance(payload, dict):
            continue
        pid = str(payload.get("id") or payload.get("order_id") or "").strip()
        if pid and pid == target:
            headers = row.get("headers") or {}
            webhook_id = (
                headers.get("x-shopify-webhook-id")
                if isinstance(headers, dict)
                else None
            )
            topic = (
                headers.get("x-shopify-topic") if isinstance(headers, dict) else None
            ) or "orders/create"
            return payload, webhook_id, topic
    return None, None, None


def _write_remap_audit(
    shopify_order_id: str, result: Dict[str, Any], current_user: dict
) -> None:
    """Write a chained audit row for a manual remap. Fail-soft: any audit error is
    swallowed so it can never block the remap (mirrors online_store_push._write_audit).
    """
    try:
        from ..dependencies import get_audit_repository

        audit = get_audit_repository()
        if audit is None:
            return
        status = (result or {}).get("status")
        ok = status in ("created", "duplicate", "replayed", "status_synced")
        audit.create(
            {
                "action": "ONLINE_ORDER_REMAP",
                "entity_type": "order",
                "entity_id": (result or {}).get("order_id") or shopify_order_id,
                "user_id": current_user.get("user_id"),
                "severity": "INFO" if ok else "WARNING",
                "details": {
                    "shopify_order_id": shopify_order_id,
                    "status": status,
                    "ims_order_id": (result or {}).get("order_id"),
                    "invoice_number": (result or {}).get("invoice_number"),
                    "customer_id": (result or {}).get("customer_id"),
                    "store_id": (result or {}).get("store_id"),
                    "status_synced": (result or {}).get("status_synced"),
                    "error": (result or {}).get("error")
                    or (result or {}).get("reason"),
                },
            }
        )
    except Exception:  # noqa: BLE001 -- audit must never break the remap
        pass
