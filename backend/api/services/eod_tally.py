"""
IMS 2.0 - F23 Blind EOD cash tally & Z-Read (transparent soft-lock)
===================================================================
A BLIND end-of-day cash count: the cashier enters the physically-counted cash
WITHOUT first seeing the system-expected figure (blind = no anchoring), then the
system reveals expected-vs-counted variance per tender, produces a Z-Read (the
classic POS day-close report: opening float, sales by tender, payouts, expected
close, counted, variance), and SOFT-LOCKS the day -- a TRANSPARENT lock that
records who/when; not an immutable hard lock. A manager can REOPEN with a reason
(audited).

REUSE, DO NOT FORK (this builds on the MERGED E5 engine):
  * The CASH-expected figure + the per-tender by-mode breakdown come from
    ``tender_reconciliation.reconcile_window`` (reads ``order.payments[]``; POS
    capture is UNCHANGED). There is NO new orders aggregation here.
  * The soft-lock is the SAME concurrency-safe shape as E5's
    ``lock_reconciliation``: a single guarded ``find_one_and_update`` on a status
    field flips the doc; two concurrent locks -> exactly one wins (the loser sees
    the doc no longer in the lockable state). Unlike E5's HARD lock, this one is
    REOPENABLE by a manager (audited) -- the soft-lock pattern from SYSTEM_INTENT.

Standalone Mongo: every write here touches ONE document in ONE collection
(``till_sessions``). There is no cross-collection "atomic" write. Audit goes
through ``AuditRepository.create`` (the hash-chained facade) -- NEVER
``append_audit_entry`` directly.

Config (variance tolerance, who-can-reopen) is read via E2 ``get_policy`` with
safe code defaults so a fresh DB behaves correctly.

No emoji (Windows cp1252).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pymongo.errors import DuplicateKeyError

from . import cash_denominations as _denom

_SESSIONS_COLLECTION = "till_sessions"

# Status lifecycle (the blind state machine):
#   OPEN            -> opening float declared; expected NOT computed/revealed
#   BLIND_SUBMITTED -> cashier posted the count; expected+variance computed +
#                      STORED but hidden from the cashier
#   LOCKED          -> manager revealed variance and soft-locked the day (Z-Read)
# A LOCKED session may be REOPENED (-> BLIND_SUBMITTED) by an authorized role
# with a mandatory reason; the reopen is audited. This is the transparent
# soft-lock (records who/when), NOT an immutable freeze.
STATUS_OPEN = "OPEN"
STATUS_BLIND_SUBMITTED = "BLIND_SUBMITTED"
STATUS_LOCKED = "LOCKED"

# Variance verdict used INSTEAD of OVER/SHORT/BALANCED when the expected drawer
# computes NEGATIVE -- cash left the drawer that it never took in (a refund
# funded from the safe; IMS has no cash-in concept). Reporting the arithmetic
# "overage" there would credit the cashier with money they never held. The raw
# figures are still stored and shown; only the VERDICT is withheld.
NEGATIVE_EXPECTED = "NEGATIVE_EXPECTED"
NEGATIVE_EXPECTED_MESSAGE = (
    "More cash was refunded than this drawer took in - a cash-in is missing "
    "(e.g. a refund funded from the safe). Record the cash-in before trusting "
    "this variance."
)

# Indian denomination ladder (paisa-exact) -- defined ONCE in
# services/cash_denominations.py and re-exported here so the till module has no
# second copy of the face values to drift from.
NOTE_FACES = _denom.NOTE_FACES
COIN_FACES = _denom.COIN_FACES

# E2 policy keys (registered in policy_registry.py). Defaults here mirror the
# registry so a direct service call (no policy doc) still behaves.
POLICY_TOLERANCE = "till.variance_tolerance_paisa"
POLICY_REOPEN_ROLES = "till.reopen_roles"

# Rs 100 (owner ruling 2026-08-25) -- mirrors the registry default in
# policy_registry.py; change both together.
_DEFAULT_TOLERANCE_PAISA = 10000
_DEFAULT_REOPEN_ROLES = ("SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER")


# ---------------------------------------------------------------------------
# Money + denomination helpers (pure -- paisa-exact)
# ---------------------------------------------------------------------------


def _to_int_paisa_from_rupees(rupees: Any) -> int:
    """Convert a rupee amount (float/int/str) to integer paisa, rounded to the
    nearest paisa. Junk -> 0. Avoids float drift via round-then-int."""
    try:
        return int(round(float(rupees or 0) * 100))
    except (TypeError, ValueError):
        return 0


def _session_day_window(session: Dict[str, Any]):
    """The (start, end) bounds of the session's IST calendar day as NAIVE-UTC
    instants (the frame ``created_at`` is stored in) -- so the by-mode
    reconciliation matches the day-close. ALWAYS bounded to ONE IST calendar day:
    if ``session_date`` is missing/unparseable it falls back to the IST day that
    contains ``opened_at`` -- it NEVER returns an open-ended ``(start, None)``
    window (an open-ended window would reconcile EVERY order from opened_at
    forward and over-state the expected figure). Never raises."""
    from datetime import date as _date, timedelta

    from ..utils.ist import ist_day_start_utc, ist_today

    day = session.get("session_date")
    try:
        d = _date.fromisoformat(str(day)[:10])
    except (TypeError, ValueError):
        d = None
    if d is None:
        # Derive the IST day from opened_at (a naive-UTC instant). Add the IST
        # offset back, take .date(); fall back to IST today if even that is junk.
        opened = session.get("opened_at")
        try:
            d = (opened + timedelta(hours=5, minutes=30)).date()
        except Exception:  # noqa: BLE001
            d = ist_today()
    start = ist_day_start_utc(d)
    return start, start + timedelta(days=1)


def normalize_denominations(rows: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Clean a list of {face, kind, pieces} dicts: drop bad faces, clamp pieces
    to non-negative ints, default kind to 'note', and attach the computed line
    total in PAISA (face*100*pieces). Order is preserved as supplied so the
    stored doc mirrors what the cashier entered. Delegates to the shared
    normaliser -- there is only one."""
    return _denom.normalize_rows(rows)


def total_paisa_from_denominations(rows: Optional[List[Dict[str, Any]]]) -> int:
    """Sum of face*100*pieces across denomination rows, in PAISA. Pure."""
    return _denom.total_paisa(rows)


def denomination_ladder() -> List[Dict[str, Any]]:
    """The blank denomination grid the UI starts from (pieces all zero)."""
    return _denom.denomination_ladder()


# ---------------------------------------------------------------------------
# Config (E2 policy with safe code defaults)
# ---------------------------------------------------------------------------


def get_variance_tolerance_paisa(store_id: Optional[str] = None, entity_id: Optional[str] = None) -> int:
    """The absolute variance band (paisa) within which a session is BALANCED.
    E2-layered (store > entity > global). Any read error -> the code default."""
    try:
        from . import policy_engine

        scope: Dict[str, Any] = {}
        if store_id:
            scope["store_id"] = store_id
        elif entity_id:
            scope["entity_id"] = entity_id
        val = policy_engine.get_policy(POLICY_TOLERANCE, scope, default=_DEFAULT_TOLERANCE_PAISA)
        return max(0, int(val or 0))
    except Exception:  # noqa: BLE001
        return _DEFAULT_TOLERANCE_PAISA


def get_reopen_roles(store_id: Optional[str] = None, entity_id: Optional[str] = None) -> set:
    """Roles permitted to REOPEN a locked till session. E2-layered. Read error
    -> the code default set."""
    try:
        from . import policy_engine

        scope: Dict[str, Any] = {}
        if store_id:
            scope["store_id"] = store_id
        elif entity_id:
            scope["entity_id"] = entity_id
        val = policy_engine.get_policy(POLICY_REOPEN_ROLES, scope, default=list(_DEFAULT_REOPEN_ROLES))
        if isinstance(val, str):
            parts = [p.strip().upper() for p in val.replace(";", ",").split(",") if p.strip()]
            return set(parts) or set(_DEFAULT_REOPEN_ROLES)
        if isinstance(val, (list, tuple, set)):
            roles = {str(r).strip().upper() for r in val if str(r).strip()}
            return roles or set(_DEFAULT_REOPEN_ROLES)
        return set(_DEFAULT_REOPEN_ROLES)
    except Exception:  # noqa: BLE001
        return set(_DEFAULT_REOPEN_ROLES)


def needs_variance_note(variance_paisa: Any, tolerance_paisa: Any) -> bool:
    """True when the counted-vs-expected gap is beyond the band -- the state in
    which the owner's 2026-08-25 ruling makes a written explanation MANDATORY
    to lock/close and alerts the store manager. A None variance needs no note:
    a day nobody counted has nothing to explain. ONE implementation -- the
    blind lock and the Finance close both call this."""
    if variance_paisa is None:
        return False
    try:
        return abs(int(variance_paisa)) > abs(int(tolerance_paisa or 0))
    except (TypeError, ValueError):
        return False


def raise_variance_task(
    *,
    store_id: Optional[str],
    session_date: Optional[str],
    variance_paisa: Any,
    tolerance_paisa: Any,
    note: Optional[str] = None,
    source: str = "day-end close",
) -> None:
    """A SYSTEM task on the store manager's worklist for an out-of-band
    day-end cash variance (owner ruling 2026-08-25: manager alert above the
    band). REUSES ``task_triggers.create_system_task`` -- the in-app bell
    already feeds from the tasks stream, so no new notification mechanism.
    Deduped per (store, day): the blind lock and the Finance close raise ONE
    task for one drawer-day. Fail-soft: an alert failure never undoes the
    lock/close that triggered it."""
    try:
        v = int(variance_paisa or 0)
    except (TypeError, ValueError):
        return
    try:
        from ..dependencies import get_task_repository
        from .task_triggers import create_system_task

        rupees = abs(v) / 100.0
        try:
            band_rupees = abs(int(tolerance_paisa or 0)) / 100.0
        except (TypeError, ValueError):
            band_rupees = 0.0
        direction = "OVER" if v > 0 else "SHORT"
        day = str(session_date or "")
        create_system_task(
            get_task_repository(),
            title=(
                f"Cash drawer {direction} by Rs {rupees:.2f} at "
                f"{store_id} ({day})"
            ),
            description=(
                f"The {day} day-end cash count at store {store_id} is "
                f"{direction} by Rs {rupees:.2f} - beyond the allowed band of "
                f"Rs {band_rupees:.2f}. Recorded at the {source}. "
                + (
                    f"Explanation given: {str(note).strip()}"
                    if str(note or "").strip()
                    else "No explanation was recorded."
                )
            ),
            priority="P2",
            category="Finance",
            store_id=store_id,
            assigned_to="STORE_MANAGER",
            dedupe_ref=f"till_variance:{store_id}:{day}",
            extra={
                "link": "/finance/cash-register",
                "payload": {
                    "store_id": store_id,
                    "session_date": day,
                    "variance_paisa": v,
                    "tolerance_paisa": int(tolerance_paisa or 0),
                },
            },
        )
    except Exception:  # noqa: BLE001
        return


def variance_status(variance_paisa: int, tolerance_paisa: int = 0) -> str:
    """Classify a signed variance against a tolerance band (absolute paisa).
    BALANCED (|v| <= tol), OVERAGE (drawer over beyond tol), SHORTAGE (short)."""
    try:
        v = int(variance_paisa or 0)
        tol = abs(int(tolerance_paisa or 0))
    except (TypeError, ValueError):
        return "BALANCED"
    if abs(v) <= tol:
        return "BALANCED"
    return "OVERAGE" if v > 0 else "SHORTAGE"


# ---------------------------------------------------------------------------
# Collection accessor
# ---------------------------------------------------------------------------


def _sessions_coll(db):
    if db is None:
        return None
    try:
        return db.get_collection(_SESSIONS_COLLECTION)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Expected-cash computation (REUSES E5's reconcile_window over order.payments[])
# ---------------------------------------------------------------------------


def compute_expected(
    db,
    store_id: str,
    window_start: Any,
    window_end: Any,
    opening_float_paisa: int,
    cash_payouts_paisa: int = 0,
) -> Dict[str, Any]:
    """The Z-Read expected figure + the by-tender breakdown.

    Z-READ MATH (paisa-exact integers):
        cash_sales_paisa   = E5 reconcile_window CASH COLLECTED (gross, ex-refunds)
        cash_refunds_paisa = E5 reconcile_window CASH REFUNDED (recorded refunds)
        expected_cash_paisa = opening + cash_sales - cash_refunds - cash_payouts
        (variance = counted - expected is computed at blind-submit)

    This is the ONLY caller that opts into the returns netting
    (include_returns=True) -- the drawer figure must net recorded cash refunds,
    windowed on the refund's own day. cash_sales_paisa is now GROSS collected and
    cash_refunds_paisa is surfaced separately so the manager console + Z-Read can
    show a distinct "Cash refunds (recorded)" line instead of silently shrinking
    sales (and so a recorded refund is never conflated with a manual cash payout).

    The per-tender ``by_mode`` from E5 rides along so the Z-Read can show sales by
    tender (UPI/CARD/etc.) -- only CASH feeds the drawer-expected figure. DB
    absent -> a zero-sales envelope (never raises)."""
    from . import tender_reconciliation as tr

    recon = tr.reconcile_window(
        db, store_id, window_start, window_end, include_returns=True
    )
    by_mode = recon.get("by_mode") or {}
    cash_row = by_mode.get("CASH") or {}
    cash_collected_paisa = _to_int_paisa_from_rupees(
        float(cash_row.get("collected", 0) or 0)
    )
    cash_refunds_paisa = _to_int_paisa_from_rupees(
        float(cash_row.get("refunded", 0) or 0)
    )

    opening = int(opening_float_paisa or 0)
    payouts = int(cash_payouts_paisa or 0)
    # Explicit identity: opening + collected - refunded - payouts. (Equivalent to
    # opening + CASH.net - payouts, but keeping collected/refunded distinct keeps
    # the console honest and lets the double-count advisory compare refunds vs
    # the manual payout box.)
    expected_cash_paisa = opening + cash_collected_paisa - cash_refunds_paisa - payouts
    # Non-blocking double-count advisory: a manual cash payout AND a recorded
    # cash refund in the same window may be the SAME money entered twice (staff
    # used the pre-fix "cash paid out" workaround). Surfaced, never auto-applied.
    refund_double_entry_advisory = cash_refunds_paisa > 0 and payouts > 0
    # A NEGATIVE expected drawer is never a real expectation -- it means cash
    # left the drawer that it never took in (a refund funded from the safe; IMS
    # has no cash-in concept). Reporting the resulting "overage" would credit
    # the cashier with money they never held, so the caller suppresses the
    # verdict. The figure itself is never clamped or hidden.
    negative_expected_advisory = expected_cash_paisa < 0
    return {
        "opening_float_paisa": opening,
        "cash_sales_paisa": cash_collected_paisa,
        "cash_refunds_paisa": cash_refunds_paisa,
        "cash_payouts_paisa": payouts,
        "expected_cash_paisa": expected_cash_paisa,
        "refund_double_entry_advisory": refund_double_entry_advisory,
        "negative_expected_advisory": negative_expected_advisory,
        "by_mode": by_mode,
        "total_net_rupees": recon.get("total_net", 0.0),
        "window_start": recon.get("window_start"),
        "window_end": recon.get("window_end"),
    }


def auto_cash_payouts_paisa(db, store_id: str, window_start: Any, window_end: Any):
    """Cash payouts for the session window, pulled from the EXPENSES BOOK.

    Owner ruling 2026-08-25 (blind is THE day-end): the payouts leg of the
    Z-Read identity is no longer a figure a cashier types into a box -- it is
    what the Expenses screen already recorded. ONE implementation on purpose:
    this delegates to finance's ``_cash_expenses_for_window`` (CASH-mode,
    APPROVED/PAID/SENT_TO_ACCOUNTANT/REIMBURSED, payroll-shaped heads
    excluded), the SAME function the Finance close charges the drawer with --
    so the two doors can never disagree about the day's payouts.

    ``window_end`` is EXCLUSIVE (start + 1 day for the standard session day),
    so the upper bound handed to the IST-day filter is the last instant INSIDE
    the window -- passing the exclusive end verbatim would pull in a whole
    extra day of expenses. Lazy import (the router imports this module).
    Fail-soft to (0, 0), same as the finance close's own read.

    Returns ``(payouts_paisa, off_till_excluded_count)`` -- the second figure
    is a COUNT of payroll-shaped expenses deliberately left out (never their
    amount), so the session can say its payouts leg omits something."""
    from datetime import timedelta

    try:
        from ..routers.finance import _cash_expenses_for_window

        start_iso = window_start.isoformat() if hasattr(window_start, "isoformat") else str(window_start)
        end_inside = window_end - timedelta(seconds=1) if hasattr(window_end, "isoformat") else window_end
        end_iso = end_inside.isoformat() if hasattr(end_inside, "isoformat") else str(end_inside)
        win = _cash_expenses_for_window(db, store_id, start_iso, end_iso)
        return _to_int_paisa_from_rupees(win.total), int(win.excluded_count or 0)
    except Exception:  # noqa: BLE001
        return 0, 0


# ---------------------------------------------------------------------------
# The per-face drawer ledger (denominated tally)
# ---------------------------------------------------------------------------


def compute_face_ledger(
    db,
    store_id: str,
    window_start: Any,
    window_end: Any,
    *,
    opening_count: Optional[Dict[str, Any]] = None,
    closing_count: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Tally the drawer FACE BY FACE across the day.

        opening float
          + notes taken in on every cash sale
          - notes handed back as change
          - notes handed back on cash refunds
          - notes paid out of the till
          = what each face SHOULD be at close
        vs the closing count = the per-face discrepancy

    This is a COUNT ledger, in pieces. It never touches, derives or replaces a
    rupee amount: the rupee expected/variance figures come from
    ``compute_expected`` exactly as before, and a store that captures no
    breakdowns still gets those figures unchanged.

    A NOT_CAPTURED block contributes NOTHING (unknown is not zero), so
    ``coverage`` reports how much of the day actually carries a breakdown --
    without it a manager could read a clean per-face tally that simply means
    nobody counted anything.

    The window is built by ``tender_reconciliation.window_match`` -- the SAME
    builder the rupee reader uses, so the two can never disagree about which
    sales belong to the day. DB absent / read failure -> an honest empty
    envelope, never a fabricated tally."""
    from . import tender_reconciliation as tr

    expected: Dict[Any, int] = {}
    counted: Dict[Any, int] = {}
    coverage = {
        "cash_sale_legs": 0,
        "cash_sale_legs_counted": 0,
        "refund_legs": 0,
        "refund_legs_counted": 0,
        "payouts": 0,
        "payouts_counted": 0,
        "flagged": 0,
    }
    _denom.accumulate(expected, opening_count, +1)
    _denom.accumulate(counted, closing_count, +1)
    if _denom.is_flagged(opening_count):
        coverage["flagged"] += 1
    if _denom.is_flagged(closing_count):
        coverage["flagged"] += 1

    read_ok = True
    if db is not None:
        match = tr.window_match(store_id, window_start, window_end)
        try:
            for order in db.get_collection("orders").find(
                match, {"_id": 0, "payments": 1}
            ):
                for pay in order.get("payments") or []:
                    if str((pay or {}).get("method") or "").upper() != "CASH":
                        continue
                    coverage["cash_sale_legs"] += 1
                    tendered = (pay or {}).get("cash_tendered_count")
                    change = (pay or {}).get("cash_change_count")
                    if _denom.is_captured(tendered) or _denom.is_captured(change):
                        coverage["cash_sale_legs_counted"] += 1
                    # Notes IN, notes OUT. Two separate movements -- a single
                    # net figure carries no face information at all.
                    _denom.accumulate(expected, tendered, +1)
                    _denom.accumulate(expected, change, -1)
                    if _denom.is_flagged(tendered) or _denom.is_flagged(change):
                        coverage["flagged"] += 1
                    if (pay or {}).get("cash_leg_balanced") is False:
                        coverage["flagged"] += 1
        except Exception:  # noqa: BLE001
            read_ok = False
        try:
            ret_match = tr.window_match(store_id, window_start, window_end)
            ret_match["status"] = "COMPLETED"
            ret_match["historical"] = {"$ne": True}
            for ret in db.get_collection("returns").find(
                ret_match, {"_id": 0, "refund_tenders": 1}
            ):
                for leg in ret.get("refund_tenders") or []:
                    if str((leg or {}).get("method") or "").upper() != "CASH":
                        continue
                    coverage["refund_legs"] += 1
                    block = (leg or {}).get("cash_count")
                    if _denom.is_captured(block):
                        coverage["refund_legs_counted"] += 1
                    if _denom.is_flagged(block):
                        coverage["flagged"] += 1
                    _denom.accumulate(expected, block, -1)
        except Exception:  # noqa: BLE001
            read_ok = False
        try:
            exp_match = tr.window_match(store_id, window_start, window_end)
            exp_match["payment_mode"] = "CASH"
            for row in db.get_collection("expenses").find(
                exp_match, {"_id": 0, "cash_count": 1}
            ):
                coverage["payouts"] += 1
                block = (row or {}).get("cash_count")
                if _denom.is_captured(block):
                    coverage["payouts_counted"] += 1
                if _denom.is_flagged(block):
                    coverage["flagged"] += 1
                _denom.accumulate(expected, block, -1)
        except Exception:  # noqa: BLE001
            read_ok = False

    rows = _denom.ledger_rows(expected, counted)
    return {
        "rows": rows,
        "coverage": coverage,
        "read_ok": read_ok,
        "opening_captured": _denom.is_captured(opening_count),
        "closing_captured": _denom.is_captured(closing_count),
        "difference_paisa": sum(r["difference_paisa"] for r in rows),
    }


# ---------------------------------------------------------------------------
# Z-Read number (atomic per-store-per-day counter)
# ---------------------------------------------------------------------------


def _next_zread_number(db, store_id: str, day: str) -> str:
    """Atomic per-(store, day) Z-Read serial via the shared ``counters``
    collection ($inc find_one_and_update -- the same pattern as invoice serials).
    Fail-soft: any error -> a uuid-suffixed fallback so a Z-Read still gets a
    unique, non-colliding label (never blocks the close)."""
    fallback = f"{store_id}/{day}/{uuid.uuid4().hex[:6].upper()}"
    if db is None:
        return fallback
    try:
        from pymongo import ReturnDocument

        key = f"till:{store_id}:{day}"
        doc = db.get_collection("counters").find_one_and_update(
            {"_id": key},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        seq = int((doc or {}).get("seq", 1) or 1)
        return f"{store_id}/{day}/{seq:03d}"
    except Exception:  # noqa: BLE001
        return fallback


# ---------------------------------------------------------------------------
# Lifecycle: open
# ---------------------------------------------------------------------------


def _adopt_declared_float(
    coll,
    existing: Dict[str, Any],
    *,
    denoms: List[Dict[str, Any]],
    declared_paisa: int,
    opening_count_state: Optional[str],
    actor: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Fill a declared opening float onto a session that has NONE on it.

    A till session can exist before any float was declared: a close screen
    auto-opens one for the day it is closing, and the record then says so with
    ``opening_float_not_recorded``. The FIRST DECLARED float fills that gap --
    without it, expected cash and every per-face expected row are computed from
    an opening of zero and the note-by-note verdict is withheld for the whole
    store-day. A float that was already declared STANDS: same "the first answer
    wins" rule the closing count follows, so a second screen can never restate
    somebody's float. Guarded update -- concurrent declarations, exactly one
    wins. Returns the session as it now reads (unchanged on any failure)."""
    if not existing.get("opening_float_not_recorded"):
        return existing
    session_id = existing.get("_id") or existing.get("session_id")
    patch = {
        "opening_float_paisa": int(declared_paisa),
        "opening_denominations": denoms,
        "opening_count": _denom.build_block(
            denoms, int(declared_paisa), state=opening_count_state, actor=actor
        ),
        "opening_float_not_recorded": False,
    }
    try:
        from pymongo import ReturnDocument

        updated = coll.find_one_and_update(
            {
                "_id": session_id,
                "status": STATUS_OPEN,
                "opening_float_not_recorded": True,
            },
            {"$set": patch},
            return_document=ReturnDocument.AFTER,
        )
    except Exception:  # noqa: BLE001
        return existing
    return updated or existing


def open_session(
    db,
    *,
    store_id: str,
    session_date: str,
    opening_denominations: Optional[List[Dict[str, Any]]] = None,
    opening_count_state: Optional[str] = None,
    opening_float_paisa: Optional[int] = None,
    shift: Optional[str] = None,
    note: Optional[str] = None,
    actor: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Open a blind till session for a (store, date).

    ONE SHARED DRAWER PER STORE: there is a single physical cash drawer per store,
    counted ONCE at EOD. So the active session is unique on (store, date) -- NOT
    on cashier. ``cashier_id`` is kept as the informational ``opened_by`` (who
    declared the float) but is NOT part of the uniqueness key. A second open for
    the same (store, date) -- by ANY cashier -- returns the EXISTING session as
    ``{"ok": True, "session", "already_open": True}`` (so a second cashier joins
    the shared drawer rather than spawning a phantom second drawer that the
    store-wide cash math would falsely short).

    Single-document insert. NO expected figure is computed or stored at open time
    (blind enforcement). Returns ``{"ok": True, "session"}``."""
    coll = _sessions_coll(db)
    if coll is None:
        return {"ok": False, "error": "no_db", "http": 503}

    cashier_id = (actor or {}).get("user_id")
    denoms = normalize_denominations(opening_denominations)
    declared = (
        int(opening_float_paisa)
        if opening_float_paisa is not None
        else total_paisa_from_denominations(denoms)
    )

    # One SHARED active session per (store, date) -- cashier is NOT in the key.
    # A second open (even by a different cashier) joins the existing drawer.
    try:
        existing = coll.find_one(
            {
                "store_id": store_id,
                "session_date": session_date,
                "status": {"$in": [STATUS_OPEN, STATUS_BLIND_SUBMITTED]},
            }
        )
    except Exception:  # noqa: BLE001
        existing = None
    if existing is not None:
        # The shared drawer already exists. If nobody had declared a float on
        # it and this door just did, that declaration lands -- see
        # _adopt_declared_float.
        if bool(denoms) or opening_float_paisa is not None:
            existing = _adopt_declared_float(
                coll,
                existing,
                denoms=denoms,
                declared_paisa=declared,
                opening_count_state=opening_count_state,
                actor=actor,
            )
        existing["session_id"] = existing.get("_id")
        existing.pop("_id", None)
        return {"ok": True, "session": existing, "already_open": True}

    now = datetime.utcnow()
    session_id = f"TILL-{store_id}-{now.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
    doc = {
        "_id": session_id,
        "session_id": session_id,
        "store_id": store_id,
        "cashier_id": cashier_id,
        "cashier_name": (actor or {}).get("full_name") or (actor or {}).get("username") or (actor or {}).get("name"),
        "session_date": session_date,
        "status": STATUS_OPEN,
        "shift": (shift or "").upper() or None,
        "opening_float_paisa": declared,
        # LEGACY SHAPE, unchanged: every existing reader of this collection
        # keeps working byte-for-byte.
        "opening_denominations": denoms,
        # THE SHARED SHAPE (services/cash_denominations.py). Same rows, plus
        # the state (so an uncounted float reads NOT_CAPTURED rather than as an
        # empty drawer) and the flag when the declared float and the notes
        # disagree. The declared float remains the money either way.
        "opening_count": _denom.build_block(
            denoms, declared, state=opening_count_state, actor=actor
        ),
        # A session opened for a day nobody declared a float on (the auto-open
        # a close screen does for a past date) has an opening float of ZERO
        # because that is the only arithmetic available -- but a float nobody
        # recorded is NOT a float of nothing. This flag is how the screen says
        # so out loud instead of letting a fabricated variance stand.
        "opening_float_not_recorded": not (bool(denoms) or opening_float_paisa is not None),
        "opened_at": now,
        "opened_by": cashier_id,
        "opening_note": note,
        # blind-submit + lock fields (hidden / null until those steps)
        "blind_count_paisa": None,
        "blind_denominations": [],
        "closing_count": _denom.not_captured_block(0),
        "cash_payouts_paisa": 0,
        "expected_cash_paisa": None,
        "variance_paisa": None,
        "variance_status": None,
        "by_mode": None,
        "computed_at": None,
        "blind_submitted_at": None,
        "blind_submitted_by": None,
        "zread_number": None,
        "locked_at": None,
        "locked_by": None,
        "reopen_count": 0,
        "history": [],
    }
    try:
        coll.insert_one(dict(doc))
    except DuplicateKeyError:
        # Open-race: another open for this (store, date) won the unique index
        # between our find_one check and this insert. Return the existing shared
        # drawer (cooperative), NOT a 500 with a leaked E11000.
        try:
            existing = coll.find_one(
                {
                    "store_id": store_id,
                    "session_date": session_date,
                    "status": {"$in": [STATUS_OPEN, STATUS_BLIND_SUBMITTED]},
                }
            )
        except Exception:  # noqa: BLE001
            existing = None
        if existing is not None:
            existing["session_id"] = existing.get("_id")
            existing.pop("_id", None)
            return {"ok": True, "session": existing, "already_open": True}
        return {"ok": False, "error": "already_open", "http": 409}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"write_failed:{exc}", "http": 500}

    _audit(
        "till.open",
        entity_id=session_id,
        actor=actor,
        after={"status": STATUS_OPEN, "opening_float_paisa": declared},
        store_id=store_id,
        detail={"session_date": session_date, "shift": doc["shift"]},
    )
    doc.pop("_id", None)
    return {"ok": True, "session": doc}


# ---------------------------------------------------------------------------
# Lifecycle: blind submit (cashier; expected computed + STORED but not revealed)
# ---------------------------------------------------------------------------


def blind_submit(
    db,
    session_id: str,
    *,
    blind_denominations: Optional[List[Dict[str, Any]]] = None,
    closing_count_state: Optional[str] = None,
    blind_count_paisa: Optional[int] = None,
    allow_uncounted_total: bool = False,
    window_start: Any = None,
    window_end: Any = None,
    idempotency_key: Optional[str] = None,
    actor: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Cashier posts the denomination counts (BLIND).

    Server stores ``blind_denominations`` + ``blind_count_paisa``, NOW computes
    ``expected_cash_paisa`` and ``variance_paisa`` (STORED, NOT returned to the
    cashier), and transitions OPEN -> BLIND_SUBMITTED via a guarded
    ``find_one_and_update`` on ``status:OPEN`` (so a double-submit can't race two
    different counts in). Denomination-integrity guard: when an explicit
    ``blind_count_paisa`` is supplied it must equal the denomination sum exactly
    (rejects a UI bug passing a wrong total).

    Idempotency: a retry with the SAME ``idempotency_key`` on an
    already-BLIND_SUBMITTED session returns the existing state (no double-apply).

    The caller (router) is responsible for hiding ``expected_cash_paisa`` /
    ``variance_paisa`` from the cashier in the RESPONSE -- this function returns
    the full doc; ``redact_for_cashier`` strips the expected fields."""
    coll = _sessions_coll(db)
    if coll is None:
        return {"ok": False, "error": "no_db", "http": 503}

    session = None
    try:
        session = coll.find_one({"_id": session_id})
    except Exception:  # noqa: BLE001
        session = None
    if session is None:
        return {"ok": False, "error": "not_found", "http": 404}

    # Idempotent retry: same key on an already-submitted session -> existing state.
    if str(session.get("status")) == STATUS_BLIND_SUBMITTED:
        if idempotency_key and session.get("idempotency_key") == idempotency_key:
            session["session_id"] = session.get("_id")
            return {"ok": True, "session": session, "idempotent": True}
        return {"ok": False, "error": "already_submitted", "http": 409}
    if str(session.get("status")) == STATUS_LOCKED:
        return {"ok": False, "error": "already_locked", "http": 409}

    denoms = normalize_denominations(blind_denominations)
    denom_total = total_paisa_from_denominations(denoms)
    # ``allow_uncounted_total``: a total with NO grid behind it is not a UI bug,
    # it is the legacy single-number close (POS Day-End types a figure and skips
    # the breakdown). There is nothing for the total to disagree WITH, so the
    # integrity check has nothing to check. It still applies in full the moment
    # a grid exists, and the native blind-EOD door never passes this flag -- its
    # behaviour is unchanged.
    skip_integrity = bool(allow_uncounted_total) and not denoms
    if blind_count_paisa is not None and not skip_integrity and int(blind_count_paisa) != denom_total:
        # Denomination integrity: a supplied total must match the grid exactly.
        return {
            "ok": False,
            "error": "denomination_mismatch",
            "http": 400,
            "denom_total_paisa": denom_total,
            "submitted_paisa": int(blind_count_paisa),
        }
    counted = denom_total if blind_count_paisa is None else int(blind_count_paisa)

    store_id = session.get("store_id")
    # The expected figure is computed over the SESSION WINDOW. By default that is
    # the session's IST calendar day (derived from session_date) so the by-mode
    # reconciliation matches the day-close; the caller may override the bounds
    # (e.g. a sub-day shift). Computing at blind-submit time stamps computed_at so
    # a sale finalizing the same second is bounded by the day, not missed.
    if window_start is not None:
        ws, we = window_start, window_end
    else:
        ws, we = _session_day_window(session)
    # AUTO-PULLED, never hand-typed (owner ruling 2026-08-25): the payouts leg
    # comes from the expenses book for the session window -- the same read the
    # Finance close charges the drawer with.
    payouts, off_till_excluded = auto_cash_payouts_paisa(db, store_id, ws, we)
    exp = compute_expected(db, store_id, ws, we, int(session.get("opening_float_paisa", 0) or 0), payouts)
    expected_cash_paisa = exp["expected_cash_paisa"]
    variance_paisa = counted - expected_cash_paisa
    tol = get_variance_tolerance_paisa(store_id=store_id)
    # A negative expected drawer means a cash-in is missing (see
    # compute_expected). Suppress the variance VERDICT rather than crediting the
    # cashier with a phantom overage; the raw variance number is still stored.
    negative_expected = bool(exp.get("negative_expected_advisory"))
    vstatus = (
        NEGATIVE_EXPECTED if negative_expected
        else variance_status(variance_paisa, tol)
    )

    now = datetime.utcnow()
    # Guarded transition OPEN -> BLIND_SUBMITTED (only one count can land).
    from pymongo import ReturnDocument

    updated = None
    try:
        updated = coll.find_one_and_update(
            {"_id": session_id, "status": STATUS_OPEN},
            {
                "$set": {
                    "status": STATUS_BLIND_SUBMITTED,
                    "blind_denominations": denoms,
                    # The shared shape alongside the legacy list. A close with
                    # no grid entered reads NOT_CAPTURED -- an uncounted drawer
                    # must never look like an emptied one.
                    "closing_count": _denom.build_block(
                        denoms, counted, state=closing_count_state, actor=actor
                    ),
                    "blind_count_paisa": counted,
                    "cash_payouts_paisa": payouts,
                    # AUTO_EXPENSES: the figure above came from the expenses
                    # book, not a hand-typed box. The advisory says a payroll-
                    # shaped expense was deliberately left out (count only).
                    "cash_payouts_source": "AUTO_EXPENSES",
                    "off_till_expense_advisory": off_till_excluded > 0,
                    "expected_cash_paisa": expected_cash_paisa,
                    "cash_sales_paisa": exp["cash_sales_paisa"],
                    "cash_refunds_paisa": exp.get("cash_refunds_paisa", 0),
                    "refund_double_entry_advisory": exp.get(
                        "refund_double_entry_advisory", False
                    ),
                    "negative_expected_advisory": negative_expected,
                    "variance_paisa": variance_paisa,
                    "variance_status": vstatus,
                    "by_mode": exp["by_mode"],
                    "tolerance_paisa": tol,
                    "computed_at": now,
                    "blind_submitted_at": now,
                    "blind_submitted_by": (actor or {}).get("user_id"),
                    "idempotency_key": idempotency_key,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"submit_failed:{exc}", "http": 500}
    if updated is None:
        # Lost the race (status flipped off OPEN between read and update).
        return {"ok": False, "error": "already_submitted", "http": 409}

    _audit(
        "till.blind_submit",
        entity_id=session_id,
        actor=actor,
        before={"status": STATUS_OPEN},
        # Audit records the FULL truth (expected + variance) -- the redaction is
        # only for the cashier's HTTP response, not the immutable trail.
        after={
            "status": STATUS_BLIND_SUBMITTED,
            "blind_count_paisa": counted,
            "expected_cash_paisa": expected_cash_paisa,
            "variance_paisa": variance_paisa,
        },
        store_id=store_id,
        detail={"session_date": session.get("session_date")},
    )
    updated["session_id"] = updated.get("_id")
    return {"ok": True, "session": updated}


# ---------------------------------------------------------------------------
# TWO DOORS, ONE RECORD
# ---------------------------------------------------------------------------


def record_screen_open(db, **kwargs: Any) -> Dict[str, Any]:
    """Fail-soft wrapper around ``open_session`` for a SCREEN that declares an
    opening float (Finance > Cash Register, POS).

    Same rule as ``record_screen_close``: linking a screen to the shared record
    must never be able to stop a store opening its till, so any failure is
    reported on the caller's own record instead of raised. The float is stored
    on the ONE (store, date) session, which is where expected cash and every
    per-face expected row are computed from."""
    try:
        return open_session(db, **kwargs)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"link_failed:{exc}"}


def record_screen_close(db, **kwargs: Any) -> Dict[str, Any]:
    """Fail-soft wrapper. See ``_record_screen_close``.

    Linking a close to the shared record must NEVER be able to stop a store
    closing its day, so every exception below this line -- a database blip, a
    malformed legacy session, anything -- becomes a reported failure rather
    than a 500 on the close screen. The screen's own record is written either
    way, and the failure is stored on it rather than swallowed."""
    try:
        return _record_screen_close(db, **kwargs)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"link_failed:{exc}"}


def _record_screen_close(
    db,
    *,
    store_id: str,
    session_date: str,
    closing_rows: Optional[List[Dict[str, Any]]] = None,
    closing_count_state: Optional[str] = None,
    counted_paisa: Optional[int] = None,
    actor: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Land a closing count from EITHER close screen on the SAME till session.

    POS Day-End and Finance > Cash Register are two DOORS. This is the one
    RECORD behind them. Whichever screen closes first creates (or joins) the
    single (store, date) session -- guarded by the collection's partial unique
    index -- and submits the count. Whichever closes second finds that session
    already counted and joins it: it does NOT open a second drawer and does NOT
    overwrite the first count. Two screens can therefore never show two
    different answers for one day, because there is only one answer.

    A CLOSE WITH NOTHING COUNTED SUBMITS NOTHING. If the screen sent neither a
    grid nor a typed total, the session is opened/joined and left OPEN -- it is
    NOT blind-submitted with a count of zero. Writing zero here would put the
    exact defect this work removes (blank persisted as an emptied drawer) onto
    the shared record, where three screens would then read it.

    FAIL-SOFT BY DESIGN. Every failure path returns ``ok: False`` with a reason
    rather than raising -- and ``record_screen_close`` catches anything that
    still escapes -- because linking to the shared record must not be able to
    stop a store closing its day. The calling screen's own record is written
    either way, and a failure here is surfaced, not swallowed into a fake
    success."""
    coll = _sessions_coll(db)
    if coll is None:
        return {"ok": False, "error": "no_db"}
    try:
        existing = coll.find_one(
            {
                "store_id": store_id,
                "session_date": session_date,
                "status": {"$in": [STATUS_OPEN, STATUS_BLIND_SUBMITTED, STATUS_LOCKED]},
            }
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"read_failed:{exc}"}

    created = False
    if existing is None:
        opened = open_session(
            db,
            store_id=store_id,
            session_date=session_date,
            actor=actor,
        )
        if not opened.get("ok"):
            return {"ok": False, "error": opened.get("error", "open_failed")}
        existing = opened["session"]
        created = not opened.get("already_open", False)

    session_id = existing.get("session_id") or existing.get("_id")
    status = str(existing.get("status"))
    # A day whose float nobody declared: expected cash is computed with an
    # opening of zero because that is the only arithmetic available. The
    # caller carries this flag onto its own record so the screen can say so
    # rather than letting a fabricated variance stand unexplained.
    float_missing = bool(existing.get("opening_float_not_recorded"))
    if status in (STATUS_BLIND_SUBMITTED, STATUS_LOCKED):
        # Already counted through the other door. The first count STANDS -- a
        # second screen must not be able to quietly restate a signed-off
        # drawer. The caller links to it and says so.
        return {
            "ok": True,
            "session_id": session_id,
            "session": existing,
            "created": created,
            "opening_float_not_recorded": float_missing,
            "already_counted": True,
        }

    rows = normalize_denominations(closing_rows)
    state_captured = str(closing_count_state or "").upper() in (
        _denom.STATE_COUNTED,
        _denom.STATE_SUGGESTED,
    )
    if not rows and not state_captured and counted_paisa is None:
        # Nothing was counted at all. Link the day to its session; submit no
        # count. An OPEN session is an honest "not counted yet"; a submitted
        # zero would be a lie the whole till reads.
        return {
            "ok": True,
            "session_id": session_id,
            "session": existing,
            "created": created,
            "opening_float_not_recorded": float_missing,
            "already_counted": False,
            "counted": False,
            "not_captured": True,
        }

    # The grid rules when there is a grid: the till's own integrity check (a
    # supplied total must equal the notes) stays in force. A typed total is only
    # forwarded when no grid came with it -- the legacy single-number close.
    forward_total = None if rows else counted_paisa
    res = blind_submit(
        db,
        session_id,
        blind_denominations=closing_rows,
        closing_count_state=closing_count_state,
        blind_count_paisa=forward_total,
        allow_uncounted_total=not rows,
        actor=actor,
    )
    if not res.get("ok"):
        if res.get("error") in ("already_submitted", "already_locked"):
            return {
                "ok": True,
                "session_id": session_id,
                "created": created,
            "opening_float_not_recorded": float_missing,
                "already_counted": True,
                "counted": True,
            }
        return {"ok": False, "error": res.get("error", "submit_failed")}
    submitted = int((res["session"] or {}).get("blind_count_paisa") or 0)
    return {
        "ok": True,
        "session_id": session_id,
        "session": res["session"],
        "created": created,
        "opening_float_not_recorded": float_missing,
        "already_counted": False,
        "counted": True,
        "submitted_paisa": submitted,
        # The screen's own figure and the shared record's figure are the same
        # number unless the screen used an override that disagreed with its own
        # notes. That is a pre-existing hazard; here it becomes visible.
        "count_differs": (
            counted_paisa is not None and int(counted_paisa) != submitted
        ),
    }


# ---------------------------------------------------------------------------
# Lifecycle: lock (manager reveals variance + soft-locks the Z-Read)
# ---------------------------------------------------------------------------


def lock_session(
    db,
    session_id: str,
    *,
    actor: Optional[Dict[str, Any]] = None,
    variance_note: Optional[str] = None,
) -> Dict[str, Any]:
    """Soft-lock the Z-Read ATOMICALLY -- the SAME guarded-find_one_and_update
    shape as E5's ``lock_reconciliation``: a single guarded update on
    ``status:BLIND_SUBMITTED`` flips it to LOCKED in one op. Two concurrent locks
    -> exactly one wins (the loser sees the doc no longer BLIND_SUBMITTED).

    MANDATORY NOTE ABOVE THE BAND (owner ruling 2026-08-25): when the stored
    variance is beyond the tolerance band, the lock is REFUSED unless a
    non-blank ``variance_note`` explains it -- the same shape as the reopen's
    mandatory reason. An out-of-band lock also raises a SYSTEM task on the
    store manager's worklist (the in-app bell feeds from tasks).

    Unlike E5's HARD lock this is a TRANSPARENT SOFT-LOCK: it stamps
    ``locked_by``/``locked_at`` + a Z-Read number, and the session can later be
    REOPENED by an authorized role (audited). Returns the full doc (expected +
    variance revealed to the manager). ``{"ok": True, "session"}`` or an error
    envelope with an HTTP code."""
    coll = _sessions_coll(db)
    if coll is None:
        return {"ok": False, "error": "no_db", "http": 503}

    session = None
    try:
        session = coll.find_one({"_id": session_id})
    except Exception:  # noqa: BLE001
        session = None
    if session is None:
        return {"ok": False, "error": "not_found", "http": 404}

    store_id = session.get("store_id")
    day = str(session.get("session_date") or "")

    # Out-of-band variance demands a written explanation BEFORE the lock. The
    # band is the one stored at blind-submit (the band the verdict was judged
    # against); a legacy session without one reads the live policy.
    variance_paisa = session.get("variance_paisa")
    tolerance_paisa = session.get("tolerance_paisa")
    if tolerance_paisa is None:
        tolerance_paisa = get_variance_tolerance_paisa(store_id=store_id)
    clean_note = str(variance_note or "").strip()
    out_of_band = needs_variance_note(variance_paisa, tolerance_paisa)
    if out_of_band and not clean_note:
        return {
            "ok": False,
            "error": "variance_note_required",
            "http": 400,
            "variance_paisa": variance_paisa,
            "tolerance_paisa": int(tolerance_paisa or 0),
        }
    # Mint the Z-Read serial only if not already assigned (a reopen->relock keeps
    # the same Z-Read number -- it is the same business day-close).
    zread = session.get("zread_number") or _next_zread_number(db, store_id, day)

    now = datetime.utcnow()
    from pymongo import ReturnDocument

    locked = None
    try:
        locked = coll.find_one_and_update(
            {"_id": session_id, "status": STATUS_BLIND_SUBMITTED},
            {
                "$set": {
                    "status": STATUS_LOCKED,
                    "zread_number": zread,
                    "locked_at": now,
                    "locked_by": (actor or {}).get("user_id"),
                    "locked_by_name": (actor or {}).get("full_name") or (actor or {}).get("username") or (actor or {}).get("name"),
                    # The mandatory explanation for an out-of-band variance
                    # (None when the day balanced and none was given).
                    "variance_note": clean_note or None,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"lock_failed:{exc}", "http": 500}
    if locked is None:
        present = None
        try:
            present = coll.find_one({"_id": session_id})
        except Exception:  # noqa: BLE001
            present = None
        status = str((present or {}).get("status"))
        if status == STATUS_LOCKED:
            return {"ok": False, "error": "already_locked", "http": 409}
        # Still OPEN (never blind-submitted) -> cannot lock yet.
        return {"ok": False, "error": "not_submitted", "http": 409}

    _audit(
        "till.lock",
        entity_id=session_id,
        actor=actor,
        before={"status": STATUS_BLIND_SUBMITTED},
        after={
            "status": STATUS_LOCKED,
            "zread_number": zread,
            "expected_cash_paisa": locked.get("expected_cash_paisa"),
            "variance_paisa": locked.get("variance_paisa"),
            "variance_status": locked.get("variance_status"),
            "variance_note": locked.get("variance_note"),
        },
        store_id=store_id,
        severity="WARNING" if str(locked.get("variance_status")) != "BALANCED" else "INFO",
        detail={"session_date": day},
    )
    # Manager alert above the band (owner ruling 2026-08-25). After the lock
    # won -- exactly one of two concurrent locks reaches this line.
    if out_of_band:
        raise_variance_task(
            store_id=store_id,
            session_date=day,
            variance_paisa=variance_paisa,
            tolerance_paisa=tolerance_paisa,
            note=clean_note,
            source="blind EOD Z-Read lock",
        )
    locked["session_id"] = locked.get("_id")
    return {"ok": True, "session": locked}


# ---------------------------------------------------------------------------
# Lifecycle: reopen (transparent soft-lock release -- mandatory reason + audited)
# ---------------------------------------------------------------------------


def reopen_session(
    db,
    session_id: str,
    *,
    reason: str,
    actor: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Reopen a LOCKED session back to BLIND_SUBMITTED (so the manager can
    re-lock after a correction). This is the SOFT-LOCK release:

      * a non-empty ``reason`` is MANDATORY (the call rejects a blank reason),
      * the actor's role must be in the E2-configured reopen set
        (``till.reopen_roles``; the ROUTER also gates this -- defense in depth),
      * the reopen is recorded in the session ``history`` array AND a
        ``till.reopen`` audit row, and ``reopen_count`` is incremented.

    Atomic guarded transition LOCKED -> BLIND_SUBMITTED (single
    find_one_and_update) so two concurrent reopens can't both apply. The Z-Read
    number is preserved (same business day-close). Returns ``{"ok": True,
    "session"}`` or an error envelope."""
    coll = _sessions_coll(db)
    if coll is None:
        return {"ok": False, "error": "no_db", "http": 503}

    clean_reason = str(reason or "").strip()
    if not clean_reason:
        return {"ok": False, "error": "reason_required", "http": 400}

    session = None
    try:
        session = coll.find_one({"_id": session_id})
    except Exception:  # noqa: BLE001
        session = None
    if session is None:
        return {"ok": False, "error": "not_found", "http": 404}

    store_id = session.get("store_id")
    roles = {str(r).upper() for r in ((actor or {}).get("roles") or [])}
    allowed = get_reopen_roles(store_id=store_id)
    if not (roles & allowed):
        return {"ok": False, "error": "not_permitted_to_reopen", "http": 403}

    now = datetime.utcnow()
    history_entry = {
        "action": "reopen",
        "at": now,
        "by": (actor or {}).get("user_id"),
        "by_name": (actor or {}).get("full_name") or (actor or {}).get("username") or (actor or {}).get("name"),
        "reason": clean_reason,
    }
    from pymongo import ReturnDocument

    reopened = None
    try:
        reopened = coll.find_one_and_update(
            {"_id": session_id, "status": STATUS_LOCKED},
            {
                "$set": {
                    "status": STATUS_BLIND_SUBMITTED,
                    "reopened_at": now,
                    "reopened_by": (actor or {}).get("user_id"),
                    "reopen_reason": clean_reason,
                    "locked_at": None,
                    "locked_by": None,
                },
                "$inc": {"reopen_count": 1},
                "$push": {"history": history_entry},
            },
            return_document=ReturnDocument.AFTER,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"reopen_failed:{exc}", "http": 500}
    if reopened is None:
        status = str(session.get("status"))
        if status != STATUS_LOCKED:
            return {"ok": False, "error": "not_locked", "http": 409}
        return {"ok": False, "error": "reopen_failed", "http": 409}

    _audit(
        "till.reopen",
        entity_id=session_id,
        actor=actor,
        before={"status": STATUS_LOCKED},
        after={"status": STATUS_BLIND_SUBMITTED, "reason": clean_reason},
        store_id=store_id,
        severity="WARNING",
        detail={"session_date": session.get("session_date"), "reopen_count": reopened.get("reopen_count")},
    )
    reopened["session_id"] = reopened.get("_id")
    return {"ok": True, "session": reopened}


# ---------------------------------------------------------------------------
# Reads + Z-Read report
# ---------------------------------------------------------------------------


def get_session(db, session_id: str) -> Optional[Dict[str, Any]]:
    """Load a till session by id (or None). Used by the routes to store-scope the
    actor BEFORE a mutation (cross-store IDOR guard)."""
    coll = _sessions_coll(db)
    if coll is None:
        return None
    try:
        doc = coll.find_one({"_id": session_id})
    except Exception:  # noqa: BLE001
        return None
    if doc is not None:
        doc["session_id"] = doc.get("_id")
    return doc


def list_sessions(
    db,
    *,
    store_id: Optional[str] = None,
    session_date: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Session rows for a store/date/status, newest first. DB absent -> []."""
    coll = _sessions_coll(db)
    if coll is None:
        return []
    match: Dict[str, Any] = {}
    if store_id:
        match["store_id"] = store_id
    if session_date:
        match["session_date"] = session_date
    if status:
        match["status"] = str(status).upper()
    try:
        cursor = coll.find(match, {"_id": 0}).sort("opened_at", -1).limit(int(limit))
        return list(cursor)
    except Exception:  # noqa: BLE001
        return []


# Fields a SALES_CASHIER / CASHIER must NEVER see before the manager locks
# (blind enforcement at the DATA layer, not just the UI). A hidden column is
# defeated by a devtools tab, so everything DERIVED from the expected-cash
# computation is withheld from the RESPONSE BODY itself: the figures, the
# verdict, the auto-pulled payout leg, and the advisories (in particular
# ``negative_expected_advisory``, which literally discloses the sign of the
# expected figure).
_CASHIER_HIDDEN_FIELDS = (
    "expected_cash_paisa",
    "variance_paisa",
    "variance_status",
    "cash_sales_paisa",
    "cash_refunds_paisa",
    "cash_payouts_paisa",
    "cash_payouts_source",
    "by_mode",
    "tolerance_paisa",
    "negative_expected_advisory",
    "refund_double_entry_advisory",
    "off_till_expense_advisory",
    "variance_note",
)


def redact_for_cashier(session: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Strip the expected/variance/by-mode fields from a session so a cashier
    NEVER sees the system figure before a manager locks (blind enforcement). The
    cashier still sees their own count + opening float + status. A copy is
    returned; the stored doc is untouched."""
    if session is None:
        return None
    out = dict(session)
    for f in _CASHIER_HIDDEN_FIELDS:
        out.pop(f, None)
    out["expected_hidden"] = True
    return out


def build_zread(db, session_id: str) -> Dict[str, Any]:
    """Full Z-Read report payload for print: session metadata, opening float,
    denomination breakdown, sales-by-tender, expected, counted, variance, lock +
    reopen trail. Manager/finance-only (the ROUTE gate restricts who can call;
    this builder assumes the caller is authorized). Returns ``{"ok": False,
    ...}`` when the session is missing."""
    session = get_session(db, session_id)
    if session is None:
        return {"ok": False, "error": "not_found", "http": 404}

    opening = int(session.get("opening_float_paisa", 0) or 0)
    cash_sales = int(session.get("cash_sales_paisa", 0) or 0)
    cash_refunds = int(session.get("cash_refunds_paisa", 0) or 0)
    payouts = int(session.get("cash_payouts_paisa", 0) or 0)
    expected = session.get("expected_cash_paisa")
    counted = session.get("blind_count_paisa")
    variance = session.get("variance_paisa")

    zread = {
        "ok": True,
        "session_id": session.get("session_id"),
        "zread_number": session.get("zread_number"),
        "store_id": session.get("store_id"),
        "session_date": session.get("session_date"),
        "shift": session.get("shift"),
        "cashier_id": session.get("cashier_id"),
        "cashier_name": session.get("cashier_name"),
        "status": session.get("status"),
        "opened_at": session.get("opened_at"),
        "opening_float_paisa": opening,
        "opening_denominations": session.get("opening_denominations") or [],
        "blind_denominations": session.get("blind_denominations") or [],
        # The shared Cash Count Blocks + the face-by-face tally of the day.
        # Purely additive to the rupee figures above, which are computed exactly
        # as before and are unaffected by whether anyone counted notes.
        "opening_count": session.get("opening_count")
        or _denom.not_captured_block(opening),
        "closing_count": session.get("closing_count")
        or _denom.not_captured_block(int(counted or 0)),
        "face_ledger": compute_face_ledger(
            db,
            session.get("store_id"),
            *_session_day_window(session),
            opening_count=session.get("opening_count"),
            closing_count=session.get("closing_count"),
        ),
        "by_mode": session.get("by_mode") or {},
        # The Z-Read identity: opening + cash_sales - cash_refunds - payouts =
        # expected. cash_sales is GROSS collected; cash_refunds is the recorded
        # cash refunds auto-deducted (a DISTINCT line from the manual payouts, so
        # a refund is never silently merged into "cash paid out").
        "cash_sales_paisa": cash_sales,
        "cash_refunds_paisa": cash_refunds,
        "cash_payouts_paisa": payouts,
        # AUTO_EXPENSES on sessions submitted after the 2026-08-25 ruling;
        # None on older sessions whose payouts were hand-keyed.
        "cash_payouts_source": session.get("cash_payouts_source"),
        "off_till_expense_advisory": bool(session.get("off_till_expense_advisory")),
        "refund_double_entry_advisory": bool(
            session.get("refund_double_entry_advisory")
        ),
        "negative_expected_advisory": bool(
            session.get("negative_expected_advisory")
        ),
        "negative_expected_message": (
            NEGATIVE_EXPECTED_MESSAGE
            if session.get("negative_expected_advisory")
            else None
        ),
        "expected_cash_paisa": expected,
        "counted_cash_paisa": counted,
        "variance_paisa": variance,
        "variance_status": session.get("variance_status"),
        "variance_note": session.get("variance_note"),
        "tolerance_paisa": session.get("tolerance_paisa"),
        "locked_at": session.get("locked_at"),
        "locked_by": session.get("locked_by"),
        "locked_by_name": session.get("locked_by_name"),
        "reopen_count": session.get("reopen_count", 0),
        "history": session.get("history") or [],
        "computed_at": session.get("computed_at"),
    }
    return zread


# ---------------------------------------------------------------------------
# Index setup (greenfield collection; called from main.py startup)
# ---------------------------------------------------------------------------


def ensure_till_indexes(db) -> None:
    """Idempotent. ONE SHARED DRAWER PER STORE: a partial-unique index so AT MOST
    ONE active (OPEN/BLIND_SUBMITTED) session can exist per (store, date) --
    cashier is deliberately NOT in the key (the drawer is shared and counted once
    at EOD; the store-wide cash math is only correct with a single session per
    store/day). Plus listing indexes. Fail-soft."""
    if db is None:
        return
    try:
        coll = db.get_collection(_SESSIONS_COLLECTION)
        # Drop the superseded per-cashier unique index (if a prior deploy created
        # it) so the shared-drawer (store, date) uniqueness takes over.
        try:
            coll.drop_index("uniq_active_till_per_cashier_day")
        except Exception:  # noqa: BLE001
            pass
        coll.create_index(
            [("store_id", 1), ("session_date", 1)],
            unique=True,
            partialFilterExpression={"status": {"$in": [STATUS_OPEN, STATUS_BLIND_SUBMITTED]}},
            name="uniq_active_till_per_store_day",
        )
        coll.create_index([("store_id", 1), ("session_date", -1)], name="till_store_date")
        coll.create_index([("store_id", 1), ("status", 1)], name="till_store_status")
    except Exception:  # noqa: BLE001
        return


# ---------------------------------------------------------------------------
# Audit (one append-only hash-chained row via AuditRepository.create)
# ---------------------------------------------------------------------------


def _audit(
    action: str,
    *,
    entity_id: str,
    actor: Optional[Dict[str, Any]],
    before: Any = None,
    after: Any = None,
    detail: Optional[Dict[str, Any]] = None,
    store_id: Optional[str] = None,
    severity: str = "INFO",
) -> None:
    """One append-only hash-chained audit row via AuditRepository.create (NEVER
    append_audit_entry). Fail-soft -- an audit failure never undoes the business
    write that triggered it."""
    try:
        from api.dependencies import get_audit_repository

        repo = get_audit_repository()
        if repo is None:
            return
        repo.create(
            {
                "action": action,
                "entity_type": "till_session",
                "entity_id": entity_id,
                "store_id": store_id or (actor or {}).get("active_store_id"),
                "user_id": (actor or {}).get("user_id"),
                "user_name": (actor or {}).get("full_name") or (actor or {}).get("username"),
                "severity": severity,
                "source": "eod_tally",
                "before_state": before,
                "after_state": after,
                "detail": detail or {},
            }
        )
    except Exception:  # noqa: BLE001
        return
