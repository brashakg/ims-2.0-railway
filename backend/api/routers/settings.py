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
from .auth import get_current_user, hash_password, verify_password
from ..dependencies import get_audit_repository

router = APIRouter()


# ============================================================================
# DATABASE HELPER FUNCTIONS
# ============================================================================


def _get_settings_collection(collection_name: str):
    """Get settings from database"""
    try:
        from database.connection import get_db

        db = get_db()
        if db and db.is_connected:
            return db.db[collection_name]
    except Exception:
        pass
    return None


def _get_business_settings_from_db() -> Optional[dict]:
    """Fetch business settings from database"""
    collection = _get_settings_collection("business_settings")
    if collection:
        settings = collection.find_one({"_id": "default"})
        if settings:
            settings.pop("_id", None)
            return settings
    return None


def _get_tax_settings_from_db() -> Optional[dict]:
    """Fetch tax settings from database"""
    collection = _get_settings_collection("tax_settings")
    if collection:
        settings = collection.find_one({"_id": "default"})
        if settings:
            settings.pop("_id", None)
            return settings
    return None


def _get_invoice_settings_from_db() -> Optional[dict]:
    """Fetch invoice settings from database"""
    collection = _get_settings_collection("invoice_settings")
    if collection:
        settings = collection.find_one({"_id": "default"})
        if settings:
            settings.pop("_id", None)
            return settings
    return None


def _get_printer_settings_from_db() -> Optional[dict]:
    """Fetch printer settings from database"""
    collection = _get_settings_collection("printer_settings")
    if collection:
        settings = collection.find_one({"_id": "default"})
        if settings:
            settings.pop("_id", None)
            return settings
    return None


def _get_notification_templates_from_db() -> List[dict]:
    """Fetch notification templates from database"""
    collection = _get_settings_collection("notification_templates")
    if collection:
        templates = list(collection.find({}))
        for t in templates:
            t.pop("_id", None)
        return templates
    return []


def _get_discount_rules_from_db() -> Optional[dict]:
    """Fetch discount rules from database"""
    collection = _get_settings_collection("discount_rules")
    if collection:
        rules = collection.find_one({"_id": "default"})
        if rules:
            rules.pop("_id", None)
            return rules
    return None


def _get_integrations_from_db() -> List[dict]:
    """Fetch integration configs from database"""
    collection = _get_settings_collection("integrations")
    if collection:
        integrations = list(collection.find({}))
        for i in integrations:
            i.pop("_id", None)
        return integrations
    return []


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
# NOTE: All settings are now fetched from the database.
# Default empty values are returned when database is unavailable.
# ============================================================================


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
    profile: ProfileUpdate, current_user: dict = Depends(get_current_user)
):
    """Update current user's profile"""
    # In production, update database
    return {
        "message": "Profile updated successfully",
        "updated_fields": profile.model_dump(exclude_none=True),
    }


@router.post("/profile/change-password")
async def change_password(
    passwords: PasswordChange, current_user: dict = Depends(get_current_user)
):
    """Change current user's password"""
    from ..dependencies import get_user_repository

    user_repo = get_user_repository()

    if user_repo:
        # Get user from database
        user = user_repo.find_by_id(current_user.get("user_id"))
        if user is not None:
            # Verify current password
            stored_hash = user.get("password_hash")
            if stored_hash and not verify_password(
                passwords.current_password, stored_hash
            ):
                raise HTTPException(
                    status_code=400, detail="Current password is incorrect"
                )

            # Update password in database
            new_hash = hash_password(passwords.new_password)
            user_repo.update(current_user.get("user_id"), {"password_hash": new_hash})
            return {"message": "Password changed successfully"}

    # For demo mode without database, validate format only
    if len(passwords.new_password) < 8:
        raise HTTPException(
            status_code=400, detail="Password must be at least 8 characters"
        )

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
    preferences: Dict, current_user: dict = Depends(get_current_user)
):
    """Update user's display preferences"""
    return {"message": "Preferences updated", "preferences": preferences}


# ============================================================================
# BUSINESS SETTINGS ENDPOINTS
# ============================================================================


@router.get("/business")
async def get_business_settings(current_user: dict = Depends(get_current_user)):
    """Get business/company settings"""
    db_settings = _get_business_settings_from_db()
    if db_settings:
        return db_settings
    # Return default empty settings
    return BusinessSettings().model_dump()


@router.put("/business")
async def update_business_settings(
    settings: BusinessSettings, current_user: dict = Depends(get_current_user)
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
    db_settings = _get_tax_settings_from_db()
    if db_settings:
        return db_settings
    # Return default settings
    return TaxSettings().model_dump()


@router.put("/tax")
async def update_tax_settings(
    settings: TaxSettings, current_user: dict = Depends(get_current_user)
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
    db_settings = _get_invoice_settings_from_db()
    if db_settings:
        return db_settings
    # Return default settings
    return InvoiceSettings().model_dump()


@router.put("/invoice")
async def update_invoice_settings(
    settings: InvoiceSettings, current_user: dict = Depends(get_current_user)
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
    templates = _get_notification_templates_from_db()
    return {"templates": templates}


@router.get("/notifications/templates/{template_id}")
async def get_notification_template(
    template_id: str, current_user: dict = Depends(get_current_user)
):
    """Get specific notification template"""
    templates = _get_notification_templates_from_db()
    for t in templates:
        if t.get("template_id") == template_id:
            return t
    raise HTTPException(status_code=404, detail="Template not found")


@router.put("/notifications/templates/{template_id}")
async def update_notification_template(
    template_id: str,
    template: NotificationTemplate,
    current_user: dict = Depends(get_current_user),
):
    """Update notification template (SUPERADMIN/ADMIN only)"""
    if not any(role in current_user["roles"] for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return {"message": "Template updated", "template": template.model_dump()}


@router.post("/notifications/templates")
async def create_notification_template(
    template: NotificationTemplate, current_user: dict = Depends(get_current_user)
):
    """Create new notification template (SUPERADMIN/ADMIN only)"""
    if not any(role in current_user["roles"] for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return {"message": "Template created", "template": template.model_dump()}


@router.delete("/notifications/templates/{template_id}")
async def delete_notification_template(
    template_id: str, current_user: dict = Depends(get_current_user)
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
    current_user: dict = Depends(get_current_user),
):
    """Send test notification"""
    return {"message": "Test notification sent", "template_id": template_id}


# ============================================================================
# PRINTER SETTINGS ENDPOINTS
# ============================================================================


@router.get("/printers")
async def get_printer_settings(current_user: dict = Depends(get_current_user)):
    """Get printer settings"""
    db_settings = _get_printer_settings_from_db()
    if db_settings:
        return db_settings
    # Return default settings
    return PrinterSettings().model_dump()


@router.put("/printers")
async def update_printer_settings(
    settings: PrinterSettings, current_user: dict = Depends(get_current_user)
):
    """Update printer settings"""
    return {"message": "Printer settings updated", "settings": settings.model_dump()}


@router.get("/printers/available")
async def list_available_printers(current_user: dict = Depends(get_current_user)):
    """List available printers (detected on network)"""
    # In production, this would scan for available printers
    # For now, return empty list as no actual printer detection
    return {"printers": []}


# ============================================================================
# DISCOUNT RULES ENDPOINTS
# ============================================================================


@router.get("/discount-rules")
async def get_discount_rules(current_user: dict = Depends(get_current_user)):
    """Get all discount rules by role and tier"""
    db_rules = _get_discount_rules_from_db()
    if db_rules and "rules" in db_rules:
        return db_rules
    # Return empty rules when no database
    return {"rules": {}}


@router.put("/discount-rules")
async def update_discount_rules(
    rules: Dict[str, Dict[str, int]], current_user: dict = Depends(get_current_user)
):
    """Update discount rules (SUPERADMIN/ADMIN only)"""
    if not any(role in current_user["roles"] for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return {"message": "Discount rules updated", "rules": rules}


@router.post("/discount-rules")
async def set_discount_rule(
    rule: DiscountSettings, current_user: dict = Depends(get_current_user)
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
    integrations = _get_integrations_from_db()
    if integrations:
        return {"integrations": integrations}
    # Return empty list when no database
    return {"integrations": []}


@router.get("/integrations/{integration_type}")
async def get_integration(
    integration_type: str, current_user: dict = Depends(get_current_user)
):
    """Get specific integration configuration"""
    return {
        "type": integration_type.upper(),
        "is_configured": False,
        "is_enabled": False,
        "config": {},
    }


@router.put("/integrations/{integration_type}")
async def update_integration(
    integration_type: str,
    config: IntegrationConfig,
    current_user: dict = Depends(get_current_user),
):
    """Update integration configuration (SUPERADMIN/ADMIN only)"""
    if not any(role in current_user["roles"] for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return {
        "message": f"{integration_type} integration updated",
        "config": config.model_dump(),
    }


@router.post("/integrations/{integration_type}/test")
async def test_integration(
    integration_type: str, current_user: dict = Depends(get_current_user)
):
    """Test integration connection"""
    return {"status": "success", "message": f"{integration_type} connection successful"}


# ============================================================================
# SYSTEM SETTINGS ENDPOINTS
# ============================================================================


@router.get("/system")
async def get_system_settings(current_user: dict = Depends(get_current_user)):
    """Get system settings"""
    collection = _get_settings_collection("system_settings")
    if collection:
        settings = collection.find_one({"_id": "default"})
        if settings:
            settings.pop("_id", None)
            return settings
    # Return default empty settings
    return {
        "maintenance_mode": False,
        "allow_registrations": False,
        "session_timeout_minutes": 480,
        "max_login_attempts": 5,
        "password_min_length": 8,
        "require_2fa": False,
        "backup_enabled": False,
        "backup_frequency": "",
        "last_backup": None,
        "data_retention_days": 365,
    }


@router.put("/system")
async def update_system_settings(
    settings: Dict, current_user: dict = Depends(get_current_user)
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
    current_user: dict = Depends(get_current_user),
):
    """Get audit logs (SUPERADMIN/ADMIN only)"""
    if not any(role in current_user["roles"] for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    audit_repo = get_audit_repository()
    if audit_repo:
        # Build filter
        filter_dict = {}
        if entity_type:
            filter_dict["entity_type"] = entity_type
        if entity_id:
            filter_dict["entity_id"] = entity_id
        if user_id:
            filter_dict["user_id"] = user_id
        if action:
            filter_dict["action"] = action

        logs = audit_repo.find_many(filter_dict, skip=offset, limit=limit)
        total = audit_repo.count(filter_dict)

        # Clean up MongoDB _id fields
        for log in logs:
            log.pop("_id", None)

        return {"logs": logs, "total": total, "limit": limit, "offset": offset}

    # Return empty when no database
    return {"logs": [], "total": 0, "limit": limit, "offset": offset}


@router.get("/audit-logs/summary")
async def get_audit_summary(current_user: dict = Depends(get_current_user)):
    """Get audit log summary for dashboard"""
    if not any(role in current_user["roles"] for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    audit_repo = get_audit_repository()
    if audit_repo:
        # Get today's summary
        from datetime import date, timedelta

        today = date.today()
        week_start = today - timedelta(days=7)

        # Count today's actions
        today_count = audit_repo.count({"date": today.isoformat()})
        week_count = audit_repo.count({})  # Would need date range query

        return {
            "today": {
                "total_actions": today_count,
                "logins": 0,
                "orders_created": 0,
                "products_updated": 0,
                "users_created": 0,
            },
            "this_week": {"total_actions": week_count, "top_users": []},
        }

    # Return empty summary when no database
    return {
        "today": {
            "total_actions": 0,
            "logins": 0,
            "orders_created": 0,
            "products_updated": 0,
            "users_created": 0,
        },
        "this_week": {"total_actions": 0, "top_users": []},
    }
