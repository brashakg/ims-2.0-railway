"""
IMS 2.0 - End-of-day petty-cash settlement (per store / per day)
================================================================
The petty-cash FLOAT engine (``petty_cash_service``) already opens a float,
tops it up, debits it on APPROVED petty-cash payouts, and reverses on rejection
-- every move is a single guarded ``money_guard`` write on ONE doc in
``petty_cash_floats`` (the balance can never go negative). What it did NOT have
was an END-OF-DAY settlement: a manager physically counting the cash drawer of
petty cash at close, recording the counted figure, and computing the variance
(counted vs the system's expected closing) so a leak / un-recorded payout is
caught the day it happens.

This module adds exactly that, MIRRORING the cash-register / blind-EOD pattern
(``cash_register.compute_variance`` + the ``till``/``eod_tally`` session model):

  * POSITION (read): derive the day's opening float, total payouts (debits),
    total top-ups + reversals (credits), and the EXPECTED closing balance for a
    store on a given IST day, straight from the float doc's append-only
    ``ledger`` (the same forensic trail money_guard already stamps). No new
    source of truth -- the float ledger IS the source.

        opening_float  = float balance at the START of the settle day
        credits_today  = sum of CREDIT ledger rows dated the settle day
                         (top-ups + payout reversals)
        debits_today   = sum of DEBIT ledger rows dated the settle day (payouts)
        expected_close = opening_float + credits_today - debits_today

    Because every ledger row stamps ``balance_after``, the expected close is
    also exactly the float's current balance when settling TODAY; computing it
    from the day's rows lets us settle a PAST day correctly too.

  * SETTLE (write): record the manager's counted closing cash, compute
    ``variance = counted - expected`` (POSITIVE = OVER / excess cash in the
    box, NEGATIVE = SHORT / missing cash -- identical sign convention to
    cash_register.compute_variance), classify against a tolerance band, and
    CLOSE the day. One settlement doc per (store, settle_date); a second settle
    is IDEMPOTENT (returns the already-recorded settlement, never re-writes the
    variance). The structural guarantee is a UNIQUE compound index on
    (store_id, settle_date).

Money safety: a settlement is a RECORD, not a balance mutation -- it does NOT
move the float (the float keeps running into the next day; petty cash is a
revolving imprest, not a till that zeroes nightly). So no money_guard write is
needed here; the only invariant is "exactly one settlement per store-day", held
by the unique index + a guarded insert.

Fail-soft (SYSTEM_INTENT): ``db=None`` / a DB hiccup never raises -- reads return
an empty/not-settled envelope, writes return a 503 envelope. No emoji (Windows
cp1252); ASCII log tag [PETTYSETTLE].
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from . import cash_register as cr

logger = logging.getLogger(__name__)

COLLECTION = "petty_cash_settlements"
FLOAT_COLLECTION = "petty_cash_floats"

# Settlement status enum -- explicit strings, never colour flags (DECISIONS).
STATUS_SETTLED = "SETTLED"

# Default tolerance band (rupees): a variance within +/- this is BALANCED.
# Petty cash is small-denomination cash; a 0-rupee tolerance would flag normal
# rounding. E2-overridable per store via policy_engine.
_DEFAULT_TOLERANCE = 0.0


def _now_iso() -> str:
    """IST-naive ISO timestamp (matches petty_cash_service / money_guard)."""
    try:
        from ..utils.ist import now_ist_naive

        return now_ist_naive().isoformat()
    except Exception:  # noqa: BLE001
        from datetime import datetime

        return datetime.now().isoformat()


def _ist_today_iso() -> str:
    """Current IST calendar date as YYYY-MM-DD."""
    try:
        from ..utils.ist import ist_today

        return ist_today().isoformat()
    except Exception:  # noqa: BLE001
        from datetime import date

        return date.today().isoformat()


def _coll(db, name: str):
    """Resolve a collection from a DB handle. None when no DB."""
    if db is None:
        return None
    getter = getattr(db, "get_collection", None)
    if callable(getter):
        try:
            return getter(name)
        except Exception:  # noqa: BLE001
            return None
    try:
        return db[name]
    except Exception:  # noqa: BLE001
        return None


def ensure_indexes(db) -> None:
    """Idempotent index creation. The UNIQUE (store_id, settle_date) compound
    index is the structural guarantee behind one-settlement-per-store-day (and
    the idempotent re-settle). Best-effort; never raises."""
    coll = _coll(db, COLLECTION)
    if coll is None:
        return
    try:
        coll.create_index([("store_id", 1), ("settle_date", 1)], unique=True)
        coll.create_index("settle_date")
    except Exception:  # noqa: BLE001
        logger.debug("[PETTYSETTLE] ensure_indexes skipped", exc_info=True)


# ---------------------------------------------------------------------------
# Date helpers (pure)
# ---------------------------------------------------------------------------


def _ledger_row_date(row: Dict[str, Any]) -> str:
    """The YYYY-MM-DD of a float ledger row from its created_at. Empty -> ''.

    Ledger rows are stamped with an IST-naive ISO ``created_at`` (see
    petty_cash_service._ledger_row), so the first 10 chars are the IST day."""
    created = row.get("created_at") or ""
    return str(created)[:10]


def compute_position(
    ledger: Optional[List[Dict[str, Any]]],
    settle_date: str,
    current_balance: float,
) -> Dict[str, Any]:
    """Derive the day's petty-cash position from the float's append-only ledger.

    PURE (no DB): takes the float's ``ledger`` list, the IST ``settle_date``
    (YYYY-MM-DD), and the float's CURRENT balance. Returns the opening float,
    the day's credit/debit totals, the expected closing, and the day's rows.

    Method (robust for settling a PAST day, not just today):
      * day rows  = ledger rows whose created_at date == settle_date
      * credits   = sum of day CREDIT deltas (top-ups + reversals)
      * debits    = sum of day DEBIT deltas (payouts; delta stored positive)
      * closing   = the balance_after of the LAST day row, else (no movement)
                    the current balance
      * opening   = closing - credits + debits
                    (== balance at the start of the settle day)

    All deltas are stored as POSITIVE magnitudes with direction in ``type``
    (see petty_cash_service._ledger_row), so we sum by type, never by sign.
    """
    rows = [r for r in (ledger or []) if isinstance(r, dict)]
    day_rows = [r for r in rows if _ledger_row_date(r) == str(settle_date)]

    credits_today = 0.0
    debits_today = 0.0
    for r in day_rows:
        try:
            delta = abs(float(r.get("delta") or 0.0))
        except (TypeError, ValueError):
            delta = 0.0
        if (r.get("type") or "").upper() == "CREDIT":
            credits_today += delta
        elif (r.get("type") or "").upper() == "DEBIT":
            debits_today += delta

    try:
        bal_now = float(current_balance or 0.0)
    except (TypeError, ValueError):
        bal_now = 0.0

    if day_rows:
        # Closing == balance_after of the last movement on the settle day. Fall
        # back to the current balance if that row was never stamped.
        last = day_rows[-1]
        closing = last.get("balance_after")
        try:
            closing = float(closing) if closing is not None else bal_now
        except (TypeError, ValueError):
            closing = bal_now
    else:
        # No movement on the settle day -> closing == opening == current balance.
        closing = bal_now

    opening = round(closing - credits_today + debits_today, 2)
    expected_close = round(closing, 2)

    return {
        "opening_float": opening,
        "credits_today": round(credits_today, 2),
        "debits_today": round(debits_today, 2),
        "payouts_count": sum(1 for r in day_rows if (r.get("type") or "").upper() == "DEBIT"),
        "expected_closing": expected_close,
        "day_ledger": day_rows,
    }


# ---------------------------------------------------------------------------
# Config (E2 seam)
# ---------------------------------------------------------------------------


def tolerance_for(store_id: Optional[str] = None,
                  entity_id: Optional[str] = None) -> float:
    """E2-resolvable variance tolerance band (rupees). Safe code default."""
    try:
        from .policy_engine import get_policy

        scope: Dict[str, Any] = {}
        if store_id:
            scope["store_id"] = store_id
        if entity_id:
            scope["entity_id"] = entity_id
        return abs(float(get_policy(
            "petty_cash.settlement_tolerance", scope or None,
            default=_DEFAULT_TOLERANCE,
        )))
    except Exception:  # noqa: BLE001
        return _DEFAULT_TOLERANCE


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def get_day_position(db, store_id: str, settle_date: Optional[str] = None,
                     entity_id: Optional[str] = None) -> Dict[str, Any]:
    """Read-only EOD position for a store on an IST day.

    Returns the opening float, the day's credit/debit totals, the expected
    closing, whether the float exists/is open, and -- if already settled --
    the recorded settlement (counted/variance/status). Fail-soft: no DB / no
    float -> an exists=False envelope (never raises)."""
    sd = (str(settle_date)[:10] if settle_date else _ist_today_iso())
    base = {
        "ok": True,
        "store_id": store_id,
        "settle_date": sd,
        "exists": False,
        "float_status": None,
        "opening_float": 0.0,
        "credits_today": 0.0,
        "debits_today": 0.0,
        "payouts_count": 0,
        "expected_closing": 0.0,
        "tolerance": tolerance_for(store_id, entity_id),
        "settled": False,
        "settlement": None,
        "day_ledger": [],
    }

    floats = _coll(db, FLOAT_COLLECTION)
    if floats is None:
        return base
    try:
        fdoc = floats.find_one({"store_id": store_id}, {"_id": 0})
    except Exception:  # noqa: BLE001
        return base
    if not fdoc:
        # No float open: still report any settlement that may have been recorded.
        settlement = _find_settlement(db, store_id, sd)
        base["settled"] = settlement is not None
        base["settlement"] = settlement
        return base

    pos = compute_position(
        fdoc.get("ledger") or [], sd, float(fdoc.get("balance") or 0.0)
    )
    settlement = _find_settlement(db, store_id, sd)
    base.update({
        "exists": True,
        "float_status": fdoc.get("status"),
        "opening_float": pos["opening_float"],
        "credits_today": pos["credits_today"],
        "debits_today": pos["debits_today"],
        "payouts_count": pos["payouts_count"],
        "expected_closing": pos["expected_closing"],
        "settled": settlement is not None,
        "settlement": settlement,
        "day_ledger": pos["day_ledger"],
    })
    return base


def _find_settlement(db, store_id: str, settle_date: str) -> Optional[Dict[str, Any]]:
    """The recorded settlement for a store-day, or None. Fail-soft."""
    coll = _coll(db, COLLECTION)
    if coll is None:
        return None
    try:
        return coll.find_one(
            {"store_id": store_id, "settle_date": str(settle_date)[:10]},
            {"_id": 0},
        )
    except Exception:  # noqa: BLE001
        return None


def get_settlement(db, store_id: str, settle_date: str) -> Optional[Dict[str, Any]]:
    """Public read of one settlement (or None)."""
    return _find_settlement(db, store_id, str(settle_date)[:10])


def list_settlements(db, store_id: str, *, limit: int = 50,
                     from_date: Optional[str] = None,
                     to_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """Settlement history for a store, newest first. Fail-soft -> []."""
    coll = _coll(db, COLLECTION)
    if coll is None:
        return []
    query: Dict[str, Any] = {"store_id": store_id}
    if from_date or to_date:
        rng: Dict[str, Any] = {}
        if from_date:
            rng["$gte"] = str(from_date)[:10]
        if to_date:
            rng["$lte"] = str(to_date)[:10]
        query["settle_date"] = rng
    try:
        cursor = coll.find(query, {"_id": 0})
        try:
            cursor = cursor.sort("settle_date", -1).limit(int(limit))
        except Exception:  # noqa: BLE001 - fake/cursorless backends
            pass
        rows = list(cursor)
    except Exception:  # noqa: BLE001
        return []
    # Defensive sort + cap for backends whose cursor ignored sort/limit.
    rows.sort(key=lambda r: r.get("settle_date") or "", reverse=True)
    return rows[: int(limit)]


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def settle_day(db, *, store_id: str, counted_closing: float, actor: str,
               settle_date: Optional[str] = None, note: Optional[str] = None,
               entity_id: Optional[str] = None) -> Dict[str, Any]:
    """Record the counted closing cash for a store-day and compute the variance.

    Idempotent per (store_id, settle_date): a second settle returns the
    EXISTING settlement (http 409, ``already_settled``) and never re-writes the
    variance -- the unique compound index makes the race-losing insert raise
    E11000, which is caught and converted to the same already-settled envelope.

    Returns ``{"ok": True, ...settlement}`` or ``{"ok": False, "http", "error"}``.
    """
    coll = _coll(db, COLLECTION)
    if coll is None:
        return {"ok": False, "http": 503, "error": "no_db"}

    try:
        counted = round(float(counted_closing), 2)
    except (TypeError, ValueError):
        return {"ok": False, "http": 422, "error": "invalid_counted"}
    if counted < 0:
        return {"ok": False, "http": 422, "error": "invalid_counted"}

    sd = (str(settle_date)[:10] if settle_date else _ist_today_iso())

    # Idempotency: already settled -> return the existing record (no re-write).
    existing = _find_settlement(db, store_id, sd)
    if existing is not None:
        return {"ok": False, "http": 409, "error": "already_settled",
                "settlement": existing}

    # Float must exist to settle (you cannot count a drawer that was never
    # opened). A read against the float doc gives us the expected closing.
    floats = _coll(db, FLOAT_COLLECTION)
    fdoc = None
    if floats is not None:
        try:
            fdoc = floats.find_one({"store_id": store_id})
        except Exception:  # noqa: BLE001
            fdoc = None
    if not fdoc:
        return {"ok": False, "http": 404, "error": "float_not_open"}

    pos = compute_position(
        fdoc.get("ledger") or [], sd, float(fdoc.get("balance") or 0.0)
    )
    expected = pos["expected_closing"]
    tolerance = tolerance_for(store_id, entity_id)
    variance = cr.compute_variance(counted, expected)
    status_band = cr.variance_status(variance, tolerance)

    now = _now_iso()
    doc = {
        "settlement_id": f"PCS-{uuid.uuid4().hex[:12]}",
        "store_id": store_id,
        "entity_id": entity_id or fdoc.get("entity_id"),
        "settle_date": sd,
        "opening_float": pos["opening_float"],
        "credits_today": pos["credits_today"],
        "debits_today": pos["debits_today"],
        "payouts_count": pos["payouts_count"],
        "expected_closing": expected,
        "counted_closing": counted,
        "variance": variance,
        "variance_status": status_band,
        "tolerance": round(float(tolerance), 2),
        "status": STATUS_SETTLED,
        "note": (note or None),
        "settled_by": actor,
        "settled_at": now,
        "created_at": now,
    }

    try:
        coll.insert_one(dict(doc))
    except Exception as e:  # noqa: BLE001
        # A racing insert on the unique (store_id, settle_date) index (E11000)
        # means another caller settled this store-day first -- return the
        # already-settled record rather than a second, conflicting variance.
        cur = _find_settlement(db, store_id, sd)
        if cur is not None:
            return {"ok": False, "http": 409, "error": "already_settled",
                    "settlement": cur}
        logger.warning("[PETTYSETTLE] settle %s %s failed: %s", store_id, sd, e)
        return {"ok": False, "http": 503, "error": "write_failed"}

    _audit(db, store_id, doc, actor)
    logger.info(
        "[PETTYSETTLE] %s %s settled: counted Rs %.2f vs expected Rs %.2f "
        "-> variance Rs %.2f (%s) by %s",
        store_id, sd, counted, expected, variance, status_band, actor,
    )
    out = {k: v for k, v in doc.items()}
    out["ok"] = True
    return out


def _audit(db, store_id: str, doc: Dict[str, Any], actor: Optional[str]) -> None:
    """Append one settlement audit row via AuditRepository.create. Fail-soft --
    an audit failure NEVER undoes or blocks the settlement record."""
    if db is None:
        return
    try:
        coll = db.get_collection("audit_logs")
        if coll is None:
            return
        from database.repositories.audit_repository import AuditRepository

        repo = AuditRepository(coll)
        repo.create({
            "log_id": f"AUD-{uuid.uuid4().hex[:12]}",
            "action": "petty_cash.settle_day",
            "entity_type": "petty_cash_settlement",
            "entity_id": doc.get("settlement_id"),
            "user_id": actor,
            "actor": actor,
            "store_id": store_id,
            "source": "PETTYSETTLE",
            "before_state": None,
            "after_state": {
                "settle_date": doc.get("settle_date"),
                "expected_closing": doc.get("expected_closing"),
                "counted_closing": doc.get("counted_closing"),
                "variance": doc.get("variance"),
                "variance_status": doc.get("variance_status"),
            },
            "severity": "WARNING" if doc.get("variance_status") != "BALANCED" else "INFO",
            "timestamp": doc.get("settled_at"),
        })
    except Exception:  # noqa: BLE001
        logger.debug("[PETTYSETTLE] audit write skipped", exc_info=True)
