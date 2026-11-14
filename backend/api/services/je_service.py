"""
IMS 2.0 - F17/#25 Maker-Checker Journal Entries service
========================================================
A manual double-entry journal-voucher engine for the accountant: draft a
balanced debit=credit voucher, route it to a DIFFERENT-user checker for a
PIN-verified E4 approval, then post it to the ledger so it flows into the P&L
read and the nightly Tally journal-voucher export.

The maker-checker gate is NOT reimplemented here. It is the shared E4 engine
(``api.services.approvals.ApprovalEngine``): ``action_type="journal_entry"`` is
already in E4's ``ACTION_TYPES`` and ``MAKER_CHECKER_ACTIONS``, so E4 itself
hard-enforces approver != maker. This module only:

  * validates a balanced voucher against a seeded chart of accounts,
  * mints an FY-scoped consecutive JE number (atomic ``counters.$inc``),
  * drives the JE state machine DRAFT -> SUBMITTED -> APPROVED -> POSTED
    (+ REJECTED / REVERSED) where each hop is ONE single-document write,
  * gates the POST behind ``consume_approval`` so a JE posts EXACTLY ONCE,
  * writes a hash-chained ``audit_logs`` row per transition via
    ``AuditRepository.create``.

Atomicity (CORRECTIONS P0-1): standalone Mongo -> NO multi-document
transactions. The E4 approval doc and the JE doc are each updated by their own
single-document write. Posting is guarded by ``status == APPROVED`` in the
update FILTER so two racing posts cannot both post (idempotent post).

Money is paisa-exact integers (rupees * 100) for every debit/credit. Status is
an explicit string enum, never a colour flag (DECISIONS colour-flag-migration).
IST tz + FY (Apr-Mar) via api.utils.ist. NO emoji (Windows cp1252); ASCII log
tag [JE]. Fail-soft: ``db=None`` => empty reads / no-op writes, never raises.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..utils.ist import now_ist, fy_start_year_ist
from .approvals import JOURNAL_CHECKER_ROLES

logger = logging.getLogger(__name__)

# JE lifecycle statuses -- explicit string enum (never a colour flag).
STATUS_DRAFT = "DRAFT"
STATUS_SUBMITTED = "SUBMITTED"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_POSTED = "POSTED"
STATUS_REVERSED = "REVERSED"

# The E4 action_type for the maker-checker approval. It is already registered in
# approvals.ACTION_TYPES + MAKER_CHECKER_ACTIONS; we never redefine those.
JE_ACTION_TYPE = "journal_entry"

# The roles that may approve / reject / post / reverse a journal (the checker
# side). One definition (services/approvals): the journal routes gate on it
# and the approval engine routes a journal's request to it, so the approvals
# inbox holds a journal's request to the same roles the journal screen does.
JE_CHECKER_ROLES = JOURNAL_CHECKER_ROLES

FLAG_ENV = "ENABLE_MANUAL_JE"


def is_je_enabled() -> bool:
    """Feature flag (off by default). JE WRITE endpoints 503 when unset; reads
    are always allowed (read-only risk is nil)."""
    return os.getenv(FLAG_ENV) == "1"


def _now() -> datetime:
    # Stored as a naive-UTC datetime to match the rest of the order/expense
    # corpus (BaseRepository._add_timestamps uses datetime.now()).
    return now_ist().replace(tzinfo=None)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if hasattr(dt, "isoformat"):
        return dt.isoformat()
    return str(dt)


# ============================================================================
# Chart of accounts (seeded minimal set; owner expands via SUPERADMIN endpoint)
# ============================================================================

# DECISIONS sec 6: the accountant's manual expense heads. allow_manual_je=False
# protects system-managed balances (GST output/input, stock) from manual edits.
DEFAULT_CHART_OF_ACCOUNTS: List[Dict[str, Any]] = [
    {"account_code": "5001", "account_name": "Depreciation - Furniture", "account_type": "EXPENSE", "allow_manual_je": True},
    {"account_code": "5002", "account_name": "Depreciation - Equipment", "account_type": "EXPENSE", "allow_manual_je": True},
    {"account_code": "5003", "account_name": "Rent", "account_type": "EXPENSE", "allow_manual_je": True},
    {"account_code": "5004", "account_name": "Bank Charges", "account_type": "EXPENSE", "allow_manual_je": True},
    {"account_code": "5005", "account_name": "Prior-Period Adjustments", "account_type": "EXPENSE", "allow_manual_je": True},
    {"account_code": "5006", "account_name": "Miscellaneous Expenses", "account_type": "EXPENSE", "allow_manual_je": True},
    {"account_code": "2001", "account_name": "Accumulated Depreciation", "account_type": "LIABILITY", "allow_manual_je": True},
    {"account_code": "4001", "account_name": "Miscellaneous Income", "account_type": "REVENUE", "allow_manual_je": True},
    {"account_code": "2101", "account_name": "GST Output Tax", "account_type": "LIABILITY", "allow_manual_je": False},
    {"account_code": "2102", "account_name": "GST Input Tax", "account_type": "ASSET", "allow_manual_je": False},
    {"account_code": "1001", "account_name": "Stock - Frames", "account_type": "ASSET", "allow_manual_je": False},
    {"account_code": "1002", "account_name": "Stock - Lenses", "account_type": "ASSET", "allow_manual_je": False},
]

_COA_BY_CODE = {a["account_code"]: a for a in DEFAULT_CHART_OF_ACCOUNTS}


def _coa_coll(db):
    if db is None:
        return None
    try:
        return db.get_collection("chart_of_accounts")
    except Exception:  # noqa: BLE001
        try:
            return db["chart_of_accounts"]
        except Exception:  # noqa: BLE001
            return None


def seed_chart_of_accounts(db) -> int:
    """Idempotent upsert of the minimal COA. Returns the number of accounts
    ensured. Fail-soft -> 0 on no DB."""
    coll = _coa_coll(db)
    if coll is None:
        return 0
    n = 0
    now = _now()
    for acct in DEFAULT_CHART_OF_ACCOUNTS:
        try:
            coll.update_one(
                {"account_code": acct["account_code"]},
                {
                    "$set": {**acct, "is_active": True},
                    "$setOnInsert": {"created_at": now},
                },
                upsert=True,
            )
            n += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("[JE] seed_chart_of_accounts %s failed: %s", acct["account_code"], e)
    return n


def list_accounts(db, *, manual_only: bool = False) -> List[Dict[str, Any]]:
    """Active chart of accounts. ``manual_only`` filters to allow_manual_je=True
    (what the JE line-account picker offers). Seeds the COA on first read so a
    fresh DB is never empty."""
    coll = _coa_coll(db)
    if coll is None:
        # Fail-soft static fallback so a no-DB caller still sees the catalogue.
        rows = [dict(a, is_active=True) for a in DEFAULT_CHART_OF_ACCOUNTS]
    else:
        try:
            rows = list(coll.find({"is_active": {"$ne": False}}, {"_id": 0}))
        except Exception:  # noqa: BLE001
            rows = []
        if not rows:
            seed_chart_of_accounts(db)
            try:
                rows = list(coll.find({"is_active": {"$ne": False}}, {"_id": 0}))
            except Exception:  # noqa: BLE001
                rows = [dict(a, is_active=True) for a in DEFAULT_CHART_OF_ACCOUNTS]
    if manual_only:
        rows = [r for r in rows if r.get("allow_manual_je")]
    return sorted(rows, key=lambda r: str(r.get("account_code")))


def upsert_account(db, *, account_code: str, account_name: str, account_type: str,
                   allow_manual_je: bool, is_active: bool = True) -> Dict[str, Any]:
    """SUPERADMIN-only COA upsert."""
    coll = _coa_coll(db)
    if coll is None:
        return {"ok": False, "error": "no_db"}
    valid_types = {"ASSET", "LIABILITY", "EQUITY", "REVENUE", "EXPENSE"}
    if account_type not in valid_types:
        return {"ok": False, "error": "invalid_account_type"}
    doc = {
        "account_code": str(account_code),
        "account_name": account_name,
        "account_type": account_type,
        "allow_manual_je": bool(allow_manual_je),
        "is_active": bool(is_active),
    }
    try:
        coll.update_one(
            {"account_code": str(account_code)},
            {"$set": doc, "$setOnInsert": {"created_at": _now()}},
            upsert=True,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[JE] upsert_account %s failed: %s", account_code, e)
        return {"ok": False, "error": "write_failed"}
    return {"ok": True, "account": doc}


def _account(db, account_code: str) -> Optional[Dict[str, Any]]:
    coll = _coa_coll(db)
    if coll is not None:
        try:
            row = coll.find_one({"account_code": str(account_code)}, {"_id": 0})
            if row:
                return row
        except Exception:  # noqa: BLE001
            pass
    return _COA_BY_CODE.get(str(account_code))


# ============================================================================
# JE number (FY-scoped consecutive serial; atomic counter)
# ============================================================================


def _je_coll(db):
    if db is None:
        return None
    try:
        return db.get_collection("journal_entries")
    except Exception:  # noqa: BLE001
        try:
            return db["journal_entries"]
        except Exception:  # noqa: BLE001
            return None


def _counters_coll(db):
    if db is None:
        return None
    try:
        return db.get_collection("counters")
    except Exception:  # noqa: BLE001
        try:
            return db["counters"]
        except Exception:  # noqa: BLE001
            return None


def ensure_indexes(db) -> None:
    """Idempotent index creation. Best-effort; never raises."""
    coll = _je_coll(db)
    if coll is None:
        return
    try:
        coll.create_index("je_id", unique=True)
        coll.create_index("je_number", unique=True)
        coll.create_index([("store_id", 1), ("entry_date", -1)])
        coll.create_index([("status", 1), ("entity_id", 1), ("entry_date", 1)])
        coll.create_index([("maker_id", 1), ("status", 1), ("created_at", -1)])
    except Exception:  # noqa: BLE001
        logger.debug("[JE] ensure_indexes skipped", exc_info=True)


def _entry_prefix(db, entity_id: Optional[str]) -> str:
    """Short entity prefix for the JE number (best-effort; defaults to GEN)."""
    if not entity_id:
        return "GEN"
    coll = None
    if db is not None:
        try:
            coll = db.get_collection("entities")
        except Exception:  # noqa: BLE001
            coll = None
    if coll is not None:
        try:
            ent = coll.find_one({"entity_id": entity_id}) or {}
            code = (ent.get("code") or ent.get("entity_code") or ent.get("short_code") or "").strip()
            if code:
                return code.upper()[:6]
        except Exception:  # noqa: BLE001
            pass
    return str(entity_id).upper()[:6]


def _fy_start_for(entry_date: datetime) -> int:
    """FY start year (April) for a naive entry_date."""
    return entry_date.year if entry_date.month >= 4 else entry_date.year - 1


def _next_je_number(db, entity_id: Optional[str], entry_date: datetime) -> str:
    """Mint an FY-scoped consecutive JE number via an atomic counter
    (``counters.find_one_and_update($inc)``), mirroring
    order_repository.next_invoice_number. Format
    ``JE/{prefix}/{FY}/{serial:06d}`` e.g. JE/BV/2026-27/000001."""
    from pymongo import ReturnDocument

    if entry_date is None:
        fy_start = fy_start_year_ist(now_ist())
    else:
        fy_start = _fy_start_for(entry_date)
    fy_label = f"{fy_start}-{str(fy_start + 1)[-2:]}"
    prefix = _entry_prefix(db, entity_id)
    key = f"je:{entity_id or 'GEN'}:{fy_start}"

    seq = 1
    coll = _counters_coll(db)
    if coll is not None:
        try:
            doc = coll.find_one_and_update(
                {"_id": key},
                {"$inc": {"seq": 1}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
            seq = int((doc or {}).get("seq") or 1)
        except Exception as e:  # noqa: BLE001
            logger.warning("[JE] counter %s failed: %s", key, e)
            seq = int(now_ist().timestamp())  # fail-soft unique-ish fallback
    return f"JE/{prefix}/{fy_label}/{seq:06d}"


# ============================================================================
# Validation
# ============================================================================


def _to_paisa(val: Any) -> int:
    """Coerce a rupee value (number) to paisa-exact integers. Raises ValueError
    on a non-finite / negative value."""
    if val is None:
        return 0
    try:
        f = float(val)
    except (TypeError, ValueError):
        raise ValueError("non_numeric_amount")
    if f != f or f in (float("inf"), float("-inf")):  # NaN / inf guard
        raise ValueError("non_finite_amount")
    if f < 0:
        raise ValueError("negative_amount")
    # Round to the nearest paisa to absorb float artefacts (12.1 -> 1210).
    return int(round(f * 100))


def validate_lines(db, lines: List[Dict[str, Any]]) -> Tuple[Optional[str], List[Dict[str, Any]], int, int]:
    """Validate + normalise JE lines.

    Returns ``(error, normalised_lines, total_debit_paisa, total_credit_paisa)``.
    ``error`` is None on success. Rules:
      * at least 2 lines,
      * each line has exactly one of debit/credit non-zero,
      * every account_code exists with allow_manual_je=True,
      * total_debit == total_credit (paisa-exact).
    """
    if not isinstance(lines, list) or len(lines) < 2:
        return "min_two_lines", [], 0, 0

    norm: List[Dict[str, Any]] = []
    total_debit = 0
    total_credit = 0
    for raw in lines:
        if not isinstance(raw, dict):
            return "bad_line", [], 0, 0
        code = str(raw.get("account_code") or "").strip()
        if not code:
            return "missing_account_code", [], 0, 0
        acct = _account(db, code)
        if not acct:
            return "unknown_account", [], 0, 0
        if not acct.get("allow_manual_je"):
            return "account_not_allowed_for_manual_je", [], 0, 0
        try:
            debit = _to_paisa(raw.get("debit"))
            credit = _to_paisa(raw.get("credit"))
        except ValueError as ve:
            return str(ve), [], 0, 0
        if debit and credit:
            return "line_both_sides", [], 0, 0
        if not debit and not credit:
            return "line_zero", [], 0, 0
        total_debit += debit
        total_credit += credit
        norm.append({
            "line_id": f"JEL-{uuid.uuid4().hex[:10]}",
            "account_code": code,
            "account_name": acct.get("account_name"),
            # Snapshot the account TYPE at posting-time: a later COA type-edit must
            # NOT retroactively re-class POSTED entries in the P&L (immutable
            # ledger). pnl_adjustments prefers this snapshot over the live COA.
            "account_type": acct.get("account_type"),
            "debit": debit,
            "credit": credit,
            "narration": (str(raw.get("narration"))[:200] if raw.get("narration") else None),
        })

    if total_debit != total_credit:
        return "unbalanced", [], 0, 0
    return None, norm, total_debit, total_credit


# ============================================================================
# Audit
# ============================================================================


def _audit(db, action: str, je: Dict[str, Any], *, actor: str, reason: str = "") -> None:
    """Hash-chained audit row per JE transition via AuditRepository.create.
    Fail-soft -> the transition still stands."""
    if db is None:
        return
    try:
        coll = db.get_collection("audit_logs")
    except Exception:  # noqa: BLE001
        return
    if coll is None:
        return
    try:
        from database.repositories.audit_repository import AuditRepository
        repo = AuditRepository(coll)
    except Exception:  # noqa: BLE001
        return
    snapshot = {
        "je_id": je.get("je_id"),
        "je_number": je.get("je_number"),
        "status": je.get("status"),
        "total_debit": je.get("total_debit"),
        "total_credit": je.get("total_credit"),
        "store_id": je.get("store_id"),
        "entity_id": je.get("entity_id"),
    }
    doc = {
        "log_id": f"AUD-{uuid.uuid4().hex[:12]}",
        "action": action,
        "entity_type": "journal_entry",
        "entity_id": je.get("je_id"),
        "user_id": actor,
        "actor": actor,
        "source": "JE",
        "before_state": None,
        "after_state": snapshot,
        "reason": reason or None,
        "severity": "INFO",
        "timestamp": _now(),
    }
    try:
        repo.create(doc)
    except Exception as e:  # noqa: BLE001
        logger.warning("[JE] audit write failed for %s: %s", je.get("je_id"), e)


# ============================================================================
# Lifecycle
# ============================================================================


def get_je(db, je_id: str) -> Optional[Dict[str, Any]]:
    coll = _je_coll(db)
    if coll is None:
        return None
    try:
        return coll.find_one({"je_id": je_id}, {"_id": 0})
    except Exception:  # noqa: BLE001
        return None


def list_jes(db, *, store_id: Optional[str] = None, status: Optional[str] = None,
             maker_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    coll = _je_coll(db)
    if coll is None:
        return []
    q: Dict[str, Any] = {}
    if store_id:
        q["store_id"] = store_id
    if status and status.upper() != "ALL":
        q["status"] = status.upper()
    if maker_id:
        q["maker_id"] = maker_id
    try:
        rows = list(coll.find(q, {"_id": 0}).sort("created_at", -1).limit(int(limit)))
    except Exception:  # noqa: BLE001
        return []
    return rows


def create_je(
    db,
    *,
    store_id: Optional[str],
    entity_id: Optional[str],
    entry_date: datetime,
    description: str,
    lines: List[Dict[str, Any]],
    maker_id: str,
    maker_name: Optional[str] = None,
    reference: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a DRAFT JE after validating the balanced lines + COA. The caller
    (router) is responsible for the period-lock gate on entry_date."""
    coll = _je_coll(db)
    if coll is None:
        return {"ok": False, "http": 503, "error": "no_db"}

    err, norm, total_debit, total_credit = validate_lines(db, lines)
    if err:
        return {"ok": False, "http": 422, "error": err}

    now = _now()
    je = {
        "je_id": f"JE-{uuid.uuid4().hex[:12]}",
        "je_number": _next_je_number(db, entity_id, entry_date),
        "store_id": store_id,
        "entity_id": entity_id,
        "entry_date": entry_date,
        "description": (description or "")[:500],
        "reference": (reference[:200] if reference else None),
        "lines": norm,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "status": STATUS_DRAFT,
        "maker_id": maker_id,
        "maker_name": maker_name,
        "checker_id": None,
        "checker_name": None,
        "checker_note": None,
        "reversal_of": None,
        "reversed_by": None,
        "approval_request_id": None,
        "created_at": now,
        "submitted_at": None,
        "checked_at": None,
        "posted_at": None,
        "updated_at": now,
    }
    try:
        coll.insert_one(je)
    except Exception as e:  # noqa: BLE001
        logger.warning("[JE] create insert failed: %s", e)
        return {"ok": False, "http": 503, "error": "write_failed"}

    _audit(db, "je_created", je, actor=maker_id)
    je.pop("_id", None)
    logger.info("[JE] %s created (%s) by %s", je["je_id"], je["je_number"], maker_id)
    return {"ok": True, "je": _jsonable(je)}


def _set(db, je_id: str, fields: Dict[str, Any], *, expect_status: Optional[str] = None,
         expect_request_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Single-document guarded update. ``expect_status`` (and the linked
    request, ``expect_request_id``) is encoded in the filter so the transition
    is atomic (two racing writers -> one wins)."""
    from pymongo import ReturnDocument

    coll = _je_coll(db)
    if coll is None:
        return None
    q: Dict[str, Any] = {"je_id": je_id}
    if expect_status is not None:
        q["status"] = expect_status
    if expect_request_id is not None:
        q["approval_request_id"] = expect_request_id
    fields = dict(fields)
    fields["updated_at"] = _now()
    try:
        return coll.find_one_and_update(
            q, {"$set": fields}, return_document=ReturnDocument.AFTER
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[JE] _set %s failed: %s", je_id, e)
        return None


def _request_spec(je: Dict[str, Any]) -> Dict[str, Any]:
    """What a journal's approval request says. submit_je raises the request
    from this and _is_own_request holds a linked request to it -- one
    definition, so the two cannot drift."""
    return {
        "action_type": JE_ACTION_TYPE,
        "requested_by": je.get("maker_id"),
        "store_id": je.get("store_id"),
        "entity_id": je.get("entity_id"),
        "amount": (je.get("total_debit") or 0) / 100.0,
        "context": {"je_id": je.get("je_id"), "je_number": je.get("je_number"),
                    "description": je.get("description")},
    }


def _is_own_request(je: Dict[str, Any], req: Optional[Dict[str, Any]]) -> bool:
    """True when ``req`` is the approval request of THIS journal: raised by its
    maker, as a journal_entry, for this journal, shop and exact total (paise).
    A decision on any other request -- one for Rs 1 that merely names the
    journal, say -- is never this journal's decision."""
    if not req or req.get("amount") is None:
        return False
    spec = _request_spec(je)
    amount = spec.pop("amount")
    return (int(round(float(req["amount"]) * 100)) == int(round(amount * 100))
            and all(req.get(k) == v for k, v in spec.items()))


def _raise_own_request(db, je: Dict[str, Any], maker_roles: Optional[List[str]]) -> Optional[str]:
    """Raise this journal's own approval request (_request_spec); its id, or
    None when none could be recorded. The ONE place a journal's request is
    raised: at submit, and again when a checker decides a journal whose
    request was decided without it (_open_request)."""
    from .approvals import ApprovalEngine, request_approval

    # The dedupe key hands a repeat its earlier live request back. Anyone can
    # raise a request under that key first (the generic create route), for any
    # amount and tier, so a request that is not this journal's own is never
    # linked: the journal then raises its own, with no key.
    for dedupe_key in (f"je:{je.get('je_id')}", None):
        req = request_approval(
            db,
            **_request_spec(je),
            requested_by_roles=list(maker_roles or []),
            reason="Manual journal entry maker-checker approval",
            dedupe_key=dedupe_key,
        )
        if not req or not req.get("request_id"):
            return None
        if _is_own_request(je, ApprovalEngine(db=db).get(req["request_id"])):
            return req["request_id"]
    return None


def submit_je(db, *, je_id: str, maker_id: str, maker_roles: List[str],
              maker_store_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """DRAFT -> SUBMITTED. Opens an E4 maker-checker approval request
    (action_type=journal_entry) and links it on the JE. Only the maker may
    submit their own DRAFT."""
    je = get_je(db, je_id)
    if je is None:
        return {"ok": False, "http": 404, "error": "not_found"}
    if je.get("status") != STATUS_DRAFT:
        return {"ok": False, "http": 409, "error": "not_draft", "status": je.get("status")}
    if je.get("maker_id") != maker_id:
        return {"ok": False, "http": 403, "error": "not_maker"}

    # REUSE E4: open a maker-checker approval request. E4 sets maker_checker=True
    # for journal_entry and will hard-block self-approval at approve-time.
    request_id = _raise_own_request(db, je, maker_roles)
    if not request_id:
        return {"ok": False, "http": 503, "error": "approval_request_failed"}

    updated = _set(
        db, je_id,
        {"status": STATUS_SUBMITTED, "submitted_at": _now(),
         "approval_request_id": request_id},
        expect_status=STATUS_DRAFT,
    )
    if updated is None:
        # Lost a race (already submitted) -- surface the current state.
        cur = get_je(db, je_id)
        return {"ok": False, "http": 409, "error": "not_draft",
                "status": (cur or {}).get("status")}
    updated.pop("_id", None)
    _audit(db, "je_submitted", updated, actor=maker_id)
    return {"ok": True, "je": _jsonable(updated), "request_id": request_id}


def _record_decision(db, je: Dict[str, Any], *, status: str, checker_id: str,
                     note: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The ONE writer of SUBMITTED -> APPROVED / REJECTED: a guarded
    single-document update (two racing writers -> one wins) plus its audit
    row. Returns the updated journal, or None when it was no longer SUBMITTED."""
    fields: Dict[str, Any] = {"status": status, "checker_id": checker_id, "checked_at": _now()}
    if status == STATUS_REJECTED:
        fields["checker_note"] = (note or "")[:500] or None
    updated = _set(db, je["je_id"], fields, expect_status=STATUS_SUBMITTED)
    if updated is None:
        return None
    updated.pop("_id", None)
    if status == STATUS_REJECTED:
        _audit(db, "je_rejected", updated, actor=checker_id, reason=note or "")
    else:
        _audit(db, "je_approved", updated, actor=checker_id)
    return updated


def _note_ok(note: Optional[str]) -> bool:
    """The ONE rejection-note rule: a journal is rejected only with a checker
    note of at least 10 characters, whichever door rejects it."""
    return len((note or "").strip()) >= 10


def _take_request_decision(db, je: Dict[str, Any]) -> Dict[str, Any]:
    """A SUBMITTED journal takes the decision already recorded on its approval
    request; the journal as it now stands is returned.

    The request and the journal are two documents and this database has no
    transactions, so the request can be decided while the journal still says
    SUBMITTED: the decision was made in the approvals inbox (L3-25), or the
    journal write that follows a journal-screen decision was lost. Post then
    refused not_approved and Approve refused already_reviewed -- a dead end.
    Every journal door runs this first, and the inbox runs it right after its
    own approve / reject (sync_with_request), so the journal catches up from
    its request. Safe to repeat and to race (_record_decision is guarded).

    Taken only when every one of these holds -- the rules the journal screen
    itself applies:
      * the request is the journal's own (_is_own_request);
      * whoever decided it held a journal checker role when they did (the
        engine records it as reviewed_by_roles). A request decided before the
        engine routed journals to the checker roles alone may carry a store
        or area manager's click, or no roles at all; that is never a journal
        decision (the checker's own Approve / Reject is: _open_request);
      * an APPROVAL was not made by the journal's maker (a maker may reject,
        that is withdraw, their own journal -- as on the journal screen);
      * a REJECTION carries the checker note (_note_ok)."""
    if je.get("status") != STATUS_SUBMITTED or not je.get("approval_request_id"):
        return je
    from .approvals import ApprovalEngine, ApprovalStatus

    req = ApprovalEngine(db=db).get(je["approval_request_id"]) or {}
    checker = req.get("reviewed_by")
    if (not checker or not _is_own_request(je, req)
            or not set(req.get("reviewed_by_roles") or []) & JE_CHECKER_ROLES):
        return je
    decided = req.get("status")
    note = (req.get("reject_reason") or "").strip()
    if decided == ApprovalStatus.APPROVED.value and checker != je.get("maker_id"):
        updated = _record_decision(db, je, status=STATUS_APPROVED, checker_id=checker)
    elif decided == ApprovalStatus.REJECTED.value and _note_ok(note):
        updated = _record_decision(db, je, status=STATUS_REJECTED, checker_id=checker, note=note)
    else:
        return je
    return updated or get_je(db, je["je_id"]) or je


def _journal_awaiting(db, req: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The SUBMITTED journal whose own approval request ``req`` is, for the
    approvals inbox; None for any other request. None too while manual
    journals are switched off: the journal screen's approve / reject / post
    are frozen then, and the inbox does not move a journal behind the switch
    (the journal catches up at its own doors once it is on again)."""
    if not is_je_enabled() or (req or {}).get("action_type") != JE_ACTION_TYPE:
        return None
    je = get_je(db, (req.get("context") or {}).get("je_id") or "")
    if (je and je.get("status") == STATUS_SUBMITTED
            and je.get("approval_request_id") == req.get("request_id")
            and _is_own_request(je, req)):
        return je
    return None


def inbox_reject_refusal(db, req: Optional[Dict[str, Any]], decider_roles: List[str],
                         note: Optional[str]) -> Optional[Dict[str, Any]]:
    """The approvals inbox is about to reject ``req``. When that rejection
    will reject a journal (sync_with_request), the journal screen's note rule
    holds here too: the refusal reject_je gives, else None."""
    if (not _note_ok(note) and set(decider_roles or []) & JE_CHECKER_ROLES
            and _journal_awaiting(db, req) is not None):
        return {"ok": False, "http": 400, "error": "note_required"}
    return None


def sync_with_request(db, req: Optional[Dict[str, Any]]) -> None:
    """The approvals inbox decided ``req``: when it is a journal's own request
    the journal moves with it now (_take_request_decision, which reads the
    decision back from the request itself)."""
    je = _journal_awaiting(db, req)
    if je is not None:
        _take_request_decision(db, je)


def _post_mark(je: Dict[str, Any]) -> str:
    """What a journal's Post stamps on the approval it spends (consumed_for),
    in the same write that spends it: the binding that lets a Post whose
    journal write was lost finish that posting, and nothing else (D54)."""
    return f"je_post:{je.get('je_id')}"


def _approval_state(je: Dict[str, Any], req: Optional[Dict[str, Any]]) -> str:
    """What the request linked to an APPROVED journal can still do for it:
      spendable    an approval Post can spend now;
      post_bound   already spent by THIS journal's Post (its journal write was
                   lost): a Post retry finishes the posting;
      pending      a request raised again, waiting for a checker;
      unspendable  lapsed, spent anywhere else, refused, missing or not the
                   journal's own: only a checker's fresh approval helps.
    Read-only (no lazy expiry write), so a list can ask it."""
    from .approvals import ApprovalStatus, _as_aware, _now as _engine_now

    if not req or not _is_own_request(je, req):
        return "unspendable"
    status = req.get("status")
    if req.get("consumed") or status == ApprovalStatus.CONSUMED.value:
        return "post_bound" if req.get("consumed_for") == _post_mark(je) else "unspendable"
    live = _as_aware(req.get("expires_at")) > _engine_now()
    if status == ApprovalStatus.APPROVED.value:
        return "spendable" if live else "unspendable"
    if status == ApprovalStatus.REQUESTED.value:
        return "pending" if live else "unspendable"
    return "unspendable"


def needs_reapproval(db, je: Dict[str, Any]) -> bool:
    """An APPROVED journal that cannot post until a checker approves it again
    (D54): its approval lapsed or was spent elsewhere, or the one raised in
    its place still waits. The journal screen offers the checker the PIN box."""
    if (je or {}).get("status") != STATUS_APPROVED:
        return False
    from .approvals import ApprovalEngine

    req = ApprovalEngine(db=db)._raw(je.get("approval_request_id") or "")
    return _approval_state(je, req) in ("pending", "unspendable")


def with_reapproval_flags(db, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """``needs_reapproval`` on every APPROVED journal of ``rows`` (in place)."""
    for row in rows:
        if row.get("status") == STATUS_APPROVED:
            row["needs_reapproval"] = needs_reapproval(db, row)
    return rows


def _open_request(db, je: Dict[str, Any], *, actor_id: str,
                  actor_roles: List[str]) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """The request a checker's Approve / Reject of this SUBMITTED journal --
    or Approve of an APPROVED one that can no longer post -- decides:
    (request id, None), or (None, the refusal to answer).

    Normally the request the journal is linked to. When that one can no
    longer decide the journal, the journal raises its own request again
    (_raise_own_request) and the checker decides THAT one, through the
    engine's usual role, maker-checker, shop and PIN gates:
      * SUBMITTED, request already DECIDED by a decision the journal does not
        take (_take_request_decision: a store or area manager's inbox click,
        an inbox decision made before the inbox moved the journal, L3-25);
      * SUBMITTED, request LAPSED undecided (D54: the engine's validity
        window ended before anyone decided; the maker cannot resubmit);
      * APPROVED, approval no longer spendable by Post (D54: lapsed before
        Post, or spent somewhere other than this journal's Post).
    The old request is left exactly as it is stored -- it stays the record of
    what happened -- and the journal's audit trail names both. The new link
    is guarded on the one it replaces, so two checkers arriving together end
    on one request. An approval spent by this journal's own Post is never
    replaced: a Post retry finishes that posting (post_je)."""
    from .approvals import ApprovalEngine, ApprovalStatus

    old_id = je.get("approval_request_id")
    old = ApprovalEngine(db=db).get(old_id) or {}
    status = je.get("status")
    if not set(actor_roles or []) & JE_CHECKER_ROLES:
        if status == STATUS_SUBMITTED:
            return old_id, None
        return None, {"ok": False, "http": 409, "error": "not_submitted", "status": status}
    if status == STATUS_APPROVED:
        state = _approval_state(je, old)
        if state == "pending":
            return old_id, None
        if state != "unspendable":
            return None, {"ok": False, "http": 409, "error": "not_submitted", "status": status}
    elif old.get("status") not in (ApprovalStatus.APPROVED.value, ApprovalStatus.REJECTED.value,
                                   ApprovalStatus.CONSUMED.value, ApprovalStatus.EXPIRED.value):
        return old_id, None
    new_id = _raise_own_request(db, je, old.get("requested_by_roles"))
    if not new_id:
        return None, {"ok": False, "http": 503, "error": "approval_request_failed"}
    linked = _set(db, je["je_id"], {"approval_request_id": new_id},
                  expect_status=status, expect_request_id=old_id)
    if linked is None:
        # Another checker moved the journal first: decide whatever request it
        # is linked to now, or say it is no longer waiting.
        cur = get_je(db, je["je_id"]) or {}
        if cur.get("status") == status and cur.get("approval_request_id") not in (None, old_id):
            return cur["approval_request_id"], None
        return None, {"ok": False, "http": 409, "error": "not_submitted",
                      "status": cur.get("status")}
    linked.pop("_id", None)
    _audit(db, "je_request_raised_again", linked, actor=actor_id,
           reason=(f"request {old_id} can no longer decide the journal "
                   f"({old.get('status') or 'missing'}); request {new_id} raised in its place"))
    return new_id, None


def approve_je(db, *, je_id: str, approver_id: str, approver_roles: List[str],
               pin: str, approver_store_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """SUBMITTED -> APPROVED. Delegates the PIN + maker-checker (approver !=
    maker) + atomic single-use approve to the E4 ApprovalEngine. Does NOT post
    to the ledger (that is a separate /post step that consumes the approval)."""
    je = get_je(db, je_id)
    if je is None:
        return {"ok": False, "http": 404, "error": "not_found"}
    je = _take_request_decision(db, je)
    if je.get("status") not in (STATUS_SUBMITTED, STATUS_APPROVED):
        return {"ok": False, "http": 409, "error": "not_submitted", "status": je.get("status")}
    request_id = je.get("approval_request_id")
    if not request_id:
        return {"ok": False, "http": 409, "error": "no_approval_request"}
    if approver_id == je.get("maker_id"):
        # The journal knows its own maker: held here too, whoever the linked
        # request names as its requester.
        return {"ok": False, "http": 403, "error": "cannot_approve_own"}
    request_id, refusal = _open_request(db, je, actor_id=approver_id, actor_roles=approver_roles)
    if refusal:
        return refusal

    from .approvals import ApprovalEngine

    res = ApprovalEngine(db=db).approve(
        request_id,
        approver_user_id=approver_id,
        approver_roles=list(approver_roles or []),
        pin=pin,
        approver_store_ids=approver_store_ids,
    )
    if not res.get("ok"):
        # E4 already computed the precise http code (403 cannot_approve_own,
        # 423 pin_not_set/pin_locked, 403 wrong_pin, 409 already_reviewed, ...).
        out = {"ok": False, "http": int(res.get("http", 400)), "error": res.get("error")}
        for k in ("remaining", "retry_after_min", "status"):
            if k in res:
                out[k] = res[k]
        return out

    if je.get("status") == STATUS_APPROVED:
        # Approved again (D54): the journal stays APPROVED, now on the
        # approval just made; Post spends that one.
        updated = _set(db, je_id, {"checker_id": approver_id, "checked_at": _now()},
                       expect_status=STATUS_APPROVED, expect_request_id=request_id)
        if updated is not None:
            updated.pop("_id", None)
            _audit(db, "je_reapproved", updated, actor=approver_id)
    else:
        updated = _record_decision(db, je, status=STATUS_APPROVED, checker_id=approver_id)
    if updated is None:
        cur = get_je(db, je_id)
        return {"ok": False, "http": 409, "error": "not_submitted",
                "status": (cur or {}).get("status")}
    return {"ok": True, "je": _jsonable(updated), "approval_token": res.get("approval_token")}


def reject_je(db, *, je_id: str, approver_id: str, approver_roles: List[str],
              pin: str, note: str, approver_store_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """SUBMITTED -> REJECTED with a mandatory checker note. Delegates the PIN +
    atomic reject to E4."""
    note = (note or "").strip()
    if not _note_ok(note):
        return {"ok": False, "http": 400, "error": "note_required"}
    je = get_je(db, je_id)
    if je is None:
        return {"ok": False, "http": 404, "error": "not_found"}
    je = _take_request_decision(db, je)
    if je.get("status") != STATUS_SUBMITTED:
        return {"ok": False, "http": 409, "error": "not_submitted", "status": je.get("status")}
    request_id = je.get("approval_request_id")
    if not request_id:
        return {"ok": False, "http": 409, "error": "no_approval_request"}
    request_id, refusal = _open_request(db, je, actor_id=approver_id, actor_roles=approver_roles)
    if refusal:
        return refusal

    from .approvals import ApprovalEngine

    res = ApprovalEngine(db=db).reject(
        request_id,
        approver_user_id=approver_id,
        approver_roles=list(approver_roles or []),
        pin=pin,
        reason=note,
        approver_store_ids=approver_store_ids,
    )
    if not res.get("ok"):
        out = {"ok": False, "http": int(res.get("http", 400)), "error": res.get("error")}
        for k in ("remaining", "retry_after_min", "status"):
            if k in res:
                out[k] = res[k]
        return out

    updated = _record_decision(db, je, status=STATUS_REJECTED, checker_id=approver_id, note=note)
    if updated is None:
        cur = get_je(db, je_id)
        return {"ok": False, "http": 409, "error": "not_submitted",
                "status": (cur or {}).get("status")}
    return {"ok": True, "je": _jsonable(updated)}


def post_je(db, *, je_id: str, poster_id: str) -> Dict[str, Any]:
    """APPROVED -> POSTED. Consumes the E4 approval EXACTLY ONCE (single-use
    guard), then atomically flips the JE to POSTED. The status==APPROVED filter
    on the JE update makes the post itself idempotent under a race. The caller
    (router) must re-check the period lock on entry_date (double gate)."""
    je = get_je(db, je_id)
    if je is None:
        return {"ok": False, "http": 404, "error": "not_found"}
    je = _take_request_decision(db, je)
    status = je.get("status")
    if status == STATUS_POSTED:
        return {"ok": False, "http": 409, "error": "already_posted"}
    if status != STATUS_APPROVED:
        return {"ok": False, "http": 409, "error": "not_approved", "status": status}
    request_id = je.get("approval_request_id")
    if not request_id:
        return {"ok": False, "http": 409, "error": "no_approval_request"}

    from .approvals import ApprovalEngine

    amount_rupees = (je.get("total_debit") or 0) / 100.0
    engine = ApprovalEngine(db=db)
    consume = engine.consume_approval(
        consumed_by=poster_id,
        action_type=JE_ACTION_TYPE,
        request_id=request_id,
        amount=amount_rupees,
        consumed_for=_post_mark(je),
    )
    # An approval THIS journal's Post already spent, whose journal write was
    # then lost, is not an error: the guarded APPROVED -> POSTED write below
    # finishes that posting exactly once (D54).
    if not consume.get("ok") and not (
        consume.get("error") == "already_consumed"
        and _approval_state(je, engine._raw(request_id)) == "post_bound"
    ):
        err = consume.get("error")
        code = {"already_consumed": 409, "expired": 410, "not_approved": 409,
                "action_mismatch": 400, "amount_exceeded": 400, "not_found": 404}.get(err, 409)
        return {"ok": False, "http": code, "error": err or "consume_failed"}

    updated = _set(
        db, je_id,
        {"status": STATUS_POSTED, "posted_at": _now()},
        expect_status=STATUS_APPROVED,
    )
    if updated is None:
        # The approval was consumed but the JE was already moved by a racing
        # poster -- treat as already posted (idempotent post).
        cur = get_je(db, je_id)
        if (cur or {}).get("status") == STATUS_POSTED:
            return {"ok": False, "http": 409, "error": "already_posted"}
        return {"ok": False, "http": 409, "error": "not_approved",
                "status": (cur or {}).get("status")}
    updated.pop("_id", None)
    _audit(db, "je_posted", updated, actor=poster_id)
    logger.info("[JE] %s POSTED by %s", je_id, poster_id)
    return {"ok": True, "je": _jsonable(updated)}


def reverse_je(db, *, je_id: str, actor_id: str, actor_name: Optional[str] = None) -> Dict[str, Any]:
    """Reverse a POSTED JE: mints a mirror JE (debits/credits swapped) dated
    today (IST), posts it immediately, links both. The original moves to
    REVERSED. The caller must check today's period is open."""
    coll = _je_coll(db)
    if coll is None:
        return {"ok": False, "http": 503, "error": "no_db"}
    je = get_je(db, je_id)
    if je is None:
        return {"ok": False, "http": 404, "error": "not_found"}
    # An already-reversed entry (REVERSED status or a reversed_by link) gets the
    # specific 'already_reversed' before the generic 'not_posted'.
    if je.get("status") == STATUS_REVERSED or je.get("reversed_by"):
        return {"ok": False, "http": 409, "error": "already_reversed"}
    if je.get("status") != STATUS_POSTED:
        return {"ok": False, "http": 409, "error": "not_posted", "status": je.get("status")}

    today = _now()
    swapped: List[Dict[str, Any]] = []
    for ln in je.get("lines") or []:
        swapped.append({
            "line_id": f"JEL-{uuid.uuid4().hex[:10]}",
            "account_code": ln.get("account_code"),
            "account_name": ln.get("account_name"),
            # Carry the posting-time type snapshot onto the mirror so the pair
            # nets to zero under the same classification forever.
            "account_type": ln.get("account_type"),
            "debit": int(ln.get("credit") or 0),
            "credit": int(ln.get("debit") or 0),
            "narration": ("Reversal: " + (ln.get("narration") or ""))[:200],
        })
    rev = {
        "je_id": f"JE-{uuid.uuid4().hex[:12]}",
        # je_number is minted AFTER the claim succeeds (below) -- minting it here
        # would burn a consecutive serial when the claim loses the race.
        "je_number": None,
        "store_id": je.get("store_id"),
        "entity_id": je.get("entity_id"),
        "entry_date": today,
        "description": f"Reversal of {je.get('je_number')}: {je.get('description') or ''}"[:500],
        "reference": je.get("je_number"),
        "lines": swapped,
        "total_debit": int(je.get("total_credit") or 0),
        "total_credit": int(je.get("total_debit") or 0),
        "status": STATUS_POSTED,
        "maker_id": actor_id,
        "maker_name": actor_name,
        "checker_id": actor_id,
        "checker_name": actor_name,
        "checker_note": None,
        "reversal_of": je_id,
        "reversed_by": None,
        "approval_request_id": None,
        "created_at": today,
        "submitted_at": today,
        "checked_at": today,
        "posted_at": today,
        "updated_at": today,
    }
    # CLAIM-FIRST (adversarial P3): atomically flip the original POSTED -> REVERSED
    # BEFORE inserting the mirror. Two racing reversals -> exactly one wins the
    # claim (the loser gets 409 with NO orphan mirror ever inserted). The old
    # order (insert mirror, then flip, silent best-effort delete on lost race)
    # left an orphan-POSTED-reversal window.
    updated = _set(
        db, je_id,
        {"status": STATUS_REVERSED, "reversed_by": rev["je_id"]},
        expect_status=STATUS_POSTED,
    )
    if updated is None:
        cur = get_je(db, je_id)
        return {"ok": False, "http": 409, "error": "already_reversed",
                "status": (cur or {}).get("status")}

    # Claim won -- now mint the consecutive serial (no burned numbers on a lost
    # race) and insert the mirror.
    rev["je_number"] = _next_je_number(db, je.get("entity_id"), today)
    try:
        coll.insert_one(rev)
    except Exception as e:  # noqa: BLE001
        # The claim stands but the mirror failed -- compensate by un-flipping OUR
        # claim (guarded on our own reversed_by link), and log LOUDLY either way:
        # if the un-flip also fails the books need manual repair NOW.
        logger.error("[JE] reversal mirror insert FAILED for %s (mirror %s): %s",
                     je_id, rev["je_id"], e)
        try:
            restored = coll.find_one_and_update(
                {"je_id": je_id, "status": STATUS_REVERSED, "reversed_by": rev["je_id"]},
                {"$set": {"status": STATUS_POSTED, "reversed_by": None,
                          "updated_at": _now()}},
            )
            if restored is None:
                logger.error("[JE] compensating un-flip MISSED for %s -- original "
                             "left REVERSED with no mirror; manual repair required",
                             je_id)
        except Exception as e2:  # noqa: BLE001
            logger.error("[JE] compensating un-flip FAILED for %s: %s -- original "
                         "left REVERSED with no mirror; manual repair required",
                         je_id, e2)
        return {"ok": False, "http": 503, "error": "write_failed"}

    rev.pop("_id", None)
    updated.pop("_id", None)
    _audit(db, "je_reversed", updated, actor=actor_id, reason=f"reversed_by={rev['je_id']}")
    _audit(db, "je_posted", rev, actor=actor_id, reason=f"reversal_of={je_id}")
    return {"ok": True, "reversal_je_id": rev["je_id"], "je": _jsonable(rev),
            "original": _jsonable(updated)}


# ============================================================================
# P&L aggregation (POSTED JEs only)
# ============================================================================


def pnl_adjustments(db, *, store_id: Optional[str], from_dt, to_dt) -> Dict[str, float]:
    """Net P&L effect of POSTED manual JEs in the date range, keyed by account
    type. REVENUE: credit increases revenue; EXPENSE: debit increases cost.
    Returns rupee floats. Fail-soft -> zeros."""
    out = {"je_revenue_adjustment": 0.0, "je_expense_adjustment": 0.0}
    coll = _je_coll(db)
    if coll is None:
        return out
    # POSTED entries hit the ledger. A REVERSED entry was genuinely posted (it
    # affected the period's books) and must STAY counted -- closed-period
    # integrity: a reversal posts a fresh offsetting JE in the reversal period
    # rather than retroactively un-counting the original. So both POSTED and
    # REVERSED originals are in scope; their reversal JEs (also POSTED) net them.
    match: Dict[str, Any] = {"status": {"$in": [STATUS_POSTED, STATUS_REVERSED]}}
    if store_id:
        match["store_id"] = store_id
    date_q: Dict[str, Any] = {}
    if from_dt is not None:
        date_q["$gte"] = from_dt
    if to_dt is not None:
        date_q["$lte"] = to_dt
    if date_q:
        match["entry_date"] = date_q
    try:
        rows = list(coll.find(match, {"_id": 0, "lines": 1}))
    except Exception:  # noqa: BLE001
        return out
    rev_net = 0
    exp_net = 0
    for je in rows:
        for ln in je.get("lines") or []:
            # Prefer the posting-time account_type SNAPSHOT on the line (immutable
            # ledger: a later COA type-edit must not re-class POSTED entries).
            # Legacy lines (pre-snapshot) fall back to the live COA.
            atype = ln.get("account_type")
            if not atype:
                acct = _account(db, ln.get("account_code"))
                if not acct:
                    continue
                atype = acct.get("account_type")
            debit = int(ln.get("debit") or 0)
            credit = int(ln.get("credit") or 0)
            if atype == "REVENUE":
                rev_net += (credit - debit)
            elif atype == "EXPENSE":
                exp_net += (debit - credit)
    out["je_revenue_adjustment"] = round(rev_net / 100.0, 2)
    out["je_expense_adjustment"] = round(exp_net / 100.0, 2)
    return out


# ============================================================================
# Tally JOURNALVOUCHER export (POSTED JEs only)
# ============================================================================


def build_journal_voucher_xml(jes: List[Dict[str, Any]]) -> str:
    """Pure function: POSTED JEs -> Tally ``<JOURNALVOUCHER>`` import XML. Debit
    side = ISDEEMEDPOSITIVE=Yes (Tally convention for the debited ledger),
    credit side = ISDEEMEDPOSITIVE=No. Amounts are rupees (paisa/100)."""
    from xml.sax.saxutils import escape

    def _date_str(dt: Any) -> str:
        if isinstance(dt, datetime):
            return dt.strftime("%Y%m%d")
        s = str(dt or "")[:10].replace("-", "")
        return s or now_ist().strftime("%Y%m%d")

    vouchers: List[str] = []
    for je in jes:
        if je.get("status") != STATUS_POSTED:
            continue
        vnum = escape(str(je.get("je_number") or je.get("je_id") or ""))
        vdate = _date_str(je.get("entry_date"))
        narr = escape(str(je.get("description") or ""))
        entries: List[str] = []
        for ln in je.get("lines") or []:
            ledger = escape(str(ln.get("account_name") or ln.get("account_code") or ""))
            debit = int(ln.get("debit") or 0)
            credit = int(ln.get("credit") or 0)
            if debit:
                # Debit: amount negative + ISDEEMEDPOSITIVE Yes (Tally convention).
                entries.append(
                    "\n    <ALLLEDGERENTRIES.LIST>"
                    f"\n      <LEDGERNAME>{ledger}</LEDGERNAME>"
                    "\n      <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>"
                    f"\n      <AMOUNT>-{debit / 100.0:.2f}</AMOUNT>"
                    "\n    </ALLLEDGERENTRIES.LIST>"
                )
            else:
                entries.append(
                    "\n    <ALLLEDGERENTRIES.LIST>"
                    f"\n      <LEDGERNAME>{ledger}</LEDGERNAME>"
                    "\n      <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>"
                    f"\n      <AMOUNT>{credit / 100.0:.2f}</AMOUNT>"
                    "\n    </ALLLEDGERENTRIES.LIST>"
                )
        vouchers.append(
            "\n  <VOUCHER VCHTYPE=\"Journal\" ACTION=\"Create\">"
            f"\n    <DATE>{vdate}</DATE>"
            "\n    <VOUCHERTYPENAME>Journal</VOUCHERTYPENAME>"
            f"\n    <VOUCHERNUMBER>{vnum}</VOUCHERNUMBER>"
            f"\n    <NARRATION>{narr}</NARRATION>"
            f"{''.join(entries)}"
            "\n  </VOUCHER>"
        )
    body = "".join(vouchers)
    return (
        "<ENVELOPE>\n"
        "  <HEADER>\n"
        "    <TALLYREQUEST>Import Data</TALLYREQUEST>\n"
        "  </HEADER>\n"
        "  <BODY>\n"
        "    <IMPORTDATA>\n"
        "      <REQUESTDESC>\n"
        "        <REPORTNAME>Vouchers</REPORTNAME>\n"
        "      </REQUESTDESC>\n"
        f"      <REQUESTDATA>{body}\n"
        "      </REQUESTDATA>\n"
        "    </IMPORTDATA>\n"
        "  </BODY>\n"
        "</ENVELOPE>"
    )


# ============================================================================
# Serialization
# ============================================================================


def _jsonable(je: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(je)
    out.pop("_id", None)
    for k in ("entry_date", "created_at", "submitted_at", "checked_at", "posted_at", "updated_at"):
        if k in out:
            out[k] = _iso(out[k])
    return out
