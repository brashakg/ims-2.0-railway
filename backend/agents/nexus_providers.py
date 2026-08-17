"""
IMS 2.0 — NEXUS integration provider clients
==============================================
One module, four thin async clients:

- shopify_push_product / shopify_pull_orders — bidirectional catalog + order sync
- razorpay_list_payments — pull recent payments to reconcile against orders
- shiprocket_track_awb — fetch current status for a shipped order
- tally_build_day_voucher_xml — format the sales-voucher XML (dumb formatter:
  it reads already-reshaped subtotal/cgst/sgst/igst fields off each order)
- tally_build_day_voucher_xml_checked — reshape (via the CANONICAL finance
  sales-JV tax rules) + balance/tax gates + build. The ONLY entry point an
  UNATTENDED caller (the NEXUS nightly tick) may use

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
from api.utils.ist import ist_date_str

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


def _load_integration_config(
    db, integration_type: str, storefront_id: Optional[str] = None
) -> Dict[str, Any]:
    """Look up {type, enabled, config:{...}} for one integration. Returns {} if missing.

    `storefront_id` (optional) keys the lookup to ONE storefront (WizOpt
    multi-storefront Phase 0). It is BACKWARD-COMPATIBLE: the live Shopify
    integrations doc carries NO storefront_id field, so the query matches it via
    an $or of [{storefront_id: <sid>}, {storefront_id: {$exists: False}}]. For
    the default "BV" this resolves the SAME doc as the un-keyed query did, so BV
    behaves byte-identically. Callers that pass no storefront_id (razorpay,
    shiprocket, ...) keep the exact previous query."""
    if db is None:
        return {}
    try:
        coll = db.get_collection("integrations")
        query: Dict[str, Any] = {"type": integration_type.lower(), "enabled": True}
        if storefront_id:
            query["$or"] = [
                {"storefront_id": storefront_id},
                {"storefront_id": {"$exists": False}},
            ]
        doc = coll.find_one(query)
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

    # Keyed to the default BV storefront (Phase 0). Backward-compatible: the
    # untagged live Shopify doc still matches, so BV behaves byte-identically.
    cfg = _load_integration_config(db, "shopify", storefront_id="BV")
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

    # Keyed to the default BV storefront (Phase 0). Backward-compatible: the
    # untagged live Shopify doc still matches, so BV behaves byte-identically.
    cfg = _load_integration_config(db, "shopify", storefront_id="BV")
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
        # VOUCHERNUMBER via the same identity chain the quarantine reports use:
        # an imported order carries `order_number`, never `order_id`, and an
        # empty <VOUCHERNUMBER> is unusable to the accountant.
        order_id = escape(_order_identity(o))
        # BUG-104: this is the <DATE> on a DATED ACCOUNTING DOCUMENT carrying
        # CGST/SGST/IGST. created_at is a naive UTC wall clock, so a sale at
        # 02:00 IST books a day early -- and on 1 April that lands the voucher
        # in the PRIOR FINANCIAL YEAR. Convert to the IST day FIRST, then strip
        # the dashes; stripping first would make the shift impossible.
        order_date = ist_date_str(o.get("created_at")).replace("-", "")  # yyyymmdd
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

        # Party leg: COMPUTE the sign, never prefix a literal '-'. A literal
        # prefix emitted "-0.00" for a fully-discounted zero-total order and
        # "--1180.00" for a negative total -- neither is a number Tally can
        # parse, and the balance gate (which negates numerically) would have
        # blessed both. `+ 0.0` normalises the negative-zero float.
        party_amount = (-total) + 0.0

        voucher = f"""
  <VOUCHER VCHTYPE="Sales" ACTION="Create">
    <DATE>{order_date}</DATE>
    <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
    <VOUCHERNUMBER>{order_id}</VOUCHERNUMBER>
    <PARTYLEDGERNAME>{party}</PARTYLEDGERNAME>{narration_block}{cost_centre_block}
    <ALLLEDGERENTRIES.LIST>
      <LEDGERNAME>{party}</LEDGERNAME>
      <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
      <AMOUNT>{party_amount:.2f}</AMOUNT>
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


class TallyExportError(RuntimeError):
    """A Tally voucher could not be built CORRECTLY -- nothing was emitted.

    Money/GST rule: it is always safer to emit NOTHING and shout than to hand
    the accountant a voucher that books the gross order value as Sales with
    ZERO output GST (sales overstated, GST liability understated, books that
    never tie to the filed GSTR-1). Callers MUST let this surface as a visible
    failure (a failed sync_run row + a `sync.failed` event) instead of writing
    a `tally_exports` row the CA could download and import.
    """


# Paise-rounding slack, same convention as validate_voucher_balance's per-order
# tolerance. Used by the tax-coverage gate below.
_TALLY_TAX_TOLERANCE = 0.50

# ---------------------------------------------------------------------------
# Field chains -- ONE definition, used by BOTH the reshape (which prices the
# voucher) and the gate (which audits it). They were different sets before, and
# a document the gate could READ but the reshape could not PRICE emitted the
# original zero-GST defect with a passed-the-gate stamp.
#
# SUPERSET of finance.get_tally_sales_jv, NOT a fork of its rules: the canonical
# keys come FIRST in the canonical order (`tax_amount` -> `tax_total`,
# `grand_total` -> `total`), and the extra keys are consulted only when the
# canonical chain yields nothing -- precisely the case where the canonical path
# silently books zero.
#   total_tax    -- reports.py:218 _order_tax reads it, so the shape exists
#   total_amount -- ONDC (ondc_seller.py) writes it instead of grand_total
#
# NOT byte-identical to the human export: on a document the canonical chain
# cannot price, the nightly is MORE correct (it prices it, or refuses loudly)
# while /finance/tally/sales-jv still books zero GST. The two Tally files for the
# same date can therefore differ on such documents. Lifting these chains into
# finance.get_tally_sales_jv is the durable fix; that file is owned elsewhere.
#
# `gst_amount` is DELIBERATELY ABSENT from the tax chain. ONDC's `gst_amount`
# (ondc_seller.py) is a CHARGES RESIDUAL -- total charged minus line subtotal --
# not a computed tax: ONDC lines never run through the IMS GST engine at ingest.
# Reading it as tax booked delivery revenue as an output-GST liability that was
# never collected. An ONDC order is quarantined loudly instead.
_TAX_FIELD_CHAIN = ("tax_amount", "tax_total", "total_tax", "tax")
_GROSS_FIELD_CHAIN = ("grand_total", "total", "total_amount")


def _first_present_amount(order: Dict[str, Any], keys) -> tuple:
    """(value, key) for the first key in `keys` carrying a NON-ZERO amount, else
    (0.0, matched_key_or_None) where matched_key is the first key that was
    PRESENT (even as an explicit zero).

    The distinction matters: an explicit `tax_amount: 0.0` is an affirmative
    "this sale carried no GST" -- optical really does sell 0%-rated lines (eye
    tests, hearing aids) and those must keep shipping -- whereas no tax key at
    all means the document is unclassifiable and must not be booked as 100%
    Sales. A key present with the value None counts as ABSENT: the writers now
    persist None rather than a manufactured 0.0 when the source said nothing.

    Raises TallyExportError (never ValueError) on a non-numeric amount so one
    junk document can only ever fail its own store, not the whole chain.
    """
    present_key = None
    for key in keys:
        if key not in order:
            continue
        raw = order.get(key)
        if raw is None:
            continue
        if present_key is None:
            present_key = key
        try:
            value = float(raw or 0)
        except (TypeError, ValueError) as e:
            raise TallyExportError(
                f"order {_order_identity(order)!r}: field {key!r} is not a "
                f"number ({raw!r}) -- refusing to price the voucher"
            ) from e
        if value:
            return round(value, 2), key
    return 0.0, present_key


# Identifier chain for quarantine reporting + the Tally VOUCHERNUMBER.
# techcherry_import._map_order persists `order_number` and NEVER `order_id`, so
# reading only `order_id` reported quarantined live imports as '?' and the owner
# could not tell which invoices were missing from the books.
_ORDER_ID_FIELDS = ("order_id", "order_number", "invoice_number", "external_order_id")


def order_identity(order: Dict[str, Any]) -> str:
    """The best human-usable identifier the document actually carries."""
    for key in _ORDER_ID_FIELDS:
        value = order.get(key)
        if value not in (None, ""):
            return str(value)
    return "?"


# Internal alias kept so the module reads consistently at its own call sites.
_order_identity = order_identity


def _amounts_or_zero(order: Dict[str, Any], keys) -> tuple:
    """Non-raising sibling of `_first_present_amount`, for the REPORT path.

    `validate_voucher_balance` produces a diagnostic, never an emitted voucher,
    so a junk value must degrade to 0.0 rather than crash the nightly tick. The
    emit path keeps the raising variant."""
    try:
        return _first_present_amount(order, keys)
    except TallyExportError:
        return 0.0, None


def _order_line_tax(order: Dict[str, Any]) -> Optional[float]:
    """Sum of the per-line `tax_amount` stamped by orders._compute_per_category_gst.

    Returns None when no line carries a tax figure at all (nothing to
    cross-check against). Used ONLY as an independent second opinion on how
    much output GST the voucher must carry -- never to compute the split.
    """
    items = order.get("items")
    if not isinstance(items, list) or not items:
        return None
    total = 0.0
    seen = False
    for it in items:
        if not isinstance(it, dict):
            continue
        raw = it.get("tax_amount")
        if raw is None:
            continue
        try:
            total += float(raw or 0)
            seen = True
        except (TypeError, ValueError):
            continue
    return round(total, 2) if seen else None


def _has_line_items(order: Dict[str, Any]) -> bool:
    """True when the document carries line detail at all. An imported invoice
    normally does NOT (techcherry_import._map_order writes
    `"items": row.get("items") or []`), and that absence is what decides which
    corroboration path a zero-tax sale is judged on."""
    items = order.get("items")
    return isinstance(items, list) and bool(items)


def _lines_prove_zero_rated(order: Dict[str, Any], grand: float) -> bool:
    """True only when the lines POSITIVELY prove a 0% rate for the WHOLE sale.

    Every line must carry `gst_rate == 0` and `tax_amount == 0` -- i.e. the
    pricing engine itself concluded there was no tax component -- AND the lines
    must ACCOUNT FOR THE INVOICE: `sum(taxable_value) + sum(tax_amount)` has to
    land on `grand_total`. Without that coverage test a single Re 1 line proved
    a Rs 1,180 bill exempt (0.08% of the invoice blessing the whole thing),
    which is worthless as the one control separating a genuine exempt sale from
    an unresolved tax.

    Coverage is measured on `taxable_value + tax_amount`, NOT on `item_total`:
    `item_total` is the PRE-cart-discount line value, so an `item_total` test
    would refuse every discounted 0%-rated bill. taxable + tax == grand_total
    is the identity `orders._compute_per_category_gst` guarantees in both the
    inclusive and exclusive pricing modes, discount or no discount.

    Absence of evidence is NOT proof: no items, an unstamped rate, or a missing
    taxable_value all return False.
    """
    items = order.get("items")
    if not isinstance(items, list) or not items:
        return False
    covered = 0.0
    for it in items:
        if not isinstance(it, dict):
            return False
        rate = it.get("gst_rate")
        taxable = it.get("taxable_value")
        if rate is None or taxable is None:
            return False
        try:
            if float(rate) != 0.0:
                return False
            line_tax = float(it.get("tax_amount") or 0)
            if abs(line_tax) > 0.01:
                return False
            covered += float(taxable) + line_tax
        except (TypeError, ValueError):
            return False
    return abs(round(covered, 2) - round(grand, 2)) <= _TALLY_TAX_TOLERANCE


def _order_declared_tax(order: Dict[str, Any]) -> float:
    """The LARGEST total-GST figure the order document itself claims.

    Reads the SAME `_TAX_FIELD_CHAIN` the reshape prices from, plus the
    per-line sum as an independent second opinion. If any of those says the
    sale carried output GST but the voucher we are about to emit books less,
    that is the zero-GST defect -- take the max so the gate can only ever
    over-demand, never under-demand. `_order_tax_statements_disagree` covers
    the OTHER direction, which max() alone cannot see.
    """
    candidates: List[float] = []
    for key in _TAX_FIELD_CHAIN:
        raw = order.get(key)
        if raw is None:
            continue
        try:
            candidates.append(float(raw or 0))
        except (TypeError, ValueError):
            continue
    line_tax = _order_line_tax(order)
    if line_tax is not None:
        candidates.append(line_tax)
    return round(max(candidates), 2) if candidates else 0.0


def _order_tax_statements_disagree(order: Dict[str, Any]) -> Optional[str]:
    """A description of the disagreement between the order's TWO tax statements,
    or None when they agree (or only one exists).

    `max()` in `_order_declared_tax` is ONE-SIDED: it quarantines lines-over-
    header (the under-booking direction) but blesses header-over-lines. A
    Rs 1,180 order whose header says Rs 300 while its lines say Rs 180 emits
    Sales 880.00 / CGST 150.00 / SGST 150.00 -- Rs 120 of PHANTOM output-GST
    liability with Sales understated by the same Rs 120, and it passes every
    other gate because the legs still net to zero. A document whose own two tax
    statements differ by more than 50 paise is unpriceable in EITHER direction.
    """
    header_tax, header_key = _amounts_or_zero(order, _TAX_FIELD_CHAIN)
    if header_key is None:
        return None
    line_tax = _order_line_tax(order)
    if line_tax is None:
        return None
    if abs(header_tax - line_tax) <= _TALLY_TAX_TOLERANCE:
        return None
    return (
        f"the order's header declares Rs {header_tax:.2f} GST ({header_key}) "
        f"while its lines sum to Rs {line_tax:.2f} -- the document disagrees "
        "with itself and is unpriceable in either direction"
    )


def tally_resolve_state_maps(db) -> tuple:
    """(store_id -> state, customer_id -> state) via the CANONICAL finance maps.

    Exposed so `_build_tally_export` can resolve them ONCE per nightly tick
    instead of once per store -- the maps are full scans of `stores` and
    `customers`, and rebuilding them inside the 6-store loop made the
    money-critical tick the agent's longest operation.
    """
    try:
        from api.routers.finance import _customer_state_map, _store_state_map
    except Exception as e:  # noqa: BLE001
        raise TallyExportError(
            "Canonical Tally sales-JV helpers (api.routers.finance) are "
            f"unavailable: {e}. Refusing to build a voucher -- an un-reshaped "
            "export books the gross as Sales with ZERO output GST."
        ) from e
    return _store_state_map(db), _customer_state_map(db)


def tally_reshape_orders_for_voucher(
    db,
    orders: List[Dict[str, Any]],
    store_states: Optional[Dict[str, Any]] = None,
    customer_states: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Apply the CANONICAL sales-JV tax reshape to RAW order documents.

    `tally_build_day_voucher_xml` is a dumb formatter: it reads `subtotal`,
    `cgst_amount`, `sgst_amount`, `igst_amount` straight off each order dict.
    A raw IMS order carries NONE of those -- its `subtotal` is the pre-cart-
    discount, tax-INCLUSIVE gross and it has no per-head GST fields at all.
    Feeding raw orders to the formatter therefore books the gross as Sales
    with ZERO output GST. Every human-driven Tally path reshapes first
    (finance.get_tally_sales_jv, finance._b2b_fetch_orders); this function is
    that same reshape for the autonomous NEXUS nightly export.

    The tax RULES are NOT re-implemented here -- they are imported from the
    canonical finance module so a second copy can never drift:
      * `_order_is_interstate` -- OS-008 order-carried `interstate` flag first,
        store-state vs customer-state (GST-code normalised) as the fallback.
      * `_jv_cgst_sgst_split`  -- CGST/SGST halves whose residual lands on SGST
        so cgst + sgst == tax to the paisa (an off-by-a-paisa split imbalances
        the voucher and Tally rejects the import).

    Returns SHALLOW COPIES -- the caller's order documents are never mutated
    (the balance validator must still see the order's own untouched fields).

    Amounts are normalised to 2dp BEFORE the split. The legs must match the
    `:.2f` the XML actually prints, and an unrounded legacy total (the
    TechCherry importer writes raw floats: techcherry_import._safe_float does
    no rounding) otherwise imbalances the voucher by a paisa and aborted the
    whole store's night.

    Raises TallyExportError -- never a bare ValueError -- on an unpriceable
    document, so one junk row can only fail its own store. Also raises when the
    document contradicts itself (see the two hard stops below), because booking
    100% of a taxed sale as Sales is the exact defect this module exists to
    prevent.
    """
    rules = _canonical_tax_rules()
    if store_states is None or customer_states is None:
        _s, _c = tally_resolve_state_maps(db)
        store_states = _s if store_states is None else store_states
        customer_states = _c if customer_states is None else customer_states
    return [
        _reshape_one_order(o, store_states, customer_states, rules)
        for o in orders or []
    ]


def _canonical_tax_rules() -> tuple:
    """(is_interstate, cgst_sgst_split) imported from the CANONICAL finance
    module. Raises TallyExportError rather than falling back to a local copy of
    the GST maths -- that drift is what this module exists to prevent."""
    try:
        from api.routers.finance import (  # noqa: WPS433 - lazy: avoids an import cycle
            _jv_cgst_sgst_split,
            _order_is_interstate,
        )
    except Exception as e:  # noqa: BLE001 - any import failure is fatal here
        raise TallyExportError(
            "Canonical Tally sales-JV helpers (api.routers.finance) are "
            f"unavailable: {e}. Refusing to build a voucher -- an un-reshaped "
            "export books the gross as Sales with ZERO output GST."
        ) from e
    return _order_is_interstate, _jv_cgst_sgst_split


def _reshape_one_order(
    order: Dict[str, Any],
    store_states: Dict[str, Any],
    customer_states: Dict[str, Any],
    rules: tuple,
) -> Dict[str, Any]:
    """Price ONE order into voucher shape. Raises TallyExportError naming the
    order when it cannot be priced honestly -- per-order so the caller can
    quarantine that single row instead of losing the whole store's night."""
    is_interstate, jv_split = rules
    row = dict(order)
    oid = _order_identity(row)
    tax, tax_key = _first_present_amount(row, _TAX_FIELD_CHAIN)
    grand, _gross_key = _first_present_amount(row, _GROSS_FIELD_CHAIN)
    line_tax = _order_line_tax(row)
    declared_net, net_key = _first_present_amount(row, ("subtotal",))

    # A document whose ONLY tax evidence is per-line is fully priceable -- the
    # line sum IS the order's output GST. Refusing it would drop a real, named
    # sale from the accountant's file for no reason.
    if tax_key is None and line_tax is not None:
        tax, tax_key = line_tax, "items[].tax_amount"

    # HARD STOP 0 -- a Sales voucher cannot carry a negative gross (the
    # formatter would emit an unparseable amount and there is no credit-note
    # path here), and a zero/absent gross alongside a declared tax or taxable
    # is a broken document, not a sale.
    if grand < 0:
        raise TallyExportError(
            f"order {oid!r}: negative gross Rs {grand:.2f} -- a Sales voucher "
            "cannot carry it (credit notes are a separate voucher type)"
        )
    if grand <= 0 and (tax or declared_net or line_tax):
        raise TallyExportError(
            f"order {oid!r}: gross resolves to Rs {grand:.2f} but the document "
            f"declares tax Rs {tax:.2f} / taxable Rs {declared_net:.2f} -- the "
            "gross could not be read, refusing to emit a zero voucher"
        )

    # HARD STOP 1 -- unclassifiable. A non-zero sale whose document carries no
    # tax figure under ANY known key and no per-line tax cannot be priced;
    # booking it as 100% Sales is the zero-GST defect. An EXPLICIT zero is an
    # affirmative "exempt" and IS allowed through -- optical genuinely sells
    # 0%-rated lines (eye tests, hearing aids) and a blanket
    # "gross > 0 and tax == 0 -> refuse" would drop those real sales from the
    # books. The writers now persist None, not a manufactured 0.0, when the
    # source said nothing, which is what makes the two cases distinguishable.
    if grand > 0 and tax_key is None:
        raise TallyExportError(
            f"order {oid!r}: gross Rs {grand:.2f} but the document declares no "
            f"GST under any known key {list(_TAX_FIELD_CHAIN)} and no per-line "
            "tax -- refusing to book it as 100% Sales"
        )

    emitted_net = round(grand - tax, 2)

    # HARD STOP 2a -- the document's own taxable is BELOW the gross while we
    # would book the whole gross as Sales: the doc says Rs 1,000 taxable on a
    # Rs 1,180 sale. A POS order's `subtotal` is the pre-cart-discount
    # tax-INCLUSIVE gross and is therefore >= grand, so a discounted POS bill
    # can never trip this.
    if 0 < declared_net < grand and (emitted_net - declared_net) > _TALLY_TAX_TOLERANCE:
        raise TallyExportError(
            f"order {oid!r}: voucher would book Sales Rs {emitted_net:.2f} but "
            f"the document's own subtotal declares Rs {declared_net:.2f} taxable "
            f"on a Rs {grand:.2f} sale -- unresolved output GST"
        )

    # HARD STOP 2b -- a ZERO-TAX sale on a positive gross must be CORROBORATED.
    # Optical genuinely sells 0%-rated lines (eye tests, hearing aids) and those
    # must keep shipping, so this is NOT "tax == 0 -> refuse". But an
    # UNcorroborated zero books 100% of the invoice as Sales with no GST, which
    # is the defect this module exists to kill.
    #
    # WHICH corroboration is required depends on whether the document carries
    # line detail at all -- keying it on `net_key`/`declared_net` was what let
    # BOTH live escapes through, because a blank taxable column is written as
    # None (key skipped) or 0.0 (nowhere near the gross), so the stop simply
    # never ran:
    #   * LINES PRESENT -> the lines are the authority and must prove it
    #     (0% rate on every line AND coverage of the whole invoice). Line detail
    #     that cannot account for the invoice is a broken document; a header
    #     figure must not rescue it, or a single Re 1 line blesses a Rs 1,180
    #     bill.
    #   * NO LINES (the normal imported-invoice shape) -> the header's declared
    #     taxable equalling the gross IS the corroboration. Refusing that
    #     refused the textbook honest exempt import while the uncorroborated one
    #     shipped -- the two stops were inverted.
    if grand > 0 and tax == 0:
        if _has_line_items(row):
            corroborated = _lines_prove_zero_rated(row, grand)
            how = "no line proves a 0% rate for the whole invoice"
        else:
            corroborated = (
                declared_net > 0
                and abs(declared_net - grand) <= _TALLY_TAX_TOLERANCE
            )
            how = (
                f"the declared taxable Rs {declared_net:.2f} does not corroborate "
                f"the gross and there is no line detail"
            )
        if not corroborated:
            raise TallyExportError(
                f"order {oid!r}: gross Rs {grand:.2f} with Rs 0.00 GST and "
                f"{how} -- the tax is unresolved, not exempt"
            )

    # HARD STOP 3 -- an arithmetically impossible voucher. These all BALANCE
    # (the legs net to zero) and pass the coverage gate, so nothing else catches
    # them: {grand 100, tax 180} books Sales -80.00; {grand 1000, tax -180}
    # books CGST/SGST -90.00 each. A negative Sales or output-GST leg is a
    # credit note in disguise reaching GSTR-1 through the SALES path.
    if tax < 0 or emitted_net < 0:
        raise TallyExportError(
            f"order {oid!r}: would emit Sales Rs {emitted_net:.2f} and GST "
            f"Rs {tax:.2f} -- a Sales voucher cannot carry a negative leg "
            "(tax exceeds the gross, or the tax itself is negative)"
        )

    if is_interstate(row, store_states, customer_states):
        row["igst_amount"] = round(tax, 2)
        row["cgst_amount"] = 0.0
        row["sgst_amount"] = 0.0
    else:
        cgst, sgst = jv_split(tax)
        row["igst_amount"] = 0.0
        row["cgst_amount"] = cgst
        row["sgst_amount"] = sgst
    # Sales A/c must carry the TAXABLE value, never the gross.
    row["subtotal"] = emitted_net
    row["grand_total"] = grand
    return row


def _voucher_legs(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The signed ledger legs `tally_build_day_voucher_xml` will ACTUALLY emit.

    This MIRRORS the formatter's own branch (`if igst > 0` -> the IGST head,
    else CGST + SGST) rather than blindly summing all three GST fields, so the
    gate can never bless a set of legs different from the ones written into the
    XML. Sign convention is the XML's own: the party leg is written as
    `-{total}`, the Sales + output-tax legs positive. A correct Sales voucher
    nets to zero.
    """
    grand = float(row.get("grand_total", 0) or 0)
    igst = float(row.get("igst_amount", 0) or 0)
    legs: List[Dict[str, Any]] = [
        {"ledger": "party", "amount": -round(grand, 2)},
        {"ledger": "Sales A/c", "amount": float(row.get("subtotal", 0) or 0)},
    ]
    if igst > 0:
        legs.append({"ledger": "IGST Output", "amount": igst})
    else:
        legs.append(
            {"ledger": "CGST Output", "amount": float(row.get("cgst_amount", 0) or 0)}
        )
        legs.append(
            {"ledger": "SGST Output", "amount": float(row.get("sgst_amount", 0) or 0)}
        )
    return legs


def tally_build_day_voucher_xml_checked(
    db,
    orders: List[Dict[str, Any]],
    store_meta: Optional[Dict[str, Any]] = None,
    store_states: Optional[Dict[str, Any]] = None,
    customer_states: Optional[Dict[str, Any]] = None,
) -> tuple:
    """Reshape -> GATE -> build. The ONLY entry point an unattended caller may use.

    Three gates run per order BEFORE a single byte of that order's XML exists:

      0. SELF-CONSISTENCY -- `_order_tax_statements_disagree`: a header tax and
         a per-line tax sum that differ by more than 50 paise, in EITHER
         direction (over-stating fabricates liability; under-stating hides it).
      1. PRICEABILITY -- `_reshape_one_order`: a negative or unreadable gross;
         an unclassifiable document (no GST under any known key AND no per-line
         tax); a document whose own `subtotal` contradicts the Sales figure we
         would emit, including a tax-inclusive gross standing in for the taxable
         with no per-line 0-rate proof; or legs that would go negative.
      2. BALANCE -- `assert_voucher_balanced` (api.services.tender_routing) on
         the legs `_voucher_legs` says the formatter will actually write.
         Debits must equal credits or Tally rejects the whole import.
      3. TAX COVERAGE -- the emitted output-tax legs must cover the GST the
         order document itself declares. This catches the original defect
         shape: a voucher whose legs balance because Sales absorbed the tax.

    An EXPLICIT `tax_amount: 0.0` is honoured as a genuine 0%-rated sale (eye
    tests, hearing aids) and ships. The gates rely on the writers persisting
    None -- never a coerced 0.0 -- when the source document said nothing.

    WHAT THE GATES DO **NOT** VERIFY: the HEAD. They check the TOTAL output GST,
    never whether it belongs on CGST+SGST or on IGST. A Jharkhand store billing
    a customer whose `customers.state` is blank (the default -- POS orders do
    not persist an `interstate` flag) books CGST+SGST where IGST is correct, and
    both gates pass. That rule is inherited byte-for-byte from
    finance.get_tally_sales_jv; it is not introduced here and is not fixed here.

    QUARANTINE, NOT BATCH ABORT. A rejected order is dropped from the export
    and reported; the remaining GOOD vouchers are still emitted. One bad
    imported row must never black out a store's books night after night -- the
    owner is not a developer and cannot hand-edit Mongo to unblock it. The
    caller is responsible for marking the resulting row UNBALANCED so the file
    downloads as `*_UNBALANCED.xml` and the missing orders are visible.

    TallyExportError is raised only when NOTHING can be emitted (every order
    rejected, or a canonical helper is unavailable) -- there is no voucher to
    write in that case.

    Returns `(xml, priced_orders, rejected)` where `rejected` is a list of
    `{"order_id": str, "reason": str}`.
    """
    rules = _canonical_tax_rules()
    if store_states is None or customer_states is None:
        _s, _c = tally_resolve_state_maps(db)
        store_states = _s if store_states is None else store_states
        customer_states = _c if customer_states is None else customer_states

    try:
        from api.services.tender_routing import assert_voucher_balanced
    except Exception as e:  # noqa: BLE001
        raise TallyExportError(
            f"Voucher balance gate (api.services.tender_routing) unavailable: {e}. "
            "Refusing to emit an unverified Tally voucher."
        ) from e

    priced: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []

    for original in orders or []:
        oid = _order_identity(original)

        # TWO-SIDED tax-statement check, BEFORE pricing: max() below only sees
        # under-booking, so a header that OVER-states the lines fabricates
        # output-GST liability and passes every other gate.
        disagreement = _order_tax_statements_disagree(original)
        if disagreement:
            rejected.append({"order_id": oid, "reason": disagreement})
            continue

        try:
            row = _reshape_one_order(original, store_states, customer_states, rules)
        except TallyExportError as e:
            rejected.append({"order_id": oid, "reason": str(e)})
            continue

        legs = _voucher_legs(row)
        try:
            assert_voucher_balanced(legs)
        except ValueError as e:
            rejected.append({"order_id": oid, "reason": f"voucher does not balance: {e}"})
            continue

        # Sum the tax legs that will REALLY be written, not the raw fields.
        emitted_tax = round(
            sum(
                leg["amount"]
                for leg in legs
                if str(leg["ledger"]).endswith("Output")
            ),
            2,
        )
        declared_tax = _order_declared_tax(original)
        if declared_tax - emitted_tax > _TALLY_TAX_TOLERANCE:
            rejected.append(
                {
                    "order_id": oid,
                    "reason": (
                        f"voucher books Rs {emitted_tax:.2f} output GST but the "
                        f"order declares Rs {declared_tax:.2f} -- Sales would be "
                        "overstated and the GST liability understated"
                    ),
                }
            )
            continue

        priced.append(row)

    if rejected and not priced:
        shown = "; ".join(f"{r['order_id']}: {r['reason']}" for r in rejected[:5])
        more = f" (+{len(rejected) - 5} more)" if len(rejected) > 5 else ""
        raise TallyExportError(
            f"all {len(rejected)} order(s) failed the Tally voucher gate: "
            f"{shown}{more}"
        )

    xml = tally_build_day_voucher_xml(priced, store_meta=store_meta)
    return xml, priced, rejected


def validate_voucher_balance(orders: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Pre-export sanity check on a day's orders (the ORDER DATA, not the voucher).

    Per-order assertion: `abs(taxable + tax - grand_total) < 0.50`
    (50-paise tolerance for half-up rounding across line items).

    `taxable` is resolved from the order-level field first and then from the
    sum of the per-line `taxable_value` that orders._compute_per_category_gst
    stamps -- a POS/online order does NOT persist an order-level `taxable`, so
    reading only that field made this check silently vacuous on every real
    order. An order with neither is reported as UNVERIFIED (it is excluded from
    the batch identity instead of being scored as if it were fine).

    Per-batch assertion, over the VERIFIED orders only:
    `sum(grand_total) ≈ sum(taxable) + sum(tax)` within ₹1 (cumulative rounding
    can exceed the per-row tolerance). `total_discount` is NOT subtracted:
    grand_total is already net of every item + cart discount, so subtracting it
    again failed the batch check for any day that had a single discount.

    `ok` REQUIRES `unverified_count == 0`. "Nothing was checked" must never
    render as "checked and fine": the download endpoint reads only this flag,
    so a day made entirely of documents whose taxable cannot be resolved (a
    TechCherry / ONDC import day) would otherwise download as a clean
    `GK1_<date>.xml` with `X-Tally-Balanced: 1` -- an affirmative green light
    derived from ZERO checked orders, on exactly the voucher shape the emit
    gates are weakest against.

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
    # Batch identity accumulators -- verified orders only.
    batch_grand = 0.0
    batch_taxable = 0.0
    batch_tax = 0.0
    unverified = 0

    for o in orders:
        # Resolve gross and tax through the SAME chains the reshape prices
        # from. Two functions in this file resolving the order's gross
        # differently is the very drift this module exists to prevent: reading
        # only `grand_total` here false-flagged a CORRECT voucher built from a
        # legacy order that carries `total` instead.
        grand, _ = _amounts_or_zero(o, _GROSS_FIELD_CHAIN)
        taxable = _resolve_order_taxable(o)
        tax, _ = _amounts_or_zero(o, _TAX_FIELD_CHAIN)
        subtotal, _ = _amounts_or_zero(o, ("subtotal",))
        discount, _ = _amounts_or_zero(o, ("total_discount",))

        sum_grand += grand
        sum_subtotal += subtotal
        sum_taxable += taxable or 0.0
        sum_tax += tax
        sum_discount += discount

        # taxable + tax should land within 50 paise of grand_total.
        # An order with no resolvable taxable (legacy rows that predate the
        # per-category GST split) is counted as UNVERIFIED, not as a pass.
        # EXCEPTION: a legitimate Rs 0 order (a fully-approved 100%-discount
        # bill) has nothing to verify and nothing at risk -- counting it would
        # flip a whole store's file to _UNBALANCED and blunt the one flag still
        # guarding the zero-GST class.
        if not taxable or taxable <= 0:
            if grand == 0 and tax == 0:
                continue
            unverified += 1
            continue

        batch_grand += grand
        batch_taxable += taxable
        batch_tax += tax

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

    batch_check_lhs = round(batch_grand, 2)
    batch_check_rhs = round(batch_taxable + batch_tax, 2)
    batch_delta = round(batch_check_lhs - batch_check_rhs, 2)
    batch_ok = abs(batch_delta) < 1.00

    return {
        # unverified == 0 is part of the verdict, not a footnote: see the
        # docstring. `verified` is broken out so a consumer can distinguish
        # "checked and wrong" from "could not check".
        "ok": len(mismatches) == 0 and batch_ok and unverified == 0,
        "verified": unverified == 0,
        "unverified_count": unverified,
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
            # Orders whose taxable could not be resolved at all -- reported so
            # "0 mismatches" is never mistaken for "everything was checked".
            "unverified_count": unverified,
        },
    }


def _resolve_order_taxable(order: Dict[str, Any]) -> Optional[float]:
    """Order-level `taxable` if present, else the sum of the per-line
    `taxable_value` stamped by orders._compute_per_category_gst. None when the
    document carries neither (the order cannot be verified)."""
    raw = order.get("taxable")
    if raw is not None:
        try:
            value = float(raw or 0)
            if value:
                return round(value, 2)
        except (TypeError, ValueError):
            pass
    items = order.get("items")
    if not isinstance(items, list) or not items:
        return None
    total = 0.0
    seen = False
    for it in items:
        if not isinstance(it, dict):
            continue
        line = it.get("taxable_value")
        if line is None:
            continue
        try:
            total += float(line or 0)
            seen = True
        except (TypeError, ValueError):
            continue
    return round(total, 2) if seen else None
