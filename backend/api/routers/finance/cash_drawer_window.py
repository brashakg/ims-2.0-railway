"""Cash-drawer window maths: what counts as a cash sale / cash expense /
cash refund for a till session, plus the session request models.

Moved verbatim out of the 6,604-line api/routers/finance.py (Wave 5
package split): no path, method, dependency, status code, response_model
or default was changed.
"""

from datetime import datetime, timezone
from ...utils.ist import ist_date_str, ist_today
from typing import Optional, List, Dict, NamedTuple
from pydantic import BaseModel, Field
from ...services.salary_visibility import is_payroll_shaped_expense
from ...services import cash_denominations as cash_denom
from ._shared import logger

# ============================================================================
# CASH REGISTER / EOD RECONCILIATION
# ============================================================================
# A till session: opened with a denomination float, closed with a counted
# denomination breakdown. Expected cash = opening + POS CASH sales for the
# window - cash refunds - cash payouts/expenses - bank deposit. Variance =
# counted - expected. Store-scoped; persisted to `cash_register_sessions`.
#
# Pure money math lives in services/cash_register.py; this router owns
# persistence, store scoping, and pulling the POS CASH figure for the window.

_CASH_SESSIONS = "cash_register_sessions"


# The count-sheet line is defined ONCE, in services/cash_denominations.py.
# This alias keeps the name this router has always used without holding a
# fourth copy of the shape for the four to drift apart. The LIST is
# ``cash_denom.CountSheet`` -- lenient rows inside a strict list still 422'd a
# sheet sent as a bare string, which is a refusal one level up.
DenominationLine = cash_denom.DenominationRow


class CashRegisterOpen(BaseModel):
    store_id: Optional[str] = None
    shift: Optional[str] = None  # AM / PM / FULL (free text)
    denominations: cash_denom.CountSheet = Field(default_factory=list)
    # COUNTED | SUGGESTED | NOT_CAPTURED -- an untouched grid is recorded as
    # never counted, never as an empty float.
    opening_count_state: Optional[str] = None
    opening_float: Optional[float] = None  # optional override of denom sum
    note: Optional[str] = None


class CashRegisterClose(BaseModel):
    session_id: str
    denominations: cash_denom.CountSheet = Field(default_factory=list)
    # COUNTED | SUGGESTED | NOT_CAPTURED -- so a close with an untouched
    # grid is recorded as never counted rather than as an empty drawer.
    closing_count_state: Optional[str] = None
    bank_deposit: float = 0.0
    counted_override: Optional[float] = None  # optional override of denom sum
    # ``tolerance`` was DELETED from this body (owner ruling 2026-08-25: ONE
    # Rs 100 band, from policy). The closer choosing their own band was the
    # exact thing the ruling banned; the server now reads
    # ``till.variance_tolerance_paisa`` -- the SAME band the blind EOD uses.
    # ``note`` becomes MANDATORY when the variance is beyond that band.
    note: Optional[str] = None


def _to_dt(s):
    """Parse an ISO date/datetime string to a NAIVE-UTC datetime (None on
    failure).

    Stored instants here are naive-UTC (``_iso_now()``); every caller compares
    the result against that frame. An offset-suffixed string -- never written
    by our stamps, but present in fixtures and possible in hand-edited rows --
    is CONVERTED to that frame. The old ``[:19]`` slice silently DROPPED the
    offset and re-badged the wall time as UTC: '...T21:00:00+05:30' (21:00
    IST) was read as 21:00 UTC, 5h30m late, which filed a legacy till close
    under the next IST business day (BUG-104's mirror image)."""
    if not s:
        return None
    raw = str(s)
    dt = None
    try:
        dt = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        try:
            dt = datetime.fromisoformat(raw[:19])
        except (ValueError, TypeError):
            try:
                dt = datetime.fromisoformat(raw[:10])
            except (ValueError, TypeError):
                return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _ist_day_face(value) -> str:
    """The IST calendar day ('YYYY-MM-DD') of a stored instant.

    BUG-104. Timestamps on this surface (`opened_at`, `closed_at`) are written
    by ``_iso_now()`` == ``datetime.utcnow().isoformat()``, so their first ten
    characters are the UTC day -- which, for anything between 00:00 and 05:30
    IST, is YESTERDAY. Parse to the instant first, then take the IST day.
    Unparseable junk keeps the old first-ten-characters behaviour rather than
    dropping the row. One definition, so no caller can drift from another."""
    dt = _to_dt(value)
    return ist_date_str(dt) if dt is not None else str(value or "")[:10]


def _created_at_or_clauses(start_iso, end_iso) -> list:
    """The dual-type `created_at` window: a DATETIME clause OR a STRING clause.

    Mongo type-brackets a Date range away from a string field, so a column
    holding both shapes must be asked for both ways or one type is silently
    dropped (BUG-031). Bounds are normalised to the naive-UTC frame the stored
    values use (BUG-104).

    ONE definition because the refund TOTAL and the per-leg list behind the
    drawer's double-entry advisory read the SAME `returns` rows: aligning
    their bound VALUES but not their clause SHAPES left a Date-typed refund
    visible to the total and invisible to the legs -- and the advisory returns
    early on an empty leg list, so it would switch itself off while the drawer
    still deducted the money."""
    start_str = _naive_utc_iso_bound(start_iso)
    end_str = _naive_utc_iso_bound(end_iso)
    start_dt = _to_dt(start_iso)
    date_win: Dict = {"$gte": start_dt} if start_dt else {}
    str_win: Dict = {"$gte": start_str} if start_str else {}
    if end_iso:
        if end_str:
            str_win["$lte"] = end_str
        end_dt = _to_dt(end_iso)
        if end_dt:
            date_win["$lte"] = end_dt
    clauses = []
    if date_win:
        clauses.append({"created_at": date_win})
    if str_win:
        clauses.append({"created_at": str_win})
    return clauses


def _ist_day_window(start_iso, end_iso) -> tuple:
    """The (start_day, end_day) IST calendar-day pair for a till session.

    BUG-104. Both bounds are IST days because they filter `expense_date`, an
    operator-typed IST CALENDAR DATE. `start_iso` / `end_iso` are the
    session's naive-UTC stamps (`_iso_now`), so their first ten characters are
    the UTC day -- yesterday for anything in the 00:00-05:30 IST band. An
    ABSENT `end_iso` means "still open, up to now", which is TODAY in the same
    IST frame.

    ONE definition on purpose. This exact two-line pair was copied inline
    FOUR times across four review rounds -- the drawer charge and the
    double-entry advisory being the pair that mattered, because a drift
    between them makes the advisory name a payout the drawer never
    subtracted. Both callers now route through here so they cannot drift."""
    start_day = _ist_day_face(start_iso)
    end_day = _ist_day_face(end_iso) if end_iso else ist_today().isoformat()
    return start_day, end_day


def _naive_utc_iso_bound(v) -> Optional[str]:
    """The NAIVE-UTC ISO face of a bound, for a LEGACY STRING clause.

    BUG-104. String-typed ``created_at`` columns (`returns`, legacy online
    orders) hold naive-UTC isoformats, so a bound compared against them
    LEXICALLY must be in that frame too. Passing the raw value through meant
    an offset-suffixed bound ('...T21:00:00+05:30') was compared face-value
    against '...T15:30:00' -- sorting 5h30m late and silently dropping rows.
    ``_to_dt`` already normalises an aware value to naive-UTC (#993);
    unparseable junk keeps the old raw face.

    ONE definition: the drawer's refund TOTAL and the per-leg list behind its
    advisory read the same returns rows, so a second copy here would let the
    total say one thing while the legs said another and the advisory quietly
    switched itself off."""
    if v is None:
        return None
    dt = v if isinstance(v, datetime) else _to_dt(v)
    if dt is None:
        return str(v)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat()


def _cash_sales_for_window(db, store_id: str, start_iso: str, end_iso: Optional[str]):
    """Net POS CASH collected for a store between start and end (ISO strings).

    Sums order.payments[] where method == 'CASH' (the canonical tender field;
    `mode` tolerated as a legacy alias). Negative CASH tenders (refunds) are
    returned separately so the reconciliation can show sales vs refunds.

    MONEY-LEAK FIX: a money refund is recorded ONLY as a `returns` doc
    (returns.py create_return) -- POS never writes a negative payments[] row --
    so cash_refunds stayed 0 and Day-End showed a false SHORT every time cash
    was refunded. Refunds are therefore ALSO netted from the RETURNS collection,
    windowed on the RETURN's OWN created_at (= the refund date; the original
    sale's day-window would book the refund into the wrong day). Deliberately
    NOT modelled as negative payments[] rows: that would corrupt the
    payment_status ladder, the online-mapper staff_sum floor, the cumulative
    refund cap, Tally receipt vouchers and stamp_payment_ledgers.

    TENDER SOURCE (money-panel redesign, PR #964 round 2): the CASH figure is
    netted off the EXPLICIT per-tender breakdown the cashier recorded on the
    Returns screen (returns.refund_tenders for a RETURN; returns.collect_method
    for a COLLECT-direction EXCHANGE cash-IN) -- NEVER the inferred refund_method.
    Guessing the tender from the original sale regressed split-tender days (a
    UPI+CASH sale's cash-back kept a false shortage; a CASH+CARD sale's whole
    refund was cut from CASH -> negative drawer). A return that carries NO
    refund_tenders is UNKNOWN to the drawer and netted NOWHERE (staff keep the
    manual "cash paid out" workaround; the drawer never sees a fabricated number).
    Returns (cash_sales, cash_refunds) as positive magnitudes."""
    if db is None:
        return 0.0, 0.0
    # BUG-031: orders.created_at is a BSON Date, so an ISO-STRING $gte/$lte never
    # matches it (Mongo type-bracketing) -> cash_sales always 0 -> false drawer
    # variance. Match BOTH a datetime window (current Date-typed docs) AND a
    # string window (any legacy ISO-string created_at) via $or.
    #
    # The string clause is built from an ISO-STRING form of the bound even when a
    # caller passes a datetime (the annotation says str, but the IST-day helpers
    # elsewhere hand out naive-UTC datetimes) -- returns.created_at is string-only
    # and would type-bracket to no match against a datetime bound, silently
    # resurrecting the whole false-shortage bug. Coerce so it never can.
    or_clauses = _created_at_or_clauses(start_iso, end_iso)
    match: Dict = {"store_id": store_id}
    if or_clauses:
        match["$or"] = or_clauses

    cash_sales = 0.0
    cash_refunds = 0.0
    try:
        cursor = db.get_collection("orders").find(match, {"_id": 0, "payments": 1})
        for o in cursor:
            for p in o.get("payments") or []:
                method = str(p.get("method") or p.get("mode") or "").upper()
                if method != "CASH":
                    continue
                try:
                    amt = float(p.get("amount", 0) or 0)
                except (TypeError, ValueError):
                    amt = 0.0
                if amt >= 0:
                    cash_sales += amt
                else:
                    cash_refunds += -amt
    except Exception:
        return 0.0, 0.0

    # Returns-collection netting (see docstring). created_at on returns docs is
    # an ISO STRING (datetime.now().isoformat()); reuse the same dual
    # string/datetime $or window (BUG-031 pattern) for safety. historical:True
    # docs (Shopify-era imports) settled OUTSIDE the drawer and are excluded.
    #
    # TENDER SOURCE (money-panel redesign): net off the EXPLICIT refund_tenders /
    # collect_method the cashier recorded -- NEVER the inferred refund_method
    # (guessing it regressed split-tender days). A return with no refund_tenders
    # is UNKNOWN to the drawer and netted NOWHERE (staff keep the manual "cash
    # paid out" workaround; the drawer never gets a fabricated number).
    #
    # Fail-soft is NON-DESTRUCTIVE: accumulate into locals and merge ONLY after
    # the cursor drains, so a mid-cursor error leaves the payments-derived
    # figures untouched (never a half-netted drawer presented as authoritative).
    try:
        from ...services.tender_routing import canonicalize_tender

        add_cash_refunds = 0.0
        add_cash_sales = 0.0
        ret_match: Dict = {
            "store_id": store_id,
            "status": "COMPLETED",
            "historical": {"$ne": True},
            "$or": or_clauses,
        }
        rcursor = db.get_collection("returns").find(
            ret_match,
            {
                "_id": 0,
                "return_type": 1,
                "status": 1,
                "historical": 1,
                "refund_tenders": 1,
                "collect_method": 1,
                "collect_amount": 1,
                "settlement": 1,
            },
        )
        for r in rcursor:
            # Belt-and-braces re-checks (a stub collection may ignore the
            # filter; dirty data must never move the drawer figure).
            if str(r.get("status") or "").upper() != "COMPLETED":
                continue
            if r.get("historical") is True:
                continue
            rtype = str(r.get("return_type") or "").upper()
            if rtype == "RETURN":
                # Sum the CASH legs of the EXPLICIT refund breakdown only.
                for t in r.get("refund_tenders") or []:
                    if canonicalize_tender((t or {}).get("method")) != "CASH":
                        continue
                    try:
                        amt = float((t or {}).get("amount") or 0)
                    except (TypeError, ValueError):
                        amt = 0.0
                    if amt > 0:
                        add_cash_refunds += amt
            elif rtype == "EXCHANGE":
                # A COLLECT is a NEW cash-in; key it off collect_method (absent
                # -> UNKNOWN, added nowhere).
                direction = str(
                    (r.get("settlement") or {}).get("direction") or ""
                ).upper()
                if direction != "COLLECT":
                    continue
                if canonicalize_tender(r.get("collect_method")) != "CASH":
                    continue
                try:
                    coll_amt = float(r.get("collect_amount") or 0)
                except (TypeError, ValueError):
                    coll_amt = 0.0
                if coll_amt > 0:
                    add_cash_sales += coll_amt
            # CREDIT_NOTE / EXCHANGE-refund issue STORE CREDIT (no drawer cash).
        cash_refunds += add_cash_refunds
        cash_sales += add_cash_sales
    except Exception:
        logger.warning(
            "[CASH-DRAWER] returns netting skipped (fail-soft)", exc_info=True
        )
    return round(cash_sales, 2), round(cash_refunds, 2)


# ===========================================================================
# OFF-TILL EXPENSE HEADS -- an EXPENSE-CLASSIFICATION correction, not a
# redaction. Read this before changing the drawer maths below.
# ===========================================================================
# OWNER RULING 2026-08-14, asked directly: are salaries, staff advances or
# PF/ESI ever paid out of a shop cash till?
#
#     "NEVER - always bank, cheque or from the office."
#
# Today the code disagrees with him, and the disagreement costs money at
# day-end rather than merely leaking one. ExpenseCreate.payment_mode is
# Optional (routers/expenses.py), and the loop below treats a BLANK mode as
# cash. So a wage bill typed into the free-text expense box with the payment-
# mode dropdown left alone is subtracted from the drawer:
#
#     expected_cash = opening + cash_sales - cash_refunds - cash_expenses
#
# Subtracting money that never left the till makes `expected` too LOW, so the
# physical count reads as a large phantom OVERAGE. That fires in every affected
# store the first month somebody books pay as an expense, and an "overage" the
# size of a month's wages is exactly the kind of alarm a manager learns to
# ignore. This is a money-correctness bug first.
#
# IT ALSO CLOSES A LEAK THIS BRANCH OPENED. Round 1 (47bd52c) made
# GET /finance/pnl payroll-EXCLUSIVE for the manager tier while these till
# routes stayed payroll-INCLUSIVE over the SAME store -- the four roles on
# rbac_policy rows for /finance/pnl and /finance/cash-register/sessions are the
# same four roles. Two requests, one subtraction, wage bill recovered. See
# services/salary_visibility.py "THE SECOND COROLLARY".
#
# THEREFORE THIS EXCLUSION IS IDENTICAL FOR EVERY ROLE, ADMIN AND SUPERADMIN
# INCLUDED. There is deliberately no role check in this function or its
# callers. A role-conditional drawer would just be a THIRD asymmetry to
# subtract across; removing the differential entirely leaves nothing to
# subtract. It also means the number a human counts money against is the same
# number for everyone who looks at it, which is the only defensible property
# for a cash-drawer figure.
#
# THE DELIBERATE CHOICE, AND ITS FAILURE MODE
# -------------------------------------------
# What if somebody books a payroll-shaped head with payment_mode EXPLICITLY set
# to CASH? That contradicts the owner. Two options:
#
#   (a) honour the explicit mode -- drawer stays arithmetically right in the
#       case where it really did happen, but the wage bill is back in a figure
#       the manager tier can read, and the cross-route subtraction reopens.
#   (b) exclude it ALWAYS, whatever the mode.
#
# WE TAKE (b). The ruling is unconditional, so an explicit CASH mode on a pay
# head is a mis-booking, and (a) would make the leak controllable by whoever
# books the expense -- an attacker-chosen field. Failure mode of (b), stated
# plainly because the owner has to live with it at day-end: IF cash genuinely
# went out of a till for a pay head, that drawer reads SHORT by that amount.
# A shortage gets investigated; it is not silent. AND IT IS NOT SILENT HERE
# EITHER -- `excluded_count` below drives a visible note on the close screen
# and the running preview (no amount, no head name), because a number a human
# counts money against must never be adjusted behind their back.
#
# RESIDUAL, DISCLOSED: on a store where cash really did leave the till for a
# pay head, the resulting variance IS that amount. Deriving it requires the
# owner's "never" to have been broken, and in that world the manager watched
# the cash leave. Not closable without reintroducing (a).


class _CashExpenseWindow(NamedTuple):
    """Drawer-relevant cash expenses, plus what was left out of them.

    ``excluded_count`` is a COUNT and never an amount: it exists so the screen
    can say "something here is not paid from the till" without handing the
    manager tier the pay figure the rest of this PR withholds.
    """

    total: float
    excluded_count: int


def _cash_expenses_for_window(
    db, store_id: str, start_iso: str, end_iso: Optional[str]
) -> _CashExpenseWindow:
    """Cash payouts from the drawer for a store in the window.

    Expenses use `expense_date` (ISO date) and `payment_mode`. Only CASH-mode
    expenses come out of the physical drawer; UPI/CARD/BANK don't. Counts
    APPROVED / PAID / SENT_TO_ACCOUNTANT spends (anything that represents money
    actually disbursed). Payroll-shaped heads are excluded outright -- see the
    block above. Fail-soft to (0.0, 0)."""
    if db is None:
        return _CashExpenseWindow(0.0, 0)
    # BUG-104. `expense_date` is an operator-typed IST CALENDAR DATE, so BOTH
    # bounds must be IST days. `start_iso` is the session's `opened_at`, a
    # naive-UTC instant: its first ten characters are the UTC day, so a till
    # opened 00:00-05:30 IST reached back into the PREVIOUS IST day and
    # subtracted a second day of payouts from the drawer (expected too low ->
    # an honest count reads as an overage). The upper bound was also
    # INCONSISTENT with the lower one -- the UTC face when a close passed
    # `end_iso`, the IST face when the live preview passed None -- so the two
    # ends of one window sat in different calendar frames.
    start_day, end_day = _ist_day_window(start_iso, end_iso)
    total = 0.0
    excluded = 0
    try:
        cursor = db.get_collection("expenses").find(
            {
                "store_id": store_id,
                "expense_date": {"$gte": start_day, "$lte": end_day},
            },
            {
                "_id": 0,
                "amount": 1,
                "payment_mode": 1,
                "status": 1,
                "expense_date": 1,
                "category": 1,
            },
        )
        for e in cursor:
            mode = str(e.get("payment_mode") or "").upper()
            if mode and mode != "CASH":
                continue  # unknown mode counts as cash (conservative)
            status = str(e.get("status") or "").upper()
            if status not in ("APPROVED", "PAID", "SENT_TO_ACCOUNTANT", "REIMBURSED"):
                continue
            # Salaries, advances and PF/ESI never come out of a shop till
            # (owner, 2026-08-14). Same matcher as /pnl and /budgets, imported
            # from services/salary_visibility -- never a local copy.
            if is_payroll_shaped_expense(e.get("category")):
                excluded += 1
                continue
            try:
                total += float(e.get("amount", 0) or 0)
            except (TypeError, ValueError):
                pass
    except Exception:
        return _CashExpenseWindow(0.0, 0)
    return _CashExpenseWindow(round(total, 2), excluded)


# Shown verbatim to whoever is counting the drawer. No amount, no head name:
# it tells them the expected figure deliberately leaves something out and who
# to ask, which is all they need to stop themselves "correcting" a real count.
OFF_TILL_EXPENSE_MESSAGE = (
    "One or more expenses booked here in this period are not paid out of the "
    "shop till, so they are left out of the expected-cash figure. If your "
    "count does not tally, check with an administrator before adjusting "
    "anything."
)


# A CASH expense whose amount matches a recorded CASH refund to within a paisa
# is almost certainly the SAME money keyed twice (the pre-fix workaround was to
# log a customer refund as a "cash paid out"). A bare co-occurrence check would
# fire on any day with a refund and Rs 200 of chai money and be tuned out inside
# a week, so the advisory requires an AMOUNT match (or an explicitly
# refund-flavoured category) and names both figures.
_REFUND_EXPENSE_HINTS = ("REFUND", "RETURN", "CUSTOMER REFUND")
_DOUBLE_ENTRY_EPSILON = 0.01


def _cash_refund_legs_for_window(db, store_id: str, start_iso, end_iso) -> List[float]:
    """Individual recorded CASH refund leg amounts in the window (drawer-truth
    from returns.refund_tenders). Feeds the amount-matched double-entry
    advisory. Fail-soft -> []."""
    if db is None:
        return []
    legs: List[float] = []
    try:
        from ...services.tender_routing import canonicalize_tender

        # The SAME WINDOW as the refund TOTAL -- same bound values AND the
        # same dual-type clause shape (_created_at_or_clauses). Emitting only
        # the string arm here left a Date-typed refund visible to the total
        # and invisible to these legs, and the advisory gives up on an empty
        # leg list: the drawer would deduct the money while the warning about
        # it silently switched off.
        leg_match: Dict = {
            "store_id": store_id,
            "status": "COMPLETED",
            "historical": {"$ne": True},
        }
        leg_or = _created_at_or_clauses(start_iso, end_iso)
        if leg_or:
            leg_match["$or"] = leg_or
        cursor = db.get_collection("returns").find(
            leg_match,
            {"_id": 0, "return_type": 1, "refund_tenders": 1},
        )
        for r in cursor:
            if str(r.get("return_type") or "").upper() != "RETURN":
                continue
            for t in r.get("refund_tenders") or []:
                if canonicalize_tender((t or {}).get("method")) != "CASH":
                    continue
                try:
                    amt = float((t or {}).get("amount") or 0)
                except (TypeError, ValueError):
                    amt = 0.0
                if amt > 0:
                    legs.append(round(amt, 2))
    except Exception:  # noqa: BLE001
        return []
    return legs


def _refund_double_entry_advisory(
    db, store_id: str, start_iso, end_iso, cash_refunds: float
) -> Optional[Dict]:
    """Amount-matched double-count advisory, or None.

    Returns ``{matched_amount, cash_refunds, cash_expenses_matched, message}``
    when a CASH expense in the window matches a recorded CASH refund leg to the
    paisa (or carries a refund-flavoured category/note). Advisory ONLY -- never
    blocks a close and never adjusts a figure (a genuine same-day petty-cash
    payout must stay recordable)."""
    if db is None or not cash_refunds:
        return None
    legs = _cash_refund_legs_for_window(db, store_id, start_iso, end_iso)
    if not legs:
        return None
    try:
        # THE SAME WINDOW the drawer itself uses (BUG-104). This advisory
        # names a specific payout and tells a manager to delete it, so reading
        # a different window from _cash_expenses_for_window is worse than
        # reading none: on a till opened 00:00-05:30 IST it reached back a day
        # and told them to remove a legitimate entry the PREVIOUS day's close
        # had already subtracted -- turning a correct count into a shortage.
        start_day, end_day = _ist_day_window(start_iso, end_iso)
        cursor = db.get_collection("expenses").find(
            {
                "store_id": store_id,
                "expense_date": {"$gte": start_day, "$lte": end_day},
            },
            {
                "_id": 0,
                "amount": 1,
                "payment_mode": 1,
                "status": 1,
                "category": 1,
                "note": 1,
                "description": 1,
            },
        )
        for e in cursor:
            mode = str(e.get("payment_mode") or "").upper()
            if mode and mode != "CASH":
                continue
            status = str(e.get("status") or "").upper()
            if status not in ("APPROVED", "PAID", "SENT_TO_ACCOUNTANT", "REIMBURSED"):
                continue
            try:
                amt = round(float(e.get("amount", 0) or 0), 2)
            except (TypeError, ValueError):
                continue
            blob = " ".join(
                str(e.get(k) or "") for k in ("category", "note", "description")
            ).upper()
            refundish = any(h in blob for h in _REFUND_EXPENSE_HINTS)
            match = any(abs(amt - leg) <= _DOUBLE_ENTRY_EPSILON for leg in legs)
            if match or (refundish and amt > 0):
                return {
                    "matched_amount": amt,
                    "cash_refunds": round(float(cash_refunds), 2),
                    "reason": "AMOUNT_MATCH" if match else "REFUND_CATEGORY",
                    "message": (
                        f"A cash expense of Rs {amt:.2f} matches a customer cash "
                        f"refund already auto-deducted from this drawer "
                        f"(recorded refunds Rs {float(cash_refunds):.2f}). If it is "
                        "the same money, remove the manual entry - otherwise "
                        "ignore this notice."
                    ),
                }
    except Exception:  # noqa: BLE001
        return None
    return None
