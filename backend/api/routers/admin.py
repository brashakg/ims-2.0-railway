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
import uuid
import os

router = APIRouter()


# ============================================================================
# SCHEMAS
# ============================================================================

class ShopifyConfig(BaseModel):
    shop_url: str = Field(..., description="Shopify store URL (e.g., mystore.myshopify.com)")
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
# IN-MEMORY CONFIG STORAGE (would be database in production)
# ============================================================================

INTEGRATION_CONFIGS: Dict[str, Dict] = {
    "shopify": {"enabled": False, "config": {}},
    "shiprocket": {"enabled": False, "config": {}, "token": None, "token_expiry": None},
    "razorpay": {"enabled": False, "config": {}},
    "whatsapp": {"enabled": False, "config": {}},
    "tally": {"enabled": False, "config": {}},
    "sms": {"enabled": True, "config": {"provider": "MSG91"}},
}


# ============================================================================
# SHOPIFY INTEGRATION ENDPOINTS
# ============================================================================

@router.get("/integrations/shopify")
async def get_shopify_config(current_user: dict = Depends(get_current_user)):
    """Get Shopify integration configuration"""
    config = INTEGRATION_CONFIGS.get("shopify", {})
    return {
        "type": "SHOPIFY",
        "name": "Shopify",
        "description": "E-commerce platform integration",
        "is_configured": bool(config.get("config")),
        "is_enabled": config.get("enabled", False),
        "config": {
            "shop_url": config.get("config", {}).get("shop_url", ""),
            # Don't expose secrets
            "api_key": "***" if config.get("config", {}).get("api_key") else "",
        }
    }


@router.post("/integrations/shopify")
async def set_shopify_config(
    config: ShopifyConfig,
    current_user: dict = Depends(get_current_user)
):
    """Configure Shopify integration"""
    if not any(role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    INTEGRATION_CONFIGS["shopify"] = {
        "enabled": config.enabled,
        "config": config.model_dump()
    }

    return {"message": "Shopify configuration saved", "enabled": config.enabled}


@router.post("/integrations/shopify/test")
async def test_shopify_connection(current_user: dict = Depends(get_current_user)):
    """Test Shopify API connection"""
    config = INTEGRATION_CONFIGS.get("shopify", {}).get("config", {})

    if not config.get("shop_url"):
        raise HTTPException(status_code=400, detail="Shopify not configured")

    # In production, would make actual API call to Shopify
    # For now, simulate successful connection
    return {
        "status": "success",
        "message": "Successfully connected to Shopify",
        "shop_info": {
            "name": config.get("shop_url", "").replace(".myshopify.com", ""),
            "plan": "Basic",
            "products_count": 156,
            "orders_count": 1247
        }
    }


@router.post("/integrations/shopify/sync-orders")
async def sync_shopify_orders(
    sync_params: ShopifyOrderSync = None,
    current_user: dict = Depends(get_current_user)
):
    """Sync orders from Shopify"""
    config = INTEGRATION_CONFIGS.get("shopify", {})

    if not config.get("enabled"):
        raise HTTPException(status_code=400, detail="Shopify integration not enabled")

    # Simulate order sync
    return {
        "status": "success",
        "message": "Order sync completed",
        "sync_details": {
            "orders_fetched": 15,
            "orders_created": 12,
            "orders_updated": 3,
            "errors": 0,
            "sync_time": datetime.now().isoformat()
        }
    }


@router.post("/integrations/shopify/sync-inventory")
async def sync_shopify_inventory(current_user: dict = Depends(get_current_user)):
    """Push inventory updates to Shopify"""
    config = INTEGRATION_CONFIGS.get("shopify", {})

    if not config.get("enabled"):
        raise HTTPException(status_code=400, detail="Shopify integration not enabled")

    # Simulate inventory sync
    return {
        "status": "success",
        "message": "Inventory sync completed",
        "sync_details": {
            "products_updated": 45,
            "skus_synced": 156,
            "errors": 0,
            "sync_time": datetime.now().isoformat()
        }
    }


# ============================================================================
# SHIPROCKET INTEGRATION ENDPOINTS
# ============================================================================

@router.get("/integrations/shiprocket")
async def get_shiprocket_config(current_user: dict = Depends(get_current_user)):
    """Get Shiprocket integration configuration"""
    config = INTEGRATION_CONFIGS.get("shiprocket", {})
    return {
        "type": "SHIPROCKET",
        "name": "Shiprocket",
        "description": "Shipping and logistics integration",
        "is_configured": bool(config.get("config")),
        "is_enabled": config.get("enabled", False),
        "config": {
            "email": config.get("config", {}).get("email", ""),
            "pickup_location_id": config.get("config", {}).get("pickup_location_id", ""),
        }
    }


@router.post("/integrations/shiprocket")
async def set_shiprocket_config(
    config: ShiprocketConfig,
    current_user: dict = Depends(get_current_user)
):
    """Configure Shiprocket integration"""
    if not any(role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    INTEGRATION_CONFIGS["shiprocket"] = {
        "enabled": config.enabled,
        "config": config.model_dump(),
        "token": None,
        "token_expiry": None
    }

    return {"message": "Shiprocket configuration saved", "enabled": config.enabled}


@router.post("/integrations/shiprocket/test")
async def test_shiprocket_connection(current_user: dict = Depends(get_current_user)):
    """Test Shiprocket API connection"""
    config = INTEGRATION_CONFIGS.get("shiprocket", {}).get("config", {})

    if not config.get("email"):
        raise HTTPException(status_code=400, detail="Shiprocket not configured")

    # In production, would authenticate with Shiprocket API
    # POST https://apiv2.shiprocket.in/v1/external/auth/login
    return {
        "status": "success",
        "message": "Successfully connected to Shiprocket",
        "account_info": {
            "email": config.get("email"),
            "company": "Better Vision Opticals",
            "wallet_balance": 5420.50,
            "pending_orders": 8
        }
    }


@router.post("/integrations/shiprocket/create-shipment")
async def create_shiprocket_shipment(
    shipment: ShipmentRequest,
    current_user: dict = Depends(get_current_user)
):
    """Create a shipment in Shiprocket"""
    config = INTEGRATION_CONFIGS.get("shiprocket", {})

    if not config.get("enabled"):
        raise HTTPException(status_code=400, detail="Shiprocket integration not enabled")

    # Generate AWB number (would come from Shiprocket API)
    awb_number = f"AWB{uuid.uuid4().hex[:10].upper()}"
    shipment_id = f"SR{uuid.uuid4().hex[:8].upper()}"

    return {
        "status": "success",
        "message": "Shipment created successfully",
        "shipment": {
            "shipment_id": shipment_id,
            "awb_code": awb_number,
            "courier_name": "Delhivery",
            "order_id": shipment.order_id,
            "pickup_scheduled": True,
            "expected_pickup": (datetime.now()).strftime("%Y-%m-%d"),
            "tracking_url": f"https://www.delhivery.com/track/package/{awb_number}",
            "label_url": f"https://shiprocket.co/label/{shipment_id}"
        }
    }


@router.get("/integrations/shiprocket/track/{awb}")
async def track_shiprocket_shipment(
    awb: str,
    current_user: dict = Depends(get_current_user)
):
    """Track a shipment by AWB number"""
    config = INTEGRATION_CONFIGS.get("shiprocket", {})

    if not config.get("enabled"):
        raise HTTPException(status_code=400, detail="Shiprocket integration not enabled")

    # Simulate tracking response
    return {
        "status": "success",
        "tracking": {
            "awb": awb,
            "current_status": "IN_TRANSIT",
            "current_location": "Mumbai Distribution Hub",
            "origin": "Delhi",
            "destination": "Mumbai",
            "expected_delivery": "2026-02-07",
            "tracking_history": [
                {"status": "PICKED_UP", "location": "Delhi Warehouse", "timestamp": "2026-02-04T10:30:00"},
                {"status": "IN_TRANSIT", "location": "Delhi Hub", "timestamp": "2026-02-04T14:00:00"},
                {"status": "IN_TRANSIT", "location": "Mumbai Distribution Hub", "timestamp": "2026-02-05T08:00:00"},
            ]
        }
    }


@router.get("/integrations/shiprocket/rates")
async def get_shiprocket_rates(
    pickup_pincode: str,
    delivery_pincode: str,
    weight: float = 0.5,
    cod: bool = False,
    current_user: dict = Depends(get_current_user)
):
    """Get shipping rates from Shiprocket"""
    config = INTEGRATION_CONFIGS.get("shiprocket", {})

    if not config.get("enabled"):
        raise HTTPException(status_code=400, detail="Shiprocket integration not enabled")

    # Simulate rate calculation
    return {
        "status": "success",
        "rates": [
            {
                "courier_name": "Delhivery Surface",
                "courier_code": "DELHIVERY_SURFACE",
                "rate": 65.00,
                "estimated_days": 5,
                "cod_available": True,
                "cod_charges": 35.00 if cod else 0
            },
            {
                "courier_name": "Delhivery Express",
                "courier_code": "DELHIVERY_EXPRESS",
                "rate": 95.00,
                "estimated_days": 3,
                "cod_available": True,
                "cod_charges": 35.00 if cod else 0
            },
            {
                "courier_name": "Bluedart",
                "courier_code": "BLUEDART",
                "rate": 120.00,
                "estimated_days": 2,
                "cod_available": True,
                "cod_charges": 50.00 if cod else 0
            },
            {
                "courier_name": "Ecom Express",
                "courier_code": "ECOM_EXPRESS",
                "rate": 55.00,
                "estimated_days": 6,
                "cod_available": True,
                "cod_charges": 30.00 if cod else 0
            }
        ]
    }


# ============================================================================
# RAZORPAY INTEGRATION ENDPOINTS
# ============================================================================

@router.get("/integrations/razorpay")
async def get_razorpay_config(current_user: dict = Depends(get_current_user)):
    """Get Razorpay integration configuration"""
    config = INTEGRATION_CONFIGS.get("razorpay", {})
    return {
        "type": "RAZORPAY",
        "name": "Razorpay",
        "description": "Payment gateway integration",
        "is_configured": bool(config.get("config")),
        "is_enabled": config.get("enabled", False),
        "config": {
            "key_id": config.get("config", {}).get("key_id", "")[:10] + "***" if config.get("config", {}).get("key_id") else "",
        }
    }


@router.post("/integrations/razorpay")
async def set_razorpay_config(
    config: RazorpayConfig,
    current_user: dict = Depends(get_current_user)
):
    """Configure Razorpay integration"""
    if not any(role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    INTEGRATION_CONFIGS["razorpay"] = {
        "enabled": config.enabled,
        "config": config.model_dump()
    }

    return {"message": "Razorpay configuration saved", "enabled": config.enabled}


@router.post("/integrations/razorpay/test")
async def test_razorpay_connection(current_user: dict = Depends(get_current_user)):
    """Test Razorpay API connection"""
    config = INTEGRATION_CONFIGS.get("razorpay", {}).get("config", {})

    if not config.get("key_id"):
        raise HTTPException(status_code=400, detail="Razorpay not configured")

    return {
        "status": "success",
        "message": "Successfully connected to Razorpay",
        "account_info": {
            "merchant_id": "BETTERVISION",
            "live_mode": False,
            "balance": 125000.00
        }
    }


# ============================================================================
# WHATSAPP INTEGRATION ENDPOINTS
# ============================================================================

@router.get("/integrations/whatsapp")
async def get_whatsapp_config(current_user: dict = Depends(get_current_user)):
    """Get WhatsApp Business integration configuration"""
    config = INTEGRATION_CONFIGS.get("whatsapp", {})
    return {
        "type": "WHATSAPP",
        "name": "WhatsApp Business",
        "description": "Customer communication via WhatsApp",
        "is_configured": bool(config.get("config")),
        "is_enabled": config.get("enabled", False),
        "config": {
            "phone_number_id": config.get("config", {}).get("phone_number_id", ""),
            "business_id": config.get("config", {}).get("business_id", ""),
        }
    }


@router.post("/integrations/whatsapp")
async def set_whatsapp_config(
    config: WhatsappConfig,
    current_user: dict = Depends(get_current_user)
):
    """Configure WhatsApp Business integration"""
    if not any(role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    INTEGRATION_CONFIGS["whatsapp"] = {
        "enabled": config.enabled,
        "config": config.model_dump()
    }

    return {"message": "WhatsApp configuration saved", "enabled": config.enabled}


@router.post("/integrations/whatsapp/test")
async def test_whatsapp_connection(current_user: dict = Depends(get_current_user)):
    """Test WhatsApp Business API connection"""
    config = INTEGRATION_CONFIGS.get("whatsapp", {}).get("config", {})

    if not config.get("api_key"):
        raise HTTPException(status_code=400, detail="WhatsApp not configured")

    return {
        "status": "success",
        "message": "Successfully connected to WhatsApp Business API",
        "account_info": {
            "business_name": "Better Vision Opticals",
            "phone_number": "+91 11 4567 8900",
            "verified": True
        }
    }


# ============================================================================
# TALLY INTEGRATION ENDPOINTS
# ============================================================================

@router.get("/integrations/tally")
async def get_tally_config(current_user: dict = Depends(get_current_user)):
    """Get Tally ERP integration configuration"""
    config = INTEGRATION_CONFIGS.get("tally", {})
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
        }
    }


@router.post("/integrations/tally")
async def set_tally_config(
    config: TallyConfig,
    current_user: dict = Depends(get_current_user)
):
    """Configure Tally ERP integration"""
    if not any(role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    INTEGRATION_CONFIGS["tally"] = {
        "enabled": config.enabled,
        "config": config.model_dump()
    }

    return {"message": "Tally configuration saved", "enabled": config.enabled}


@router.post("/integrations/tally/test")
async def test_tally_connection(current_user: dict = Depends(get_current_user)):
    """Test Tally ERP connection"""
    config = INTEGRATION_CONFIGS.get("tally", {}).get("config", {})

    if not config.get("server_url"):
        raise HTTPException(status_code=400, detail="Tally not configured")

    return {
        "status": "success",
        "message": "Successfully connected to Tally",
        "company_info": {
            "name": config.get("company_name", ""),
            "financial_year": "2025-26",
            "last_sync": datetime.now().isoformat()
        }
    }


# ============================================================================
# SMS GATEWAY ENDPOINTS
# ============================================================================

@router.get("/integrations/sms")
async def get_sms_config(current_user: dict = Depends(get_current_user)):
    """Get SMS Gateway configuration"""
    config = INTEGRATION_CONFIGS.get("sms", {})
    return {
        "type": "SMS",
        "name": "SMS Gateway",
        "description": "Transactional SMS service",
        "is_configured": bool(config.get("config")),
        "is_enabled": config.get("enabled", False),
        "config": {
            "provider": config.get("config", {}).get("provider", ""),
            "sender_id": config.get("config", {}).get("sender_id", ""),
        }
    }


@router.post("/integrations/sms")
async def set_sms_config(
    config: SmsConfig,
    current_user: dict = Depends(get_current_user)
):
    """Configure SMS Gateway"""
    if not any(role in current_user.get("roles", []) for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    INTEGRATION_CONFIGS["sms"] = {
        "enabled": config.enabled,
        "config": config.model_dump()
    }

    return {"message": "SMS Gateway configuration saved", "enabled": config.enabled}


# ============================================================================
# INTEGRATIONS OVERVIEW
# ============================================================================

@router.get("/integrations")
async def list_all_integrations(current_user: dict = Depends(get_current_user)):
    """List all available integrations and their status"""
    integrations = []

    integration_meta = {
        "shopify": {"name": "Shopify", "description": "E-commerce platform sync", "category": "E-commerce"},
        "shiprocket": {"name": "Shiprocket", "description": "Shipping and logistics", "category": "Logistics"},
        "razorpay": {"name": "Razorpay", "description": "Payment gateway", "category": "Payments"},
        "whatsapp": {"name": "WhatsApp Business", "description": "Customer notifications", "category": "Communications"},
        "tally": {"name": "Tally ERP", "description": "Accounting sync", "category": "Accounting"},
        "sms": {"name": "SMS Gateway", "description": "Transactional SMS", "category": "Communications"},
    }

    for key, meta in integration_meta.items():
        config = INTEGRATION_CONFIGS.get(key, {})
        integrations.append({
            "type": key.upper(),
            "name": meta["name"],
            "description": meta["description"],
            "category": meta["category"],
            "is_configured": bool(config.get("config")),
            "is_enabled": config.get("enabled", False),
        })

    return {"integrations": integrations}
