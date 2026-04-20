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

import httpx

from .providers import dispatch_mode  # reuse DISPATCH_MODE gate

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
        return doc.get("config") or {}
    except Exception as e:
        logger.debug(f"[NEXUS] Config read failed for {integration_type}: {e}")
        return {}


def _is_destructive_allowed() -> bool:
    """Shopify/Razorpay WRITES gated on DISPATCH_MODE=live (matches WhatsApp gate)."""
    return dispatch_mode() == "live"


# ============================================================================
# SHOPIFY — product push + order pull
# ============================================================================


async def shopify_pull_orders(db, since_hours: int = 2) -> SyncResult:
    """Pull Shopify orders created in the last N hours for fulfillment routing."""
    cfg = _load_integration_config(db, "shopify")
    shop_url = cfg.get("shop_url")
    access_token = cfg.get("access_token")
    if not shop_url or not access_token:
        return SyncResult(ok=False, provider="shopify", kind="pull",
                          error="shop_url or access_token not configured")

    since = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()
    url = f"https://{shop_url}/admin/api/2024-01/orders.json"
    params = {"status": "any", "updated_at_min": since, "limit": 100}
    headers = {"X-Shopify-Access-Token": access_token}

    try:
        async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT) as client:
            resp = await client.get(url, params=params, headers=headers)
        if resp.status_code != 200:
            return SyncResult(ok=False, provider="shopify", kind="pull",
                              error=f"status {resp.status_code}: {resp.text[:200]}")
        orders = resp.json().get("orders", [])
        return SyncResult(
            ok=True, provider="shopify", kind="pull",
            items_synced=len(orders),
            payload={"order_ids": [o.get("id") for o in orders[:10]]},  # sample
        )
    except httpx.TimeoutException:
        return SyncResult(ok=False, provider="shopify", kind="pull", error="timeout")
    except (httpx.HTTPError, ValueError) as e:
        return SyncResult(ok=False, provider="shopify", kind="pull", error=str(e))


async def shopify_push_product(db, product: Dict[str, Any]) -> SyncResult:
    """Push one product to Shopify (create or update). Gated on DISPATCH_MODE=live."""
    if not _is_destructive_allowed():
        return SyncResult(ok=True, provider="shopify", kind="push",
                          notes=f"SIMULATED — dispatch_mode={dispatch_mode()}")

    cfg = _load_integration_config(db, "shopify")
    shop_url = cfg.get("shop_url")
    access_token = cfg.get("access_token")
    if not shop_url or not access_token:
        return SyncResult(ok=False, provider="shopify", kind="push",
                          error="shop_url or access_token not configured")

    shopify_id = product.get("shopify_product_id")
    path = (
        f"products/{shopify_id}.json" if shopify_id
        else "products.json"
    )
    method = "PUT" if shopify_id else "POST"
    url = f"https://{shop_url}/admin/api/2024-01/{path}"
    headers = {"X-Shopify-Access-Token": access_token, "content-type": "application/json"}
    body = {"product": product}

    try:
        async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT) as client:
            resp = await client.request(method, url, headers=headers, json=body)
        if resp.status_code not in (200, 201):
            return SyncResult(ok=False, provider="shopify", kind="push",
                              error=f"status {resp.status_code}: {resp.text[:200]}")
        returned = resp.json().get("product") or {}
        return SyncResult(
            ok=True, provider="shopify", kind="push",
            items_synced=1,
            payload={"shopify_product_id": returned.get("id")},
        )
    except httpx.TimeoutException:
        return SyncResult(ok=False, provider="shopify", kind="push", error="timeout")
    except (httpx.HTTPError, ValueError) as e:
        return SyncResult(ok=False, provider="shopify", kind="push", error=str(e))


# ============================================================================
# RAZORPAY — payment reconciliation (read-only)
# ============================================================================


async def razorpay_list_payments(db, since_hours: int = 2) -> SyncResult:
    """Pull recent Razorpay payments. Used to reconcile IMS orders vs Razorpay settlements."""
    cfg = _load_integration_config(db, "razorpay")
    key_id = cfg.get("key_id")
    key_secret = cfg.get("key_secret")
    if not key_id or not key_secret:
        return SyncResult(ok=False, provider="razorpay", kind="pull",
                          error="key_id or key_secret not configured")

    since = int((datetime.now(timezone.utc) - timedelta(hours=since_hours)).timestamp())
    url = "https://api.razorpay.com/v1/payments"
    params = {"from": since, "count": 100}

    try:
        async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT) as client:
            resp = await client.get(url, params=params, auth=(key_id, key_secret))
        if resp.status_code != 200:
            return SyncResult(ok=False, provider="razorpay", kind="pull",
                              error=f"status {resp.status_code}: {resp.text[:200]}")
        items = resp.json().get("items", [])
        return SyncResult(
            ok=True, provider="razorpay", kind="pull",
            items_synced=len(items),
            payload={"total": len(items), "captured": sum(1 for i in items if i.get("status") == "captured")},
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
        return SyncResult(ok=False, provider="shiprocket", kind="pull",
                          error="email or password not configured")

    # Shiprocket API requires a token via /auth/login first
    try:
        async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT) as client:
            auth_resp = await client.post(
                "https://apiv2.shiprocket.in/v1/external/auth/login",
                json={"email": email, "password": password},
            )
            if auth_resp.status_code != 200:
                return SyncResult(ok=False, provider="shiprocket", kind="pull",
                                  error=f"auth status {auth_resp.status_code}")
            token = (auth_resp.json() or {}).get("token")
            if not token:
                return SyncResult(ok=False, provider="shiprocket", kind="pull",
                                  error="no token in auth response")

            # Track the AWB
            track_resp = await client.get(
                f"https://apiv2.shiprocket.in/v1/external/courier/track/awb/{awb}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if track_resp.status_code != 200:
                return SyncResult(ok=False, provider="shiprocket", kind="pull",
                                  error=f"track status {track_resp.status_code}")
            data = track_resp.json() or {}
            tracking = (data.get("tracking_data") or {}).get("shipment_track") or []
            latest_status = tracking[0].get("current_status") if tracking else None
            return SyncResult(
                ok=True, provider="shiprocket", kind="pull",
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


def tally_build_day_voucher_xml(orders: List[Dict[str, Any]]) -> str:
    """
    Build a single Tally import XML for the day's sales vouchers.
    Pure function — no I/O. The caller decides what to do with the XML
    (write to tally_exports collection for CA to download, or push to
    Tally HTTP-Server if one's wired).

    Tally XML format: https://help.tallysolutions.com/docs/te9rel66/Tally.ERP9/...
    Simplified schema — one VOUCHER per order with sales ledger + party
    ledger + tax ledgers (CGST/SGST). Real tally templates add
    cost-center allocations, but those are per-tenant and can be
    parameterized later.
    """
    vouchers = []
    for o in orders:
        order_id = o.get("order_id", "")
        order_date = o.get("created_at", "")[:10].replace("-", "")  # yyyymmdd
        party = o.get("customer_name") or "Walk-in Customer"
        subtotal = float(o.get("subtotal", 0) or 0)
        cgst = float(o.get("cgst_amount", 0) or 0)
        sgst = float(o.get("sgst_amount", 0) or 0)
        total = float(o.get("grand_total", 0) or 0)

        voucher = f"""
  <VOUCHER VCHTYPE="Sales" ACTION="Create">
    <DATE>{order_date}</DATE>
    <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
    <VOUCHERNUMBER>{order_id}</VOUCHERNUMBER>
    <PARTYLEDGERNAME>{party}</PARTYLEDGERNAME>
    <ALLLEDGERENTRIES.LIST>
      <LEDGERNAME>{party}</LEDGERNAME>
      <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
      <AMOUNT>-{total:.2f}</AMOUNT>
    </ALLLEDGERENTRIES.LIST>
    <ALLLEDGERENTRIES.LIST>
      <LEDGERNAME>Sales A/c</LEDGERNAME>
      <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
      <AMOUNT>{subtotal:.2f}</AMOUNT>
    </ALLLEDGERENTRIES.LIST>
    <ALLLEDGERENTRIES.LIST>
      <LEDGERNAME>CGST Output</LEDGERNAME>
      <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
      <AMOUNT>{cgst:.2f}</AMOUNT>
    </ALLLEDGERENTRIES.LIST>
    <ALLLEDGERENTRIES.LIST>
      <LEDGERNAME>SGST Output</LEDGERNAME>
      <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
      <AMOUNT>{sgst:.2f}</AMOUNT>
    </ALLLEDGERENTRIES.LIST>
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
