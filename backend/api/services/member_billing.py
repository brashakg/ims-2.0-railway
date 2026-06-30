"""
IMS 2.0 - Bill-to-member resolution (Account / Member model)
============================================================
Council decision COUNCIL_ACCOUNT_BILLING_DECISION_2026-06-19 (LOCKED).

The data model is UNCHANGED: an Account is a `customers` doc and its Members are
entries in the nested `customers.patients[]` array. This module is the single,
pure, testable place that owns the "every order bills a MEMBER, never a bare
account" rule:

  * build_primary_member()   -- mint the canonical Primary member dict for an
                                account (B2C: from the account name/mobile).
  * ensure_primary_member()  -- guarantee an account doc has >=1 member, marking
                                a Primary; returns (primary_member, changed).
  * find_member()            -- look up a member by patient_id inside an account.
  * choose_primary_member()  -- pick the Primary/account-holder member from a
                                patients[] list (Self / is_primary first).

PHASE 1 (CORE) deliberately keeps loyalty + store credit at the ACCOUNT level --
this module only resolves WHICH member an order is billed to. It does NOT touch
pricing, GST, payment, or loyalty math.

No emojis (Windows cp1252 safe). No DB access here -- callers persist.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Tuple


def _patients_of(customer: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return the account's member list (always a list, never None)."""
    if not customer:
        return []
    raw = customer.get("patients")
    return list(raw) if isinstance(raw, list) else []


def _norm(v: Any) -> str:
    return str(v).strip() if v is not None else ""


def build_primary_member(
    *,
    name: str,
    mobile: Optional[str] = None,
    patient_id: Optional[str] = None,
    relation: str = "Self",
) -> Dict[str, Any]:
    """Build the canonical Primary member dict for an account.

    Shape mirrors the existing patients[] entries created by customers.py
    (patient_id / name / mobile / relation) plus the new is_primary flag the
    council added. A B2B 'department' Primary (P4) reuses this same shape; the
    relation just differs -- P1 only needs the B2C self-member.
    """
    return {
        "patient_id": patient_id or str(uuid.uuid4()),
        "name": _norm(name) or "Primary",
        "mobile": _norm(mobile) or None,
        "relation": relation or "Self",
        "is_primary": True,
    }


def choose_primary_member(
    patients: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Pick the account-holder / Primary member from a members list.

    Priority: an explicit is_primary member -> a relation == 'Self' member ->
    the first member. Returns None when the list is empty.
    """
    if not patients:
        return None
    for p in patients:
        if p and p.get("is_primary"):
            return p
    for p in patients:
        if p and _norm(p.get("relation")).lower() == "self":
            return p
    return patients[0]


def ensure_primary_member(
    customer: Dict[str, Any],
) -> Tuple[Dict[str, Any], bool]:
    """Guarantee `customer` has at least one member and that one is the Primary.

    MUTATES the passed-in `customer` dict in place (sets customer["patients"]
    and customer["primary_patient_id"]) and returns (primary_member, changed)
    where `changed` is True when the caller must persist the doc.

    INVARIANT enforced (council data-model rule 1): every account has >=1 member;
    the account holder is a REAL member row (is_primary=true) -- NOT the legacy
    patient_id==customer_id special case. When patients[] is empty a Primary is
    minted from the account name/mobile. When members exist but none is flagged,
    the chosen primary (Self / first) is flagged is_primary and the root
    primary_patient_id pointer is set.
    """
    patients = _patients_of(customer)
    changed = False

    if not patients:
        primary = build_primary_member(
            name=customer.get("name") or customer.get("customer_name") or "Primary",
            mobile=(
                customer.get("mobile")
                or customer.get("phone")
                or None
            ),
        )
        patients = [primary]
        customer["patients"] = patients
        changed = True
    else:
        primary = choose_primary_member(patients)
        # primary is guaranteed non-None here (patients is non-empty).
        if not primary.get("is_primary"):
            primary["is_primary"] = True
            changed = True
        # Backfill a stable patient_id on a legacy member that lacks one.
        if not _norm(primary.get("patient_id")):
            primary["patient_id"] = str(uuid.uuid4())
            changed = True

    if customer.get("primary_patient_id") != primary.get("patient_id"):
        customer["primary_patient_id"] = primary.get("patient_id")
        changed = True

    return primary, changed


def find_member(
    customer: Optional[Dict[str, Any]],
    patient_id: str,
) -> Optional[Dict[str, Any]]:
    """Return the member dict whose patient_id == patient_id within `customer`,
    or None when no such member exists on this account.

    Done in Python (scan patients[]) rather than a Mongo dot-notation query so it
    works identically against a real Mongo doc and the in-memory test FakeDB.
    """
    pid = _norm(patient_id)
    if not pid:
        return None
    for p in _patients_of(customer):
        if p and _norm(p.get("patient_id")) == pid:
            return p
    # Legacy read fallback ONLY (never a write path): some historical accounts
    # used patient_id == customer_id with no real member row.
    if customer and _norm(customer.get("customer_id")) == pid:
        return {
            "patient_id": pid,
            "name": customer.get("name") or "Customer",
            "relation": "Self",
            "is_primary": True,
            "_legacy_self": True,
        }
    return None
