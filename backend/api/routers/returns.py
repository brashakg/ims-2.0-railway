"""
IMS 2.0 - Customer Returns / Exchange / Credit-Note Router
==========================================================
Records customer-side returns, exchanges and store-credit notes against an
original sale. This is REVENUE-ADJACENT but it only RECORDS money movement -
no payment gateway is ever called. Refunds and collections are recorded as
amounts + a method; the actual settlement happens out-of-band at the till.

What each return_type does:
  - RETURN      -> record refund_amount = returned_value (to source / chosen method).
  - CREDIT_NOTE -> issue store credit for returned_value to the customer (ledger).
  - EXCHANGE    -> settle returned_value against replacement_items; record the
                   COLLECT / REFUND difference (REFUND may be issued as credit).

For every type, returned units in GOOD condition are put BACK into sellable
serialized stock (the `stock_units` collection - re-activate the original unit
or mint a fresh AVAILABLE one), idempotently + fail-soft. See restock_engine
for the resellable-vs-hold decision logic.

Conventions mirrored from the rest of the codebase:
  - get_current_user / require_roles for auth + RBAC.
  - Repository accessors from ..dependencies; fail-soft when DB is absent.
  - Store-credit reuses services.store_credit_ledger.make_entry + the same
    `credit_note_ledger` collection the customers router writes.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, Query
from pydantic import BaseModel, Field

try:
    from pymongo import ReturnDocument
except ImportError:  # pragma: no cover - test stubs may not have pymongo

    class ReturnDocument:  # type: ignore[no-redef]
        AFTER = "after"
        BEFORE = "before"


from .auth import get_current_user, require_roles
from ..dependencies import (
    can_access_store_scoped,
    get_customer_repository,
    get_order_repository,
    get_product_repository,
    get_stock_repository,
    validate_store_access,
)
from ..services import restock_engine
from ..services import returns_engine as engine
from ..services import store_credit_ledger as scl

logger = logging.getLogger(__name__)
router = APIRouter()

# Roles allowed to CREATE a return - mirrors the Returns nav item guard in
# frontend/src/components/shell/Rail.tsx. SUPERADMIN auto-passes.
# SALES_CASHIER was merged into SALES_STAFF (backlog #12); this list previously
# granted SALES_CASHIER but NOT SALES_STAFF, so the access moves to the survivor
# SALES_STAFF to preserve it under the merged role.
_RETURN_ROLES = (
    "ADMIN",
    "STORE_MANAGER",
    "CASHIER",
    "SALES_STAFF",
)

ReturnType = Literal["RETURN", "EXCHANGE", "CREDIT_NOTE"]
ItemCondition = Literal["GOOD", "OPENED", "DAMAGED"]


# ============================================================================
# SCHEMAS
# ============================================================================


class ReturnLine(BaseModel):
    order_item_id: Optional[str] = None
    product_id: Optional[str] = None
    product_name: str = ""
    sku: str = ""
    return_qty: float = Field(..., ge=0)
    # NET (pre-GST) unit price as billed on the original order line. The refund
    # is the GST-INCLUSIVE gross, so this is grossed up by `gst_rate` (resolved
    # authoritatively from the original order when available - see
    # _priced_return_lines). Do NOT pass an already-gross price here.
    unit_price: float = Field(..., ge=0)
    # GST rate (%) the line was billed at. Optional hint from the till; the
    # server prefers the rate stamped on the original order line. 0 / None ->
    # treat unit_price as already gross (no gross-up).
    gst_rate: Optional[float] = Field(default=None, ge=0)
    reason: Optional[str] = None
    condition: ItemCondition = "GOOD"
    # Whether this returned unit should go back into sellable stock. Defaults
    # True; only GOOD-condition units are ever restocked (see restock_engine).
    # The till can set this False to hold a GOOD-looking unit off the shelf.
    restock: bool = True
    notes: Optional[str] = None
    # E3w (E3 acceptance #8, return half): the physical serial scanned at the
    # till for THIS returned unit. When BOTH this and the matched stock unit's
    # `stock_units.serial` are present and they DIFFER, the return is hard-
    # blocked (409 SERIAL_MISMATCH) unless a manager override is consumed.
    # Permissive: if EITHER serial is absent (legacy / dirty data) the check is
    # skipped -- never block a legitimate return on missing serial data.
    serial: Optional[str] = None


class ReplacementLine(BaseModel):
    product_id: Optional[str] = None
    name: str = ""
    sku: str = ""
    quantity: float = Field(..., ge=0)
    unit_price: float = Field(..., ge=0)
    # GST rate (%) for the replacement line so its gross matches what the
    # customer will be billed. 0 / None -> unit_price treated as already gross.
    gst_rate: Optional[float] = Field(default=None, ge=0)


class ReturnCreate(BaseModel):
    order_id: Optional[str] = None
    order_number: Optional[str] = None
    customer_id: Optional[str] = None
    store_id: Optional[str] = None
    return_type: ReturnType = "RETURN"
    items: List[ReturnLine] = Field(default_factory=list)
    replacement_items: List[ReplacementLine] = Field(default_factory=list)
    approval_note: Optional[str] = None
    refund_method: Optional[str] = None
    # Optional absolute Rs deduction for damaged / opened goods. 0 = full
    # refund. Must be >= 0 and <= the GST-inclusive gross refund (enforced in
    # the handler -> 422). Net refund = gross - restocking_fee.
    restocking_fee: float = Field(default=0.0, ge=0)
    # E3w (E3 acceptance #8): a manager-approved override token for a
    # SERIAL_MISMATCH hard-block. When a return line's scanned serial differs
    # from the matched stock unit's serial, the return is 409'd UNLESS this
    # token resolves to a valid, single-use, not-expired E4 approval of
    # action_type RETURN_SERIAL_OVERRIDE (atomically consumed by create_return).
    # If the E4 approval engine is unreachable, the override is DENIED (safe
    # default = block) -- the till must clear the mismatch the normal way.
    serial_mismatch_override_token: Optional[str] = None
    serial_mismatch_override_request_id: Optional[str] = None
    # F27 refund approval matrix. When the matrix is ENABLED for this scope and
    # the resolved tier for this refund is > 0, the return is blocked UNLESS one
    # of these resolves to a valid, single-use, not-expired E4 approval of
    # action_type REFUND_APPROVAL_MATRIX bound to THIS order + store (the maker
    # mints it via POST /approvals/requests + a manager PIN-approves it; the
    # requester cannot approve their own -- it is a maker-checker action). DARK by
    # default (matrix_enabled=False): when the matrix is off these are ignored and
    # the refund path is byte-identical to today. The token is consumed atomically
    # by create_return; a missing / mismatched / expired token -> 403.
    refund_approval_token: Optional[str] = None
    refund_approval_request_id: Optional[str] = None
    # Overall refund reason driving the matrix (DEFECTIVE / CHANGE_OF_MIND /
    # PRICE_MATCH / GOODWILL / ...). Optional; falls back to the first per-line
    # reason. Only consulted when the matrix is enabled.
    refund_reason: Optional[str] = None


# ============================================================================
# DB HELPERS (fail-soft - no DB must never 500)
# ============================================================================


def _get_db():
    """Raw MongoDB handle, or None when unavailable (mock / no-DB mode)."""
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and getattr(conn, "is_connected", False):
            return conn.db
    except Exception:  # noqa: BLE001
        pass
    return None


def _returns_coll():
    db = _get_db()
    if db is None:
        return None
    try:
        return db.get_collection("returns")
    except Exception:  # noqa: BLE001
        return None


def _ledger_coll():
    """Same collection the customers router uses for the credit-note ledger."""
    db = _get_db()
    if db is None:
        return None
    try:
        return db.get_collection("credit_note_ledger")
    except Exception:  # noqa: BLE001
        return None


def generate_return_id() -> str:
    """Short, human-friendly return id, e.g. RET-250523-AB12CD."""
    stamp = datetime.now().strftime("%y%m%d")
    return f"RET-{stamp}-{uuid.uuid4().hex[:6].upper()}"


def _resolve_user_name(user_id: Optional[str]) -> Optional[str]:
    """Best-effort display name (full_name -> username -> id) for a user id, so
    the return doc records the approver in NAMES not UUIDs. Fail-soft -> the id
    itself (or None when no id)."""
    if not user_id:
        return None
    db = _get_db()
    if db is None:
        return user_id
    try:
        u = db.get_collection("users").find_one(
            {"user_id": user_id}, {"_id": 0, "full_name": 1, "username": 1}
        )
    except Exception:  # noqa: BLE001
        return user_id
    if not u:
        return user_id
    return u.get("full_name") or u.get("username") or user_id


def _resolve_order(create: ReturnCreate) -> Optional[Dict[str, Any]]:
    """Look up the original order by id or number. Fail-soft -> None."""
    repo = get_order_repository()
    if repo is None:
        return None
    try:
        if create.order_id:
            found = repo.find_by_id(create.order_id)
            if found:
                return found
        if create.order_number:
            return repo.find_by_order_number(create.order_number)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RETURNS] order lookup failed: %s", exc)
    return None


def _order_line_index(order: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Index the original order's items by item_id and by product_id.

    Returns {"by_item": {item_id: line}, "by_product": {product_id: line}} so a
    return line can recover the rate it was billed at. by_product keeps the
    FIRST line per product (good enough to recover a GST rate, which is the
    same for every unit of a product). Fail-soft -> empty maps.
    """
    by_item: Dict[str, Dict[str, Any]] = {}
    by_product: Dict[str, Dict[str, Any]] = {}
    if not order:
        return {"by_item": by_item, "by_product": by_product}
    for line in order.get("items") or []:
        if not isinstance(line, dict):
            continue
        iid = line.get("item_id") or line.get("id")
        if iid and iid not in by_item:
            by_item[str(iid)] = line
        pid = line.get("product_id")
        if pid and pid not in by_product:
            by_product[str(pid)] = line
    return {"by_item": by_item, "by_product": by_product}


def _line_purchased_qty(line: Dict[str, Any]) -> float:
    """Purchased quantity on an original order line. Defensive -> 0 on bad input."""
    try:
        return float(line.get("quantity") or line.get("qty") or 0)
    except (TypeError, ValueError):
        return 0.0


def _resolve_original_line(
    ret_line: ReturnLine, idx: Dict[str, Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Resolve the ORIGINAL order line a return line refers to.

    Match order (most-specific first): by `order_item_id` against the order
    line's `item_id`, else by `product_id`. Returns the matched original line
    dict, or None when it cannot be resolved -- the caller rejects with 400 so
    we never refund against a line that wasn't actually sold.
    """
    by_item = idx["by_item"]
    by_product = idx["by_product"]
    if ret_line.order_item_id and str(ret_line.order_item_id) in by_item:
        return by_item[str(ret_line.order_item_id)]
    if ret_line.product_id and str(ret_line.product_id) in by_product:
        return by_product[str(ret_line.product_id)]
    return None


def _sum_prior_refunds(order_id: Optional[str]) -> float:
    """Total net_refund already issued for an order across COMPLETED returns
    (BUG-096 cumulative monetary cap). Fail-soft -> 0.0 when unavailable."""
    if not order_id:
        return 0.0
    coll = _returns_coll()
    if coll is None:
        return 0.0
    total = 0.0
    try:
        for doc in coll.find({"order_id": order_id}, {"_id": 0}):
            if doc.get("status") != "COMPLETED":
                continue
            net = doc.get("net_refund")
            if net is not None:
                try:
                    total += float(net)
                except (TypeError, ValueError):
                    pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("[RETURNS] _sum_prior_refunds lookup failed: %s", exc)
    return round(total, 2)


def _order_billed_total(order: Optional[Dict[str, Any]]) -> float:
    """Best-effort billed value of an order -- the refund ceiling FALLBACK for a
    legacy order that predates amount_paid tracking (a current order always carries
    amount_paid, set at create and incremented on payment). Prefers grand_total /
    total, else sums the line totals (item_total, or unit_price*quantity)."""
    if not order:
        return 0.0
    for k in ("grand_total", "total", "total_amount", "grandTotal"):
        v = order.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    total = 0.0
    for li in order.get("items") or []:
        line = None
        for k in ("item_total", "item_subtotal", "subtotal", "line_total"):
            if li.get(k) is not None:
                line = li.get(k)
                break
        if line is None:
            up = li.get("unit_price")
            qty = li.get("quantity", 1)
            if up is not None:
                try:
                    line = float(up) * float(qty or 1)
                except (TypeError, ValueError):
                    line = None
        if line is not None:
            try:
                total += float(line)
            except (TypeError, ValueError):
                pass
    return round(total, 2)


def _already_returned_qty(
    order_id: Optional[str],
    item_id: Optional[str],
    product_id: Optional[str],
) -> float:
    """Sum the quantities ALREADY returned for one (order, line) across the
    `returns` collection.

    A line is identified by its original order `item_id` when known, otherwise
    by `product_id`. We scan completed return docs for the same order and add up
    the `return_qty` of every prior return line that targets the same line. This
    is the human-facing cumulative cap (clear 400) and also works when the DB
    has no atomic find_one_and_update. Fail-soft -> 0.0 when the returns
    collection is unavailable (the atomic order-line claim is the second guard).
    """
    if not order_id:
        return 0.0
    coll = _returns_coll()
    if coll is None:
        return 0.0
    total = 0.0
    try:
        for doc in coll.find({"order_id": order_id}, {"_id": 0}):
            for prior in doc.get("items") or []:
                if not isinstance(prior, dict):
                    continue
                # Prefer item-level identity; fall back to product identity so a
                # legacy return recorded without order_item_id still counts.
                p_item = prior.get("order_item_id")
                p_prod = prior.get("product_id")
                if item_id and p_item:
                    same_line = str(p_item) == str(item_id)
                elif product_id and p_prod:
                    same_line = str(p_prod) == str(product_id)
                else:
                    same_line = False
                if not same_line:
                    continue
                try:
                    total += float(prior.get("return_qty") or 0)
                except (TypeError, ValueError):
                    continue
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RETURNS] already-returned scan failed: %s", exc)
        return 0.0
    return round(total, 4)


def _orders_coll():
    """Raw `orders` collection for the atomic per-line returnable-qty claim.

    Prefers the order repository's bound collection (so tests that patch the
    repo share the same fake), falling back to the DB handle. None when neither
    is available -> the caller relies on the pre-validation scan only.
    """
    repo = get_order_repository()
    coll = getattr(repo, "collection", None) if repo is not None else None
    if coll is not None:
        return coll
    db = _get_db()
    if db is None:
        return None
    try:
        return db.get_collection("orders")
    except Exception:  # noqa: BLE001
        return None


def _claim_returnable_qty(
    order_id: Optional[str],
    orig_line: Dict[str, Any],
    return_qty: float,
) -> bool:
    """Atomically reserve `return_qty` units against an order line's remaining
    returnable quantity -- the same guard-in-the-filter pattern as the voucher
    redeem.

    The filter matches the order doc only when the targeted array element still
    has enough un-returned units left: returned_qty(default 0) <= purchased -
    return_qty. The SAME write increments that element's `returned_qty` by
    `return_qty` via the positional `$`. Two concurrent returns of the same last
    unit cannot both match the filter, so neither can drive returned_qty past
    purchased -> no repeatable / double refund.

    Identity uses the line's `item_id` when present (exact element), else
    `product_id` (first line for that product). `$elemMatch` keeps the filter
    predicate and the positional `$inc` on the SAME element.

    Returns True when the claim succeeded, False on no-match (already returned /
    over-cap / concurrent loser). Fail-soft: returns True when no orders
    collection is available, or the driver lacks find_one_and_update, so the
    pre-validation scan stays the guard rather than blocking a valid return.
    """
    if not order_id or return_qty <= 0:
        return True
    coll = _orders_coll()
    if coll is None:
        return True
    if not hasattr(coll, "find_one_and_update"):
        return True

    item_id = orig_line.get("item_id") or orig_line.get("id")
    product_id = orig_line.get("product_id")
    purchased = _line_purchased_qty(orig_line)
    # Remaining-returnable cap: an element is claimable only if the units already
    # returned leave room for this return_qty.
    cap = round(purchased - return_qty, 4)

    if item_id:
        elem: Dict[str, Any] = {"item_id": item_id}
    else:
        elem = {"product_id": product_id}
    # returned_qty may be absent on legacy lines; treat missing as 0 by matching
    # either "<= cap" or "field absent" (only valid when cap >= 0).
    if cap >= 0:
        elem["$or"] = [
            {"returned_qty": {"$lte": cap}},
            {"returned_qty": {"$exists": False}},
            {"returned_qty": None},
        ]
    else:
        # cap < 0 means even a single unit over-returns -> never claimable.
        elem["returned_qty"] = {"$lt": -1}  # impossible: forces no-match

    match = {"order_id": order_id, "items": {"$elemMatch": elem}}
    update = {"$inc": {"items.$.returned_qty": return_qty}}
    try:
        updated = coll.find_one_and_update(
            match, update, return_document=ReturnDocument.AFTER
        )
    except Exception as exc:  # noqa: BLE001
        # Driver lacks positional update / find_one_and_update filter support ->
        # fall back to the pre-validation scan rather than block the return.
        logger.warning("[RETURNS] returnable-qty claim errored: %s", exc)
        return True
    return updated is not None


def _release_returnable_qty(
    order_id: Optional[str],
    orig_line: Dict[str, Any],
    return_qty: float,
) -> None:
    """Undo a successful _claim_returnable_qty (decrement the element's
    returned_qty) when a later step of the SAME request fails and we must not
    leave a phantom reservation. Best-effort + fail-soft -> never raises."""
    if not order_id or return_qty <= 0:
        return
    coll = _orders_coll()
    if coll is None or not hasattr(coll, "find_one_and_update"):
        return
    item_id = orig_line.get("item_id") or orig_line.get("id")
    product_id = orig_line.get("product_id")
    elem: Dict[str, Any] = (
        {"item_id": item_id} if item_id else {"product_id": product_id}
    )
    try:
        coll.find_one_and_update(
            {"order_id": order_id, "items": {"$elemMatch": elem}},
            {"$inc": {"items.$.returned_qty": -return_qty}},
            return_document=ReturnDocument.AFTER,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RETURNS] returnable-qty release failed: %s", exc)


def _billed_unit_gross(line: Dict[str, Any]) -> Optional[float]:
    """Per-unit GST-INCLUSIVE gross a customer was actually billed for a line.

    Uses the stored (taxable_value + tax_amount) / quantity -- the real billed
    gross. This is correct for BOTH inclusive orders (taxable = gross/(1+rate),
    tax = gross - taxable -> sum = gross) AND legacy exclusive orders (taxable =
    net, tax = net*rate -> sum = grossed-up amount the customer paid). Returns
    None when those fields / a positive qty are absent so the caller can fall
    back. Defensive: never raises.
    """
    tv = line.get("taxable_value")
    tx = line.get("tax_amount")
    if tv is None or tx is None:
        return None
    try:
        qty = float(line.get("quantity") or 0)
        gross_line = float(tv) + float(tx)
    except (TypeError, ValueError):
        return None
    if qty <= 0:
        return None
    return round(gross_line / qty, 2)


def _priced_return_lines(
    lines: List[ReturnLine], order: Optional[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Build engine-ready dicts carrying the GST-INCLUSIVE gross `unit_price`
    the customer was billed for each line plus the `gst_rate` it was billed at.

    `unit_price` resolution (most authoritative first):
      1. the matching ORIGINAL order line's stored billed gross,
         (taxable_value + tax_amount) / quantity -- correct for inclusive AND
         legacy exclusive orders, so the refund always matches what was paid;
      2. legacy fallback (older orders with no stored taxable/tax): the till's
         NET unit_price grossed up by the resolved rate (preserves the #140
         under-refund fix);
      3. the till's unit_price unchanged (no order line / no rate).

    `gst_rate` resolution: original order line's rate, else the till's rate,
    else 0. It is used ONLY to back the tax OUT of the gross for the credit
    note -- `returns_engine` no longer grosses unit_price up (it is inclusive).

    Returns one dict per line carrying unit_price, gst_rate, return_qty, plus
    the identity/restock fields the rest of the flow needs.
    """
    idx = _order_line_index(order)
    by_item = idx["by_item"]
    by_product = idx["by_product"]
    out: List[Dict[str, Any]] = []
    for ln in lines:
        d = ln.model_dump()
        rate: Optional[float] = None
        orig = None
        if ln.order_item_id and str(ln.order_item_id) in by_item:
            orig = by_item[str(ln.order_item_id)]
        elif ln.product_id and str(ln.product_id) in by_product:
            orig = by_product[str(ln.product_id)]
        billed_unit: Optional[float] = None
        if orig is not None:
            orig_rate = orig.get("gst_rate")
            if orig_rate is not None:
                rate = float(orig_rate)
            billed_unit = _billed_unit_gross(orig)
        if rate is None and ln.gst_rate is not None:
            rate = float(ln.gst_rate)
        if billed_unit is not None:
            # Authoritative: the gross actually billed for this unit.
            d["unit_price"] = billed_unit
        elif rate:
            # Legacy order with no stored taxable/tax -> gross the till's NET
            # unit_price up by the rate so we still refund the full amount.
            try:
                d["unit_price"] = round(
                    float(d.get("unit_price") or 0.0) * (1.0 + rate / 100.0), 2
                )
            except (TypeError, ValueError):
                pass
        d["gst_rate"] = rate if rate is not None else 0.0
        out.append(d)
    return out


def _order_payment_method(order: Optional[Dict[str, Any]]) -> str:
    """Best-effort original payment method for defaulting a refund method."""
    if not order:
        return "SOURCE"
    for key in ("payment_method", "payment_mode", "paymentMode"):
        val = order.get(key)
        if val:
            return str(val)
    payments = order.get("payments") or []
    if isinstance(payments, list) and payments:
        first = payments[0] or {}
        val = first.get("method") or first.get("mode") or first.get("payment_method")
        if val:
            return str(val)
    return "SOURCE"


def _current_credit_balance(customer_id: str, customer_doc: Optional[dict]) -> float:
    """Ledger is authoritative once it has entries; otherwise bridge from the
    legacy customer.store_credit number (same rule as the customers router)."""
    coll = _ledger_coll()
    if coll is not None:
        try:
            entries = list(coll.find({"customer_id": customer_id}, {"_id": 0}))
            if entries:
                return scl.compute_balance(entries)
        except Exception:  # noqa: BLE001
            pass
    return float((customer_doc or {}).get("store_credit", 0) or 0)


def _issue_store_credit(
    customer_id: str,
    amount: float,
    reason: str,
    ref: str,
    current_user: dict,
    gross: Optional[float] = None,
    restocking_fee: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Append an ISSUED ledger entry + bump the customer's running balance.

    Reuses services.store_credit_ledger.make_entry and the same persistence
    shape as customers.py. `amount` is the NET credit issued; when a credit
    note carries a restocking fee, `gross` + `restocking_fee` are stamped on
    the persisted ledger row so the GST-inclusive gross, the fee, and the net
    are all auditable from the ledger alone. Fully fail-soft: returns None (and
    never raises) when the DB is absent or anything goes wrong, so a return is
    still recorded even if the credit ledger write fails.
    """
    if not customer_id or amount <= 0:
        return None

    repo = get_customer_repository()
    customer_doc = None
    if repo is not None:
        try:
            customer_doc = repo.find_by_id(customer_id)
        except Exception:  # noqa: BLE001
            customer_doc = None

    balance = _current_credit_balance(customer_id, customer_doc)
    try:
        entry = scl.make_entry(
            customer_id=customer_id,
            entry_type=scl.ISSUED,
            amount=amount,
            current_balance=balance,
            reason=reason,
            ref=ref,
            store_id=current_user.get("active_store_id"),
            user_id=current_user.get("user_id"),
        )
    except ValueError as exc:
        logger.warning("[RETURNS] credit entry rejected: %s", exc)
        return None

    # Stamp the gross / fee / net trail onto the ledger row for the credit note.
    if gross is not None:
        entry["gross_refund"] = round(float(gross), 2)
    if restocking_fee is not None:
        entry["restocking_fee"] = round(float(restocking_fee), 2)
    entry["net_refund"] = round(float(amount), 2)

    coll = _ledger_coll()
    if coll is not None:
        try:
            coll.insert_one(dict(entry))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RETURNS] credit ledger insert failed: %s", exc)
    # Keep the legacy customer.store_credit number in sync so the rest of the
    # app (POS redeem, customer card) sees the new balance.
    if repo is not None:
        try:
            repo.update(customer_id, {"store_credit": entry["balance_after"]})
        except Exception:  # noqa: BLE001
            pass
    entry.pop("_id", None)
    return entry


def _reactivate_original_unit(
    stock_repo: Any,
    product_id: str,
    store_id: Optional[str],
    order_id: Optional[str],
    used_ids: set,
) -> Optional[str]:
    """Find the original serialized unit sold for this product and flip it back
    to AVAILABLE. Returns its stock_id, or None when no candidate is found.

    Preference order (most-specific first):
      1. a unit for (product_id, store_id) tied to THIS order_id with status
         SOLD - i.e. the exact unit that was sold on this order;
      2. any SOLD unit for (product_id, store_id).

    IMPORTANT: only status=="SOLD" units are eligible. Earlier code used
    `$ne: AVAILABLE`, which would happily resurrect DAMAGED / SCRAPPED /
    TRANSFERRED / RETURNED units back onto the sellable shelf. Returns are
    strictly an "undo" of a sale - other statuses must stay where they are.

    A unit already reactivated earlier in this same return (`used_ids`) is
    skipped so two returned units never collapse onto one stock row.
    """
    if stock_repo is None or not product_id:
        return None

    candidates: List[Dict[str, Any]] = []
    base = {"product_id": product_id}
    if store_id:
        base["store_id"] = store_id
    sold_only = {"status": "SOLD"}

    try:
        if order_id:
            q = dict(base)
            q["order_id"] = order_id
            q.update(sold_only)
            candidates = stock_repo.find_many(q) or []
        if not candidates:
            q = dict(base)
            q.update(sold_only)
            candidates = stock_repo.find_many(q) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RETURNS] stock lookup failed: %s", exc)
        return None

    for unit in candidates:
        sid = unit.get("stock_id") or unit.get("_id")
        if not sid or sid in used_ids:
            continue
        # P1-C CLEAN RESALE LINEAGE: the unit is going BACK on the sellable
        # shelf, so it must NOT keep the attribution of the sale it was returned
        # from. Strip the stale order_id / sold_at / sold_to_customer_id (else an
        # AVAILABLE unit still "answers to" the refunded order, and reconciliation
        # / warranty-by-serial / per-order unit counts read it as belonging to a
        # sale it no longer does). We stamp returned_from_order_id (a forward
        # audit link to the reversed sale) + prior_sold_order_id (the prior value)
        # so the history is preserved; a later sale (claim_one_available) then
        # stamps the NEW order_id onto a clean unit.
        prior_order_id = unit.get("order_id") or order_id
        try:
            ok = stock_repo.update(
                sid,
                {
                    "status": "AVAILABLE",
                    "returned_at": datetime.now().isoformat(),
                    "reserved_at": None,
                    # Clear the stale sale attribution.
                    "order_id": None,
                    "sold_at": None,
                    "sold_to_customer_id": None,
                    # Preserve the lineage for audit / reconciliation.
                    "returned_from_order_id": prior_order_id,
                    "prior_sold_order_id": prior_order_id,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RETURNS] stock reactivate failed: %s", exc)
            continue
        if ok:
            used_ids.add(sid)
            return str(sid)
    return None


def _audit_stock_transition(
    stock_id: str,
    prior_status: Optional[str],
    new_status: str,
    return_id: str,
    store_id: Optional[str],
    user_id: Optional[str],
) -> None:
    """Cheap insert into the stock_audit collection on every unit status flip
    driven by a return. Async-fail-soft: any error logged + swallowed so the
    restock flow can never break on the audit-row insert.

    The audit row is the only place we can answer "which unit moved when, and
    why" after the fact - the stock_units doc itself only holds the LATEST
    status. Three transitions are emitted from the returns flow:
      * status: SOLD     -> AVAILABLE   (reactivate original sold unit)
      * status: <absent> -> AVAILABLE   (mint a fresh unit from a return)
      * status: RESERVED -> AVAILABLE   (not currently driven from here, but
                                         the shape supports it).
    """
    if not stock_id:
        return
    try:
        from ..dependencies import get_db

        db = get_db()
        if db is None or not getattr(db, "is_connected", False):
            return
        coll = db.db.get_collection("stock_audit")
        if coll is None:
            return
        coll.insert_one(
            {
                "stock_id": str(stock_id),
                "prior_status": prior_status,
                "new_status": new_status,
                "source": "RETURN_RESTOCK",
                "return_id": return_id,
                "store_id": store_id,
                "by_user": user_id,
                "at": datetime.now().isoformat(),
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[RETURNS] stock_audit insert skipped: %s", exc)


def _restock_good_items(
    items: List[ReturnLine],
    store_id: Optional[str],
    return_id: str,
    order_id: Optional[str] = None,
    already_applied: bool = False,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Put GOOD-condition returned units BACK into sellable serialized stock.

    Real per-store on-hand is the serialized `stock` collection (one row per
    physical unit - see inventory.py + get_stock_repository), NOT the
    product-level `stock_quantity` aggregate. For each resellable unit we:
      * RE-ACTIVATE the original serialized unit (status -> AVAILABLE) if we can
        find the SOLD/RETURNED row that left when it was sold; OTHERWISE
      * MINT a fresh AVAILABLE unit {quantity:1, source_type:"RETURN",
        source_id:<return_id>}.

    Idempotent: pass already_applied=True (when the return doc was restocked
    before) and this is a no-op. Fully fail-soft: any stock-write failure is
    logged and leaves applied=False on the return so it can be retried, never
    breaking the return record. Damaged / opened / opted-out units are skipped
    and listed under `skipped`.

    Returns a dict with `restocked` (per-line summary for the return doc),
    `restock_stock_ids` (every stock row touched), `applied`, and `skipped`.
    """
    plan = restock_engine.plan_restock(
        [it.model_dump() for it in (items or [])],
        already_applied=already_applied,
    )

    result: Dict[str, Any] = {
        "restocked": [],
        "restock_stock_ids": [],
        "applied": False,
        "skipped": plan.get("skipped", []),
    }
    if plan.get("already_applied"):
        result["applied"] = True
        return result

    units = plan.get("units", [])
    if not units:
        # Nothing resellable -> trivially "applied" (no work left to retry).
        result["applied"] = True
        return result

    stock_repo = None
    try:
        stock_repo = get_stock_repository()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RETURNS] stock repo unavailable: %s", exc)

    # No DB / stock repo -> record intent only, leave applied=False to retry.
    if stock_repo is None:
        per_line: Dict[str, Dict[str, Any]] = {}
        for u in units:
            pid = u.get("product_id")
            row = per_line.setdefault(
                pid,
                {
                    "product_id": pid,
                    "sku": u.get("sku", ""),
                    "product_name": u.get("product_name", ""),
                    "quantity": 0,
                    "applied": False,
                },
            )
            row["quantity"] += 1
        result["restocked"] = list(per_line.values())
        result["applied"] = False
        return result

    # We have a stock repo - actually re-add each unit.
    used_ids: set = set()
    per_line_applied: Dict[str, Dict[str, Any]] = {}
    all_ok = True
    for u in units:
        pid = u.get("product_id")
        row = per_line_applied.setdefault(
            pid,
            {
                "product_id": pid,
                "sku": u.get("sku", ""),
                "product_name": u.get("product_name", ""),
                "quantity": 0,
                "reactivated": 0,
                "minted": 0,
                "applied": True,
            },
        )
        sid = _reactivate_original_unit(stock_repo, pid, store_id, order_id, used_ids)
        if sid:
            row["reactivated"] += 1
            row["quantity"] += 1
            result["restock_stock_ids"].append(sid)
            _audit_stock_transition(
                stock_id=sid,
                prior_status="SOLD",
                new_status="AVAILABLE",
                return_id=return_id,
                store_id=store_id,
                user_id=user_id,
            )
            continue
        # Mint a fresh AVAILABLE serialized unit.
        # P1-C: stamp returned_from_order_id (the sale this return reverses) so
        # the minted unit has a forward audit link to its origin -- without it a
        # fresh return-unit has NO connection back to the reversed sale. It
        # carries NO order_id / sold_at (it is unsold shelf stock); a later sale
        # stamps the new order_id cleanly.
        try:
            created = stock_repo.create(
                {
                    "store_id": store_id,
                    "product_id": pid,
                    "quantity": 1,
                    "status": "AVAILABLE",
                    "source_type": "RETURN",
                    "source_id": return_id,
                    "returned_from_order_id": order_id,
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RETURNS] stock mint failed: %s", exc)
            created = None
        if created:
            new_id = created.get("stock_id") or created.get("_id")
            row["minted"] += 1
            row["quantity"] += 1
            if new_id:
                result["restock_stock_ids"].append(str(new_id))
                _audit_stock_transition(
                    stock_id=str(new_id),
                    prior_status=None,
                    new_status="AVAILABLE",
                    return_id=return_id,
                    store_id=store_id,
                    user_id=user_id,
                )
        else:
            all_ok = False
            row["applied"] = False

    result["restocked"] = list(per_line_applied.values())
    # Applied only if every unit landed somewhere; otherwise leave False so a
    # later retry can finish the job (the stock_ids already added are recorded).
    result["applied"] = all_ok
    return result


def _reason_summary(items: List[ReturnLine]) -> str:
    """Distinct reasons across the returned lines, comma-joined."""
    seen: List[str] = []
    for it in items or []:
        if it.return_qty > 0 and it.reason and it.reason not in seen:
            seen.append(it.reason)
    return ", ".join(seen)


def _matched_unit_serial(stock_repo, product_id, store_id, order_id) -> Optional[str]:
    """Best-effort lookup of the serial on the SOLD stock unit a return line
    refers to. Mirrors `_reactivate_original_unit`'s most-specific-first search
    (order_id+product+store SOLD, then product+store SOLD). Returns the unit's
    `serial` or None. Pure read, fail-soft -> None."""
    if stock_repo is None or not product_id:
        return None
    base: Dict[str, Any] = {"product_id": product_id, "status": "SOLD"}
    if store_id:
        base["store_id"] = store_id
    try:
        candidates: List[Dict[str, Any]] = []
        if order_id:
            q = dict(base)
            q["order_id"] = order_id
            candidates = stock_repo.find_many(q) or []
        if not candidates:
            candidates = stock_repo.find_many(dict(base)) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RETURNS] serial-match lookup failed: %s", exc)
        return None
    for unit in candidates:
        serial = unit.get("serial")
        if serial:
            return str(serial).strip().upper()
    return None


def _consume_serial_override(body: "ReturnCreate", current_user: dict) -> bool:
    """Try to consume a manager-approved RETURN_SERIAL_OVERRIDE approval (E4).

    Returns True only when the E4 engine atomically consumes a valid, single-
    use, not-expired approval of the right action_type. SAFE DEFAULT: any
    missing token, missing engine, or error -> False (the mismatch stays
    blocked). E3w wires the EXISTING E4 `consume_approval` -- it does NOT
    reimplement approvals.

    TODO(E4-FE / #25-#27): expose the approval-request + PIN-approve flow in the
    POS returns UI so the till can mint this token; until then a manager mints
    it via POST /approvals/requests + /approvals/requests/{id}/approve."""
    token = (body.serial_mismatch_override_token or "").strip() or None
    request_id = (body.serial_mismatch_override_request_id or "").strip() or None
    if not token and not request_id:
        return False
    try:
        from ..services.approvals import ApprovalEngine

        engine = ApprovalEngine(db=_get_db())
        res = engine.consume_approval(
            consumed_by=current_user.get("user_id") or "",
            action_type="RETURN_SERIAL_OVERRIDE",
            request_id=request_id,
            approval_token=token,
        )
        return bool(res.get("ok"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RETURNS] serial-override consume failed: %s", exc)
        return False


def _gate_refund_approval_matrix(
    body: "ReturnCreate",
    *,
    net_amount: float,
    store_id: Optional[str],
    entity_id: Optional[str],
    resolved_order_id: Optional[str],
    current_user: dict,
) -> Optional[str]:
    """F27: enforce the configurable refund approval matrix in FRONT of recording
    the refund. PURE GATE -- it changes NO money math; it only decides whether a
    consumed E4 approval token is required and verifies it.

    DARK by default: when the matrix is disabled for this scope (the flag default
    is False) required_tier_for_refund returns None and this is a no-op -- the
    refund path is byte-identical to today.

    When the matrix is ENABLED and the resolved tier is > 0 the caller MUST supply
    a refund_approval_token / _request_id that resolves to a valid, single-use,
    not-expired E4 approval of action_type REFUND_APPROVAL_MATRIX that is bound to
    THIS refund -- the approval's store_id must match and its context.order_id must
    match this order, and its approved amount must be >= this net refund. A
    missing / mismatched / expired token -> HTTPException(403). Separation of
    duties (requester != approver) is enforced at approve-time by the E4 engine
    (REFUND_APPROVAL_MATRIX is a maker-checker action).

    Returns a dict ``{"approval_by", "approval_request_id", "approval_status"}``
    describing the consumed approval (for stamping the return doc), or None when
    no approval was required (gate dark / below floor). ``approval_status`` is
    always "APPROVED" here because the gate only proceeds past a SUCCESSFUL
    consume; a missing / invalid token raises 403 above.
    """
    role = (current_user.get("activeRole")
            or (current_user.get("roles") or [None])[0])
    reason = (body.refund_reason
              or next((it.reason for it in body.items
                       if it.return_qty > 0 and it.reason), None))
    try:
        from ..services.refund_approval_matrix import required_tier_for_refund

        tier = required_tier_for_refund(
            int(round(float(net_amount) * 100)),  # rupees -> integer paise
            reason,
            role,
            store_id=store_id,
            entity_id=entity_id,
        )
    except Exception as exc:  # noqa: BLE001 - fail OPEN (no gate) on a resolver error
        logger.warning("[RETURNS] F27 tier resolve failed; no gate: %s", exc)
        return None

    if tier is None:
        return None  # gate dark / below the role floor -> no approval required

    token = (body.refund_approval_token or "").strip() or None
    request_id = (body.refund_approval_request_id or "").strip() or None
    if not token and not request_id:
        raise HTTPException(
            status_code=403,
            detail={
                "reason": "REFUND_APPROVAL_REQUIRED",
                "required_tier": tier,
                "message": (
                    "This refund requires a tiered approval. A manager must "
                    "PIN-approve an approval request, then re-submit the return "
                    "with the approval token."
                ),
            },
        )

    try:
        from ..services.approvals import ApprovalEngine

        engine = ApprovalEngine(db=_get_db())
        res = engine.consume_approval(
            consumed_by=current_user.get("user_id") or "",
            action_type="REFUND_APPROVAL_MATRIX",
            request_id=request_id,
            approval_token=token,
            amount=float(net_amount),  # token's approved amount must be >= this refund
            expected_store_id=store_id,
            expected_context={"order_id": resolved_order_id} if resolved_order_id else None,
            min_tier=tier,  # F27: the token's approver tier must MEET the matrix tier
        )
    except Exception as exc:  # noqa: BLE001 - a consume error must NOT let the refund through
        logger.warning("[RETURNS] F27 approval consume failed: %s", exc)
        raise HTTPException(
            status_code=403,
            detail={"reason": "REFUND_APPROVAL_INVALID",
                    "message": "Could not validate the refund approval token."},
        )

    if not res.get("ok"):
        raise HTTPException(
            status_code=403,
            detail={
                "reason": "REFUND_APPROVAL_INVALID",
                "error": res.get("error"),
                "required_tier": tier,
                "message": (
                    "The refund approval token is invalid, expired, already "
                    "used, or bound to a different refund."
                ),
            },
        )
    consumed = res.get("request") or {}
    return {
        "approval_by": (consumed.get("reviewed_by") or current_user.get("user_id")),
        "approval_request_id": (consumed.get("request_id") or request_id),
        "approval_status": "APPROVED",
    }


def _normalize_tender(method: Optional[str]) -> str:
    """Canonicalise a payment/refund method for original-tender comparison.
    Folds the common card synonyms together (CARD == CREDIT_CARD == DEBIT_CARD)
    and upper-cases so a cosmetic-case difference never trips the hard-lock."""
    m = (method or "").strip().upper()
    if not m:
        return ""
    if m in ("CARD", "CREDIT_CARD", "DEBIT_CARD", "CREDITCARD", "DEBITCARD"):
        return "CARD"
    if m in ("BANK", "BANK_TRANSFER", "NEFT", "IMPS", "RTGS"):
        return "BANK_TRANSFER"
    return m


def _gate_original_tender(
    body: "ReturnCreate",
    *,
    order: Optional[Dict[str, Any]],
    store_id: Optional[str],
    entity_id: Optional[str],
) -> None:
    """F27 / DECISIONS sec 6: refunds always go back to the ORIGINAL tender.

    When ``refund.original_tender_enforce`` is ON (default True) for this scope
    and the cashier supplied a ``refund_method`` that differs from the order's
    original payment method, reject with 422 TENDER_MISMATCH -- a card sale can
    never be rerouted to a cash refund. Only applies to RETURN (an EXCHANGE/
    CREDIT_NOTE settles to store credit by design). PERMISSIVE / fail-soft: no
    user-chosen method, an unknown original tender (``SOURCE``), or a policy
    read error -> no block (the refund still DEFAULTS to the original method
    downstream). Turn the flag OFF to permit an explicit tender override.
    """
    if body.return_type != "RETURN":
        return
    chosen = (body.refund_method or "").strip()
    if not chosen:
        return  # no override attempted -> defaults to original tender downstream
    try:
        from ..services.policy_engine import get_policy

        scope: Dict[str, Any] = {}
        if store_id:
            scope["store_id"] = store_id
        if entity_id:
            scope["entity_id"] = entity_id
        enforce = bool(get_policy(
            "refund.original_tender_enforce", scope or None, default=True
        ))
    except Exception as exc:  # noqa: BLE001 - fail OPEN (no block) on a policy error
        logger.warning("[RETURNS] F27 tender-enforce policy read failed; no block: %s", exc)
        return
    if not enforce:
        return  # override explicitly permitted by policy

    original = _order_payment_method(order)
    if _normalize_tender(original) == "SOURCE" or not original:
        return  # original tender unknown -> cannot enforce, stay permissive
    if _normalize_tender(chosen) != _normalize_tender(original):
        raise HTTPException(
            status_code=422,
            detail={
                "reason": "TENDER_MISMATCH",
                "original_tender": original,
                "requested_method": chosen,
                "message": (
                    f"Refunds must go back to the original tender "
                    f"({original}). This cannot be changed while "
                    f"'Refund to original tender' is enforced."
                ),
            },
        )


def _guard_return_serial_mismatch(resolved_lines, body: "ReturnCreate",
                                  resolved_order_id, store_id, current_user):
    """E3 acceptance #8 (return half): hard-block a serial-mismatched return.

    For each resolved return line that carries a scanned `serial`, compare it to
    the matched SOLD stock unit's `serial`. A MISMATCH -> HTTP 409 with
    `reason=SERIAL_MISMATCH`, UNLESS a manager override is consumed (then the
    return proceeds and `override_by` is recorded on the response of the caller).

    PERMISSIVE: when EITHER serial is absent the check is skipped -- a legitimate
    return is never blocked on missing / dirty serial data. Returns the override
    actor id when an override was consumed (so the caller can stamp it), else
    None. Raises HTTPException(409) on an un-overridden mismatch."""
    # No serialized return line carries a serial -> nothing to compare; skip
    # before touching the stock repo (the common case, fully permissive).
    if not any((getattr(e["ret_line"], "serial", None) or "").strip()
               for e in resolved_lines):
        return None
    try:
        stock_repo = get_stock_repository()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RETURNS] serial-guard stock repo unavailable: %s", exc)
        return None  # cannot compare -> permissive
    if stock_repo is None:
        return None  # no serialized stock to compare against -> permissive
    override_consumed: Optional[bool] = None  # lazy: only consume once if needed
    override_by: Optional[str] = None
    for entry in resolved_lines:
        ret_line = entry["ret_line"]
        orig_line = entry["orig_line"]
        scanned = (getattr(ret_line, "serial", None) or "").strip().upper()
        if not scanned:
            continue  # no till serial -> skip (permissive)
        product_id = orig_line.get("product_id") or ret_line.product_id
        unit_serial = _matched_unit_serial(
            stock_repo, product_id, store_id, resolved_order_id
        )
        if not unit_serial:
            continue  # no recorded serial -> skip (permissive)
        if scanned == unit_serial:
            continue  # match -> fine
        # MISMATCH. Allow ONLY a consumed manager override.
        if override_consumed is None:
            override_consumed = _consume_serial_override(body, current_user)
            if override_consumed:
                override_by = current_user.get("user_id")
        if not override_consumed:
            raise HTTPException(
                status_code=409,
                detail={
                    "reason": "SERIAL_MISMATCH",
                    "message": (
                        "Scanned serial does not match the sold unit's serial. "
                        "A manager override is required to proceed."
                    ),
                    "product_id": product_id,
                    "scanned_serial": scanned,
                    "expected_serial": unit_serial,
                },
            )
    return override_by


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.post("", status_code=201)
@router.post("/", status_code=201)
async def create_return(
    body: ReturnCreate = Body(...),
    current_user: dict = Depends(require_roles(*_RETURN_ROLES)),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Create a customer return / exchange / credit-note.

    Money is RECORDED, never executed. Returns the new return_id plus the
    computed amounts for the chosen return_type.

    POS-14: supports an optional ``Idempotency-Key`` header. When a non-empty
    key is supplied and a return with that key already exists in the
    ``returns`` collection, the EXISTING return_id is returned without
    duplicating the financial record. Fail-soft: any lookup failure falls
    through to the normal (non-idempotent) path.
    """
    # POS-14: idempotency guard -- look for an existing return with this key.
    # isinstance guard: when this endpoint is called directly (unit tests), the
    # default is the FastAPI Header(...) object, not a str -- don't .strip() it.
    idem_key = idempotency_key.strip() if isinstance(idempotency_key, str) else ""
    if idem_key:
        try:
            coll = _returns_coll()
            if coll is not None:
                existing = coll.find_one({"idempotency_key": idem_key})
                if existing:
                    logger.info(
                        "[RETURNS] idempotency replay for key %s -> return %s",
                        idem_key,
                        existing.get("return_id"),
                    )
                    return {
                        "return_id": existing.get("return_id"),
                        "return_type": existing.get("return_type"),
                        "returned_value": existing.get("returned_value"),
                        "gross_refund": existing.get("gross_refund"),
                        "restocking_fee": existing.get("restocking_fee"),
                        "net_refund": existing.get("net_refund"),
                        "gst_breakup": existing.get("gst_breakup"),
                        "refund_amount": existing.get("refund_amount"),
                        "refund_method": existing.get("refund_method"),
                        "credit_amount": existing.get("credit_amount"),
                        "collect_amount": existing.get("collect_amount"),
                        "settlement": existing.get("settlement"),
                        "restocked": existing.get("restocked", []),
                        "restock_applied": existing.get("restock_applied", False),
                        "restock_stock_ids": existing.get("restock_stock_ids", []),
                        "message": "Return already recorded (idempotent replay)",
                        "_idempotent_replay": True,
                    }
        except Exception as _exc:  # noqa: BLE001
            logger.warning("[RETURNS] idempotency lookup skipped: %s", _exc)

    # Accounting period lock: cannot create returns in a closed month.
    # IST audit: lock against the IST business day, not the UTC day --
    # date.today() on Railway (UTC) is yesterday between 00:00-05:30 IST,
    # which falsely blocked returns for 5.5h on the 1st after a month-lock.
    db = _get_db()
    if db is not None:
        from .finance import check_period_locked
        from ..utils.ist import ist_today
        check_period_locked(db, ist_today())

    active_lines = [it for it in body.items if it.return_qty > 0]
    if not active_lines:
        raise HTTPException(
            status_code=400, detail="Select at least one item to return"
        )

    # IDOR guard (P1): the refund's store must be one the caller can access.
    # body.store_id was previously trusted as-is, letting a store-scoped role
    # book a refund into ANY store. Falls back to the caller's active store
    # when omitted (validate_store_access returns it); 403 otherwise.
    store_id = validate_store_access(body.store_id, current_user)

    order = _resolve_order(body)

    # 0. QUANTITY INTEGRITY (the over-refund guard). A return must trace to a
    #    real original sale line, and the returned qty can never exceed what is
    #    still returnable (purchased - already_returned). This blocks both the
    #    "return_qty 100 on a qty-1 line" over-refund AND the "submit the same
    #    return 3x" repeat over-refund. Applies to RETURN / CREDIT_NOTE /
    #    EXCHANGE alike. Two layers, mirroring the voucher redeem:
    #      (a) a pre-validation scan over prior `returns` -> clear 400 with the
    #          cumulative cap, and the only guard in DB-less / no-atomic mode;
    #      (b) an atomic find_one_and_update claim on the order line's remaining
    #          returnable qty -> closes the concurrent double-submit race.
    #    We must resolve the order to do (a)+(b); an unresolvable order or line
    #    is a hard 400 -- we never blind-refund.
    if order is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Original order could not be resolved -- a return must reference "
                "the order it is against (order_id or order_number)."
            ),
        )

    # IDOR guard (P1): the resolved order must belong to a store the caller can
    # access. _resolve_order fetches by id/number with no ownership check, so a
    # store-scoped role could previously create a refund against ANOTHER
    # store's sale. HQ roles (SUPERADMIN/ADMIN) pass via can_access_store_scoped.
    if not can_access_store_scoped(order.get("store_id"), current_user):
        raise HTTPException(
            status_code=403,
            detail="No access to the store this order belongs to.",
        )

    # BUG-096: only a completed sale is returnable. Reject a CANCELLED / DRAFT /
    # REFUNDED / VOID order -- you cannot refund a sale that never completed or was
    # already fully refunded. Legacy orders with no status field fall through to
    # the cumulative monetary cap below (the amount_paid guard catches never-paid).
    _ostatus = order.get("status") or order.get("orderStatus")
    if _ostatus in ("CANCELLED", "DRAFT", "REFUNDED", "VOID", "VOIDED"):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot create a return against an order with status "
                f"'{_ostatus}'. Only a confirmed / processing / ready / delivered "
                f"order is returnable."
            ),
        )

    line_idx = _order_line_index(order)
    resolved_order_id = body.order_id or order.get("order_id")

    # Resolve every line + validate the cumulative cap BEFORE touching money or
    # claiming anything, so a bad line rejects with nothing reserved. Resolution
    # is stashed for the atomic claim that runs only AFTER every 400/422 input
    # check passes (so a validation error never leaves a phantom reservation).
    resolved_lines: List[Dict[str, Any]] = []
    for ret_line in active_lines:
        orig_line = _resolve_original_line(ret_line, line_idx)
        if orig_line is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Return line does not match any line on the original order "
                    f"(order_item_id={ret_line.order_item_id!r}, "
                    f"product_id={ret_line.product_id!r}). Cannot refund an "
                    "item that was not on this sale."
                ),
            )
        purchased = _line_purchased_qty(orig_line)
        item_id = orig_line.get("item_id") or orig_line.get("id")
        product_id = orig_line.get("product_id")
        already = _already_returned_qty(resolved_order_id, item_id, product_id)
        remaining = round(purchased - already, 4)
        if ret_line.return_qty > remaining + 1e-9:
            name = ret_line.product_name or orig_line.get("product_name") or product_id
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Return quantity {ret_line.return_qty:g} exceeds the "
                    f"returnable quantity {remaining:g} for '{name}' "
                    f"(purchased {purchased:g}, already returned {already:g})."
                ),
            )
        resolved_lines.append({"ret_line": ret_line, "orig_line": orig_line})

    # 0b. SERIAL-MISMATCH HARD-BLOCK (E3 acceptance #8, return half). Runs after
    #     the quantity guard and BEFORE any money math / atomic claim, so a
    #     mismatched return rejects with nothing reserved. Permissive: skipped
    #     when either the scanned or the recorded serial is absent. A consumed
    #     manager override (E4) lets it proceed; `serial_override_by` is stamped
    #     on the return doc below.
    serial_override_by = _guard_return_serial_mismatch(
        resolved_lines, body, resolved_order_id, store_id, current_user
    )

    # 1. Money math (pure engine; validates negatives). returned_value is the
    #    GST-INCLUSIVE gross the customer paid: the original order line's
    #    `gst_rate` is recovered (see _priced_return_lines) and the NET
    #    unit_price grossed up by it. This fixes the under-refund bug where the
    #    bare net subtotal was returned, dropping the GST the customer paid.
    priced_lines = _priced_return_lines(active_lines, order)
    try:
        gross_refund = engine.returned_value(priced_lines)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Restocking fee: optional Rs deduction for damaged / opened goods. Only
    # meaningful on a refund (RETURN / CREDIT_NOTE); an EXCHANGE is a like-for-
    # like swap settled on the difference, so a fee there is ambiguous -> 422.
    restocking_fee = round(float(body.restocking_fee or 0.0), 2)
    if restocking_fee > 0 and body.return_type == "EXCHANGE":
        raise HTTPException(
            status_code=422,
            detail="restocking_fee does not apply to an EXCHANGE",
        )
    try:
        # net = gross - fee; raises if the fee exceeds the gross refund.
        net_amount = engine.net_refund(gross_refund, restocking_fee)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # BUG-096 cumulative monetary cap: the total net value refunded across ALL
    # COMPLETED returns for this order may never exceed what the customer actually
    # paid (order.amount_paid). Closes unlimited / repeatable over-refund (and a
    # refund on a never-paid order -> amount_paid 0 -> any refund rejected).
    # Checked HERE in the validation phase -- BEFORE the atomic qty claim below --
    # so a rejected over-refund never leaves a phantom reservation. 1-paisa epsilon.
    _ap = (order or {}).get("amount_paid")
    if _ap is None:
        # Legacy order with no payment tracking -> cap at the order's billed value
        # (still blocks unlimited over-refund). A CURRENT order always has
        # amount_paid present (0 == genuinely never paid -> any refund rejected).
        _refund_ceiling = _order_billed_total(order)
    else:
        _refund_ceiling = float(_ap or 0.0)
    _prior_refunds = _sum_prior_refunds(resolved_order_id)
    if round(_prior_refunds + net_amount, 2) > _refund_ceiling + 0.01:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Refund would exceed what is refundable on this order. "
                f"Cap (amount paid / order value): Rs {_refund_ceiling:.2f}; "
                f"already refunded: Rs {_prior_refunds:.2f}; this refund: Rs {net_amount:.2f}."
            ),
        )

    # F27 ORIGINAL-TENDER HARD-LOCK. Runs before recording (and before the matrix
    # gate) so a tender mismatch rejects with nothing reserved. PERMISSIVE: no-op
    # unless the cashier supplied a refund_method that differs from the order's
    # original tender AND the policy is enforced (default True) for this scope.
    _gate_original_tender(
        body,
        order=order,
        store_id=store_id,
        entity_id=(order or {}).get("entity_id"),
    )

    # F27 REFUND APPROVAL MATRIX GATE. Runs AFTER all money math + the over-refund
    # cap (so net_amount is final) and BEFORE any recording / atomic stock claim,
    # so a refund that needs approval rejects with nothing reserved. PURE GATE: it
    # changes NO money math -- it only requires + verifies a consumed E4 approval
    # token bound to this refund when the matrix is enabled and the tier is > 0.
    # DARK by default (flag off) -> no-op, refund path byte-identical to today.
    _refund_approval = _gate_refund_approval_matrix(
        body,
        net_amount=net_amount,
        store_id=store_id,
        entity_id=(order or {}).get("entity_id"),
        resolved_order_id=resolved_order_id,
        current_user=current_user,
    )
    refund_approval_by = (_refund_approval or {}).get("approval_by")
    refund_approval_request_id = (_refund_approval or {}).get("approval_request_id")
    refund_approval_status = (_refund_approval or {}).get("approval_status")
    # Resolve the approver's display name (NAMES not UUIDs in history). Fail-soft.
    refund_approval_by_name = _resolve_user_name(refund_approval_by)

    # Back the GST out of the gross for the credit note / GSTR-1 reversal. The
    # tax is INSIDE the gross (not added on top). Use the dominant rate across
    # the returned lines when they span rates.
    gst_view = engine.gst_breakup(gross_refund, engine.dominant_gst_rate(priced_lines))

    # `ret_value` carries the GST-inclusive gross for the response + doc
    # (preserves the existing field name; now correctly gross, not net).
    ret_value = gross_refund

    customer_id = body.customer_id or (order or {}).get("customer_id")
    customer_name = (
        (order or {}).get("customer_name") or (order or {}).get("customerName") or ""
    )

    return_id = generate_return_id()

    # 2. Type-specific recording.
    refund_amount: Optional[float] = None
    credit_amount: Optional[float] = None
    settlement: Optional[Dict[str, Any]] = None
    collect_amount: Optional[float] = None
    credit_entry: Optional[Dict[str, Any]] = None
    replacement_dump = [r.model_dump() for r in body.replacement_items]

    # For an EXCHANGE, compute (and validate) the settlement up front so its 400
    # fires BEFORE we reserve any returnable qty -- a validation error must never
    # leave a phantom reservation on the order line.
    if body.return_type == "EXCHANGE":
        try:
            settlement = engine.exchange_settlement(ret_value, replacement_dump)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    # 2b. ATOMIC QUANTITY CLAIM (the concurrency belt-and-suspenders, mirroring
    #     the voucher redeem guard). All 400/422 input validation has now passed,
    #     so this is the LAST thing that can fail before side effects. Each line
    #     reserves its returnable qty atomically; if any claim loses the race to
    #     a concurrent double-submit we release the ones already taken and reject
    #     (all-or-nothing) so a partial reservation never lingers, and so we
    #     never issue store credit / persist a return that lost the race.
    claimed: List[Dict[str, Any]] = []
    for rl in resolved_lines:
        ok = _claim_returnable_qty(
            resolved_order_id, rl["orig_line"], float(rl["ret_line"].return_qty)
        )
        if not ok:
            for done in claimed:
                _release_returnable_qty(
                    resolved_order_id,
                    done["orig_line"],
                    float(done["ret_line"].return_qty),
                )
            name = (
                rl["ret_line"].product_name
                or rl["orig_line"].get("product_name")
                or rl["orig_line"].get("product_id")
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Return for '{name}' could not be reserved -- it was just "
                    "returned by another transaction. Re-check the returnable "
                    "quantity and retry."
                ),
            )
        claimed.append(rl)

    # BUG-099: reverse loyalty for this return -- claw back points earned on the
    # original order + restore points redeemed on it. Fail-soft (never blocks the
    # return); a genuine failure flags the return doc for reconciliation. Walk-in /
    # no customer -> skip (walk-ins never earn loyalty).
    loyalty_reversal_failed = False
    _cust = customer_id
    if _cust and _cust != "walk-in" and not str(_cust).startswith("walkin-"):
        try:
            from .loyalty import reverse_for_return as _reverse_loyalty

            _lr = _reverse_loyalty(return_id, resolved_order_id, _cust)
            if _lr.get("ok"):
                logger.info(
                    "[RETURNS] loyalty reversed for %s: clawed=%s restored=%s",
                    return_id,
                    _lr.get("earned_clawed", 0),
                    _lr.get("redeemed_restored", 0),
                )
            elif _lr.get("reason") not in ("missing_ids", "loyalty_db_unavailable"):
                loyalty_reversal_failed = True
                logger.error(
                    "[RETURNS] loyalty reversal FAILED for %s: %s",
                    return_id,
                    _lr.get("reason"),
                )
        except Exception as exc:  # noqa: BLE001
            loyalty_reversal_failed = True
            logger.error(
                "[RETURNS] loyalty reversal exception for %s: %s", return_id, exc
            )

    if body.return_type == "RETURN":
        # Net of any restocking fee = the cash actually given back.
        refund_amount = net_amount
        refund_method = body.refund_method or _order_payment_method(order)

    elif body.return_type == "CREDIT_NOTE":
        credit_amount = net_amount
        refund_method = "STORE_CREDIT"
        credit_entry = _issue_store_credit(
            customer_id,
            net_amount,
            reason=f"Credit note for return {return_id}",
            ref=return_id,
            current_user=current_user,
            gross=gross_refund,
            restocking_fee=restocking_fee,
        )

    else:  # EXCHANGE (settlement already computed + validated above)
        refund_method = body.refund_method or _order_payment_method(order)
        if settlement["direction"] == engine.COLLECT:
            collect_amount = settlement["difference"]
        elif settlement["direction"] == engine.REFUND:
            # Refund the difference as store credit (recorded, not executed).
            credit_amount = settlement["difference"]
            credit_entry = _issue_store_credit(
                customer_id,
                settlement["difference"],
                reason=f"Exchange refund for return {return_id}",
                ref=return_id,
                current_user=current_user,
            )

    # 3. Restock resellable (GOOD) units back into serialized stock (fail-soft).
    #    (resolved_order_id already computed during the quantity-integrity guard.)
    restock_result: Dict[str, Any] = {
        "restocked": [],
        "restock_stock_ids": [],
        "applied": False,
        "skipped": [],
    }
    try:
        restock_result = _restock_good_items(
            active_lines,
            store_id,
            return_id,
            order_id=resolved_order_id,
            user_id=current_user.get("user_id"),
        )
    except Exception as exc:  # noqa: BLE001
        # A stock-write failure must never break the return record - leave
        # applied=False so it can be retried later.
        logger.warning("[RETURNS] restock failed (recorded, not applied): %s", exc)
    restocked = restock_result.get("restocked", [])
    restock_applied = bool(restock_result.get("applied"))
    restock_stock_ids = restock_result.get("restock_stock_ids", [])

    # Online oversell guard (council B11): a GOOD-condition return puts stock
    # back on the shelf, so the online AVAILABLE count goes UP -- re-push it to
    # Shopify. Gated (IMS_SHOPIFY_WRITES + DISPATCH_MODE) + fail-soft: never
    # blocks the return. No online mapping for a SKU -> no-op.
    if restock_applied and restocked:
        try:
            from ..services.online_stock_writeback import writeback_after_restock

            restocked_skus = [
                r.get("sku") for r in restocked if isinstance(r, dict) and r.get("sku")
            ]
            writeback_after_restock(None, restocked_skus, store_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[RETURNS] online write-back skipped: %s", exc)

    # 4. Persist the return doc.
    now = datetime.now().isoformat()
    doc: Dict[str, Any] = {
        "return_id": return_id,
        "order_id": resolved_order_id,
        "order_number": body.order_number or (order or {}).get("order_number"),
        "customer_id": customer_id,
        "customer_name": customer_name,
        "store_id": store_id,
        "return_type": body.return_type,
        # Persist the GST-resolved lines (each carries the rate it was billed
        # at) so the doc is self-describing for the credit note / audit.
        "items": priced_lines,
        "replacement_items": replacement_dump,
        "returned_value": ret_value,
        # GST-inclusive gross the customer paid for the returned qty, the
        # optional restocking fee, and the resulting net refund. Persisted so
        # the credit note / GSTR-1 reversal is auditable.
        "gross_refund": gross_refund,
        "restocking_fee": restocking_fee,
        "net_refund": net_amount,
        "gst_breakup": gst_view,
        "refund_amount": refund_amount,
        "refund_method": refund_method,
        "credit_amount": credit_amount,
        "collect_amount": collect_amount,
        "settlement": settlement,
        "restocked": restocked,
        "restock_applied": restock_applied,
        "restock_stock_ids": restock_stock_ids,
        "credit_entry": credit_entry,
        "status": "COMPLETED",
        # BUG-099: True when the loyalty reversal could not be applied (a real
        # failure, not a walk-in / no-loyalty no-op) -> reconciliation should retry.
        "loyalty_reversal_failed": loyalty_reversal_failed,
        "reason_summary": _reason_summary(active_lines),
        "approval_note": body.approval_note or "",
        # E3w: stamped only when a manager override cleared a SERIAL_MISMATCH on
        # this return (None for the normal / matched-serial path) -- the override
        # actor for the audit trail.
        "serial_override_by": serial_override_by,
        # F27: stamped only when the refund approval matrix was enabled and a
        # consumed E4 approval token cleared this refund (None when the gate was
        # dark or no approval was required) -- the approver for the audit trail.
        "refund_approval_by": refund_approval_by,
        # The approver's display name + the approval request id + status, so the
        # returns-history table can show an "Approved by <name>" pill without a
        # second lookup. All None when no approval was required (gate dark).
        "refund_approval_by_name": refund_approval_by_name,
        "refund_approval_request_id": refund_approval_request_id,
        "refund_approval_status": refund_approval_status,
        "created_by": current_user.get("user_id"),
        "created_by_name": current_user.get(
            "full_name", current_user.get("username", "")
        ),
        "created_at": now,
        # POS-14: stamp the idempotency key so a duplicate POST with the same key
        # is caught by the guard at the top of this handler without double-refunding.
        "idempotency_key": (idem_key or None),
    }

    coll = _returns_coll()
    if coll is not None:
        try:
            coll.insert_one(dict(doc))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RETURNS] persist failed: %s", exc)

    # Audit log (optional, fail-soft).
    try:
        from ..dependencies import get_audit_repository

        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "RETURN_CREATED",
                    "entity_type": "return",
                    "entity_id": return_id,
                    "store_id": store_id,
                    "user_id": current_user.get("user_id"),
                    "details": {
                        "return_type": body.return_type,
                        "returned_value": ret_value,
                        "gross_refund": gross_refund,
                        "restocking_fee": restocking_fee,
                        "net_refund": net_amount,
                        "refund_amount": refund_amount,
                        "credit_amount": credit_amount,
                        "collect_amount": collect_amount,
                    },
                    "created_at": now,
                }
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RETURNS] audit log skipped: %s", exc)

    # 5. Response.
    fee_note = (
        f" (gross Rs {gross_refund} - restocking fee Rs {restocking_fee})"
        if restocking_fee > 0
        else ""
    )
    message = {
        "RETURN": f"Refund of Rs {net_amount} recorded{fee_note}",
        "CREDIT_NOTE": f"Store credit of Rs {net_amount} issued{fee_note}",
        "EXCHANGE": "Exchange recorded",
    }[body.return_type]

    return {
        "return_id": return_id,
        "return_type": body.return_type,
        "returned_value": ret_value,
        "gross_refund": gross_refund,
        "restocking_fee": restocking_fee,
        "net_refund": net_amount,
        "gst_breakup": gst_view,
        "refund_amount": refund_amount,
        "refund_method": refund_method,
        "credit_amount": credit_amount,
        "collect_amount": collect_amount,
        "settlement": settlement,
        "restocked": restocked,
        "restock_applied": restock_applied,
        "restock_stock_ids": restock_stock_ids,
        "message": message,
    }


@router.get("")
@router.get("/")
async def list_returns(
    store_id: Optional[str] = Query(None),
    return_type: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
):
    """List returns, store-scoped, newest first.

    HQ roles (SUPERADMIN/ADMIN/AREA_MANAGER) may pass any store_id; lower
    roles are pinned to their active store.
    """
    coll = _returns_coll()
    if coll is None:
        return {"returns": [], "total": 0}

    roles = current_user.get("roles", []) or []
    is_hq = any(r in roles for r in ("SUPERADMIN", "ADMIN", "AREA_MANAGER"))
    effective_store = store_id if is_hq else current_user.get("active_store_id")

    query: Dict[str, Any] = {}
    if effective_store:
        query["store_id"] = effective_store
    if return_type:
        query["return_type"] = return_type

    try:
        total = coll.count_documents(query)
        cursor = (
            coll.find(query, {"_id": 0}).sort("created_at", -1).skip(skip).limit(limit)
        )
        returns = list(cursor)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RETURNS] list failed: %s", exc)
        return {"returns": [], "total": 0}

    return {"returns": returns, "total": total}


@router.get("/{return_id}")
async def get_return(
    return_id: str = Path(..., description="Return ID"),
    current_user: dict = Depends(get_current_user),
):
    """Get a single return by id."""
    coll = _returns_coll()
    if coll is None:
        raise HTTPException(status_code=404, detail="Return not found")
    try:
        doc = coll.find_one({"return_id": return_id}, {"_id": 0})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RETURNS] get failed: %s", exc)
        raise HTTPException(status_code=404, detail="Return not found")
    if not doc:
        raise HTTPException(status_code=404, detail="Return not found")
    # IDOR guard: a return carries customer identity + refund amounts. Only a
    # caller with access to the return's store may read it (mirrors the
    # store-pinned list above; SUPERADMIN/ADMIN pass through). A legacy doc
    # with no store_id falls back to the caller's active store (allowed) --
    # same semantics as GET /orders/{order_id}.
    validate_store_access(doc.get("store_id"), current_user)
    return doc


@router.post("/{return_id}/restock")
async def retry_restock(
    return_id: str = Path(..., description="Return ID"),
    current_user: dict = Depends(require_roles(*_RETURN_ROLES)),
):
    """Re-run restock for a return whose units never made it back to stock.

    Idempotent + race-safe: a SINGLE atomic find_one_and_update claims the
    return for restock. If `restock_applied` is already True the claim fails
    and we report "Already restocked". If another worker is currently mid-
    restock (`restock_in_progress`=True) the claim also fails and we report
    that. Otherwise we run the stock writes and persist the new state.

    Why this matters: the old code read the doc, checked applied=False, did
    stock writes, then wrote applied=True. Two concurrent retries both saw
    applied=False, both did stock writes, both wrote applied=True -> the same
    unit got minted/reactivated twice. The claim+commit guards both layers.

    Also lets a transient DB failure during create() be retried without
    re-recording any money movement.
    """
    coll = _returns_coll()
    if coll is None:
        raise HTTPException(status_code=404, detail="Return not found")

    # Atomic claim: succeed only if NOT already applied AND NOT in progress.
    now_iso = datetime.now().isoformat()
    try:
        claim = coll.find_one_and_update(
            {
                "return_id": return_id,
                "restock_applied": {"$ne": True},
                "restock_in_progress": {"$ne": True},
            },
            {
                "$set": {
                    "restock_in_progress": True,
                    "restock_started_at": now_iso,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RETURNS] restock claim failed: %s", exc)
        claim = None

    if claim is None:
        # Either applied, in-progress, or no such return. Distinguish by reading.
        try:
            doc = coll.find_one({"return_id": return_id}, {"_id": 0})
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RETURNS] restock retry lookup failed: %s", exc)
            raise HTTPException(status_code=404, detail="Return not found")
        if not doc:
            raise HTTPException(status_code=404, detail="Return not found")
        if doc.get("restock_applied"):
            return {
                "return_id": return_id,
                "restock_applied": True,
                "restock_stock_ids": doc.get("restock_stock_ids", []),
                "message": "Already restocked",
            }
        # Someone else is mid-restock; tell the caller to retry shortly.
        return {
            "return_id": return_id,
            "restock_applied": False,
            "restock_stock_ids": doc.get("restock_stock_ids", []),
            "message": "Restock in progress - retry shortly",
        }

    # We hold the claim. Already-restocked units are tracked in restock_stock_ids
    # on the doc; we re-run plan_restock fresh, then mark sold+commit.
    existing_ids = list(claim.get("restock_stock_ids") or [])
    lines = [ReturnLine(**it) for it in (claim.get("items") or [])]

    try:
        restock_result = _restock_good_items(
            lines,
            claim.get("store_id"),
            return_id,
            order_id=claim.get("order_id"),
            already_applied=False,
            user_id=current_user.get("user_id"),
        )
    except Exception as exc:  # noqa: BLE001
        # Even on failure we MUST release the in-progress flag so a future
        # retry isn't deadlocked.
        logger.warning("[RETURNS] restock retry stock writes failed: %s", exc)
        try:
            coll.update_one(
                {"return_id": return_id},
                {"$set": {"restock_in_progress": False}},
            )
        except Exception:  # noqa: BLE001
            pass
        raise HTTPException(status_code=500, detail="Restock retry failed")

    # Merge stock_ids: any units we already had (from a partial earlier run)
    # plus what we just produced, deduped while preserving order.
    new_ids = list(restock_result.get("restock_stock_ids", []))
    merged_ids: List[str] = []
    seen: set = set()
    for sid in existing_ids + new_ids:
        if sid and sid not in seen:
            seen.add(sid)
            merged_ids.append(sid)

    update = {
        "restocked": restock_result.get("restocked", []),
        "restock_applied": bool(restock_result.get("applied")),
        "restock_stock_ids": merged_ids,
        "restock_in_progress": False,
        "restock_completed_at": datetime.now().isoformat(),
    }
    try:
        coll.update_one({"return_id": return_id}, {"$set": update})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RETURNS] restock retry persist failed: %s", exc)

    return {
        "return_id": return_id,
        "restock_applied": update["restock_applied"],
        "restock_stock_ids": update["restock_stock_ids"],
        "message": (
            "Restock applied"
            if update["restock_applied"]
            else "Restock partial - retry again"
        ),
    }
