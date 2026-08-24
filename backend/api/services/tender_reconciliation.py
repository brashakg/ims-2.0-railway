"""
IMS 2.0 - E5 Tender reconciliation (DB-facing reader + day-close)
=================================================================
Reads the EXISTING ``order.payments[]`` (POS capture is UNCHANGED) and derives:

  * the EFFECTIVE tender->ledger map for a scope (E2 layering store > entity >
    global, via the settings doc-per-scope pattern + E2's entity resolver),
  * an additive ledger STAMP on each payment row (never rewrites a capture
    field),
  * a by-mode reconciliation over an IST-day window,
  * a daily ``payment_reconciliations`` snapshot doc (system-of-record for the
    cash-variance), locked ATOMICALLY (one guarded ``find_one_and_update`` on
    ``status:OPEN``) and immutable thereafter, hash-chained-audited on lock.

Standalone Mongo: every write here touches ONE document in ONE collection. The
lock is a single guarded ``find_one_and_update``; there is no cross-collection
"atomic" write. Audit goes through ``AuditRepository.create`` (the hash-chained
facade) -- NEVER ``append_audit_entry`` directly.

No emoji (Windows cp1252).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from api.services.tender_routing import (
    IMS_DEFAULT_LEDGERS,
    canonicalize_tender,
    resolve_ledger,
    split_payments_by_mode,
)

_MAP_COLLECTION = "tender_ledger_map"
_SNAPSHOT_COLLECTION = "payment_reconciliations"


# ---------------------------------------------------------------------------
# Scope addressing (uppercase scope keys per packet: GLOBAL / ENTITY:<id> /
# STORE:<id>) -- resolved with E2's entity resolver so there is ONE store->entity
# lookup convention across engines (ENGINES.md note 7).
# ---------------------------------------------------------------------------


def _scope_chain(store_id: Optional[str], entity_id: Optional[str]) -> List[str]:
    """Scope doc ``_id``s to merge, LEAST specific first (so a later, more
    specific override wins on dict update). Falls through to GLOBAL when an
    entity_id cannot be resolved (dirty data) -- never raises."""
    chain: List[str] = ["GLOBAL"]
    eid = entity_id
    if store_id and not eid:
        try:
            from api.services.policy_engine import _resolve_entity_id

            eid = _resolve_entity_id(store_id)
        except Exception:  # noqa: BLE001
            eid = None
    if eid:
        chain.append(f"ENTITY:{eid}")
    if store_id:
        chain.append(f"STORE:{store_id}")
    return chain


def _scope_doc_ledgers(coll, addr: str) -> Dict[str, str]:
    """The sparse ``ledgers`` override map for one scope address. Missing doc /
    read error -> {}."""
    if coll is None:
        return {}
    try:
        doc = coll.find_one({"_id": addr}) or {}
    except Exception:  # noqa: BLE001
        return {}
    led = doc.get("ledgers") or {}
    # Only keep string overrides keyed by an UPPER tender name.
    return {str(k).upper(): str(v) for k, v in led.items() if v}


def get_effective_tender_map(
    db,
    store_id: Optional[str] = None,
    entity_id: Optional[str] = None,
) -> Dict[str, str]:
    """The resolved tender->ledger map for a scope.

    Starts from the code defaults (``IMS_DEFAULT_LEDGERS``) and layers sparse
    overrides GLOBAL -> ENTITY -> STORE (most specific wins). DB unavailable ->
    pure defaults (the resolver still routes correctly on a fresh DB)."""
    effective: Dict[str, str] = dict(IMS_DEFAULT_LEDGERS)
    coll = _settings_coll(db)
    for addr in _scope_chain(store_id, entity_id):
        for tender, ledger in _scope_doc_ledgers(coll, addr).items():
            effective[tender] = ledger
    return effective


def get_effective_tender_map_with_sources(
    db,
    store_id: Optional[str] = None,
    entity_id: Optional[str] = None,
) -> Dict[str, Dict[str, str]]:
    """Like ``get_effective_tender_map`` but each row reports its inheritance
    source: ``{tender: {ledger, source}}`` where source is
    ``store|entity|global|default`` (acceptance test #7 surfaces the source)."""
    rows: Dict[str, Dict[str, str]] = {
        t: {"ledger": l, "source": "default"} for t, l in IMS_DEFAULT_LEDGERS.items()
    }
    coll = _settings_coll(db)
    for addr in _scope_chain(store_id, entity_id):
        level = addr.split(":", 1)[0].lower()  # GLOBAL -> global, ENTITY:x -> entity
        for tender, ledger in _scope_doc_ledgers(coll, addr).items():
            rows.setdefault(tender, {})
            rows[tender] = {"ledger": ledger, "source": level}
    return rows


def _settings_coll(db):
    """The tender_ledger_map collection (None when DB absent)."""
    if db is None:
        return None
    try:
        return db.get_collection(_MAP_COLLECTION)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Map writes (settings doc-per-scope; single-document upsert)
# ---------------------------------------------------------------------------


def set_tender_ledger(
    db,
    *,
    scope: str,
    tender: str,
    ledger: str,
    actor: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Upsert ONE tender->ledger override at a scope (``GLOBAL`` / ``ENTITY:<id>``
    / ``STORE:<id>``). One single-document ``$set`` + one hash-chained audit row
    (two sequential single-doc writes; no cross-collection transaction).

    Returns ``{"ok": True, "scope", "tender", "ledger"}`` or
    ``{"ok": False, "error"}`` (the router maps error -> HTTP)."""
    coll = _settings_coll(db)
    if coll is None:
        return {"ok": False, "error": "no_db"}
    canon = canonicalize_tender(tender)
    if canon == "UNKNOWN" and str(tender).strip().upper() != "UNKNOWN":
        return {"ok": False, "error": "unknown_tender"}
    if not str(ledger or "").strip():
        return {"ok": False, "error": "empty_ledger"}

    now = datetime.utcnow()
    before = None
    try:
        existing = coll.find_one({"_id": scope}) or {}
        before = (existing.get("ledgers") or {}).get(canon)
    except Exception:  # noqa: BLE001
        before = None
    try:
        coll.update_one(
            {"_id": scope},
            {
                "$set": {
                    f"ledgers.{canon}": str(ledger).strip(),
                    "scope": scope,
                    "updated_at": now,
                    "updated_by": (actor or {}).get("user_id"),
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"write_failed:{exc}"}

    _audit(
        "tender_ledger_map_update",
        entity_id=f"{scope}:{canon}",
        actor=actor,
        before={"ledger": before},
        after={"ledger": str(ledger).strip()},
        detail={"scope": scope, "tender": canon},
    )
    return {"ok": True, "scope": scope, "tender": canon, "ledger": str(ledger).strip()}


# ---------------------------------------------------------------------------
# Stamp (additive denormalization onto an order's payment rows)
# ---------------------------------------------------------------------------


def stamp_payment_ledgers(db, order_doc: Dict[str, Any], tender_map: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """ADD ``canonical_tender, ledger, ledger_stamped_at`` to each
    ``order.payments[]`` row (never rewrites the capture fields ``method`` /
    ``amount`` / ``reference`` / ``received_by`` / ``received_at`` /
    ``idempotency_key``). Returns the count stamped + the order_id.

    Idempotent + re-runnable: re-stamping just refreshes the derived fields. A
    single ``update_one`` on the one orders document (no cross-collection write).
    When ``tender_map`` is omitted it is resolved for the order's store."""
    payments = list(order_doc.get("payments") or [])
    if not payments:
        return {"ok": True, "order_id": order_doc.get("order_id"), "stamped": 0}
    if tender_map is None:
        tender_map = get_effective_tender_map(db, store_id=order_doc.get("store_id"))

    now = datetime.utcnow()
    stamped: List[Dict[str, Any]] = []
    for p in payments:
        row = dict(p)
        canon = canonicalize_tender(row.get("method"), row.get("mode"))
        row["canonical_tender"] = canon
        row["ledger"] = resolve_ledger(canon, tender_map)
        row["ledger_stamped_at"] = now
        stamped.append(row)

    if db is not None:
        try:
            coll = db.get_collection("orders")
            coll.update_one(
                {"order_id": order_doc.get("order_id")},
                {"$set": {"payments": stamped}},
            )
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "order_id": order_doc.get("order_id"), "stamped": len(stamped)}


# ---------------------------------------------------------------------------
# By-mode reconciliation over an IST-day window
# ---------------------------------------------------------------------------


def reconcile_window(
    db,
    store_id: str,
    window_start: Any,
    window_end: Any = None,
    *,
    tender_map: Optional[Dict[str, str]] = None,
    include_returns: bool = False,
) -> Dict[str, Any]:
    """Aggregate ``order.payments[]`` for ``store_id`` in [start, end] by
    canonical tender. ``window_*`` may be ISO strings or datetimes; both a
    Date-typed and a string ``created_at`` are matched (BUG-031 dual-window, same
    as finance._cash_sales_for_window) so a legacy string created_at is not
    missed.

    Returns ``{store_id, window_start, window_end, by_mode: {<tender>:
    {collected, refunded, net, count, ledger}}, total_net}``. Each by-mode row
    carries its resolved ledger so the snapshot is self-describing. DB absent ->
    an empty by-mode envelope (never raises).

    MONEY-LEAK FIX: a money refund is recorded ONLY as a ``returns`` doc
    (returns.py create_return) -- POS never writes a negative payments[] row --
    so the by-mode nets (and the F23 blind Z-Read expected drawer figure that
    eod_tally.compute_expected derives from CASH.net) never saw refunds: a
    false SHORT every time cash was refunded. When ``include_returns=True`` the
    COMPLETED, non-historical returns in the window are netted into the by-mode
    rows, windowed on the RETURN's OWN created_at (= the refund date), off the
    EXPLICIT per-tender breakdown the cashier recorded (returns.refund_tenders /
    returns.collect_method) -- NEVER the inferred refund_method. CASH refunds
    reduce the expected drawer; UPI/CARD refunds reduce those tenders' nets. A
    COLLECT-direction EXCHANGE (difference collected at the till) is a symmetric
    cash-IN on collect_method. ``count`` stays the number of payments[] rows.

    CONTRACT GUARD (money-panel redesign): ``include_returns`` DEFAULTS TO FALSE
    so every existing consumer (bank_reconciliation.build_pos_digital_expected,
    the payment_reconciliations snapshot, the cash-register close by-mode
    display) keeps the payments-only contract it was written against -- netting
    refunds into it silently emitted NEGATIVE expected gateway settlements with
    negative MDR. Only eod_tally.compute_expected (the drawer figure) opts in."""
    if tender_map is None:
        tender_map = get_effective_tender_map(db, store_id=store_id)

    all_payments: List[Dict[str, Any]] = []
    payments_read_ok = True
    if db is not None:
        try:
            match = _window_match(store_id, window_start, window_end)
            cursor = db.get_collection("orders").find(match, {"_id": 0, "payments": 1})
            for o in cursor:
                all_payments.extend(o.get("payments") or [])
        except Exception:  # noqa: BLE001
            all_payments = []
            payments_read_ok = False

    by_mode = split_payments_by_mode(all_payments)
    # Skip the returns netting when the payments read failed, so the two Day-End
    # readers never disagree (a refunds-only envelope must not be frozen into the
    # hash-chained snapshot on a transient Mongo blip).
    if include_returns and payments_read_ok:
        _net_returns_into_by_mode(db, by_mode, store_id, window_start, window_end)
    total_net = 0.0
    for tender, agg in by_mode.items():
        agg["ledger"] = resolve_ledger(tender, tender_map)
        total_net += agg.get("net", 0.0)
    return {
        "store_id": store_id,
        "window_start": _iso(window_start),
        "window_end": _iso(window_end),
        "by_mode": by_mode,
        "total_net": round(total_net, 2),
    }


def _net_returns_into_by_mode(
    db,
    by_mode: Dict[str, Dict[str, Any]],
    store_id: str,
    window_start: Any,
    window_end: Any,
) -> None:
    """Net COMPLETED, non-historical ``returns`` docs into the by-mode rows
    (in place). See reconcile_window's docstring for the why.

    TENDER SOURCE (money-panel redesign): net off the EXPLICIT breakdown the
    cashier recorded, NEVER the inferred refund_method. A RETURN subtracts each
    ``refund_tenders`` leg from its own canonical tender (refunded up, net
    down). A COLLECT-direction EXCHANGE adds ``collect_amount`` to
    ``collect_method`` (collected up, net up). A return that carries NO
    refund_tenders / collect_method is UNKNOWN and netted NOWHERE (never
    fabricated). CREDIT_NOTE / EXCHANGE-refund issue store credit -- no tender
    money moves.

    ``created_at`` on returns docs is an ISO STRING (datetime.now().isoformat());
    ``_window_match`` already emits the dual string/datetime $or window (BUG-031
    pattern). ``historical: True`` docs (Shopify-era imports) settled outside
    the till and are excluded. NON-DESTRUCTIVE fail-soft: accumulate into a
    local delta map and merge into ``by_mode`` only after the cursor drains, so
    a mid-cursor error leaves the payments[] aggregation untouched."""
    if db is None:
        return
    try:
        deltas: Dict[str, Dict[str, float]] = {}

        def _bump(tender: str, *, refunded: float = 0.0, collected: float = 0.0):
            d = deltas.setdefault(tender, {"collected": 0.0, "refunded": 0.0})
            d["refunded"] += refunded
            d["collected"] += collected

        ret_match = _window_match(store_id, window_start, window_end)
        ret_match["status"] = "COMPLETED"
        ret_match["historical"] = {"$ne": True}
        cursor = db.get_collection("returns").find(
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
        for r in cursor:
            # Belt-and-braces re-checks (a stub collection may ignore the
            # filter; dirty data must never move a money figure).
            if str(r.get("status") or "").upper() != "COMPLETED":
                continue
            if r.get("historical") is True:
                continue
            rtype = str(r.get("return_type") or "").upper()
            if rtype == "RETURN":
                for t in r.get("refund_tenders") or []:
                    tender = canonicalize_tender((t or {}).get("method"))
                    if tender == "UNKNOWN":
                        continue
                    try:
                        amt = float((t or {}).get("amount") or 0)
                    except (TypeError, ValueError):
                        amt = 0.0
                    if amt > 0:
                        _bump(tender, refunded=amt)
            elif rtype == "EXCHANGE":
                direction = str(
                    (r.get("settlement") or {}).get("direction") or ""
                ).upper()
                if direction != "COLLECT":
                    continue
                tender = canonicalize_tender(r.get("collect_method"))
                if tender == "UNKNOWN":
                    continue
                try:
                    coll_amt = float(r.get("collect_amount") or 0)
                except (TypeError, ValueError):
                    coll_amt = 0.0
                if coll_amt > 0:
                    _bump(tender, collected=coll_amt)

        for tender, d in deltas.items():
            row = by_mode.setdefault(
                tender,
                {"collected": 0.0, "refunded": 0.0, "net": 0.0, "count": 0},
            )
            row["collected"] = round(row["collected"] + d["collected"], 2)
            row["refunded"] = round(row["refunded"] + d["refunded"], 2)
            row["net"] = round(row["net"] + d["collected"] - d["refunded"], 2)
    except Exception:  # noqa: BLE001
        return


def _window_match(store_id: str, start: Any, end: Any) -> Dict[str, Any]:
    """Build the orders match for a window, tolerating Date- and string-typed
    created_at (Mongo type-bracketing -- an ISO-string bound never matches a
    BSON Date and vice versa)."""
    start_dt = _to_dt(start)
    start_str = _iso(start)
    date_win: Dict[str, Any] = {}
    str_win: Dict[str, Any] = {}
    if start_dt is not None:
        date_win["$gte"] = start_dt
    if start_str:
        str_win["$gte"] = start_str
    if end is not None:
        end_dt = _to_dt(end)
        end_str = _iso(end)
        if end_dt is not None:
            date_win["$lte"] = end_dt
        if end_str:
            str_win["$lte"] = end_str
    or_clauses: List[Dict[str, Any]] = []
    if date_win:
        or_clauses.append({"created_at": date_win})
    if str_win:
        or_clauses.append({"created_at": str_win})
    match: Dict[str, Any] = {"store_id": store_id}
    if or_clauses:
        match["$or"] = or_clauses
    return match


# Public alias. The denominated per-face drawer ledger (eod_tally) must scan
# EXACTLY the window this module scans, or the two Day-End readers would
# disagree about which sales are in the day. One window builder, two readers.
window_match = _window_match


# ---------------------------------------------------------------------------
# Daily snapshot + atomic lock
# ---------------------------------------------------------------------------


def build_reconciliation_snapshot(
    db,
    store_id: str,
    window_start: Any,
    window_end: Any = None,
    *,
    actor: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create-or-fetch the OPEN ``payment_reconciliations`` doc for a store/IST
    day. One doc per (store_id, window_start). If a LOCKED snapshot already
    exists for the day it is returned as-is (immutable); an OPEN one is rebuilt
    with the current by-mode figures. Single-document upsert.

    Returns the snapshot doc (``_id`` retained as ``snapshot_id``)."""
    recon = reconcile_window(db, store_id, window_start, window_end)
    ws = _iso(window_start)
    we = _iso(window_end)

    coll = _snapshot_coll(db)
    if coll is None:
        # Fail-soft: a derivable, un-persisted snapshot (still correct to read).
        return {
            "snapshot_id": None,
            "store_id": store_id,
            "window_start": ws,
            "window_end": we,
            "by_mode": recon["by_mode"],
            "total_net": recon["total_net"],
            "status": "OPEN",
            "persisted": False,
        }

    existing = None
    try:
        existing = coll.find_one({"store_id": store_id, "window_start": ws})
    except Exception:  # noqa: BLE001
        existing = None
    if existing is not None and str(existing.get("status")) == "LOCKED":
        existing["snapshot_id"] = existing.get("_id")
        return existing

    now = datetime.utcnow()
    # DETERMINISTIC OPEN id (P3 fix): concurrent first-builds for the same
    # store/day converge on ONE doc (both upsert _id RECON-<store>-<day>)
    # instead of each minting a distinct uuid-suffixed OPEN doc -- locking the
    # second of those later violated the LOCKED partial-unique index and
    # surfaced as a 500. Pre-existing prod docs with the old uuid-suffixed _id
    # keep working: the lookup above is by (store_id, window_start), and an
    # existing doc's _id is retained verbatim.
    snapshot_id = (existing or {}).get("_id") or f"RECON-{store_id}-{(ws or '')[:10]}"
    doc = {
        "_id": snapshot_id,
        "snapshot_id": snapshot_id,
        "store_id": store_id,
        "window_start": ws,
        "window_end": we,
        "by_mode": recon["by_mode"],
        "total_net": recon["total_net"],
        "status": "OPEN",
        "updated_at": now,
        "updated_by": (actor or {}).get("user_id"),
    }
    # The status guard keeps a stale rebuild (raced by a concurrent lock) from
    # overwriting a just-LOCKED snapshot -- LOCKED stays immutable even inside
    # the race window. Still a single-document upsert.
    upsert_filter = {"_id": snapshot_id, "status": {"$ne": "LOCKED"}}
    update = {"$set": doc, "$setOnInsert": {"created_at": now}}
    try:
        coll.update_one(upsert_filter, update, upsert=True)
    except Exception:  # noqa: BLE001
        # Two concurrent FIRST builds race the same deterministic _id: the
        # loser's upsert-insert hits DuplicateKeyError. Retry once -- the doc
        # now exists, so the retry is a plain update (still ONE doc). A retry
        # that fails again (e.g. the day got LOCKED mid-race) falls back to
        # the un-persisted envelope; the stored snapshot is untouched.
        try:
            coll.update_one(upsert_filter, update, upsert=True)
        except Exception:  # noqa: BLE001
            doc["persisted"] = False
            return doc
    doc["persisted"] = True
    return doc


def get_snapshot(db, snapshot_id: str) -> Optional[Dict[str, Any]]:
    """Load a reconciliation snapshot by id (or None). Used by the lock route to
    store-scope the actor BEFORE the irreversible lock (cross-store IDOR guard)."""
    coll = _snapshot_coll(db)
    if coll is None:
        return None
    try:
        return coll.find_one({"_id": snapshot_id})
    except Exception:  # noqa: BLE001
        return None


def lock_reconciliation(db, snapshot_id: str, actor: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Lock a snapshot ATOMICALLY: a single guarded ``find_one_and_update`` on
    ``status:OPEN`` flips it to ``LOCKED`` in the same op. Two concurrent locks
    -> exactly one wins (the loser sees the doc no longer OPEN). A LOCKED
    snapshot is immutable thereafter -- this is the ONLY mutation path and it
    cannot run twice. Hash-chained audit row via ``AuditRepository.create``.

    Returns ``{"ok": True, "snapshot"}`` or ``{"ok": False, "error", "http"}``."""
    coll = _snapshot_coll(db)
    if coll is None:
        return {"ok": False, "error": "no_db", "http": 503}

    from pymongo import ReturnDocument
    from pymongo.errors import DuplicateKeyError

    now = datetime.utcnow()
    locked = None
    try:
        locked = coll.find_one_and_update(
            {"_id": snapshot_id, "status": "OPEN"},
            {
                "$set": {
                    "status": "LOCKED",
                    "locked_at": now,
                    "locked_by": (actor or {}).get("user_id"),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        # The LOCKED partial-unique index (at most ONE LOCKED snapshot per
        # store/day) rejected the flip: a SIBLING doc for the same store/day
        # is already LOCKED (legacy duplicate OPEN docs from the uuid-id era).
        # That is the business condition "day already locked" -- surface it as
        # a clean 409, not a 500 lock_failed (P3 fix).
        return {"ok": False, "error": "already_locked", "http": 409}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"lock_failed:{exc}", "http": 500}

    if locked is None:
        # Either the snapshot does not exist or it is already LOCKED.
        present = None
        try:
            present = coll.find_one({"_id": snapshot_id})
        except Exception:  # noqa: BLE001
            present = None
        if present is None:
            return {"ok": False, "error": "not_found", "http": 404}
        return {"ok": False, "error": "already_locked", "http": 409}

    _audit(
        "payment_reconciliation_lock",
        entity_id=snapshot_id,
        actor=actor,
        before={"status": "OPEN"},
        after={"status": "LOCKED", "total_net": locked.get("total_net")},
        detail={
            "store_id": locked.get("store_id"),
            "window_start": locked.get("window_start"),
        },
        store_id=locked.get("store_id"),
        severity="WARNING",
    )
    locked["snapshot_id"] = locked.get("_id")
    return {"ok": True, "snapshot": locked}


def _snapshot_coll(db):
    if db is None:
        return None
    try:
        return db.get_collection(_SNAPSHOT_COLLECTION)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Index setup (called from main.py startup; greenfield collection so the
# partial-unique index is safe -- one LOCKED snapshot per store/day).
# ---------------------------------------------------------------------------


def ensure_reconciliation_indexes(db) -> None:
    """Idempotent. A partial-unique index so AT MOST ONE LOCKED snapshot can
    exist per (store_id, window_start) -- OPEN duplicates are tolerated (rebuild
    churn) but a day can be locked once. Fail-soft."""
    if db is None:
        return
    try:
        coll = db.get_collection(_SNAPSHOT_COLLECTION)
        coll.create_index(
            [("store_id", 1), ("window_start", 1)],
            unique=True,
            partialFilterExpression={"status": "LOCKED"},
            name="uniq_locked_recon_per_store_day",
        )
        coll.create_index([("store_id", 1), ("status", 1)], name="recon_store_status")
    except Exception:  # noqa: BLE001
        pass
    try:
        # The drawer readers now scan `returns` on every close/preview poll and
        # every Z-Read, and that collection also holds the bulk Shopify
        # historical import. Without this the scan is a COLLSCAN at 21:00 while
        # the store is trying to shut. Idempotent + fail-soft.
        db.get_collection("returns").create_index(
            [("store_id", 1), ("status", 1), ("created_at", -1)],
            name="returns_store_status_created",
        )
    except Exception:  # noqa: BLE001
        return


# ---------------------------------------------------------------------------
# Audit + small helpers
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
                "entity_type": "payment_reconciliation",
                "entity_id": entity_id,
                "store_id": store_id or (actor or {}).get("active_store_id"),
                "user_id": (actor or {}).get("user_id"),
                "user_name": (actor or {}).get("full_name") or (actor or {}).get("username"),
                "severity": severity,
                "source": "tender_reconciliation",
                "before_state": before,
                "after_state": after,
                "detail": detail or {},
            }
        )
    except Exception:  # noqa: BLE001
        return


def _to_dt(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v)[:19])
    except (ValueError, TypeError):
        try:
            return datetime.fromisoformat(str(v)[:10])
        except (ValueError, TypeError):
            return None


def _iso(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)
