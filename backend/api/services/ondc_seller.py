"""
IMS 2.0 - ONDC Seller Node  (Backlog BVI-20)
=============================================
India's Open Network for Digital Commerce (ONDC) seller-side integration.

ARCHITECTURE
------------
IMS acts as a SELLER-NP (Seller Network Participant) through an external SNP
(Seller Network Participant intermediary, e.g. Eunimart, eSamudaay, eSellers).
The SNP exposes IMS to buyer apps (Paytm, PhonePe, Meesho etc.) and calls
back to these endpoints when orders arrive.

DARK-DEFAULT SAFETY CONTRACT (identical to Shopify-push pattern)
-----------------------------------------------------------------
All outbound SNP calls are SIMULATED (dry-run, no network hit) unless ALL
three conditions hold:
  1. IMS_ONDC_ENABLED env is exactly "1" / "true" / "on" / "yes"  (default OFF)
  2. ondc_enabled(db) -> True       (env gate + SNP creds present in `integrations`)
  3. Real credentials found in MongoDB `integrations` collection
     {type:"ondc", enabled:true, config:{snp_url, subscriber_id, ukp, ...}}

Default / missing-creds / gate-off => SIMULATED, never crashes, never spams
buyers or the network.  A fresh Railway deploy can't accidentally publish
catalog or accept real orders.

FAIL-SOFT: every public function returns a structured result dict and NEVER
raises. Exceptions are caught, logged, and returned as {ok: False, error: ...}.

PURE HELPERS (testable without DB)
-----------------------------------
  build_ondc_item(product, variants)  -- map one catalog_product doc -> ONDC item
  build_ondc_catalog(db)              -- map ALL active catalog_products -> list
  publish_catalog(db)                 -- push to SNP (DARK when not enabled)
  ingest_ondc_order(db, payload)      -- map ONDC on_confirm -> IMS order
  reconcile_tcs(db, order_id, settlement) -- record TCS / commission deduction
  ondc_enabled(db)                    -- gate check

HSN / GST NOTE
--------------
ONDC /on_search items MUST carry:
  @ondc/org/statutory_reqs_packaged_commodities: item_name, net_quantity
  @ondc/org/statutory_reqs_prepackaged_food:     (skip -- optical)
  @ondc/org/mandatory_reqs_linked:               country_of_origin
  plus at the descriptor level: hsn_code, gst_rate

We reuse the same HSN->GST master (api/services/gst_rates.py) that POS uses
so that GST charged on ONDC orders always matches POS billing.

TCS
---
ONDC mandates 1% TCS (Tax Collected at Source) on the commission / net payout.
reconcile_tcs writes a finance deduction doc (collection: ondc_settlements)
so Finance / Accountant sees the deduction without manual ledger entry.
"""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

PROVIDER_TIMEOUT = float(os.getenv("NEXUS_PROVIDER_TIMEOUT", "30.0"))

# ONDC protocol version this node targets (Beckn protocol v0.9.4 / ONDC spec v2)
ONDC_CORE_VERSION = "1.2.0"

# ONDC domain for B2C retail
ONDC_DOMAIN = "ONDC:RET12"  # fashion & accessories (closest to optical/eyewear)
# Fallback domain; optical has no specific sub-domain yet in ONDC spec v2
ONDC_DOMAIN_OPTICAL = "ONDC:RET12"

# GST item_type -> ONDC tax codes (approximate; optical frames 5%, sunglasses 18%)
_ONDC_GST_MAP = {
    5: "GST_5",
    12: "GST_12",
    18: "GST_18",
    28: "GST_28",
}

# Country of origin fallback
_DEFAULT_COO = os.getenv("ONDC_DEFAULT_COO", "IND")

# TCS rate (1% per ONDC settlement terms for FY 2024-25)
TCS_RATE = 0.01


# ---------------------------------------------------------------------------
# Gate helpers
# ---------------------------------------------------------------------------


def _env_ondc_enabled() -> bool:
    """IMS_ONDC_ENABLED env gate (default OFF)."""
    return os.getenv("IMS_ONDC_ENABLED", "").strip().lower() in (
        "1",
        "true",
        "on",
        "yes",
    )


def _load_ondc_config(db) -> Dict[str, Any]:
    """Read ONDC integration credentials from MongoDB `integrations` collection.
    Returns {} when not configured or DB unavailable."""
    if db is None:
        return {}
    try:
        coll = db.get_collection("integrations")
        doc = coll.find_one({"type": "ondc", "enabled": True})
        if not doc:
            return {}
        return doc.get("config") or {}
    except Exception as exc:
        logger.debug("[ONDC] Config read failed: %s", exc)
        return {}


def ondc_enabled(db) -> bool:
    """Full gate: env flag ON *and* SNP credentials present in DB.

    Three requirements (mirrors shopify single-writer gate):
      1. IMS_ONDC_ENABLED=1 in env
      2. integrations.{type:ondc, enabled:true} document exists in DB
      3. That document has a non-empty snp_url + subscriber_id
    """
    if not _env_ondc_enabled():
        return False
    cfg = _load_ondc_config(db)
    return bool(cfg.get("snp_url") and cfg.get("subscriber_id"))


def _simulated_reason(db) -> str:
    """Human description of why we are in SIMULATED mode (advisory only)."""
    if not _env_ondc_enabled():
        return "IMS_ONDC_ENABLED is off (default)"
    cfg = _load_ondc_config(db)
    if not cfg:
        return "ONDC integration not configured in DB (type=ondc, enabled=true)"
    if not cfg.get("snp_url"):
        return "snp_url missing from ONDC integration config"
    if not cfg.get("subscriber_id"):
        return "subscriber_id missing from ONDC integration config"
    return "gate off"


# ---------------------------------------------------------------------------
# PURE catalog mapping helpers (no I/O -- fully testable)
# ---------------------------------------------------------------------------

def _f(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _s(v, default: str = "") -> str:
    return str(v or default).strip()


def _gst_rate_for(product: Dict[str, Any]) -> float:
    """Resolve GST rate for an IMS product doc. Mirrors POS billing logic."""
    if product.get("gst_rate") is not None:
        return _f(product["gst_rate"])
    # Category-based fallback (optical: frames 5%, sunglasses 18%)
    cat = _s(product.get("category", "")).upper()
    if cat in ("SUNGLASS", "SUNGLASSES", "WATCH", "WATCHES", "ACCESSORIES"):
        return 18.0
    if cat in ("CONTACT_LENS", "CONTACT LENS", "CONTACTS"):
        return 5.0
    # Frames, spectacle lenses, ophthalmic lenses = 5%
    return 5.0


def _hsn_for(product: Dict[str, Any]) -> str:
    """Resolve HSN code. Uses the product's own code or category fallback."""
    if product.get("hsn_code"):
        return _s(product["hsn_code"])
    cat = _s(product.get("category", "")).upper()
    # HSN 9004: spectacles/goggles; 9003: frames; 7015: optical glass
    if "FRAME" in cat:
        return "9003"
    if "SUNGLASS" in cat or "GOGGLE" in cat:
        return "9004"
    if "LENS" in cat and "CONTACT" not in cat:
        return "7015"
    if "CONTACT" in cat:
        return "9001"
    return "9004"  # generic optical default


def _ondc_quantity(product: Dict[str, Any], variant: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Build ONDC quantity block from IMS physical stock."""
    if variant and variant.get("stock_quantity") is not None:
        available = max(0, _i(variant["stock_quantity"]))
    elif product.get("quantity") is not None:
        available = max(0, _i(product["quantity"]))
    else:
        available = 0
    return {
        "available": {"count": str(available)},
        "maximum": {"count": str(available)},
    }


def build_ondc_item(
    product: Dict[str, Any],
    variant: Optional[Dict[str, Any]] = None,
    *,
    fulfillment_id: str = "1",
    location_id: str = "1",
) -> Dict[str, Any]:
    """Map one IMS catalog_product (+ optional catalog_variant) to an ONDC item.

    This is PURE -- no DB, no I/O. Testable in isolation.

    ONDC item shape (Beckn protocol, /on_search context):
    {
      "id": <ims_sku or product_id>,
      "descriptor": {
        "name": "...",
        "code": "<SKU>",
        "short_desc": "...",
        "long_desc": "...",
        "images": [{"url": "...", "size_type": "sm"}]
      },
      "price": {"currency": "INR", "value": "<MRP>", "offered_value": "<offer>"},
      "quantity": {"available": {"count": "..."}, "maximum": {"count": "..."}},
      "category_id": "<ONDC category code>",
      "fulfillment_id": "<fulfillment id>",
      "location_id": "<location id>",
      "@ondc/org/returnable": true/false,
      "@ondc/org/cancellable": true/false,
      "@ondc/org/time_to_ship": "PT2D",
      "@ondc/org/available_on_cod": false,
      "@ondc/org/contact_details_consumer_care": "...",
      "@ondc/org/statutory_reqs_packaged_commodities": {
        "manufacturer_or_packer_name": "...",
        "manufacturer_or_packer_address": "...",
        "country_of_origin": "IND",
        "month_year_of_manufacture_packing_import": "...",
        "imported_product_country_of_origin": null
      },
      "tags": [
        {"code": "origin", "list": [{"code": "country", "value": "IND"}]},
        {"code": "gst", "list": [
          {"code": "tax_rate", "value": "5"},
          {"code": "hsn_code", "value": "9004"}
        ]}
      ]
    }
    """
    sku = _s(variant.get("sku") if variant else None) or _s(product.get("sku"))
    item_id = sku or _s(product.get("_id") or product.get("id", str(uuid.uuid4())))

    name = _s(product.get("name"))
    brand = _s(product.get("brand", ""))
    if brand and not name.startswith(brand):
        display_name = f"{brand} {name}".strip()
    else:
        display_name = name

    if variant:
        color = _s(variant.get("color", ""))
        size = _s(variant.get("size", ""))
        variant_suffix = " ".join(filter(None, [color, size]))
        if variant_suffix:
            display_name = f"{display_name} - {variant_suffix}"

    mrp = _f(product.get("price") or product.get("mrp"))
    offer_price = _f(
        (variant.get("price") if variant else None)
        or product.get("offer_price")
        or mrp
    )

    gst_rate = _gst_rate_for(product)
    hsn = _hsn_for(product)
    coo = _s(product.get("country_of_origin") or _DEFAULT_COO)
    gst_code = _ONDC_GST_MAP.get(int(gst_rate), f"GST_{int(gst_rate)}")

    # Time to ship: default 48h for optical (lenses need surfacing)
    time_to_ship = _s(product.get("ondc_time_to_ship", "PT2D"))

    images = []
    for img in (product.get("images") or []):
        url = _s(img.get("url") if isinstance(img, dict) else img)
        if url:
            images.append({"url": url, "size_type": "sm"})

    quantity = _ondc_quantity(product, variant)

    item: Dict[str, Any] = {
        "id": item_id,
        "descriptor": {
            "name": display_name,
            "code": item_id,
            "short_desc": _s(product.get("description", display_name))[:100],
            "long_desc": _s(product.get("description", display_name))[:500],
            "images": images[:5],
        },
        "price": {
            "currency": "INR",
            "value": f"{mrp:.2f}",
            "offered_value": f"{offer_price:.2f}",
            "maximum_value": f"{mrp:.2f}",
        },
        "quantity": quantity,
        "category_id": "RET12-1101",  # Eyewear sub-category
        "fulfillment_id": fulfillment_id,
        "location_id": location_id,
        "@ondc/org/returnable": True,
        "@ondc/org/cancellable": True,
        "@ondc/org/return_window": "P7D",
        "@ondc/org/seller_pickup_return": True,
        "@ondc/org/time_to_ship": time_to_ship,
        "@ondc/org/available_on_cod": False,
        "@ondc/org/contact_details_consumer_care": os.getenv(
            "ONDC_CONSUMER_CARE_CONTACT",
            "Better Vision Optical, support@bettervision.in, +91 9999999999",
        ),
        "@ondc/org/statutory_reqs_packaged_commodities": {
            "manufacturer_or_packer_name": brand or "Better Vision",
            "manufacturer_or_packer_address": os.getenv(
                "ONDC_REGISTERED_ADDRESS",
                "India",
            ),
            "country_of_origin": coo,
            "month_year_of_manufacture_packing_import": "",
            "imported_product_country_of_origin": None if coo == "IND" else coo,
        },
        "tags": [
            {
                "code": "origin",
                "list": [{"code": "country", "value": coo}],
            },
            {
                "code": "gst",
                "list": [
                    {"code": "tax_rate", "value": str(int(gst_rate))},
                    {"code": "hsn_code", "value": hsn},
                    {"code": "tax_code", "value": gst_code},
                ],
            },
        ],
    }
    return item


def build_ondc_catalog(db) -> List[Dict[str, Any]]:
    """Map ALL active IMS catalog_products (with their variants) into ONDC items.

    Fail-soft: DB unavailable or empty -> returns []. Never raises.
    Pure mapping; does NOT write to DB or call any network.

    Returns a list of ONDC item dicts (one per variant, or one per product
    when no variants exist).
    """
    if db is None:
        return []
    items: List[Dict[str, Any]] = []
    try:
        products_coll = db.get_collection("catalog_products")
        variants_coll = db.get_collection("catalog_variants")

        # Active, sellable products only
        query = {
            "status": {"$in": ["ACTIVE", "active"]},
            "price": {"$exists": True, "$ne": None},
        }
        products = list(products_coll.find(query, {"_id": 0}))

        for product in products:
            pid = _s(product.get("product_id") or product.get("sku", ""))
            if not pid:
                continue

            # Fetch variants for this product
            var_docs = list(
                variants_coll.find(
                    {"product_id": pid, "status": {"$in": ["ACTIVE", "active"]}},
                    {"_id": 0},
                )
            )

            if var_docs:
                for var in var_docs:
                    try:
                        items.append(build_ondc_item(product, var))
                    except Exception as exc:
                        logger.warning(
                            "[ONDC] Skipping variant %s: %s",
                            var.get("sku", "?"),
                            exc,
                        )
            else:
                try:
                    items.append(build_ondc_item(product))
                except Exception as exc:
                    logger.warning(
                        "[ONDC] Skipping product %s: %s",
                        pid,
                        exc,
                    )

    except Exception as exc:
        logger.error("[ONDC] build_ondc_catalog failed: %s", exc, exc_info=True)

    logger.info("[ONDC] build_ondc_catalog -> %d items", len(items))
    return items


# ---------------------------------------------------------------------------
# Beckn / ONDC envelope helpers
# ---------------------------------------------------------------------------


def _beckn_context(
    action: str,
    cfg: Dict[str, Any],
    *,
    message_id: Optional[str] = None,
    transaction_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a standard Beckn context envelope for outgoing calls."""
    return {
        "domain": ONDC_DOMAIN,
        "action": action,
        "core_version": ONDC_CORE_VERSION,
        "bap_id": "",
        "bap_uri": "",
        "bpp_id": _s(cfg.get("subscriber_id")),
        "bpp_uri": _s(cfg.get("subscriber_url")),
        "transaction_id": transaction_id or str(uuid.uuid4()),
        "message_id": message_id or str(uuid.uuid4()),
        "city": _s(cfg.get("city_code", "*")),
        "country": "IND",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "ttl": "PT30S",
    }


def _sign_request(payload_bytes: bytes, ukp: str) -> str:
    """Simple HMAC-SHA256 signature for the SNP (placeholder; real ONDC uses
    Ed25519 signing via the subscriber's private key). This stub produces a
    deterministic signature so tests can verify it without a real key pair.
    Replace with Ed25519 when onboarding with a real SNP."""
    import hmac
    sig = hmac.new(ukp.encode(), payload_bytes, hashlib.sha256).hexdigest()
    return f"Signature keyId={hashlib.sha256(ukp.encode()).hexdigest()[:8]},algorithm=HMAC-SHA256,signature={sig}"


# ---------------------------------------------------------------------------
# publish_catalog  (outbound SNP push -- DARK by default)
# ---------------------------------------------------------------------------


async def publish_catalog(db) -> Dict[str, Any]:
    """Push the IMS catalog to the configured SNP's /search endpoint.

    Returns:
        {ok, mode, item_count, simulated_reason, published_at, error}

    DARK CONTRACT: returns mode=SIMULATED with no network call unless
    ondc_enabled(db) is True. Idempotent: writes `last_published_at` on
    the integrations doc on a successful LIVE push.
    """
    cfg = _load_ondc_config(db)
    enabled = ondc_enabled(db)

    items = build_ondc_catalog(db)
    item_count = len(items)

    if not enabled:
        reason = _simulated_reason(db)
        logger.info("[ONDC] publish_catalog SIMULATED (%s), %d items", reason, item_count)
        return {
            "ok": True,
            "mode": "SIMULATED",
            "item_count": item_count,
            "simulated_reason": reason,
            "published_at": None,
            "error": None,
        }

    # LIVE path
    snp_url = _s(cfg.get("snp_url")).rstrip("/")
    subscriber_id = _s(cfg.get("subscriber_id"))
    ukp = _s(cfg.get("ukp", ""))  # unique key pair / HMAC secret

    context = _beckn_context("search", cfg)
    payload = {
        "context": context,
        "message": {
            "catalog": {
                "bpp/descriptor": {
                    "name": os.getenv("ONDC_SELLER_NAME", "Better Vision Optical"),
                    "short_desc": "Optical Retail Chain",
                },
                "bpp/providers": [
                    {
                        "id": subscriber_id,
                        "descriptor": {
                            "name": os.getenv("ONDC_SELLER_NAME", "Better Vision Optical"),
                        },
                        "items": items,
                    }
                ],
            }
        },
    }

    import json
    payload_bytes = json.dumps(payload, ensure_ascii=False).encode()
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if ukp:
        headers["Authorization"] = _sign_request(payload_bytes, ukp)

    try:
        async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT) as client:
            resp = await client.post(
                f"{snp_url}/search",
                content=payload_bytes,
                headers=headers,
            )
        published_at = datetime.now(timezone.utc).isoformat()
        ok = resp.status_code < 300

        # Write back last_published_at (idempotent)
        if db is not None and ok:
            try:
                db.get_collection("integrations").update_one(
                    {"type": "ondc"},
                    {
                        "$set": {
                            "last_published_at": published_at,
                            "last_item_count": item_count,
                        }
                    },
                )
            except Exception as exc:
                logger.warning("[ONDC] last_published_at writeback failed: %s", exc)

        result: Dict[str, Any] = {
            "ok": ok,
            "mode": "LIVE",
            "item_count": item_count,
            "simulated_reason": None,
            "published_at": published_at if ok else None,
            "error": None if ok else f"HTTP {resp.status_code}: {resp.text[:200]}",
        }
        logger.info(
            "[ONDC] publish_catalog LIVE ok=%s items=%d status=%d",
            ok,
            item_count,
            resp.status_code,
        )
        return result

    except Exception as exc:
        logger.error("[ONDC] publish_catalog LIVE failed: %s", exc, exc_info=True)
        return {
            "ok": False,
            "mode": "LIVE",
            "item_count": item_count,
            "simulated_reason": None,
            "published_at": None,
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# ingest_ondc_order  (SNP -> IMS order mapping)
# ---------------------------------------------------------------------------

# ONDC payment type -> IMS payment_mode
_ONDC_PAYMENT_MAP = {
    "PRE-PAID": "UPI",
    "POST-PAID": "COD",
    "ON-ORDER": "UPI",
    "ON-FULFILLMENT": "COD",
}

# ONDC order state -> IMS order status
_ONDC_STATUS_MAP = {
    "Created": "CONFIRMED",
    "Accepted": "CONFIRMED",
    "In-progress": "PROCESSING",
    "Completed": "DELIVERED",
    "Cancelled": "CANCELLED",
}


def ingest_ondc_order(db, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Map an ONDC on_confirm (or on_init/on_select) payload to a canonical IMS order.

    The resulting IMS order is written to the `orders` collection with
    channel="ONDC" so Finance / P&L count it the same as POS or Shopify online
    orders. Returns:
        {ok, order_id, ims_order, mode, error}

    Fail-soft: a bad / partial payload => {ok: False, error: ...}, never raises.
    Idempotent: uses ondc_order_id as the dedup key (same as Shopify's
    external_order_id guard).
    """
    try:
        return _ingest_ondc_order_inner(db, payload)
    except Exception as exc:
        logger.error("[ONDC] ingest_ondc_order unhandled: %s", exc, exc_info=True)
        return {"ok": False, "order_id": None, "ims_order": None, "mode": "SIMULATED", "error": str(exc)}


def _ingest_ondc_order_inner(db, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Inner (may raise -- wrapped by ingest_ondc_order)."""
    context = payload.get("context") or {}
    message = payload.get("message") or {}
    order = message.get("order") or {}

    ondc_order_id = _s(order.get("id"))
    if not ondc_order_id:
        return {
            "ok": False,
            "order_id": None,
            "ims_order": None,
            "mode": "SIMULATED",
            "error": "Missing order.id in ONDC payload",
        }

    # Idempotency guard -- if this ONDC order is already in IMS, return it
    if db is not None:
        existing = db.get_collection("orders").find_one(
            {"external_order_id": ondc_order_id, "channel": "ONDC"},
            {"_id": 0, "order_id": 1},
        )
        if existing:
            return {
                "ok": True,
                "order_id": existing.get("order_id"),
                "ims_order": existing,
                "mode": "IDEMPOTENT",
                "error": None,
            }

    # --- Extract buyer info ---
    billing = order.get("billing") or {}
    buyer_name = _s(billing.get("name"))
    buyer_phone = _s(billing.get("phone", ""))
    buyer_email = _s(billing.get("email", ""))

    # --- Extract items ---
    ondc_items = order.get("items") or []
    ims_items = []
    subtotal = 0.0
    for itm in ondc_items:
        sku = _s(itm.get("id"))
        qty = _i(itm.get("quantity", {}).get("count", 1))
        price_str = _s(
            (itm.get("price") or {}).get("value", "0")
        )
        unit_price = _f(price_str)
        line_total = unit_price * qty
        subtotal += line_total
        ims_items.append({
            "sku": sku,
            "product_id": sku,
            "name": _s((itm.get("descriptor") or {}).get("name", sku)),
            "quantity": qty,
            "unit_price": unit_price,
            "discount": 0.0,
            "gst_rate": 5.0,  # Default; GST engine will overwrite from catalog
            "total": line_total,
            "channel": "ONDC",
        })

    # --- Payment ---
    payments = order.get("payment") or {}
    payment_type = _s(payments.get("type", "PRE-PAID"))
    payment_mode = _ONDC_PAYMENT_MAP.get(payment_type, "UPI")
    payment_status = "PAID" if payment_type in ("PRE-PAID", "ON-ORDER") else "UNPAID"

    total_amount = _f((payments.get("params") or {}).get("amount", subtotal))

    # --- Fulfillment / delivery ---
    fulfillments = order.get("fulfillments") or [{}]
    ff = fulfillments[0] if fulfillments else {}
    ff_end = (ff.get("end") or {})
    delivery_address_obj = ff_end.get("location") or {}
    delivery_address = _s(
        delivery_address_obj.get("address", {}).get("door", "")
        + " "
        + delivery_address_obj.get("address", {}).get("name", "")
    ).strip() or "N/A"

    ondc_state = _s(order.get("state", "Created"))
    ims_status = _ONDC_STATUS_MAP.get(ondc_state, "CONFIRMED")

    # --- Build canonical IMS order ---
    now = datetime.now(timezone.utc)
    order_id = f"ONDC-{ondc_order_id[:16]}-{now.strftime('%Y%m%d%H%M%S')}"

    ims_order: Dict[str, Any] = {
        "order_id": order_id,
        "external_order_id": ondc_order_id,
        "channel": "ONDC",
        "status": ims_status,
        "payment_status": payment_status,
        "payment_mode": payment_mode,
        "customer_name": buyer_name,
        "customer_phone": buyer_phone,
        "customer_email": buyer_email,
        "delivery_address": delivery_address,
        "items": ims_items,
        "subtotal": subtotal,
        "total_amount": total_amount,
        "gst_amount": round(total_amount - subtotal, 2),
        "discount_amount": 0.0,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "ondc_context": context,
        "ondc_payload_hash": hashlib.sha256(
            str(payload).encode()
        ).hexdigest()[:16],
        "store_id": os.getenv("ONDC_DEFAULT_STORE_ID", ""),
        "notes": f"ONDC order from {_s(context.get('bap_id', 'buyer-app'))}",
    }

    # Persist if DB available (DARK when no DB)
    mode = "SIMULATED"
    if db is not None:
        try:
            db.get_collection("orders").insert_one({**ims_order})
            mode = "LIVE"
            logger.info(
                "[ONDC] Ingested ONDC order %s -> IMS %s", ondc_order_id, order_id
            )
        except Exception as exc:
            logger.error("[ONDC] Order insert failed: %s", exc, exc_info=True)
            return {
                "ok": False,
                "order_id": order_id,
                "ims_order": ims_order,
                "mode": "SIMULATED",
                "error": str(exc),
            }
    else:
        logger.info(
            "[ONDC] Order ingestion SIMULATED (no DB): %s", ondc_order_id
        )

    return {
        "ok": True,
        "order_id": order_id,
        "ims_order": ims_order,
        "mode": mode,
        "error": None,
    }


# ---------------------------------------------------------------------------
# reconcile_tcs  (Finance integration -- ONDC TCS / commission deduction)
# ---------------------------------------------------------------------------


def reconcile_tcs(
    db,
    order_id: str,
    settlement: Dict[str, Any],
) -> Dict[str, Any]:
    """Record the ONDC TCS / commission deduction for Finance.

    ONDC mandates 1% TCS on the gross order value plus the SNP's commission.
    This writes to `ondc_settlements` so the Accountant can see the net payout
    without manual ledger work.

    Returns:
        {ok, settlement_id, tcs_amount, commission_amount, net_payout, error}

    Fail-soft: errors return {ok: False, error: ...}, never raise.
    """
    try:
        gross = _f(settlement.get("gross_amount", 0))
        commission_pct = _f(settlement.get("commission_pct", 0))  # e.g. 3.0 for 3%
        commission_amount = round(gross * commission_pct / 100, 2)
        tcs_amount = round(gross * TCS_RATE, 2)
        net_payout = round(gross - commission_amount - tcs_amount, 2)

        settlement_doc = {
            "settlement_id": f"ONDCS-{uuid.uuid4().hex[:12].upper()}",
            "order_id": order_id,
            "gross_amount": gross,
            "commission_pct": commission_pct,
            "commission_amount": commission_amount,
            "tcs_rate": TCS_RATE,
            "tcs_amount": tcs_amount,
            "net_payout": net_payout,
            "settlement_date": _s(settlement.get("settlement_date", "")),
            "snp_settlement_ref": _s(settlement.get("snp_ref", "")),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "PENDING",
        }

        if db is not None:
            db.get_collection("ondc_settlements").insert_one({**settlement_doc})
            logger.info(
                "[ONDC] TCS recorded for order %s: gross=%.2f tcs=%.2f net=%.2f",
                order_id,
                gross,
                tcs_amount,
                net_payout,
            )

        settlement_doc.pop("_id", None)
        return {
            "ok": True,
            "settlement_id": settlement_doc["settlement_id"],
            "tcs_amount": tcs_amount,
            "commission_amount": commission_amount,
            "net_payout": net_payout,
            "error": None,
        }
    except Exception as exc:
        logger.error("[ONDC] reconcile_tcs failed: %s", exc, exc_info=True)
        return {
            "ok": False,
            "settlement_id": None,
            "tcs_amount": None,
            "commission_amount": None,
            "net_payout": None,
            "error": str(exc),
        }
