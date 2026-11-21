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
from ...dependencies import store_in_view, validate_store_access
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

    # Guard: one OPEN session per store at a time. A guard read that FAILED
    # refuses the open: read as "none open", it let a second session open
    # beside the one already open.
    try:
        existing = coll.find_one({"store_id": store_id, "status": "OPEN"})
    except Exception:  # noqa: BLE001
        logger.exception("Cash register open: guard read failed for %s", store_id)
        raise HTTPException(status_code=503, detail=_REGISTER_READ_FAILED)
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


_REGISTER_READ_FAILED = (
    "The cash register could not be read just now. Nothing was changed. "
    "Try again in a moment."
)


def _already_closed(coll, stored: dict):
    """A close sent for a session that is ALREADY closed. The saved close
    stands and goes back with the 409, so the screen shows what was saved
    instead of a bare refusal (same shape as Day-End's conflict).

    This is also where a close whose reply was lost is finished (Codex
    F-1421-R3-A): the write committed, the request answered an error, and the
    manager task the close owed was never raised. The retry lands here and
    raises it from the saved record -- once, however often it is sent."""
    till_service.settle_followup(
        coll, {"session_id": stored.get("session_id")}, stored
    )
    # D110: so are the audit rows the till drawer it opened or counted owes.
    till_service.settle_drawers(_get_db(), [stored.get("till_session_id")])
    public = dict(stored)
    public.pop("_id", None)
    raise HTTPException(
        status_code=409,
        detail={"message": "Session already closed", "session": public},
    )


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
    try:
        session = coll.find_one({"session_id": body.session_id})
    except Exception:  # noqa: BLE001
        # Unreadable is not absent: "not found" sent staff looking for a
        # session that exists.
        logger.exception("Cash register close: session read failed for %s", body.session_id)
        raise HTTPException(status_code=503, detail=_REGISTER_READ_FAILED)
    if session is None:
        raise HTTPException(status_code=404, detail="Cash register session not found")

    store_id = validate_store_access(session.get("store_id") or "", current_user)
    if session.get("status") == "CLOSED":
        _already_closed(coll, session)

    start_iso = session.get("opened_at") or _iso_now()
    end_iso = _iso_now()

    # ONE band, from policy (owner ruling 2026-08-25): the SAME store-scopable
    # ``till.variance_tolerance_paisa`` the blind EOD verdict uses -- never a
    # figure the closer types. Rupees at this door's boundary.
    tolerance_rupees = (
        till_service.get_variance_tolerance_paisa(store_id=store_id) / 100.0
    )

    # A failed sales / refunds / expenses read is REFUSED before anything is
    # written: answering zero stored a false variance, demanded a note and
    # alerted the manager for a drawer that balanced (D53). The session stays
    # OPEN and the same close can be sent again.
    def _strict(read):
        try:
            return read()
        except Exception:  # noqa: BLE001
            logger.exception("Cash register close: cash read failed for %s", body.session_id)
            raise HTTPException(
                status_code=503,
                detail=(
                    "The day's cash sales or expenses could not be read, so the "
                    "register was not closed. Try again in a moment."
                ),
            )

    cash_sales, cash_refunds = _strict(
        lambda: _cash_sales_for_window(db, store_id, start_iso, end_iso, strict=True)
    )
    _cash_exp = _strict(
        lambda: _cash_expenses_for_window(db, store_id, start_iso, end_iso, strict=True)
    )
    cash_expenses = _cash_exp.total
    off_till_excluded = _cash_exp.excluded_count
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

    # TWO DOORS, ONE RECORD: this close and POS Day-End land the SAME counted
    # drawer on the SAME till session for the day, so the two screens can never
    # hold two different answers. The rupee arithmetic is UNCHANGED --
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
        # A count this close lands on the till is marked as THIS session's
        # (D120 tells it from a count made through another door).
        origin=body.session_id,
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
    shared_paisa = till_service.shared_counted_paisa(till_link)

    # A DRAWER COUNTED BY ANYTHING OTHER THAN THIS CLOSE (D120, which replaces
    # D111's formula). The close is judged on ITS OWN sheet against the most
    # recent such count plus the cash moved since it: the till's count (the
    # Blind EOD Tally, Day-End or an earlier register's close -- standing when
    # this session opened or made while it was open), or a later sheet of an
    # earlier register session. Comparing the EARLIER count with the LATER
    # cash stored a shortage that did not exist, demanded a note for it and
    # alerted the manager (Rs 3,000 counted, Rs 500 taken after: Rs 3,500 in
    # hand read Rs 500 short). The till's count is not replaced (L3-12).
    # A count THIS close landed itself (refused for its note and sent again)
    # is not such a count: count_by_another tells them apart by who made it.
    # Where the close starts and the cash since it are ONE rule (D127,
    # ``eod_tally.standing_count``): the drawer's latest count by any door --
    # a later sheet of an earlier register session included -- less a bank
    # deposit recorded after it on a close with no sheet, plus the day so
    # far minus what every count of the day was charged with (D60).
    standing = till_service.count_by_another(till_link, _to_dt(start_iso), body.session_id)
    moved = None
    banked_since = 0
    count_noted = False
    drawer_unknown = False
    if standing is not None:
        reading = _strict(
            lambda: till_service.standing_count(db, till_link["session"], body.session_id)
        )
        standing = {"paisa": reading["paisa"], "at": reading["at"]}
        banked_since = reading["banked_paisa"]
        count_noted = reading["count_noted"]
        drawer_unknown = reading["unknown"]
        start_iso = standing["at"].isoformat()
        opening_float = cash_denom.paisa_to_rupees(standing["paisa"])
        moved = reading["moved"]
        if moved is not None:
            cash_sales = cash_denom.paisa_to_rupees(moved["cash_sales_paisa"])
            cash_refunds = cash_denom.paisa_to_rupees(moved["cash_refunds_paisa"])
            cash_expenses = cash_denom.paisa_to_rupees(moved["cash_payouts_paisa"])
            off_till_excluded = moved["off_till_excluded"]

    # Why this close may not judge its count against its own window (D53),
    # or None. Not for a D120 session: its opening is ONE drawer's count and
    # its cash is what moved after every count of the day, so no other
    # drawer's cash or payouts are in its figures.
    withheld = (
        None if standing is not None else till_service.withheld_comparison(till_link)
    )

    # THIS SESSION'S OWN EARLIER COUNT, AND A NEW SHEET (D120: "Counted = this
    # close's own sheet"). A close that counted the drawer and was refused
    # for its note, or whose closing write failed, left its count on the
    # till. Sent again with the SAME sheet it is judged exactly as before.
    # Sent again with ANOTHER sheet (cash was taken in between and the drawer
    # recounted), the old sheet was adopted and compared with the longer
    # window: Rs 2,700 counted, Rs 500 taken after, Rs 3,200 in hand read
    # Rs 800 short instead of Rs 300, and a close that had balanced read
    # Rs 500 short. The new sheet is judged against the float plus the
    # window. The till's first count is not replaced (L3-12), and if that
    # count was itself beyond the band its note is still required below.
    # Several drawers on the day: nothing is compared there, as before.
    sheet = cash_register.total_from_denominations(denoms) if denoms else counted
    resheeted = bool(
        standing is None
        and withheld is None
        and till_link.get("already_counted")
        and (till_link.get("session") or {}).get("blind_count_origin") == body.session_id
        and sheet is not None
        and shared_paisa is not None
        and shared_paisa != cash_denom.rupees_to_paisa(sheet)
    )
    since_count = None
    if resheeted:
        # The figure this sheet would have put on the till: the notes when
        # there are notes (as on the first attempt).
        counted = sheet
        # This sheet is now the drawer's LATEST count, so the next session
        # starts from it (D120) and must take off the cash it holds beyond
        # the till's count: the day so far minus what the counted drawers
        # and any later sheet were charged with. Stored with the close
        # (``_later_closes`` reads it). Unreadable = refused, nothing stored.
        since_count = _strict(
            lambda: till_service.standing_count(db, till_link["session"], body.session_id)
        )["moved"]

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
    if moved is not None:
        # The D111 session's tenders on the same basis as its cash above.
        by_mode_breakdown = moved["by_mode"]

    # Non-blocking, AMOUNT-MATCHED double-count advisory: a manual CASH expense
    # that matches a recorded cash refund to the paisa is probably the same money
    # keyed twice (the pre-fix workaround). Never auto-applied, never blocking.
    refund_double_entry = _refund_double_entry_advisory(
        db, store_id, start_iso, end_iso, cash_refunds
    )

    count_adopted = shared_paisa is not None and not resheeted and (
        counted is None or shared_paisa != cash_denom.rupees_to_paisa(counted)
    )
    if standing is not None:
        # No sheet of its own: the till's count is shown, and NOTHING is
        # compared with it (no variance, so no note and no alert below).
        count_adopted = counted is None
        if count_adopted:
            counted = cash_denom.paisa_to_rupees(shared_paisa)
            summary["counted"] = counted
            summary["variance_status"] = cash_register.COUNTED_EARLIER
    elif count_adopted:
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

    # SEVERAL DRAWERS ON THE DAY (D53): `counted` is ONE drawer's count and
    # `expected` is this window's arithmetic, so no single variance or
    # "balanced" verdict is true for the day, and drawers are never added
    # together. Withhold it; the record carries each drawer's own count and
    # variance (till_drawers).
    # The same withholding when the link failed before the day's drawers could
    # be read: a whole-window comparison there could be a false shortage.
    # Not for a D120 session (see ``withheld`` above).
    several_drawers = withheld is not None
    # A D111 session whose cash since the count is not known (a counted
    # drawer of the day holds an older-code snapshot, D76), or after a close
    # IMS could not place on one drawer (D127(2)): nothing is compared and
    # its window figures are not shown as its own (D86(3)).
    uncompared = standing is not None and (moved is None or drawer_unknown)
    if several_drawers or uncompared:
        summary["variance"] = None
        summary["variance_status"] = withheld or cash_register.COMPARISON_UNAVAILABLE

    # WHAT THE BAND JUDGES. One drawer: the one counted figure for the day.
    # Several drawers (D53): no whole-day figure, so the drawer THIS close
    # counted is judged on ITS OWN variance (stored by the till at its blind
    # count) -- unless a manager already LOCKED it, in which case its lock
    # already took the note and raised the alert. Without this the note and
    # the manager alert (owner ruling 2026-08-25) waited for a lock that might
    # never come, and a shortage outside the band raised nothing.
    judged_variance_paisa = (
        cash_denom.rupees_to_paisa(summary["variance"])
        if summary["variance"] is not None
        else None
    )
    judged_band_paisa = cash_denom.rupees_to_paisa(tolerance_rupees)
    if several_drawers:
        judged_variance_paisa, drawer_band = till_service.unlocked_count_variance(
            till_link.get("session")
        )
        if drawer_band is not None:
            judged_band_paisa = drawer_band

    # THE DRAWER'S OWN COUNT, BEYOND THE BAND AND NEVER EXPLAINED (owner
    # ruling 2026-08-25). A D120 close starts from a count made through
    # another door, whose note waits for the Z-Read lock. D128(1): that
    # count's difference is explained ONCE, by the door that owns it, so
    # this close asks NO note for it: it shows it (``till_count_variance``)
    # and owes the count's alert under the count's own id (the lock's,
    # insert-once), so a shortage nobody locked still reaches the manager
    # and a later lock raises nothing more. Its own figures stay as D120
    # computes them. Not when the drawer is LOCKED, not when the count was
    # made by another register's close (noted there), not when an earlier
    # close on this drawer already met it (``count_noted``). This session's
    # OWN refused count, when a new sheet is sent (above), is its own door's
    # count: that one still needs the note here, so a sheet retyped to
    # balance does not escape it.
    count_owed = None
    if (standing is not None and not count_noted) or resheeted:
        count_owed = till_service.count_awaiting_its_note(
            till_link.get("session"), body.session_id, judged_band_paisa
        )
    count_needs_note = count_owed is not None and resheeted

    # MANDATORY NOTE ABOVE THE BAND (owner ruling 2026-08-25). Checked AFTER
    # the shared-record adoption so the figure judged is the ONE counted figure
    # for the day. Same rule, same helper as the blind Z-Read lock. Refusing
    # here is retry-safe: the count already landed on the shared till record,
    # and a resubmit with the note adopts it back (already_counted) unchanged.
    close_note = (body.note or "").strip()
    variance_out_of_band = till_service.needs_variance_note(
        judged_variance_paisa, judged_band_paisa
    )
    if variance_out_of_band and not close_note:
        raise HTTPException(
            status_code=400,
            detail=(
                "variance_note_required: the drawer is over/short beyond the "
                f"allowed band of Rs {judged_band_paisa / 100:.0f} - a note "
                "explaining the variance is required to close."
            ),
        )
    if count_needs_note and not close_note:
        raise HTTPException(
            status_code=400,
            detail=(
                "variance_note_required: this drawer's earlier count today is "
                "over/short beyond the allowed band of Rs "
                f"{count_owed['tolerance_paisa'] / 100:.0f} and has not been "
                "explained yet - a note explaining it is required to close."
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
        # The drawer this close is on. When the close could not read the till
        # it keeps the drawer it OPENED on (D127(2)): a close that names no
        # drawer cannot be placed, and its bank deposit then reached no later
        # session (a false shortage on the next close).
        "till_session_id": till_link.get("session_id") or session.get("till_session_id"),
        "till_link_ok": bool(till_link.get("ok")),
        "till_link_error": till_link.get("error"),
        "till_already_counted": bool(till_link.get("already_counted")),
        "till_counted": bool(till_link.get("counted")),
        "till_opening_float_not_recorded": bool(
            till_link.get("opening_float_not_recorded")
        ),
        # Drawers on this shop-day. Above 1, `counted` is the latest drawer's
        # count only, not the day's total (D40); the screen says so.
        "till_drawers_on_day": till_link.get("drawers_on_day"),
        # D53: each drawer's own count and (once locked) variance.
        "till_drawers": till_link.get("drawers") if several_drawers else None,
        # True when this screen's counted figure and the shared record's differ
        # -- a manual override typed over the notes, or a drawer the other door
        # had already counted. Either way the shared figure is what is stored
        # above, and this flag is how the screen says so out loud.
        "till_count_differs": (
            bool(till_link.get("count_differs")) or count_adopted or resheeted
        ),
        # The count on this close came from the shared record, not from the
        # grid on this screen.
        "counted_from_shared_record": count_adopted,
        # D120: the drawer was counted by something other than this close.
        # Its expected cash starts from the latest such count (not the float
        # typed at opening), less any bank deposit recorded after it on a
        # close with no sheet, and covers the cash moved since it.
        "till_counted_earlier": standing is not None,
        "till_standing_count": opening_float if standing is not None else None,
        "till_banked_since_count": (
            cash_denom.paisa_to_rupees(banked_since) if standing is not None else None
        ),
        "till_standing_counted_at": start_iso if standing is not None else None,
        # The drawer's own earlier count was beyond the band and unexplained:
        # shown here and its alert owed by this close (D128(1)); noted here
        # only when it is this session's own refused count. The difference
        # of THAT count (rupees), not of this sheet.
        "till_count_variance": (
            cash_denom.paisa_to_rupees(count_owed["variance_paisa"])
            if count_owed is not None
            else None
        ),
        # A new sheet stored over this session's own first count: the cash it
        # was charged with after that count, for the next session (D120).
        "till_since_count": (
            {
                k: since_count[k]
                for k in ("cash_sales_paisa", "cash_refunds_paisa", "cash_payouts_paisa", "by_mode")
            }
            if since_count is not None
            else None
        ),
        "cash_sales": cash_sales,
        "cash_refunds": cash_refunds,
        "cash_expenses": cash_expenses,
        "refund_double_entry_advisory": refund_double_entry,
        # Salaries / advances / PF-ESI are never paid from a till (owner
        # 2026-08-14), so they are OUT of `cash_expenses` above. Say so on the
        # record: an expected-cash figure that quietly leaves something out is
        # exactly the "screen stating something the system knows is not true"
        # that PR #960 was written to kill. Count only -- never the amount.
        "off_till_expense_advisory": bool(off_till_excluded),
        "off_till_expense_message": (
            OFF_TILL_EXPENSE_MESSAGE if off_till_excluded else None
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
        # Why nothing was compared, when the reason is not the status itself.
        "comparison_withheld_reason": (
            till_service.EARLIER_CLOSE_DRAWER_UNKNOWN if uncompared and drawer_unknown else None
        ),
        "comparison_withheld_message": (
            till_service.EARLIER_CLOSE_DRAWER_UNKNOWN_MESSAGE
            if uncompared and drawer_unknown
            else None
        ),
    }
    # WHAT THIS CLOSE OWES THE MANAGER, stored IN the close itself so it cannot
    # be lost between the write below and the lines after it (a lost reply, a
    # stopped process): see eod_tally.settle_followup.
    #  * the alert above the band (owner ruling 2026-08-25): SAME helper and
    #    SAME per-drawer dedupe (D63) as the blind Z-Read lock, so the two
    #    doors raise ONE task for one drawer, and another drawer of the day
    #    out of band raises its own;
    #  * the count did not reach the till: that drawer is still OPEN there and
    #    nothing has checked it, so the manager is told to count it on the
    #    Z-Read.
    close_day = ist_date_str(_to_dt(end_iso)) or str(end_iso)[:10]
    # One alert per COUNT (D63, D128(1)). When the figure judged here IS the
    # drawer's till count (this close made it, adopted it, or -- several
    # drawers -- judged that drawer's own variance) the alert is the count's:
    # the drawer's key and the count's fixed id, the one the Z-Read lock uses,
    # so the two doors raise ONE task for it, ever. A D120 session's sheet or
    # a new sheet over its own first count is a count of its own: its own key
    # and id, so an alert for the earlier count does not swallow it.
    drawer_id = till_link.get("session_id")
    judged_the_drawers_count = standing is None and not resheeted and bool(drawer_id)
    own_alert_key = drawer_id if judged_the_drawers_count else (
        body.session_id if standing is not None or resheeted else drawer_id
    )
    own_task_id = (
        till_service.count_task_id(drawer_id)
        if judged_the_drawers_count
        else till_service.followup_task_id("variance", body.session_id)
    )
    update[till_service.FOLLOWUP_FIELD] = till_service.owed_followup(
        {
            "kind": "variance",
            "store_id": store_id,
            "session_date": close_day,
            "variance_paisa": judged_variance_paisa,
            "tolerance_paisa": judged_band_paisa,
            "note": close_note,
            "source": "Finance cash-register close",
            "session_id": own_alert_key,
            "task_id": own_task_id,
        }
        if variance_out_of_band
        else None,
        # The drawer's own unexplained count, under the drawer's key and day
        # and the COUNT's fixed id (the Z-Read lock's): insert-once.
        {
            "kind": "variance",
            "store_id": store_id,
            "session_date": (till_link.get("session") or {}).get("session_date") or close_day,
            "variance_paisa": count_owed["variance_paisa"],
            "tolerance_paisa": count_owed["tolerance_paisa"],
            "note": close_note,
            "source": "drawer's earlier count, seen unexplained at the Finance cash-register close",
            "session_id": drawer_id,
            "task_id": till_service.count_task_id(drawer_id),
        }
        if count_owed is not None
        and not (variance_out_of_band and own_task_id == till_service.count_task_id(drawer_id))
        else None,
        {
            "kind": "uncounted",
            "store_id": store_id,
            "session_date": close_day,
            "source": "Finance cash-register close",
            "task_id": till_service.followup_task_id("uncounted", body.session_id),
        }
        if counted is not None and not till_link.get("ok")
        else None,
        # A sheet nothing could judge is checked by hand (as on the till, D76).
        # Not after a close IMS could not place on one drawer: D127(2) asks
        # no note and raises no alert for a difference it cannot prove.
        {
            "kind": "uncompared",
            "store_id": store_id,
            "session_date": close_day,
            "session_id": body.session_id,
            "task_id": till_service.followup_task_id("uncompared", body.session_id),
        }
        if uncompared and not count_adopted and not drawer_unknown
        else None,
    )
    # Guarded on "not yet CLOSED": of two closes sent at once only one lands,
    # so the second can neither overwrite the first record nor raise a second
    # alert; it is told the session is already closed and shown the saved one.
    try:
        landed = coll.update_one(
            {"session_id": body.session_id, "status": {"$ne": "CLOSED"}},
            {"$set": update},
        )
    except Exception:  # noqa: BLE001
        logger.exception("Cash session close failed")
        # The write may have COMMITTED with only its acknowledgement lost.
        # Read it back: this very close stored -> carry on as saved; another
        # close stored -> that one stands; still open or unreadable -> say so,
        # and the same close can be sent again.
        try:
            stored = coll.find_one({"session_id": body.session_id})
        except Exception:  # noqa: BLE001
            stored = None
        if (stored or {}).get("status") != "CLOSED":
            raise HTTPException(
                status_code=500,
                detail="Could not close the cash session - try again or contact support",
            )
        if (
            stored.get("closed_at") != end_iso
            or stored.get("closed_by") != current_user.get("user_id")
        ):
            _already_closed(coll, stored)
        landed = None
    if landed is not None and getattr(landed, "matched_count", 1) == 0:
        try:
            stored = coll.find_one({"session_id": body.session_id})
        except Exception:  # noqa: BLE001
            stored = None
        if stored is not None:
            _already_closed(coll, stored)
        raise HTTPException(
            status_code=409, detail={"message": "Session already closed"}
        )

    merged = dict(session)
    merged.update(update)
    merged.pop("_id", None)
    # Raise what the close owes, now. If a task cannot be stored the close
    # stays saved and stays owing: the next retry or read of this screen
    # raises it (manager_followup.pending says which).
    till_service.settle_followup(coll, {"session_id": body.session_id}, merged)
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
    is entered.

    The shop is the one store-level rule (store_in_view): a named shop must be
    the caller's; none named, the active shop. A non-admin login with NO shop
    reads none (owner ruling 2026-10-12, R4) -- it used to read every shop's
    sessions and open till."""
    db = _get_db()
    if db is None:
        return {"sessions": [], "open_session": None, "expected_preview": None}

    scoped_store = store_in_view(store_id, current_user, one_shop=True)
    coll = db.get_collection(_CASH_SESSIONS)

    match: Dict = {}
    if scoped_store:
        match["store_id"] = scoped_store
    if status:
        match["status"] = status.upper()

    # A read that FAILED is said so (503). It used to answer an empty history
    # and "no session open": the screen then offered to open a till that was
    # already open and showed a shop with no closes.
    expected_preview = None
    try:
        cursor = coll.find(match, {"_id": 0}).sort("opened_at", -1).limit(limit)
        sessions: List[dict] = list(cursor)
        # Surface the currently-open session + a running expected preview.
        open_match = {"status": "OPEN"}
        if scoped_store:
            open_match["store_id"] = scoped_store
        open_session = coll.find_one(open_match, {"_id": 0})
    except Exception:  # noqa: BLE001
        logger.exception("Cash register sessions read failed")
        raise HTTPException(status_code=503, detail=_REGISTER_READ_FAILED)

    # A close that still owes the manager a task (its reply was lost, or the
    # process stopped before raising it) is finished here: once the session
    # shows as closed this screen offers no retry.
    for row in sessions:
        till_service.settle_followup(
            coll, {"session_id": row.get("session_id")}, row
        )
    # D110: and what the till drawers these sessions opened or counted owe.
    till_service.settle_drawers(db, [row.get("till_session_id") for row in sessions])

    if open_session is not None:
        os_store = open_session.get("store_id")
        start_iso = open_session.get("opened_at") or _iso_now()
        try:
            cash_sales, cash_refunds = _cash_sales_for_window(
                db, os_store, start_iso, None, strict=True
            )
            _cash_exp = _cash_expenses_for_window(
                db, os_store, start_iso, None, strict=True
            )
        except Exception:  # noqa: BLE001
            # No preview rather than one computed from zero sales: its
            # advisories (a negative expected drawer) would be false.
            logger.exception("Cash register preview read failed for %s", os_store)
        else:
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
