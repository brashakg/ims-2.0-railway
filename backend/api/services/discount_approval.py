"""
IMS 2.0 - Who approved a POS discount (THE one rule)
=====================================================
Owner decision 2026-10-18: the approver of a discount is decided by the SERVER.
A name typed by the till is never trusted or stored as the approver.

  * The seller's own ROLE cap covers the bill's discount (and, for a Rs 0 bill,
    the seller is a manager) -> the seller is the approver.
  * Otherwise the order carries the request_id of an E4 ``discount_override``
    approval: APPROVED, unspent, unexpired, raised by THIS seller for THIS
    store and THIS cart, for at least this percent, approved by SOMEONE ELSE
    whose own role cap covers it. The approver recorded is that person.
  * Anything else -> 403. DISCOUNT_NEEDS_APPROVAL carries everything the till
    needs to open the E4 request; DISCOUNT_APPROVAL_INVALID says why the
    approval it sent cannot be used.

The override lifts ONLY the role cap. The category / luxury-brand caps, the
HQ-offer rule, promo ceilings and the cost floor are checked by the pricing
gates before this runs, and stay hard limits whoever approves.

Spending is single-use and atomic (ApprovalEngine.consume_approval, a compare-
and-set on the approval doc), done immediately before the order is written;
a write that then fails gives it back (give_back). Every door that accepts a
discount calls this module -- never a second copy of the rule.

No emoji / non-ASCII (Windows cp1252).
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from fastapi import HTTPException

from .approvals import ApprovalEngine, _as_aware
from .role_caps import ROLE_DISCOUNT_CAPS, effective_discount_cap

logger = logging.getLogger(__name__)

ACTION = "discount_override"

# Who may sign off a Rs 0 bill: the managers (E4's own discount-approver tier).
MANAGER_ROLES = frozenset({"SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"})


def _db():
    """Raw Mongo handle (patched in tests). None = no DB: an approval cannot be
    checked, so none is accepted (fail closed)."""
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and getattr(conn, "is_connected", False):
            return conn.db
    except Exception:  # noqa: BLE001
        pass
    return None


def _user(db, user_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if db is None or not user_id:
        return None
    try:
        return db.get_collection("users").find_one(
            {"user_id": user_id},
            {"_id": 0, "roles": 1, "full_name": 1, "username": 1},
        )
    except Exception:  # noqa: BLE001
        return None


def _name(doc: Optional[Dict[str, Any]], fallback: Optional[str]) -> Optional[str]:
    return (doc or {}).get("full_name") or (doc or {}).get("username") or fallback


def cart_key(product_ids: Iterable[Any]) -> str:
    """The cart an approval is bound to: its distinct product ids, sorted."""
    return ",".join(sorted({str(p).strip() for p in product_ids if str(p or "").strip()}))


def _covers(roles: List[str], cap: float, need: float, zero_total: bool) -> bool:
    return need <= cap + 1e-9 and (not zero_total or bool(set(roles) & MANAGER_ROLES))


def _refuse(code: str, message: str, status: int = 403, **extra) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def resolve_discount_approver(
    current_user: dict,
    *,
    store_id: Optional[str],
    lines: List[Dict[str, Any]],
    zero_total: bool = False,
    approval_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Who approved this bill's discount, or None when it carries none.

    ``lines`` -- one dict per order line: product_id, product_name and
    discount_percent = the line's TOTAL manual discount (its own discount and
    the bill discount combined, pricing.combined_discount_pct).

    Returns {"user_id", "name", "approval_id"} (approval_id None when the
    seller's own limit covers it). Raises 403 otherwise. Reads only: the
    approval is spent later, by spend(), right before the order is written.
    """
    need = max((float(ln.get("discount_percent") or 0.0) for ln in lines), default=0.0)
    if need <= 1e-9 and not zero_total:
        return None
    db = _db()
    uid = current_user.get("user_id")
    roles = list(current_user.get("roles") or [])
    cap = effective_discount_cap(roles, current_user.get("discount_cap"))
    if _covers(roles, cap, need, zero_total):
        fallback = current_user.get("full_name") or current_user.get("username") or uid
        return {"user_id": uid, "name": _name(_user(db, uid), fallback), "approval_id": None}

    key = cart_key(ln.get("product_id") for ln in lines)
    if not approval_id:
        over = [
            {
                "product_id": ln.get("product_id"),
                "product_name": ln.get("product_name"),
                "discount_percent": round(float(ln.get("discount_percent") or 0.0), 2),
            }
            for ln in lines
            if float(ln.get("discount_percent") or 0.0) > cap + 1e-9
        ]
        if need > cap + 1e-9:
            label = (over[0].get("product_name") or over[0].get("product_id")) if over else "this bill"
            message = (
                f"The discount on {label} comes to {need:.2f}% (item and bill "
                f"discounts together), which exceeds your limit of {cap:g}%. "
                f"A manager must approve it."
            )
        else:
            message = "A Rs 0 bill needs a manager's approval."
        # Rounded UP, so the percent the approval carries always covers `need`.
        asked = math.ceil(need * 100 - 1e-6) / 100
        raise _refuse(
            "DISCOUNT_NEEDS_APPROVAL",
            message,
            requested_percent=asked,
            max_percent=cap,
            zero_total=bool(zero_total),
            lines=over,
            # The exact E4 request (POST /approvals/requests) that would cover
            # this bill; the till adds a reason and sends it as it is. The tier
            # only routes the bell -- spend-time checks below are the rule.
            approval_request={
                "action_type": ACTION,
                "store_id": store_id,
                "required_tier": (
                    "auto" if asked <= ROLE_DISCOUNT_CAPS["STORE_MANAGER"] else "admin"
                ),
                "context": {
                    "discount_percent": asked,
                    "zero_total": bool(zero_total),
                    "product_ids": key,
                },
            },
        )

    doc = ApprovalEngine(db=db).get(approval_id) if db is not None else None
    approver = _user(db, (doc or {}).get("reviewed_by"))
    why = _approval_problem(doc, approver, uid, store_id, key, need, zero_total)
    if why:
        raise _refuse(
            "DISCOUNT_APPROVAL_INVALID",
            f"That manager approval cannot be used: {why}. Ask a manager again.",
        )
    return {
        "user_id": doc["reviewed_by"],
        "name": _name(approver, doc["reviewed_by"]),
        "approval_id": approval_id,
    }


def _approval_problem(doc, approver, uid, store_id, key, need, zero_total) -> Optional[str]:
    if not doc:
        return "it was not found"
    if doc.get("action_type") != ACTION:
        return "it is not a discount approval"
    if doc.get("consumed") or doc.get("status") == "CONSUMED":
        return "it has already been used on a sale"
    if doc.get("status") != "APPROVED":
        return f"it is {str(doc.get('status') or 'not approved').lower()}, not approved"
    if _as_aware(doc.get("expires_at")) <= datetime.now(timezone.utc):
        return "it has expired"
    if doc.get("store_id") != store_id:
        return "it was given for another store"
    if doc.get("requested_by") != uid:
        return "it was asked for by someone else"
    if not doc.get("reviewed_by") or doc.get("reviewed_by") == uid:
        return "you cannot approve your own discount"
    ctx = doc.get("context") or {}
    if str(ctx.get("product_ids") or "") != key:
        return "it was given for a different bill"
    try:
        granted = float(ctx.get("discount_percent"))
    except (TypeError, ValueError):
        granted = -1.0
    if granted + 1e-9 < need:
        return f"it covers {max(granted, 0):g}% and this bill needs {need:.2f}%"
    if zero_total and not ctx.get("zero_total"):
        return "it does not cover a Rs 0 bill"
    a_roles = list((approver or {}).get("roles") or [])
    # The approver's ROLE cap (no per-user override): an override can never
    # lift a discount above what the approving role may give.
    a_cap = effective_discount_cap(a_roles)
    if not _covers(a_roles, a_cap, need, zero_total):
        if need > a_cap + 1e-9:
            return f"the approver's own limit is {a_cap:g}%"
        return "only a manager can approve a Rs 0 bill"
    return None


def spend(approver: Optional[Dict[str, Any]], current_user: dict, store_id: Optional[str]) -> None:
    """Spend the approval behind ``approver`` exactly once (atomic compare-and-
    set). No approval -> no-op. A lost race -> 409 and nothing is saved."""
    approval_id = (approver or {}).get("approval_id")
    if not approval_id:
        return
    res = ApprovalEngine(db=_db()).consume_approval(
        consumed_by=current_user.get("user_id") or "",
        action_type=ACTION,
        request_id=approval_id,
        expected_store_id=store_id,
    )
    if not res.get("ok"):
        logger.warning("[DISCOUNT] approval %s not spent: %s", approval_id, res.get("error"))
        raise _refuse(
            "DISCOUNT_APPROVAL_USED",
            "That manager approval was just used on another sale, or has "
            "expired. Nothing was saved. Ask a manager again.",
            status=409,
        )


def give_back(approver: Optional[Dict[str, Any]], current_user: dict) -> None:
    """The order that spent the approval was not saved: give it back. Fail-soft
    (a write that cannot be undone leaves it spent -- fail closed)."""
    approval_id = (approver or {}).get("approval_id")
    if not approval_id:
        return
    try:
        ApprovalEngine(db=_db()).release_consumption(
            approval_id, consumed_by=current_user.get("user_id") or ""
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[DISCOUNT] approval %s not given back: %s", approval_id, exc)
