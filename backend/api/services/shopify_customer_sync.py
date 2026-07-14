"""
IMS 2.0 - Shopify CUSTOMER upsert  (BVI-retirement phase 0)
============================================================
Handles Shopify `customers/create` and `customers/update` webhooks: upsert the
buyer into the IMS customers / CRM store, the SAME way the BVI historical import
did -- dedupe on mobile then email via IMS's canonical customer-dedupe, and merge
rather than clobber.

DEDUPE (reuse, don't reinvent):
  * Identity resolution goes through online_order_mapper._match_or_create_customer,
    which wraps the ONE canonical api.services.customer_service.ensure_customer
    (normalized-mobile dedupe + lenient create, source="ONLINE") and adds the
    email-fallback match/create -- exactly the path an online ORDER's buyer
    resolves through, so a Shopify customer and their orders collapse to ONE
    record.

MERGE, DON'T OVERWRITE (owner requirement):
  * We NEVER clobber richer IMS data with sparser Shopify data. On an update we
    only fill IMS fields that are BLANK (name placeholder, missing email/phone/
    address) and always stamp shopify_customer_id when absent. A non-empty IMS
    field wins.

FAIL-SOFT: no db / repo error -> logged, returns a skip result, never raises.

PUBLIC API:
    upsert_shopify_customer(db, payload, *, topic=None) -> dict
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Placeholder names an online create may have stamped -- treat as "no real name"
# so a genuine Shopify name is allowed to fill them in.
_PLACEHOLDER_NAMES = {"", "customer", "online customer"}


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _meaningful_name(name: Any) -> bool:
    return _norm(name).lower() not in _PLACEHOLDER_NAMES


def _extract_customer(payload: Dict[str, Any]) -> Dict[str, str]:
    """Pull a normalized buyer out of a Shopify Customer webhook payload (the
    payload IS the customer object). Falls back to default_address for phone /
    name when the top-level fields are blank."""
    name = " ".join(
        _norm(p) for p in (payload.get("first_name"), payload.get("last_name")) if _norm(p)
    ).strip()
    phone = _norm(payload.get("phone"))
    email = _norm(payload.get("email"))

    addr = (
        payload.get("default_address")
        if isinstance(payload.get("default_address"), dict)
        else None
    )
    if not addr:
        addrs = payload.get("addresses")
        if isinstance(addrs, list) and addrs and isinstance(addrs[0], dict):
            addr = addrs[0]
    if isinstance(addr, dict):
        if not phone:
            phone = _norm(addr.get("phone"))
        if not name:
            name = _norm(addr.get("name")) or " ".join(
                _norm(p)
                for p in (addr.get("first_name"), addr.get("last_name"))
                if _norm(p)
            ).strip()

    if not name:
        name = email or phone or "Online Customer"

    return {
        "name": name,
        "phone": phone,
        "email": email,
        "shopify_customer_id": _norm(payload.get("id")),
        "address": _format_address(addr) if isinstance(addr, dict) else "",
    }


def _format_address(addr: Dict[str, Any]) -> str:
    parts = [
        addr.get("address1"),
        addr.get("address2"),
        addr.get("city"),
        addr.get("province"),
        addr.get("zip"),
        addr.get("country"),
    ]
    return ", ".join(_norm(p) for p in parts if _norm(p))


def _find_existing(db, buyer: Dict[str, str]):
    """Best-effort existing-customer lookup (shopify_customer_id, then normalized
    mobile, then email) so we can report created-vs-updated + decide the merge.
    Returns the doc or None. Fail-soft."""
    try:
        from ..dependencies import get_customer_repository

        repo = get_customer_repository()
    except Exception:  # noqa: BLE001
        repo = None
    if repo is None:
        return None

    shopify_id = buyer.get("shopify_customer_id")
    if shopify_id and db is not None:
        try:
            coll = db.get_collection("customers")
            if coll is not None:
                doc = coll.find_one({"shopify_customer_id": shopify_id})
                if doc:
                    return doc
        except Exception:  # noqa: BLE001
            pass

    from .online_order_mapper import _normalize_indian_mobile

    phone = _normalize_indian_mobile(buyer.get("phone", ""))
    if phone:
        try:
            found = repo.find_by_mobile(phone)
            if found:
                return found
        except Exception:  # noqa: BLE001
            pass
    email = buyer.get("email")
    if email:
        try:
            finder = getattr(repo, "find_by_email", None)
            if callable(finder):
                found = finder(email)
                if found:
                    return found
        except Exception:  # noqa: BLE001
            pass
    return None


def _merge_fields(customer_id: str, buyer: Dict[str, str]) -> Dict[str, Any]:
    """Fill only BLANK IMS fields from the (sparser) Shopify payload -- never
    clobber a non-empty IMS value. Always stamp shopify_customer_id / email when
    absent. Returns the applied update (for the result), or {} when nothing
    changed. Fail-soft."""
    try:
        from ..dependencies import get_customer_repository

        repo = get_customer_repository()
    except Exception:  # noqa: BLE001
        repo = None
    if repo is None or not customer_id:
        return {}
    try:
        existing = repo.find_by_id(customer_id) or {}
    except Exception:  # noqa: BLE001
        existing = {}

    updates: Dict[str, Any] = {}
    if buyer.get("name") and _meaningful_name(buyer["name"]) and not _meaningful_name(
        existing.get("name")
    ):
        updates["name"] = buyer["name"]
    if buyer.get("email") and not _norm(existing.get("email")):
        updates["email"] = buyer["email"]
    if buyer.get("phone") and not _norm(existing.get("mobile")) and not _norm(
        existing.get("phone")
    ):
        from .online_order_mapper import _normalize_indian_mobile

        norm = _normalize_indian_mobile(buyer["phone"])
        if norm:
            updates["mobile"] = norm
            updates["phone"] = norm
            updates["raw_phone"] = buyer["phone"]
    if buyer.get("shopify_customer_id") and not _norm(
        existing.get("shopify_customer_id")
    ):
        updates["shopify_customer_id"] = buyer["shopify_customer_id"]
    if buyer.get("address") and not _norm(existing.get("address")):
        updates["address"] = buyer["address"]

    if not updates:
        return {}
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        repo.update(customer_id, updates)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_CUSTOMER] merge update failed: %s", exc)
        return {}
    return updates


def upsert_shopify_customer(
    db, payload: Dict[str, Any], *, topic: Optional[str] = None
) -> Dict[str, Any]:
    """Upsert a Shopify customer into IMS, deduped on mobile/email, merging (not
    clobbering) into an existing record. NEVER raises."""
    try:
        payload = payload if isinstance(payload, dict) else {}
        shopify_customer_id = _norm(payload.get("id"))
        buyer = _extract_customer(payload)
        # Nothing to key on -> skip (never mint a keyless orphan customer).
        if not buyer.get("phone") and not buyer.get("email"):
            return {"status": "skipped", "reason": "no_phone_or_email"}
        if db is None:
            return {"status": "simulated", "shopify_customer_id": shopify_customer_id}

        existing = _find_existing(db, buyer)
        was_existing = bool(existing and _norm(existing.get("customer_id")))

        # Resolve online store bucket (reuse the online-order resolver) + dedupe /
        # create via the SAME path an online order's buyer resolves through.
        from .online_order_mapper import (
            _resolve_online_store_id,
            _match_or_create_customer,
        )

        store_id = _resolve_online_store_id({}, db)
        customer_id = _match_or_create_customer(db, buyer, store_id)
        if not customer_id:
            return {
                "status": "skipped",
                "reason": "could_not_resolve_customer",
                "shopify_customer_id": shopify_customer_id,
            }

        # Merge sparse Shopify fields into BLANK IMS fields only (never clobber).
        merged = _merge_fields(customer_id, buyer)

        status = "updated" if was_existing else "created"
        logger.info(
            "[SHOPIFY_CUSTOMER] %s customer=%s shopify_id=%s merged_fields=%s",
            status,
            customer_id,
            shopify_customer_id,
            list(merged.keys()),
        )
        return {
            "status": status,
            "customer_id": customer_id,
            "shopify_customer_id": shopify_customer_id,
            "merged_fields": list(merged.keys()),
        }
    except Exception as exc:  # noqa: BLE001 -- the drain loop must never die here
        logger.warning("[SHOPIFY_CUSTOMER] upsert_shopify_customer failed soft: %s", exc)
        return {"status": "skipped", "reason": f"exception:{type(exc).__name__}"}
