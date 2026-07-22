"""
IMS 2.0 - Abandoned-checkout capture (Shopify checkouts/create + checkouts/update)
==================================================================================
Shopify fires a `checkouts/create` webhook when a shopper reaches the checkout
and `checkouts/update` as they progress. If they never place the order the
checkout is "abandoned". This service persists the RAW ESSENTIALS of each such
checkout into the `abandoned_checkouts` collection so the seller can later see
what nearly-sold (and, downstream, recover it).

SCOPE (deliberately narrow):
  * Upsert keyed on the Shopify checkout token (fallback: numeric id) so
    create + repeated update deliveries collapse to ONE row -- idempotent.
  * Store contact info ONLY (email / phone / name) plus a compact line-item
    summary, totals, currency and the create/update timestamps.
  * CONSENT-AWARE: we store the buyer's marketing-consent flag but NEVER trigger
    any marketing send from here -- capture is storage only. Any recovery
    outreach is a separate, consent-gated decision made elsewhere.
  * `recovered` starts False and is set ONLY on first insert. Flipping it True
    when an order lands with the same checkout token is OUT OF SCOPE here (that
    check happens on order ingest); we just make room for it and never clobber a
    later True.

Contract (mirrors the rest of the Shopify bridge): 100% FAIL-SOFT. A bad or
partial payload -> logged SKIP; no DB -> SIMULATED; every path returns a small
status dict and NEVER raises (the NEXUS drain loop must keep ticking).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# How many line-item rows we keep in the compact summary (a checkout can carry
# many; we only need enough to recognise it, not the full cart).
_MAX_LINE_ITEMS = 25


def _coll(db, name: str):
    """Collection access that works on a real DatabaseConnection (get_collection)
    and the in-memory Mock (subscript). Fail-soft -> None."""
    if db is None:
        return None
    try:
        getter = getattr(db, "get_collection", None)
        if callable(getter):
            return getter(name)
    except Exception:  # noqa: BLE001
        pass
    try:
        return db[name]
    except Exception:  # noqa: BLE001
        return None


def _s(value: Any) -> str:
    """Trimmed string, '' for None."""
    return str(value).strip() if value not in (None, "") else ""


def _num(value: Any) -> Optional[float]:
    """Best-effort float, or None. Shopify money fields arrive as strings."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def checkout_token(payload: Dict[str, Any]) -> str:
    """The natural key for a checkout: Shopify `token` (its stable checkout id),
    falling back to `cart_token`, then the numeric `id`. Pure. '' when none."""
    if not isinstance(payload, dict):
        return ""
    return (
        _s(payload.get("token"))
        or _s(payload.get("cart_token"))
        or _s(payload.get("id"))
    )


def _line_items_summary(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Compact, contact-free line summary: title + sku + quantity per line,
    capped at _MAX_LINE_ITEMS. Pure."""
    out: List[Dict[str, Any]] = []
    for li in (payload.get("line_items") or [])[:_MAX_LINE_ITEMS]:
        if not isinstance(li, dict):
            continue
        try:
            qty = int(li.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0
        out.append(
            {
                "title": _s(li.get("title")) or None,
                "sku": _s(li.get("sku")) or None,
                "quantity": qty,
            }
        )
    return out


def summarize_checkout(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Pure: distil a Shopify checkout webhook payload into the raw essentials we
    persist. Contact info is captured but no marketing action is implied.

    Returns a dict WITHOUT the natural key (the caller stamps token). Item count
    is the summed quantity across all lines (not just the capped summary)."""
    payload = payload if isinstance(payload, dict) else {}
    customer = payload.get("customer") if isinstance(payload.get("customer"), dict) else {}

    total_qty = 0
    for li in payload.get("line_items") or []:
        if isinstance(li, dict):
            try:
                total_qty += int(li.get("quantity") or 0)
            except (TypeError, ValueError):
                pass

    name = " ".join(
        p for p in (_s(customer.get("first_name")), _s(customer.get("last_name"))) if p
    ).strip()

    return {
        "checkout_id": _s(payload.get("id")) or None,
        # Contact only -- stored so the seller can recognise / (consent-gated,
        # elsewhere) recover the cart. NOT a marketing trigger.
        "email": _s(payload.get("email")) or _s(customer.get("email")) or None,
        "phone": _s(payload.get("phone")) or _s(customer.get("phone")) or None,
        "customer_name": name or None,
        "shopify_customer_id": _s(customer.get("id")) or None,
        # Consent flag is RECORDED, never acted on here.
        "marketing_consent": bool(
            payload.get("buyer_accepts_marketing")
            or customer.get("accepts_marketing")
            or False
        ),
        "currency": _s(payload.get("currency")) or _s(payload.get("presentment_currency")) or None,
        "total_price": _num(payload.get("total_price")),
        "subtotal_price": _num(payload.get("subtotal_price")),
        "total_tax": _num(payload.get("total_tax")),
        "item_count": total_qty,
        "line_items": _line_items_summary(payload),
        "abandoned_checkout_url": _s(payload.get("abandoned_checkout_url")) or None,
        "shopify_created_at": _s(payload.get("created_at")) or None,
        "shopify_updated_at": _s(payload.get("updated_at")) or None,
        "completed_at": _s(payload.get("completed_at")) or None,
    }


def capture_checkout(
    db, payload: Dict[str, Any], topic: str = "checkouts/update"
) -> Dict[str, Any]:
    """Upsert one Shopify checkout into `abandoned_checkouts`, keyed on the
    checkout token (natural key). Idempotent: create + repeated update deliveries
    collapse to a single evolving row. Fail-soft -> status dict, never raises.

    `recovered` is initialised False on first insert only and never written here
    afterwards, so a later order-ingest that flips it True (out of scope for this
    module) is preserved.
    """
    token = checkout_token(payload)
    if not token:
        logger.info("[CHECKOUT_CAPTURE] skipped: no checkout token on payload")
        return {"status": "skipped", "reason": "no_token"}

    essentials = summarize_checkout(payload)
    now_iso = datetime.now(timezone.utc).isoformat()

    coll = _coll(db, "abandoned_checkouts")
    if coll is None:
        # No DB (local/test) -> SIMULATED: prove we distilled the payload.
        return {
            "status": "simulated",
            "token": token,
            "item_count": essentials.get("item_count", 0),
        }

    set_fields = {**essentials, "checkout_token": token, "last_topic": str(topic), "updated_at": now_iso}
    try:
        res = coll.update_one(
            {"checkout_token": token},
            {
                "$set": set_fields,
                "$setOnInsert": {
                    "first_seen_at": now_iso,
                    "recovered": False,
                },
            },
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001 -- capture must never crash the drain
        logger.warning("[CHECKOUT_CAPTURE] upsert failed for token=%s: %s", token, exc)
        return {"status": "error", "token": token, "error": str(exc)[:200]}

    created = bool(getattr(res, "upserted_id", None))
    logger.info(
        "[CHECKOUT_CAPTURE] %s checkout token=%s items=%s",
        "captured" if created else "updated",
        token,
        essentials.get("item_count", 0),
    )
    return {
        "status": "captured" if created else "updated",
        "token": token,
        "item_count": essentials.get("item_count", 0),
    }
