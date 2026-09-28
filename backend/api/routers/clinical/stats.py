"""Optometrist stats + the optometrist -> retail conversion dashboard (F24).

Moved verbatim out of the 3,158-line api/routers/clinical.py (Wave 6
package split): no path, method, dependency, status code, response_model,
default or validation was changed.
"""

from fastapi import HTTPException, Depends, Query
from typing import Optional
from datetime import date
from ..auth import require_roles
from ...dependencies import (
    get_eye_test_repository,
    get_order_repository,
    validate_store_access,
)
from ...services import conversion_analytics as _conversion
from ._shared import (
    _CONVERSION_REVENUE_ROLES,
    _CONVERSION_VIEW_ROLES,
    _HQ_ROLES,
    _conversion_can_see_revenue,
    router,
)


@router.get("/optometrist/{optometrist_id}/stats")
async def get_optometrist_stats(
    optometrist_id: str,
    from_date: date = Query(...),
    to_date: date = Query(...),
    store_id: Optional[str] = Query(None),
    conversion_window_days: int = Query(7, ge=1, le=90),
    current_user: dict = Depends(require_roles(*_CONVERSION_VIEW_ROLES)),
):
    """Statistics for one optometrist: test counts + retail-conversion (F24).

    RBAC (F24): previously this route had NO role guard -- any authenticated
    caller (incl. SALES_CASHIER) could read another optometrist's scorecard. It
    is now gated to clinical-management + optometrist roles, and an OPTOMETRIST
    caller may only read THEIR OWN id (else 403). Revenue is role-gated: an
    OPTOMETRIST never receives the rupee figures (present-but-null).
    """
    roles = set(current_user.get("roles") or [])
    # OPTOMETRIST self-scope: a pure optometrist may only see their own row.
    is_revenue_role = bool(roles & _CONVERSION_REVENUE_ROLES) or "SUPERADMIN" in roles
    if not is_revenue_role and optometrist_id != current_user.get("user_id"):
        raise HTTPException(
            status_code=403,
            detail="Optometrists may only view their own conversion stats",
        )

    test_repo = get_eye_test_repository()
    order_repo = get_order_repository()

    # Backward-compatible base shape (total_tests / completed_tests / completion_rate)
    base = {"total_tests": 0, "completed_tests": 0, "completion_rate": 0}
    if test_repo is not None:
        try:
            base = test_repo.get_optometrist_stats(optometrist_id, from_date, to_date)
        except Exception:  # noqa: BLE001 - read path never 500s on a stats hiccup
            base = {"total_tests": 0, "completed_tests": 0, "completion_rate": 0}

    # Resolve the store scope for the conversion join. Mirror the sibling
    # /conversion-dashboard: a non-HQ revenue role (STORE_MANAGER) is validated
    # against the requested store -- WITHOUT this a STORE_MANAGER could pass a
    # foreign ?store_id and read another store's conversion + revenue (P1
    # cross-store money IDOR). HQ roles pass any store / their whole reach.
    if is_revenue_role:
        if roles & _HQ_ROLES:
            if store_id:
                scope = [validate_store_access(store_id, current_user)]
            else:
                scope = list(current_user.get("store_ids") or [])
                active = current_user.get("active_store_id")
                if active and active not in scope:
                    scope.append(active)
        else:
            resolved = validate_store_access(store_id, current_user)
            scope = [resolved] if resolved else []
    else:
        # Optometrist is bounded to their active store.
        active = current_user.get("active_store_id")
        scope = [active] if active else []

    dashboard = _conversion.get_conversion_dashboard(
        test_repo,
        order_repo,
        store_ids=[s for s in scope if s],
        from_date=from_date,
        to_date=to_date,
        conversion_window_days=conversion_window_days,
        include_revenue=is_revenue_role,
        optometrist_id_filter=optometrist_id,
    )
    rows = dashboard.get("rows") or []
    row = rows[0] if rows else {}

    out = dict(base)
    out.update(
        {
            "converted_count": row.get("converted_count", 0),
            "conversion_rate_pct": row.get("conversion_rate_pct", 0.0),
            "avg_days_to_order": row.get("avg_days_to_order"),
            "unattributed_tests": row.get("unattributed_tests", 0),
            # Role-gated: None (present-but-null) for OPTOMETRIST callers.
            "revenue_attributed": row.get("revenue_attributed"),
            "avg_order_value": row.get("avg_order_value"),
        }
    )
    return out


@router.get("/conversion-dashboard")
async def get_conversion_dashboard(
    from_date: date = Query(...),
    to_date: date = Query(...),
    store_id: Optional[str] = Query(None),
    conversion_window_days: int = Query(7, ge=1, le=90),
    current_user: dict = Depends(require_roles(*_CONVERSION_VIEW_ROLES)),
):
    """F24 -- Optometrist -> retail conversion dashboard.

    READ-ONLY: joins COMPLETED ``eye_tests`` to non-cancelled ``orders`` (same
    customer, within ``conversion_window_days``) to compute per-optometrist
    conversion. Touches NO order/POS/payment state.

    Role-gating (DECISIONS sec 3, LOCKED):
      * OPTOMETRIST -> only their OWN row; revenue figures are stripped
        server-side (emitted as null, never the rupee value, never 0).
      * STORE_MANAGER -> their store (validate_store_access) + revenue + summary.
      * AREA_MANAGER / ADMIN / SUPERADMIN -> any store (or whole org scope when
        store_id omitted) + revenue.
    """
    roles = set(current_user.get("roles") or [])
    is_revenue_role = _conversion_can_see_revenue(current_user) or "SUPERADMIN" in roles

    if not is_revenue_role:
        # Pure OPTOMETRIST: force self + active-store scope; revenue stripped.
        active = current_user.get("active_store_id")
        store_ids = [active] if active else []
        optometrist_filter = current_user.get("user_id")
    elif roles & _HQ_ROLES:
        # HQ roles: any store, or whole reachable scope when store_id omitted.
        if store_id:
            store_ids = [validate_store_access(store_id, current_user)]
        else:
            store_ids = list(current_user.get("store_ids") or [])
            active = current_user.get("active_store_id")
            if active and active not in store_ids:
                store_ids.append(active)
        optometrist_filter = None
    else:
        # STORE_MANAGER: bounded to their own store.
        resolved = validate_store_access(store_id, current_user)
        store_ids = [resolved] if resolved else []
        optometrist_filter = None

    return _conversion.get_conversion_dashboard(
        get_eye_test_repository(),
        get_order_repository(),
        store_ids=[s for s in store_ids if s],
        from_date=from_date,
        to_date=to_date,
        conversion_window_days=conversion_window_days,
        include_revenue=is_revenue_role,
        optometrist_id_filter=optometrist_filter,
    )
