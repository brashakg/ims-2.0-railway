"""
IMS 2.0 - E1 Money-guard engine (Phase A facade)
=================================================
The single atomic guard for every balance-mutating operation. Phase A is a thin
FACADE over the THREE EXISTING balance collections -- it does NOT introduce a
unified `money_accounts` system-of-record, a migration, a new index, or any
cross-collection dual-write. See docs/roadmap/CORRECTIONS.md P0-1 (binding;
outranks the E1 packet): this Mongo is STANDALONE (no replica set) so there are
ZERO multi-document transactions; a single guarded find_one_and_update touches
exactly one document in one collection.

Account types (Phase A):
  GIFT_VOUCHER -> existing `vouchers` collection      (balance, key=code)
  LOYALTY      -> existing `loyalty_accounts`         (balance_points, key=customer_id)
  STORE_CREDIT -> existing `customers`                (store_credit, key=customer_id)
  PETTY_CASH / FAMILY_WALLET / CONSIGNMENT -> DEFERRED. No collection is created
    in Phase A (P0-1). These return GuardResult(ok=False, reason="unavailable")
    until a future packet authorizes their storage on a replica-set deployment.

Contract:
  * Never raises on a business failure -- returns a typed GuardResult with ok=False
    and a machine reason. Fail-soft when the db/collection is absent ("unavailable").
    Fail-CLOSED on a collection that cannot do the atomic op for a debit
    ("no_atomic") -- the balance is never touched.
  * Every successful credit/debit funnels its mutation through ONE guarded write
    and emits one append-only audit row via AuditRepository.create (NEVER
    append_audit_entry directly). Audit is fail-soft and never blocks the money move.
  * Idempotency: a credit/debit carrying an idempotency_key is de-duplicated via a
    money_ledger marker on the same document, so a retried call does not double-apply.
    The marker is written whenever an idempotency_key is supplied -- independent of
    record_ledger -- so the dedup guarantee can never silently no-op.

This module has NO FastAPI router and makes NO engine calls in Phase A (it is
tier-agnostic -- the caller enforces refund/discount tiers from E2). E6 OTP verbs
(request_pool_redeem / confirm_pool_redeem) are stubbed until E6 Wave 0b.
No emoji in this file (Windows cp1252).
"""
from dataclasses import dataclass
from datetime import datetime, date
from typing import Any, Dict, Optional
import uuid

try:  # pymongo is present in prod; tests use fakes that honour return_document
    from pymongo import ReturnDocument

    _AFTER = ReturnDocument.AFTER
except Exception:  # noqa: BLE001
    _AFTER = True

try:  # money/audit records are stamped India-time (IST), per project rule
    from api.utils.ist import now_ist_naive as _ist_now
except Exception:  # noqa: BLE001
    _ist_now = None


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class GuardResult:
    """Result of a guarded money move. Never an exception for a business failure.

    reason machine-codes: None (success) | "duplicate" | "insufficient" |
    "inactive" | "expired" | "not_found" | "no_atomic" | "unavailable" |
    "unknown_type" | "invalid_amount".
    """

    ok: bool
    balance: float = 0.0
    txn_id: Optional[str] = None
    reason: Optional[str] = None
    status: Optional[str] = None
    detail: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class AccountSpec:
    coll: str               # Mongo collection name
    key_field: str          # identity field on the document
    balance_field: str      # numeric balance field
    integer: bool = False   # points (int) vs INR (float)
    round_dp: Optional[int] = None   # round INR to N dp; None = pass float through
    floor: float = 0.0      # hard floor (debit guard: balance >= amount)
    status_field: Optional[str] = None   # spend requires this field == "ACTIVE"
    expiry_field: Optional[str] = None   # ISO-date field; spend blocked when past it
    mechanism: str = "find_modify"   # "find_modify" | "update_reread"
    greenfield: bool = False         # True => deferred type, returns "unavailable"


# Phase A: the three real types point at their EXISTING collections. The three
# new types are greenfield-deferred (no money_accounts collection built here).
ACCOUNT_TYPES: Dict[str, AccountSpec] = {
    "GIFT_VOUCHER": AccountSpec(
        coll="vouchers", key_field="code", balance_field="balance",
        integer=False, round_dp=None, status_field="status",
        expiry_field="expiry_date", mechanism="find_modify",
    ),
    "LOYALTY": AccountSpec(
        coll="loyalty_accounts", key_field="customer_id", balance_field="balance_points",
        integer=True, round_dp=None, status_field=None, mechanism="find_modify",
    ),
    "STORE_CREDIT": AccountSpec(
        coll="customers", key_field="customer_id", balance_field="store_credit",
        integer=False, round_dp=2, status_field=None, mechanism="update_reread",
    ),
    # Deferred per CORRECTIONS P0-1 -- no money_accounts collection/index in Phase A.
    "PETTY_CASH": AccountSpec(
        coll="money_accounts", key_field="account_key", balance_field="balance",
        integer=False, round_dp=2, status_field="status", greenfield=True,
    ),
    "FAMILY_WALLET": AccountSpec(
        coll="money_accounts", key_field="account_key", balance_field="balance",
        integer=False, round_dp=2, status_field="status", greenfield=True,
    ),
    "CONSIGNMENT": AccountSpec(
        coll="money_accounts", key_field="account_key", balance_field="balance",
        integer=False, round_dp=2, status_field="status", greenfield=True,
    ),
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """IST timestamp for money_ledger / new-verb records (forensic trail is India-
    time). Falls back to naive local only if the IST helper is unavailable."""
    if _ist_now is not None:
        try:
            return _ist_now().isoformat()
        except Exception:  # noqa: BLE001
            pass
    return datetime.now().isoformat()


def _today_date_iso() -> str:
    """Local calendar date as ISO (matches vouchers._today_iso). Spend-expiry uses
    a lexicographic ISO-date compare; a card expiring TODAY is still redeemable."""
    return date.today().isoformat()


def _is_expired_iso(expiry: Any) -> bool:
    if not expiry:
        return False
    return str(expiry)[:10] < _today_date_iso()


def _classify_debit_failure(coll, spec: "AccountSpec", account_key: str, amt: float) -> str:
    """Disambiguate why a guarded debit matched nothing, for types carrying status/
    expiry semantics (e.g. vouchers). Read-only; returns a machine reason. Types
    without status/expiry simply report 'insufficient' (mirrors the legacy None)."""
    if not (spec.status_field or spec.expiry_field):
        return "insufficient"
    try:
        doc = coll.find_one({spec.key_field: account_key})
    except Exception:  # noqa: BLE001
        doc = None
    if not doc:
        return "not_found"
    if spec.expiry_field and _is_expired_iso(doc.get(spec.expiry_field)):
        return "expired"
    if spec.status_field and doc.get(spec.status_field) != "ACTIVE":
        return "inactive"
    return "insufficient"


def _norm_amount(spec: AccountSpec, amount: Any):
    """Normalize the amount per the account type. Mirrors the existing paths:
    loyalty=int, store-credit=round(2), voucher=float pass-through."""
    if spec.integer:
        return int(amount)
    if spec.round_dp is not None:
        return round(float(amount), spec.round_dp)
    return float(amount)


def _resolve_collection(db_or_coll: Any, spec: AccountSpec):
    """Accept EITHER a bound collection (repo shims pass self.collection / _coll(db))
    OR a db handle (new callers pass the DatabaseConnection.db).

    Discriminate on the CLASS, NEVER the instance: a pymongo Collection defines
    __getattr__ that synthesizes a sub-collection for ANY name, so instance
    `hasattr(coll, "get_collection")` is deceptively True on a real Collection and
    would silently retarget every write to an empty sub-collection. get_collection
    is a real method on Database (class-level) and absent on Collection, so a
    class-level lookup is immune to __getattr__."""
    if db_or_coll is None:
        return None
    if getattr(type(db_or_coll), "get_collection", None) is not None:
        try:
            c = db_or_coll.get_collection(spec.coll)
            if c is not None:
                return c
        except Exception:  # noqa: BLE001
            pass
        return getattr(db_or_coll, spec.coll, None)
    return db_or_coll  # already a collection


def _audit(action: str, account_type: str, account_key: str, *, delta: float,
           balance_after: float, reason: str, ref: Optional[str], actor: Optional[str],
           store_id: Optional[str], idempotency_key: Optional[str]) -> None:
    """Append one append-only audit row. Fail-soft: an audit error NEVER raises
    and never undoes the money move. Always via AuditRepository.create (never
    append_audit_entry). Lazy import to avoid a repo<->dependencies import cycle."""
    try:
        from api.dependencies import get_audit_repository

        repo = get_audit_repository()
        if repo is None:
            return
        repo.create({
            "action": action,
            "entity_type": "money_account",
            "entity_id": account_key,
            "user_id": actor,
            "store_id": store_id,
            "severity": "INFO",
            "detail": {
                "type": account_type,
                "delta": delta,
                "balance_after": balance_after,
                "reason": reason,
                "ref": ref,
                "idempotency_key": idempotency_key,
            },
        })
    except Exception:  # noqa: BLE001
        return


def _find_existing_txn(coll, spec: AccountSpec, account_key: str, idempotency_key: str):
    """Return (txn_id, balance) of an already-applied money_ledger entry carrying
    this idempotency_key, or None. Used to make retries idempotent."""
    try:
        finder = getattr(coll, "find_one", None)
        if not callable(finder):
            return None
        doc = finder({spec.key_field: account_key})
        if not doc:
            return None
        for entry in (doc.get("money_ledger") or []):
            if entry.get("idempotency_key") == idempotency_key:
                bal = doc.get(spec.balance_field)
                return entry.get("txn_id"), (float(bal) if bal is not None else 0.0)
    except Exception:  # noqa: BLE001
        return None
    return None


# ---------------------------------------------------------------------------
# Public verbs
# ---------------------------------------------------------------------------


def debit(db_or_coll: Any, account_type: str, account_key: str, amount: Any, *,
          reason: str = "debit", actor: Optional[str] = None, ref: Optional[str] = None,
          store_id: Optional[str] = None, entity_id: Optional[str] = None,
          idempotency_key: Optional[str] = None, guard_extra: Optional[Dict] = None,
          inc_extra: Optional[Dict] = None, set_extra: Optional[Dict] = None,
          push_extra: Optional[Dict] = None, record_ledger: bool = True) -> GuardResult:
    """Guarded debit: the floor (balance >= amount) lives IN the filter, so two
    racing debits that together exceed the balance can never both win. Returns
    GuardResult; never raises on a business failure.

    The three Phase-A shims pass guard_extra/inc_extra/set_extra/push_extra to
    reconstruct their exact historical filter/update (and record_ledger=False so
    the existing collections' document shape is unchanged)."""
    spec = ACCOUNT_TYPES.get(account_type)
    if spec is None:
        return GuardResult(ok=False, reason="unknown_type")
    if spec.greenfield:
        return GuardResult(ok=False, reason="unavailable")
    coll = _resolve_collection(db_or_coll, spec)
    if coll is None:
        return GuardResult(ok=False, reason="unavailable")

    amt = _norm_amount(spec, amount)
    if amt <= 0:
        return GuardResult(ok=False, reason="invalid_amount")

    if idempotency_key:
        prior = _find_existing_txn(coll, spec, account_key, idempotency_key)
        if prior is not None:
            return GuardResult(ok=True, balance=prior[1], txn_id=prior[0], reason="duplicate")

    txn_id = str(uuid.uuid4())
    filt: Dict[str, Any] = {spec.key_field: account_key, spec.balance_field: {"$gte": amt}}
    # Engine owns the instrument's spend semantics: an ACTIVE-status requirement
    # and a not-expired guard live here, so EVERY caller (and the voucher shim)
    # gets them in one place. Types without these fields are unaffected.
    if spec.status_field:
        filt[spec.status_field] = "ACTIVE"
    if spec.expiry_field:
        filt["$or"] = [
            {spec.expiry_field: None},
            {spec.expiry_field: {"$gte": _today_date_iso()}},
        ]
    # Race-safe idempotency: a concurrent retry carrying the same key cannot match
    # (the marker is already present), so it cannot double-debit.
    if idempotency_key:
        filt["money_ledger.idempotency_key"] = {"$ne": idempotency_key}
    if guard_extra:
        filt.update(guard_extra)

    if spec.mechanism == "update_reread":
        if not callable(getattr(coll, "update_one", None)):
            return GuardResult(ok=False, reason="no_atomic")
        update: Dict[str, Any] = {"$inc": {spec.balance_field: -amt}}
        if set_extra:
            update.setdefault("$set", {}).update(set_extra)
        try:
            res = coll.update_one(filt, update)
        except Exception:  # noqa: BLE001
            return GuardResult(ok=False, reason="no_atomic")
        matched = getattr(res, "matched_count", None)
        if matched is None:
            matched = getattr(res, "modified_count", 0)
        if not matched:
            if idempotency_key:
                prior = _find_existing_txn(coll, spec, account_key, idempotency_key)
                if prior is not None:
                    return GuardResult(ok=True, balance=prior[1], txn_id=prior[0], reason="duplicate")
            return GuardResult(ok=False, reason="insufficient")
        post = None
        try:
            post = coll.find_one({spec.key_field: account_key})
        except Exception:  # noqa: BLE001
            post = None
        bal = float((post or {}).get(spec.balance_field) or 0.0)
        if record_ledger or idempotency_key:
            _append_ledger(coll, spec, account_key, txn_id, "DEBIT", -amt, bal,
                           reason, ref, actor, store_id, idempotency_key)
        _audit("money.debit", account_type, account_key, delta=-amt, balance_after=bal,
               reason=reason, ref=ref, actor=actor, store_id=store_id,
               idempotency_key=idempotency_key)
        return GuardResult(ok=True, balance=bal, txn_id=txn_id,
                           status=(post or {}).get(spec.status_field) if spec.status_field else None,
                           detail={"post_doc": post})

    # mechanism == "find_modify"
    if not callable(getattr(coll, "find_one_and_update", None)):
        return GuardResult(ok=False, reason="no_atomic")
    inc: Dict[str, Any] = {spec.balance_field: -amt}
    if inc_extra:
        inc.update(inc_extra)
    update = {"$inc": inc}
    sets = dict(set_extra or {})
    if "updated_at" not in sets:
        sets["updated_at"] = _now_iso()
    update["$set"] = sets
    if push_extra:
        update["$push"] = dict(push_extra)
    try:
        post = coll.find_one_and_update(filt, update, return_document=_AFTER)
    except Exception:  # noqa: BLE001
        # An unexpected driver error must NOT fall through to an unconditional
        # debit -- fail closed (mirrors loyalty try_debit).
        return GuardResult(ok=False, reason="no_atomic")
    if post is None:
        if idempotency_key:
            prior = _find_existing_txn(coll, spec, account_key, idempotency_key)
            if prior is not None:
                return GuardResult(ok=True, balance=prior[1], txn_id=prior[0], reason="duplicate")
        return GuardResult(ok=False, reason=_classify_debit_failure(coll, spec, account_key, amt))
    bal = float(post.get(spec.balance_field) or 0.0)
    if record_ledger or idempotency_key:
        _append_ledger(coll, spec, account_key, txn_id, "DEBIT", -amt, bal,
                       reason, ref, actor, store_id, idempotency_key)
    _audit("money.debit", account_type, account_key, delta=-amt, balance_after=bal,
           reason=reason, ref=ref, actor=actor, store_id=store_id,
           idempotency_key=idempotency_key)
    return GuardResult(ok=True, balance=bal, txn_id=txn_id,
                       status=post.get(spec.status_field) if spec.status_field else None,
                       detail={"post_doc": post})


def credit(db_or_coll: Any, account_type: str, account_key: str, amount: Any, *,
           reason: str = "credit", actor: Optional[str] = None, ref: Optional[str] = None,
           store_id: Optional[str] = None, entity_id: Optional[str] = None,
           idempotency_key: Optional[str] = None, set_extra: Optional[Dict] = None,
           push_extra: Optional[Dict] = None, record_ledger: bool = True) -> GuardResult:
    """Unconditional credit (no floor; a credit cannot overspend). Idempotent when
    an idempotency_key is supplied. Emits one audit row on success."""
    spec = ACCOUNT_TYPES.get(account_type)
    if spec is None:
        return GuardResult(ok=False, reason="unknown_type")
    if spec.greenfield:
        return GuardResult(ok=False, reason="unavailable")
    coll = _resolve_collection(db_or_coll, spec)
    if coll is None:
        return GuardResult(ok=False, reason="unavailable")

    amt = _norm_amount(spec, amount)
    if amt <= 0:
        return GuardResult(ok=False, reason="invalid_amount")

    if idempotency_key:
        prior = _find_existing_txn(coll, spec, account_key, idempotency_key)
        if prior is not None:
            return GuardResult(ok=True, balance=prior[1], txn_id=prior[0], reason="duplicate")

    txn_id = str(uuid.uuid4())
    # Guard against the dup-race too: only apply when the key is absent.
    filt: Dict[str, Any] = {spec.key_field: account_key}
    if idempotency_key:
        filt["money_ledger.idempotency_key"] = {"$ne": idempotency_key}

    inc = {spec.balance_field: amt}
    update: Dict[str, Any] = {"$inc": inc}
    sets = dict(set_extra or {})
    if "updated_at" not in sets:
        sets["updated_at"] = _now_iso()
    update["$set"] = sets

    if callable(getattr(coll, "find_one_and_update", None)):
        try:
            post = coll.find_one_and_update(filt, update, return_document=_AFTER)
        except Exception:  # noqa: BLE001
            post = None
        if post is None:
            # Either the doc is missing, or a concurrent same-key credit already
            # applied. Distinguish via a dedup read.
            if idempotency_key:
                prior = _find_existing_txn(coll, spec, account_key, idempotency_key)
                if prior is not None:
                    return GuardResult(ok=True, balance=prior[1], txn_id=prior[0], reason="duplicate")
            return GuardResult(ok=False, reason="not_found")
        bal = float(post.get(spec.balance_field) or 0.0)
        if record_ledger or idempotency_key:
            _append_ledger(coll, spec, account_key, txn_id, "CREDIT", amt, bal,
                           reason, ref, actor, store_id, idempotency_key)
        _audit("money.credit", account_type, account_key, delta=amt, balance_after=bal,
               reason=reason, ref=ref, actor=actor, store_id=store_id,
               idempotency_key=idempotency_key)
        return GuardResult(ok=True, balance=bal, txn_id=txn_id, detail={"post_doc": post})

    # update_one fallback (collection without find_one_and_update)
    if not callable(getattr(coll, "update_one", None)):
        return GuardResult(ok=False, reason="no_atomic")
    try:
        res = coll.update_one(filt, update)
    except Exception:  # noqa: BLE001
        return GuardResult(ok=False, reason="no_atomic")
    matched = getattr(res, "matched_count", None)
    if matched is None:
        matched = getattr(res, "modified_count", 0)
    if not matched:
        if idempotency_key:
            prior = _find_existing_txn(coll, spec, account_key, idempotency_key)
            if prior is not None:
                return GuardResult(ok=True, balance=prior[1], txn_id=prior[0], reason="duplicate")
        return GuardResult(ok=False, reason="not_found")
    post = None
    try:
        post = coll.find_one({spec.key_field: account_key})
    except Exception:  # noqa: BLE001
        post = None
    bal = float((post or {}).get(spec.balance_field) or 0.0)
    if record_ledger or idempotency_key:
        _append_ledger(coll, spec, account_key, txn_id, "CREDIT", amt, bal,
                       reason, ref, actor, store_id, idempotency_key)
    _audit("money.credit", account_type, account_key, delta=amt, balance_after=bal,
           reason=reason, ref=ref, actor=actor, store_id=store_id,
           idempotency_key=idempotency_key)
    return GuardResult(ok=True, balance=bal, txn_id=txn_id, detail={"post_doc": post})


def _append_ledger(coll, spec: AccountSpec, account_key: str, txn_id: str, ltype: str,
                   delta: float, balance_after: float, reason: str, ref: Optional[str],
                   actor: Optional[str], store_id: Optional[str],
                   idempotency_key: Optional[str]) -> None:
    """Append one signed entry to the document's money_ledger array (the unified
    in-document ledger for the new verbs). Fail-soft. Only the NEW credit/debit
    verbs record this; the Phase-A shims pass record_ledger=False so existing
    documents are not reshaped."""
    entry = {
        "txn_id": txn_id,
        "type": ltype,
        "delta": delta,
        "balance_after": balance_after,
        "reason": reason,
        "ref": ref,
        "actor": actor,
        "store_id": store_id,
        "idempotency_key": idempotency_key,
        "created_at": _now_iso(),
    }
    try:
        updater = getattr(coll, "update_one", None)
        if callable(updater):
            updater({spec.key_field: account_key}, {"$push": {"money_ledger": entry}})
    except Exception:  # noqa: BLE001
        return


def get_balance(db_or_coll: Any, account_type: str, account_key: str) -> Dict[str, Any]:
    """Read-only projection of {balance, status, holds}. No write. Greenfield types
    report status='unavailable' (no collection exists in Phase A)."""
    spec = ACCOUNT_TYPES.get(account_type)
    if spec is None:
        return {"balance": 0.0, "status": "unknown_type", "holds": []}
    if spec.greenfield:
        return {"balance": 0.0, "status": "unavailable", "holds": []}
    coll = _resolve_collection(db_or_coll, spec)
    if coll is None:
        return {"balance": 0.0, "status": "unavailable", "holds": []}
    try:
        doc = coll.find_one({spec.key_field: account_key}) or {}
    except Exception:  # noqa: BLE001
        doc = {}
    bal = doc.get(spec.balance_field)
    return {
        "balance": float(bal) if bal is not None else 0.0,
        "status": doc.get(spec.status_field) if spec.status_field else doc.get("status"),
        "holds": doc.get("holds") or [],
    }


# ---------------------------------------------------------------------------
# Family-wallet OTP verbs -- DEFERRED to E6 Wave 0b (ENGINES.md Conflicts S1).
# E1 does NOT store OTP state; these stub to "unavailable" until E6 is live.
# ---------------------------------------------------------------------------


def request_pool_redeem(*args, **kwargs) -> GuardResult:
    return GuardResult(ok=False, reason="unavailable")


def confirm_pool_redeem(*args, **kwargs) -> GuardResult:
    return GuardResult(ok=False, reason="unavailable")
