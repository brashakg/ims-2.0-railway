"""Feature #6 -- Luxury / per-unit SERIAL tracking.

A unique serial number is captured at STOCK-IN for serialized / high-value
items (hearing aids, luxury frames, watches -- per-category flag via E2
policy), tracked through the SALE, and looked up later for warranty / recall.

This is the per-UNIT layer (owner decision 2026-06-09 #9: hearing-aid serial
is per-UNIT at stock-in, NOT at the catalogue level). It REUSES the existing
``stock_units`` collection (also used by F21 quarantine + N4 returns) and the
existing ``orders`` flow -- it does NOT fork either.

Serial lifecycle (status on the stock_unit doc)::

    IN_STOCK  --(at sale, atomic guarded find_one_and_update)-->  SOLD
       ^                                                            |
       |                                                            v
       +-------------------- RETURNED <----------------------------+
                                |
                                v
                            RECALLED   (terminal-ish; recall can also act
                                        directly on a SOLD unit)

Key guarantees:
- The serial is UNIQUE (partial unique index wired at startup, see
  database/connection.py ``uniq_stock_unit_serial``) so two stock-ins of the
  same serial -> the second is rejected (409).
- The IN_STOCK -> SOLD transition is a guarded ``find_one_and_update`` keyed
  on ``status == IN_STOCK`` so the SAME serial can NEVER be double-sold: two
  concurrent sales of one serial -> exactly one wins (``mark_serial_sold``).
- It attaches to the sale as an inventory side-effect ONLY -- it does NOT
  touch the order total or any money capture.

Money, if any, is integer paise. Everything is store-scoped: every read/write
keyed on (store_id, serial) so one store can never touch another's units.

This module owns the Mongo I/O for the per-unit serial layer. RBAC, audit
rows, and the HTTP surface live in ``api/routers/serial_tracking.py``; the
at-sale guarded transition is invoked (fail-soft) from the order finalize path
in ``orders.py::_mark_units_sold`` -- it is system-driven, never a cashier
action.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

# Stock-unit serial lifecycle states.
STATUS_IN_STOCK = "IN_STOCK"
STATUS_SOLD = "SOLD"
STATUS_RETURNED = "RETURNED"
STATUS_RECALLED = "RECALLED"

ALL_STATUSES = (STATUS_IN_STOCK, STATUS_SOLD, STATUS_RETURNED, STATUS_RECALLED)

# Allowed forward transitions for a serialized stock_unit.
ALLOWED_TRANSITIONS = {
    STATUS_IN_STOCK: {STATUS_SOLD, STATUS_RECALLED},
    STATUS_SOLD: {STATUS_RETURNED, STATUS_RECALLED},
    STATUS_RETURNED: {STATUS_IN_STOCK, STATUS_RECALLED},
    STATUS_RECALLED: set(),
}

# E2 policy key naming the categories (UPPER-cased) tracked per-unit by serial.
# DARK by default: an empty list means NOTHING is forced to carry a serial, so a
# fresh deploy behaves exactly as today (non-serialized category is a no-op).
SERIALIZED_CATEGORIES_KEY = "inventory.serialized_categories"


class SerialError(Exception):
    """A business-rule failure in the serial layer. Carries an HTTP-ish status
    so the router can translate it (409 duplicate / 404 unknown / 409 conflict)
    without leaking Mongo internals."""

    def __init__(self, message: str, status: int = 400, code: str = "serial_error"):
        super().__init__(message)
        self.status = status
        self.code = code


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def normalize_serial(s):
    """Canonicalize a raw serial for storage + uniqueness comparison.

    Trims surrounding whitespace, collapses internal whitespace, and
    upper-cases. Returns an empty string for ``None`` / non-string / blank
    input so callers can reject it cleanly. Pure -- no I/O.
    """
    if not isinstance(s, str):
        return ""
    # Collapse any run of internal whitespace to nothing-significant: keep a
    # single token form. Serials are alnum-ish; we strip and upper-case and
    # remove interior spaces so "ab 12" and "AB12" collide.
    return "".join(s.split()).upper()


def _norm_category(c) -> str:
    return str(c or "").strip().upper()


def serialized_categories(get_policy_fn, store_id: Optional[str] = None) -> List[str]:
    """Resolve the configured serialized categories (UPPER) via E2 get_policy.

    ``get_policy_fn`` is passed in so the router can hand the real
    ``policy_engine.get_policy`` while tests inject a stub -- the service never
    imports the engine itself (keeps it pure + trivially testable). Fail-soft:
    a missing key / engine error resolves to the empty list (feature dark).
    """
    try:
        scope = {"store_id": store_id} if store_id else None
        raw = get_policy_fn(SERIALIZED_CATEGORIES_KEY, scope, default=[])
    except Exception:  # noqa: BLE001
        return []
    if isinstance(raw, str):
        raw = [p for p in raw.replace(",", " ").split() if p]
    if not isinstance(raw, (list, tuple, set)):
        return []
    return [_norm_category(c) for c in raw if _norm_category(c)]


def is_serialized_category(category, get_policy_fn, store_id: Optional[str] = None) -> bool:
    """True iff ``category`` is in the configured serialized set for this store.

    Empty config -> always False (a non-serialized category is a no-op: the
    sale path does not force a serial; stock-in does not require one)."""
    cat = _norm_category(category)
    if not cat:
        return False
    return cat in set(serialized_categories(get_policy_fn, store_id))


# ---------------------------------------------------------------------------
# DB operations (the collection is passed in -> store-scoped + test-friendly)
# ---------------------------------------------------------------------------


def find_serial(units_coll, serial: str, store_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Return the stock_unit carrying ``serial`` (store-scoped if given).

    Looks up the normalized serial. Returns None when absent or coll is None.
    """
    if units_coll is None:
        return None
    sn = normalize_serial(serial)
    if not sn:
        return None
    query: Dict[str, Any] = {"serial": sn}
    if store_id:
        query["store_id"] = store_id
    doc = units_coll.find_one(query)
    if doc is not None:
        doc.pop("_id", None)
    return doc


def capture_serial(
    units_coll,
    *,
    serial: str,
    product_id: str,
    store_id: str,
    grn_id: Optional[str] = None,
    barcode: Optional[str] = None,
    warranty_months: Optional[int] = None,
    warranty_expiry_date: Optional[str] = None,
    captured_by: Optional[str] = None,
) -> Dict[str, Any]:
    """STOCK-IN: mint an IN_STOCK serialized stock_unit for ONE physical unit.

    Reuses the ``stock_units`` collection (one row == one physical unit, the
    existing serialized model). Uniqueness is enforced two ways: a pre-check
    (clean 409) AND the partial UNIQUE index (the real backstop against a
    concurrent duplicate insert -- a DuplicateKeyError is translated to the
    same 409 so the two never both land).

    Raises ``SerialError`` on a blank serial (400) or a duplicate (409).
    Returns the created unit doc (no ``_id``).
    """
    if units_coll is None:
        raise SerialError("inventory store unavailable", status=503, code="unavailable")
    sn = normalize_serial(serial)
    if not sn:
        raise SerialError("serial is required", status=400, code="serial_required")
    if not product_id:
        raise SerialError("product_id is required", status=400, code="product_required")
    if not store_id:
        raise SerialError("store_id is required", status=400, code="store_required")

    # Pre-check (store-scoped + global): a serial must be globally unique on the
    # index, so reject if it exists ANYWHERE -- the index key is (serial) so a
    # different store reusing the same serial would also collide. The clean 409
    # here is just a nicer message than the raw DuplicateKeyError below.
    if units_coll.find_one({"serial": sn}) is not None:
        raise SerialError(
            f"serial {sn} already exists", status=409, code="serial_duplicate"
        )

    now = datetime.utcnow().isoformat()
    doc: Dict[str, Any] = {
        "stock_id": str(uuid.uuid4()),
        "serial": sn,
        "product_id": product_id,
        "store_id": store_id,
        "barcode": barcode,
        "quantity": 1,
        "status": STATUS_IN_STOCK,
        "serial_tracked": True,
        "grn_id": grn_id,
        "source_type": "GRN" if grn_id else "SERIAL_INTAKE",
        "source_id": grn_id,
        "warranty_months": warranty_months,
        "warranty_expiry_date": warranty_expiry_date,
        "order_id": None,
        "sold_to_customer_id": None,
        "sold_at": None,
        "captured_by": captured_by,
        "created_by": captured_by,
        "created_at": now,
        "updated_at": now,
    }
    try:
        units_coll.insert_one(doc)
    except Exception as exc:  # noqa: BLE001 -- catch DuplicateKeyError + emulated index
        # The partial UNIQUE index is the real race backstop: two concurrent
        # captures of the same serial -> exactly one insert wins, the other
        # raises here and is surfaced as the same 409 (NO duplicate unit).
        if _is_duplicate_key(exc):
            raise SerialError(
                f"serial {sn} already exists", status=409, code="serial_duplicate"
            ) from exc
        raise
    doc.pop("_id", None)
    return doc


def mark_serial_sold(
    units_coll,
    *,
    serial: str,
    order_id: str,
    store_id: Optional[str] = None,
    customer_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """AT-SALE: atomically transition this serial IN_STOCK -> SOLD.

    THE DOUBLE-SELL GUARD. A single ``find_one_and_update`` whose FILTER
    requires ``status == IN_STOCK`` does the guard AND the flip in one
    indivisible op. Two concurrent sales of one serial: Mongo lets only ONE
    writer match the IN_STOCK document; the other matches nothing and gets
    None back -> the same serial can NEVER be sold twice. No read-modify-write
    window. Mirrors vouchers.redeem_voucher_atomic.

    Returns the updated unit doc on success, or None if the unit was not
    IN_STOCK (already sold / recalled / absent). It NEVER raises for a
    business-rule miss -- the caller (order finalize) is fail-soft and must
    not have order creation blocked by a stock-side miss.

    Crucially this is an INVENTORY write only: it stamps order_id + customer +
    sold_at on the unit. It does NOT touch the order total or any payment --
    money capture stays entirely in the POS / order path.
    """
    if units_coll is None or not order_id:
        return None
    sn = normalize_serial(serial)
    if not sn:
        return None
    filt: Dict[str, Any] = {"serial": sn, "status": STATUS_IN_STOCK}
    if store_id:
        filt["store_id"] = store_id
    now = datetime.utcnow().isoformat()
    updated = units_coll.find_one_and_update(
        filt,
        {
            "$set": {
                "status": STATUS_SOLD,
                "order_id": order_id,
                "sold_to_customer_id": customer_id,
                "sold_at": now,
                "updated_at": now,
            }
        },
        return_document=_RETURN_AFTER,
    )
    if updated is not None:
        updated.pop("_id", None)
    return updated


def transition_serial(
    units_coll,
    *,
    serial: str,
    to_status: str,
    store_id: Optional[str] = None,
    actor: Optional[str] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Move a serial to RETURNED / RECALLED (or back to IN_STOCK) atomically.

    The guard is the SET of valid source statuses for ``to_status`` (per
    ALLOWED_TRANSITIONS), encoded into the find_one_and_update filter, so an
    illegal transition (e.g. RECALLED -> SOLD) simply matches no document.

    Returns the updated unit. Raises ``SerialError`` 404 when the serial is
    unknown, or 409 when the unit is not in a state that permits ``to_status``.
    """
    if units_coll is None:
        raise SerialError("inventory store unavailable", status=503, code="unavailable")
    sn = normalize_serial(serial)
    if not sn:
        raise SerialError("serial is required", status=400, code="serial_required")
    if to_status not in ALL_STATUSES:
        raise SerialError(f"invalid status {to_status}", status=400, code="bad_status")

    # Sources from which `to_status` is reachable.
    valid_from = [s for s, dests in ALLOWED_TRANSITIONS.items() if to_status in dests]
    filt: Dict[str, Any] = {"serial": sn, "status": {"$in": valid_from}}
    if store_id:
        filt["store_id"] = store_id
    now = datetime.utcnow().isoformat()
    set_doc: Dict[str, Any] = {"status": to_status, "updated_at": now}
    if to_status == STATUS_RECALLED:
        set_doc["recalled_at"] = now
        set_doc["recalled_by"] = actor
        set_doc["recall_reason"] = reason
    elif to_status == STATUS_RETURNED:
        set_doc["returned_at"] = now
        set_doc["returned_by"] = actor

    updated = units_coll.find_one_and_update(
        filt, {"$set": set_doc}, return_document=_RETURN_AFTER
    )
    if updated is not None:
        updated.pop("_id", None)
        return updated

    # No match: disambiguate for a precise error (read is post-failure only).
    existing = find_serial(units_coll, sn, store_id)
    if existing is None:
        raise SerialError(f"serial {sn} not found", status=404, code="serial_not_found")
    raise SerialError(
        f"serial {sn} is {existing.get('status')}, cannot move to {to_status}",
        status=409,
        code="invalid_transition",
    )


def lookup_warranty(
    units_coll,
    orders_coll,
    customers_coll,
    *,
    serial: str,
    store_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """WARRANTY / RECALL LOOKUP by serial: unit -> its sale -> customer.

    Resolves the stock_unit, joins the order that sold it (by ``order_id``),
    the customer, and computes a warranty window status. A staff read.
    Raises ``SerialError`` 404 when the serial is unknown.

    The warranty window is derived from an explicit ``warranty_expiry_date`` if
    present, else ``sold_at`` + ``warranty_months``. Pure date math (no money).
    """
    unit = find_serial(units_coll, serial, store_id)
    if unit is None:
        raise SerialError(
            f"serial {normalize_serial(serial)} not found",
            status=404,
            code="serial_not_found",
        )

    order = None
    oid = unit.get("order_id")
    if oid and orders_coll is not None:
        order = orders_coll.find_one({"order_id": oid})
        if order is not None:
            order.pop("_id", None)

    customer = None
    cid = unit.get("sold_to_customer_id") or (order or {}).get("customer_id")
    if cid and customers_coll is not None:
        customer = customers_coll.find_one({"customer_id": cid})
        if customer is not None:
            customer.pop("_id", None)

    warranty = compute_warranty(unit, now=now)
    return {
        "serial": unit.get("serial"),
        "status": unit.get("status"),
        "unit": unit,
        "order": order,
        "customer": customer,
        "warranty": warranty,
    }


def compute_warranty(unit: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Derive the warranty window for a unit. Pure -> unit-testable.

    Returns {expiry_date, active(bool|None), days_remaining(int|None)}.
    ``active`` is None when there is no derivable expiry (NONE state).
    """
    now = now or datetime.utcnow()
    expiry = unit.get("warranty_expiry_date")
    if not expiry:
        sold_at = unit.get("sold_at")
        months = unit.get("warranty_months")
        if sold_at and months:
            base = _parse_dt(sold_at)
            if base is not None:
                expiry = _add_months(base, int(months)).isoformat()
    if not expiry:
        return {"expiry_date": None, "active": None, "days_remaining": None}
    exp = _parse_dt(expiry)
    if exp is None:
        return {"expiry_date": expiry, "active": None, "days_remaining": None}
    delta_days = (exp.date() - now.date()).days
    return {
        "expiry_date": expiry,
        "active": exp.date() >= now.date(),
        "days_remaining": delta_days,
    }


# ---------------------------------------------------------------------------
# small internals
# ---------------------------------------------------------------------------

try:  # pymongo present in prod; tests that emulate the coll don't need it.
    from pymongo import ReturnDocument as _RD
    from pymongo.errors import DuplicateKeyError as _DupKeyError

    _RETURN_AFTER = _RD.AFTER
except Exception:  # noqa: BLE001
    _RETURN_AFTER = True  # fake collections accept a truthy "after" flag
    _DupKeyError = None


def _is_duplicate_key(exc: Exception) -> bool:
    """True for a Mongo duplicate-key error OR a fake-collection emulation
    thereof (tests raise an exception whose class name contains 'Duplicate')."""
    if _DupKeyError is not None and isinstance(exc, _DupKeyError):
        return True
    name = type(exc).__name__.lower()
    return "duplicate" in name or "duplicatekey" in name


def _parse_dt(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def _add_months(dt: datetime, months: int) -> datetime:
    """Add whole months, clamping the day to the target month's length."""
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    # Clamp day (e.g. Jan-31 + 1mo -> Feb-28/29).
    import calendar

    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)
