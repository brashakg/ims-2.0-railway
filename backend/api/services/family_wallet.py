"""F49 - Family/household loyalty points wallet (pure-ish service layer).

A household groups up to ``loyalty.pool_max_members`` (E2 policy, default 7)
customers into one shared loyalty-points pool. The pool BALANCE lives in a
money_guard FAMILY_WALLET account doc keyed on the household_id -- every
credit/debit goes through money_guard's guarded find_one_and_update (floor in
the filter), never read-modify-write. Pool redemption is OTP-gated via the
existing reminder_rail send/verify_pool_redemption_otp slice and mints a
store-credit voucher through the canonical vouchers helper.

UNIT NOTE: loyalty points are POINTS, not paise. The FAMILY_WALLET
money_guard account stores integer POINTS as its amount unit. Do NOT convert
to rupees/paise here; the voucher minted at redemption carries the rupee
conversion.

Membership invariants:
- A customer belongs to at most ONE active household (pre-checked by query;
  see race caveat in create_household/add_member docstrings).
- max-members is enforced IN the find_one_and_update filter ($size < max),
  so two concurrent adds at capacity-1 produce exactly one winner.
- The primary member (member_customer_ids[0]) cannot be removed.

Chain-wide by owner decision: household lookup + pool redemption are NOT
store-scoped (mirrors chain-wide customer-lookup + voucher-redeem).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

HOUSEHOLDS_COLLECTION = "households"


def ensure_indexes(db) -> None:
    """Create indexes for the households collection (fail-soft)."""
    raise NotImplementedError


def create_household(db, *, primary_customer_id: str, store_id: Optional[str],
                     actor: Dict[str, Any]) -> Dict[str, Any]:
    """Create a household with the primary customer as member[0]."""
    raise NotImplementedError


def add_member(db, household_id: str, customer_id: str, *,
               actor: Dict[str, Any], max_members: int = 7) -> Dict[str, Any]:
    """Add a member via a single guarded find_one_and_update (max in filter)."""
    raise NotImplementedError


def remove_member(db, household_id: str, customer_id: str, *,
                  actor: Dict[str, Any]) -> Dict[str, Any]:
    """Remove a non-primary member via guarded $pull."""
    raise NotImplementedError


def get_household(db, household_id: str) -> Optional[Dict[str, Any]]:
    """Fetch one household by id (chain-wide, fail-soft)."""
    raise NotImplementedError


def get_household_by_customer(db, customer_id: str) -> Optional[Dict[str, Any]]:
    """Find the active household containing customer_id (chain-wide)."""
    raise NotImplementedError


def pool_balance(db, household_id: str) -> int:
    """Current pool balance in POINTS (money_guard account; 0 if absent)."""
    raise NotImplementedError


def pool_earn(db, household_id: str, points: int, *,
              source_order_id: Optional[str], actor: Dict[str, Any]) -> Dict[str, Any]:
    """Credit POINTS to the household pool (money_guard CREDIT, lazy account)."""
    raise NotImplementedError


def pool_redeem(db, household_id: str, points: int, *,
                redeeming_customer_id: str, otp_token: str,
                actor: Dict[str, Any]) -> Dict[str, Any]:
    """OTP-verified pool debit (floor in filter) + loyalty-txn audit row."""
    raise NotImplementedError
