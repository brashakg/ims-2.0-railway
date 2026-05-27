"""
IMS 2.0 - Admin Router
======================
Admin API endpoints for integrations, system management, and configuration.
Handles Shopify, Shiprocket, and other third-party integrations.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime
from .auth import get_current_user
import os


def _simulated_integration_response(
    integration: str, action: str, extra: dict | None = None
) -> dict:
    """Honest response for integration test/sync endpoints that do NOT perform
    a live API call. Real integration runs through the NEXUS agent
    (nexus_providers.py) with credentials + DISPATCH_MODE=live. We return a
    truthful 'simulated' status instead of fabricated success metrics."""
    resp = {
        "status": "simulated",
        "simulated": True,
        "integration": integration,
        "action": action,
        "message": (
            f"{integration} {action} is not performed live by this endpoint. "
            "Configuration is saved; real sync runs via the NEXUS agent when "
            "credentials and DISPATCH_MODE=live are set."
        ),
    }
    if extra:
        resp.update(extra)
    return resp


async def _require_admin_role(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Router-level dependency: gate every admin endpoint to SUPERADMIN/ADMIN.

    Integration endpoints expose identifiers (shop URLs, vendor emails,
    webhook IDs) that shouldn't be visible to store staff even when API-key
    fields are masked. Test and sync endpoints trigger real (or simulated)
    third-party calls, which is strictly an admin concern. Before this gate
    was added, any authenticated user could hit /api/v1/admin/integrations/*
    and see whether a store has Shopify configured, which vendor we ship
    through, etc. Now only SUPERADMIN/ADMIN get a response.

    Applied at the APIRouter level (see below) so it cannot be bypassed by
    forgetting to add `Depends(...)` on a new handler.
    """
    roles = current_user.get("roles", []) if current_user else []
    if not any(r in ("SUPERADMIN", "ADMIN") for r in roles):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return current_user


# Gate the entire admin router — 24+ endpoints, all integration-related,
# none of which should be exposed to store-level roles.
router = APIRouter(dependencies=[Depends(_require_admin_role)])


# ============================================================================
# DATABASE HELPER FUNCTIONS
# ============================================================================


def _get_integrations_collection():
    """Get integrations collection from database"""
    try:
        from database.connection import get_db

        db = get_db()
        if db and db.is_connected:
            return db.get_collection("integrations")
    except Exception:
        pass
    return None


def _get_integration_config(integration_type: str) -> dict:
    """Get a specific integration config from database"""
    collection = _get_integrations_collection()
    if collection is not None:
        config = collection.find_one({"type": integration_type.lower()})
        if config:
            config.pop("_id", None)
            return config
    return {"enabled": False, "config": {}}


def _save_integration_config(integration_type: str, config_data: dict) -> bool:
    """Save integration config to database"""
    collection = _get_integrations_collection()
    if collection is not None:
        collection.update_one(
            {"type": integration_type.lower()},
            {"$set": {**config_data, "type": integration_type.lower()}},
            upsert=True,
        )
        return True
    return False


# ============================================================================
# SCHEMAS
# ============================================================================


class ShopifyConfig(BaseModel):
    shop_url: str = Field(
        ..., description="Shopify store URL (e.g., mystore.myshopify.com)"
    )
    api_key: str = Field(..., description="Shopify API key")
    api_secret: str = Field(..., description="Shopify API secret")
    access_token: str = Field(..., description="Shopify access token")
    enabled: bool = True


class ShiprocketConfig(BaseModel):
    email: str = Field(..., description="Shiprocket account email")
    password: str = Field(..., description="Shiprocket account password")
    pickup_location_id: Optional[str] = None
    enabled: bool = True


class RazorpayConfig(BaseModel):
    key_id: str = Field(..., description="Razorpay Key ID")
    key_secret: str = Field(..., description="Razorpay Key Secret")
    webhook_secret: Optional[str] = None
    enabled: bool = True


class WhatsappConfig(BaseModel):
    api_key: str = Field(..., description="WhatsApp Business API key")
    phone_number_id: str = Field(..., description="Phone number ID")
    business_id: str = Field(..., description="Business account ID")
    enabled: bool = True


class TallyConfig(BaseModel):
    server_url: str = Field(..., description="Tally server URL")
    company_name: str = Field(..., description="Company name in Tally")
    sync_interval: int = Field(default=60, description="Sync interval in minutes")
    enabled: bool = True


class SmsConfig(BaseModel):
    provider: str = Field(..., description="SMS provider (MSG91, Twilio, etc.)")
    api_key: str = Field(..., description="API key")
    sender_id: str = Field(..., description="Sender ID")
    enabled: bool = True


class ShipmentRequest(BaseModel):
    order_id: str
    pickup_location: Optional[str] = None
    delivery_name: str
    delivery_phone: str
    delivery_address: str
    delivery_city: str
    delivery_state: str
    delivery_pincode: str
    length: float = 30
    width: float = 20
    height: float = 10
    weight: float = 0.5


class ShopifyOrderSync(BaseModel):
    since_date: Optional[str] = None
    status: Optional[str] = None


# ============================================================================
# NOTE: Integration configs are now stored in the database.
# ============================================================================


# ============================================================================
# SHOPIFY INTEGRATION ENDPOINTS
# ============================================================================


@router.get("")
@router.get("/")
async def get_admin_root():
    """Root endpoint for admin integrations overview"""
    return {
        "module": "admin",
        "status": "active",
        "message": "integrations endpoint ready",
    }


@router.get("/integrations/shopify")
async def get_shopify_config(current_user: dict = Depends(get_current_user)):
    """Get Shopify integration configuration"""
    config = _get_integration_config("shopify")
    return {
        "type": "SHOPIFY",
        "name": "Shopify",
        "description": "Shopify POS + stock sync (inventory mirror)",
        "is_configured": bool(config.get("config")),
        "is_enabled": config.get("enabled", False),
        "config": {
            "shop_url": config.get("config", {}).get("shop_url", ""),
            # Don't expose secrets
            "api_key": "***" if config.get("config", {}).get("api_key") else "",
        },
    }


@router.post("/integrations/shopify")
async def set_shopify_config(
    config: ShopifyConfig, current_user: dict = Depends(get_current_user)
):
    """Configure Shopify integration"""
    if not any(
        role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    _save_integration_config(
        "shopify", {"enabled": config.enabled, "config": config.model_dump()}
    )

    return {"message": "Shopify configuration saved", "enabled": config.enabled}


@router.post("/integrations/shopify/test")
async def test_shopify_connection(current_user: dict = Depends(get_current_user)):
    """Test Shopify API connection"""
    config = _get_integration_config("shopify").get("config", {})

    if not config.get("shop_url"):
        raise HTTPException(status_code=400, detail="Shopify not configured")

    return _simulated_integration_response("Shopify", "connection test")


@router.post("/integrations/shopify/sync-orders")
async def sync_shopify_orders(
    sync_params: ShopifyOrderSync = None, current_user: dict = Depends(get_current_user)
):
    """Sync orders from Shopify"""
    config = _get_integration_config("shopify")

    if not config.get("enabled"):
        raise HTTPException(status_code=400, detail="Shopify integration not enabled")

    return _simulated_integration_response("Shopify", "order sync")


@router.post("/integrations/shopify/sync-inventory")
async def sync_shopify_inventory(current_user: dict = Depends(get_current_user)):
    """Push inventory updates to Shopify"""
    config = _get_integration_config("shopify")

    if not config.get("enabled"):
        raise HTTPException(status_code=400, detail="Shopify integration not enabled")

    return _simulated_integration_response("Shopify", "inventory sync")


# ============================================================================
# SHIPROCKET INTEGRATION ENDPOINTS
# ============================================================================


@router.get("/integrations/shiprocket")
async def get_shiprocket_config(current_user: dict = Depends(get_current_user)):
    """Get Shiprocket integration configuration"""
    config = _get_integration_config("shiprocket")
    return {
        "type": "SHIPROCKET",
        "name": "Shiprocket",
        "description": "Shipping and logistics integration",
        "is_configured": bool(config.get("config")),
        "is_enabled": config.get("enabled", False),
        "config": {
            "email": config.get("config", {}).get("email", ""),
            "pickup_location_id": config.get("config", {}).get(
                "pickup_location_id", ""
            ),
        },
    }


@router.post("/integrations/shiprocket")
async def set_shiprocket_config(
    config: ShiprocketConfig, current_user: dict = Depends(get_current_user)
):
    """Configure Shiprocket integration"""
    if not any(
        role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    _save_integration_config(
        "shiprocket",
        {
            "enabled": config.enabled,
            "config": config.model_dump(),
            "token": None,
            "token_expiry": None,
        },
    )

    return {"message": "Shiprocket configuration saved", "enabled": config.enabled}


@router.post("/integrations/shiprocket/test")
async def test_shiprocket_connection(current_user: dict = Depends(get_current_user)):
    """Test Shiprocket API connection"""
    config = _get_integration_config("shiprocket").get("config", {})

    if not config.get("email"):
        raise HTTPException(status_code=400, detail="Shiprocket not configured")

    return _simulated_integration_response("Shiprocket", "connection test")


@router.post("/integrations/shiprocket/create-shipment")
async def create_shiprocket_shipment(
    shipment: ShipmentRequest, current_user: dict = Depends(get_current_user)
):
    """Create a shipment in Shiprocket"""
    config = _get_integration_config("shiprocket")

    if not config.get("enabled"):
        raise HTTPException(
            status_code=400, detail="Shiprocket integration not enabled"
        )

    return _simulated_integration_response(
        "Shiprocket", "create shipment", {"order_id": shipment.order_id}
    )


@router.get("/integrations/shiprocket/track/{awb}")
async def track_shiprocket_shipment(
    awb: str, current_user: dict = Depends(get_current_user)
):
    """Track a shipment by AWB number"""
    config = _get_integration_config("shiprocket")

    if not config.get("enabled"):
        raise HTTPException(
            status_code=400, detail="Shiprocket integration not enabled"
        )

    return _simulated_integration_response(
        "Shiprocket", "track shipment", {"awb": awb}
    )


@router.get("/integrations/shiprocket/rates")
async def get_shiprocket_rates(
    pickup_pincode: str,
    delivery_pincode: str,
    weight: float = 0.5,
    cod: bool = False,
    current_user: dict = Depends(get_current_user),
):
    """Get shipping rates from Shiprocket"""
    config = _get_integration_config("shiprocket")

    if not config.get("enabled"):
        raise HTTPException(
            status_code=400, detail="Shiprocket integration not enabled"
        )

    return _simulated_integration_response(
        "Shiprocket",
        "rate quote",
        {
            "pickup_pincode": pickup_pincode,
            "delivery_pincode": delivery_pincode,
            "weight": weight,
            "cod": cod,
            "rates": [],
        },
    )


# ============================================================================
# RAZORPAY INTEGRATION ENDPOINTS
# ============================================================================


@router.get("/integrations/razorpay")
async def get_razorpay_config(current_user: dict = Depends(get_current_user)):
    """Get Razorpay integration configuration"""
    config = _get_integration_config("razorpay")
    return {
        "type": "RAZORPAY",
        "name": "Razorpay",
        "description": "Payment gateway integration",
        "is_configured": bool(config.get("config")),
        "is_enabled": config.get("enabled", False),
        "config": {
            "key_id": (
                config.get("config", {}).get("key_id", "")[:10] + "***"
                if config.get("config", {}).get("key_id")
                else ""
            ),
        },
    }


@router.post("/integrations/razorpay")
async def set_razorpay_config(
    config: RazorpayConfig, current_user: dict = Depends(get_current_user)
):
    """Configure Razorpay integration"""
    if not any(
        role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    _save_integration_config(
        "razorpay", {"enabled": config.enabled, "config": config.model_dump()}
    )

    return {"message": "Razorpay configuration saved", "enabled": config.enabled}


@router.post("/integrations/razorpay/test")
async def test_razorpay_connection(current_user: dict = Depends(get_current_user)):
    """Test Razorpay API connection"""
    config = _get_integration_config("razorpay").get("config", {})

    if not config.get("key_id"):
        raise HTTPException(status_code=400, detail="Razorpay not configured")

    return _simulated_integration_response("Razorpay", "connection test")


# ============================================================================
# WHATSAPP INTEGRATION ENDPOINTS
# ============================================================================


@router.get("/integrations/whatsapp")
async def get_whatsapp_config(current_user: dict = Depends(get_current_user)):
    """Get WhatsApp Business integration configuration"""
    config = _get_integration_config("whatsapp")
    return {
        "type": "WHATSAPP",
        "name": "WhatsApp Business",
        "description": "Customer communication via WhatsApp",
        "is_configured": bool(config.get("config")),
        "is_enabled": config.get("enabled", False),
        "config": {
            "phone_number_id": config.get("config", {}).get("phone_number_id", ""),
            "business_id": config.get("config", {}).get("business_id", ""),
        },
    }


@router.post("/integrations/whatsapp")
async def set_whatsapp_config(
    config: WhatsappConfig, current_user: dict = Depends(get_current_user)
):
    """Configure WhatsApp Business integration"""
    if not any(
        role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    _save_integration_config(
        "whatsapp", {"enabled": config.enabled, "config": config.model_dump()}
    )

    return {"message": "WhatsApp configuration saved", "enabled": config.enabled}


@router.post("/integrations/whatsapp/test")
async def test_whatsapp_connection(current_user: dict = Depends(get_current_user)):
    """Test WhatsApp Business API connection"""
    config = _get_integration_config("whatsapp").get("config", {})

    if not config.get("api_key"):
        raise HTTPException(status_code=400, detail="WhatsApp not configured")

    return _simulated_integration_response("WhatsApp Business", "connection test")


# ============================================================================
# TALLY INTEGRATION ENDPOINTS
# ============================================================================


@router.get("/integrations/tally")
async def get_tally_config(current_user: dict = Depends(get_current_user)):
    """Get Tally ERP integration configuration"""
    config = _get_integration_config("tally")
    return {
        "type": "TALLY",
        "name": "Tally ERP",
        "description": "Accounting software integration",
        "is_configured": bool(config.get("config")),
        "is_enabled": config.get("enabled", False),
        "config": {
            "server_url": config.get("config", {}).get("server_url", ""),
            "company_name": config.get("config", {}).get("company_name", ""),
            "sync_interval": config.get("config", {}).get("sync_interval", 60),
        },
    }


@router.post("/integrations/tally")
async def set_tally_config(
    config: TallyConfig, current_user: dict = Depends(get_current_user)
):
    """Configure Tally ERP integration"""
    if not any(
        role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    _save_integration_config(
        "tally", {"enabled": config.enabled, "config": config.model_dump()}
    )

    return {"message": "Tally configuration saved", "enabled": config.enabled}


@router.post("/integrations/tally/test")
async def test_tally_connection(current_user: dict = Depends(get_current_user)):
    """Test Tally ERP connection"""
    config = _get_integration_config("tally").get("config", {})

    if not config.get("server_url"):
        raise HTTPException(status_code=400, detail="Tally not configured")

    return _simulated_integration_response("Tally ERP", "connection test")


# ============================================================================
# TALLY EXPORT — list / download / regenerate (Phase I-6)
# ============================================================================
# Per-store voucher XML download for the CA's RDP-Tally companies.
# The orchestrator (NEXUS daily 23:00 tick) writes one row per
# (export_date, store_id) tuple to the `tally_exports` Mongo collection;
# these endpoints surface that surface to the operator UI.

from fastapi import Query
from fastapi.responses import Response


def _tally_exports_collection():
    """Get the tally_exports collection or None when DB is offline."""
    try:
        from database.connection import get_db

        db = get_db()
        if db and db.is_connected:
            return db.get_collection("tally_exports")
    except Exception:
        pass
    return None


def _normalise_export_date(date_str: str) -> str:
    """Frontend posts YYYY-MM-DD; orchestrator writes ISO datetime at
    midnight UTC (with +00:00 suffix). Normalise to the SAME form used
    as the natural key in `tally_exports` so equality matches."""
    try:
        anchor = datetime.fromisoformat(date_str).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        # Force UTC tzinfo so isoformat produces '...+00:00' to match
        # what the orchestrator writes via datetime.now(timezone.utc).
        from datetime import timezone as _tz

        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=_tz.utc)
        return anchor.isoformat()
    except Exception:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")


def _scrub_export_row(row: dict) -> dict:
    """Drop _id and the heavy XML payload from list responses; keep the
    XML when streaming a single row for download."""
    if row is None:
        return {}
    out = {k: v for k, v in row.items() if k not in {"_id", "xml"}}
    return out


@router.get("/integrations/tally/exports")
async def list_tally_exports(
    date: str = Query(..., description="YYYY-MM-DD"),
    current_user: dict = Depends(get_current_user),
):
    """List all per-store Tally exports for the given date.
    Returns one row per store that had qualifying orders that day.
    """
    coll = _tally_exports_collection()
    if coll is None:
        return {"date": date, "exports": [], "total": 0}
    export_date_iso = _normalise_export_date(date)
    rows = []
    try:
        cursor = coll.find({"export_date": export_date_iso})
        for row in cursor:
            scrubbed = _scrub_export_row(row)
            # Surface a precomputed download URL so the frontend doesn't
            # have to know how to assemble it.
            sid = scrubbed.get("store_id")
            if sid:
                scrubbed["download_url"] = (
                    f"/api/v1/admin/integrations/tally/voucher.xml"
                    f"?date={date}&store_id={sid}"
                )
            rows.append(scrubbed)
    except Exception:
        pass
    rows.sort(key=lambda r: (r.get("store_code") or "", r.get("store_name") or ""))
    return {"date": date, "exports": rows, "total": len(rows)}


@router.get("/integrations/tally/voucher.xml")
async def download_tally_voucher_xml(
    date: str = Query(..., description="YYYY-MM-DD"),
    store_id: str = Query(..., description="Active store_id"),
    current_user: dict = Depends(get_current_user),
):
    """Stream the raw Tally voucher XML for one (date, store) tuple.
    Filename gets `_UNBALANCED` suffix if the row failed validation
    so the CA never accidentally imports an unreconciled voucher.
    """
    coll = _tally_exports_collection()
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    export_date_iso = _normalise_export_date(date)
    row = coll.find_one({"export_date": export_date_iso, "store_id": store_id})
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No Tally export found for date={date}, store_id={store_id}",
        )
    xml = row.get("xml", "")
    code = row.get("store_code") or store_id
    suffix = "" if row.get("balanced", True) else "_UNBALANCED"
    filename = f"{code}_{date}{suffix}.xml"
    return Response(
        content=xml,
        media_type="application/xml",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Tally-Balanced": "1" if row.get("balanced", True) else "0",
            "X-Tally-Voucher-Count": str(row.get("voucher_count", 0)),
        },
    )


class RegenerateTallyExport(BaseModel):
    date: str = Field(..., description="YYYY-MM-DD")
    store_id: Optional[str] = Field(
        None, description="Single store to regenerate; omit to run all active stores"
    )


@router.post("/integrations/tally/regenerate")
async def regenerate_tally_export(
    payload: RegenerateTallyExport,
    current_user: dict = Depends(get_current_user),
):
    """Manually re-run the Tally export for one store or the whole chain.
    Idempotent — overwrites any existing row for the same (date, store_id).
    SUPERADMIN only.
    """
    if "SUPERADMIN" not in (current_user.get("roles") or []):
        raise HTTPException(
            status_code=403, detail="SUPERADMIN required to regenerate Tally exports"
        )

    # Validate date format up front so the error message points at the input.
    try:
        anchor = datetime.fromisoformat(payload.date).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    except Exception:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")

    # Lazy-import the registry so the router file stays cheap to load
    # when the agents subsystem isn't initialised (tests, scripts, etc).
    try:
        from agents.registry import get_agent

        nexus = get_agent("nexus")
        if nexus is None:
            raise HTTPException(status_code=503, detail="NEXUS agent not registered")
        result = await nexus._build_tally_export(
            target_date=anchor, store_id=payload.store_id
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Tally regenerate failed: {e}")

    return {
        "ok": getattr(result, "ok", False),
        "items_synced": getattr(result, "items_synced", 0),
        "notes": getattr(result, "notes", ""),
        "error": getattr(result, "error", None),
        "date": payload.date,
        "store_id": payload.store_id,
    }


# ============================================================================
# SMS GATEWAY ENDPOINTS
# ============================================================================


@router.get("/integrations/sms")
async def get_sms_config(current_user: dict = Depends(get_current_user)):
    """Get SMS Gateway configuration"""
    config = _get_integration_config("sms")
    return {
        "type": "SMS",
        "name": "SMS Gateway",
        "description": "Transactional SMS service",
        "is_configured": bool(config.get("config")),
        "is_enabled": config.get("enabled", False),
        "config": {
            "provider": config.get("config", {}).get("provider", ""),
            "sender_id": config.get("config", {}).get("sender_id", ""),
        },
    }


@router.post("/integrations/sms")
async def set_sms_config(
    config: SmsConfig, current_user: dict = Depends(get_current_user)
):
    """Configure SMS Gateway"""
    if not any(
        role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    _save_integration_config(
        "sms", {"enabled": config.enabled, "config": config.model_dump()}
    )

    return {"message": "SMS Gateway configuration saved", "enabled": config.enabled}


# ============================================================================
# INTEGRATIONS OVERVIEW
# ============================================================================


@router.get("/integrations")
async def list_all_integrations(current_user: dict = Depends(get_current_user)):
    """List all available integrations and their status"""
    integrations = []

    integration_meta = {
        "shopify": {
            "name": "Shopify",
            "description": "Shopify POS + stock mirror (inventory sync only)",
            "category": "Inventory",
        },
        "shiprocket": {
            "name": "Shiprocket",
            "description": "Shipping and logistics",
            "category": "Logistics",
        },
        "razorpay": {
            "name": "Razorpay",
            "description": "Payment gateway",
            "category": "Payments",
        },
        "whatsapp": {
            "name": "WhatsApp Business",
            "description": "Customer notifications",
            "category": "Communications",
        },
        "tally": {
            "name": "Tally ERP",
            "description": "Accounting sync",
            "category": "Accounting",
        },
        "sms": {
            "name": "SMS Gateway",
            "description": "Transactional SMS",
            "category": "Communications",
        },
    }

    for key, meta in integration_meta.items():
        config = _get_integration_config(key)
        integrations.append(
            {
                "type": key.upper(),
                "name": meta["name"],
                "description": meta["description"],
                "category": meta["category"],
                "is_configured": bool(config.get("config")),
                "is_enabled": config.get("enabled", False),
            }
        )

    return {"integrations": integrations}
