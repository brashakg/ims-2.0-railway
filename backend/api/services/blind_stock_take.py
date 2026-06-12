"""Blind stock take (F15) -- transparent, soft-lockable physical count.

Staff physically count stock WITHOUT first seeing the system on-hand (blind =
no anchoring). On manager LOCK the system reveals per-SKU expected-vs-counted
VARIANCE, a summary, and SOFT-LOCKS the session (transparent: who/when, manager
re-openable with a mandatory reason, audited). A confirmed variance can enqueue
a stock-ADJUSTMENT PROPOSAL (reversible, manager-approved) -- it does NOT
silently mutate on-hand.

Mirrors the merged #23 (eod_tally) blind-entry + redact-before-reveal + atomic
soft-lock find_one_and_update pattern, and builds on the existing
inventory.py ``stock_counts`` flow (does NOT fork it).

Money/valuation is integer paise; cost is read from the product doc.

This module is PURE where possible: the variance math takes plain dicts so it
is trivially unit-testable and has no DB / framework imports.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


# --- count-session status state machine -------------------------------------
STATUS_OPEN = "open"        # accepting blind counted quantities
STATUS_LOCKED = "locked"    # manager revealed variance + soft-locked
STATUS_REOPENED = "reopened"  # manager re-opened (audited, reason required)

# Per-SKU variance verdicts
VERDICT_MATCHED = "matched"
VERDICT_OVER = "over"      # counted > expected (surplus found)
VERDICT_SHORT = "short"    # counted < expected (shrinkage)


def variance(counted: Optional[int], expected: Optional[int]) -> int:
    """Per-SKU variance = counted - expected (units). None treated as 0."""
    return int(counted or 0) - int(expected or 0)


def verdict(counted: Optional[int], expected: Optional[int], tolerance: int = 0) -> str:
    """Classify a per-SKU variance as matched / over / short within tolerance."""
    delta = variance(counted, expected)
    if abs(delta) <= max(0, int(tolerance)):
        return VERDICT_MATCHED
    return VERDICT_OVER if delta > 0 else VERDICT_SHORT


# ---------------------------------------------------------------------------
# DB-side engine. Self-contained blind-count session (own collection), reusing
# the #23 (eod_tally) blind-redact + atomic soft-lock PATTERN -- not a fork of
# the legacy stock_counts flow.
# ---------------------------------------------------------------------------
import hashlib
import uuid
from datetime import datetime, timezone

COLLECTION = "blind_stock_takes"
ADJUSTMENT_COLLECTION = "stock_adjustment_proposals"
TOLERANCE_KEY = "inventory.blind_count_tolerance_units"
REOPEN_ROLES_KEY = "inventory.blind_count_reopen_roles"
# Fields revealed only AFTER a manager lock -- redacted from a counter pre-lock.
_REVEAL_FIELDS = ("items_revealed", "summary", "expected_on_hand")


class BlindStockTakeError(Exception):
    def __init__(self, message, status=400, code="blind_stock_error"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _is_manager(user, reopen_roles=None):
    roles = {str(r).upper() for r in (user.get("roles", []) or [])}
    mgr = {"STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"}
    if reopen_roles:
        mgr = {str(r).upper() for r in reopen_roles} | {"ADMIN", "SUPERADMIN"}
    return bool(roles & mgr)


def build_summary(items, tolerance=0):
    """Per-SKU variance rollup. ``items`` carry counted_qty + expected. Pure."""
    matched = over = short = 0
    net_units = 0
    net_value_paise = 0
    rows = []
    for it in items or []:
        counted = it.get("counted_qty")
        expected = it.get("expected")
        v = variance(counted, expected)
        verd = verdict(counted, expected, tolerance)
        cost_paise = int(it.get("cost_paise") or 0)
        net_units += v
        net_value_paise += v * cost_paise
        if verd == VERDICT_MATCHED:
            matched += 1
        elif verd == VERDICT_OVER:
            over += 1
        else:
            short += 1
        rows.append({**it, "variance_units": v, "verdict": verd,
                     "variance_value_paise": v * cost_paise})
    return rows, {
        "total_skus": len(items or []),
        "matched": matched, "over": over, "short": short,
        "net_variance_units": net_units,
        "net_variance_value_paise": net_value_paise,
        "within_tolerance": over == 0 and short == 0,
    }


def redact_for_counter(session, user, reopen_roles=None):
    """Blind enforcement at the DATA layer: a non-manager NEVER sees the expected
    on-hand / variance / summary while the session is OPEN *or REOPENED* (no
    anchoring -- a reopened session is being RE-counted blind, so the prior
    lock's revealed figures must stay hidden from the counter). After a manager
    LOCK the reveal is visible to everyone (the count is done)."""
    if session is None:
        return None
    if session.get("status") not in (STATUS_OPEN, STATUS_REOPENED):
        return session
    if _is_manager(user, reopen_roles):
        return session
    out = {k: v for k, v in session.items() if k not in _REVEAL_FIELDS}
    out["items"] = [{kk: vv for kk, vv in (it or {}).items()
                     if kk not in ("expected", "variance_units", "verdict", "variance_value_paise")}
                    for it in (session.get("items") or [])]
    out["_blind_redacted"] = True
    return out


class BlindStockTakeEngine:
    """Persistence + atomic soft-lock for the blind count. Accessors injected."""

    def __init__(self, db=None):
        self.db = db

    def _coll(self):
        return None if self.db is None else self.db.get_collection(COLLECTION)

    def open_session(self, *, store_id, actor, scope=None):
        coll = self._coll()
        if coll is None:
            raise BlindStockTakeError("inventory store unavailable", status=503, code="no_db")
        if not store_id:
            raise BlindStockTakeError("store_id is required", status=400)
        sid = "BST-" + uuid.uuid4().hex[:10].upper()
        now = _now_iso()
        doc = {
            "_id": sid, "session_id": sid, "store_id": store_id,
            "scope": scope or {}, "status": STATUS_OPEN,
            "items": [],  # [{product_id, sku, counted_qty}] -- NO expected while open
            "opened_by": actor.get("user_id"), "opened_at": now, "updated_at": now,
        }
        coll.insert_one(dict(doc))
        return doc

    def submit_count(self, session_id, counts, *, store_id, actor):
        """Store blind counted quantities (no expected revealed)."""
        coll = self._coll()
        if coll is None:
            raise BlindStockTakeError("inventory store unavailable", status=503, code="no_db")
        clean = []
        for c in counts or []:
            pid = c.get("product_id") or c.get("sku")
            if not pid:
                continue
            clean.append({"product_id": pid, "sku": c.get("sku") or pid,
                          "counted_qty": int(c.get("counted_qty") or 0)})
        from pymongo import ReturnDocument
        # OPEN *or REOPENED*: a reopened session is being re-counted. Clear the
        # prior lock's stale reveal so a manager never sees old expected/variance
        # mixed with fresh blind counts; the next lock recomputes from `items`.
        updated = coll.find_one_and_update(
            {"_id": session_id, "store_id": store_id, "status": {"$in": [STATUS_OPEN, STATUS_REOPENED]}},
            {"$set": {"items": clean, "items_revealed": [], "summary": None,
                      "counted_by": actor.get("user_id"), "updated_at": _now_iso()}},
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise BlindStockTakeError("count session not open / not found", status=409, code="not_open")
        return updated

    def lock_and_reveal(self, session_id, *, store_id, actor, on_hand_resolver, cost_resolver=None, tolerance=0):
        """Atomic (OPEN|REOPENED) -> LOCKED. THE GUARD: a single guarded
        find_one_and_update keyed on status in (OPEN, REOPENED) -- two concurrent
        locks: exactly one wins (the first flips to LOCKED, the second no longer
        matches). On the winning lock, compute per-SKU variance (counted - system
        on_hand) + summary, and persist the reveal. A REOPENED session can be
        re-locked after a recount. on_hand_resolver(store_id, [pid]) -> {pid: qty}."""
        coll = self._coll()
        if coll is None:
            raise BlindStockTakeError("inventory store unavailable", status=503, code="no_db")
        sess = coll.find_one({"_id": session_id, "store_id": store_id})
        if sess is None:
            raise BlindStockTakeError("count session not found", status=404, code="not_found")
        items = sess.get("items") or []
        pids = [it.get("product_id") for it in items if it.get("product_id")]
        on_hand = on_hand_resolver(store_id, pids) or {}
        costs = (cost_resolver(pids) if cost_resolver else {}) or {}
        enriched = [{**it, "expected": int(on_hand.get(it.get("product_id"), 0)),
                     "cost_paise": int(costs.get(it.get("product_id"), 0))} for it in items]
        rows, summary = build_summary(enriched, tolerance)
        now = _now_iso()
        from pymongo import ReturnDocument
        updated = coll.find_one_and_update(
            {"_id": session_id, "store_id": store_id,
             "status": {"$in": [STATUS_OPEN, STATUS_REOPENED]}},  # GUARD
            {"$set": {"status": STATUS_LOCKED, "items": rows, "items_revealed": rows,
                      "summary": summary, "tolerance": int(tolerance),
                      "locked_by": actor.get("user_id"), "locked_at": now, "updated_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise BlindStockTakeError("count session not open (already locked)", status=409, code="not_open")
        return updated

    def reopen(self, session_id, *, store_id, actor, reason, reopen_roles=None):
        coll = self._coll()
        if coll is None:
            raise BlindStockTakeError("inventory store unavailable", status=503, code="no_db")
        if not reason or not str(reason).strip():
            raise BlindStockTakeError("a reason is required to reopen a locked count", status=400, code="reason_required")
        from pymongo import ReturnDocument
        updated = coll.find_one_and_update(
            {"_id": session_id, "store_id": store_id, "status": STATUS_LOCKED},
            {"$set": {"status": STATUS_REOPENED, "reopen_reason": reason,
                      "reopened_by": actor.get("user_id"), "reopened_at": _now_iso()}},
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise BlindStockTakeError("count session is not locked", status=409, code="not_locked")
        return updated

    def get(self, session_id, *, store_id=None):
        coll = self._coll()
        if coll is None:
            return None
        q = {"_id": session_id}
        if store_id:
            q["store_id"] = store_id
        return coll.find_one(q)

    def propose_adjustment(self, session_id, *, store_id, actor):
        """Enqueue a REVERSIBLE stock-adjustment PROPOSAL from a locked count's
        variances. Does NOT mutate on-hand -- a manager approves it elsewhere."""
        coll = self._coll()
        if coll is None:
            raise BlindStockTakeError("inventory store unavailable", status=503, code="no_db")
        sess = coll.find_one({"_id": session_id, "store_id": store_id})
        if sess is None:
            raise BlindStockTakeError("count session not found", status=404, code="not_found")
        # Must be LOCKED (revealed + final). A REOPENED session is mid-recount --
        # it must be re-locked first, so its variances are settled before a
        # proposal can be raised from them.
        if sess.get("status") != STATUS_LOCKED:
            raise BlindStockTakeError("count must be locked before proposing an adjustment", status=409, code="not_locked")
        lines = [{"product_id": it.get("product_id"), "delta_units": it.get("variance_units"),
                  "from_qty": it.get("expected"), "to_qty": it.get("counted_qty")}
                 for it in (sess.get("items_revealed") or sess.get("items") or [])
                 if int(it.get("variance_units") or 0) != 0]
        # Idempotent per (session, lock generation): a deterministic _id keyed on
        # session + locked_at means a double-submit returns the SAME proposal (no
        # duplicate adjustments from one locked count), while a genuine re-lock
        # after a reopen+recount mints a fresh proposal (new locked_at -> new id).
        gen = str(sess.get("locked_at") or "")
        pid = "ADJ-" + hashlib.sha1(f"{session_id}|{gen}".encode("utf-8")).hexdigest()[:12].upper()
        adj = self.db.get_collection(ADJUSTMENT_COLLECTION)
        existing = adj.find_one({"_id": pid})
        if existing is not None:
            return existing
        doc = {"_id": pid, "proposal_id": pid, "source": "blind_stock_take",
               "source_id": session_id, "source_lock_at": gen, "store_id": store_id,
               "status": "PROPOSED", "lines": lines,
               "created_by": actor.get("user_id"), "created_at": _now_iso()}
        from pymongo.errors import DuplicateKeyError
        try:
            adj.insert_one(dict(doc))
        except DuplicateKeyError:
            return adj.find_one({"_id": pid}) or doc
        return doc


def ensure_indexes(db):
    """Idempotent indexes. Fail-soft."""
    if db is None:
        return
    try:
        db.get_collection(COLLECTION).create_index([("store_id", 1), ("status", 1)])
    except Exception:  # noqa: BLE001
        return
