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


def _set(db, je_id: str, fields: Dict[str, Any], *, expect_status: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Single-document guarded update. ``expect_status`` is encoded in the
    filter so the transition is atomic (two racing writers -> one wins)."""
    from pymongo import ReturnDocument

    coll = _je_coll(db)
    if coll is None:
        return None
    q: Dict[str, Any] = {"je_id": je_id}
    if expect_status is not None:
        q["status"] = expect_status
    fields = dict(fields)
    fields["updated_at"] = _now()
    try:
        return coll.find_one_and_update(
            q, {"$set": fields}, return_document=ReturnDocument.AFTER
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[JE] _set %s failed: %s", je_id, e)
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
    from .approvals import request_approval

    amount_rupees = (je.get("total_debit") or 0) / 100.0
    req = request_approval(
        db,
        action_type=JE_ACTION_TYPE,
        requested_by=maker_id,
        requested_by_roles=list(maker_roles or []),
        store_id=je.get("store_id"),
        entity_id=je.get("entity_id"),
        amount=amount_rupees,
        context={"je_id": je_id, "je_number": je.get("je_number"),
                 "description": je.get("description")},
        reason="Manual journal entry maker-checker approval",
        dedupe_key=f"je:{je_id}",
    )
    if not req or not req.get("request_id"):
        return {"ok": False, "http": 503, "error": "approval_request_failed"}

    updated = _set(
        db, je_id,
        {"status": STATUS_SUBMITTED, "submitted_at": _now(),
         "approval_request_id": req["request_id"]},
        expect_status=STATUS_DRAFT,
    )
    if updated is None:
        # Lost a race (already submitted) -- surface the current state.
        cur = get_je(db, je_id)
        return {"ok": False, "http": 409, "error": "not_draft",
                "status": (cur or {}).get("status")}
    updated.pop("_id", None)
    _audit(db, "je_submitted", updated, actor=maker_id)
    return {"ok": True, "je": _jsonable(updated), "request_id": req["request_id"]}


def approve_je(db, *, je_id: str, approver_id: str, approver_roles: List[str],
               pin: str, approver_store_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """SUBMITTED -> APPROVED. Delegates the PIN + maker-checker (approver !=
    maker) + atomic single-use approve to the E4 ApprovalEngine. Does NOT post
    to the ledger (that is a separate /post step that consumes the approval)."""
    je = get_je(db, je_id)
    if je is None:
        return {"ok": False, "http": 404, "error": "not_found"}
    if je.get("status") != STATUS_SUBMITTED:
        return {"ok": False, "http": 409, "error": "not_submitted", "status": je.get("status")}
    request_id = je.get("approval_request_id")
    if not request_id:
        return {"ok": False, "http": 409, "error": "no_approval_request"}

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

    updated = _set(
        db, je_id,
        {"status": STATUS_APPROVED, "checker_id": approver_id, "checked_at": _now()},
        expect_status=STATUS_SUBMITTED,
    )
    if updated is None:
        cur = get_je(db, je_id)
        return {"ok": False, "http": 409, "error": "not_submitted",
                "status": (cur or {}).get("status")}
    updated.pop("_id", None)
    _audit(db, "je_approved", updated, actor=approver_id)
    return {"ok": True, "je": _jsonable(updated), "approval_token": res.get("approval_token")}


def reject_je(db, *, je_id: str, approver_id: str, approver_roles: List[str],
              pin: str, note: str, approver_store_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """SUBMITTED -> REJECTED with a mandatory checker note. Delegates the PIN +
    atomic reject to E4."""
    note = (note or "").strip()
    if len(note) < 10:
        return {"ok": False, "http": 400, "error": "note_required"}
    je = get_je(db, je_id)
    if je is None:
        return {"ok": False, "http": 404, "error": "not_found"}
    if je.get("status") != STATUS_SUBMITTED:
        return {"ok": False, "http": 409, "error": "not_submitted", "status": je.get("status")}
    request_id = je.get("approval_request_id")
    if not request_id:
        return {"ok": False, "http": 409, "error": "no_approval_request"}

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

    updated = _set(
        db, je_id,
        {"status": STATUS_REJECTED, "checker_id": approver_id, "checked_at": _now(),
         "checker_note": note[:500]},
        expect_status=STATUS_SUBMITTED,
    )
    if updated is None:
        cur = get_je(db, je_id)
        return {"ok": False, "http": 409, "error": "not_submitted",
                "status": (cur or {}).get("status")}
    updated.pop("_id", None)
    _audit(db, "je_rejected", updated, actor=approver_id, reason=note)
    return {"ok": True, "je": _jsonable(updated)}


def post_je(db, *, je_id: str, poster_id: str) -> Dict[str, Any]:
    """APPROVED -> POSTED. Consumes the E4 approval EXACTLY ONCE (single-use
    guard), then atomically flips the JE to POSTED. The status==APPROVED filter
    on the JE update makes the post itself idempotent under a race. The caller
    (router) must re-check the period lock on entry_date (double gate)."""
    je = get_je(db, je_id)
    if je is None:
        return {"ok": False, "http": 404, "error": "not_found"}
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
    consume = ApprovalEngine(db=db).consume_approval(
        consumed_by=poster_id,
        action_type=JE_ACTION_TYPE,
        request_id=request_id,
        amount=amount_rupees,
    )
    if not consume.get("ok"):
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
