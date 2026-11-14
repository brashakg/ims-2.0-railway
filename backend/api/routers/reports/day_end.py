"""Day-end close (cash-drawer reconciliation persistence)."""

from api.utils.event_time import utc_event_iso

from fastapi import Depends, Query
from pydantic import BaseModel, Field
from typing import Optional
from datetime import date, datetime
from ..auth import require_roles
from ...dependencies import store_in_view
from ...dependencies import (
    get_audit_repository,
    get_db,
    validate_store_access,
)
from ...services import cash_denominations as cash_denom
from ...services import eod_tally as till_service
from ..till import _is_cashier_only
from ._shared import (
    logger,
    router,
)

# ============================================================================
# Day-End Close (cash-drawer reconciliation persistence)
# ============================================================================
# The Day-End Closing Report (frontend reports/DayEndReport.tsx) lets a store
# reconcile the physical cash drawer against system cash and "Close Day". That
# action previously only flipped a local React flag (no persistence), so a page
# refresh lost the close and there was no audit record. These endpoints persist
# one immutable close per (store_id, date) and audit it.
#
# SYSTEM_INTENT: Audit Everything + Fail Loudly -> closing a day is a recorded,
# idempotent event; a second close of the same day is rejected (409), not
# silently re-written.

# SALES_CASHIER merged into SALES_STAFF (backlog #12): this gate granted
# SALES_CASHIER but not SALES_STAFF, so the access moves to the survivor.
_DAY_END_CLOSE_ROLES = (
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
    "ACCOUNTANT",
    "SALES_STAFF",
    "CASHIER",
)


class DayEndCloseBody(BaseModel):
    """Body for POST /reports/day-end-close. closing_cash = physically counted
    cash in the drawer; system_cash = cash the POS expects. variance is derived
    server-side (never trusted from the client)."""

    date: str = Field(..., description="Business date being closed (YYYY-MM-DD)")
    store_id: Optional[str] = Field(
        None, description="Store; defaults to the user's active store"
    )
    # BLANK IS NOT ZERO. This defaulted to 0.0, so a manager who closed without
    # typing a count persisted "Rs 0.00 in the drawer" and a variance equal to
    # the negative of the whole day's cash -- indistinguishable from a genuinely
    # emptied till. An absent count is now recorded as absent, and no variance
    # is invented for it.
    closing_cash: Optional[float] = Field(
        None, description="Physically counted cash in drawer; absent = not counted"
    )
    system_cash: Optional[float] = Field(
        None, description="System-expected cash (from POS)"
    )
    # The notes and coins behind that count. Optional and skippable -- a close
    # is never refused for want of a breakdown.
    closing_count: Optional[cash_denom.CashCountInput] = None
    notes: Optional[str] = Field(None, max_length=2000)


def day_end_close_key(store_id: str, day: str) -> str:
    """The _id of a shop-day's close: one per (store_id, date), enforced by
    Mongo's own unique _id index."""
    return f"DAYEND:{store_id}:{day}"


def _day_end_doc_public(doc: dict, user: Optional[dict] = None) -> dict:
    """Strip the Mongo _id for a JSON-safe response. A cashier-only caller
    never sees a drawer's till variance (blind enforcement, as on every till
    read): the per-drawer counts stay, the variances go."""
    if not doc:
        return {}
    out = dict(doc)
    out.pop("_id", None)
    if user is not None and out.get("till_drawers") and _is_cashier_only(user):
        out["till_drawers"] = [
            {k: v for k, v in d.items() if k not in ("variance_paisa", "variance_status")}
            for d in out["till_drawers"]
        ]
    return out


@router.get("/day-end-close")
async def get_day_end_close(
    date: str = Query(..., description="Business date (YYYY-MM-DD)"),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_DAY_END_CLOSE_ROLES)),
):
    """Return the day-end close status for (store_id, date). `closed` is False
    with `close: null` when the day hasn't been closed yet (honest empty state,
    not a fabricated record)."""
    sid = store_in_view(store_id, current_user, one_shop=True)
    db = get_db()
    if db is None or not getattr(db, "is_connected", False):
        # No DB -> we genuinely don't know; report not-closed rather than fake.
        return {"closed": False, "store_id": sid, "date": date, "close": None}

    doc = db.get_collection("day_end_closes").find_one({"store_id": sid, "date": date})
    return {
        "closed": bool(doc),
        "store_id": sid,
        "date": date,
        "close": _day_end_doc_public(doc, current_user) if doc else None,
    }


@router.post("/day-end-close")
async def create_day_end_close(
    body: DayEndCloseBody,
    current_user: dict = Depends(require_roles(*_DAY_END_CLOSE_ROLES)),
):
    """Persist a day-end cash-drawer close. Idempotent per (store_id, date): a
    repeat close of an already-closed day returns 409 (the existing close is in
    the error so the UI can surface it). Variance is computed server-side."""
    from fastapi import HTTPException

    sid = validate_store_access(body.store_id, current_user)
    if not sid:
        raise HTTPException(
            status_code=400, detail="No store selected for day-end close"
        )
    db = get_db()
    if db is None or not getattr(db, "is_connected", False):
        raise HTTPException(status_code=503, detail="Database not available")

    # W1.4 / OS-030: an ONLINE store has no cash drawer -- day-end cash close
    # does not apply (payments settle via the online payment gateway).
    from ...services.stores_util import is_online_store

    if is_online_store(db, sid):
        raise HTTPException(
            status_code=400,
            detail=(
                "This is an online store - there is no cash drawer to "
                "reconcile, so day-end close does not apply here."
            ),
        )

    closes = db.get_collection("day_end_closes")
    existing = closes.find_one({"store_id": sid, "date": body.date})
    if existing:
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"Day {body.date} already closed for store {sid}",
                "close": _day_end_doc_public(existing, current_user),
            },
        )

    # The notes-and-coins count, if one was entered. When the cashier counted
    # the grid but typed no total, the count IS the total -- that is what is
    # physically in the drawer.
    closing_block = cash_denom.block_from_input(
        body.closing_count, 0, actor=current_user
    )
    counted_from_grid = (
        cash_denom.paisa_to_rupees(closing_block["total_paisa"])
        if cash_denom.is_captured(closing_block)
        else None
    )
    closing_cash = (
        round(float(body.closing_cash), 2)
        if body.closing_cash is not None
        else counted_from_grid
    )
    if closing_cash is not None:
        # For a DRAWER COUNT the count is the amount, so the block reconciles
        # against the figure being stored rather than against a stale zero.
        cash_denom.restate_amount(
            closing_block, cash_denom.rupees_to_paisa(closing_cash)
        )
    system_cash = (
        round(float(body.system_cash), 2) if body.system_cash is not None else None
    )

    # TWO DOORS, ONE RECORD: the count also lands on the day's single till
    # session -- the same record Finance > Cash Register closes onto -- so the
    # two screens can never hold two different answers. Fail-soft: linking must
    # never stop a store closing its day, and a failure is reported, not hidden.
    till_link = till_service.record_screen_close(
        db,
        store_id=sid,
        session_date=body.date,
        closing_rows=(
            body.closing_count.rows if body.closing_count is not None else None
        ),
        closing_count_state=(
            body.closing_count.state if body.closing_count is not None else None
        ),
        # The typed figure, so a close with a total but no breakdown still lands
        # a real count on the shared record instead of nothing -- and a close
        # with NEITHER lands no count at all rather than a fabricated zero.
        counted_paisa=(
            cash_denom.rupees_to_paisa(closing_cash) if closing_cash is not None else None
        ),
        actor=current_user,
    )

    # ONE DRAWER, ONE COUNTED FIGURE (audit L3-12). Whatever the shared till
    # session holds after this close linked to it IS the day's count, and this
    # record REPORTS it. Two ways it can differ from the figure typed here:
    # the drawer was ALREADY counted (the blind count is THE close, owner ruling
    # 2026-08-25, and record_screen_close never restates it), or THIS close
    # counted first with a grid and a different typed total (the till's own
    # rule lets the notes rule). Either way storing the typed figure is how one
    # drawer read Rs 1,000 on the Z-Read and Rs 3,000 here. Same rule, same
    # helper, no gate -- exactly as the Finance > Cash Register door
    # (cash_drawer.close_cash_register). The grid typed here is still kept as
    # this screen's sheet; restated against the shared figure it flags itself.
    shared_paisa = till_service.shared_counted_paisa(till_link)
    count_adopted = shared_paisa is not None and (
        closing_cash is None
        or shared_paisa != cash_denom.rupees_to_paisa(closing_cash)
    )
    # What THIS screen sent, kept when it is set aside: a figure someone typed
    # that is not the drawer's count is evidence, not noise.
    entered_cash = closing_cash if count_adopted else None
    if count_adopted:
        closing_cash = cash_denom.paisa_to_rupees(shared_paisa)
        cash_denom.restate_amount(closing_block, shared_paisa)
    # No count -> no variance. A fabricated variance against a fabricated zero
    # is what made this record unreadable. Derived AFTER the adoption above, so
    # it is the variance of the one counted figure for the day.
    #
    # SEVERAL DRAWERS ON THE DAY (D53): closing_cash is ONE drawer's count and
    # system_cash is the whole day's, so their difference is no variance at
    # all (two drawers of Rs 1,000 and Rs 3,000 read "Rs 1,000 short").
    # Drawers are never added together either -- cash carried from one drawer
    # to the next is not recorded. So no whole-day variance is stored; each
    # drawer's own count and variance are (till_drawers).
    several_drawers = int(till_link.get("drawers_on_day") or 1) > 1
    variance = (
        round(closing_cash - system_cash, 2)
        if (closing_cash is not None and system_cash is not None and not several_drawers)
        else None
    )

    now = datetime.utcnow()
    doc = {
        "store_id": sid,
        "date": body.date,
        "closing_cash": closing_cash,
        "system_cash": system_cash,
        "variance": variance,
        "closing_count": closing_block,
        "cash_counted": cash_denom.is_captured(closing_block),
        # The shared record this close is part of.
        "till_session_id": till_link.get("session_id"),
        "till_link_ok": bool(till_link.get("ok")),
        "till_link_error": till_link.get("error"),
        "till_already_counted": bool(till_link.get("already_counted")),
        "till_counted": bool(till_link.get("counted")),
        # The count on this close came from the shared till record, not from
        # what was typed on this screen -- the screen says so out loud.
        "counted_from_shared_record": count_adopted,
        "till_count_differs": bool(till_link.get("count_differs")) or count_adopted,
        # The figure entered on this screen that was NOT used (None when no
        # figure was entered, or when this screen's own count stands).
        "entered_closing_cash": entered_cash,
        # Nobody declared an opening float for this day, so expected cash was
        # computed from zero. The screen must say so rather than present the
        # resulting variance as a real one.
        "till_opening_float_not_recorded": bool(
            till_link.get("opening_float_not_recorded")
        ),
        # Drawers on this shop-day. Above 1, closing_cash is the latest
        # drawer's count only, not the day's total (D40); the screen says so.
        "till_drawers_on_day": till_link.get("drawers_on_day"),
        # D53: each drawer's own count and (once locked) variance, oldest
        # first; None on a one-drawer day or when the list could not be read.
        "till_drawers": till_link.get("drawers") if several_drawers else None,
        "variance_withheld_several_drawers": several_drawers,
        "notes": (body.notes or "").strip() or None,
        "closed_by": current_user.get("user_id"),
        "closed_at": utc_event_iso(now),
    }

    try:
        # ONE CLOSE PER SHOP-DAY, ATOMICALLY. The find_one above is only a
        # fast path: two closes that both read "not closed" used to both
        # insert, leaving two different close records for one day. The key is
        # now the record's _id, which Mongo keeps unique on every collection,
        # so the second insert is refused by the database itself -- no index
        # to build and nothing stored rewritten. Older closes keep their
        # random ids; the find_one above still finds them.
        closes.insert_one({"_id": day_end_close_key(sid, body.date), **doc})
    except Exception:  # pragma: no cover - surfaced as 500 by FastAPI
        # The race loser (or a retry whose first insert landed but whose
        # acknowledgement was lost) lands here; re-read and report the one
        # stored close as a 409 rather than a 500.
        winner = closes.find_one({"store_id": sid, "date": body.date})
        if winner:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": f"Day {body.date} already closed for store {sid}",
                    "close": _day_end_doc_public(winner, current_user),
                },
            )
        logger.exception("Day-end close record failed")
        raise HTTPException(
            status_code=500,
            detail="Could not record the day-end close - try again or contact support",
        )

    # Audit (fail-soft: an audit hiccup must not undo the business record).
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "DAY_END_CLOSED",
                    "entity_type": "day_end_close",
                    "entity_id": f"{sid}:{body.date}",
                    "store_id": sid,
                    "user_id": current_user.get("user_id"),
                    # A day nobody counted has NO variance, so it is not a
                    # variance warning. (``None != 0`` is True in Python, which
                    # would have stamped every uncounted close as a discrepancy.)
                    "severity": "WARNING" if (variance is not None and variance != 0) else "INFO",
                    "details": {
                        "date": body.date,
                        "closing_cash": doc["closing_cash"],
                        "system_cash": doc["system_cash"],
                        "variance": variance,
                        "counted_from_shared_record": count_adopted,
                        "entered_closing_cash": entered_cash,
                    },
                }
            )
    except Exception:
        pass

    return {
        "closed": True,
        "store_id": sid,
        "date": body.date,
        "close": _day_end_doc_public(doc, current_user),
    }
