"""
IMS 2.0 — NEXUS integration provider clients
==============================================
One module, four thin async clients:

- shopify_push_product / shopify_pull_orders — bidirectional catalog + order sync
- razorpay_list_payments — pull recent payments to reconcile against orders
- shiprocket_track_awb — fetch current status for a shipped order
- tally_build_day_voucher_xml — build the nightly sales-voucher XML

Shared patterns from claude_client / providers.py:
- Every call is async, uses httpx, and fails soft.
- DISPATCH_MODE (reused from providers.py) gates destructive writes
  (Shopify product updates, Razorpay refunds). Read-only syncs (pull
  orders, pull tracking) are allowed in any mode since they don't
  affect external systems.
- Credentials read from env / MongoDB integrations collection. If a
  credential is missing, the call returns a structured "not_configured"
  result and the caller records that in sync_runs.
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
import logging
import os
from xml.sax.saxutils import escape

import httpx

from .providers import dispatch_mode  # reuse DISPATCH_MODE gate
from api.utils.dates import to_date_str

logger = logging.getLogger(__name__)


PROVIDER_TIMEOUT = float(os.getenv("NEXUS_PROVIDER_TIMEOUT", "30.0"))


@dataclass
class SyncResult:
    ok: bool
    provider: str
    kind: str  # pull / push / export
    items_synced: int = 0
    error: Optional[str] = None
    notes: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None


# ============================================================================
# Config helper — reads the integrations MongoDB collection
# ============================================================================


def _load_integration_config(db, integration_type: str) -> Dict[str, Any]:
    """Look up {type, enabled, config:{...}} for one integration. Returns {} if missing."""
    if db is None:
        return {}
    try:
        coll = db.get_collection("integrations")
        doc = coll.find_one({"type": integration_type.lower(), "enabled": True})
        if not doc:
            return {}
        # BUG-155: secrets are Fernet-encrypted at rest; decrypt for provider use.
        from api.services import cred_crypto

        return cred_crypto.decrypt_config(doc.get("config") or {})
    except Exception as e:
        logger.debug(f"[NEXUS] Config read failed for {integration_type}: {e}")
        return {}


def _is_destructive_allowed() -> bool:
    """Shopify/Razorpay WRITES gated on DISPATCH_MODE=live (matches WhatsApp gate)."""
    return dispatch_mode() == "live"


def shopify_dispatch_mode() -> str:
    """Effective dispatch mode for SHOPIFY writes only.

    Owner 2026-07-05 (Phase-6 cutover): going Shopify-live must NOT require
    arming the global DISPATCH_MODE=live, which would also arm WhatsApp/SMS
    (MEGAPHONE) and every other NEXUS write the moment their creds appear.
    SHOPIFY_DISPATCH_MODE, when set, OVERRIDES the global mode for Shopify
    write paths (values: off/test/live). Unset -> global DISPATCH_MODE as
    before, so existing deployments behave identically."""
    import os

    override = (os.getenv("SHOPIFY_DISPATCH_MODE") or "").strip().lower()
    if override in ("off", "test", "live"):
        return override
    return dispatch_mode()


def _is_shopify_write_allowed() -> bool:
    """Shopify-specific live gate (see shopify_dispatch_mode)."""
    return shopify_dispatch_mode() == "live"


def ims_shopify_writes_enabled() -> bool:
    """The e-commerce app (BVI) is now the SINGLE owner of the Shopify catalog,
    so IMS Shopify WRITES are retired by default -- this prevents two systems
    pushing to the same Shopify store. Set IMS_SHOPIFY_WRITES=1 only if BVI is
    ever decommissioned and IMS must own Shopify again."""
    import os

    return os.getenv("IMS_SHOPIFY_WRITES", "").strip().lower() in (
        "1",
        "true",
        "on",
        "yes",
    )


# ============================================================================
# SHOPIFY — product push + order pull
# ============================================================================


async def shopify_pull_orders(db, since_hours: int = 2) -> SyncResult:
    """Pull Shopify orders created in the last N hours for fulfillment routing."""
    # Resolve creds via the shared resolver (OAuth client-credentials preferred;
    # the stored Mongo token is stale/401s). Lazy import avoids an import cycle
    # (shopify_auth imports this module for its vault fallback).
    from api.services.shopify_auth import resolve_shopify_credentials

    creds = resolve_shopify_credentials(db)
    shop_url = (creds or {}).get("shop_url")
    access_token = (creds or {}).get("access_token")
    if not shop_url or not access_token:
        return SyncResult(
            ok=False,
            provider="shopify",
            kind="pull",
            error="shop_url or access_token not configured",
        )

    since = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()
    url = f"https://{shop_url}/admin/api/{SHOPIFY_API_VERSION}/orders.json"
    params = {"status": "any", "updated_at_min": since, "limit": 100}
    headers = {"X-Shopify-Access-Token": access_token}

    try:
        async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT) as client:
            resp = await client.get(url, params=params, headers=headers)
        if resp.status_code != 200:
            return SyncResult(
                ok=False,
                provider="shopify",
                kind="pull",
                error=f"status {resp.status_code}: {resp.text[:200]}",
            )
        orders = resp.json().get("orders", [])
        return SyncResult(
            ok=True,
            provider="shopify",
            kind="pull",
            items_synced=len(orders),
            payload={"order_ids": [o.get("id") for o in orders[:10]]},  # sample
        )
    except httpx.TimeoutException:
        return SyncResult(ok=False, provider="shopify", kind="pull", error="timeout")
    except (httpx.HTTPError, ValueError) as e:
        return SyncResult(ok=False, provider="shopify", kind="pull", error=str(e))


async def shopify_push_product(db, product: Dict[str, Any]) -> SyncResult:
    """Push one product to Shopify (create or update). RETIRED: Shopify is owned
    by the e-commerce app (BVI). Gated on IMS_SHOPIFY_WRITES, then DISPATCH_MODE."""
    if not ims_shopify_writes_enabled():
        return SyncResult(
            ok=True,
            provider="shopify",
            kind="push",
            notes="RETIRED — Shopify catalog is owned by the e-commerce app (BVI); "
            "IMS Shopify writes are disabled (set IMS_SHOPIFY_WRITES=1 to re-enable)",
        )
    if not _is_shopify_write_allowed():
        return SyncResult(
            ok=True,
            provider="shopify",
            kind="push",
            notes=f"SIMULATED — shopify_dispatch_mode={shopify_dispatch_mode()}",
        )

    cfg = _load_integration_config(db, "shopify")
    shop_url = cfg.get("shop_url")
    access_token = cfg.get("access_token")
    if not shop_url or not access_token:
        return SyncResult(
            ok=False,
            provider="shopify",
            kind="push",
            error="shop_url or access_token not configured",
        )

    shopify_id = product.get("shopify_product_id")
    path = f"products/{shopify_id}.json" if shopify_id else "products.json"
    method = "PUT" if shopify_id else "POST"
    url = f"https://{shop_url}/admin/api/2024-01/{path}"
    headers = {
        "X-Shopify-Access-Token": access_token,
        "content-type": "application/json",
    }
    body = {"product": product}

    try:
        async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT) as client:
            resp = await client.request(method, url, headers=headers, json=body)
        if resp.status_code not in (200, 201):
            return SyncResult(
                ok=False,
                provider="shopify",
                kind="push",
                error=f"status {resp.status_code}: {resp.text[:200]}",
            )
        returned = resp.json().get("product") or {}
        return SyncResult(
            ok=True,
            provider="shopify",
            kind="push",
            items_synced=1,
            payload={"shopify_product_id": returned.get("id")},
        )
    except httpx.TimeoutException:
        return SyncResult(ok=False, provider="shopify", kind="push", error="timeout")
    except (httpx.HTTPError, ValueError) as e:
        return SyncResult(ok=False, provider="shopify", kind="push", error=str(e))


# Shopify GraphQL Admin API version. inventorySetQuantities (absolute set) has
# been GA since 2023-10; pin a known-good version so a Shopify default bump
# can't silently change the contract. Override via SHOPIFY_API_VERSION if needed.
SHOPIFY_API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2024-10")

# Shopify caps a single inventorySetQuantities call at 250 quantity entries.
_SHOPIFY_SET_MAX = 250


async def shopify_set_inventory_available(
    db,
    inventory_item_id: str,
    location_id: str,
    available: int,
) -> SyncResult:
    """Set the ABSOLUTE available quantity for ONE Shopify variant at ONE
    location via the GraphQL Admin API `inventorySetQuantities` mutation.

    IMS is the inventory MASTER: on an in-store sale we push the reduced
    available count so the website cannot oversell. We push the ABSOLUTE value
    (not a delta) so a retry is idempotent -- replaying the same push lands the
    same number.

    Gating (identical convention to shopify_push_product):
      1. IMS_SHOPIFY_WRITES must be enabled (BVI owns the catalog by default).
      2. DISPATCH_MODE must be `live` for a real write; otherwise SIMULATED.
      3. Missing shop creds / ids -> structured no-op (never raises).

    `inventory_item_id` and `location_id` are Shopify GIDs
    (e.g. "gid://shopify/InventoryItem/123"). A bare numeric id is accepted and
    promoted to a GID. Returns a SyncResult; NEVER raises -- a Shopify failure
    must not propagate into the sale path.
    """
    if not ims_shopify_writes_enabled():
        return SyncResult(
            ok=True,
            provider="shopify",
            kind="push",
            notes="RETIRED -- Shopify catalog is owned by the e-commerce app (BVI); "
            "IMS Shopify writes are disabled (set IMS_SHOPIFY_WRITES=1 to re-enable)",
        )

    inv_gid = _as_shopify_gid(inventory_item_id, "InventoryItem")
    loc_gid = _as_shopify_gid(location_id, "Location")
    if not inv_gid or not loc_gid:
        return SyncResult(
            ok=False,
            provider="shopify",
            kind="push",
            error="inventory_item_id or location_id missing",
        )

    try:
        qty = max(0, int(available))
    except (TypeError, ValueError):
        return SyncResult(
            ok=False,
            provider="shopify",
            kind="push",
            error=f"non-integer available={available!r}",
        )

    if not _is_shopify_write_allowed():
        # off/test/unknown -> log only, no live write. Identical to today's
        # default behaviour (no outbound Shopify call).
        return SyncResult(
            ok=True,
            provider="shopify",
            kind="push",
            items_synced=0,
            notes=f"SIMULATED -- shopify_dispatch_mode={shopify_dispatch_mode()}; would set "
            f"{inv_gid} @ {loc_gid} -> available={qty}",
            payload={
                "inventory_item_id": inv_gid,
                "location_id": loc_gid,
                "available": qty,
            },
        )

    cfg = _load_integration_config(db, "shopify")
    shop_url = cfg.get("shop_url")
    access_token = cfg.get("access_token")
    if not shop_url or not access_token:
        return SyncResult(
            ok=False,
            provider="shopify",
            kind="push",
            error="shop_url or access_token not configured",
        )

    # inventorySetQuantities atomically sets the on-hand/available count to an
    # absolute value. `ignoreCompareQuantity` lets us set without supplying the
    # current value (we are the source of truth and just overwrite).
    mutation = """
    mutation imsSetInventory($input: InventorySetQuantitiesInput!) {
      inventorySetQuantities(input: $input) {
        inventoryAdjustmentGroup { createdAt reason }
        userErrors { field message }
      }
    }
    """
    variables = {
        "input": {
            "name": "available",
            "reason": "correction",
            "ignoreCompareQuantity": True,
            "quantities": [
                {
                    "inventoryItemId": inv_gid,
                    "locationId": loc_gid,
                    "quantity": qty,
                }
            ],
        }
    }
    url = f"https://{shop_url}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        "X-Shopify-Access-Token": access_token,
        "content-type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT) as client:
            resp = await client.post(
                url, headers=headers, json={"query": mutation, "variables": variables}
            )
        if resp.status_code not in (200, 201):
            return SyncResult(
                ok=False,
                provider="shopify",
                kind="push",
                error=f"status {resp.status_code}: {resp.text[:200]}",
            )
        body = resp.json() or {}
        # GraphQL transport-200 can still carry top-level `errors` or per-field
        # userErrors -- treat both as failures so the caller can record them.
        if body.get("errors"):
            return SyncResult(
                ok=False,
                provider="shopify",
                kind="push",
                error=f"graphql errors: {str(body['errors'])[:200]}",
            )
        result = (body.get("data") or {}).get("inventorySetQuantities") or {}
        user_errors = result.get("userErrors") or []
        if user_errors:
            return SyncResult(
                ok=False,
                provider="shopify",
                kind="push",
                error=f"userErrors: {str(user_errors)[:200]}",
            )
        return SyncResult(
            ok=True,
            provider="shopify",
            kind="push",
            items_synced=1,
            payload={
                "inventory_item_id": inv_gid,
                "location_id": loc_gid,
                "available": qty,
            },
        )
    except httpx.TimeoutException:
        return SyncResult(ok=False, provider="shopify", kind="push", error="timeout")
    except (httpx.HTTPError, ValueError) as e:
        return SyncResult(ok=False, provider="shopify", kind="push", error=str(e))


def _as_shopify_gid(value: Any, kind: str) -> str:
    """Normalize a Shopify id to a GID. Accepts an existing GID
    ("gid://shopify/InventoryItem/123") or a bare numeric id ("123") and
    promotes the latter. Returns "" for empty/None."""
    s = str(value).strip() if value not in (None, "") else ""
    if not s:
        return ""
    if s.startswith("gid://"):
        return s
    if s.isdigit():
        return f"gid://shopify/{kind}/{s}"
    return s


# ============================================================================
# RAZORPAY — payment reconciliation (read-only)
# ============================================================================


async def razorpay_list_payments(db, since_hours: int = 2) -> SyncResult:
    """Pull recent Razorpay payments. Used to reconcile IMS orders vs Razorpay settlements."""
    cfg = _load_integration_config(db, "razorpay")
    key_id = cfg.get("key_id")
    key_secret = cfg.get("key_secret")
    if not key_id or not key_secret:
        return SyncResult(
            ok=False,
            provider="razorpay",
            kind="pull",
            error="key_id or key_secret not configured",
        )

    since = int((datetime.now(timezone.utc) - timedelta(hours=since_hours)).timestamp())
    url = "https://api.razorpay.com/v1/payments"
    params = {"from": since, "count": 100}

    try:
        async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT) as client:
            resp = await client.get(url, params=params, auth=(key_id, key_secret))
        if resp.status_code != 200:
            return SyncResult(
                ok=False,
                provider="razorpay",
                kind="pull",
                error=f"status {resp.status_code}: {resp.text[:200]}",
            )
        items = resp.json().get("items", [])
        return SyncResult(
            ok=True,
            provider="razorpay",
            kind="pull",
            items_synced=len(items),
            payload={
                "total": len(items),
                "captured": sum(1 for i in items if i.get("status") == "captured"),
            },
        )
    except httpx.TimeoutException:
        return SyncResult(ok=False, provider="razorpay", kind="pull", error="timeout")
    except (httpx.HTTPError, ValueError) as e:
        return SyncResult(ok=False, provider="razorpay", kind="pull", error=str(e))


# ============================================================================
# SHIPROCKET — tracking status pull (read-only)
# ============================================================================


async def shiprocket_track_awb(db, awb: str) -> SyncResult:
    """Pull current tracking status for one AWB. Caller iterates over outbound shipments."""
    cfg = _load_integration_config(db, "shiprocket")
    email = cfg.get("email")
    password = cfg.get("password")
    if not email or not password:
        return SyncResult(
            ok=False,
            provider="shiprocket",
            kind="pull",
            error="email or password not configured",
        )

    # Shiprocket API requires a token via /auth/login first
    try:
        async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT) as client:
            auth_resp = await client.post(
                "https://apiv2.shiprocket.in/v1/external/auth/login",
                json={"email": email, "password": password},
            )
            if auth_resp.status_code != 200:
                return SyncResult(
                    ok=False,
                    provider="shiprocket",
                    kind="pull",
                    error=f"auth status {auth_resp.status_code}",
                )
            token = (auth_resp.json() or {}).get("token")
            if not token:
                return SyncResult(
                    ok=False,
                    provider="shiprocket",
                    kind="pull",
                    error="no token in auth response",
                )

            # Track the AWB
            track_resp = await client.get(
                f"https://apiv2.shiprocket.in/v1/external/courier/track/awb/{awb}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if track_resp.status_code != 200:
                return SyncResult(
                    ok=False,
                    provider="shiprocket",
                    kind="pull",
                    error=f"track status {track_resp.status_code}",
                )
            data = track_resp.json() or {}
            tracking = (data.get("tracking_data") or {}).get("shipment_track") or []
            latest_status = tracking[0].get("current_status") if tracking else None
            return SyncResult(
                ok=True,
                provider="shiprocket",
                kind="pull",
                items_synced=1,
                payload={"awb": awb, "latest_status": latest_status},
            )
    except httpx.TimeoutException:
        return SyncResult(ok=False, provider="shiprocket", kind="pull", error="timeout")
    except (httpx.HTTPError, ValueError) as e:
        return SyncResult(ok=False, provider="shiprocket", kind="pull", error=str(e))


# ============================================================================
# TALLY — nightly sales-voucher XML build
# ============================================================================


def tally_build_day_voucher_xml(
    orders: List[Dict[str, Any]],
    store_meta: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Build a single Tally import XML for the day's sales vouchers.
    Pure function — no I/O. The caller decides what to do with the XML
    (write to tally_exports collection for CA to download, or push to
    Tally HTTP-Server if one's wired).

    `store_meta`, when provided, is baked into the per-voucher
    `<NARRATION>` and `<COSTCENTRECATEGORY>` so the CA's RDP-Tally
    companies (one per branch) can identify the source store at import
    time. Expected keys: store_id, store_code, store_name. None of
    them are required individually — narration falls back to whatever
    is present.

    Tally XML format: https://help.tallysolutions.com/docs/te9rel66/Tally.ERP9/...
    Simplified schema — one VOUCHER per order with sales ledger + party
    ledger + tax ledgers (CGST/SGST). Real tally templates add
    cost-center allocations, but those are per-tenant and can be
    parameterized later.
    """
    meta = store_meta or {}
    store_code = str(meta.get("store_code") or meta.get("store_id") or "").strip()
    store_name = str(meta.get("store_name") or "").strip()
    narration_bits = [b for b in (store_code, store_name) if b]
    narration = " · ".join(narration_bits)
    # Escape store metadata for XML safety
    escaped_store_code = escape(store_code) if store_code else ""
    escaped_narration = escape(narration) if narration else ""

    vouchers = []
    for o in orders:
        order_id = escape(str(o.get("order_id", "")))
        order_date = to_date_str(o.get("created_at")).replace("-", "")  # yyyymmdd
        party = escape(o.get("customer_name") or "Walk-in Customer")
        subtotal = float(o.get("subtotal", 0) or 0)
        cgst = float(o.get("cgst_amount", 0) or 0)
        sgst = float(o.get("sgst_amount", 0) or 0)
        igst = float(o.get("igst_amount", 0) or 0)
        total = float(o.get("grand_total", 0) or 0)

        narration_block = (
            f"\n    <NARRATION>{escaped_narration}</NARRATION>" if escaped_narration else ""
        )
        cost_centre_block = (
            f"\n    <COSTCENTRECATEGORY>{escaped_store_code}</COSTCENTRECATEGORY>"
            if escaped_store_code
            else ""
        )

        # Build tax ledger entries. Inter-state sales carry igst_amount > 0 and
        # zero cgst/sgst; intra-state the opposite. Emit the right ledger(s) so
        # the voucher doesn't imbalance in Tally on import.
        if igst > 0:
            tax_entries = f"""
    <ALLLEDGERENTRIES.LIST>
      <LEDGERNAME>IGST Output</LEDGERNAME>
      <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
      <AMOUNT>{igst:.2f}</AMOUNT>
    </ALLLEDGERENTRIES.LIST>"""
        else:
            tax_entries = f"""
    <ALLLEDGERENTRIES.LIST>
      <LEDGERNAME>CGST Output</LEDGERNAME>
      <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
      <AMOUNT>{cgst:.2f}</AMOUNT>
    </ALLLEDGERENTRIES.LIST>
    <ALLLEDGERENTRIES.LIST>
      <LEDGERNAME>SGST Output</LEDGERNAME>
      <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
      <AMOUNT>{sgst:.2f}</AMOUNT>
    </ALLLEDGERENTRIES.LIST>"""

        voucher = f"""
  <VOUCHER VCHTYPE="Sales" ACTION="Create">
    <DATE>{order_date}</DATE>
    <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
    <VOUCHERNUMBER>{order_id}</VOUCHERNUMBER>
    <PARTYLEDGERNAME>{party}</PARTYLEDGERNAME>{narration_block}{cost_centre_block}
    <ALLLEDGERENTRIES.LIST>
      <LEDGERNAME>{party}</LEDGERNAME>
      <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
      <AMOUNT>-{total:.2f}</AMOUNT>
    </ALLLEDGERENTRIES.LIST>
    <ALLLEDGERENTRIES.LIST>
      <LEDGERNAME>Sales A/c</LEDGERNAME>
      <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
      <AMOUNT>{subtotal:.2f}</AMOUNT>
    </ALLLEDGERENTRIES.LIST>{tax_entries}
  </VOUCHER>"""
        vouchers.append(voucher)

    body = "".join(vouchers)
    wrapper = f"""<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>Vouchers</REPORTNAME>
      </REQUESTDESC>
      <REQUESTDATA>{body}
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""
    return wrapper


def validate_voucher_balance(orders: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Pre-export sanity check on a day's orders.

    Per-order assertion: `abs(taxable + tax - grand_total) < 0.50`
    (50-paise tolerance for half-up rounding across line items).

    Per-batch assertion: `sum(grand_total) - sum(total_discount) ≈
    sum(taxable) + sum(tax)` within ₹1 (cumulative rounding can be
    larger than the per-row tolerance).

    Returns a structured report so the orchestrator can decide whether
    to flag the row as `balanced=False` and suffix the XML filename
    with `_UNBALANCED`. Does NOT mutate the orders. Pure function.
    """
    mismatches: List[Dict[str, Any]] = []
    sum_grand = 0.0
    sum_subtotal = 0.0
    sum_taxable = 0.0
    sum_tax = 0.0
    sum_discount = 0.0

    for o in orders:
        grand = float(o.get("grand_total", 0) or 0)
        taxable = float(o.get("taxable", 0) or 0)
        tax = float(o.get("tax", o.get("tax_amount", 0)) or 0)
        subtotal = float(o.get("subtotal", 0) or 0)
        discount = float(o.get("total_discount", 0) or 0)

        sum_grand += grand
        sum_subtotal += subtotal
        sum_taxable += taxable
        sum_tax += tax
        sum_discount += discount

        # taxable + tax should land within 50 paise of grand_total.
        # Skip the check when taxable is zero (e.g. legacy orders that
        # predate the per-category GST split): it would always fail.
        if taxable <= 0:
            continue
        expected = round(taxable + tax, 2)
        delta = round(grand - expected, 2)
        if abs(delta) >= 0.5:
            mismatches.append(
                {
                    "order_id": o.get("order_id", ""),
                    "grand_total": round(grand, 2),
                    "taxable_plus_tax": expected,
                    "delta": delta,
                }
            )

    batch_check_lhs = round(sum_grand - sum_discount, 2)
    batch_check_rhs = round(sum_taxable + sum_tax, 2)
    batch_delta = round(batch_check_lhs - batch_check_rhs, 2)
    batch_ok = abs(batch_delta) < 1.00

    return {
        "ok": len(mismatches) == 0 and batch_ok,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[
            :50
        ],  # cap report size; full list still in mismatch_count
        "batch_delta": batch_delta,
        "batch_ok": batch_ok,
        "totals": {
            "grand_total": round(sum_grand, 2),
            "subtotal": round(sum_subtotal, 2),
            "taxable": round(sum_taxable, 2),
            "tax": round(sum_tax, 2),
            "total_discount": round(sum_discount, 2),
            "order_count": len(orders),
        },
    }
