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
                                  (filter by ?status=, ?date_from / ?date_to and
                                  ?search=; SERVER-SIDE PROJECTION -- the browser
                                  receives only the ~15 scalars the screen shows,
                                  never raw line items / tax tables / payments /
                                  addresses (OS-063)). Cross-store callers ALSO
                                  get the FAILED queue: recent shopify
                                  webhook_inbox order payloads with NO matching
                                  orders doc are merged in as map_status=FAILED
                                  (processed but never booked) or PENDING
                                  (received, not yet drained), each with an
                                  honest map_error -- previously those orders
                                  were invisible and the screen's FAILED banner /
                                  Re-map button were dead UI (OS-011).
  POST /remap/{shopify_order_id}  SUPERADMIN/ADMIN re-run the mapper for one order
                                  from its last persisted webhook_inbox payload
                                  (recovers an order that failed to map / needs a
                                  status re-sync). Writes a chained audit_logs row.
                                  The response carries an explicit ok / map_status
                                  verdict so a mapper 'skipped' result can never
                                  toast as a false success (OS-011).
  POST /{order_id}/clear-rx-hold  SUPERADMIN/ADMIN release the clinical Rx
                                  FLAG-AND-HOLD on one online order after the
                                  prescription has been captured/verified
                                  (OS-012: the hold was write-only -- nothing
                                  could ever see or release it). Audited.

ROLE GATE (router-level, in lock-step with rbac_policy.POLICY):
  * GET  list  -> ADMIN / SUPERADMIN / ACCOUNTANT (ACCOUNTANT reads the books).
  * POST remap / clear-rx-hold -> ADMIN / SUPERADMIN ONLY (they mutate an order).
  SUPERADMIN is auto-granted by require_roles, so it is not repeated in the tuples
  but IS listed in every POLICY row.

Everything is FAIL-SOFT: no DB -> the list returns an empty page (never a 500); a
remap with no stored payload is a 404; the mapper itself never raises. AUDIT
EVERYTHING: a remap writes a chained audit_logs row (fail-soft) so the owner has an
immutable record of every manual re-run (SYSTEM_INTENT: Audit Everything).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .auth import require_roles
from ..dependencies import user_store_scope

router = APIRouter()


def _parse_created_bound(raw: Optional[str], *, end: bool) -> Optional[datetime]:
    """Parse a ?date_from / ?date_to string into a naive-UTC datetime bound on
    `created_at` (online orders now persist created_at as a BSON datetime). A
    date-only value expands to the end of the day for an upper bound. Returns None
    when unparseable (the caller then omits the datetime branch)."""
    if not raw:
        return None
    txt = str(raw).strip()
    if not txt:
        return None
    try:
        dt = datetime.fromisoformat(txt.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.fromisoformat(txt[:10])
        except ValueError:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    if end and len(txt) <= 10:
        dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    return dt

# Reading the online order book is also for the ACCOUNTANT; mutating (remap /
# clear-rx-hold) is not.
_READ_ROLES = ("ADMIN", "ACCOUNTANT")
_REMAP_ROLES = ("ADMIN",)

# Default / max page size for the list (keep a bound so a huge book can't be pulled
# in one shot).
_DEFAULT_LIMIT = 100
_MAX_LIMIT = 500

# How many recent shopify webhook_inbox rows to scan for the FAILED queue. The
# collection is TTL-bounded to 30 days (webhooks.py), so this covers the entire
# actionable window at current volumes.
_INBOX_SCAN_LIMIT = 300

# Mapper result statuses that mean "the order IS (still) in the books" -- the ONLY
# statuses a remap may report as success. Anything else ('skipped', 'error', ...)
# is a failure the operator must see (OS-011: 'skipped' used to toast success).
_REMAP_OK_STATUSES = ("created", "duplicate", "replayed", "status_synced")

# Server-side projection for the list (OS-063): ship ONLY what the screen renders.
# `items` is fetched but immediately collapsed to items_count and stripped -- the
# browser never receives line items, per-line GST tables, payments, or addresses.
_LIST_PROJECTION: Dict[str, int] = {
    "order_id": 1,
    "order_number": 1,
    "shopify_order_id": 1,
    "shopify_order_name": 1,
    "channel": 1,
    "source": 1,
    "store_id": 1,
    "customer_id": 1,
    "customer_name": 1,
    "customer_phone": 1,
    "customer_email": 1,
    "grand_total": 1,
    "currency": 1,
    "status": 1,
    "payment_status": 1,
    "fulfillment_status": 1,
    "shopify_fulfillment_id": 1,
    "shopify_fulfillment_pushed_at": 1,
    "rx_pending": 1,
    "rx_hold_reasons": 1,
    "rx_hold_reason": 1,
    "fulfillment_hold": 1,
    "rx_hold_cleared": 1,
    "rx_hold_cleared_at": 1,
    "invoice_number": 1,
    "placed_at": 1,
    "created_at": 1,
    "updated_at": 1,
    "items": 1,  # collapsed to items_count server-side, then stripped
}

# Bulk / PII fields defensively stripped from every list row even when a mock DB
# ignores the projection (the seeded MockDatabase returns whole docs).
_LIST_STRIP_FIELDS = (
    "items",
    "payments",
    "tax_summary",
    "tax_totals",
    "delivery_address",
    "shipping_address",
    "billing_address",
    "status_history",
)

# Fields the ?search= regex covers -- the same identifiers the screen renders.
_SEARCH_FIELDS = (
    "order_number",
    "shopify_order_id",
    "shopify_order_name",
    "customer_name",
    "customer_phone",
    "customer_email",
)


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


def _slim_list_row(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Collapse a fetched order doc to the list-row shape (OS-063): _id gone,
    `items` -> items_count, bulk/PII fields stripped (belt-and-braces for mock
    DBs that ignore the find() projection), map_status stamped explicitly."""
    doc = _clean(doc)
    items = doc.get("items")
    doc["items_count"] = len(items) if isinstance(items, list) else 0
    for f in _LIST_STRIP_FIELDS:
        doc.pop(f, None)
    # A doc in the orders collection IS in the books -- say so explicitly rather
    # than making the frontend infer it from the presence of an id.
    doc.setdefault("map_status", "MAPPED")
    return doc


def _search_clause(search: str) -> Dict[str, Any]:
    """Case-insensitive server-side search over the identifiers the screen
    renders. The needle is regex-escaped so user input is always literal."""
    import re as _re

    rx = {"$regex": _re.escape(search.strip()), "$options": "i"}
    return {"$or": [{f: rx} for f in _SEARCH_FIELDS]}


def _row_matches_search(row: Dict[str, Any], search: str) -> bool:
    """In-Python mirror of _search_clause for the synthetic FAILED/PENDING rows
    (which never touch Mongo)."""
    needle = search.strip().lower()
    if not needle:
        return True
    for f in _SEARCH_FIELDS:
        val = row.get(f)
        if val is not None and needle in str(val).lower():
            return True
    return False


def _unbooked_webhook_rows(db, *, search: Optional[str]) -> List[Dict[str, Any]]:
    """The FAILED queue (OS-011): recent shopify ORDER webhooks with NO matching
    orders doc. A Shopify order whose mapping fails never produces an orders doc
    (the mapper fail-softs to {'status':'skipped'}), so before this the list
    could never show a FAILED row -- the screen's red banner and Re-map button
    were dead UI while unbooked orders sat invisibly in webhook_inbox.

    Returns synthetic rows: map_status=FAILED when the inbox row was processed
    (drained, but no order exists -> the mapping genuinely failed) or PENDING
    (received, not yet drained by NEXUS), each with an honest map_error. Newest
    delivery per Shopify order id wins. Fail-soft: any error -> [] (the booked
    list still renders)."""
    try:
        inbox = db.get_collection("webhook_inbox")
        orders_coll = db.get_collection("orders")
        if inbox is None or orders_coll is None:
            return []
        rows = list(
            inbox.find({"vendor": "shopify"})
            .sort("received_at", -1)
            .limit(_INBOX_SCAN_LIMIT)
        )
    except Exception:  # noqa: BLE001 - the FAILED queue must never break the list
        return []

    # Newest-first scan: first row seen per order id is the latest delivery.
    candidates: Dict[str, Any] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        payload = row.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        headers = row.get("headers")
        headers = headers if isinstance(headers, dict) else {}
        topic = str(headers.get("x-shopify-topic") or "").lower()
        # Only ORDER-shaped deliveries belong in this queue -- refund/fulfillment
        # webhooks reference a parent order but are not order payloads.
        if not (topic.startswith("orders/") or isinstance(payload.get("line_items"), list)):
            continue
        sid = str(payload.get("id") or "").strip()
        if not sid or sid in candidates:
            continue
        candidates[sid] = (row, payload)
    if not candidates:
        return []

    try:
        booked = {
            str(d.get("shopify_order_id"))
            for d in orders_coll.find(
                {"shopify_order_id": {"$in": sorted(candidates.keys())}},
                {"shopify_order_id": 1},
            )
            if isinstance(d, dict)
        }
    except Exception:  # noqa: BLE001
        return []

    out: List[Dict[str, Any]] = []
    for sid, (row, payload) in candidates.items():
        if sid in booked:
            continue
        processed = bool(row.get("processed"))
        # Honest skip reason, best-effort: an explicit handler/skip note on the
        # inbox row wins; else derive the mapper's own no-line-items skip; else a
        # plain-English generic that still tells the operator what to do.
        reason = str(row.get("handler_error") or row.get("skipped_reason") or "").strip()
        if not reason and not isinstance(payload.get("line_items"), list):
            reason = "The webhook payload carried no line items, so no order could be booked."
        if not reason:
            reason = (
                "The webhook was processed but no IMS order was booked (for "
                "example an unmappable product or customer). Fix the cause, then re-map."
            )
        cust = payload.get("customer")
        cust = cust if isinstance(cust, dict) else {}
        name = (
            f"{str(cust.get('first_name') or '').strip()} "
            f"{str(cust.get('last_name') or '').strip()}"
        ).strip() or None
        try:
            total = (
                float(payload.get("total_price"))
                if payload.get("total_price") is not None
                else None
            )
        except (TypeError, ValueError):
            total = None
        line_items = payload.get("line_items")
        out.append(
            {
                "shopify_order_id": sid,
                "shopify_order_name": payload.get("name"),
                "channel": "ONLINE",
                "map_status": "FAILED" if processed else "PENDING",
                "map_error": reason if processed else None,
                "customer_name": name,
                "customer_email": payload.get("email") or cust.get("email") or None,
                "grand_total": total,
                "currency": payload.get("currency") or "INR",
                "items_count": len(line_items) if isinstance(line_items, list) else 0,
                "placed_at": payload.get("created_at"),
                "created_at": row.get("received_at"),
            }
        )
    if search:
        out = [r for r in out if _row_matches_search(r, search)]
    return out


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
    search: Optional[str] = Query(
        None,
        description=(
            "Case-insensitive server-side search over order number / Shopify "
            "ref / customer name, phone, email (regex-escaped literal)."
        ),
    ),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(require_roles(*_READ_ROLES)),
) -> Dict[str, Any]:
    """List IMS orders that originated from the online channel (channel='ONLINE'),
    newest first. Optional filters: ?status=, ?date_from=, ?date_to= (created_at),
    ?search=. Rows are server-side projected (no line items / tax tables /
    payments / addresses reach the browser -- OS-063).

    Cross-store callers additionally receive the FAILED queue (`failed` +
    `failed_count`): recent Shopify order webhooks with no matching orders doc,
    surfaced as map_status=FAILED/PENDING synthetic rows so unbooked orders are
    visible and re-mappable (OS-011). `failed` rows accompany the FIRST page only
    (offset=0, no ?status filter) so pagination never duplicates them; store-scoped
    callers don't receive them (unbooked webhook payloads carry no store stamp, so
    they are admin/HQ material -- fail closed).

    Fail-soft: no DB -> an empty page (never a 500). Returns a bounded page plus the
    total count so the UI can paginate.
    """
    db = _get_db()
    if db is None:
        return {
            "orders": [],
            "failed": [],
            "failed_count": 0,
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

    # Compose the multi-branch clauses ($or for the dual-type date range, $or for
    # the search fields) under one $and so they can never clobber each other.
    and_clauses: List[Dict[str, Any]] = []

    # Dual-type created_at range. Online orders now persist created_at as a naive-UTC
    # BSON DATETIME (shopify_ingest), but LEGACY online orders (pre-fix / not yet
    # backfilled) wrote ISO STRINGS. Mongo type-brackets a Date range away from a
    # string field, so build BOTH a datetime range and the raw string range and $or
    # them -- otherwise the date filter silently drops whichever type it isn't.
    created_dt: Dict[str, Any] = {}
    created_str: Dict[str, Any] = {}
    lo = _parse_created_bound(date_from, end=False)
    hi = _parse_created_bound(date_to, end=True)
    if date_from:
        created_str["$gte"] = date_from
        if lo is not None:
            created_dt["$gte"] = lo
    if date_to:
        created_str["$lte"] = date_to
        if hi is not None:
            created_dt["$lte"] = hi
    if created_str:
        branches: List[Dict[str, Any]] = [{"created_at": created_str}]
        if created_dt:
            branches.append({"created_at": created_dt})
        and_clauses.append({"$or": branches})

    search_txt = (search or "").strip()
    if search_txt:
        and_clauses.append(_search_clause(search_txt))
    if and_clauses:
        query["$and"] = and_clauses

    # The FAILED queue (OS-011) -- cross-store callers, first page, no status
    # filter (synthetic rows have no IMS status to filter on).
    failed_rows: List[Dict[str, Any]] = []
    if is_cross and not status:
        failed_rows = _unbooked_webhook_rows(db, search=search_txt or None)
    failed_count = sum(1 for r in failed_rows if r.get("map_status") == "FAILED")

    try:
        coll = db.get_collection("orders")
        if coll is None:
            return {
                "orders": [],
                "failed": [],
                "failed_count": 0,
                "total": 0,
                "limit": limit,
                "offset": offset,
                "db_connected": True,
            }
        total = int(coll.count_documents(query))
        cursor = (
            coll.find(query, dict(_LIST_PROJECTION))
            .sort("created_at", -1)
            .skip(offset)
            .limit(limit)
        )
        orders: List[Dict[str, Any]] = [_slim_list_row(d) for d in cursor]
    except Exception:  # noqa: BLE001 - reads degrade, never 500
        return {
            "orders": [],
            "failed": [],
            "failed_count": 0,
            "total": 0,
            "limit": limit,
            "offset": offset,
            "db_connected": True,
        }

    return {
        "orders": orders,
        # First page only, so Load-more pagination never duplicates these rows.
        "failed": failed_rows if offset == 0 else [],
        "failed_count": failed_count,
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

    # Explicit verdict (OS-011): the mapper fail-softs to {'status':'skipped',
    # 'reason':...} -- which carries NO 'error' key and NO order id, so the old
    # frontend inference read it as a success and toasted 'Order re-mapped into
    # the books' for an order that was NOT booked. Say plainly whether the order
    # is in the books so the UI can never invent a false success.
    status = str((result or {}).get("status") or "")
    ok = status in _REMAP_OK_STATUSES
    return {
        "shopify_order_id": shopify_order_id,
        "ok": ok,
        "map_status": "MAPPED" if ok else "FAILED",
        "map_error": (
            None
            if ok
            else (result or {}).get("error") or (result or {}).get("reason") or status or "unknown"
        ),
        "result": result,
    }


# ---------------------------------------------------------------------------
# POST /{order_id}/clear-rx-hold -- release the clinical Rx FLAG-AND-HOLD
# ---------------------------------------------------------------------------


@router.post("/{order_id}/clear-rx-hold")
async def clear_rx_hold(
    order_id: str,
    current_user: dict = Depends(require_roles(*_REMAP_ROLES)),
) -> Dict[str, Any]:
    """Release the Rx FLAG-AND-HOLD on ONE online order (OS-012).

    Ingest stamps a spectacle-lens order missing a valid prescription with
    rx_pending + fulfillment_hold (flag-and-hold, owner decision 2026-06-30), but
    the hold was WRITE-ONLY: no endpoint could release it, so a held order could
    only be resolved by editing the database. This endpoint releases the hold
    AFTER staff have captured/verified the prescription. ADMIN/SUPERADMIN only
    (matches the remap gate; a wider OPTOMETRIST flow can ride the Tasks
    worklist). The cleared markers stay on the doc as an audit trail, plus a
    chained audit_logs row.

    409 when the order carries no active hold; 404 when it doesn't exist (or is
    not an online order)."""
    db = _get_db()
    if db is None:
        raise HTTPException(
            status_code=503, detail="Online Store orders unavailable (no DB)"
        )

    try:
        coll = db.get_collection("orders")
        order = (
            coll.find_one({"order_id": order_id, "channel": "ONLINE"})
            if coll is not None
            else None
        )
    except Exception:  # noqa: BLE001
        order = None
    if not order:
        raise HTTPException(status_code=404, detail="Online order not found")

    if not (order.get("rx_pending") or order.get("fulfillment_hold")):
        raise HTTPException(
            status_code=409, detail="This order has no active Rx hold to clear."
        )

    now_dt = datetime.now(timezone.utc).replace(tzinfo=None)
    update = {
        "rx_pending": False,
        "fulfillment_hold": False,
        "rx_hold_cleared": True,
        "rx_hold_cleared_by": current_user.get("user_id"),
        "rx_hold_cleared_at": now_dt.isoformat(),
        "updated_at": now_dt,
    }
    try:
        coll.update_one({"order_id": order_id}, {"$set": update})
    except Exception:  # noqa: BLE001 - surface the failure, don't fake success
        raise HTTPException(
            status_code=503, detail="Could not update the order (database error)"
        )

    _write_rx_hold_audit(order, current_user)
    return {
        "order_id": order_id,
        "rx_pending": False,
        "fulfillment_hold": False,
        "rx_hold_cleared": True,
    }


def _write_rx_hold_audit(order: Dict[str, Any], current_user: dict) -> None:
    """Chained audit row for an Rx-hold release. Fail-soft: audit errors are
    swallowed so they can never block the release (mirrors _write_remap_audit)."""
    try:
        from ..dependencies import get_audit_repository

        audit = get_audit_repository()
        if audit is None:
            return
        audit.create(
            {
                "action": "ONLINE_ORDER_RX_HOLD_CLEAR",
                "entity_type": "order",
                "entity_id": order.get("order_id"),
                "store_id": order.get("store_id"),
                "user_id": current_user.get("user_id"),
                "severity": "INFO",
                "details": {
                    "order_number": order.get("order_number"),
                    "shopify_order_id": order.get("shopify_order_id"),
                    "prior_rx_hold_reasons": order.get("rx_hold_reasons"),
                    "prior_rx_hold_reason": order.get("rx_hold_reason"),
                },
            }
        )
    except Exception:  # noqa: BLE001 -- audit must never break the release
        pass


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
