"""F34 global target ticker (the one ungated GET) and its settings POST.

Moved verbatim out of the 6,604-line api/routers/finance.py (Wave 5
package split): no path, method, dependency, status code, response_model
or default was changed.
"""

from typing import Optional, List, Dict
from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field
from ..auth import get_current_user
from ...dependencies import validate_store_access
from ...services.cache import cache
from ...services import ticker_service, policy_engine
from ...services import name_resolver
from ._shared import _get_db, router, ticker_router

# === F34 Global Target Ticker ============================================
# A privacy-stratified live monthly-revenue-vs-target card on the Hub.
# Management roles see rupees + pace; floor roles see ONLY pct_complete.
# raw_visible is computed SERVER-SIDE from the JWT role (never trusted from the
# client). No REVENUE budget for the month -> no_target=true (never fabricated).

_TICKER_CACHE_PREFIX = "ticker:"


def _ticker_stores_for(
    db, store_id: Optional[str], current_user: dict
) -> List[Dict[str, str]]:
    """The list of {store_id, store_name} this caller may see on the ticker.

    - explicit store_id -> validate_store_access (403 on cross-store request).
    - HQ roles (SUPERADMIN/ADMIN/AREA_MANAGER) with no store_id -> all active
      stores (AREA_MANAGER limited to their own store_ids).
    - any other role -> their single active store.
    """
    # Resolve store_id -> human store name via the shared, fail-soft resolver
    # (owner backlog #4 / #780). The stores collection keys its display name on
    # ``store_name`` (then ``store_code``); the earlier inline lookup read a
    # non-existent ``name`` field, so the Hub "Monthly target" rows fell back to
    # the raw store_id (a UUID). store_name_map reads the correct fields.
    name_by_id: Dict[str, str] = name_resolver.store_name_map(db)

    def _entry(sid: str) -> Dict[str, str]:
        return {"store_id": sid, "store_name": name_by_id.get(sid, sid)}

    if store_id:
        sid = validate_store_access(store_id, current_user)
        return [_entry(sid)] if sid else []

    roles = set(current_user.get("roles") or [])
    if roles & {"SUPERADMIN", "ADMIN"}:
        # All active stores.
        active: List[str] = []
        try:
            for s in db.get_collection("stores").find(
                {"$or": [{"is_active": True}, {"is_active": {"$exists": False}}]},
                {"_id": 0, "store_id": 1},
            ):
                if s.get("store_id"):
                    active.append(s["store_id"])
        except Exception:  # noqa: BLE001
            active = list(name_by_id.keys())
        return [_entry(s) for s in (active or list(name_by_id.keys()))]
    if "AREA_MANAGER" in roles:
        return [_entry(s) for s in (current_user.get("store_ids") or [])]
    # Store-scoped role: their single active store.
    sid = current_user.get("active_store_id")
    return [_entry(sid)] if sid else []


@ticker_router.get("/target-ticker")
async def get_target_ticker(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Monthly-target ticker, privacy-stratified by JWT role (server-side).

    Management (SUPERADMIN/ADMIN/AREA_MANAGER/STORE_MANAGER/ACCOUNTANT) get
    raw_visible:true with mtd_revenue/monthly_target/pct_complete/pace; floor
    roles get raw_visible:false and pct_complete ONLY (rupee keys are ABSENT,
    never null). Fail-soft: DB down -> a single no_target store, HTTP 200."""
    raw_visible = ticker_service.raw_visible_for(current_user)
    refresh_seconds = int(
        policy_engine.get_policy(
            "ticker.refresh_seconds",
            scope={},
            default=ticker_service.DEFAULT_REFRESH_SECONDS,
        )
    )

    db = _get_db()
    if db is None:
        entry = ticker_service.compute_store_entry(
            store_id="",
            store_name="",
            monthly_target=None,
            mtd=0.0,
            days_elapsed=0,
            days_in_month=0,
            milestones_fired=[],
        )
        entry = entry if raw_visible else ticker_service.mask_entry(entry)
        return {
            "raw_visible": raw_visible,
            "stores": [entry],
            "ticker_refresh_seconds": refresh_seconds,
        }

    stores = _ticker_stores_for(db, store_id, current_user)
    period = ticker_service.current_period()
    _, days_elapsed, days_in_month = ticker_service._month_bounds()
    orders_coll = db.get_collection("orders")
    budgets_coll = db.get_collection("budgets")

    out_stores: List[Dict] = []
    for st in stores:
        sid = st["store_id"]
        # Cache the AGGREGATE (revenue + target) per store+period; masking happens
        # AFTER the cache read so raw + masked share one cached compute.
        ck = "%s%s:%s" % (_TICKER_CACHE_PREFIX, sid, period)
        cached = cache.get(ck)
        if cached is not None:
            mtd = float(cached.get("mtd_revenue") or 0.0)
            target = cached.get("monthly_target")
            milestones_fired = cached.get("milestones_fired") or []
        else:
            mtd = ticker_service.mtd_revenue(orders_coll, sid)
            target = None
            milestones_fired = []
            try:
                bdoc = (
                    budgets_coll.find_one(
                        {"store_id": sid, "period": period, "head": "REVENUE"}
                    )
                    if budgets_coll is not None
                    else None
                )
            except Exception:  # noqa: BLE001
                bdoc = None
            if bdoc:
                target = bdoc.get("planned_amount")
                milestones_fired = bdoc.get("milestones_fired") or []
            cache.set(
                ck,
                {
                    "mtd_revenue": mtd,
                    "monthly_target": target,
                    "milestones_fired": milestones_fired,
                },
                ttl=cache.TTL_SHORT,
            )

        entry = ticker_service.compute_store_entry(
            store_id=sid,
            store_name=st["store_name"],
            monthly_target=target,
            mtd=mtd,
            days_elapsed=days_elapsed,
            days_in_month=days_in_month,
            milestones_fired=milestones_fired,
        )
        out_stores.append(entry if raw_visible else ticker_service.mask_entry(entry))

    if not out_stores:
        # No store resolved (e.g. a role with no active store) -- greyed card.
        entry = ticker_service.compute_store_entry(
            store_id="",
            store_name="",
            monthly_target=None,
            mtd=0.0,
            days_elapsed=days_elapsed,
            days_in_month=days_in_month,
            milestones_fired=[],
        )
        out_stores = [entry if raw_visible else ticker_service.mask_entry(entry)]

    return {
        "raw_visible": raw_visible,
        "stores": out_stores,
        "ticker_refresh_seconds": refresh_seconds,
    }


class TickerSettingsBody(BaseModel):
    milestone_pcts: List[int] = Field(..., description="Milestone thresholds 1-100")
    refresh_seconds: int = Field(..., ge=30, le=300)


@router.post("/target-ticker/settings")
async def update_target_ticker_settings(
    body: TickerSettingsBody,
    current_user: dict = Depends(get_current_user),
):
    """Set the two E2 ticker keys (SUPERADMIN/ADMIN only) via the policy engine.
    Invalidates the per-store ticker cache so the next GET reflects the change."""
    roles = set(current_user.get("roles") or [])
    if not (roles & {"SUPERADMIN", "ADMIN"}):
        raise HTTPException(
            status_code=403, detail="Only SUPERADMIN/ADMIN may change ticker settings"
        )

    pcts = body.milestone_pcts or []
    if not pcts or any((not isinstance(p, int)) or p < 1 or p > 100 for p in pcts):
        raise HTTPException(
            status_code=400, detail="milestone_pcts must be integers in 1..100"
        )
    # de-dup + sort for a stable stored list
    pcts = sorted(set(int(p) for p in pcts))

    try:
        policy_engine.set_policy(
            "ticker.milestone_pcts", pcts, scope={}, actor=current_user
        )
        policy_engine.set_policy(
            "ticker.refresh_seconds",
            int(body.refresh_seconds),
            scope={},
            actor=current_user,
        )
    except policy_engine.PolicyError as exc:
        raise HTTPException(status_code=getattr(exc, "status", 400), detail=str(exc))

    # Invalidate cached aggregates (Redis: pattern; in-memory: best-effort per-store).
    try:
        cache.delete_pattern("%s*" % _TICKER_CACHE_PREFIX)
    except Exception:  # noqa: BLE001
        pass

    return {
        "milestone_pcts": pcts,
        "refresh_seconds": int(body.refresh_seconds),
        "saved": True,
    }
