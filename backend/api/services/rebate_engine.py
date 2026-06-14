"""Vendor volume-rebate engine -- pure, deterministic, integer paise.

Feature #18. Tiered earned-rebate computation over a vendor's accepted
purchase-invoice spend within a period. All money is integer paise. No I/O
here -- callers pass plain dicts; the router (vendor_rebates.py) wires DB + the
AP reduction (a credit note against the vendor) + the Tally JV intent.

Owner decision (binding, memory owner_decisions_2026_06_12_gates)
----------------------------------------------------------------
  * The EARNED rebate REDUCES VENDOR AP -- a credit note against the vendor (you
    owe them less). Tally: CREDIT the vendor ledger, DEBIT a
    "Rebates Receivable / Discount Received" head.
  * MANUAL post first (auto_post=false). NO TASKMASTER auto-posting in this PR.

Money convention
----------------
Everything is integer paise. The spend basis comes from accepted purchase
invoices (persisted as ``vendor_bills`` with ``doc_type == "PURCHASE_INVOICE"``)
whose money fields are stored in RUPEE floats; the router converts to paise once
at the boundary (``non_adapt.rupees_to_paise``) and passes paise in here.

A TIER is ``{min_spend_paise, rebate_pct?, rebate_flat_paise?, cap_paise?}``.
Exactly one of ``rebate_pct`` / ``rebate_flat_paise`` drives the earn; an
optional ``cap_paise`` clamps the result. Tiers must be a strictly-increasing
ladder on ``min_spend_paise`` (guarded -- a malformed / duplicate / decreasing
ladder RAISES so a misconfigured agreement can never silently mis-pay).
"""

from __future__ import annotations

from typing import Any, Optional


class RebateConfigError(ValueError):
    """A malformed tier ladder (non-monotonic, duplicate, or no earn rule)."""


# ---------------------------------------------------------------------------
# Coercion helpers (defensive -- a garbage field reads as 0, never raises)
# ---------------------------------------------------------------------------


def _int_paise(v: Any) -> int:
    """Coerce to a non-negative integer paise value. Junk -> 0."""
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def _parse_date(s: Any):
    """Tolerant ISO parse to a date. None on junk."""
    from datetime import datetime, date

    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    if not s or not isinstance(s, str):
        return None
    txt = s.strip()
    if not txt:
        return None
    try:
        return date.fromisoformat(txt[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 1. Period spend (pure)
# ---------------------------------------------------------------------------


# A bill counts toward rebate spend unless it is voided/cancelled/reversed.
_EXCLUDED_BILL_STATUSES = {
    "VOID",
    "VOIDED",
    "CANCELLED",
    "CANCELED",
    "REVERSED",
    "DELETED",
}


def compute_period_spend(invoices, vendor_id, period_start, period_end) -> int:
    """Sum eligible accepted purchase-invoice spend (integer paise) for one
    vendor over the half-open window [period_start, period_end).

    Pure. ``invoices`` is a list of dicts each carrying:
      - vendor_id
      - bill_date / invoice_date (ISO 'YYYY-MM-DD')
      - taxable_amount_paise  (the pre-tax spend basis, already in paise)
      - status (optional; voided/cancelled bills are excluded)
      - doc_type (optional; only PURCHASE_INVOICE counts when present)

    The window is HALF-OPEN: a bill dated exactly on ``period_end`` belongs to
    the NEXT period, never double-counted.
    """
    start = _parse_date(period_start)
    end = _parse_date(period_end)
    if start is None or end is None or start > end:
        return 0

    total = 0
    for inv in invoices or []:
        if not isinstance(inv, dict):
            continue
        if inv.get("vendor_id") != vendor_id:
            continue
        # Only accepted purchase invoices form the rebate basis. A row that
        # declares a doc_type must be a PURCHASE_INVOICE; a legacy row with no
        # doc_type is accepted (the caller already scoped the query to bills).
        doc_type = inv.get("doc_type")
        if doc_type is not None and doc_type != "PURCHASE_INVOICE":
            continue
        status = str(inv.get("status") or "").strip().upper()
        if status in _EXCLUDED_BILL_STATUSES:
            continue
        bdate = _parse_date(inv.get("bill_date") or inv.get("invoice_date"))
        if bdate is None:
            continue
        # half-open [start, end)
        if bdate < start or bdate >= end:
            continue
        total += _int_paise(inv.get("taxable_amount_paise"))
    return total


# ---------------------------------------------------------------------------
# 2. Tier resolution (pure) + monotonicity guard
# ---------------------------------------------------------------------------


def _validate_tiers(tiers) -> list:
    """Return tiers sorted ascending by min_spend_paise after asserting the
    ladder is well-formed. Raises RebateConfigError on a malformed ladder.

    A valid ladder:
      * is a non-empty list of dicts
      * every min_spend_paise is a non-negative integer
      * min_spend_paise values are STRICTLY INCREASING (no duplicates, no
        decrease) -- otherwise tier resolution would be ambiguous
      * each tier carries exactly one earn rule: rebate_pct OR rebate_flat_paise
    """
    if not isinstance(tiers, (list, tuple)) or not tiers:
        raise RebateConfigError("tiers must be a non-empty list")

    norm = []
    for t in tiers:
        if not isinstance(t, dict):
            raise RebateConfigError("each tier must be a dict")
        if "min_spend_paise" not in t:
            raise RebateConfigError("tier missing min_spend_paise")
        try:
            ms = int(t["min_spend_paise"])
        except (TypeError, ValueError) as exc:
            raise RebateConfigError("min_spend_paise must be an integer") from exc
        if ms < 0:
            raise RebateConfigError("min_spend_paise must be >= 0")
        has_pct = t.get("rebate_pct") is not None
        has_flat = t.get("rebate_flat_paise") is not None
        if has_pct == has_flat:
            raise RebateConfigError(
                "each tier needs exactly one of rebate_pct / rebate_flat_paise"
            )
        if has_pct:
            try:
                pct = float(t["rebate_pct"])
            except (TypeError, ValueError) as exc:
                raise RebateConfigError("rebate_pct must be numeric") from exc
            if pct < 0 or pct > 100:
                raise RebateConfigError("rebate_pct must be within 0..100")
        norm.append((ms, t))

    norm.sort(key=lambda x: x[0])
    prev = None
    for ms, _t in norm:
        if prev is not None and ms <= prev:
            # duplicate or decreasing min_spend -> ambiguous ladder
            raise RebateConfigError(
                "tier min_spend_paise must be strictly increasing (no duplicates)"
            )
        prev = ms
    return [t for _ms, t in norm]


def resolve_tier(spend_paise, tiers) -> Optional[dict]:
    """Return the HIGHEST tier whose min_spend_paise <= spend_paise, or None if
    no tier clears. Raises RebateConfigError on a malformed ladder."""
    ordered = _validate_tiers(tiers)
    spend = _int_paise(spend_paise)
    chosen = None
    for t in ordered:  # ascending -> last match is the highest cleared tier
        if int(t["min_spend_paise"]) <= spend:
            chosen = t
        else:
            break
    return chosen


# ---------------------------------------------------------------------------
# 3. Rebate amount (pure, paise-exact)
# ---------------------------------------------------------------------------


def _round_half_up(numerator: int, denominator: int) -> int:
    """Integer round-half-up of numerator/denominator (both positive)."""
    if denominator <= 0:
        return 0
    return (numerator * 2 + denominator) // (2 * denominator)


def compute_rebate_paise(spend_paise, tier) -> int:
    """Paise-exact rebate for the resolved tier.

    * percentage:  round-half-up(spend_paise * pct / 100)  (integer paise)
    * flat:        the flat paise amount
    * optional cap_paise clamps the result: min(rebate, cap)
    * returns 0 if tier is None / falsy
    """
    if not tier or not isinstance(tier, dict):
        return 0
    spend = _int_paise(spend_paise)

    if tier.get("rebate_pct") is not None:
        try:
            pct = float(tier["rebate_pct"])
        except (TypeError, ValueError):
            return 0
        if pct <= 0 or spend <= 0:
            rebate = 0
        else:
            # spend(paise) * pct / 100, half-up, with all-integer arithmetic to
            # avoid binary-float drift: numerator = spend * pct_scaled.
            # pct may carry up to 2 dp (e.g. 1.25%); scale by 100.
            pct_scaled = int(round(pct * 100))  # 1.25% -> 125
            rebate = _round_half_up(
                spend * pct_scaled, 10000
            )  # /100 (pct) /100 (scale)
    else:
        rebate = _int_paise(tier.get("rebate_flat_paise"))

    cap = tier.get("cap_paise")
    if cap is not None:
        rebate = min(rebate, _int_paise(cap))
    return rebate if rebate > 0 else 0


# ---------------------------------------------------------------------------
# 4. One-call convenience (still pure) -- compute the full earn for a period
# ---------------------------------------------------------------------------


def compute_earn(invoices, vendor_id, period_start, period_end, tiers) -> dict:
    """Compute {spend_paise, tier, rebate_paise} for a vendor + period + ladder.

    Pure. Returns the resolved tier dict (or None) and the paise-exact rebate.
    Raises RebateConfigError on a malformed ladder (so a misconfigured agreement
    fails loudly at preview/post time rather than mis-paying)."""
    spend = compute_period_spend(invoices, vendor_id, period_start, period_end)
    tier = resolve_tier(spend, tiers)
    rebate = compute_rebate_paise(spend, tier)
    return {"spend_paise": spend, "tier": tier, "rebate_paise": rebate}


# ---------------------------------------------------------------------------
# DB engine -- agreement CRUD + manual period post (reduce vendor AP).
# Standalone Mongo: the double-post guard is a UNIQUE index on
# (agreement_id, period_start) backed by a guarded insert. Owner decision:
# the earned rebate REDUCES VENDOR AP via a credit-note doc (no bill_id) that
# ap_engine.build_aging nets off the vendor's payable; a Tally JV intent
# (credit vendor / debit Rebates-Receivable) is recorded, never live-dispatched.
# ---------------------------------------------------------------------------

import uuid
from datetime import datetime, timezone

COLLECTION_AGREEMENTS = "vendor_rebate_agreements"
COLLECTION_LEDGER = "vendor_rebate_ledger"
# The AP-reducing credit note MUST land in the collection every AP/aging reader
# consumes -- ap_engine.build_aging is fed `vendor_debit_notes` (finance._ap_rows,
# vendors aging endpoints). A volume rebate is, in AP terms, a debit-note-direction
# reduction of what we owe the vendor: a row with bill_id=None nets off net_payable.
# (Writing to a separate `vendor_credit_notes` collection would be invisible to AP
# and the rebate reduction would silently never happen -- adversarial P1.)
CREDIT_NOTES_COLLECTION = "vendor_debit_notes"
BILLS_COLLECTION = "vendor_bills"
PERIODS = ("MONTHLY", "QUARTERLY", "ANNUAL")


class RebateError(Exception):
    def __init__(self, message, status=400, code="rebate_error"):
        super().__init__(message)
        self.status = status
        self.code = code


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_db(db) -> None:
    if db is None:
        raise RebateError("rebate store unavailable", status=503, code="no_db")


def ensure_indexes(db) -> None:
    """Idempotent indexes. The ledger UNIQUE (agreement_id, period_start) is the
    double-post backstop. Fail-soft."""
    if db is None:
        return
    try:
        db.get_collection(COLLECTION_AGREEMENTS).create_index(
            [("vendor_id", 1), ("active", 1)]
        )
        db.get_collection(COLLECTION_LEDGER).create_index(
            [("agreement_id", 1), ("period_start", 1)], unique=True
        )
    except Exception:  # noqa: BLE001
        return


def create_agreement(db, payload, *, actor) -> dict:
    """Create a rebate agreement. Validates the tier ladder up front (loud) so a
    malformed ladder can never be saved and silently mis-pay later."""
    _require_db(db)
    payload = payload or {}
    vendor_id = str(payload.get("vendor_id") or "").strip()
    if not vendor_id:
        raise RebateError("vendor_id is required", status=422)
    period = str(payload.get("period") or "MONTHLY").strip().upper()
    if period not in PERIODS:
        raise RebateError("period must be MONTHLY, QUARTERLY or ANNUAL", status=422)
    try:
        tiers = _validate_tiers(payload.get("tiers"))
    except RebateConfigError as exc:
        raise RebateError(
            "invalid tier ladder: " + str(exc), status=422, code="bad_tiers"
        )
    aid = "VRA-" + uuid.uuid4().hex[:10].upper()
    now = _now_iso()
    doc = {
        "_id": aid,
        "agreement_id": aid,
        "vendor_id": vendor_id,
        "name": str(payload.get("name") or "").strip() or f"Rebate {vendor_id}",
        "period": period,
        "basis": "PURCHASE_INVOICE",
        "tiers": tiers,
        "active": bool(payload.get("active", True)),
        "auto_post": False,  # manual-post only in this phase (owner decision)
        "created_by": actor.get("user_id"),
        "created_at": now,
        "updated_at": now,
    }
    db.get_collection(COLLECTION_AGREEMENTS).insert_one(dict(doc))
    return doc


def list_agreements(db, vendor_id=None) -> list:
    if db is None:
        return []
    q = {}
    if vendor_id:
        q["vendor_id"] = vendor_id
    try:
        return list(db.get_collection(COLLECTION_AGREEMENTS).find(q))
    except Exception:  # noqa: BLE001
        return []


def get_agreement(db, agreement_id) -> Optional[dict]:
    if db is None:
        return None
    return db.get_collection(COLLECTION_AGREEMENTS).find_one(
        {"agreement_id": agreement_id}
    )


def update_agreement(db, agreement_id, payload, *, actor) -> dict:
    _require_db(db)
    coll = db.get_collection(COLLECTION_AGREEMENTS)
    existing = coll.find_one({"agreement_id": agreement_id})
    if existing is None:
        raise RebateError("agreement not found", status=404, code="not_found")
    set_fields = {"updated_by": actor.get("user_id"), "updated_at": _now_iso()}
    if payload.get("tiers") is not None:
        try:
            set_fields["tiers"] = _validate_tiers(payload.get("tiers"))
        except RebateConfigError as exc:
            raise RebateError(
                "invalid tier ladder: " + str(exc), status=422, code="bad_tiers"
            )
    for k in ("name", "period", "active"):
        if k in (payload or {}):
            set_fields[k] = payload[k]
    if "period" in set_fields and str(set_fields["period"]).upper() not in PERIODS:
        raise RebateError("period must be MONTHLY, QUARTERLY or ANNUAL", status=422)
    from pymongo import ReturnDocument

    return coll.find_one_and_update(
        {"agreement_id": agreement_id},
        {"$set": set_fields},
        return_document=ReturnDocument.AFTER,
    )


def _accepted_invoices_for(db, vendor_id) -> list:
    """Vendor's accepted purchase invoices, shaped for compute_period_spend
    (taxable_amount_paise injected from the rupee taxable_amount field)."""
    if db is None:
        return []
    from .non_adapt import rupees_to_paise

    out = []
    try:
        cur = db.get_collection(BILLS_COLLECTION).find({"vendor_id": vendor_id})
    except Exception:  # noqa: BLE001
        return []
    for b in cur:
        taxable = b.get("taxable_amount")
        if taxable is None:
            taxable = b.get("taxable")
        if taxable is None:
            taxable = b.get("total_amount")
        out.append(
            {
                "vendor_id": b.get("vendor_id"),
                "doc_type": b.get("doc_type"),
                "status": b.get("status"),
                "bill_date": b.get("bill_date"),
                "invoice_date": b.get("invoice_date"),
                "taxable_amount_paise": rupees_to_paise(taxable),
            }
        )
    return out


def preview(db, agreement_id, period_start, period_end) -> dict:
    """Compute {spend_paise, tier, rebate_paise} for a period. NO write."""
    _require_db(db)
    ag = get_agreement(db, agreement_id)
    if ag is None:
        raise RebateError("agreement not found", status=404, code="not_found")
    invoices = _accepted_invoices_for(db, ag.get("vendor_id"))
    try:
        earn = compute_earn(
            invoices, ag.get("vendor_id"), period_start, period_end, ag.get("tiers")
        )
    except RebateConfigError as exc:
        raise RebateError(
            "invalid tier ladder: " + str(exc), status=422, code="bad_tiers"
        )
    return {
        **earn,
        "vendor_id": ag.get("vendor_id"),
        "agreement_id": agreement_id,
        "period_start": period_start,
        "period_end": period_end,
    }


def post(
    db, agreement_id, period_start, period_end, *, actor, period_lock_check=None
) -> dict:
    """Post a period's earned rebate ONCE. The (agreement_id, period_start)
    unique index + a guarded insert make a double-post impossible (the 2nd
    insert raises DuplicateKey -> 409). On the winning post: write the POSTED
    ledger row, a credit_note_number, a vendor CREDIT-NOTE doc (no bill_id, so
    ap_engine nets it off the vendor's payable -> AP goes DOWN), and the Tally
    JV intent (credit vendor / debit Rebates-Receivable; not live-dispatched)."""
    _require_db(db)
    ag = get_agreement(db, agreement_id)
    if ag is None:
        raise RebateError("agreement not found", status=404, code="not_found")
    if not ag.get("active", True):
        raise RebateError("agreement is inactive", status=409, code="inactive")
    if period_lock_check is not None:
        # Raises HTTPException-like on a locked period; the router passes finance.check_period_locked.
        period_lock_check(period_start)
    earn = preview(db, agreement_id, period_start, period_end)
    rebate_paise = int(earn.get("rebate_paise") or 0)
    rid = "VRB-" + uuid.uuid4().hex[:10].upper()
    cnn = "RCN-" + uuid.uuid4().hex[:8].upper()
    now = _now_iso()
    vendor_id = ag.get("vendor_id")
    ledger = {
        "_id": rid,
        "rebate_id": rid,
        "agreement_id": agreement_id,
        "vendor_id": vendor_id,
        "period_start": period_start,
        "period_end": period_end,
        "spend_paise": int(earn.get("spend_paise") or 0),
        "tier": earn.get("tier"),
        "rebate_paise": rebate_paise,
        "status": "POSTED",
        "credit_note_number": cnn,
        "ap_reduction_paise": rebate_paise,  # reduces what we owe the vendor
        "tally_voucher": {
            "type": "JOURNAL",
            "narration": f"Volume rebate {cnn} for {vendor_id}",
            "entries": [
                {
                    "ledger": "Sundry Creditors / " + str(vendor_id),
                    "credit_paise": rebate_paise,
                },
                {"ledger": "Rebates Receivable", "debit_paise": rebate_paise},
            ],
            "dispatched": False,  # recorded intent; NEXUS Tally export is not live here
        },
        "posted_by": actor.get("user_id"),
        "posted_at": now,
        "created_at": now,
    }
    from pymongo.errors import DuplicateKeyError

    try:
        db.get_collection(COLLECTION_LEDGER).insert_one(dict(ledger))
    except DuplicateKeyError:
        existing = db.get_collection(COLLECTION_LEDGER).find_one(
            {"agreement_id": agreement_id, "period_start": period_start}
        )
        raise RebateError(
            "this period is already posted for this agreement",
            status=409,
            code="already_posted",
        ) from None
    # The AP-reducing credit note: no bill_id -> build_aging treats it as an
    # on-account credit that lowers net_payable for the vendor. Amount in RUPEES
    # to match the aging inputs (bills/payments are rupee floats there).
    if rebate_paise > 0:
        try:
            db.get_collection(CREDIT_NOTES_COLLECTION).insert_one(
                {
                    "_id": cnn,
                    "credit_note_number": cnn,
                    "vendor_id": vendor_id,
                    "amount": round(rebate_paise / 100.0, 2),
                    "amount_paise": rebate_paise,
                    "bill_id": None,
                    "source": "VOLUME_REBATE",
                    "rebate_id": rid,
                    "created_by": actor.get("user_id"),
                    "created_at": now,
                }
            )
        except Exception:  # noqa: BLE001
            pass  # ledger is the system-of-record; credit-note mirror is best-effort
    return ledger


def list_ledger(db, vendor_id=None) -> list:
    if db is None:
        return []
    q = {}
    if vendor_id:
        q["vendor_id"] = vendor_id
    try:
        return list(db.get_collection(COLLECTION_LEDGER).find(q))
    except Exception:  # noqa: BLE001
        return []


def get_ledger(db, rebate_id) -> Optional[dict]:
    if db is None:
        return None
    return db.get_collection(COLLECTION_LEDGER).find_one({"rebate_id": rebate_id})
