"""Cash register open / close / sessions (the manual denomination flow).

Moved verbatim out of the 6,604-line api/routers/finance.py (Wave 5
package split): no path, method, dependency, status code, response_model
or default was changed.
"""

from datetime import datetime
from ...utils.ist import ist_date_str
from typing import Any, Optional, List, Dict
from fastapi import Depends, HTTPException, Query
from ..auth import get_current_user
from ...dependencies import validate_store_access
from ...services import cash_register
from ...services.stores_util import is_online_store
from ...services import cash_denominations as cash_denom
from ...services import eod_tally as till_service
from ._shared import _get_db, _iso_now, logger, router
from .cash_drawer_window import (
    CashRegisterClose,
    CashRegisterOpen,
    OFF_TILL_EXPENSE_MESSAGE,
    _CASH_SESSIONS,
    _cash_expenses_for_window,
    _cash_sales_for_window,
    _refund_double_entry_advisory,
    _to_dt,
)


@router.post("/cash-register/open")
async def open_cash_register(
    body: CashRegisterOpen,
    current_user: dict = Depends(get_current_user),
):
    """Open a till session with an opening float counted by denomination.

    Store-scoped (validate_store_access). Blocks a second OPEN session for the
    same store so the drawer can't be opened twice without closing."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    store_id = validate_store_access(body.store_id or "", current_user)
    if not store_id:
        raise HTTPException(status_code=400, detail="No store context for this user")

    # W1.4 / OS-030: an ONLINE store has no cash drawer -- its payments settle
    # through the online payment gateway. Opening a till for it would create a
    # fictitious session in the cash-reconciliation summary.
    if is_online_store(db, store_id):
        raise HTTPException(
            status_code=400,
            detail=(
                "This is an online store - payments settle via the payment "
                "gateway, so there is no cash drawer to open."
            ),
        )

    coll = db.get_collection(_CASH_SESSIONS)

    # Guard: one OPEN session per store at a time.
    existing = None
    try:
        existing = coll.find_one({"store_id": store_id, "status": "OPEN"})
    except Exception:
        existing = None
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "A cash register session is already open for this store. "
                "Close it before opening a new one."
            ),
        )

    denoms = cash_register.normalize_denominations(
        [d.model_dump() for d in body.denominations]
    )
    denom_total = cash_register.total_from_denominations(denoms)
    opening_float = (
        round(float(body.opening_float), 2)
        if body.opening_float is not None
        else denom_total
    )

    now = _iso_now()

    # TWO DOORS, ONE RECORD -- the OPEN half. The float declared here lands on
    # the day's single till session, the same record POS Day-End closes onto.
    # Without it the shared record holds an opening float of ZERO with
    # opening_float_not_recorded set, so expected cash and EVERY per-face
    # expected row are computed from nothing and the note-by-note verdict is
    # withheld for a store that opens on this screen. Fail-soft: linking must
    # never stop a store opening its drawer.
    #
    # NOTHING DECLARED -> NOTHING FORWARDED. A blank grid with no typed float
    # makes `opening_float` 0.0 because it is the sum of nothing; forwarding
    # that would put "the drawer opened with Rs 0.00" on the record all three
    # screens read.
    till_open = till_service.record_screen_open(
        db,
        store_id=store_id,
        # The BUSINESS day, in the same IST frame the close half uses.
        session_date=ist_date_str(_to_dt(now)) or str(now)[:10],
        opening_denominations=denoms,
        opening_count_state=body.opening_count_state,
        opening_float_paisa=(
            cash_denom.rupees_to_paisa(opening_float)
            if (denoms or body.opening_float is not None)
            else None
        ),
        shift=body.shift,
        note=body.note,
        actor=current_user,
    )
    till_session = till_open.get("session") or {}

    session_id = f"CR-{store_id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    doc = {
        "session_id": session_id,
        "store_id": store_id,
        "status": "OPEN",
        "shift": (body.shift or "").upper() or None,
        "opening_float": opening_float,
        # LEGACY SHAPE, unchanged -- every existing reader of this collection
        # keeps working byte-for-byte.
        "opening_denominations": denoms,
        # THE SHARED SHAPE. Same rows, plus the state, so a float nobody
        # counted reads NOT_CAPTURED instead of as an empty drawer.
        "opening_count": cash_denom.build_block(
            denoms,
            cash_denom.rupees_to_paisa(opening_float),
            state=body.opening_count_state,
            actor=current_user,
        ),
        "opened_at": now,
        "opened_by": current_user.get("user_id"),
        "opened_by_name": current_user.get("name"),
        "opening_note": body.note,
        # The shared session this float was declared on, and whether it got
        # there. A failure is stored, not swallowed into a fake success.
        "till_session_id": till_session.get("session_id"),
        "till_link_ok": bool(till_open.get("ok")),
        "till_link_error": till_open.get("error"),
        # True when the day's session already carried a declared float: that
        # one STANDS and this screen's is not the shared one.
        "till_float_already_declared": bool(
            till_open.get("already_open")
            and not till_session.get("opening_float_not_recorded")
            and till_session.get("opening_float_paisa")
            != cash_denom.rupees_to_paisa(opening_float)
        ),
        # close-time fields, filled on /close
        "closed_at": None,
        "closed_by": None,
        "closed_by_name": None,
        "closing_denominations": [],
        "counted": None,
        "expected": None,
        "variance": None,
        "variance_status": None,
    }
    try:
        coll.insert_one(dict(doc))
    except Exception:  # noqa: BLE001
        logger.exception("Cash session open failed")
        raise HTTPException(
            status_code=500,
            detail="Could not open the cash session - try again or contact support",
        )
    doc.pop("_id", None)
    return doc


def _shared_counted_paisa(till_link: Optional[Dict[str, Any]]) -> Optional[int]:
    """The counted figure ON THE SHARED TILL RECORD after a close linked to it,
    or None when nothing was counted there (or the link failed).

    This is the ONE answer for the store-day: whichever door counted the drawer
    first owns it, and both screens must report that same number."""
    session = (till_link or {}).get("session") or {}
    value = session.get("blind_count_paisa")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@router.post("/cash-register/close")
async def close_cash_register(
    body: CashRegisterClose,
    current_user: dict = Depends(get_current_user),
):
    """Close a till session: count the drawer by denomination, compute expected
    vs counted variance, and lock the session.

    Expected = opening float + POS CASH sales for the session window
    - cash refunds - cash expenses - bank deposit."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    coll = db.get_collection(_CASH_SESSIONS)
    session = None
    try:
        session = coll.find_one({"session_id": body.session_id})
    except Exception:
        session = None
    if session is None:
        raise HTTPException(status_code=404, detail="Cash register session not found")

    store_id = validate_store_access(session.get("store_id") or "", current_user)
    if session.get("status") == "CLOSED":
        raise HTTPException(status_code=409, detail="Session already closed")

    start_iso = session.get("opened_at") or _iso_now()
    end_iso = _iso_now()

    # ONE band, from policy (owner ruling 2026-08-25): the SAME store-scopable
    # ``till.variance_tolerance_paisa`` the blind EOD verdict uses -- never a
    # figure the closer types. Rupees at this door's boundary.
    tolerance_rupees = (
        till_service.get_variance_tolerance_paisa(store_id=store_id) / 100.0
    )

    cash_sales, cash_refunds = _cash_sales_for_window(db, store_id, start_iso, end_iso)
    _cash_exp = _cash_expenses_for_window(db, store_id, start_iso, end_iso)
    cash_expenses = _cash_exp.total
    opening_float = float(session.get("opening_float", 0) or 0)

    denoms = cash_register.normalize_denominations(
        [d.model_dump() for d in body.denominations]
    )
    # The sheet exactly as it will be stored, built ONCE here; its amount is
    # restated at the bottom, when the counted figure is final.
    closing_block = cash_denom.build_block(
        denoms, 0, state=body.closing_count_state, actor=current_user
    )
    # NOBODY COUNTED -> NO COUNTED FIGURE. The sum of a blank grid is 0.00 --
    # the sum of nothing -- and persisting it said the drawer WAS counted and
    # found empty: a full-day negative variance and a SHORT verdict against a
    # manager who simply never counted. Blank is an absence, at this door too.
    # `is_captured` is the same authority the shared record uses, so an
    # explicit COUNTED ("counted, and there was none") is still a real zero,
    # and so is a typed override of 0.
    counted_recorded = (
        cash_denom.is_captured(closing_block) or body.counted_override is not None
    )
    counted = (
        (
            round(float(body.counted_override), 2)
            if body.counted_override is not None
            else cash_register.total_from_denominations(denoms)
        )
        if counted_recorded
        else None
    )

    summary = cash_register.build_close_summary(
        opening_float=opening_float,
        cash_sales=cash_sales,
        cash_refunds=cash_refunds,
        cash_expenses=cash_expenses,
        bank_deposit=body.bank_deposit,
        denominations=denoms,
        tolerance=tolerance_rupees,
    )
    # build_close_summary uses the denoms total for counted; honour an override.
    summary["counted"] = counted
    summary["variance"] = (
        None
        if counted is None
        else cash_register.compute_variance(counted, summary["expected"])
    )
    # RECOMPUTE THE VERDICT -- but NEVER resurrect an over/short verdict that
    # build_close_summary deliberately withheld. No count means no variance and
    # so no verdict at all; a negative expected drawer means a cash-in is
    # missing, and re-deriving the status here overwrote NEGATIVE_EXPECTED with
    # a phantom OVERAGE and persisted it next to its own amber note saying the
    # verdict was withheld.
    if counted is None:
        summary["variance_status"] = cash_register.NOT_COUNTED
    elif summary.get("negative_expected_advisory"):
        summary["variance_status"] = cash_register.NEGATIVE_EXPECTED
    else:
        summary["variance_status"] = cash_register.variance_status(
            summary["variance"], tolerance_rupees
        )

    # E5 (ADDITIVE): by-mode reconciliation over the same session window. This
    # does NOT touch the CASH-only variance above (build_close_summary is
    # unchanged) -- it stores a per-tender breakdown alongside it so the close
    # screen / Tally JV can see UPI/CARD/etc. net. Fail-soft: any error leaves
    # the cash close exactly as before.
    # include_returns=True so this breakdown is on the SAME basis as the
    # blind-EOD rows (net of recorded refunds). The manager console renders both
    # in one grid; mixing a payments-only figure with a returns-netted one made
    # the same store/day/tender show two different numbers with no label.
    by_mode_breakdown = None
    try:
        from ...services.tender_reconciliation import reconcile_window

        _recon = reconcile_window(
            db, store_id, start_iso, end_iso, include_returns=True
        )
        by_mode_breakdown = _recon.get("by_mode")
    except Exception:  # noqa: BLE001
        by_mode_breakdown = None

    # Non-blocking, AMOUNT-MATCHED double-count advisory: a manual CASH expense
    # that matches a recorded cash refund to the paisa is probably the same money
    # keyed twice (the pre-fix workaround). Never auto-applied, never blocking.
    refund_double_entry = _refund_double_entry_advisory(
        db, store_id, start_iso, end_iso, cash_refunds
    )

    # TWO DOORS, ONE RECORD: this close and POS Day-End land the SAME counted
    # drawer on the SAME till session for the day, so the two screens can never
    # hold two different answers. The rupee arithmetic above is UNCHANGED --
    # this only links and shares the count. Fail-soft: linking must never stop
    # a store closing its till, and a failure is reported, not hidden.
    till_link = till_service.record_screen_close(
        db,
        store_id=store_id,
        # The BUSINESS day, in the same frame everything else uses. `end_iso`
        # is a NAIVE-UTC instant, so slicing its first ten characters reads
        # the UTC day: a till closed between 00:00 and 05:30 IST would link
        # to YESTERDAY's session while this console files the very same row
        # under today (it already parses-then-shifts, just below). Same
        # helper, same answer.
        session_date=ist_date_str(_to_dt(end_iso)) or str(end_iso)[:10],
        closing_rows=[d.model_dump() for d in body.denominations],
        closing_count_state=body.closing_count_state,
        # The counted figure this screen is storing. It is only forwarded as the
        # count when no grid came with it; with a grid, the notes rule and this
        # is used to notice a `counted_override` that disagrees with them.
        #
        # NOTHING COUNTED -> NOTHING FORWARDED. `counted` is None when nobody
        # counted, and a None never becomes a Rs 0.00 submitted to the shared
        # record -- that is the blank-persisted-as-emptied defect this work
        # removes, and the record all three screens read is the worst place for
        # it. Same test as the figure this screen stores: one answer, not two.
        counted_paisa=(
            cash_denom.rupees_to_paisa(counted) if counted is not None else None
        ),
        actor=current_user,
    )

    # ONE DRAWER, ONE COUNTED FIGURE. When the day was already counted through
    # the other door, THAT count stands (record_screen_close never overwrites a
    # signed-off count) -- so this screen must REPORT it rather than its own.
    # Reporting its own is how one drawer on one day came to read Rs 2,000 on
    # Finance > Cash Register and Rs 3,000 on the Z-Read. Only the COUNT is
    # shared; the expected figure below is still this window's own arithmetic.
    # The grid typed here is still stored as this screen's sheet, and a sheet
    # that disagrees with the shared count flags itself (matches_amount False).
    shared_paisa = _shared_counted_paisa(till_link)
    count_adopted = shared_paisa is not None and (
        counted is None or shared_paisa != cash_denom.rupees_to_paisa(counted)
    )
    if count_adopted:
        counted = cash_denom.paisa_to_rupees(shared_paisa)
        summary["counted"] = counted
        summary["variance"] = cash_register.compute_variance(
            counted, summary["expected"]
        )
        # There IS a count now, so the withheld NOT_COUNTED verdict must not
        # survive -- only NEGATIVE_EXPECTED still outranks the arithmetic.
        summary["variance_status"] = (
            cash_register.NEGATIVE_EXPECTED
            if summary.get("negative_expected_advisory")
            else cash_register.variance_status(summary["variance"], tolerance_rupees)
        )

    # MANDATORY NOTE ABOVE THE BAND (owner ruling 2026-08-25). Checked AFTER
    # the shared-record adoption so the figure judged is the ONE counted figure
    # for the day. Same rule, same helper as the blind Z-Read lock. Refusing
    # here is retry-safe: the count already landed on the shared till record,
    # and a resubmit with the note adopts it back (already_counted) unchanged.
    close_note = (body.note or "").strip()
    variance_out_of_band = till_service.needs_variance_note(
        (
            cash_denom.rupees_to_paisa(summary["variance"])
            if summary["variance"] is not None
            else None
        ),
        cash_denom.rupees_to_paisa(tolerance_rupees),
    )
    if variance_out_of_band and not close_note:
        raise HTTPException(
            status_code=400,
            detail=(
                "variance_note_required: the drawer is over/short beyond the "
                f"allowed band of Rs {tolerance_rupees:.0f} - a note explaining "
                "the variance is required to close."
            ),
        )

    update = {
        "status": "CLOSED",
        "closed_at": end_iso,
        "closed_by": current_user.get("user_id"),
        "closed_by_name": current_user.get("name"),
        "closing_denominations": denoms,
        # The shared Cash Count Block + the session it belongs to. The block
        # was built above; this points it at the figure actually being stored.
        "closing_count": cash_denom.restate_amount(
            closing_block, cash_denom.rupees_to_paisa(counted)
        ),
        "till_session_id": till_link.get("session_id"),
        "till_link_ok": bool(till_link.get("ok")),
        "till_link_error": till_link.get("error"),
        "till_already_counted": bool(till_link.get("already_counted")),
        "till_counted": bool(till_link.get("counted")),
        "till_opening_float_not_recorded": bool(
            till_link.get("opening_float_not_recorded")
        ),
        # True when this screen's counted figure and the shared record's differ
        # -- a manual override typed over the notes, or a drawer the other door
        # had already counted. Either way the shared figure is what is stored
        # above, and this flag is how the screen says so out loud.
        "till_count_differs": bool(till_link.get("count_differs")) or count_adopted,
        # The count on this close came from the shared record, not from the
        # grid on this screen.
        "counted_from_shared_record": count_adopted,
        "cash_sales": cash_sales,
        "cash_refunds": cash_refunds,
        "cash_expenses": cash_expenses,
        "refund_double_entry_advisory": refund_double_entry,
        # Salaries / advances / PF-ESI are never paid from a till (owner
        # 2026-08-14), so they are OUT of `cash_expenses` above. Say so on the
        # record: an expected-cash figure that quietly leaves something out is
        # exactly the "screen stating something the system knows is not true"
        # that PR #960 was written to kill. Count only -- never the amount.
        "off_till_expense_advisory": bool(_cash_exp.excluded_count),
        "off_till_expense_message": (
            OFF_TILL_EXPENSE_MESSAGE if _cash_exp.excluded_count else None
        ),
        "negative_expected_advisory": summary.get("negative_expected_advisory", False),
        "negative_expected_message": summary.get("negative_expected_message"),
        # by_mode_breakdown is NET OF RECORDED REFUNDS (same basis as the
        # blind-EOD rows) so the manager console never shows two definitions of
        # the same tender figure in one grid.
        "by_mode_breakdown": by_mode_breakdown,
        "by_mode_basis": "NET_OF_RECORDED_REFUNDS",
        "bank_deposit": summary["bank_deposit"],
        "counted": counted,
        "expected": summary["expected"],
        "variance": summary["variance"],
        "variance_status": summary["variance_status"],
        "tolerance": summary["tolerance"],
        "closing_note": body.note,
    }
    try:
        coll.update_one({"session_id": body.session_id}, {"$set": update})
    except Exception:  # noqa: BLE001
        logger.exception("Cash session close failed")
        raise HTTPException(
            status_code=500,
            detail="Could not close the cash session - try again or contact support",
        )

    # Manager alert above the band (owner ruling 2026-08-25). SAME helper and
    # SAME (store, day) dedupe as the blind Z-Read lock, so the two doors raise
    # ONE task for one drawer-day. Fail-soft inside the helper.
    if variance_out_of_band:
        till_service.raise_variance_task(
            store_id=store_id,
            session_date=ist_date_str(_to_dt(end_iso)) or str(end_iso)[:10],
            variance_paisa=cash_denom.rupees_to_paisa(summary["variance"]),
            tolerance_paisa=cash_denom.rupees_to_paisa(tolerance_rupees),
            note=close_note,
            source="Finance cash-register close",
        )

    merged = dict(session)
    merged.update(update)
    merged.pop("_id", None)
    return merged


@router.get("/cash-register/sessions")
async def list_cash_register_sessions(
    store_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="OPEN / CLOSED"),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
):
    """Cash register session history, store-scoped, newest first.

    Also returns the live `open_session` (if any) and an `expected_preview` for
    it so the close screen can show the running expected figure before a count
    is entered."""
    db = _get_db()
    if db is None:
        return {"sessions": [], "open_session": None, "expected_preview": None}

    scoped_store = validate_store_access(store_id or "", current_user)
    coll = db.get_collection(_CASH_SESSIONS)

    match: Dict = {}
    if scoped_store:
        match["store_id"] = scoped_store
    elif store_id:
        match["store_id"] = store_id
    if status:
        match["status"] = status.upper()

    sessions: List[dict] = []
    try:
        cursor = coll.find(match, {"_id": 0}).sort("opened_at", -1).limit(limit)
        sessions = list(cursor)
    except Exception:
        sessions = []

    # Surface the currently-open session + a running expected preview.
    open_session = None
    expected_preview = None
    try:
        open_match = {"status": "OPEN"}
        if scoped_store:
            open_match["store_id"] = scoped_store
        elif store_id:
            open_match["store_id"] = store_id
        open_session = coll.find_one(open_match, {"_id": 0})
    except Exception:
        open_session = None

    if open_session is not None:
        os_store = open_session.get("store_id")
        start_iso = open_session.get("opened_at") or _iso_now()
        cash_sales, cash_refunds = _cash_sales_for_window(db, os_store, start_iso, None)
        _cash_exp = _cash_expenses_for_window(db, os_store, start_iso, None)
        cash_expenses = _cash_exp.total
        opening_float = float(open_session.get("opening_float", 0) or 0)
        expected = cash_register.compute_expected_cash(
            opening_float, cash_sales, cash_refunds, cash_expenses, 0.0
        )
        expected_preview = {
            "opening_float": round(opening_float, 2),
            "cash_sales": cash_sales,
            "cash_refunds": cash_refunds,
            "cash_expenses": cash_expenses,
            "bank_deposit": 0.0,
            "expected": expected,
            # AMOUNT-MATCHED advisory (or None): a manual cash expense that
            # matches a recorded cash refund is probably the same money twice.
            "refund_double_entry_advisory": _refund_double_entry_advisory(
                db, os_store, start_iso, None, cash_refunds
            ),
            # Something booked this period is not paid from the till, so it is
            # not in `cash_expenses` and not in `expected`. Count only, never
            # the amount -- see OFF_TILL_EXPENSE_MESSAGE.
            "off_till_expense_advisory": bool(_cash_exp.excluded_count),
            "off_till_expense_message": (
                OFF_TILL_EXPENSE_MESSAGE if _cash_exp.excluded_count else None
            ),
            # A negative expectation means a cash-in is missing -- never present
            # the resulting "overage" as a verdict.
            "negative_expected_advisory": expected < 0,
            "negative_expected_message": (
                cash_register.NEGATIVE_EXPECTED_MESSAGE if expected < 0 else None
            ),
        }

    return {
        "sessions": sessions,
        "open_session": open_session,
        "expected_preview": expected_preview,
    }
