"""Order list, pending-delivery, unpaid, overdue, search, sales summary and
status counts.

Moved verbatim out of the 6,649-line api/routers/orders.py (Wave 5 package
split): no path, method, dependency, status code, response_model, default,
rounding or validation was changed.
"""

from datetime import date
from fastapi import Depends, Query
from typing import Optional
from ..auth import get_current_user
from ...dependencies import (
    get_customer_repository,
    get_order_repository,
    validate_store_access,
)
from ._shared import (
    OrderStatus,
    _stamp_status_actor_names,
    order_to_frontend,
    router,
)

# ============================================================================
# ENDPOINTS
# ============================================================================


@router.get("")
async def list_orders(
    store_id: Optional[str] = Query(None),
    status: Optional[OrderStatus] = Query(None),
    customer_id: Optional[str] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    current_user: dict = Depends(get_current_user),
):
    """List orders with filters"""
    repo = get_order_repository()
    active_store = validate_store_access(store_id, current_user)

    # 30-DAY BROWSE HORIZON (owner ruling 2026-09-01). Everyone except ADMIN /
    # SUPERADMIN sees only the last 30 days when BROWSING. Asking for ONE
    # customer is a named lookup, not browsing, so `?customer_id=` lifts the
    # window entirely - staff need the whole history of the person they are
    # serving, and none of the business's.
    #
    # The clamp raises `from_date`; it never lowers it. Without that,
    # `?from_date=2020-01-01` walks straight past the window and the rule is
    # decorative.
    from ...services.data_horizon import horizon_start

    _horizon = horizon_start(current_user, customer_scoped=bool(customer_id))
    if _horizon is not None:
        _floor = _horizon.date()
        from_date = max(from_date, _floor) if from_date else _floor

    if repo is not None:
        if customer_id:
            orders = repo.find_by_customer(customer_id, limit=limit)
            # Store-scope: a store-level role must not enumerate a customer's
            # orders from OTHER stores via ?customer_id (cross-store IDOR).
            # Cross-store roles (ADMIN/AREA_MANAGER/SUPERADMIN) are unaffected.
            from ...dependencies import filter_docs_by_store

            orders = filter_docs_by_store(orders, current_user)
        elif active_store:
            orders = repo.find_by_store(
                active_store,
                from_date=from_date,
                to_date=to_date,
                status=status.value if status else None,
            )
        else:
            filter_dict = {}
            if status:
                filter_dict["status"] = status.value
            # This branch takes no from_date, so the horizon goes on the query.
            if _horizon is not None:
                filter_dict["created_at"] = {"$gte": _horizon}
            orders = repo.find_many(filter_dict, skip=skip, limit=limit)

        # Convert to frontend format (camelCase)
        from ...utils.pagination import paginate

        _stamp_status_actor_names(orders)
        orders_formatted = [order_to_frontend(o) for o in orders]
        page = (skip // limit) + 1 if limit > 0 else 1
        result = paginate(orders_formatted, page=page, page_size=limit)
        result["orders"] = result["data"]  # backward compat
        return result

    return {
        "orders": [],
        "total": 0,
        "data": [],
        "pagination": {"total": 0, "page": 1, "page_size": limit, "total_pages": 0},
    }


# NOTE: Specific routes MUST come before /{order_id} to avoid being matched as order_id
@router.get("/pending/delivery")
async def get_pending_deliveries(
    store_id: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="Order number, customer name or phone"),
    current_user: dict = Depends(get_current_user),
):
    """The delivery counter's queue: orders waiting to be collected.

    SEARCH (owner 2026-09-02: "let users search through customer name and phone
    number too"). The counter could previously only find a job by its number.
    ``?q=`` searches the SAME queue through ``repo.search_orders`` -- the exact
    matcher GET /orders/search uses over order_number / customer_name /
    customer_phone -- and keeps only the rows still awaiting collection. No
    second matcher, and the "awaiting collection" predicate comes from the
    repository constant rather than a re-typed "READY".

    30-DAY BROWSE HORIZON (owner ruling 2026-09-01; owner 2026-09-02: "let
    users search through 30 days pending delivery data, except admin and
    superadmin"). Clamped on ``created_at``, which is a real BSON date -- it is
    written by BaseRepository._add_timestamps (datetime.now()), reached from
    this door's own writer OrderRepository.create_unique. That matters: the
    sibling field ``expected_delivery`` is stored as an ISO *string*
    (orders.py, order create: ``expected_delivery.isoformat()``), so a datetime
    bound against it would match nothing -- which is exactly why the clamp is
    NOT on the promised date.

    Why clamping the queue does not strand work in hand: a pair uncollected for
    40 days is the row staff most need, and both ways of reaching it stay open.
    Scanning the job card reads GET /orders/{order_id}, a single-record lookup
    with no window at all; typing the customer's name or number here resolves to
    ONE customer and lifts the window entirely through the SAME
    ``_query_names_one_customer`` exemption GET /orders/search uses. What the
    horizon removes is the unbounded BROWSE -- scrolling the whole shelf's
    history -- which is the exfiltration surface the rule exists for. ADMIN and
    SUPERADMIN keep the full queue (is_unrestricted).
    """
    repo = get_order_repository()
    active_store = validate_store_access(store_id, current_user)

    if repo is not None:
        from ...services.data_horizon import drop_rows_before_horizon

        needle = (q or "").strip()
        if needle:
            orders = [
                o
                for o in (repo.search_orders(needle, active_store) or [])
                if o.get("status") == repo.READY_FOR_DELIVERY_STATUS
            ]
            # A fuzzy fragment ("ra", "ORD") is still BROWSING and stays
            # clamped. Resolving the query to one customer -- rather than
            # trusting the shape of the string -- is what stops a one-character
            # search from becoming the way out of the window.
            customer_scoped = _query_names_one_customer(needle, active_store)
        else:
            orders = repo.find_ready_for_delivery(active_store)
            customer_scoped = False

        orders = drop_rows_before_horizon(
            orders, current_user, customer_scoped=customer_scoped
        )
        _stamp_status_actor_names(orders)
        orders_formatted = [order_to_frontend(o) for o in orders]
        return {"orders": orders_formatted}

    return {"orders": []}


@router.get("/unpaid/list")
async def get_unpaid_orders(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get unpaid/partially paid orders"""
    repo = get_order_repository()
    active_store = validate_store_access(store_id, current_user)

    if repo is not None:
        orders = repo.find_unpaid(active_store)
        _stamp_status_actor_names(orders)
        orders_formatted = [order_to_frontend(o) for o in orders]
        return {"orders": orders_formatted}

    return {"orders": []}


@router.get("/overdue/list")
async def get_overdue_orders(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get overdue orders (past expected delivery)"""
    repo = get_order_repository()
    active_store = validate_store_access(store_id, current_user)

    if repo is not None:
        orders = repo.find_overdue(active_store)
        _stamp_status_actor_names(orders)
        orders_formatted = [order_to_frontend(o) for o in orders]
        return {"orders": orders_formatted}

    return {"orders": []}


def _query_names_one_customer(q: str, store_id: Optional[str]) -> bool:
    """True when this search string identifies ONE customer -- the owner's
    named-lookup exemption to the 30-day browse horizon.

    Two conditions, both required:
      1. the customer search resolves to exactly ONE record, and
      2. the query really is that customer's name or number.

    (2) is not redundant. "Resolved to one" is also true of a store with one
    customer in it, or a matcher looser than we assume -- and then ANY string,
    "ORD" included, is a named lookup and the window is gone. Verifying the
    match against the returned record makes the exemption depend on the query
    naming a person rather than on the size of the customer book.

    Fail-CLOSED: any error means "not a named lookup". An exemption that opens
    on an exception is not an exemption.
    """
    try:
        needle = (q or "").strip().lower()
        if len(needle) < 2:
            return False
        repo = get_customer_repository()
        if repo is None:
            return False
        hits = repo.search_customers(q, store_id) or []
        if len(hits) != 1:
            return False
        c = hits[0]
        digits = "".join(ch for ch in needle if ch.isdigit())
        for key in ("name", "mobile", "phone", "email"):
            v = str(c.get(key) or "").lower()
            if not v:
                continue
            if needle in v:
                return True
            if (
                digits
                and len(digits) >= 6
                and digits in "".join(ch for ch in v if ch.isdigit())
            ):
                return True
        # A family member (patients[].name / .mobile) names the account too.
        for p in c.get("patients") or []:
            if not isinstance(p, dict):
                continue
            for key in ("name", "mobile"):
                v = str(p.get(key) or "").lower()
                if v and needle in v:
                    return True
        return False
    except Exception:  # noqa: BLE001 -- never let the exemption fail OPEN
        return False


@router.get("/search")
async def search_orders(
    q: str = Query(..., min_length=2),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Search orders by number, customer name, or phone"""
    repo = get_order_repository()
    active_store = validate_store_access(store_id, current_user)

    if repo is not None:
        from ...services.data_horizon import drop_rows_before_horizon

        orders = repo.search_orders(q, active_store)
        # 30-day browse horizon (owner ruling 2026-09-01), with the owner's
        # named-lookup exemption. Searching "ORD" or a two-letter fragment is
        # BROWSING and is clamped; a query that resolves to exactly ONE customer
        # is the person the staff member is serving, and their whole history is
        # exactly what the exemption is for. Resolving it -- rather than
        # trusting the shape of the string -- is what stops a one-character
        # search from becoming the way out of the window.
        orders = drop_rows_before_horizon(
            orders,
            current_user,
            customer_scoped=_query_names_one_customer(q, active_store),
        )
        _stamp_status_actor_names(orders)
        orders_formatted = [order_to_frontend(o) for o in orders]
        return {"orders": orders_formatted}

    return {"orders": []}


@router.get("/sales/summary")
async def get_sales_summary(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Get sales summary for a date range"""
    repo = get_order_repository()
    active_store = validate_store_access(store_id, current_user)

    if repo and active_store:
        summary = repo.get_sales_summary(active_store, from_date, to_date)
        return summary

    return {
        "totalOrders": 0,
        "totalRevenue": 0,
        "totalPaid": 0,
        "avgOrderValue": 0,
        "totalItems": 0,
    }


@router.get("/status/counts")
async def get_status_counts(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get order counts by status"""
    repo = get_order_repository()
    active_store = validate_store_access(store_id, current_user)

    if repo is not None:
        counts = repo.get_status_counts(active_store)
        return {"statusCounts": counts}

    return {"statusCounts": {}}
