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

# Indian denomination ladder (paisa-exact). face is RUPEES; the count grid sums
# face*pieces in RUPEES then we convert to paisa once at the boundary so float
# noise never accumulates. Rs 2000 is withdrawn (kept out of the default grid,
# same as the existing cash_register module).
NOTE_FACES = (500, 200, 100, 50, 20, 10)
COIN_FACES = (10, 5, 2, 1)

# E2 policy keys (registered in policy_registry.py). Defaults here mirror the
# registry so a direct service call (no policy doc) still behaves.
POLICY_TOLERANCE = "till.variance_tolerance_paisa"
POLICY_REOPEN_ROLES = "till.reopen_roles"

_DEFAULT_TOLERANCE_PAISA = 0
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
    reconciliation matches the day-close. Falls back to ``opened_at`` (open span)
    if ``session_date`` is missing/unparseable. Never raises."""
    from datetime import date as _date, timedelta

    day = session.get("session_date")
    try:
        from ..utils.ist import ist_day_start_utc

        d = _date.fromisoformat(str(day)[:10])
        start = ist_day_start_utc(d)
        return start, start + timedelta(days=1)
    except Exception:  # noqa: BLE001
        return session.get("opened_at"), None


def _coerce_pieces(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def _coerce_face(value: Any) -> Optional[int]:
    try:
        f = int(value)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def normalize_denominations(rows: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Clean a list of {face, kind, pieces} dicts: drop bad faces, clamp pieces
    to non-negative ints, default kind to 'note', and attach the computed line
    total in PAISA (face*100*pieces). Order is preserved as supplied so the
    stored doc mirrors what the cashier entered."""
    out: List[Dict[str, Any]] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        face = _coerce_face(r.get("face"))
        if face is None:
            continue
        pieces = _coerce_pieces(r.get("pieces"))
        kind = str(r.get("kind") or "note").lower()
        if kind not in ("note", "coin"):
            kind = "note"
        out.append(
            {
                "face": face,
                "kind": kind,
                "pieces": pieces,
                "line_total_paisa": face * 100 * pieces,
            }
        )
    return out


def total_paisa_from_denominations(rows: Optional[List[Dict[str, Any]]]) -> int:
    """Sum of face*100*pieces across denomination rows, in PAISA. Pure."""
    return sum(r["line_total_paisa"] for r in normalize_denominations(rows))


def denomination_ladder() -> List[Dict[str, Any]]:
    """The blank denomination grid the UI starts from (pieces all zero)."""
    rows: List[Dict[str, Any]] = []
    for face in NOTE_FACES:
        rows.append({"face": face, "kind": "note", "pieces": 0})
    for face in COIN_FACES:
        rows.append({"face": face, "kind": "coin", "pieces": 0})
    return rows


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
        cash_sales_paisa = E5 reconcile_window CASH net (collected - refunded)
        expected_cash_paisa = opening_float + cash_sales - cash_payouts
        (variance = counted - expected is computed at blind-submit)

    The per-tender ``by_mode`` from E5 rides along so the Z-Read can show sales by
    tender (UPI/CARD/etc.) -- only CASH feeds the drawer-expected figure. This is
    the SAME E5 reader the day-close already uses; there is no parallel orders
    aggregation. DB absent -> a zero-sales envelope (never raises)."""
    from . import tender_reconciliation as tr

    recon = tr.reconcile_window(db, store_id, window_start, window_end)
    by_mode = recon.get("by_mode") or {}
    cash_row = by_mode.get("CASH") or {}
    cash_net_rupees = float(cash_row.get("net", 0) or 0)
    cash_sales_paisa = _to_int_paisa_from_rupees(cash_net_rupees)

    opening = int(opening_float_paisa or 0)
    payouts = int(cash_payouts_paisa or 0)
    expected_cash_paisa = opening + cash_sales_paisa - payouts
    return {
        "opening_float_paisa": opening,
        "cash_sales_paisa": cash_sales_paisa,
        "cash_payouts_paisa": payouts,
        "expected_cash_paisa": expected_cash_paisa,
        "by_mode": by_mode,
        "total_net_rupees": recon.get("total_net", 0.0),
        "window_start": recon.get("window_start"),
        "window_end": recon.get("window_end"),
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


def open_session(
    db,
    *,
    store_id: str,
    session_date: str,
    opening_denominations: Optional[List[Dict[str, Any]]] = None,
    opening_float_paisa: Optional[int] = None,
    shift: Optional[str] = None,
    note: Optional[str] = None,
    actor: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Open a blind till session for a (store, cashier, date).

    Enforces ONE active (OPEN/BLIND_SUBMITTED) session per (store, cashier, date)
    -> a second open returns ``{"ok": False, "error": "already_open", "http":
    409}``. Single-document insert. NO expected figure is computed or stored at
    open time (blind enforcement). Returns ``{"ok": True, "session"}``."""
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

    # One active session per (store, cashier, date).
    try:
        existing = coll.find_one(
            {
                "store_id": store_id,
                "cashier_id": cashier_id,
                "session_date": session_date,
                "status": {"$in": [STATUS_OPEN, STATUS_BLIND_SUBMITTED]},
            }
        )
    except Exception:  # noqa: BLE001
        existing = None
    if existing is not None:
        return {"ok": False, "error": "already_open", "http": 409}

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
        "opening_denominations": denoms,
        "opened_at": now,
        "opened_by": cashier_id,
        "opening_note": note,
        # blind-submit + lock fields (hidden / null until those steps)
        "blind_count_paisa": None,
        "blind_denominations": [],
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
    blind_count_paisa: Optional[int] = None,
    cash_payouts_paisa: int = 0,
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
    if blind_count_paisa is not None and int(blind_count_paisa) != denom_total:
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
    payouts = int(cash_payouts_paisa or 0)
    exp = compute_expected(db, store_id, ws, we, int(session.get("opening_float_paisa", 0) or 0), payouts)
    expected_cash_paisa = exp["expected_cash_paisa"]
    variance_paisa = counted - expected_cash_paisa
    tol = get_variance_tolerance_paisa(store_id=store_id)
    vstatus = variance_status(variance_paisa, tol)

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
                    "blind_count_paisa": counted,
                    "cash_payouts_paisa": payouts,
                    "expected_cash_paisa": expected_cash_paisa,
                    "cash_sales_paisa": exp["cash_sales_paisa"],
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
# Lifecycle: lock (manager reveals variance + soft-locks the Z-Read)
# ---------------------------------------------------------------------------


def lock_session(db, session_id: str, *, actor: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Soft-lock the Z-Read ATOMICALLY -- the SAME guarded-find_one_and_update
    shape as E5's ``lock_reconciliation``: a single guarded update on
    ``status:BLIND_SUBMITTED`` flips it to LOCKED in one op. Two concurrent locks
    -> exactly one wins (the loser sees the doc no longer BLIND_SUBMITTED).

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
        },
        store_id=store_id,
        severity="WARNING" if str(locked.get("variance_status")) != "BALANCED" else "INFO",
        detail={"session_date": day},
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
# (blind enforcement at the DATA layer, not just the UI).
_CASHIER_HIDDEN_FIELDS = (
    "expected_cash_paisa",
    "variance_paisa",
    "variance_status",
    "cash_sales_paisa",
    "by_mode",
    "tolerance_paisa",
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
        "by_mode": session.get("by_mode") or {},
        # The Z-Read identity: opening + cash_sales - payouts = expected.
        "cash_sales_paisa": cash_sales,
        "cash_payouts_paisa": payouts,
        "expected_cash_paisa": expected,
        "counted_cash_paisa": counted,
        "variance_paisa": variance,
        "variance_status": session.get("variance_status"),
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
    """Idempotent. A partial-unique index so AT MOST ONE active
    (OPEN/BLIND_SUBMITTED) session can exist per (store, cashier, date), plus a
    listing index. Fail-soft."""
    if db is None:
        return
    try:
        coll = db.get_collection(_SESSIONS_COLLECTION)
        coll.create_index(
            [("store_id", 1), ("cashier_id", 1), ("session_date", 1)],
            unique=True,
            partialFilterExpression={"status": {"$in": [STATUS_OPEN, STATUS_BLIND_SUBMITTED]}},
            name="uniq_active_till_per_cashier_day",
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
