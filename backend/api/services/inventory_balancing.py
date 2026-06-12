"""Cross-store inventory balancing (Feature #1).

PURE READ-ONLY analytics + proposal engine. NEVER mutates stock and NEVER
executes a transfer. For each product, computes per-store velocity over an
N-day window, classifies each (product, store) as DEAD / OVERSTOCK / HEALTHY /
UNDERSTOCK / STOCKOUT, then proposes unit moves from overstock/dead donor
stores to the highest-velocity understock/stockout recipient store.

A manager acts on a proposal via the EXISTING transfers flow. This module does
NOT import or call any transfer-execution code -- it only reads.

Money/stock are integer UNITS here (qty); valuation is out of scope for #1.
The classify/propose math is PURE (plain dicts in, plain dicts out) so it is
trivially unit-testable; the DB rollups mirror predictive_reorder's
already-fetched-docs approach so a fake in-memory Mongo and real Mongo behave
identically (no aggregation-pipeline divergence).

No emoji (Windows cp1252).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

# E2 policy keys + fail-soft defaults.
POLICY_WINDOW_DAYS = "inventory.balancing_window_days"
POLICY_OVERSTOCK_DAYS_COVER = "inventory.overstock_days_cover"
POLICY_UNDERSTOCK_DAYS_COVER = "inventory.understock_days_cover"
POLICY_TARGET_DAYS_COVER = "inventory.target_days_cover"

DEFAULT_WINDOW_DAYS = 90
DEFAULT_OVERSTOCK_DAYS_COVER = 120
DEFAULT_UNDERSTOCK_DAYS_COVER = 21
DEFAULT_TARGET_DAYS_COVER = 45

# Classification labels.
DEAD = "DEAD"            # on-hand > 0 but zero sales in the window (frozen capital)
OVERSTOCK = "OVERSTOCK"  # days_of_cover above the overstock band (slow mover)
HEALTHY = "HEALTHY"
UNDERSTOCK = "UNDERSTOCK"  # days_of_cover below the understock band (running low)
STOCKOUT = "STOCKOUT"    # zero on-hand (empty)

_DONOR_CLASSES = (DEAD, OVERSTOCK)
_RECIPIENT_CLASSES = (UNDERSTOCK, STOCKOUT)
_INACTIVE_PRODUCT_STATES = {"inactive", "archived", "deleted", "discontinued"}


# ---------------------------------------------------------------------------
# Pure velocity + classification math (no DB, no framework)
# ---------------------------------------------------------------------------


def _daily_velocity(units_sold: float, window_days: int) -> float:
    wd = max(1, int(window_days or 0))
    return max(0.0, float(units_sold or 0)) / wd


def _days_of_cover(on_hand: float, daily_velocity: float) -> float:
    """Days of stock left at the current sell-through. +inf when nothing sells
    but stock remains (a DEAD/idle SKU never 'runs out')."""
    if daily_velocity <= 0:
        return math.inf if (on_hand or 0) > 0 else 0.0
    return max(0.0, float(on_hand or 0)) / daily_velocity


def _target_cover_units(daily_velocity: float, target_days_cover: int) -> int:
    return int(math.ceil(max(0.0, daily_velocity) * max(0, int(target_days_cover))))


def _round_doc(value: float) -> Optional[float]:
    """Round days-of-cover for display; +inf -> None (never runs out)."""
    if value is None or math.isinf(value) or math.isnan(value):
        return None
    return round(float(value), 1)


def _thresholds_with_defaults(thresholds: Optional[Dict[str, Any]]) -> Dict[str, int]:
    t = thresholds or {}
    return {
        "window_days": int(t.get("window_days") or DEFAULT_WINDOW_DAYS),
        "overstock_days_cover": int(t.get("overstock_days_cover") or DEFAULT_OVERSTOCK_DAYS_COVER),
        "understock_days_cover": int(t.get("understock_days_cover") or DEFAULT_UNDERSTOCK_DAYS_COVER),
        "target_days_cover": int(t.get("target_days_cover") or DEFAULT_TARGET_DAYS_COVER),
    }


def classify_store_stock(stat: Dict[str, Any], *, thresholds: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Enrich ONE per-(product, store) stat dict with velocity, days-of-cover,
    a classification, and the donor surplus / recipient deficit (units).

    ``stat`` carries: product_id, store_id, brand, category, on_hand, units_sold.
    A DEAD donor's whole on-hand is movable (target cover 0); an OVERSTOCK donor
    only sheds what sits above its own target cover -- never below it.
    """
    t = _thresholds_with_defaults(thresholds)
    on_hand = max(0, int(stat.get("on_hand") or 0))
    units_sold = max(0, int(stat.get("units_sold") or 0))
    vel = _daily_velocity(units_sold, t["window_days"])
    doc = _days_of_cover(on_hand, vel)
    target_units = _target_cover_units(vel, t["target_days_cover"])

    if on_hand > 0 and units_sold == 0:
        klass = DEAD
    elif on_hand == 0:
        klass = STOCKOUT
    elif doc > t["overstock_days_cover"]:
        klass = OVERSTOCK
    elif doc < t["understock_days_cover"]:
        klass = UNDERSTOCK
    else:
        klass = HEALTHY

    surplus = max(0, on_hand - target_units) if klass in _DONOR_CLASSES else 0
    deficit = max(0, target_units - on_hand) if klass in _RECIPIENT_CLASSES else 0

    return {
        **stat,
        "on_hand": on_hand,
        "units_sold": units_sold,
        "daily_velocity": round(vel, 4),
        "days_of_cover": _round_doc(doc),
        "target_cover_units": target_units,
        "classification": klass,
        "surplus_units": surplus,
        "deficit_units": deficit,
    }


def classify_all(stats: List[Dict[str, Any]], *, thresholds: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    return [classify_store_stock(s, thresholds=thresholds) for s in (stats or [])]


def _ensure_classified(stats: List[Dict[str, Any]], thresholds: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for s in stats or []:
        out.append(s if "classification" in s else classify_store_stock(s, thresholds=thresholds))
    return out


def _build_move(product_id, donor, recipient, qty, window_days) -> Dict[str, Any]:
    dvel = float(donor.get("daily_velocity") or 0)
    rvel = float(recipient.get("daily_velocity") or 0)
    d_after = int(donor.get("on_hand") or 0) - qty
    r_after = int(recipient.get("on_hand") or 0) + qty
    return {
        "product_id": product_id,
        "brand": donor.get("brand") or recipient.get("brand") or "",
        "category": donor.get("category") or recipient.get("category") or "",
        "from_store": donor.get("store_id"),
        "to_store": recipient.get("store_id"),
        "qty": int(qty),
        "donor_classification": donor.get("classification"),
        "recipient_classification": recipient.get("classification"),
        "donor_on_hand_before": int(donor.get("on_hand") or 0),
        "donor_on_hand_after": d_after,
        "recipient_on_hand_before": int(recipient.get("on_hand") or 0),
        "recipient_on_hand_after": r_after,
        "donor_days_cover_before": donor.get("days_of_cover"),
        "donor_days_cover_after": _round_doc(_days_of_cover(d_after, dvel)),
        "recipient_days_cover_before": recipient.get("days_of_cover"),
        "recipient_days_cover_after": _round_doc(_days_of_cover(r_after, rvel)),
        "reason": (f"{donor.get('classification')} at {donor.get('store_id')} "
                   f"-> {recipient.get('classification')} at {recipient.get('store_id')} "
                   f"(move {int(qty)} unit(s))"),
    }


def propose_moves(stats: List[Dict[str, Any]], *, thresholds: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """The heart: per product, greedily move surplus units from donor stores
    (DEAD first, then largest OVERSTOCK surplus) to recipient stores (highest
    velocity / demand first). A donor never sheds below its own target cover; a
    single-store product or a product with no donor/recipient yields no move.
    Deterministic ordering so the output is stable + testable. PURE -- returns a
    list of proposal dicts; mutates nothing.
    """
    t = _thresholds_with_defaults(thresholds)
    enriched = _ensure_classified(stats, t)
    by_product: Dict[Any, List[Dict[str, Any]]] = {}
    for e in enriched:
        by_product.setdefault(e.get("product_id"), []).append(e)

    moves: List[Dict[str, Any]] = []
    for pid in sorted(by_product.keys(), key=lambda x: str(x)):
        rows = by_product[pid]
        donors = [r for r in rows if int(r.get("surplus_units") or 0) > 0]
        recipients = [r for r in rows if int(r.get("deficit_units") or 0) > 0]
        if not donors or not recipients:
            continue
        # Donor priority: DEAD before OVERSTOCK, then larger surplus, then store.
        donors.sort(key=lambda r: (0 if r.get("classification") == DEAD else 1,
                                   -int(r.get("surplus_units") or 0), str(r.get("store_id"))))
        # Recipient priority: highest demand (velocity), then larger deficit, then store.
        recipients.sort(key=lambda r: (-float(r.get("daily_velocity") or 0),
                                       -int(r.get("deficit_units") or 0), str(r.get("store_id"))))
        donor_remaining = {id(d): int(d.get("surplus_units") or 0) for d in donors}
        for rcp in recipients:
            need = int(rcp.get("deficit_units") or 0)
            for dn in donors:
                if need <= 0:
                    break
                avail = donor_remaining[id(dn)]
                if avail <= 0 or dn.get("store_id") == rcp.get("store_id"):
                    continue
                qty = min(avail, need)
                if qty < 1:
                    continue
                qty = int(qty)
                donor_remaining[id(dn)] -= qty
                need -= qty
                moves.append(_build_move(pid, dn, rcp, qty, t["window_days"]))
    return moves


def summarize(enriched: List[Dict[str, Any]], proposals: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts = {DEAD: 0, OVERSTOCK: 0, HEALTHY: 0, UNDERSTOCK: 0, STOCKOUT: 0}
    for e in enriched or []:
        k = e.get("classification") or HEALTHY
        counts[k] = counts.get(k, 0) + 1
    return {
        "total_proposals": len(proposals or []),
        "total_units_to_move": sum(int(m.get("qty") or 0) for m in (proposals or [])),
        "skus_with_proposals": len({m.get("product_id") for m in (proposals or [])}),
        "classification_counts": counts,
    }


# ---------------------------------------------------------------------------
# DB rollups (read-only). Mirror predictive_reorder's already-fetched-docs
# approach so fake-Mongo and real-Mongo behave identically.
# ---------------------------------------------------------------------------


def _order_status_excluded(status: Any) -> bool:
    return str(status or "").upper() in {"CANCELLED", "DRAFT", "VOID", "VOIDED", "REFUNDED"}


def _coerce_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str):
        v = value.strip()
        if not v:
            return None
        if v.endswith("Z"):
            v = v[:-1]
        try:
            dt = datetime.fromisoformat(v)
            return dt.replace(tzinfo=None) if dt.tzinfo else dt
        except ValueError:
            try:
                dt = datetime.fromisoformat(v[:19])
                return dt.replace(tzinfo=None) if dt.tzinfo else dt
            except ValueError:
                return None
    return None


def _on_hand_by_product_store(db, product_ids: List[str]) -> Dict[Tuple[str, str], int]:
    """Per-(product, store) on-hand from the serialized `stock_units` collection,
    using the SAME available-status definition as inventory._on_hand_by_product
    (the canonical on-hand allowlist + the explicit non-sellable exclusion list).
    Fail-soft -> {}."""
    out: Dict[Tuple[str, str], int] = {}
    if db is None or not product_ids:
        return out
    try:
        from .item_events import ON_HAND_STATUSES, EXCLUDED_STATUSES
        avail = set(ON_HAND_STATUSES)
        excl = set(EXCLUDED_STATUSES)
    except Exception:  # noqa: BLE001
        avail, excl = set(), set()
    try:
        cur = db.get_collection("stock_units").find(
            {"product_id": {"$in": list(product_ids)}},
            {"product_id": 1, "store_id": 1, "status": 1, "quantity": 1})
        rows = list(cur)
    except Exception:  # noqa: BLE001
        return {}
    for row in rows:
        st = row.get("status")
        if st in excl:
            continue
        if avail and not (st in avail or st is None):
            continue
        pid = row.get("product_id")
        store = row.get("store_id")
        if not pid or not store:
            continue
        q = row.get("quantity")
        try:
            q = 1 if q is None else int(q)
        except (TypeError, ValueError):
            q = 1
        out[(str(pid), str(store))] = out.get((str(pid), str(store)), 0) + q
    return out


def _units_sold_by_product_store(db, *, now: datetime, window_days: int) -> Dict[Tuple[str, str], float]:
    """Per-(product, store) units sold in the trailing window. Pure roll-up over
    already-fetched order docs (no pipeline divergence). Fail-soft -> {}."""
    out: Dict[Tuple[str, str], float] = {}
    if db is None:
        return out
    try:
        orders = list(db.get_collection("orders").find({}))
    except Exception:  # noqa: BLE001
        return {}
    cut = now - timedelta(days=max(1, int(window_days)))
    for order in orders:
        if _order_status_excluded(order.get("status")):
            continue
        created = _coerce_dt(order.get("created_at"))
        if created is None or created < cut:
            continue
        store = order.get("store_id") or order.get("store")
        if not store:
            continue
        for item in order.get("items") or []:
            pid = item.get("product_id") or item.get("sku")
            if not pid:
                continue
            qty = item.get("quantity")
            qty = 1 if qty is None else qty
            try:
                qty = float(qty)
            except (TypeError, ValueError):
                qty = 1.0
            if qty <= 0:
                continue
            key = (str(pid), str(store))
            out[key] = out.get(key, 0.0) + qty
    return out


def gather_stats(db, *, window_days: int, brand: Optional[str] = None,
                 category: Optional[str] = None, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Build the per-(product, store) stat list across ALL stores (cross-store
    balancing reads the whole picture; the AUTH store-scope is applied to the
    OUTPUT proposals by the router, not here). Fail-soft -> []."""
    if db is None:
        return []
    if now is None:
        now = datetime.utcnow()
    pquery: Dict[str, Any] = {}
    if brand:
        pquery["brand"] = brand
    if category:
        pquery["category"] = category
    meta: Dict[str, Dict[str, str]] = {}
    try:
        for p in db.get_collection("products").find(
                pquery, {"product_id": 1, "brand": 1, "category": 1, "status": 1}):
            pid = p.get("product_id")
            if not pid:
                continue
            if str(p.get("status") or "active").lower() in _INACTIVE_PRODUCT_STATES:
                continue
            meta[str(pid)] = {"brand": p.get("brand") or "", "category": p.get("category") or ""}
    except Exception:  # noqa: BLE001
        return []
    if not meta:
        return []
    pids = list(meta.keys())
    on_hand = _on_hand_by_product_store(db, pids)
    sold = _units_sold_by_product_store(db, now=now, window_days=window_days)
    keys = set(on_hand.keys()) | {k for k in sold.keys() if k[0] in meta}
    stats: List[Dict[str, Any]] = []
    for (pid, store) in keys:
        if pid not in meta:
            continue
        stats.append({
            "product_id": pid,
            "store_id": store,
            "brand": meta[pid]["brand"],
            "category": meta[pid]["category"],
            "on_hand": int(on_hand.get((pid, store), 0)),
            "units_sold": int(round(sold.get((pid, store), 0.0))),
        })
    return stats
