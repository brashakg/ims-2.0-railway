"""
IMS 2.0 - Shiprocket shipping service
=====================================
Thin async client for Shiprocket (India's logistics aggregator). Three jobs:

  1. authenticate()        - log in, cache the bearer token (Shiprocket tokens
                             last ~10 days; we cache in-process with a margin).
  2. create_shipment()     - book a "custom" order + shipment on Shiprocket from
                             one IMS order; returns awb / shipment_id / label url.
  3. track()               - fetch the current tracking status for an AWB or a
                             Shiprocket shipment id.

Design mirrors agents/providers.py + agents/nexus_providers.py exactly:

  - Every network call is async, uses httpx, and FAILS SOFT. Missing creds,
    timeouts, non-200s -> a structured result, never an exception. A misbooked
    deploy must never 500 the Orders page.
  - DISPATCH_MODE (reused from agents.providers) gates the one DESTRUCTIVE call
    (create_shipment, which actually books a courier + can cost money). When
    DISPATCH_MODE != 'live', OR credentials are unset, create_shipment returns a
    SIMULATED result with a deterministic fake AWB and never touches the network
    - identical to how MEGAPHONE/NEXUS gate their sends. track() is read-only so
    it is allowed in any mode, but still degrades to a SIMULATED/last-known
    payload when creds are missing rather than raising.

Credentials are resolved from EITHER:
  - env vars  SHIPROCKET_EMAIL / SHIPROCKET_PASSWORD   (preferred, documented
    in CLAUDE.md so Railway can set them as variable references), OR
  - the `integrations` MongoDB collection ({type:'shiprocket', enabled:true,
    config:{email, password}}) - the same place nexus_providers.shiprocket_track_awb
    already reads from, so an admin-configured integration keeps working.

ASCII only (Windows cp1252). Use "Rs" not the rupee glyph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import logging
import os

import httpx

try:
    # Reuse the single DISPATCH_MODE gate the rest of the app uses.
    from agents.providers import dispatch_mode
except Exception:  # noqa: BLE001 - never let an import break the module

    def dispatch_mode() -> str:
        return os.getenv("DISPATCH_MODE", "off").lower()


logger = logging.getLogger(__name__)

SHIPROCKET_BASE_URL = "https://apiv2.shiprocket.in/v1/external"
PROVIDER_TIMEOUT = float(os.getenv("SHIPROCKET_TIMEOUT", "30.0"))

# Default pickup location nickname registered in the Shiprocket dashboard.
# Real bookings need a pickup location that exists in the account; this is the
# Shiprocket "Primary" default and is overridable per-call / per-env.
DEFAULT_PICKUP_LOCATION = os.getenv("SHIPROCKET_PICKUP_LOCATION", "Primary")


# ============================================================================
# Result envelope (mirrors providers.DispatchResult / nexus.SyncResult)
# ============================================================================


@dataclass
class ShipResult:
    """Standard result for any Shiprocket call. Never raised - always returned."""

    ok: bool
    status: str  # BOOKED | TRACKED | SIMULATED | FAILED | SKIPPED
    awb: Optional[str] = None
    shipment_id: Optional[str] = None
    sr_order_id: Optional[str] = None  # Shiprocket's internal order id
    courier: Optional[str] = None
    label_url: Optional[str] = None
    tracking_status: Optional[str] = None
    tracking_url: Optional[str] = None
    error: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)
    dispatched_at: str = ""

    def __post_init__(self) -> None:
        if not self.dispatched_at:
            self.dispatched_at = datetime.now(timezone.utc).isoformat()


# ============================================================================
# Credential resolution (env first, then integrations collection)
# ============================================================================


def _creds_from_integrations(db) -> Dict[str, str]:
    """Read {email, password} from the integrations collection. {} if absent."""
    if db is None:
        return {}
    try:
        coll = db.get_collection("integrations")
        doc = coll.find_one({"type": "shiprocket", "enabled": True})
        if not doc:
            return {}
        cfg = doc.get("config") or {}
        return {
            "email": cfg.get("email") or "",
            "password": cfg.get("password") or "",
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("[SHIPROCKET] integrations read failed: %s", exc)
        return {}


def _resolve_credentials(db=None) -> Dict[str, str]:
    """Env vars win; fall back to the integrations collection."""
    email = os.getenv("SHIPROCKET_EMAIL", "")
    password = os.getenv("SHIPROCKET_PASSWORD", "")
    if email and password:
        return {"email": email, "password": password}
    return _creds_from_integrations(db)


def credentials_present(db=None) -> bool:
    """True when we have both an email and a password from some source."""
    creds = _resolve_credentials(db)
    return bool(creds.get("email") and creds.get("password"))


# ============================================================================
# Auth - cache the bearer token in-process
# ============================================================================

# Module-level token cache. Shiprocket tokens are valid for ~240h; we keep a
# generous safety margin and just re-auth if a call 401s. Key includes the
# email so switching creds doesn't reuse a stale token.
_token_cache: Dict[str, Any] = {"email": None, "token": None, "fetched_at": 0.0}
# Re-auth after this many seconds even if the token would technically still work.
_TOKEN_TTL_SECONDS = float(os.getenv("SHIPROCKET_TOKEN_TTL", str(9 * 24 * 3600)))


def _cached_token(email: str) -> Optional[str]:
    if _token_cache.get("email") != email or not _token_cache.get("token"):
        return None
    age = datetime.now(timezone.utc).timestamp() - float(
        _token_cache.get("fetched_at") or 0.0
    )
    if age > _TOKEN_TTL_SECONDS:
        return None
    return _token_cache.get("token")


def _store_token(email: str, token: str) -> None:
    _token_cache["email"] = email
    _token_cache["token"] = token
    _token_cache["fetched_at"] = datetime.now(timezone.utc).timestamp()


def _reset_token_cache() -> None:
    _token_cache["email"] = None
    _token_cache["token"] = None
    _token_cache["fetched_at"] = 0.0


async def authenticate(db=None, *, force: bool = False) -> ShipResult:
    """Log in to Shiprocket and cache the bearer token. Never raises.

    Returns a ShipResult; on success ``raw['token']`` holds the bearer token.
    SIMULATED (no network) when credentials are unset.
    """
    creds = _resolve_credentials(db)
    email = creds.get("email") or ""
    password = creds.get("password") or ""
    if not email or not password:
        return ShipResult(
            ok=False,
            status="SIMULATED",
            error="shiprocket credentials not configured",
        )

    if not force:
        cached = _cached_token(email)
        if cached:
            return ShipResult(ok=True, status="TRACKED", raw={"token": cached})

    try:
        async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT) as client:
            resp = await client.post(
                f"{SHIPROCKET_BASE_URL}/auth/login",
                json={"email": email, "password": password},
            )
        if resp.status_code != 200:
            return ShipResult(
                ok=False,
                status="FAILED",
                error=f"auth status {resp.status_code}",
            )
        token = (resp.json() or {}).get("token")
        if not token:
            return ShipResult(
                ok=False, status="FAILED", error="no token in auth response"
            )
        _store_token(email, token)
        return ShipResult(ok=True, status="TRACKED", raw={"token": token})
    except httpx.TimeoutException:
        return ShipResult(ok=False, status="FAILED", error="auth timeout")
    except (httpx.HTTPError, ValueError) as exc:
        return ShipResult(ok=False, status="FAILED", error=f"auth error: {exc}")


# ============================================================================
# Payload helpers
# ============================================================================


def _simulated_awb(order_id: str) -> str:
    """Deterministic fake AWB so the same order simulates the same number."""
    stamp = datetime.now(timezone.utc).strftime("%y%m%d")
    suffix = "".join(c for c in (order_id or "SIM") if c.isalnum())[-6:].upper()
    return f"SIMSR{stamp}{suffix or 'ORDER'}"


def build_shipment_payload(
    order: Dict[str, Any],
    address: Dict[str, Any],
    *,
    pickup_location: Optional[str] = None,
) -> Dict[str, Any]:
    """Map an IMS order + a delivery address into Shiprocket's create-custom-order
    request body. Pure function (no I/O) so it is unit-testable in isolation.

    `order` is the IMS order doc (snake_case). `address` carries the ship-to
    fields the booking form supplies (with customer-doc fallbacks resolved by the
    caller). Money is in Rupees. Quantities default to 1.
    """
    items = order.get("items") or []
    line_items: List[Dict[str, Any]] = []
    for it in items:
        line_items.append(
            {
                "name": (it.get("product_name") or it.get("sku") or "Item")[:100],
                "sku": (it.get("sku") or it.get("product_id") or "SKU")[:50],
                "units": int(it.get("quantity") or 1),
                "selling_price": float(
                    it.get("item_total")
                    or it.get("unit_price")
                    or 0.0
                ),
            }
        )
    if not line_items:
        # Shiprocket rejects empty carts; send a single summary line.
        line_items.append(
            {
                "name": "Order " + str(order.get("order_number") or ""),
                "sku": str(order.get("order_id") or "ORDER"),
                "units": 1,
                "selling_price": float(order.get("grand_total") or 0.0),
            }
        )

    order_date = (order.get("created_at") or datetime.now().isoformat())[:10]
    sub_total = float(order.get("grand_total") or order.get("subtotal") or 0.0)

    return {
        "order_id": str(order.get("order_number") or order.get("order_id") or ""),
        "order_date": order_date,
        "pickup_location": pickup_location or DEFAULT_PICKUP_LOCATION,
        "billing_customer_name": address.get("name")
        or order.get("customer_name")
        or "Customer",
        "billing_last_name": address.get("last_name") or "",
        "billing_address": address.get("address") or "",
        "billing_city": address.get("city") or "",
        "billing_pincode": str(address.get("pincode") or ""),
        "billing_state": address.get("state") or "",
        "billing_country": address.get("country") or "India",
        "billing_email": address.get("email") or order.get("customer_email") or "",
        "billing_phone": str(
            address.get("phone") or order.get("customer_phone") or ""
        ),
        "shipping_is_billing": True,
        "order_items": line_items,
        "payment_method": address.get("payment_method") or "Prepaid",
        "sub_total": sub_total,
        # Default parcel dims (cm / kg) - small optical parcel. Overridable.
        "length": float(address.get("length") or 15),
        "breadth": float(address.get("breadth") or 10),
        "height": float(address.get("height") or 6),
        "weight": float(address.get("weight") or 0.5),
    }


# ============================================================================
# Create shipment - the one DESTRUCTIVE, DISPATCH_MODE-gated call
# ============================================================================


async def create_shipment(
    order: Dict[str, Any],
    address: Dict[str, Any],
    db=None,
    *,
    pickup_location: Optional[str] = None,
) -> ShipResult:
    """Book a shipment on Shiprocket for one IMS order. NEVER raises.

    Gating (mirrors providers.send_whatsapp):
      - DISPATCH_MODE != 'live'  -> SIMULATED (no network), fake AWB.
      - credentials unset        -> SIMULATED (no network), fake AWB.
    Only when mode == 'live' AND creds are present do we hit Shiprocket.
    """
    order_id = str(order.get("order_id") or order.get("order_number") or "")

    mode = dispatch_mode()
    if mode != "live":
        return ShipResult(
            ok=True,
            status="SIMULATED",
            awb=_simulated_awb(order_id),
            shipment_id=f"sim-shp-{order_id}" if order_id else "sim-shp",
            courier="SIMULATED",
            tracking_status="SIMULATED",
            error=f"DISPATCH_MODE={mode} - not booking a live shipment",
        )

    if not credentials_present(db):
        return ShipResult(
            ok=True,
            status="SIMULATED",
            awb=_simulated_awb(order_id),
            shipment_id=f"sim-shp-{order_id}" if order_id else "sim-shp",
            courier="SIMULATED",
            tracking_status="SIMULATED",
            error="shiprocket credentials not configured",
        )

    auth = await authenticate(db)
    token = (auth.raw or {}).get("token")
    if not auth.ok or not token:
        return ShipResult(
            ok=False, status="FAILED", error=auth.error or "auth failed"
        )

    payload = build_shipment_payload(
        order, address, pickup_location=pickup_location
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT) as client:
            resp = await client.post(
                f"{SHIPROCKET_BASE_URL}/orders/create/adhoc",
                headers=headers,
                json=payload,
            )
        if resp.status_code == 401:
            # Token went stale - re-auth once and retry.
            _reset_token_cache()
            auth = await authenticate(db, force=True)
            token = (auth.raw or {}).get("token")
            if not auth.ok or not token:
                return ShipResult(
                    ok=False, status="FAILED", error="re-auth failed"
                )
            async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT) as client:
                resp = await client.post(
                    f"{SHIPROCKET_BASE_URL}/orders/create/adhoc",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        if resp.status_code not in (200, 201):
            return ShipResult(
                ok=False,
                status="FAILED",
                error=f"create status {resp.status_code}: {resp.text[:200]}",
            )
        data = resp.json() or {}
        return ShipResult(
            ok=True,
            status="BOOKED",
            awb=data.get("awb_code") or None,
            shipment_id=str(data.get("shipment_id") or "") or None,
            sr_order_id=str(data.get("order_id") or "") or None,
            courier=data.get("courier_name") or None,
            label_url=data.get("label_url") or None,
            tracking_status=data.get("status") or "NEW",
            raw=data,
        )
    except httpx.TimeoutException:
        return ShipResult(ok=False, status="FAILED", error="create timeout")
    except (httpx.HTTPError, ValueError) as exc:
        return ShipResult(ok=False, status="FAILED", error=f"create error: {exc}")


# ============================================================================
# Track - read-only, allowed in any mode (still fail-soft)
# ============================================================================


async def track(
    awb: Optional[str] = None,
    shipment_id: Optional[str] = None,
    db=None,
) -> ShipResult:
    """Fetch current tracking for an AWB (preferred) or a Shiprocket shipment id.

    Read-only, so it runs in any DISPATCH_MODE. When credentials are unset it
    returns a SIMULATED result rather than raising, so callers can always fall
    back to the last-known status persisted on the shipment doc.
    """
    if not awb and not shipment_id:
        return ShipResult(ok=False, status="FAILED", error="awb or shipment_id required")

    if not credentials_present(db):
        return ShipResult(
            ok=True,
            status="SIMULATED",
            awb=awb,
            shipment_id=shipment_id,
            tracking_status="SIMULATED",
            error="shiprocket credentials not configured",
        )

    auth = await authenticate(db)
    token = (auth.raw or {}).get("token")
    if not auth.ok or not token:
        return ShipResult(
            ok=False, status="FAILED", awb=awb, error=auth.error or "auth failed"
        )

    if awb:
        url = f"{SHIPROCKET_BASE_URL}/courier/track/awb/{awb}"
    else:
        url = f"{SHIPROCKET_BASE_URL}/courier/track/shipment/{shipment_id}"

    try:
        async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT) as client:
            resp = await client.get(
                url, headers={"Authorization": f"Bearer {token}"}
            )
        if resp.status_code != 200:
            return ShipResult(
                ok=False,
                status="FAILED",
                awb=awb,
                shipment_id=shipment_id,
                error=f"track status {resp.status_code}",
            )
        data = resp.json() or {}
        tracking_data = data.get("tracking_data") or {}
        shipment_track = tracking_data.get("shipment_track") or []
        latest = shipment_track[0] if shipment_track else {}
        return ShipResult(
            ok=True,
            status="TRACKED",
            awb=awb or str(latest.get("awb_code") or "") or None,
            shipment_id=shipment_id,
            tracking_status=latest.get("current_status")
            or tracking_data.get("shipment_status")
            or None,
            tracking_url=tracking_data.get("track_url") or None,
            courier=latest.get("courier_name") or None,
            raw=data,
        )
    except httpx.TimeoutException:
        return ShipResult(ok=False, status="FAILED", awb=awb, error="track timeout")
    except (httpx.HTTPError, ValueError) as exc:
        return ShipResult(
            ok=False, status="FAILED", awb=awb, error=f"track error: {exc}"
        )
