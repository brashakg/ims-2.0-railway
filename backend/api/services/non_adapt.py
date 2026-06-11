"""Feature #14 -- Non-adaptation / remake tracking (pure helpers).

A customer who cannot ADAPT to new spectacles (esp. progressives / multifocals)
gets a REMAKE within a policy window. This module holds the pure, side-effect-free
logic used by the non_adapt router: the policy window check and the paise-exact
remake charge decision. The router owns Mongo I/O, RBAC, audit, and the
order/workshop reuse.

----------------------------------------------------------------------------
NON-ADAPTATION RECORD SHAPE (Mongo collection `non_adapt_records`, single doc)
----------------------------------------------------------------------------
{
  "_id": ObjectId,
  "record_id": "NA-<short>",            # human-friendly id
  "store_id": "<store>",                # store-scoped (validate_store_access)
  "original_order_id": "<order id>",    # the order being remade
  "original_item_id": "<line id>",      # the specific lens line, optional
  "lens_brand": "Zeiss",               # denormalised for the quality report
  "product_id": "<catalog product>",    # optional, for the quality report
  "reason": "PROGRESSIVE_INTOLERANCE", # see NonAdaptReason
  "reason_note": "free text",
  "optometrist_id": "<user>",           # who captured / owns the re-check
  "rx_recheck_required": true,           # a non-adapt often => Rx re-check
  "prescription_id": "<rx>",            # new Rx if re-checked, optional

  # --- remake link (set when a remake is initiated) ---
  "remake_order_id": "<new order id>",  # reuses the existing order create path
  "remake_workshop_job_id": "<job id>", # reuses the existing workshop job path
  "remake_status": "RECORDED|REMAKE_INITIATED|COMPLETED|CANCELLED",

  # --- policy charge decision (paise; see remake_charge_paise) ---
  "original_cost_paise": 250000,
  "within_window": true,
  "window_days": 45,
  "charge_policy": "FREE|PERCENT|FULL",
  "charge_percent": 0,                   # when PERCENT
  "remake_charge_paise": 0,              # the DECISION, not a payment capture
  "charge_waived": true,                 # free/discounted vs full
  "authorized_by": "<user>",            # who authorized a waiver (manager+)

  "created_by": "<user>",
  "created_at": "ISO8601",
  "updated_at": "ISO8601",
}

This module does NOT capture money. `remake_charge_paise` is only the DECISION;
the actual remake order, if created, goes through the existing order/workshop
create path so POS pricing/payment stays the single source of truth.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Union


# Canonical non-adaptation reasons (quality signal -- aggregated in the report).
NON_ADAPT_REASONS = (
    "PROGRESSIVE_INTOLERANCE",
    "WRONG_POWER_FELT",
    "COSMETIC",
    "OTHER",
)

# Charge policy modes resolved from E2 get_policy.
CHARGE_FREE = "FREE"
CHARGE_PERCENT = "PERCENT"
CHARGE_FULL = "FULL"

DEFAULT_WINDOW_DAYS = 45


def _as_date(value: Union[str, date, datetime]) -> date:
    """Coerce an ISO string / datetime / date into a date (pure)."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    # ISO string; tolerate a trailing 'Z' and time component.
    text = str(value).strip().replace("Z", "")
    if "T" in text:
        text = text.split("T", 1)[0]
    return date.fromisoformat(text)


def is_within_window(
    sale_date: Union[str, date, datetime],
    today: Union[str, date, datetime],
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> bool:
    """True if `today` is within `window_days` (inclusive) of `sale_date`.

    The window is the policy grace period for a free/discounted remake. A remake
    requested on the boundary day still counts as within the window. Future
    sale dates (today < sale_date) are treated as within the window.
    """
    # Adversarial P2: a malformed / non-ISO sale or today value must NOT crash the
    # engine (500). Per the module contract, undeterminable dates fail SOFT to
    # within-window (the customer-favourable side -- a free/discounted remake),
    # never to a silent charge.
    try:
        sale = _as_date(sale_date)
        now = _as_date(today)
    except (ValueError, TypeError):
        return True
    delta = (now - sale).days
    if delta < 0:
        return True
    return delta <= int(window_days)


def remake_charge_paise(
    original_cost_paise: int,
    within_window: bool,
    charge_policy: str,
    charge_percent: Union[int, float] = 0,
) -> int:
    """Resolve the paise-exact remake charge DECISION.

    - Within the window:
        FREE    -> 0
        PERCENT -> round(original * percent / 100), half-up, clamped [0, original]
        FULL    -> original (a policy may choose to charge in-window)
    - Outside the window: always the full original cost (chargeable).

    Pure + deterministic. Never returns a negative value. This is the charge
    DECISION only -- it is not a payment capture.
    """
    original = int(original_cost_paise)
    if original < 0:
        raise ValueError("original_cost_paise must be >= 0")

    if not within_window:
        return original

    policy = (charge_policy or "").upper()
    if policy == CHARGE_FREE:
        return 0
    if policy == CHARGE_FULL:
        return original
    if policy == CHARGE_PERCENT:
        pct = float(charge_percent or 0)
        if pct <= 0:
            return 0
        if pct >= 100:
            return original
        # Half-up rounding on integer paise.
        charge = int((original * pct + 50) // 100)
        if charge < 0:
            return 0
        if charge > original:
            return original
        return charge
    # Unknown policy in-window: fail safe to FREE (the audited, gated path).
    return 0


# ---------------------------------------------------------------------------
# Money + line helpers (pure)
# ---------------------------------------------------------------------------

# Remake lifecycle states (also documented in the record-shape header).
STATUS_RECORDED = "RECORDED"
STATUS_REMAKE_INITIATED = "REMAKE_INITIATED"
STATUS_COMPLETED = "COMPLETED"
STATUS_CANCELLED = "CANCELLED"


def rupees_to_paise(value: Union[int, float, None]) -> int:
    """Rupees (float, as orders store line money) -> integer paise, half-up.

    Order lines persist `item_total` / `unit_price` in RUPEES; the non-adapt
    charge decision is paise-exact, so we round to the nearest paise here once,
    at the boundary, rather than carrying float drift into the decision."""
    if value is None:
        return 0
    return int(round(float(value) * 100))


def _line_cost_paise(line: Dict[str, Any]) -> int:
    """The original line value used as the remake's charge basis, in paise.

    Prefers the line's net `item_total` (qty x unit_price less per-line
    discount, as orders persist it); falls back to unit_price * quantity. This
    is the customer-facing line value -- the remake-charge decision is a
    fraction (or all/none) of it, never a re-pricing."""
    if not isinstance(line, dict):
        return 0
    total = line.get("item_total")
    if total is None:
        total = line.get("final_price")
    if total is not None:
        return max(0, rupees_to_paise(total))
    unit = line.get("unit_price") or 0
    qty = line.get("quantity") or 1
    return max(0, rupees_to_paise(float(unit) * float(qty)))


def _is_lens_line(line: Dict[str, Any]) -> bool:
    """A non-adaptation is, by definition, against a LENS / CONTACT_LENS line
    (the customer can't adapt to the prescription optics). We match on the
    line's item_type; a line carrying lens_details is also treated as a lens."""
    it = str((line or {}).get("item_type") or "").upper()
    if it in ("LENS", "CONTACT_LENS", "CONTACT LENS", "OPTICAL_LENS"):
        return True
    return bool((line or {}).get("lens_details"))


def find_order_line(order: Dict[str, Any], item_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Resolve the specific order line a non-adapt is recorded against.

    With an explicit `item_id` -> that exact line (or None if it isn't on the
    order). Without one -> the FIRST lens line (a non-adapt is a lens problem),
    else the first line. Returns None for an order with no items."""
    items = (order or {}).get("items") or []
    if not items:
        return None
    if item_id:
        for ln in items:
            if isinstance(ln, dict) and (ln.get("item_id") or ln.get("id")) == item_id:
                return ln
        return None
    for ln in items:
        if isinstance(ln, dict) and _is_lens_line(ln):
            return ln
    first = items[0]
    return first if isinstance(first, dict) else None


def _order_sale_date(order: Dict[str, Any]) -> Optional[Union[str, date, datetime]]:
    """The original sale date for the window check. Orders persist a BSON-Date
    `created_at`; `order_date` is the camelCase/display variant. Returns None
    when neither is present (the engine then treats the remake as IN-window --
    we never charge a customer on missing data)."""
    for key in ("created_at", "order_date", "createdAt", "confirmed_at"):
        v = (order or {}).get(key)
        if v:
            return v
    return None


# ---------------------------------------------------------------------------
# E2 policy resolution (window + charge), fail-soft to registry defaults
# ---------------------------------------------------------------------------


def resolve_charge_policy(store_id: Optional[str]) -> Dict[str, Any]:
    """Resolve (window_days, charge_policy, charge_percent) from E2 with
    store>entity>global scoping. Fail-soft: a policy-engine outage falls back to
    the registry code defaults (45 / FREE / 0) -- the same a fresh DB resolves
    to -- so the decision stays deterministic either way."""
    window = DEFAULT_WINDOW_DAYS
    policy = CHARGE_FREE
    percent: Union[int, float] = 0
    try:
        from .policy_engine import get_policy

        scope = {"store_id": store_id} if store_id else None
        window = int(get_policy("non_adapt.window_days", scope, default=DEFAULT_WINDOW_DAYS))
        policy = str(get_policy("non_adapt.charge_policy", scope, default=CHARGE_FREE)).upper()
        percent = float(get_policy("non_adapt.charge_percent", scope, default=0.0))
    except Exception:  # noqa: BLE001
        return {"window_days": DEFAULT_WINDOW_DAYS, "charge_policy": CHARGE_FREE,
                "charge_percent": 0}
    if policy not in (CHARGE_FREE, CHARGE_PERCENT, CHARGE_FULL):
        policy = CHARGE_FREE
    return {"window_days": window, "charge_policy": policy, "charge_percent": percent}


def resolve_charge(
    order: Dict[str, Any],
    line: Optional[Dict[str, Any]],
    store_id: Optional[str],
    today: Optional[Union[str, date, datetime]] = None,
) -> Dict[str, Any]:
    """The full paise-exact charge DECISION for a non-adapt remake.

    Combines the E2 policy (window + mode + percent) with the original line cost
    and the sale-vs-today window check. Returns a flat dict the record + the
    router surface verbatim. This is a DECISION, not a payment capture.

    `charge_waived` is True iff the resolved charge is LESS than the full
    original cost (free or discounted) -- the waiver flag that gates who may
    authorize it (a cashier can't silently waive an out-of-window charge)."""
    pol = resolve_charge_policy(store_id)
    original = _line_cost_paise(line or {})
    sale_date = _order_sale_date(order)
    now = today or datetime.utcnow()
    if sale_date is None:
        within = True  # never charge on missing sale-date data
    else:
        within = is_within_window(sale_date, now, pol["window_days"])
    charge = remake_charge_paise(
        original, within, pol["charge_policy"], pol["charge_percent"]
    )
    return {
        "original_cost_paise": original,
        "within_window": within,
        "window_days": int(pol["window_days"]),
        "charge_policy": pol["charge_policy"],
        "charge_percent": pol["charge_percent"],
        "remake_charge_paise": charge,
        "charge_waived": charge < original,
    }


# ---------------------------------------------------------------------------
# Engine -- Mongo I/O, store-scoped, atomic single-doc, audited waiver
# ---------------------------------------------------------------------------


class NonAdaptError(Exception):
    """Raised for a rejected non-adapt op (the router maps `.status` to HTTP)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class NonAdaptEngine:
    """Records non-adaptations + tracked remakes against an original order line.

    Owns Mongo I/O for the `non_adapt_records` collection only. It does NOT
    create the remake order or the workshop job itself -- those go through the
    EXISTING order / workshop create paths (the router calls them and hands the
    resulting ids back here to LINK), so POS pricing / payment + the workshop
    lifecycle stay the single source of truth. Money math is never forked.
    """

    COLLECTION = "non_adapt_records"

    def __init__(self, db=None):
        self.db = db

    # -- collection access ---------------------------------------------------

    def _coll(self):
        if self.db is None:
            return None
        try:
            return self.db.get_collection(self.COLLECTION)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _now_iso() -> str:
        return datetime.utcnow().isoformat()

    # -- record a non-adaptation --------------------------------------------

    def record(
        self,
        *,
        order: Dict[str, Any],
        line: Optional[Dict[str, Any]],
        reason: str,
        store_id: str,
        actor: Dict[str, Any],
        reason_note: Optional[str] = None,
        optometrist_id: Optional[str] = None,
        rx_recheck_required: bool = True,
        prescription_id: Optional[str] = None,
        today: Optional[Union[str, date, datetime]] = None,
    ) -> Dict[str, Any]:
        """Create + persist ONE non_adapt_records doc (atomic single insert).

        Resolves the E2 charge DECISION at record time (window + mode), stamps
        the original line denormals for the quality report, and links the
        original order/line. Raises NonAdaptError on a bad reason / empty order.
        """
        rsn = str(reason or "").upper()
        if rsn not in NON_ADAPT_REASONS:
            raise NonAdaptError(
                f"reason must be one of {list(NON_ADAPT_REASONS)}", status=400
            )
        items = (order or {}).get("items") or []
        if not items:
            raise NonAdaptError(
                "original order has no line items to record a non-adaptation against",
                status=400,
            )
        decision = resolve_charge(order, line, store_id, today=today)
        line = line or {}
        record_id = "NA-" + uuid.uuid4().hex[:10].upper()
        now = self._now_iso()
        doc: Dict[str, Any] = {
            "record_id": record_id,
            "store_id": store_id,
            "original_order_id": order.get("order_id") or order.get("_id"),
            "original_item_id": line.get("item_id") or line.get("id"),
            "lens_brand": line.get("brand") or (line.get("lens_details") or {}).get("brand"),
            "product_id": line.get("product_id"),
            "reason": rsn,
            "reason_note": reason_note,
            "optometrist_id": optometrist_id or actor.get("user_id"),
            "rx_recheck_required": bool(rx_recheck_required),
            "prescription_id": prescription_id or line.get("prescription_id"),
            # remake link -- filled by create_remake
            "remake_order_id": None,
            "remake_workshop_job_id": None,
            "remake_status": STATUS_RECORDED,
            # charge decision (paise)
            "original_cost_paise": decision["original_cost_paise"],
            "within_window": decision["within_window"],
            "window_days": decision["window_days"],
            "charge_policy": decision["charge_policy"],
            "charge_percent": decision["charge_percent"],
            "remake_charge_paise": decision["remake_charge_paise"],
            "charge_waived": decision["charge_waived"],
            "authorized_by": None,
            "created_by": actor.get("user_id"),
            "created_at": now,
            "updated_at": now,
        }
        coll = self._coll()
        if coll is not None:
            doc["_id"] = record_id
            try:
                coll.insert_one(dict(doc))
            except Exception as exc:  # noqa: BLE001
                raise NonAdaptError(f"failed to record non-adaptation: {exc}", status=500)
        return doc

    # -- read ----------------------------------------------------------------

    def get(self, record_id: str) -> Optional[Dict[str, Any]]:
        coll = self._coll()
        if coll is None:
            return None
        doc = coll.find_one({"record_id": record_id})
        if doc is not None and not isinstance(doc.get("_id"), str):
            doc["_id"] = str(doc.get("_id"))
        return doc

    def list_for_order(self, order_id: str, store_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Per-order remake history. Store-scoped when store_id is given."""
        coll = self._coll()
        if coll is None:
            return []
        q: Dict[str, Any] = {"original_order_id": order_id}
        if store_id:
            q["store_id"] = store_id
        out: List[Dict[str, Any]] = []
        for d in coll.find(q):
            if not isinstance(d.get("_id"), str):
                d["_id"] = str(d.get("_id"))
            out.append(d)
        out.sort(key=lambda d: d.get("created_at") or "", reverse=True)
        return out

    # -- link a remake (the order/workshop are created by the router) --------

    def link_remake(
        self,
        record_id: str,
        *,
        remake_order_id: Optional[str] = None,
        remake_workshop_job_id: Optional[str] = None,
        actor: Optional[Dict[str, Any]] = None,
        authorized_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Atomically stamp the remake-order / workshop-job pointers onto the
        record + advance its status. Uses find_one_and_update so a concurrent
        double-link can't clobber. `authorized_by` is set when this remake
        carried a waiver (free / discounted) -- the audit happens in the router
        once, where the AuditRepository lives."""
        coll = self._coll()
        if coll is None:
            raise NonAdaptError("non-adapt store unavailable", status=503)
        set_fields: Dict[str, Any] = {
            "remake_status": STATUS_REMAKE_INITIATED,
            "updated_at": self._now_iso(),
        }
        if remake_order_id is not None:
            set_fields["remake_order_id"] = remake_order_id
        if remake_workshop_job_id is not None:
            set_fields["remake_workshop_job_id"] = remake_workshop_job_id
        if authorized_by is not None:
            set_fields["authorized_by"] = authorized_by
        if actor is not None:
            set_fields["remake_initiated_by"] = actor.get("user_id")
        try:
            from pymongo import ReturnDocument

            updated = coll.find_one_and_update(
                # ONCE-ONLY guard: only the RECORDED -> REMAKE_INITIATED transition
                # links a remake. A concurrent/repeat /remake no longer matches, so
                # it can't clobber the prior link or re-stamp authorized_by (which
                # would silently re-waive). Mirrors the guarded-status pattern.
                {"record_id": record_id, "remake_status": STATUS_RECORDED},
                {"$set": set_fields},
                return_document=ReturnDocument.AFTER,
            )
        except Exception as exc:  # noqa: BLE001
            raise NonAdaptError(f"failed to link remake: {exc}", status=500)
        if updated is None:
            existing = coll.find_one({"record_id": record_id})
            if existing is not None:
                raise NonAdaptError("remake already initiated for this record", status=409)
            raise NonAdaptError("non-adapt record not found", status=404)
        if not isinstance(updated.get("_id"), str):
            updated["_id"] = str(updated.get("_id"))
        return updated

    # -- quality report ------------------------------------------------------

    def report(
        self,
        *,
        store_id: Optional[str] = None,
        reason: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Aggregate the non-adapt quality signal: counts by reason, by
        optometrist, and by lens brand, plus the total + waiver/charge tallies.

        A pure in-Python rollup over the store-scoped record set (the volumes
        are small -- one row per non-adaptation), so it works identically on a
        live Mongo or the test fake. Store-scoped: a store-bound caller passes
        their own store_id and never sees another store's quality data."""
        coll = self._coll()
        q: Dict[str, Any] = {}
        if store_id:
            q["store_id"] = store_id
        if reason:
            q["reason"] = str(reason).upper()
        rows: List[Dict[str, Any]] = []
        if coll is not None:
            for d in coll.find(q):
                ca = d.get("created_at") or ""
                if date_from and ca and ca < date_from:
                    continue
                if date_to and ca and ca > date_to:
                    continue
                rows.append(d)

        by_reason: Dict[str, int] = {}
        by_optometrist: Dict[str, int] = {}
        by_brand: Dict[str, int] = {}
        remakes = 0
        waived = 0
        total_charge_paise = 0
        for d in rows:
            by_reason[d.get("reason") or "OTHER"] = by_reason.get(d.get("reason") or "OTHER", 0) + 1
            opt = d.get("optometrist_id") or "UNKNOWN"
            by_optometrist[opt] = by_optometrist.get(opt, 0) + 1
            brand = d.get("lens_brand") or "UNKNOWN"
            by_brand[brand] = by_brand.get(brand, 0) + 1
            if d.get("remake_order_id") or d.get("remake_workshop_job_id"):
                remakes += 1
            if d.get("charge_waived"):
                waived += 1
            total_charge_paise += int(d.get("remake_charge_paise") or 0)

        return {
            "total": len(rows),
            "remakes_initiated": remakes,
            "waived": waived,
            "total_charge_paise": total_charge_paise,
            "by_reason": by_reason,
            "by_optometrist": by_optometrist,
            "by_lens_brand": by_brand,
        }


__all__ = [
    "NON_ADAPT_REASONS",
    "CHARGE_FREE",
    "CHARGE_PERCENT",
    "CHARGE_FULL",
    "DEFAULT_WINDOW_DAYS",
    "STATUS_RECORDED",
    "STATUS_REMAKE_INITIATED",
    "STATUS_COMPLETED",
    "STATUS_CANCELLED",
    "is_within_window",
    "remake_charge_paise",
    "rupees_to_paise",
    "find_order_line",
    "resolve_charge_policy",
    "resolve_charge",
    "NonAdaptError",
    "NonAdaptEngine",
]
