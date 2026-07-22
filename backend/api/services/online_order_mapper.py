"""
IMS 2.0 - Online-order MAPPER (Shopify order -> canonical IMS order)  [BVI Phase 3b]
====================================================================================
THE GAP this closes: the signed `/webhooks/shopify` receiver verifies + persists
a Shopify order to `webhook_inbox`, and NEXUS drains it -- but until now there was
NO single, authoritative MAPPER that turns an ingested Shopify ORDER into a
canonical IMS order so online sales flow into Orders / Finance / P&L. This is it.

DESIGN: count-once, do NOT fork.
  * The GST math + invoice-serial allocation + the hard idempotency guards already
    live in `api.services.shopify_ingest.ingest_shopify_order` (Council B10). This
    mapper REUSES that create path verbatim -- it never re-implements the line
    mapping, the inclusive GST extraction, the place-of-supply CGST/SGST/IGST split
    or the per-(store,FY) invoice counter. One create path = revenue counted ONCE.
  * On top of ingest, the mapper adds the three things Phase 3b requires that the
    raw ingest does not do:
      1. VARIANT RESOLUTION -- map each Shopify line's `variant_id` ->
         `catalog_variants` -> the IMS `sku` (then product_id), so the existing
         SKU->product-master HSN/GST resolution inside ingest fires. Fallback order:
         variant_id -> sku already on the line -> title. A pre-pass enriches the
         payload line_items in place; ingest then bills against the real SKU.
      2. CUSTOMER MATCH/CREATE -- match an IMS `customers` row by phone then email,
         else CREATE a minimal customer (channel ONLINE). The order is then stamped
         with the resolved `customer_id` so CRM / loyalty / AR see the same buyer.
      3. STATUS SYNC on re-ingest -- a replayed / updated / cancelled Shopify order
         for an EXISTING IMS order does NOT create a 2nd order; instead it UPDATES
         payment_status / fulfillment_status / status in place (orders/updated,
         orders/cancelled). The hard order-id guard in ingest already prevents the
         double-create; the mapper layers the status update on the duplicate path.

  * STORE bucket: the online channel bills under a configured store. Resolution
    order: explicit `_ims_online_store_id` on the payload -> the `shopify`
    integration config (`online_store_id`) -> ONLINE_STORE_ID env -> a SETTINGS
    value (`online_store_id`) -> the first/primary ACTIVE store -> the stable
    virtual default "BV-ONLINE-01". (shopify_ingest reads the payload field, so the
    mapper resolves the rest and stamps the payload before delegating.)

FAIL-SOFT, end to end: a bad / partial payload yields a logged SKIP result and NEVER
raises (the NEXUS drain loop must keep ticking). No DB -> the underlying ingest
SIMULATES. Every helper swallows its own errors.

PUBLIC API:
    map_shopify_order(payload, db, *, webhook_id=None, topic=None) -> dict
        Idempotently create-or-sync the IMS order for one Shopify order payload.
        Returns the ingest result dict, augmented with the resolved
        {"customer_id", "store_id"} and (on a re-ingest) {"status_synced": bool}.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .phone import normalize_indian_mobile

logger = logging.getLogger(__name__)


# Shopify financial_status -> canonical IMS payment_status.
# (Shopify: pending | authorized | partially_paid | paid | partially_refunded |
#  refunded | voided.) Anything unknown -> UNPAID (safe: it shows as a receivable).
_PAYMENT_STATUS_MAP = {
    "paid": "PAID",
    "partially_paid": "PARTIAL",
    "authorized": "UNPAID",
    "pending": "UNPAID",
    "voided": "CANCELLED",
    "refunded": "REFUNDED",
    "partially_refunded": "PARTIAL_REFUND",
}

# Shopify fulfillment_status -> canonical IMS fulfillment_status.
# (Shopify: null (unfulfilled) | partial | fulfilled | restocked.)
_FULFILLMENT_STATUS_MAP = {
    "fulfilled": "FULFILLED",
    "partial": "PARTIAL",
    "restocked": "RESTOCKED",
    "": "UNFULFILLED",
}

# Shopify cancelled / fulfilled -> the IMS order lifecycle `status`.
# A cancelled Shopify order maps the IMS order to CANCELLED so finance excludes it.
_DELIVERED_FULFILLMENT = {"fulfilled"}


def _f(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _norm(value: Any) -> str:
    return str(value or "").strip()


# ---------------------------------------------------------------------------
# 1) Variant resolution -- enrich each Shopify line with its IMS sku.
# ---------------------------------------------------------------------------


def _resolve_variant_sku(line: Dict[str, Any], variant_repo) -> str:
    """Resolve the IMS `sku` for one Shopify line item.

    Priority:
      1. the line's Shopify `variant_id` -> a `catalog_variants` row carrying that
         `shopify_variant_id` -> its `sku` (the authoritative Online-Store mapping);
      2. the `sku` Shopify already sent on the line (offline POS bills this SKU too);
      3. '' -> ingest then falls back to the product_type/title GST category hint.

    Best-effort: any repo error returns '' so ingest degrades to the hint path.
    """
    existing_sku = _norm(line.get("sku"))

    variant_id = _norm(line.get("variant_id"))
    if variant_id and variant_repo is not None:
        try:
            row = variant_repo.find_one({"shopify_variant_id": variant_id})
            if not row:
                # Shopify GIDs ("gid://shopify/ProductVariant/123") vs bare numeric
                # ids: try the other shape so a mapping stored either way resolves.
                alt = (
                    variant_id.rsplit("/", 1)[-1]
                    if variant_id.startswith("gid://")
                    else f"gid://shopify/ProductVariant/{variant_id}"
                )
                if alt and alt != variant_id:
                    row = variant_repo.find_one({"shopify_variant_id": alt})
            if row and _norm(row.get("sku")):
                return _norm(row.get("sku"))
        except Exception:  # noqa: BLE001 - variant lookup is best-effort
            logger.debug("[ONLINE_MAP] variant lookup failed", exc_info=True)

    # Fall back to the SKU Shopify already put on the line.
    return existing_sku


def _enrich_line_items_with_sku(payload: Dict[str, Any], variant_repo) -> int:
    """Mutate payload['line_items'] in place: stamp the resolved IMS `sku` on each
    line so the downstream ingest resolves the real product master + HSN/GST.
    Returns the count of lines whose sku was filled in from a variant mapping
    (purely for logging). Never raises."""
    filled = 0
    for line in payload.get("line_items") or []:
        if not isinstance(line, dict):
            continue
        before = _norm(line.get("sku"))
        resolved = _resolve_variant_sku(line, variant_repo)
        if resolved and resolved != before:
            line["sku"] = resolved
            filled += 1
        elif resolved:
            line["sku"] = resolved
    return filled


def _get_variant_repo(db):
    """A CatalogVariantRepository bound to the live `catalog_variants` collection,
    or None. Fail-soft (no DB / import error -> None -> ingest uses the hint path)."""
    if db is None:
        return None
    try:
        from database.repositories import CatalogVariantRepository

        coll = db.get_collection("catalog_variants")
        if coll is None:
            return None
        return CatalogVariantRepository(coll)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# 2) Store bucket resolution (settings / primary-store fallback).
# ---------------------------------------------------------------------------


def _resolve_online_store_id(payload: Dict[str, Any], db) -> str:
    """The store the online channel bills under. Resolution order:
      1. explicit `_ims_online_store_id` on the payload (test / manual override);
      2. the `shopify` integration config `online_store_id`;
      3. the ONLINE_STORE_ID env var;
      4. a settings value (`settings` collection, key `online_store_id`);
      5. the first/primary ACTIVE store (store directory);
      6. the stable virtual default "BV-ONLINE-01".
    Never raises; each lookup is independently fail-soft."""
    explicit = _norm(payload.get("_ims_online_store_id"))
    if explicit:
        return explicit

    if db is not None:
        # (2) integration config
        try:
            integ = db.get_collection("integrations")
            if integ is not None:
                doc = integ.find_one({"type": "shopify"})
                cfg = (doc or {}).get("config") or {}
                cand = _norm(cfg.get("online_store_id"))
                if cand:
                    return cand
        except Exception:  # noqa: BLE001
            pass

    env_val = _norm(os.getenv("ONLINE_STORE_ID"))
    if env_val:
        return env_val

    if db is not None:
        # (4) settings collection -- a single settings doc may carry the key.
        try:
            settings = db.get_collection("settings")
            if settings is not None:
                row = settings.find_one({"key": "online_store_id"})
                cand = _norm((row or {}).get("value"))
                if cand:
                    return cand
                # Some deployments keep one settings singleton doc.
                singleton = settings.find_one({})
                cand = _norm((singleton or {}).get("online_store_id"))
                if cand:
                    return cand
        except Exception:  # noqa: BLE001
            pass

        # (5) first/primary active store.
        try:
            from ..dependencies import get_store_repository

            store_repo = get_store_repository()
            if store_repo is not None:
                actives = store_repo.find_active() or []
                if actives:
                    primary = next(
                        (s for s in actives if s.get("is_primary") or s.get("primary")),
                        actives[0],
                    )
                    cand = _norm(primary.get("store_id"))
                    if cand:
                        return cand
        except Exception:  # noqa: BLE001
            pass

    return "BV-ONLINE-01"


# ---------------------------------------------------------------------------
# 3) Customer match / create.
# ---------------------------------------------------------------------------


def _extract_buyer(payload: Dict[str, Any]) -> Dict[str, str]:
    """Pull a normalized buyer (name / phone / email / state) out of a Shopify
    order payload, looking at the nested customer object then the top-level
    contact fields + the shipping/billing address."""
    cust = payload.get("customer") if isinstance(payload.get("customer"), dict) else {}

    name = " ".join(
        _norm(p) for p in (cust.get("first_name"), cust.get("last_name")) if _norm(p)
    ).strip()

    phone = _norm(cust.get("phone")) or _norm(payload.get("phone"))
    email = (
        _norm(cust.get("email"))
        or _norm(payload.get("email"))
        or _norm(payload.get("contact_email"))
    )

    # Address fallbacks for phone + a human name.
    for key in ("shipping_address", "billing_address", "default_address"):
        addr = cust.get(key) if isinstance(cust.get(key), dict) else payload.get(key)
        if isinstance(addr, dict):
            if not phone:
                phone = _norm(addr.get("phone"))
            if not name:
                name = (
                    _norm(addr.get("name"))
                    or " ".join(
                        _norm(p)
                        for p in (addr.get("first_name"), addr.get("last_name"))
                        if _norm(p)
                    ).strip()
                )

    if not name:
        name = email or phone or "Online Customer"

    return {
        "name": name,
        "phone": phone,
        "email": email,
        "shopify_customer_id": _norm(cust.get("id")),
    }


def _normalize_indian_mobile(phone: str) -> str:
    """Reduce a Shopify phone (often '+91 98xxxxxxxx' / '0098...') to the bare
    10-digit Indian mobile IMS stores, so a match against an existing customer
    works. Delegates to the ONE canonical normalizer (api.services.phone) so the
    stored form never drifts from natively-created customers. Returns '' when no
    valid Indian mobile can be derived (the mapper is fail-soft and must never
    raise -- normalize_indian_mobile's ValueError is swallowed here)."""
    try:
        return normalize_indian_mobile(phone) or ""
    except ValueError:
        return ""


# ---------------------------------------------------------------------------
# Contact-tier model (owner-approved 2026-07-20)
# ---------------------------------------------------------------------------
# Mobile stays the PRIMARY retail identity (loyalty / WhatsApp / Rx need it), but
# an EMAIL-ONLY online buyer must no longer be dropped: it is captured as a
# customer record flagged contact_tier="MARKETING" (no loyalty, no WhatsApp
# sends, excluded from POS pickers where a tier filter opts in). It AUTO-UPGRADES
# to a full customer (contact_tier="FULL") the moment a phone appears for it.
#
# NOTE: this customer-record field is a DIFFERENT concept from
# ``match_keys.contact_tier`` (which labels an ad-audience row PHONE_ONLY /
# EMAIL_ONLY / ...). They live in separate namespaces and never interact.
CONTACT_TIER_MARKETING = "MARKETING"
CONTACT_TIER_FULL = "FULL"


def _is_full_record(doc: Dict[str, Any]) -> bool:
    """A record an email-only contact must NEVER be merged into by email alone:
    it carries a phone (a real, phone-keyed customer identity) OR is explicitly
    tagged contact_tier=FULL. A no-phone MARKETING (or legacy untagged email-only)
    record is NOT full and may be deduped/upgraded. Families share emails, so a
    shared email hitting a phone-carrying record is a DIFFERENT person."""
    if not isinstance(doc, dict):
        return False
    if _norm(doc.get("mobile")) or _norm(doc.get("phone")):
        return True
    return _norm(doc.get("contact_tier")).upper() == CONTACT_TIER_FULL


def _upgrade_marketing_to_full(
    repo, email: str, phone_norm: str, raw_phone: str, shopify_id: str = ""
) -> Optional[str]:
    """Auto-upgrade an email-only MARKETING contact to a FULL customer when a
    phone first appears for it. Returns the upgraded customer_id, or None when
    there is no upgradeable MARKETING record for this email.

    Guards (owner dedupe rules 2026-07-20):
      * phone-first still wins -- if ANY record already keys on this mobile we do
        NOT intercept (return None so the canonical mobile dedupe resolves it);
      * we NEVER touch a phone-carrying / FULL record matched only by a shared
        email (families share emails) -- only a no-phone MARKETING contact.
    Fail-soft: any repo error returns None (the caller then creates/dedupes as
    usual)."""
    email = _norm(email).lower()  # casefolded dedupe key (exact-match find_by_email)
    if not email or not phone_norm:
        return None
    try:
        if repo.find_by_mobile(phone_norm):
            return None  # an existing mobile-keyed record -> phone-first dedupe wins
    except Exception:  # noqa: BLE001
        return None
    try:
        match = repo.find_by_email(email)
    except Exception:  # noqa: BLE001
        return None
    if not match or not _norm(match.get("customer_id")):
        return None
    if _is_full_record(match):
        return None  # never merge a phone onto a FULL / shared-email family record
    cid = _norm(match.get("customer_id"))
    updates: Dict[str, Any] = {
        "mobile": phone_norm,
        "phone": phone_norm,
        "raw_phone": raw_phone or phone_norm,
        "contact_tier": CONTACT_TIER_FULL,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if shopify_id and not _norm(match.get("shopify_customer_id")):
        updates["shopify_customer_id"] = shopify_id
    try:
        repo.update(cid, updates)
    except Exception:  # noqa: BLE001
        logger.debug("[ONLINE_MAP] marketing->full upgrade write failed", exc_info=True)
        return None
    logger.info("[ONLINE_MAP] upgraded MARKETING contact %s -> FULL (phone added)", cid)
    return cid


def _match_or_create_customer(
    db, buyer: Dict[str, str], store_id: str
) -> Optional[str]:
    """Return the IMS `customer_id` for this online buyer -- matching an existing
    customer by phone then email, else creating a minimal ONLINE customer.

    Unification step-5: the MOBILE-keyed dedup+create is now delegated to the ONE
    canonical ``api.services.customer_service.ensure_customer`` (source="ONLINE"),
    so an online buyer resolves to the SAME single record as an in-store walk-in
    with the same number. This wrapper keeps the two online-specific concerns the
    generic service deliberately doesn't own: (1) the EMAIL-fallback match/create
    for buyers with no usable phone, and (2) stamping the Shopify linkage
    (``shopify_customer_id`` + ``email``) onto a freshly-minted ONLINE record so
    the Online-Store dashboard's "customers joined from Shopify" count (which keys
    on shopify_customer_id) still works.

    Behaviour change (intentional, step-5): a NEW phone-keyed record now carries the
    canonical skeleton + customer ``source="ONLINE"`` (was "shopify"; the ORDER doc
    still carries source="shopify", untouched) and the full store-key set, and
    dedups against in-store customers. ``channel="ONLINE"``, ``shopify_customer_id``,
    ``raw_phone`` (step-2) and ``email`` are all preserved.

    Fully fail-soft: no DB / repo error / nothing to key on -> returns None and the
    order is still created (carrying the buyer's name+phone on the order doc, exactly
    as before). NEVER raises.
    """
    if db is None:
        return None
    try:
        from ..dependencies import get_customer_repository

        repo = get_customer_repository()
    except Exception:  # noqa: BLE001
        repo = None
    if repo is None:
        return None

    # Store ONLY the canonical normalized mobile (no raw-string fallback): a
    # garbage/foreign number must not be persisted as a fake "mobile" that can
    # never dedup. The original input is preserved verbatim under raw_phone.
    raw_phone = _norm(buyer.get("phone"))
    phone = _normalize_indian_mobile(buyer.get("phone", ""))
    email = _norm(buyer.get("email"))
    # Normalized lowercase email is the dedupe key for email-only MARKETING
    # contacts (owner rule 2026-07-20). find_by_email is an exact match, so the
    # stored value AND every lookup use this same casefolded form.
    email_key = email.lower()
    shopify_id = buyer.get("shopify_customer_id") or ""

    # --- phone path: delegate the dedup+create to the canonical service --------
    if phone:
        # AUTO-UPGRADE (owner model 2026-07-20): a phone has now appeared for an
        # email-only MARKETING contact. Phone-first dedupe still wins (an existing
        # mobile-keyed record is matched by ensure_customer below); we only
        # intercept to UPGRADE a pre-existing MARKETING record that shares this
        # email -> flip it to FULL + set its mobile, instead of minting a
        # duplicate. Skipped when there is no email (nothing to match on) or no
        # MARKETING record -> the phone create path stays byte-identical.
        if email_key:
            upgraded_id = _upgrade_marketing_to_full(
                repo, email_key, phone, raw_phone, shopify_id
            )
            if upgraded_id:
                return upgraded_id
        from .customer_service import ensure_customer

        try:
            customer_id, created = ensure_customer(
                db,
                mobile=phone,
                name=buyer.get("name") or "Online Customer",
                store_id=store_id,
                source="ONLINE",
                raw_phone=raw_phone,
            )
        except Exception:  # noqa: BLE001 -- the mapper must never raise
            logger.debug("[ONLINE_MAP] ensure_customer failed", exc_info=True)
            customer_id, created = (None, False)
        if customer_id:
            # Stamp the Shopify-specific fields the generic skeleton omits onto a
            # NEWLY created record (a dedup match keeps its existing identity).
            if created and (email or shopify_id):
                try:
                    repo.update(
                        customer_id,
                        {
                            k: v
                            for k, v in {
                                "email": email or None,
                                "shopify_customer_id": shopify_id,
                            }.items()
                            if v is not None
                        },
                    )
                except Exception:  # noqa: BLE001
                    logger.debug("[ONLINE_MAP] shopify-stamp failed", exc_info=True)
            return _norm(customer_id)
        # Service couldn't resolve via phone -> fall through to the email path.

    # --- email path: match an existing customer by email, else create ----------
    # The canonical service is mobile-keyed; an email-only buyer is handled here so
    # the prior behaviour (email-only buyers DO become customers) is preserved.
    if email_key:
        try:
            found = repo.find_by_email(email_key)
            # Email dedupe only collapses into a NON-full (MARKETING) contact. An
            # email shared with a phone-carrying / FULL record belongs to a
            # DIFFERENT person (families share emails) -> we do NOT merge; a fresh
            # MARKETING contact is created below instead.
            if found and _norm(found.get("customer_id")) and not _is_full_record(found):
                return _norm(found.get("customer_id"))
        except Exception:  # noqa: BLE001
            logger.debug("[ONLINE_MAP] email match failed", exc_info=True)

    # Nothing to identify the buyer by -> don't mint an orphan keyless customer.
    if not phone and not email:
        return None

    # --- create an email-only (or create-failed phone) minimal ONLINE customer --
    now = datetime.now(timezone.utc).isoformat()
    customer_id = str(uuid.uuid4())
    doc = {
        "customer_id": customer_id,
        "name": buyer.get("name") or "Online Customer",
        "mobile": phone,
        "phone": phone,
        "raw_phone": raw_phone,
        "email": email,
        "customer_type": "B2C",
        "source": "ONLINE",
        "channel": "ONLINE",
        "home_store_id": store_id,
        "preferred_store_id": store_id,
        "primary_store_id": store_id,
        "store_ids": [store_id] if store_id else [],
        "is_active": True,
        "loyalty_points": 0,
        "store_credit": 0.0,
        "total_purchases": 0,
        "patients": [],
        "shopify_customer_id": shopify_id,
        "created_at": now,
        "updated_at": now,
    }
    # Owner model (2026-07-20): an email-only online contact is captured as a
    # MARKETING-tier customer (no loyalty / no WhatsApp / excluded from POS
    # pickers) that auto-upgrades to FULL the moment a phone appears. A record
    # that already carries a phone here (the ensure_customer-failed fallback) is a
    # normal full customer, so it stays untagged (absent tier == FULL).
    if email and not phone:
        doc["contact_tier"] = CONTACT_TIER_MARKETING
        # Store the casefolded email so a re-delivery in any surface case dedupes
        # (find_by_email is exact-match).
        doc["email"] = email_key
    try:
        created = repo.create(doc)
        if created and _norm(created.get("customer_id")):
            return _norm(created.get("customer_id"))
        return customer_id
    except Exception:  # noqa: BLE001
        # A racing create (unique mobile/email) -> re-read by phone/email.
        try:
            if phone:
                found = repo.find_by_mobile(phone)
                if found and _norm(found.get("customer_id")):
                    return _norm(found.get("customer_id"))
            if email_key:
                found = repo.find_by_email(email_key)
                if found and _norm(found.get("customer_id")):
                    return _norm(found.get("customer_id"))
        except Exception:  # noqa: BLE001
            pass
        logger.debug("[ONLINE_MAP] customer create failed", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# 4) Status sync (orders/updated, orders/cancelled re-ingest).
# ---------------------------------------------------------------------------


def _derive_statuses(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Canonical (payment_status, fulfillment_status, order_status) from a Shopify
    payload's financial_status / fulfillment_status / cancelled_at."""
    fin = _norm(payload.get("financial_status")).lower()
    ful = _norm(payload.get("fulfillment_status")).lower()
    cancelled_at = payload.get("cancelled_at")

    payment_status = _PAYMENT_STATUS_MAP.get(fin, "UNPAID")
    fulfillment_status = _FULFILLMENT_STATUS_MAP.get(ful, "UNFULFILLED")

    if cancelled_at:
        order_status = "CANCELLED"
    elif fin == "refunded":
        order_status = "REFUNDED"
    elif ful in _DELIVERED_FULFILLMENT:
        order_status = "DELIVERED"
    else:
        order_status = "CONFIRMED"

    return {
        "payment_status": payment_status,
        "fulfillment_status": fulfillment_status,
        "order_status": order_status,
        "cancelled": bool(cancelled_at),
    }


def _sync_existing_order_status(
    db, shopify_order_id: str, payload: Dict[str, Any]
) -> bool:
    """Update an EXISTING IMS order's status fields from a re-ingested Shopify
    payload (orders/updated, orders/paid, orders/cancelled). Does NOT touch money
    lines / the GST invoice (those are immutable once minted) -- only the lifecycle
    status, payment_status, fulfillment_status, balance_due + amount_paid on a
    paid transition, and cancelled_at. Returns True on a write. Fail-soft."""
    if db is None or not shopify_order_id:
        return False
    try:
        orders_coll = db.get_collection("orders")
    except Exception:  # noqa: BLE001
        orders_coll = None
    if orders_coll is None:
        return False

    try:
        existing = orders_coll.find_one({"shopify_order_id": shopify_order_id})
    except Exception:  # noqa: BLE001
        existing = None
    if not existing:
        return False

    # HISTORICAL import guard: a pre-IMS order imported for customer-360
    # history only (scripts/migrate_bvi_pim.py orders leg; status="HISTORICAL",
    # source="bvi_import") was settled OUTSIDE IMS books and is excluded from
    # every revenue/GST/P&L aggregation BY ITS STATUS. A late Shopify
    # orders/updated webhook (e.g. the owner archiving an old order) must
    # never flip that status to DELIVERED/CONFIRMED -- that would silently
    # start counting the order as IMS revenue.
    if existing.get("historical") or existing.get("source") == "bvi_import":
        logger.info(
            "[ONLINE_MAP] skip status sync for HISTORICAL import shopify_order=%s",
            shopify_order_id,
        )
        return False

    st = _derive_statuses(payload)
    grand_total = _f(existing.get("grand_total"))
    update: Dict[str, Any] = {
        "payment_status": st["payment_status"],
        "fulfillment_status": st["fulfillment_status"],
        "status": st["order_status"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if st["payment_status"] == "PAID":
        update["amount_paid"] = grand_total
        update["balance_due"] = 0.0
    if st["cancelled"]:
        update["cancelled_at"] = _norm(payload.get("cancelled_at"))

    try:
        orders_coll.update_one({"shopify_order_id": shopify_order_id}, {"$set": update})
        logger.info(
            "[ONLINE_MAP] synced status for shopify_order=%s -> status=%s payment=%s "
            "fulfillment=%s",
            shopify_order_id,
            st["order_status"],
            st["payment_status"],
            st["fulfillment_status"],
        )
        return True
    except Exception:  # noqa: BLE001
        logger.debug("[ONLINE_MAP] status sync write failed", exc_info=True)
        return False


def _stamp_order_customer(
    db, shopify_order_id: str, customer_id: Optional[str]
) -> None:
    """Best-effort: reconcile the freshly created order doc's customer linkage with
    the customer the MAPPER actually resolved (ingest doesn't know about the matched
    IMS customer -- it copies the raw Shopify ``customer.id`` onto the order).

    Two cases (unification step-4 -- phantom-profile fix):
      * customer_id resolved -> stamp the canonical IMS ``customer_id`` so CRM /
        loyalty / AR see the same buyer.
      * customer_id is None (a GUEST: no usable phone AND no email, so NO IMS
        customer was minted) -> NULL the order's ``customer_id`` (it currently holds
        the raw Shopify id, a dangling non-IMS reference) and mark the order as a
        guest. The buyer's name/phone remain on the order's ``customer_name`` /
        ``customer_phone`` snapshot, so the sale is fully recorded against an
        unidentified buyer instead of a phantom customer record.

    Fail-soft: any error is swallowed (the order itself is already created)."""
    if db is None or not shopify_order_id:
        return
    try:
        orders_coll = db.get_collection("orders")
        if orders_coll is None:
            return
        if customer_id:
            update = {"customer_id": customer_id, "is_guest_order": False}
        else:
            # GUEST path: clear the phantom Shopify-id link, mark unidentified.
            update = {"customer_id": None, "is_guest_order": True}
        orders_coll.update_one(
            {"shopify_order_id": shopify_order_id},
            {"$set": update},
        )
    except Exception:  # noqa: BLE001
        logger.debug("[ONLINE_MAP] customer_id stamp failed", exc_info=True)


# ---------------------------------------------------------------------------
# PUBLIC: map_shopify_order
# ---------------------------------------------------------------------------


def map_shopify_order(
    payload: Dict[str, Any],
    db,
    *,
    webhook_id: Optional[str] = None,
    topic: Optional[str] = None,
) -> Dict[str, Any]:
    """Idempotently create-or-sync the canonical IMS order for one Shopify order.

    Steps (count-once):
      1. Resolve the online store bucket (settings / primary-store fallback) and
         stamp it on the payload so the shared ingest bills under it.
      2. Pre-pass: resolve each line's variant_id -> catalog_variants -> IMS sku
         (fallback sku-on-line, then title) and stamp it on the line.
      3. Match/create the IMS customer (phone -> email -> minimal create).
      4. Delegate to shopify_ingest.ingest_shopify_order (the ONE create path with
         GST + invoice serial + hard order-id/webhook-id idempotency).
         * created -> stamp the resolved customer_id on the order.
         * duplicate/replayed -> the order already exists; SYNC its status fields
           from this (possibly updated/cancelled) payload instead of re-creating.

    Returns the ingest result dict, augmented:
      {... , "customer_id": <id or None>, "store_id": <bucket>,
       "status_synced": <bool, only on a re-ingest>}.

    NEVER raises -- a bad payload yields {"status": "skipped", "reason": ...}. The
    NEXUS drain loop relies on this.
    """
    try:
        payload = payload if isinstance(payload, dict) else {}

        shopify_order_id = _norm(payload.get("id")) or _norm(payload.get("order_id"))
        # A cancelled / updated webhook for an order we already booked may not carry
        # line_items; in that case we still want to SYNC status. Otherwise (no id, or
        # a create with no lines) there is nothing to do.
        if not shopify_order_id:
            return {"status": "skipped", "reason": "no_shopify_order_id"}

        # (1) store bucket -> stamp on the payload (ingest reads _ims_online_store_id).
        store_id = _resolve_online_store_id(payload, db)
        payload["_ims_online_store_id"] = store_id

        # If this is a status-only re-ingest (no line_items) of an order we already
        # have, sync status and return without touching the create path.
        if not payload.get("line_items"):
            synced = _sync_existing_order_status(db, shopify_order_id, payload)
            if synced:
                return {
                    "status": "status_synced",
                    "shopify_order_id": shopify_order_id,
                    "store_id": store_id,
                    "status_synced": True,
                }
            return {
                "status": "skipped",
                "reason": "no_line_items",
                "shopify_order_id": shopify_order_id,
                "store_id": store_id,
            }

        # (2) variant -> sku enrichment.
        variant_repo = _get_variant_repo(db)
        filled = _enrich_line_items_with_sku(payload, variant_repo)
        if filled:
            logger.info(
                "[ONLINE_MAP] resolved %d Shopify variant(s) -> IMS sku for order %s",
                filled,
                shopify_order_id,
            )

        # (3) customer match/create.
        buyer = _extract_buyer(payload)
        customer_id = _match_or_create_customer(db, buyer, store_id)
        # Stamp the resolved IMS customer id so the shared ingest's clinical
        # FLAG & HOLD check matches against THIS customer's prescriptions (the raw
        # Shopify customer id is not the IMS one; without this the Rx-match would
        # miss and over-hold). Absent (guest) -> ingest falls back to the Shopify
        # id, which simply won't match -> the order is flagged for staff to verify.
        if customer_id:
            payload["_ims_customer_id"] = customer_id

        # (4) delegate to the single, idempotent create path.
        # The shared ingest only creates on orders/create | orders/paid. When THIS
        # delivery is an orders/updated / orders/cancelled that still carries
        # line_items for an order we have NOT yet booked (we missed the create
        # webhook), we still want the order created exactly once -- so we normalize
        # the topic to a create for ingest. The real lifecycle status (paid /
        # cancelled / fulfilled) is then applied from the actual payload by the
        # status-sync below, independent of the Shopify topic name.
        from .shopify_ingest import ingest_shopify_order

        ingest_topic = topic
        norm_topic = _norm(topic).lower()
        if norm_topic and norm_topic not in ("orders/create", "orders/paid"):
            ingest_topic = "orders/create"

        result = ingest_shopify_order(
            db, payload, webhook_id=webhook_id, topic=ingest_topic
        )
        result = result if isinstance(result, dict) else {"status": "error"}
        status = result.get("status")

        # Reconcile the created order's customer linkage with what the mapper
        # resolved. ALWAYS run on a create (not only when a customer matched): a
        # GUEST (no phone + no email -> customer_id is None) must have the raw
        # Shopify-id link cleared to NULL + be marked is_guest_order, so a buyer we
        # can never dedup is recorded as an unidentified sale, NOT a phantom record.
        if status == "created":
            _stamp_order_customer(db, shopify_order_id, customer_id)

        # On a duplicate / replayed delivery the order already exists -> SYNC its
        # status from this payload (it may be an orders/updated or orders/paid that
        # advanced financial_status / fulfillment since the first ingest).
        status_synced = False
        if status in ("duplicate", "replayed"):
            status_synced = _sync_existing_order_status(db, shopify_order_id, payload)

        result["customer_id"] = customer_id
        result["store_id"] = store_id
        if status in ("duplicate", "replayed"):
            result["status_synced"] = status_synced
        return result
    except Exception as exc:  # noqa: BLE001 - the drain loop must never die here
        logger.warning("[ONLINE_MAP] map_shopify_order failed soft: %s", exc)
        return {"status": "skipped", "reason": f"exception:{type(exc).__name__}"}
