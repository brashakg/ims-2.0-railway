"""Feature #16 -- Bank / Cash / POS reconciliation engine.

Reconciles three money trails per store and date-range:
  (1) CASH      -- expected deposit (from #23 till Z-read close) vs actual bank deposit line.
  (2) POS digital -- card/UPI/wallet on orders (via E5 reconcile_window) vs gateway/bank
                     SETTLEMENT net of MDR fee.
  (3) BANK statement lines (manual entry or CSV import) matched against each expected item.

Output of a reconciliation run:
  - matched               : expected items matched to a bank line (paisa-exact or within tolerance).
  - unmatched_in_books    : expected items with no bank line.
  - unmatched_in_bank     : bank lines with no expected item.
  - variance              : paisa-exact totals (expected vs bank), MDR/fee variance explicit.

READ-ONLY against orders + till-close docs (no POS-capture change). All writes land on the new
``bank_reconciliations`` + ``bank_statement_lines`` collections.

Reuses E5 (tender_reconciliation.reconcile_window) + #23 (eod_tally till-close Z-read) -- no fork.
Standalone Mongo single-doc atomic; store-scoped; integer paise; IST windows.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


# --------------------------------------------------------------------------- #
# Integer-paise helpers (money is ALWAYS integer paise inside this module).
# --------------------------------------------------------------------------- #
def to_paise(rupees: Any) -> int:
    """Convert a rupee amount (int/float/str/Decimal) to integer paise, rounding half-up."""
    raise NotImplementedError


def paise_to_rupees(paise: int) -> float:
    """Convert integer paise to a 2-decimal rupee float (display only)."""
    raise NotImplementedError


def within_tolerance(a_paise: int, b_paise: int, tolerance_paise: int) -> bool:
    """True if abs(a-b) <= tolerance (all integer paise)."""
    raise NotImplementedError


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
        """Expected cash deposits per store/day from #23 till Z-read close docs."""
        raise NotImplementedError

    def build_pos_digital_expected(
        self, store_id: str, start_iso: str, end_iso: str
    ) -> List[Dict[str, Any]]:
        """Expected digital (card/UPI/wallet) per E5 reconcile_window over orders."""
        raise NotImplementedError

    def load_bank_lines(
        self, store_id: str, start_iso: str, end_iso: str
    ) -> List[Dict[str, Any]]:
        """Bank statement lines (manual / CSV import) in window for this store."""
        raise NotImplementedError

    # ---- matching -------------------------------------------------------- #
    def match_trail(
        self,
        expected: List[Dict[str, Any]],
        bank_lines: List[Dict[str, Any]],
        tolerance_paise: int,
        mdr_bps: int = 0,
    ) -> Dict[str, Any]:
        """Match expected items against bank lines -> matched / unmatched / variance.

        For digital trails, ``mdr_bps`` lets a bank line that is short by the MDR fee
        still match (fee variance recorded explicitly, not flagged as a true variance).
        """
        raise NotImplementedError

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
        raise NotImplementedError

    # ---- soft-lock + sign-off (single guarded find_one_and_update) ------- #
    def acquire_lock(self, run_id: str, actor: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Atomic soft-lock: one concurrent caller wins (guarded find_one_and_update)."""
        raise NotImplementedError

    def sign_off(self, run_id: str, store_id: str, actor: Dict[str, Any]) -> Dict[str, Any]:
        """Manager/admin sign-off on a reconciliation run (atomic + audited)."""
        raise NotImplementedError

    # ---- bank line ingestion --------------------------------------------- #
    def add_bank_line(
        self, store_id: str, line: Dict[str, Any], actor: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Insert a single bank statement line (manual entry / CSV row)."""
        raise NotImplementedError
