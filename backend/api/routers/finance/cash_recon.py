"""Cash reconciliation summary across both day-close systems, and its sign-off.

Moved verbatim out of the 6,604-line api/routers/finance.py (Wave 5
package split): no path, method, dependency, status code, response_model
or default was changed.
"""

from ...utils.ist import ist_today
from typing import Optional, List, Dict
from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel
from ..auth import get_current_user
from ...dependencies import validate_store_access
from ...services import cash_register
from ._shared import _get_db, _iso_now, _store_name_map, logger, router
from .cash_drawer_window import _CASH_SESSIONS, _ist_day_face

# ============================================================================
# Cash reconciliation summary -- manager-facing console (#7)
# ============================================================================
# A unified, read-only view across BOTH day-close systems so a manager / owner
# can see, per store per day, whether the cash drawer tallied:
#   * cash_register_sessions (CR-...) -- the manual close-by-denomination flow
#     (status CLOSED): counted/expected/variance in RUPEES, by_mode_breakdown.
#   * till_sessions (TILL-...)        -- the BLIND EOD count (status LOCKED):
#     blind_count_paisa/expected_cash_paisa/variance_paisa in PAISA, by_mode.
# Both already compute the variance; this endpoint ONLY reads + normalises them
# into one row shape and applies store-scoping. NO new variance math, NO writes
# (other than the optional manager sign-off below). Variance = counted - expected
# (positive = OVERAGE / drawer over; negative = SHORTAGE / drawer short);
# |variance| <= a small rounding epsilon is BALANCED.

_CASH_RECON_ROLES = (
    "SUPERADMIN",
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
    "ACCOUNTANT",
)
# Rounding epsilon (rupees) within which a session is treated as BALANCED even
# when a session was closed with tolerance 0 -- guards against sub-paisa float
# noise. A real over/short is always > 1 paisa, so 0.005 never masks one.
_RECON_EPSILON = 0.005

_CASH_RECON_SIGNOFFS = "cash_recon_signoffs"


def _recon_status(variance: float, tolerance: float = 0.0) -> str:
    """Classify a rupee variance into BALANCED / OVERAGE / SHORTAGE. The band is
    the session's own tolerance OR the rounding epsilon, whichever is larger."""
    try:
        v = float(variance or 0)
        band = max(abs(float(tolerance or 0)), _RECON_EPSILON)
    except (TypeError, ValueError):
        return "BALANCED"
    if abs(v) <= band:
        return "BALANCED"
    return "OVERAGE" if v > 0 else "SHORTAGE"


def _user_name_map(db, user_ids) -> Dict[str, str]:
    """user_id -> display name for the given ids (closed_by / locked_by fallback
    when the session doc didn't already stamp the name). Fail-soft to {}."""
    ids = [u for u in set(user_ids) if u]
    if not ids or db is None:
        return {}
    out: Dict[str, str] = {}
    try:
        for u in db.get_collection("users").find(
            {"$or": [{"user_id": {"$in": ids}}, {"id": {"$in": ids}}]},
            {"_id": 0, "user_id": 1, "id": 1, "full_name": 1, "name": 1, "username": 1},
        ):
            uid = u.get("user_id") or u.get("id")
            if uid:
                out[uid] = (
                    u.get("full_name") or u.get("name") or u.get("username") or uid
                )
    except Exception:  # noqa: BLE001
        pass
    return out


def _norm_by_mode(raw) -> Dict[str, Dict[str, float]]:
    """Normalise a by-mode/by-tender breakdown into {MODE: {net, count}} rupees.
    Both engines store {MODE: {collected, refunded, net, count}} -- we keep the
    net + count for the console (the per-tender drill-down)."""
    out: Dict[str, Dict[str, float]] = {}
    if not isinstance(raw, dict):
        return out
    for mode, row in raw.items():
        if not isinstance(row, dict):
            continue
        try:
            net = round(float(row.get("net", 0) or 0), 2)
        except (TypeError, ValueError):
            net = 0.0
        try:
            count = int(row.get("count", 0) or 0)
        except (TypeError, ValueError):
            count = 0
        out[str(mode).upper()] = {"net": net, "count": count}
    return out


@router.get("/cash-reconciliation-summary")
async def cash_reconciliation_summary(
    from_date: Optional[str] = Query(
        None, alias="from", description="Range start (YYYY-MM-DD, inclusive)"
    ),
    to_date: Optional[str] = Query(
        None, alias="to", description="Range end (YYYY-MM-DD, inclusive)"
    ),
    store_id: Optional[str] = Query(None, description="Filter to one store"),
    current_user: dict = Depends(get_current_user),
):
    """Manager-facing cash reconciliation across the close-by-denomination
    (cash_register_sessions, CLOSED) AND the blind-EOD (till_sessions, LOCKED)
    flows for a date range.

    One normalised row per closed session: opening_float, cash_sales,
    cash_refunds, cash_expenses, expected_cash, counted_cash (blind for till
    sessions), variance (counted - expected), variance_status (BALANCED /
    OVERAGE / SHORTAGE), by_mode (per-tender net), closed_by + closed_at.

    Store-scope: STORE_MANAGER / store-level roles see only their own store;
    ADMIN / AREA_MANAGER / ACCOUNTANT / SUPERADMIN see all (or a chosen store).
    Read-only; no variance is recomputed (both engines already did it)."""
    from ...dependencies import resolve_store_scope

    roles = set(current_user.get("roles") or [])
    if not (roles & set(_CASH_RECON_ROLES)):
        raise HTTPException(status_code=403, detail="Manager / finance roles required")

    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    # Resolve the store filter through the canonical guard: an explicit store_id
    # is access-checked; an omitted one means all-stores for admins but the
    # caller's OWN store for a store-scoped role (no all-stores by omission).
    scoped_store = resolve_store_scope(store_id, current_user)

    # Default the range to the current IST month so the console is never empty
    # on first load.
    today = ist_today()
    start_day = (from_date or today.replace(day=1).isoformat())[:10]
    end_day = (to_date or today.isoformat())[:10]

    store_names = _store_name_map(db)

    rows: List[dict] = []
    pending_user_ids: List[str] = []

    # --- 1) Manual close-by-denomination sessions (rupees) -----------------
    cr_match: Dict = {"status": "CLOSED"}
    if scoped_store:
        cr_match["store_id"] = scoped_store
    try:
        cr_cursor = db.get_collection(_CASH_SESSIONS).find(cr_match, {"_id": 0})
        cr_sessions = list(cr_cursor)
    except Exception:  # noqa: BLE001
        cr_sessions = []

    for s in cr_sessions:
        # The "business day" is the close date (these sessions have no
        # session_date field). Fall back to opened_at.
        # BUG-104, VALUE rule via parse-then-shift (the _credit_note_date_ist
        # shape): closed_at/opened_at are written by _iso_now() ==
        # datetime.utcnow().isoformat() -- a NAIVE-UTC ISO STRING, whose
        # frame is known from the writer, so parse it to the instant FIRST
        # and then take the IST day. Slicing [:10] read the UTC day: a till
        # closed after midnight IST (00:00-05:30) filed under YESTERDAY's
        # business day AND was range-filtered against the operator's IST
        # start/end days in the wrong frame. Unparseable junk falls back to
        # the old first-10-chars behaviour.
        closed_at = s.get("closed_at") or s.get("opened_at")
        sess_day = _ist_day_face(closed_at)
        if sess_day and not (start_day <= sess_day <= end_day):
            continue
        expected = round(float(s.get("expected", 0) or 0), 2)
        # A DRAWER NOBODY COUNTED HAS NO FIGURE HERE EITHER. `counted` is None
        # on such a close record; `float(None or 0)` would have rebuilt the
        # fabricated zero -- and its full-day shortfall -- one screen further
        # along, which is where a manager actually reads it.
        raw_counted = s.get("counted")
        counted = None if raw_counted is None else round(float(raw_counted or 0), 2)
        variance = None if counted is None else round(counted - expected, 2)
        tol = float(s.get("tolerance", 0) or 0)
        closed_by = s.get("closed_by")
        if closed_by and not s.get("closed_by_name"):
            pending_user_ids.append(closed_by)
        rows.append(
            {
                "session_id": s.get("session_id"),
                "source": "CASH_REGISTER",
                "store_id": s.get("store_id"),
                "store_name": store_names.get(s.get("store_id"), s.get("store_id")),
                "session_date": sess_day,
                "shift": s.get("shift"),
                "opening_float": round(float(s.get("opening_float", 0) or 0), 2),
                "cash_sales": round(float(s.get("cash_sales", 0) or 0), 2),
                "cash_refunds": round(float(s.get("cash_refunds", 0) or 0), 2),
                "cash_expenses": round(float(s.get("cash_expenses", 0) or 0), 2),
                "bank_deposit": round(float(s.get("bank_deposit", 0) or 0), 2),
                "expected_cash": expected,
                "counted_cash": counted,
                "blind": False,
                "variance": variance,
                # Same guard as the close handler: a withheld NEGATIVE_EXPECTED
                # verdict must not be re-derived into a phantom OVERAGE here.
                # (Otherwise _recon_status is authoritative for this grid -- it
                # emits the grid's OVERAGE/SHORTAGE vocabulary, which the totals
                # below bucket on; the session's own OVER/SHORT wording is not
                # interchangeable with it.)
                "variance_status": (
                    cash_register.NOT_COUNTED
                    if counted is None
                    else (
                        cash_register.NEGATIVE_EXPECTED
                        if s.get("negative_expected_advisory")
                        else _recon_status(variance, tol)
                    )
                ),
                "tolerance": round(abs(tol), 2),
                "by_mode": _norm_by_mode(s.get("by_mode_breakdown")),
                # Both row kinds are now on ONE basis so the grid's tender
                # column means the same thing in every row. Legacy sessions
                # closed before the change carry a payments-only breakdown.
                "by_mode_basis": s.get("by_mode_basis") or "PAYMENTS_ONLY_LEGACY",
                "refund_double_entry_advisory": s.get("refund_double_entry_advisory"),
                # Carried from the close record, not recomputed. The row's own
                # arithmetic (opening + sales - refunds - expenses = expected)
                # must keep tying out, and `expected` here is the figure the
                # closer actually counted money against on the day -- restating
                # a signed-off drawer from today's rules would be worse than
                # the thing it fixes. New closes are already payroll-free at
                # source; sessions closed before this change keep their number
                # and now say so.
                "off_till_expense_advisory": bool(s.get("off_till_expense_advisory")),
                "negative_expected_advisory": bool(s.get("negative_expected_advisory")),
                "closed_by": closed_by,
                "closed_by_name": s.get("closed_by_name"),
                "closed_at": closed_at,
                # The shared till session this close landed on, if any. Used
                # below to make sure ONE counted drawer is ONE row here.
                "till_session_id": s.get("till_session_id"),
            }
        )

    # --- 2) Blind EOD (Z-Read) sessions, paisa -> rupees -------------------
    till_match: Dict = {"status": "LOCKED"}
    if scoped_store:
        till_match["store_id"] = scoped_store
    try:
        till_cursor = db.get_collection("till_sessions").find(till_match, {"_id": 0})
        till_sessions = list(till_cursor)
    except Exception:  # noqa: BLE001
        till_sessions = []

    def _p2r(paisa) -> float:
        try:
            return round(int(paisa or 0) / 100.0, 2)
        except (TypeError, ValueError):
            return 0.0

    for s in till_sessions:
        sess_day = str(s.get("session_date") or "")[:10]
        if sess_day and not (start_day <= sess_day <= end_day):
            continue
        expected = _p2r(s.get("expected_cash_paisa"))
        # Same rule on the Z-Read side: `blind_count_paisa` stays None until
        # somebody counts, so an uncounted drawer reports no figure, not zero.
        counted = (
            None
            if s.get("blind_count_paisa") is None
            else _p2r(s.get("blind_count_paisa"))
        )
        variance = None if counted is None else round(counted - expected, 2)
        tol = _p2r(s.get("tolerance_paisa"))
        locked_by = s.get("locked_by")
        if locked_by and not s.get("locked_by_name"):
            pending_user_ids.append(locked_by)
        rows.append(
            {
                "session_id": s.get("session_id"),
                "source": "BLIND_EOD",
                "store_id": s.get("store_id"),
                "store_name": store_names.get(s.get("store_id"), s.get("store_id")),
                "session_date": sess_day,
                "shift": s.get("shift"),
                "opening_float": _p2r(s.get("opening_float_paisa")),
                # cash_sales is now GROSS collected; cash_refunds is the recorded
                # cash refunds auto-deducted (distinct from the manual cash
                # payouts in cash_expenses) so this row is comparable to the
                # cash-register rows above and no sales silently vanish.
                "cash_sales": _p2r(s.get("cash_sales_paisa")),
                "cash_refunds": _p2r(s.get("cash_refunds_paisa")),
                "cash_expenses": _p2r(s.get("cash_payouts_paisa")),
                "bank_deposit": 0.0,
                "expected_cash": expected,
                "counted_cash": counted,
                "blind": True,
                "variance": variance,
                # Trust the engine's stored status when present (it used the
                # configured tolerance); else classify with the same band.
                "variance_status": (
                    cash_register.NOT_COUNTED
                    if counted is None
                    else s.get("variance_status") or _recon_status(variance, tol)
                ),
                "tolerance": tol,
                "by_mode": _norm_by_mode(s.get("by_mode")),
                "by_mode_basis": "NET_OF_RECORDED_REFUNDS",
                "refund_double_entry_advisory": (
                    {"reason": "CO_OCCURRENCE"}
                    if s.get("refund_double_entry_advisory")
                    else None
                ),
                # Since the 2026-08-25 ruling `cash_payouts_paisa` is AUTO-
                # PULLED from the `expenses` collection at blind-submit, and
                # the session stores whether a payroll-shaped head was
                # deliberately left out. Older sessions (hand-keyed payouts)
                # never stored the flag -> False, as before.
                "off_till_expense_advisory": bool(s.get("off_till_expense_advisory")),
                "negative_expected_advisory": bool(s.get("negative_expected_advisory")),
                "closed_by": locked_by,
                "closed_by_name": s.get("locked_by_name"),
                "closed_at": s.get("locked_at"),
                "zread_number": s.get("zread_number"),
            }
        )

    # --- 2b) ONE COUNTED DRAWER, ONE ROW -----------------------------------
    # Since both close screens land on the SAME till session, a drawer closed
    # from Finance > Cash Register and then locked as a Z-Read would otherwise
    # appear TWICE in this grid -- the same money counted once, reported as two
    # days' worth. Where a cash-register row names a till session that is also
    # in this grid, the till row is the survivor (it is the shared record, and
    # it carries the Z-Read number); the cash-register row is folded into it so
    # nothing about who closed it is lost.
    till_row_ids = {
        r["session_id"]
        for r in rows
        if r.get("source") == "BLIND_EOD" and r.get("session_id")
    }
    if till_row_ids:
        by_id = {r["session_id"]: r for r in rows if r.get("source") == "BLIND_EOD"}
        survivors: List[dict] = []
        for r in rows:
            linked = r.get("till_session_id")
            if r.get("source") == "CASH_REGISTER" and linked in till_row_ids:
                by_id[linked]["also_closed_from"] = r.get("session_id")
                by_id[linked]["also_closed_from_source"] = "CASH_REGISTER"
                continue
            survivors.append(r)
        rows = survivors

    # Resolve any missing closer names in one batch, then backfill.
    if pending_user_ids:
        name_map = _user_name_map(db, pending_user_ids)
        for r in rows:
            if not r.get("closed_by_name") and r.get("closed_by"):
                r["closed_by_name"] = name_map.get(r["closed_by"], r["closed_by"])

    # Attach any manager sign-off marker (reviewed/audited) for each session.
    try:
        sess_ids = [r["session_id"] for r in rows if r.get("session_id")]
        if sess_ids:
            signoffs = {
                d.get("session_id"): d
                for d in db.get_collection(_CASH_RECON_SIGNOFFS).find(
                    {"session_id": {"$in": sess_ids}}, {"_id": 0}
                )
            }
            for r in rows:
                so = signoffs.get(r.get("session_id"))
                r["signoff"] = (
                    {
                        "reviewed": True,
                        "reviewed_by": so.get("reviewed_by"),
                        "reviewed_by_name": so.get("reviewed_by_name"),
                        "reviewed_at": so.get("reviewed_at"),
                        "note": so.get("note"),
                    }
                    if so
                    else {"reviewed": False}
                )
    except Exception:  # noqa: BLE001
        for r in rows:
            r.setdefault("signoff", {"reviewed": False})

    # Newest day first; within a day, newest close first.
    rows.sort(
        key=lambda r: (r.get("session_date") or "", str(r.get("closed_at") or "")),
        reverse=True,
    )

    # Per-range totals.
    def _sum(field: str) -> float:
        return round(sum(float(r.get(field, 0) or 0) for r in rows), 2)

    over = [r for r in rows if r.get("variance_status") == "OVERAGE"]
    short = [r for r in rows if r.get("variance_status") == "SHORTAGE"]
    balanced = [r for r in rows if r.get("variance_status") == "BALANCED"]
    totals = {
        "sessions": len(rows),
        "balanced": len(balanced),
        "overage": len(over),
        "shortage": len(short),
        "opening_float": _sum("opening_float"),
        "cash_sales": _sum("cash_sales"),
        "cash_refunds": _sum("cash_refunds"),
        "cash_expenses": _sum("cash_expenses"),
        "expected_cash": _sum("expected_cash"),
        "counted_cash": _sum("counted_cash"),
        "variance": _sum("variance"),
        "overage_amount": round(sum(r["variance"] for r in over), 2),
        "shortage_amount": round(sum(abs(r["variance"]) for r in short), 2),
    }

    return {
        "from": start_day,
        "to": end_day,
        "store_id": scoped_store,
        "rows": rows,
        "totals": totals,
    }


class CashReconSignoff(BaseModel):
    session_id: str
    source: str = "CASH_REGISTER"  # CASH_REGISTER | BLIND_EOD (informational)
    note: Optional[str] = None


@router.post("/cash-reconciliation-signoff")
async def cash_reconciliation_signoff(
    body: CashReconSignoff,
    current_user: dict = Depends(get_current_user),
):
    """Manager SIGN-OFF: mark a reconciled session as reviewed + audited.

    Idempotent upsert into ``cash_recon_signoffs`` keyed on session_id. The
    actor must have access to the session's store. Manager / finance roles only.
    This is the lightweight review marker the console surfaces per row; it does
    NOT change any variance figure (the day-close lock is the source of truth)."""
    roles = set(current_user.get("roles") or [])
    if not (roles & set(_CASH_RECON_ROLES)):
        raise HTTPException(status_code=403, detail="Manager / finance roles required")

    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    # Locate the underlying session in either collection to store-scope the actor
    # (cross-store IDOR guard) before recording the sign-off.
    session = None
    for coll_name in (_CASH_SESSIONS, "till_sessions"):
        try:
            doc = db.get_collection(coll_name).find_one(
                {"session_id": body.session_id}, {"_id": 0}
            )
        except Exception:  # noqa: BLE001
            doc = None
        if doc:
            session = doc
            break
    if session is None:
        raise HTTPException(status_code=404, detail="Reconciliation session not found")

    validate_store_access(session.get("store_id") or "", current_user)

    now = _iso_now()
    reviewer = current_user.get("name") or current_user.get("full_name")
    record = {
        "session_id": body.session_id,
        "source": body.source,
        "store_id": session.get("store_id"),
        "reviewed": True,
        "reviewed_by": current_user.get("user_id"),
        "reviewed_by_name": reviewer,
        "reviewed_at": now,
        "note": body.note,
    }
    try:
        db.get_collection(_CASH_RECON_SIGNOFFS).update_one(
            {"session_id": body.session_id},
            {"$set": record},
            upsert=True,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Cash reconciliation sign-off failed")
        raise HTTPException(
            status_code=500,
            detail="Could not record the sign-off - try again or contact support",
        )

    # Audit the review (fail-soft; never undoes the sign-off write).
    try:
        from api.dependencies import get_audit_repository

        repo = get_audit_repository()
        if repo is not None:
            repo.create(
                {
                    "action": "cash_recon.signoff",
                    "entity_type": "cash_recon_session",
                    "entity_id": body.session_id,
                    "store_id": session.get("store_id"),
                    "user_id": current_user.get("user_id"),
                    "user_name": reviewer,
                    "severity": "INFO",
                    "source": "finance",
                    "after_state": {"reviewed": True, "note": body.note},
                }
            )
    except Exception:  # noqa: BLE001
        pass

    return {"ok": True, "signoff": record}
