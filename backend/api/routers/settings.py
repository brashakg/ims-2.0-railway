"""
IMS 2.0 - Settings Router
==========================
Comprehensive settings management for all user roles.
- Profile/Account settings
- Business settings (SUPERADMIN/ADMIN)
- Tax & Invoice settings
- Notification templates
- Integration configurations
- Audit logs
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from .auth import get_current_user

router = APIRouter()


# ============================================================================
# SCHEMAS
# ============================================================================

class DiscountSettings(BaseModel):
    role: str
    category: str
    max_discount: float


class IntegrationConfig(BaseModel):
    integration_type: str  # SHOPIFY, TALLY, SHIPROCKET, WHATSAPP, RAZORPAY
    enabled: bool
    config: Dict


class ProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8)


class BusinessSettings(BaseModel):
    company_name: str = "Better Vision Opticals"
    company_short_name: str = "BVO"
    tagline: str = "See Better, Live Better"
    logo_url: Optional[str] = None
    favicon_url: Optional[str] = None
    primary_color: str = "#ba8659"
    secondary_color: str = "#a67547"
    support_email: str = "support@bettervision.in"
    support_phone: str = "+91 11 4567 8900"
    website: str = "https://bettervision.in"
    address: str = ""
    terms_url: Optional[str] = None
    privacy_url: Optional[str] = None


class TaxSettings(BaseModel):
    gst_enabled: bool = True
    company_gstin: str = ""
    default_gst_rate: float = 18.0
    hsn_validation: bool = True
    e_invoice_enabled: bool = False
    e_invoice_username: Optional[str] = None
    e_way_bill_enabled: bool = False
    e_way_bill_threshold: float = 50000.0
    tds_enabled: bool = False
    tds_rate: float = 0.0


class InvoiceSettings(BaseModel):
    invoice_prefix: str = "INV"
    invoice_start_number: int = 1
    current_invoice_number: int = 1
    financial_year: str = "2024-25"
    show_logo_on_invoice: bool = True
    show_terms_on_invoice: bool = True
    default_terms: str = "Goods once sold will not be returned or exchanged."
    default_warranty_days: int = 365
    show_qr_code: bool = True
    digital_signature_enabled: bool = False


class NotificationTemplate(BaseModel):
    template_id: str
    template_type: str  # SMS, EMAIL, WHATSAPP
    trigger_event: str  # ORDER_CREATED, ORDER_READY, PAYMENT_RECEIVED, etc.
    is_enabled: bool = True
    subject: Optional[str] = None  # For email
    content: str
    variables: List[str] = []  # Available variables like {customer_name}, {order_id}


class PrinterSettings(BaseModel):
    receipt_printer_name: Optional[str] = None
    receipt_printer_width: int = 80  # mm
    label_printer_name: Optional[str] = None
    label_size: str = "50x25"  # mm
    auto_print_receipt: bool = True
    auto_print_job_card: bool = True
    copies_per_print: int = 1


class AuditLogEntry(BaseModel):
    id: str
    timestamp: datetime
    user_id: str
    user_name: str
    action: str  # CREATE, UPDATE, DELETE, LOGIN, LOGOUT, etc.
    entity_type: str  # USER, ORDER, PRODUCT, STORE, etc.
    entity_id: Optional[str] = None
    changes: Optional[Dict] = None
    ip_address: Optional[str] = None


# ============================================================================
# MOCK DATA
# ============================================================================

# Business settings (would come from database in production)
MOCK_BUSINESS_SETTINGS = BusinessSettings(
    company_name="Better Vision Opticals",
    company_short_name="BVO",
    tagline="See Better, Live Better",
    logo_url="/images/logo.png",
    primary_color="#ba8659",
    secondary_color="#a67547",
    support_email="support@bettervision.in",
    support_phone="+91 11 4567 8900",
    website="https://bettervision.in",
    address="123 Vision Street, Connaught Place, New Delhi - 110001",
)

MOCK_TAX_SETTINGS = TaxSettings(
    gst_enabled=True,
    company_gstin="07AABCT1234Q1ZP",
    default_gst_rate=18.0,
    hsn_validation=True,
    e_invoice_enabled=False,
    e_way_bill_enabled=False,
    e_way_bill_threshold=50000.0,
)

MOCK_INVOICE_SETTINGS = InvoiceSettings(
    invoice_prefix="BVO",
    invoice_start_number=1,
    current_invoice_number=1542,
    financial_year="2024-25",
    show_logo_on_invoice=True,
    show_terms_on_invoice=True,
    default_terms="1. Goods once sold will not be returned or exchanged.\n2. Warranty valid only with original invoice.\n3. Lens warranty does not cover scratches.",
    default_warranty_days=365,
    show_qr_code=True,
)

MOCK_NOTIFICATION_TEMPLATES = [
    NotificationTemplate(
        template_id="order_created_sms",
        template_type="SMS",
        trigger_event="ORDER_CREATED",
        is_enabled=True,
        content="Dear {customer_name}, your order #{order_id} has been placed at Better Vision. Total: Rs.{total}. Expected delivery: {delivery_date}.",
        variables=["customer_name", "order_id", "total", "delivery_date"]
    ),
    NotificationTemplate(
        template_id="order_ready_sms",
        template_type="SMS",
        trigger_event="ORDER_READY",
        is_enabled=True,
        content="Dear {customer_name}, your order #{order_id} is ready for pickup at {store_name}. Please visit with your invoice.",
        variables=["customer_name", "order_id", "store_name"]
    ),
    NotificationTemplate(
        template_id="order_ready_whatsapp",
        template_type="WHATSAPP",
        trigger_event="ORDER_READY",
        is_enabled=True,
        content="Hello {customer_name}! 👓\n\nGreat news! Your eyewear order #{order_id} is ready for pickup.\n\n📍 Store: {store_name}\n🕐 Store Hours: {store_hours}\n\nPlease bring your invoice for collection.\n\nThank you for choosing Better Vision! 🙏",
        variables=["customer_name", "order_id", "store_name", "store_hours"]
    ),
    NotificationTemplate(
        template_id="payment_received_sms",
        template_type="SMS",
        trigger_event="PAYMENT_RECEIVED",
        is_enabled=True,
        content="Dear {customer_name}, payment of Rs.{amount} received for order #{order_id}. Balance: Rs.{balance}. Thank you!",
        variables=["customer_name", "amount", "order_id", "balance"]
    ),
    NotificationTemplate(
        template_id="eye_test_reminder_sms",
        template_type="SMS",
        trigger_event="EYE_TEST_REMINDER",
        is_enabled=True,
        content="Dear {customer_name}, it's been a year since your last eye test. Book your appointment at Better Vision. Call: {store_phone}",
        variables=["customer_name", "store_phone"]
    ),
    NotificationTemplate(
        template_id="birthday_wish_whatsapp",
        template_type="WHATSAPP",
        trigger_event="CUSTOMER_BIRTHDAY",
        is_enabled=False,
        content="🎂 Happy Birthday {customer_name}! 🎉\n\nWishing you clear vision and a wonderful year ahead!\n\nEnjoy 10% off on your next purchase at Better Vision.\nCode: BDAY{customer_id}\n\nValid for 7 days.",
        variables=["customer_name", "customer_id"]
    ),
]

MOCK_AUDIT_LOGS = [
    AuditLogEntry(
        id="log-001",
        timestamp=datetime.now(),
        user_id="user-002",
        user_name="Avinash Kumar (CEO)",
        action="LOGIN",
        entity_type="SESSION",
        entity_id=None,
        ip_address="192.168.1.100"
    ),
    AuditLogEntry(
        id="log-002",
        timestamp=datetime.now(),
        user_id="user-007",
        user_name="Rajesh Kumar",
        action="CREATE",
        entity_type="ORDER",
        entity_id="ORD-2024-001542",
        changes={"status": "DRAFT", "total": 12500},
        ip_address="192.168.1.101"
    ),
    AuditLogEntry(
        id="log-003",
        timestamp=datetime.now(),
        user_id="user-015",
        user_name="Rohit Malhotra",
        action="UPDATE",
        entity_type="PRODUCT",
        entity_id="PROD-FR-001",
        changes={"mrp": {"old": 5000, "new": 5500}},
        ip_address="192.168.1.102"
    ),
]

MOCK_PRINTER_SETTINGS = PrinterSettings(
    receipt_printer_name="EPSON TM-T88V",
    receipt_printer_width=80,
    label_printer_name="Zebra ZD420",
    label_size="50x25",
    auto_print_receipt=True,
    auto_print_job_card=True,
    copies_per_print=1,
)


# ============================================================================
# PROFILE ENDPOINTS
# ============================================================================

@router.get("/profile")
async def get_profile(current_user: dict = Depends(get_current_user)):
    """Get current user's profile"""
    return {
        "user_id": current_user["user_id"],
        "username": current_user["username"],
        "roles": current_user["roles"],
        "store_ids": current_user["store_ids"],
        "active_store_id": current_user.get("active_store_id"),
    }


@router.put("/profile")
async def update_profile(
    profile: ProfileUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Update current user's profile"""
    # In production, update database
    return {
        "message": "Profile updated successfully",
        "updated_fields": profile.model_dump(exclude_none=True)
    }


@router.post("/profile/change-password")
async def change_password(
    passwords: PasswordChange,
    current_user: dict = Depends(get_current_user)
):
    """Change current user's password"""
    # In production, verify current password and update
    return {"message": "Password changed successfully"}


@router.get("/profile/preferences")
async def get_preferences(current_user: dict = Depends(get_current_user)):
    """Get user's display preferences"""
    return {
        "theme": "light",
        "language": "en",
        "currency": "INR",
        "date_format": "DD/MM/YYYY",
        "notifications_enabled": True,
        "email_notifications": True,
        "sms_notifications": True,
        "dashboard_widgets": ["sales", "orders", "tasks", "inventory"],
    }


@router.put("/profile/preferences")
async def update_preferences(
    preferences: Dict,
    current_user: dict = Depends(get_current_user)
):
    """Update user's display preferences"""
    return {"message": "Preferences updated", "preferences": preferences}


# ============================================================================
# BUSINESS SETTINGS ENDPOINTS
# ============================================================================

@router.get("/business")
async def get_business_settings(current_user: dict = Depends(get_current_user)):
    """Get business/company settings"""
    return MOCK_BUSINESS_SETTINGS.model_dump()


@router.put("/business")
async def update_business_settings(
    settings: BusinessSettings,
    current_user: dict = Depends(get_current_user)
):
    """Update business settings (SUPERADMIN/ADMIN only)"""
    if not any(role in current_user["roles"] for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    # In production, update database
    return {"message": "Business settings updated", "settings": settings.model_dump()}


@router.post("/business/logo")
async def upload_logo(current_user: dict = Depends(get_current_user)):
    """Upload company logo (placeholder for file upload)"""
    if not any(role in current_user["roles"] for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return {"message": "Logo uploaded", "url": "/images/logo.png"}


# ============================================================================
# TAX SETTINGS ENDPOINTS
# ============================================================================

@router.get("/tax")
async def get_tax_settings(current_user: dict = Depends(get_current_user)):
    """Get tax and compliance settings"""
    return MOCK_TAX_SETTINGS.model_dump()


@router.put("/tax")
async def update_tax_settings(
    settings: TaxSettings,
    current_user: dict = Depends(get_current_user)
):
    """Update tax settings (SUPERADMIN/ADMIN only)"""
    if not any(role in current_user["roles"] for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return {"message": "Tax settings updated", "settings": settings.model_dump()}


# ============================================================================
# INVOICE SETTINGS ENDPOINTS
# ============================================================================

@router.get("/invoice")
async def get_invoice_settings(current_user: dict = Depends(get_current_user)):
    """Get invoice settings"""
    return MOCK_INVOICE_SETTINGS.model_dump()


@router.put("/invoice")
async def update_invoice_settings(
    settings: InvoiceSettings,
    current_user: dict = Depends(get_current_user)
):
    """Update invoice settings (SUPERADMIN/ADMIN only)"""
    if not any(role in current_user["roles"] for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return {"message": "Invoice settings updated", "settings": settings.model_dump()}


# ============================================================================
# NOTIFICATION SETTINGS ENDPOINTS
# ============================================================================

@router.get("/notifications/templates")
async def get_notification_templates(current_user: dict = Depends(get_current_user)):
    """Get all notification templates"""
    return {"templates": [t.model_dump() for t in MOCK_NOTIFICATION_TEMPLATES]}


@router.get("/notifications/templates/{template_id}")
async def get_notification_template(
    template_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get specific notification template"""
    for t in MOCK_NOTIFICATION_TEMPLATES:
        if t.template_id == template_id:
            return t.model_dump()
    raise HTTPException(status_code=404, detail="Template not found")


@router.put("/notifications/templates/{template_id}")
async def update_notification_template(
    template_id: str,
    template: NotificationTemplate,
    current_user: dict = Depends(get_current_user)
):
    """Update notification template (SUPERADMIN/ADMIN only)"""
    if not any(role in current_user["roles"] for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return {"message": "Template updated", "template": template.model_dump()}


@router.post("/notifications/templates")
async def create_notification_template(
    template: NotificationTemplate,
    current_user: dict = Depends(get_current_user)
):
    """Create new notification template (SUPERADMIN/ADMIN only)"""
    if not any(role in current_user["roles"] for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return {"message": "Template created", "template": template.model_dump()}


@router.delete("/notifications/templates/{template_id}")
async def delete_notification_template(
    template_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Delete notification template (SUPERADMIN/ADMIN only)"""
    if not any(role in current_user["roles"] for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return {"message": "Template deleted"}


@router.post("/notifications/test")
async def test_notification(
    template_id: str,
    test_phone: Optional[str] = None,
    test_email: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """Send test notification"""
    return {"message": "Test notification sent", "template_id": template_id}


# ============================================================================
# PRINTER SETTINGS ENDPOINTS
# ============================================================================

@router.get("/printers")
async def get_printer_settings(current_user: dict = Depends(get_current_user)):
    """Get printer settings"""
    return MOCK_PRINTER_SETTINGS.model_dump()


@router.put("/printers")
async def update_printer_settings(
    settings: PrinterSettings,
    current_user: dict = Depends(get_current_user)
):
    """Update printer settings"""
    return {"message": "Printer settings updated", "settings": settings.model_dump()}


@router.get("/printers/available")
async def list_available_printers(current_user: dict = Depends(get_current_user)):
    """List available printers (detected on network)"""
    return {
        "printers": [
            {"name": "EPSON TM-T88V", "type": "RECEIPT", "status": "online"},
            {"name": "Zebra ZD420", "type": "LABEL", "status": "online"},
            {"name": "HP LaserJet Pro", "type": "A4", "status": "online"},
        ]
    }


# ============================================================================
# DISCOUNT RULES ENDPOINTS
# ============================================================================

@router.get("/discount-rules")
async def get_discount_rules(current_user: dict = Depends(get_current_user)):
    """Get all discount rules by role and tier"""
    return {
        "rules": {
            "SALES_STAFF": {"MASS": 5, "PREMIUM": 3, "LUXURY": 0},
            "SALES_CASHIER": {"MASS": 10, "PREMIUM": 5, "LUXURY": 3},
            "OPTOMETRIST": {"MASS": 5, "PREMIUM": 3, "LUXURY": 0},
            "WORKSHOP_STAFF": {"MASS": 0, "PREMIUM": 0, "LUXURY": 0},
            "STORE_MANAGER": {"MASS": 15, "PREMIUM": 10, "LUXURY": 5},
            "ACCOUNTANT": {"MASS": 10, "PREMIUM": 5, "LUXURY": 3},
            "AREA_MANAGER": {"MASS": 20, "PREMIUM": 15, "LUXURY": 10},
            "ADMIN": {"MASS": 100, "PREMIUM": 100, "LUXURY": 100},
            "SUPERADMIN": {"MASS": 100, "PREMIUM": 100, "LUXURY": 100},
        }
    }


@router.put("/discount-rules")
async def update_discount_rules(
    rules: Dict[str, Dict[str, int]],
    current_user: dict = Depends(get_current_user)
):
    """Update discount rules (SUPERADMIN/ADMIN only)"""
    if not any(role in current_user["roles"] for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return {"message": "Discount rules updated", "rules": rules}


@router.post("/discount-rules")
async def set_discount_rule(
    rule: DiscountSettings,
    current_user: dict = Depends(get_current_user)
):
    """Set individual discount rule"""
    if not any(role in current_user["roles"] for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return {"message": "Discount rule updated"}


# ============================================================================
# INTEGRATION ENDPOINTS
# ============================================================================

@router.get("/integrations")
async def list_integrations(current_user: dict = Depends(get_current_user)):
    """List all integration configurations"""
    return {
        "integrations": [
            {
                "type": "RAZORPAY",
                "name": "Razorpay",
                "description": "Online payment gateway",
                "is_configured": False,
                "is_enabled": False,
                "config": {}
            },
            {
                "type": "WHATSAPP",
                "name": "WhatsApp Business",
                "description": "Customer notifications via WhatsApp",
                "is_configured": False,
                "is_enabled": False,
                "config": {}
            },
            {
                "type": "TALLY",
                "name": "Tally ERP",
                "description": "Accounting synchronization",
                "is_configured": False,
                "is_enabled": False,
                "config": {}
            },
            {
                "type": "SHOPIFY",
                "name": "Shopify",
                "description": "E-commerce platform sync",
                "is_configured": False,
                "is_enabled": False,
                "config": {}
            },
            {
                "type": "SMS",
                "name": "SMS Gateway",
                "description": "SMS notifications",
                "is_configured": True,
                "is_enabled": True,
                "config": {"provider": "MSG91"}
            },
        ]
    }


@router.get("/integrations/{integration_type}")
async def get_integration(
    integration_type: str,
    current_user: dict = Depends(get_current_user)
):
    """Get specific integration configuration"""
    return {
        "type": integration_type.upper(),
        "is_configured": False,
        "is_enabled": False,
        "config": {}
    }


@router.put("/integrations/{integration_type}")
async def update_integration(
    integration_type: str,
    config: IntegrationConfig,
    current_user: dict = Depends(get_current_user)
):
    """Update integration configuration (SUPERADMIN/ADMIN only)"""
    if not any(role in current_user["roles"] for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return {"message": f"{integration_type} integration updated", "config": config.model_dump()}


@router.post("/integrations/{integration_type}/test")
async def test_integration(
    integration_type: str,
    current_user: dict = Depends(get_current_user)
):
    """Test integration connection"""
    return {"status": "success", "message": f"{integration_type} connection successful"}


# ============================================================================
# SYSTEM SETTINGS ENDPOINTS
# ============================================================================

@router.get("/system")
async def get_system_settings(current_user: dict = Depends(get_current_user)):
    """Get system settings"""
    return {
        "maintenance_mode": False,
        "allow_registrations": False,
        "session_timeout_minutes": 480,
        "max_login_attempts": 5,
        "password_min_length": 8,
        "require_2fa": False,
        "backup_enabled": True,
        "backup_frequency": "daily",
        "last_backup": "2024-02-04T00:00:00Z",
        "data_retention_days": 365,
    }


@router.put("/system")
async def update_system_settings(
    settings: Dict,
    current_user: dict = Depends(get_current_user)
):
    """Update system settings (SUPERADMIN only)"""
    if "SUPERADMIN" not in current_user["roles"]:
        raise HTTPException(status_code=403, detail="Superadmin access required")
    return {"message": "System settings updated", "settings": settings}


# ============================================================================
# AUDIT LOG ENDPOINTS
# ============================================================================

@router.get("/audit-logs")
async def get_audit_logs(
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    user_id: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    current_user: dict = Depends(get_current_user)
):
    """Get audit logs (SUPERADMIN/ADMIN only)"""
    if not any(role in current_user["roles"] for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    logs = MOCK_AUDIT_LOGS

    # Apply filters
    if entity_type:
        logs = [l for l in logs if l.entity_type == entity_type]
    if user_id:
        logs = [l for l in logs if l.user_id == user_id]
    if action:
        logs = [l for l in logs if l.action == action]

    return {
        "logs": [l.model_dump() for l in logs],
        "total": len(logs),
        "limit": limit,
        "offset": offset
    }


@router.get("/audit-logs/summary")
async def get_audit_summary(
    current_user: dict = Depends(get_current_user)
):
    """Get audit log summary for dashboard"""
    if not any(role in current_user["roles"] for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    return {
        "today": {
            "total_actions": 156,
            "logins": 24,
            "orders_created": 45,
            "products_updated": 12,
            "users_created": 2
        },
        "this_week": {
            "total_actions": 892,
            "top_users": [
                {"user_id": "user-007", "user_name": "Rajesh Kumar", "actions": 156},
                {"user_id": "user-008", "user_name": "Neha Gupta", "actions": 134},
            ]
        }
    }
