"""Feature #16 -- Bank / Cash / POS reconciliation engine.

Reconciles three money trails per store and date-range:
  (1) CASH      -- expected deposit (the cash physically counted + locked at the
                   #23 till Z-read close) vs the actual bank deposit line.
  (2) POS digital -- card/UPI/wallet on orders (via E5 reconcile_window) vs the
                   gateway/bank SETTLEMENT credited, net of the MDR fee.
  (3) BANK statement lines (manual entry or CSV import) matched against each
                   expected item.

Output of a reconciliation run:
  - matched               : expected items matched to a bank line (within tolerance,
                            MDR fee recorded explicitly for digital).
  - unmatched_in_books    : expected items with no bank line.
  - unmatched_in_bank     : bank lines with no expected item.
  - variance              : paisa-exact totals (expected vs bank), MDR/fee variance explicit.

READ-ONLY against orders + till-close docs (no POS-capture change). All writes land on the
new ``bank_reconciliations`` + ``bank_statement_lines`` collections.

Reuses E5 (tender_reconciliation.reconcile_window) + #23 (eod_tally till-close Z-read) -- no fork.
Standalone Mongo single-doc atomic; store-scoped; integer paise; IST windows.
"""
from __future__ import annotations

import uuid
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime
from typing import Any, Dict, List, Optional

# Collections owned by this feature (all writes land here; orders/till read-only).
RECON_COLLECTION = "bank_reconciliations"
BANK_LINES_COLLECTION = "bank_statement_lines"

# Run lifecycle. OPEN -> LOCKED (soft, transparent) -> SIGNED_OFF. Each transition
# is a single guarded find_one_and_update (mirrors eod_tally / E5).
STATUS_OPEN = "OPEN"
STATUS_LOCKED = "LOCKED"
STATUS_SIGNED_OFF = "SIGNED_OFF"

# Trail kinds.
KIND_CASH = "CASH"
KIND_DIGITAL = "POS_DIGITAL"

# till_sessions states whose counted cash is a real, deposit-bound figure.
_TILL_CLOSED_STATES = ("LOCKED", "BLIND_SUBMITTED")


# --------------------------------------------------------------------------- #
# Integer-paise helpers (money is ALWAYS integer paise inside this module).
# --------------------------------------------------------------------------- #
def to_paise(rupees: Any) -> int:
    """Convert a rupee amount (int/float/str/Decimal) to integer paise, rounding half-up.

    Pure + total: None/blank/garbage -> 0 (never raises) so a bad CSV cell can't
    crash an import. A value already in paise should NOT be passed here.
    """
    if rupees is None or rupees == "":
        return 0
    try:
        d = Decimal(str(rupees))
    except Exception:  # noqa: BLE001
        return 0
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def paise_to_rupees(paise: int) -> float:
    """Convert integer paise to a 2-decimal rupee float (display only)."""
    try:
        return round(int(paise) / 100.0, 2)
    except Exception:  # noqa: BLE001
        return 0.0


def within_tolerance(a_paise: int, b_paise: int, tolerance_paise: int) -> bool:
    """True if abs(a-b) <= tolerance (all integer paise)."""
    return abs(int(a_paise) - int(b_paise)) <= int(tolerance_paise or 0)


def _mdr_fee_paise(gross_paise: int, mdr_bps: int) -> int:
    """MDR fee on a gross digital amount, in integer paise (bps = 1/100 of a percent).

    e.g. mdr_bps=200 -> 2.00%. Rounded half-up. A bank settlement is the gross
    LESS this fee, so the expected net = gross - fee.
    """
    if not mdr_bps:
        return 0
    fee = (Decimal(int(gross_paise)) * Decimal(int(mdr_bps)) / Decimal(10000)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    return int(fee)


def _audit(
    action: str,
    *,
    entity_id: str,
    actor: Optional[Dict[str, Any]],
    detail: Optional[Dict[str, Any]] = None,
    store_id: Optional[str] = None,
    severity: str = "INFO",
) -> None:
    """One append-only hash-chained audit row via AuditRepository.create. Fail-soft --
    an audit failure never undoes the business write that triggered it. Mirrors
    eod_tally._audit."""
    try:
        from api.dependencies import get_audit_repository

        repo = get_audit_repository()
        if repo is None:
            return
        repo.create(
            {
                "action": action,
                "entity_type": "bank_reconciliation",
                "entity_id": entity_id,
                "store_id": store_id or (actor or {}).get("active_store_id"),
                "user_id": (actor or {}).get("user_id"),
                "user_name": (actor or {}).get("full_name") or (actor or {}).get("username"),
                "severity": severity,
                "source": "bank_reconciliation",
                "detail": detail or {},
            }
        )
    except Exception:  # noqa: BLE001
        return


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #
class BankReconciliationEngine:
    """Pure reconciliation logic + persistence orchestration for Feature #16.

    Accessors are injected so tests can monkeypatch every DB touch.
    """

    def __init__(self, db: Any) -> None:
        self.db = db

    # ---- trail builders (READ-ONLY) -------------------------------------- #
    def build_cash_expected(
        self, store_id: str, start_iso: str, end_iso: str
    ) -> List[Dict[str, Any]]:
        """Expected cash deposits per store/day from #23 till Z-read close docs.

        The bank-bound figure is the cash PHYSICALLY COUNTED at close
        (``blind_count_paisa``) -- that is what gets deposited. (The till's own
        counted-vs-system variance is #23's concern, not the bank trail.)
        Read-only over ``till_sessions``; DB absent -> [].
        """
        if self.db is None:
            return []
        try:
            cur = self.db.get_collection("till_sessions").find(
                {
                    "store_id": store_id,
                    "session_date": {"$gte": start_iso, "$lte": end_iso},
                    "status": {"$in": list(_TILL_CLOSED_STATES)},
                }
            )
            out: List[Dict[str, Any]] = []
            for s in cur:
                counted = s.get("blind_count_paisa")
                if counted is None:
                    counted = s.get("counted_cash_paisa", s.get("expected_cash_paisa", 0))
                out.append(
                    {
                        "kind": KIND_CASH,
                        "ref_date": s.get("session_date"),
                        "tender": "CASH",
                        "expected_paise": int(counted or 0),
                        "source_id": s.get("session_id") or s.get("_id"),
                    }
                )
            return out
        except Exception:  # noqa: BLE001
            return []

    def build_pos_digital_expected(
        self, store_id: str, start_iso: str, end_iso: str
    ) -> List[Dict[str, Any]]:
        """Expected digital (card/UPI/wallet) settlements per E5 reconcile_window.

        One expected item per non-cash tender; gross = net (collected - refunded)
        the gateway will settle (less MDR, handled at match time). Read-only.
        """
        try:
            from api.services import tender_reconciliation as tr

            recon = tr.reconcile_window(self.db, store_id, start_iso, end_iso)
        except Exception:  # noqa: BLE001
            return []
        out: List[Dict[str, Any]] = []
        for tender, agg in (recon.get("by_mode") or {}).items():
            if str(tender).upper() == "CASH":
                continue  # cash is the #23 trail, not a gateway settlement
            net_rupees = agg.get("net", 0.0)
            gross_paise = to_paise(net_rupees)
            if gross_paise == 0:
                continue
            out.append(
                {
                    "kind": KIND_DIGITAL,
                    "ref_date": end_iso,
                    "tender": str(tender).upper(),
                    "expected_paise": gross_paise,
                    "count": agg.get("count", 0),
                }
            )
        return out

    def load_bank_lines(
        self, store_id: str, start_iso: str, end_iso: str
    ) -> List[Dict[str, Any]]:
        """Bank statement lines (manual / CSV import) in window for this store."""
        if self.db is None:
            return []
        try:
            cur = self.db.get_collection(BANK_LINES_COLLECTION).find(
                {
                    "store_id": store_id,
                    "value_date": {"$gte": start_iso, "$lte": end_iso},
                }
            )
            return [dict(d) for d in cur]
        except Exception:  # noqa: BLE001
            return []

    # ---- matching -------------------------------------------------------- #
    def match_trail(
        self,
        expected: List[Dict[str, Any]],
        bank_lines: List[Dict[str, Any]],
        tolerance_paise: int,
        mdr_bps: int = 0,
    ) -> Dict[str, Any]:
        """Match expected items against bank lines -> matched / unmatched / variance.

        Greedy first-fit per (kind, tender). For a DIGITAL item the bank credit is
        the gross LESS the MDR fee, so we match on expected_net = gross - fee and
        record the fee explicitly (a fee gap is NOT a true variance). Each bank
        line is consumed at most once. All money integer paise.
        """
        tol = int(tolerance_paise or 0)
        # Index unconsumed bank lines; preserve order for determinism.
        remaining = list(range(len(bank_lines)))
        matched: List[Dict[str, Any]] = []
        unmatched_books: List[Dict[str, Any]] = []

        total_expected = 0
        total_bank_matched = 0
        total_variance = 0
        total_fee = 0

        for item in expected:
            gross = int(item.get("expected_paise", 0))
            total_expected += gross
            fee = _mdr_fee_paise(gross, mdr_bps) if item.get("kind") == KIND_DIGITAL else 0
            expected_net = gross - fee

            hit_idx = None
            for ri in remaining:
                bl = bank_lines[ri]
                # Only match like-for-like trail/tender when the bank line is tagged.
                bl_kind = bl.get("kind")
                if bl_kind and bl_kind != item.get("kind"):
                    continue
                bl_tender = (bl.get("tender") or "").upper()
                if bl_tender and item.get("tender") and bl_tender != str(item["tender"]).upper():
                    continue
                if within_tolerance(expected_net, int(bl.get("amount_paise", 0)), tol):
                    hit_idx = ri
                    break

            if hit_idx is not None:
                bl = bank_lines[hit_idx]
                remaining.remove(hit_idx)
                bank_amt = int(bl.get("amount_paise", 0))
                variance = bank_amt - expected_net  # within tolerance by construction
                total_bank_matched += bank_amt
                total_variance += variance
                total_fee += fee
                matched.append(
                    {
                        "tender": item.get("tender"),
                        "kind": item.get("kind"),
                        "ref_date": item.get("ref_date"),
                        "expected_gross_paise": gross,
                        "mdr_fee_paise": fee,
                        "expected_net_paise": expected_net,
                        "bank_amount_paise": bank_amt,
                        "variance_paise": variance,
                        "bank_line_id": bl.get("line_id") or bl.get("_id"),
                    }
                )
            else:
                unmatched_books.append(
                    {
                        "tender": item.get("tender"),
                        "kind": item.get("kind"),
                        "ref_date": item.get("ref_date"),
                        "expected_gross_paise": gross,
                        "mdr_fee_paise": fee,
                        "expected_net_paise": expected_net,
                    }
                )

        unmatched_bank = [
            {
                "bank_line_id": bank_lines[ri].get("line_id") or bank_lines[ri].get("_id"),
                "tender": bank_lines[ri].get("tender"),
                "kind": bank_lines[ri].get("kind"),
                "value_date": bank_lines[ri].get("value_date"),
                "amount_paise": int(bank_lines[ri].get("amount_paise", 0)),
            }
            for ri in remaining
        ]
        total_unmatched_bank = sum(b["amount_paise"] for b in unmatched_bank)

        return {
            "matched": matched,
            "unmatched_in_books": unmatched_books,
            "unmatched_in_bank": unmatched_bank,
            "totals": {
                "expected_gross_paise": total_expected,
                "bank_matched_paise": total_bank_matched,
                "mdr_fee_paise": total_fee,
                "variance_paise": total_variance,
                "unmatched_in_books_paise": sum(
                    u["expected_net_paise"] for u in unmatched_books
                ),
                "unmatched_in_bank_paise": total_unmatched_bank,
            },
            "reconciled": not unmatched_books and not unmatched_bank and total_variance == 0,
        }

    # ---- run orchestration ----------------------------------------------- #
    def run_reconciliation(
        self,
        store_id: str,
        start_iso: str,
        end_iso: str,
        tolerance_paise: int,
        mdr_bps: int,
        actor: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build all three trails, match, persist a reconciliation run, return the run doc."""
        bank_lines = self.load_bank_lines(store_id, start_iso, end_iso)
        cash_expected = self.build_cash_expected(store_id, start_iso, end_iso)
        digital_expected = self.build_pos_digital_expected(store_id, start_iso, end_iso)

        cash_bank = [b for b in bank_lines if (b.get("kind") or KIND_CASH) == KIND_CASH]
        digital_bank = [b for b in bank_lines if b.get("kind") == KIND_DIGITAL]

        cash_result = self.match_trail(cash_expected, cash_bank, tolerance_paise, mdr_bps=0)
        digital_result = self.match_trail(
            digital_expected, digital_bank, tolerance_paise, mdr_bps=mdr_bps
        )

        run_id = f"BR-{uuid.uuid4().hex[:12].upper()}"
        now = datetime.utcnow()
        doc = {
            "_id": run_id,
            "run_id": run_id,
            "store_id": store_id,
            "window_start": start_iso,
            "window_end": end_iso,
            "tolerance_paise": int(tolerance_paise or 0),
            "mdr_bps": int(mdr_bps or 0),
            "status": STATUS_OPEN,
            "cash": cash_result,
            "digital": digital_result,
            "reconciled": cash_result["reconciled"] and digital_result["reconciled"],
            "created_at": now,
            "created_by": (actor or {}).get("user_id"),
        }
        if self.db is not None:
            try:
                self.db.get_collection(RECON_COLLECTION).insert_one(dict(doc))
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"write_failed:{exc}", "http": 500}
        _audit(
            "bank_recon.run",
            entity_id=run_id,
            actor=actor,
            store_id=store_id,
            detail={
                "window": [start_iso, end_iso],
                "reconciled": doc["reconciled"],
                "cash_variance_paise": cash_result["totals"]["variance_paise"],
                "digital_variance_paise": digital_result["totals"]["variance_paise"],
            },
        )
        return {"ok": True, "run": doc}

    # ---- soft-lock + sign-off (single guarded find_one_and_update) ------- #
    def acquire_lock(self, run_id: str, actor: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Atomic soft-lock OPEN -> LOCKED: one concurrent caller wins, the other
        gets None (guarded find_one_and_update on status)."""
        if self.db is None:
            return None
        from pymongo import ReturnDocument

        try:
            updated = self.db.get_collection(RECON_COLLECTION).find_one_and_update(
                {"_id": run_id, "status": STATUS_OPEN},
                {
                    "$set": {
                        "status": STATUS_LOCKED,
                        "locked_at": datetime.utcnow(),
                        "locked_by": (actor or {}).get("user_id"),
                    }
                },
                return_document=ReturnDocument.AFTER,
            )
        except Exception:  # noqa: BLE001
            return None
        if updated is not None:
            _audit(
                "bank_recon.lock",
                entity_id=run_id,
                actor=actor,
                store_id=updated.get("store_id"),
            )
        return updated

    def sign_off(self, run_id: str, store_id: str, actor: Dict[str, Any]) -> Dict[str, Any]:
        """Manager/admin sign-off on a reconciliation run (atomic + audited).

        Guarded transition (OPEN|LOCKED) -> SIGNED_OFF, pinned to the run's store.
        Two concurrent sign-offs: exactly one wins; the loser gets already_signed.
        """
        if self.db is None:
            return {"ok": False, "error": "no_db", "http": 503}
        from pymongo import ReturnDocument

        try:
            updated = self.db.get_collection(RECON_COLLECTION).find_one_and_update(
                {
                    "_id": run_id,
                    "store_id": store_id,
                    "status": {"$in": [STATUS_OPEN, STATUS_LOCKED]},
                },
                {
                    "$set": {
                        "status": STATUS_SIGNED_OFF,
                        "signed_off_at": datetime.utcnow(),
                        "signed_off_by": (actor or {}).get("user_id"),
                    }
                },
                return_document=ReturnDocument.AFTER,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"signoff_failed:{exc}", "http": 500}
        if updated is None:
            # Either not found / wrong store, or already signed off.
            existing = None
            try:
                existing = self.db.get_collection(RECON_COLLECTION).find_one({"_id": run_id})
            except Exception:  # noqa: BLE001
                existing = None
            if existing is None or existing.get("store_id") != store_id:
                return {"ok": False, "error": "not_found", "http": 404}
            return {"ok": False, "error": "already_signed_off", "http": 409}
        _audit(
            "bank_recon.sign_off",
            entity_id=run_id,
            actor=actor,
            store_id=store_id,
            severity="INFO",
        )
        return {"ok": True, "run": updated}

    # ---- bank line ingestion --------------------------------------------- #
    def add_bank_line(
        self, store_id: str, line: Dict[str, Any], actor: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Insert a single bank statement line (manual entry / CSV row).

        ``line`` carries value_date (ISO), amount (rupees) OR amount_paise, kind
        (CASH|POS_DIGITAL), optional tender + reference. Amount stored as integer paise.
        """
        if self.db is None:
            return {"ok": False, "error": "no_db", "http": 503}
        amount_paise = line.get("amount_paise")
        if amount_paise is None:
            amount_paise = to_paise(line.get("amount"))
        line_id = f"BL-{uuid.uuid4().hex[:12].upper()}"
        doc = {
            "_id": line_id,
            "line_id": line_id,
            "store_id": store_id,
            "value_date": line.get("value_date"),
            "amount_paise": int(amount_paise or 0),
            "kind": (line.get("kind") or KIND_CASH),
            "tender": (line.get("tender") or "").upper() or None,
            "reference": line.get("reference"),
            "created_at": datetime.utcnow(),
            "created_by": (actor or {}).get("user_id"),
        }
        try:
            self.db.get_collection(BANK_LINES_COLLECTION).insert_one(dict(doc))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"write_failed:{exc}", "http": 500}
        _audit("bank_recon.add_line", entity_id=line_id, actor=actor, store_id=store_id)
        return {"ok": True, "line": doc}


def ensure_indexes(db) -> None:
    """Idempotent indexes for the two new collections. Fail-soft."""
    if db is None:
        return
    try:
        db.get_collection(RECON_COLLECTION).create_index([("store_id", 1), ("window_start", 1)])
        db.get_collection(BANK_LINES_COLLECTION).create_index(
            [("store_id", 1), ("value_date", 1)]
        )
    except Exception:  # noqa: BLE001
        return
