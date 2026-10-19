"""
IMS 2.0 - Who approved a POS discount (THE one rule)
=====================================================
Owner decision 2026-10-18: the approver of a discount is decided by the SERVER.
A name typed by the till is never trusted or stored as the approver.

  * The seller's own limit covers the bill's discount (and, for a Rs 0 bill,
    the seller is a manager) -> the seller is the approver.
  * Otherwise the order carries the request_id of an E4 ``discount_override``
    approval for EXACTLY this bill: the server built that request itself from
    its own pricing of the cart (ask_manager -- a till cannot write one), it is
    APPROVED, unspent, unexpired, raised by THIS seller for THIS store, and it
    was approved by SOMEONE ELSE, still active, whose own role cap covers it.
    The approver recorded is that person.
  * Anything else -> a refusal with a stable code the till acts on.

"Exactly this bill": every line's product, quantity, unit price and line %,
the bill %, the rupees taken off (and the draft + change for the line doors),
hashed canonically (bill_hash). The hash is checked when the approval is read
AND inside the atomic spend, so an 18% approval on one frame can never be
spent on five, nor turned into a bill discount on a dearer lens.

The override lifts ONLY the role cap. The category / luxury-brand caps, the
HQ-offer rule, promo ceilings and the cost floor are checked by the pricing
gates before this runs, and stay hard limits whoever approves.

Spending is single-use and atomic (ApprovalEngine.consume_approval, a compare-
and-set on the approval doc), done immediately before the order is written; a
write that then fails gives it back -- only once it is confirmed that nothing
was saved. Every door that changes a discounted bill calls this module.

No emoji / non-ASCII (Windows cp1252).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from ..utils.event_time import utc_now_iso
from .approvals import ApprovalEngine, _as_aware
from .role_caps import ROLE_DISCOUNT_CAPS, effective_discount_cap

logger = logging.getLogger(__name__)

ACTION = "discount_override"

# Discount requests one cashier may have waiting for a manager at a time.
MAX_WAITING = 3

# Who may sign off a Rs 0 bill: the managers (E4's own discount-approver tier).
MANAGER_ROLES = frozenset({"SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"})


def _db():
    """Raw Mongo handle (patched in tests). None = no DB: an approval cannot be
    checked or raised, so none is accepted (fail closed)."""
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
            {"_id": 0, "roles": 1, "full_name": 1, "username": 1,
             "discount_cap": 1, "is_active": 1},
        )
    except Exception:  # noqa: BLE001
        return None


def _name(doc: Optional[Dict[str, Any]], fallback: Optional[str]) -> Optional[str]:
    return (doc or {}).get("full_name") or (doc or {}).get("username") or fallback


def _refuse(code: str, message: str, status: int = 403, **extra) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def _pct_up(value: float) -> float:
    """Percent rounded UP to 2 places: 20.004 reads 20.01, never "20.00%"."""
    return math.ceil(float(value) * 100 - 1e-6) / 100


def who(current_user: dict) -> Dict[str, Any]:
    """The acting user as an approver record: id + display name."""
    uid = current_user.get("user_id")
    fallback = current_user.get("full_name") or current_user.get("username") or uid
    return {"user_id": uid, "name": _name(_user(_db(), uid), fallback)}


def seller_cap(current_user: dict) -> float:
    """The seller's own limit: role cap, raised (never lowered) by the
    per-user override on their account -- the same figure the till shows."""
    doc = _user(_db(), current_user.get("user_id"))
    override = (doc or {}).get("discount_cap", current_user.get("discount_cap"))
    return effective_discount_cap(current_user.get("roles") or [], override)


# ---------------------------------------------------------------------------
# The bill an approval is bound to
# ---------------------------------------------------------------------------


def bill_of(
    *,
    store_id: Optional[str],
    lines: List[Dict[str, Any]],
    cart_discount_percent: float,
    discount_rupees: float,
    bill_before: float,
    bill_after: float,
    zero_total: bool = False,
    order_id: Optional[str] = None,
    change: Optional[str] = None,
    customer_id: Optional[str] = None,
    customer_name: Optional[str] = None,
) -> Dict[str, Any]:
    """The server's own pricing of a bill, and its canonical hash.

    ``lines`` -- product_id, product_name, quantity, mrp, unit_price,
    discount_percent (the line's own %) and combined_percent (the line's TOTAL
    manual discount, item x bill: pricing.combined_discount_pct) -- the figure
    the seller's limit is measured on. ``name_from_till`` marks a line with no
    catalogue product (a lens / custom line), whose name the till typed: that
    name is part of the bill an approval covers. A catalogue product's name is
    the catalogue's (the product id already pins it).
    ``customer_id`` -- whose bill it is: an approval for one customer's bill
    cannot be spent on another's.
    ``order_id`` / ``change`` -- set by the doors that edit a saved draft, so
    an approval for one draft change cannot be spent on another.
    """
    canonical = {
        "lines": sorted(
            [
                str(ln.get("product_id") or ""),
                int(ln.get("quantity") or 0),
                round(float(ln.get("unit_price") or 0.0), 2),
                round(float(ln.get("discount_percent") or 0.0), 2),
                str(ln.get("product_name") or "") if ln.get("name_from_till") else "",
            ]
            for ln in lines
        ),
        "customer_id": customer_id,
        "cart_discount_percent": round(float(cart_discount_percent or 0.0), 2),
        "discount_rupees": round(float(discount_rupees or 0.0), 2),
        "zero_total": bool(zero_total),
        "order_id": order_id,
        "change": change,
    }
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    need = max((float(ln.get("combined_percent") or 0.0) for ln in lines), default=0.0)
    return {
        "store_id": store_id,
        "bill_hash": digest,
        "need": need,
        "zero_total": bool(zero_total),
        "lines": lines,
        "cart_discount_percent": round(float(cart_discount_percent or 0.0), 2),
        "discount_rupees": round(float(discount_rupees or 0.0), 2),
        "bill_before": round(float(bill_before or 0.0), 2),
        "bill_after": round(float(bill_after or 0.0), 2),
        "order_id": order_id,
        "customer_id": customer_id,
        "customer_name": customer_name,
    }


def _covers(roles: List[str], cap: float, need: float, zero_total: bool) -> bool:
    return need <= cap + 1e-9 and (not zero_total or bool(set(roles) & MANAGER_ROLES))


def _needs(current_user: dict, bill: Dict[str, Any]) -> Optional[float]:
    """None when the seller's own limit covers ``bill``; else their limit."""
    if bill["need"] <= 1e-9 and not bill["zero_total"]:
        return None
    cap = seller_cap(current_user)
    if _covers(list(current_user.get("roles") or []), cap, bill["need"], bill["zero_total"]):
        return None
    return cap


def resolve_discount_approver(
    current_user: dict,
    bill: Dict[str, Any],
    approval_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Who approved this bill's discount, or None when it carries none.

    Returns {"user_id", "name", "approval_id", "bill_hash"} (approval_id None
    when the seller's own limit covers it). Raises otherwise. Reads only: the
    approval is spent by spend(), right before the order is written.
    """
    if bill["need"] <= 1e-9 and not bill["zero_total"]:
        return None
    cap = _needs(current_user, bill)
    if cap is None:
        return {**who(current_user), "approval_id": None, "bill_hash": bill["bill_hash"]}
    if not approval_id:
        raise _needs_approval(bill, cap)

    db = _db()
    if db is None:
        raise _refuse(
            "DISCOUNT_APPROVAL_UNAVAILABLE",
            "Manager approvals cannot be checked right now. Try again in a moment.",
            status=503,
        )
    doc = ApprovalEngine(db=db).get(approval_id)
    approver = _user(db, (doc or {}).get("reviewed_by"))
    problem = _approval_problem(doc, approver, current_user.get("user_id"), bill)
    if problem:
        raise _refuse(*problem)
    return {
        "user_id": doc["reviewed_by"],
        "name": _name(approver, doc["reviewed_by"]),
        "approval_id": approval_id,
        "bill_hash": bill["bill_hash"],
    }


def _needs_approval(bill: Dict[str, Any], cap: float) -> HTTPException:
    need = bill["need"]
    over = [
        {
            "product_id": ln.get("product_id"),
            "product_name": ln.get("product_name"),
            "discount_percent": _pct_up(ln.get("combined_percent") or 0.0),
        }
        for ln in bill["lines"]
        if float(ln.get("combined_percent") or 0.0) > cap + 1e-9
    ]
    if need > cap + 1e-9:
        label = (over[0].get("product_name") or over[0].get("product_id")) if over else "this bill"
        message = (
            f"The discount on {label} comes to {_pct_up(need):.2f}% (item and bill "
            f"discounts together), which exceeds your limit of {cap:g}%. "
            f"A manager must approve it."
        )
    else:
        message = "A Rs 0 bill needs a manager's approval."
    return _refuse(
        "DISCOUNT_NEEDS_APPROVAL",
        message,
        requested_percent=_pct_up(need),
        max_percent=cap,
        zero_total=bill["zero_total"],
        lines=over,
    )


def _approval_problem(doc, approver, uid, bill) -> Optional[tuple]:
    """(code, message, status) when the approval cannot cover this bill."""
    def bad(why: str, code: str = "DISCOUNT_APPROVAL_INVALID", status: int = 403):
        return (code, f"That manager approval cannot be used: {why}. Ask a manager again.", status)

    if not doc or doc.get("action_type") != ACTION:
        return bad("it was not found", "DISCOUNT_APPROVAL_NOT_FOUND", 404)
    if doc.get("consumed") or doc.get("status") == "CONSUMED":
        return bad("it has already been used on a sale", "DISCOUNT_APPROVAL_USED", 409)
    if doc.get("status") == "EXPIRED" or (
        doc.get("status") == "APPROVED"
        and _as_aware(doc.get("expires_at")) <= datetime.now(timezone.utc)
    ):
        return bad("it has expired", "DISCOUNT_APPROVAL_EXPIRED", 410)
    if doc.get("status") != "APPROVED":
        return bad(f"it is {str(doc.get('status') or 'not approved').lower()}, not approved")
    if doc.get("store_id") != bill["store_id"]:
        return bad("it was given for another store")
    if doc.get("requested_by") != uid:
        return bad("it was asked for by someone else")
    if not doc.get("reviewed_by") or doc.get("reviewed_by") == uid:
        return bad("you cannot approve your own discount")
    if (doc.get("context") or {}).get("bill_hash") != bill["bill_hash"]:
        return bad("it was given for a different bill (the items, prices or discounts changed)")
    if not approver or approver.get("is_active") is False:
        return bad("the approving manager's account is not active")
    a_roles = list(approver.get("roles") or [])
    # The approver's ROLE cap: an override can never lift a discount above
    # what the approving role may give.
    a_cap = effective_discount_cap(a_roles)
    if not _covers(a_roles, a_cap, bill["need"], bill["zero_total"]):
        if bill["need"] > a_cap + 1e-9:
            return bad(f"the approver's own limit is {a_cap:g}%")
        return bad("only a manager can approve a Rs 0 bill")
    return None


# ---------------------------------------------------------------------------
# Asking: the SERVER builds the request from its own pricing of the bill
# ---------------------------------------------------------------------------


def _routing(need: float) -> Dict[str, Any]:
    """Send the request only to roles whose own cap covers it."""
    if need <= ROLE_DISCOUNT_CAPS["STORE_MANAGER"] + 1e-9:
        return {"required_tier": "auto", "only_roles": None}
    if need <= ROLE_DISCOUNT_CAPS["AREA_MANAGER"] + 1e-9:
        return {"required_tier": "admin", "only_roles": None}
    return {"required_tier": "admin", "only_roles": ["SUPERADMIN", "ADMIN"]}


def ask_manager(current_user: dict, bill: Dict[str, Any], note: Optional[str] = None):
    """Open (or return the open) discount_override request for exactly this
    bill. 202 {request_id, status, bill_hash}; 409 when no approval is needed.
    Never creates an order -- the caller returns this before any reservation."""
    if _needs(current_user, bill) is None:
        raise _refuse(
            "DISCOUNT_APPROVAL_NOT_NEEDED",
            "This bill is within your own limit. Complete the sale.",
            status=409,
        )
    db = _db()
    if db is None:
        raise _refuse(
            "DISCOUNT_APPROVAL_UNAVAILABLE",
            "Manager approvals cannot be raised right now. Try again in a moment.",
            status=503,
        )
    uid = current_user.get("user_id")
    eng = ApprovalEngine(db=db)
    # The same bill already approved and unspent: hand that back.
    try:
        live = db.get_collection(ApprovalEngine.COLLECTION).find_one(
            {
                "action_type": ACTION,
                "requested_by": uid,
                "store_id": bill["store_id"],
                "context.bill_hash": bill["bill_hash"],
                "status": "APPROVED",
                "consumed": False,
                "expires_at": {"$gt": datetime.now(timezone.utc)},
            },
            {"_id": 0, "request_id": 1},
        )
    except Exception:  # noqa: BLE001
        live = None
    if live:
        return JSONResponse(
            status_code=202,
            content={"code": "DISCOUNT_APPROVAL_REQUESTED", "request_id": live["request_id"],
                     "status": "APPROVED", "bill_hash": bill["bill_hash"]},
        )
    coll = db.get_collection(ApprovalEngine.COLLECTION)
    waiting_q = {
        "action_type": ACTION,
        "requested_by": uid,
        "status": "REQUESTED",
        "expires_at": {"$gt": datetime.now(timezone.utc)},
    }
    try:
        same = coll.find_one(
            {**waiting_q, "context.bill_hash": bill["bill_hash"], "store_id": bill["store_id"]},
            {"_id": 0, "request_id": 1},
        )
        waiting = 0 if same else len(list(coll.find(waiting_q, {"_id": 0, "request_id": 1})))
    except Exception:  # noqa: BLE001
        same, waiting = None, 0
    # The same bill already waiting is not a new request (the engine's
    # dedupe_key below hands it back), so it never counts against the cap.
    # ponytail: a count, not an atomic guard -- two asks racing past it can
    # leave a 4th waiting (harmless: each still needs a manager's PIN). Make
    # it a counter doc with a conditional $inc if that ever matters.
    if waiting >= MAX_WAITING:
        raise _refuse(
            "DISCOUNT_APPROVAL_TOO_MANY",
            f"You have {waiting} requests waiting for a manager. Wait for them, "
            f"or let them expire, before asking again.",
            status=429,
        )
    me = who(current_user)
    context = {
        "bill_hash": bill["bill_hash"],
        "discount_percent": _pct_up(bill["need"]),
        "zero_total": bill["zero_total"],
        "lines": [
            {k: ln.get(k) for k in ("product_id", "product_name", "quantity", "mrp",
                                    "unit_price", "discount_percent")}
            for ln in bill["lines"]
        ],
        "cart_discount_percent": bill["cart_discount_percent"],
        "discount_rupees": bill["discount_rupees"],
        "bill_before": bill["bill_before"],
        "bill_after": bill["bill_after"],
        "order_id": bill.get("order_id"),
        "customer_id": bill.get("customer_id"),
        "customer_name": bill.get("customer_name"),
        "requested_by_name": me["name"],
    }
    routing = _routing(bill["need"])
    res = eng.request(
        action_type=ACTION,
        requested_by=uid,
        requested_by_roles=list(current_user.get("roles") or []),
        store_id=bill["store_id"],
        context=context,
        # The only words the cashier writes; the card labels them as theirs.
        reason=(note or "").strip()[:300],
        required_tier=routing["required_tier"],
        only_roles=routing["only_roles"],
        dedupe_key=f"{ACTION}:{bill['store_id']}:{uid}:{bill['bill_hash']}",
    )
    if not res.get("ok"):
        raise _refuse(
            "DISCOUNT_APPROVAL_UNAVAILABLE",
            "The request to a manager could not be sent. Try again.",
            status=503,
        )
    return JSONResponse(
        status_code=202,
        content={"code": "DISCOUNT_APPROVAL_REQUESTED", "request_id": res["request_id"],
                 "status": res.get("status"), "bill_hash": bill["bill_hash"]},
    )


# ---------------------------------------------------------------------------
# Spending
# ---------------------------------------------------------------------------


def spend(approver: Optional[Dict[str, Any]], current_user: dict, store_id: Optional[str]) -> None:
    """Spend the approval behind ``approver`` exactly once -- an atomic
    compare-and-set that re-checks the store and the bill hash. No approval
    -> no-op. A lost race -> 409 and nothing is saved. On success, the same
    seller's other open requests for this bill are closed."""
    approval_id = (approver or {}).get("approval_id")
    if not approval_id:
        return
    uid = current_user.get("user_id") or ""
    eng = ApprovalEngine(db=_db())
    res = eng.consume_approval(
        consumed_by=uid,
        action_type=ACTION,
        request_id=approval_id,
        expected_store_id=store_id,
        expected_context={"bill_hash": approver.get("bill_hash")},
    )
    if not res.get("ok"):
        logger.warning("[DISCOUNT] approval %s not spent: %s", approval_id, res.get("error"))
        raise _refuse(
            "DISCOUNT_APPROVAL_USED",
            "That manager approval was just used on another sale, or has "
            "expired. Nothing was saved. Ask a manager again.",
            status=409,
        )
    _close_siblings(eng, approval_id, uid, store_id, approver.get("bill_hash"))


def _close_siblings(eng: ApprovalEngine, spent_id, uid, store_id, bill_hash) -> None:
    coll = eng._coll()  # pylint: disable=protected-access
    if coll is None or not bill_hash:
        return
    try:
        others = list(coll.find(
            {"action_type": ACTION, "requested_by": uid, "store_id": store_id,
             "context.bill_hash": bill_hash, "consumed": False,
             "status": {"$in": ["REQUESTED", "APPROVED"]}},
            {"_id": 0},
        ))
        for doc in others:
            if doc.get("request_id") == spent_id:
                continue
            res = coll.update_one(
                {"request_id": doc["request_id"], "consumed": False,
                 "status": {"$in": ["REQUESTED", "APPROVED"]}},
                {"$set": {"status": "EXPIRED", "closed_reason": f"bill sold with {spent_id}"}},
            )
            if getattr(res, "modified_count", 0):
                # pylint: disable-next=protected-access
                eng._write_audit("superseded", dict(doc, status="EXPIRED"), actor=uid,
                                 reason=f"bill sold with {spent_id}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[DISCOUNT] could not close sibling approvals of %s: %s", spent_id, exc)


def give_back(
    approver: Optional[Dict[str, Any]],
    current_user: dict,
    was_saved: Callable[[], bool],
) -> None:
    """The order that spent the approval failed to save: give the approval
    back -- but ONLY once ``was_saved()`` confirms nothing was written. If the
    check fails or finds the sale, the approval stays spent (fail closed):
    an approval must never be re-armed while a sale it paid for exists."""
    approval_id = (approver or {}).get("approval_id")
    if not approval_id:
        return
    try:
        if was_saved():
            return
        ApprovalEngine(db=_db()).release_consumption(
            approval_id, consumed_by=current_user.get("user_id") or ""
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[DISCOUNT] approval %s not given back: %s", approval_id, exc)


# ---------------------------------------------------------------------------
# Rs 0 bills (C-4)
# ---------------------------------------------------------------------------


def is_full_line(line: Dict[str, Any]) -> bool:
    """A giveaway line: 100% off (explicit or effective), or priced Rs 0 with
    no MRP to measure a discount from."""
    pct = max(float(line.get("discount_percent") or 0.0),
              float(line.get("effective_discount_percent") or 0.0))
    return pct >= 100.0 - 1e-9 or (
        float(line.get("unit_price") or 0.0) <= 0.0 and not line.get("mrp")
    )


def zero_total_state(items: List[Dict[str, Any]], grand_total: float, cart_pct: float):
    """(is_zero_total, full_lines) for a bill's line docs."""
    full = [it for it in items if is_full_line(it or {})]
    zero = bool(items) and (
        round(float(grand_total or 0.0), 2) <= 0.0 or float(cart_pct or 0.0) >= 100.0 or bool(full)
    )
    return zero, full


def zero_total_reason(cart_reason: Optional[str], full_lines: List[Dict[str, Any]]) -> str:
    """The written reason a Rs 0 bill needs: the bill's, else a giveaway
    line's. 400 when there is none."""
    for cand in [cart_reason] + [ln.get("discount_reason") for ln in full_lines]:
        if cand is not None and str(cand).strip():
            return str(cand)
    raise HTTPException(status_code=400, detail="Zero-total or 100% discount requires a reason.")


def audit_zero_total(*, order_id, store_id, user_id, approver, reason, details) -> None:
    """The immutable ORDER_ZERO_TOTAL_APPROVED row. Fail-soft: an audit
    failure never blocks the sale."""
    try:
        from ..dependencies import get_audit_repository

        audit = get_audit_repository()
        if audit is None:
            return
        audit.create({
            "action": "ORDER_ZERO_TOTAL_APPROVED",
            "entity_type": "order",
            "entity_id": order_id,
            "store_id": store_id,
            "user_id": user_id,
            "severity": "WARNING",
            "details": {
                **details,
                "approved_by": (approver or {}).get("user_id"),
                "approved_by_name": (approver or {}).get("name"),
                "approval_id": (approver or {}).get("approval_id"),
                "reason": reason,
            },
            "created_at": utc_now_iso(),
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ORDERS] zero-total audit skipped: %s", exc)
