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

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any
from datetime import datetime
import hashlib
import os
import base64
import re
import logging
from .auth import get_current_user, hash_password, verify_password, require_roles
from ..dependencies import get_audit_repository

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================================
# Credential Encryption / Masking
# ============================================================================
# Encrypts API keys at rest using AES-like XOR with HMAC-derived key.
# For production, replace with Fernet (cryptography lib) or cloud KMS.

# Source the at-rest credential key from env. Prefer a dedicated
# CREDENTIAL_ENCRYPTION_KEY, fall back to the app's JWT_SECRET_KEY. We do NOT
# ship a hardcoded default: a known constant would mean integration secrets
# (Razorpay/Shopify/Tally keys) are effectively stored in plaintext. Fail
# loudly if neither is set -- api.routers.auth already guarantees
# JWT_SECRET_KEY is present whenever the app boots, so this only trips if this
# module is imported in isolation without a configured environment.
_CRED_SECRET = os.getenv("CREDENTIAL_ENCRYPTION_KEY") or os.getenv("JWT_SECRET_KEY")
if not _CRED_SECRET:
    raise RuntimeError(
        "CREDENTIAL_ENCRYPTION_KEY or JWT_SECRET_KEY environment variable is "
        "required to encrypt integration credentials at rest. "
        "Generate one with: openssl rand -hex 32"
    )

# Sensitive config field names that must be encrypted at rest & masked on read
_SENSITIVE_FIELDS = {
    "api_key",
    "api_secret",
    "secret_key",
    "key_secret",
    "secret",
    "password",
    "token",
    "access_token",
    "refresh_token",
    "private_key",
    "webhook_secret",
    "razorpay_key_secret",
    "shopify_api_secret",
    "whatsapp_api_key",
    "tally_password",
    "shiprocket_password",
}


def _mask_value(val: str) -> str:
    """Mask a credential: show first 4 and last 2 chars only."""
    if not val or len(val) < 8:
        return "****"
    return val[:4] + "*" * (len(val) - 6) + val[-2:]


def _mask_config(config: dict) -> dict:
    """Deep-mask any sensitive fields in a config dict."""
    masked = {}
    for k, v in config.items():
        if isinstance(v, dict):
            masked[k] = _mask_config(v)
        elif isinstance(v, str) and k.lower() in _SENSITIVE_FIELDS:
            masked[k] = _mask_value(v)
        else:
            masked[k] = v
    return masked


def _encrypt_value(plaintext: str) -> str:
    """Simple reversible encoding for credential storage. NOT military-grade but
    prevents plaintext exposure in DB dumps. Use Fernet for production."""
    key = hashlib.sha256(_CRED_SECRET.encode()).digest()
    encoded = plaintext.encode("utf-8")
    xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(encoded))
    return "enc:" + base64.b64encode(xored).decode("ascii")


def _decrypt_value(ciphertext: str) -> str:
    """Reverse _encrypt_value."""
    if not ciphertext.startswith("enc:"):
        return ciphertext  # Not encrypted (legacy data)
    raw = base64.b64decode(ciphertext[4:])
    key = hashlib.sha256(_CRED_SECRET.encode()).digest()
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(raw)).decode("utf-8")


def _encrypt_config(config: dict) -> dict:
    """Encrypt sensitive fields before writing to MongoDB."""
    encrypted = {}
    for k, v in config.items():
        if isinstance(v, dict):
            encrypted[k] = _encrypt_config(v)
        elif (
            isinstance(v, str)
            and k.lower() in _SENSITIVE_FIELDS
            and not v.startswith("enc:")
        ):
            encrypted[k] = _encrypt_value(v)
        else:
            encrypted[k] = v
    return encrypted


def _decrypt_config(config: dict) -> dict:
    """Decrypt sensitive fields after reading from MongoDB (for internal use)."""
    decrypted = {}
    for k, v in config.items():
        if isinstance(v, dict):
            decrypted[k] = _decrypt_config(v)
        elif isinstance(v, str) and v.startswith("enc:"):
            try:
                decrypted[k] = _decrypt_value(v)
            except Exception:
                decrypted[k] = v  # Can't decrypt — return as-is
        else:
            decrypted[k] = v
    return decrypted


# ============================================================================
# DATABASE HELPER FUNCTIONS
# ============================================================================


def _get_settings_collection(collection_name: str):
    """Get settings from database"""
    try:
        from database.connection import get_db

        db = get_db()
        if db and db.is_connected:
            return db.get_collection(collection_name)
    except Exception:
        pass
    return None


def _get_business_settings_from_db() -> Optional[dict]:
    """Fetch business settings from database"""
    collection = _get_settings_collection("business_settings")
    if collection is not None:
        settings = collection.find_one({"_id": "default"})
        if settings:
            settings.pop("_id", None)
            return settings
    return None


def _get_tax_settings_from_db() -> Optional[dict]:
    """Fetch tax settings from database"""
    collection = _get_settings_collection("tax_settings")
    if collection is not None:
        settings = collection.find_one({"_id": "default"})
        if settings:
            settings.pop("_id", None)
            return settings
    return None


def _get_invoice_settings_from_db() -> Optional[dict]:
    """Fetch invoice settings from database"""
    collection = _get_settings_collection("invoice_settings")
    if collection is not None:
        settings = collection.find_one({"_id": "default"})
        if settings:
            settings.pop("_id", None)
            return settings
    return None


def _get_printer_settings_from_db() -> Optional[dict]:
    """Fetch printer settings from database"""
    collection = _get_settings_collection("printer_settings")
    if collection is not None:
        settings = collection.find_one({"_id": "default"})
        if settings:
            settings.pop("_id", None)
            return settings
    return None


def _get_notification_templates_from_db() -> List[dict]:
    """Fetch notification templates from database"""
    collection = _get_settings_collection("notification_templates")
    if collection is not None:
        templates = list(collection.find({}))
        for t in templates:
            t.pop("_id", None)
        return templates
    return []


def _get_discount_rules_from_db() -> Optional[dict]:
    """Fetch discount rules from database"""
    collection = _get_settings_collection("discount_rules")
    if collection is not None:
        rules = collection.find_one({"_id": "default"})
        if rules:
            rules.pop("_id", None)
            return rules
    return None


def _get_integrations_from_db(mask: bool = True) -> List[dict]:
    """Fetch integration configs from database.
    mask=True (default): sensitive fields are masked for API responses.
    mask=False: decrypts for internal use (e.g., actually calling Shopify API).
    """
    collection = _get_settings_collection("integrations")
    if collection is not None:
        integrations = list(collection.find({}))
        for i in integrations:
            i.pop("_id", None)
            if "config" in i and isinstance(i["config"], dict):
                if mask:
                    # Decrypt then mask — API consumers see masked values
                    decrypted = _decrypt_config(i["config"])
                    i["config"] = _mask_config(decrypted)
                else:
                    # Decrypt for internal use
                    i["config"] = _decrypt_config(i["config"])
        return integrations
    return []


def _get_user_from_db(user_id: str) -> Optional[dict]:
    """Fetch user profile data from database by user_id"""
    try:
        from database.connection import get_db

        db = get_db()
        if db and db.is_connected:
            users_collection = db.get_collection("users")
            user = users_collection.find_one({"_id": user_id})
            if user:
                user.pop("_id", None)
                return user
    except Exception:
        pass
    return None


# ============================================================================
# SCHEMAS
# ============================================================================


class DiscountSettings(BaseModel):
    role: str
    category: str
    max_discount: float = Field(..., ge=0.0, le=100.0)


class IntegrationConfig(BaseModel):
    integration_type: str  # SHOPIFY, TALLY, SHIPROCKET, WHATSAPP, RAZORPAY
    enabled: bool
    config: Dict


# Marketplace channels (Amazon / Flipkart). Light config scaffold so the
# channels are wireable later — no live marketplace API calls are made here.
_MARKETPLACE_CHANNELS = ("amazon", "flipkart")


class MarketplaceChannelConfig(BaseModel):
    enabled: bool = False
    seller_id: str = ""
    # Free-form extras (marketplace, region, fulfillment mode, etc.). Any
    # secret-looking keys (api_key/secret/token/...) are encrypted at rest
    # by _encrypt_config, exactly like the integrations collection.
    config: Dict[str, Any] = Field(default_factory=dict)


class MarketplaceChannelsPayload(BaseModel):
    channels: Dict[str, MarketplaceChannelConfig] = Field(default_factory=dict)


class ProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None


class PasswordChange(BaseModel):
    current_password: str
    # max_length=72 avoids silent bcrypt truncation (bcrypt only uses first 72 bytes)
    new_password: str = Field(..., min_length=8, max_length=72)


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


_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")


class TaxSettings(BaseModel):
    gst_enabled: bool = True
    company_gstin: str = ""
    default_gst_rate: float = Field(default=18.0, ge=0.0, le=100.0)
    hsn_validation: bool = True
    e_invoice_enabled: bool = False
    e_invoice_username: Optional[str] = None
    e_way_bill_enabled: bool = False
    e_way_bill_threshold: float = Field(default=50000.0, ge=0.0)
    tds_enabled: bool = False
    tds_rate: float = Field(default=0.0, ge=0.0, le=100.0)

    @field_validator("company_gstin")
    @classmethod
    def _validate_company_gstin(cls, v: str) -> str:
        if not v:
            return v
        normalised = v.strip().upper()
        if not _GSTIN_RE.match(normalised):
            raise ValueError(
                "company_gstin must be 15 characters in GSTIN format "
                "(e.g. 29ABCDE1234F1Z5)"
            )
        return normalised


_FINANCIAL_YEAR_RE = re.compile(r"^[0-9]{4}-[0-9]{2}$")


class InvoiceSettings(BaseModel):
    invoice_prefix: str = "INV"
    invoice_start_number: int = Field(default=1, ge=1)
    current_invoice_number: int = Field(default=1, ge=1)
    financial_year: str = "2024-25"
    show_logo_on_invoice: bool = True
    show_terms_on_invoice: bool = True
    default_terms: str = "Goods once sold will not be returned or exchanged."
    default_warranty_days: int = Field(default=365, ge=0, le=3650)
    show_qr_code: bool = True
    digital_signature_enabled: bool = False

    @field_validator("invoice_prefix")
    @classmethod
    def _validate_prefix(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("invoice_prefix must not be empty")
        if len(stripped) > 10:
            raise ValueError("invoice_prefix must be 10 characters or fewer")
        return stripped.upper()

    @field_validator("financial_year")
    @classmethod
    def _validate_fy(cls, v: str) -> str:
        if v and not _FINANCIAL_YEAR_RE.match(v.strip()):
            raise ValueError("financial_year must be in YYYY-YY format (e.g. 2024-25)")
        return v.strip()


class NotificationTemplate(BaseModel):
    template_id: str
    template_type: str  # SMS, EMAIL, WHATSAPP
    trigger_event: str  # ORDER_CREATED, ORDER_READY, PAYMENT_RECEIVED, etc.
    is_enabled: bool = True
    subject: Optional[str] = None  # For email
    content: str
    variables: List[str] = []  # Available variables like {customer_name}, {order_id}


_VALID_RECEIPT_WIDTHS = {58, 80}
_VALID_LABEL_SIZES = {"50x25", "50x30", "100x50"}


class PrinterSettings(BaseModel):
    receipt_printer_name: Optional[str] = None
    receipt_printer_width: int = 80  # mm
    label_printer_name: Optional[str] = None
    label_size: str = "50x25"  # mm
    auto_print_receipt: bool = True
    auto_print_job_card: bool = True
    copies_per_print: int = Field(default=1, ge=1, le=10)
    # QZ Tray silent raw label printing. When False (or no cert/key on the
    # server), the frontend falls back to HTML print windows.
    qz_enabled: bool = True
    # Auto-print a stage sticker each time a workshop job advances a stage.
    auto_print_stage_sticker: bool = True

    @field_validator("receipt_printer_width")
    @classmethod
    def _validate_width(cls, v: int) -> int:
        if v not in _VALID_RECEIPT_WIDTHS:
            raise ValueError(
                f"receipt_printer_width must be one of {sorted(_VALID_RECEIPT_WIDTHS)} mm"
            )
        return v

    @field_validator("label_size")
    @classmethod
    def _validate_label_size(cls, v: str) -> str:
        if v not in _VALID_LABEL_SIZES:
            raise ValueError(
                f"label_size must be one of {sorted(_VALID_LABEL_SIZES)}"
            )
        return v


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


@router.get("")
@router.get("/")
async def get_settings_root():
    """Root endpoint for system settings"""
    return {
        "module": "settings",
        "status": "active",
        "message": "settings overview endpoint ready",
    }


@router.get("/profile")
async def get_profile(current_user: dict = Depends(get_current_user)):
    """Get current user's profile"""
    # Try to fetch full user data from database
    user_data = _get_user_from_db(current_user["user_id"])

    if user_data:
        # Merge database fields with JWT data
        return {
            "user_id": current_user["user_id"],
            "username": current_user["username"],
            "roles": current_user["roles"],
            "store_ids": current_user["store_ids"],
            "active_store_id": current_user.get("active_store_id"),
            "full_name": user_data.get("full_name"),
            "email": user_data.get("email"),
            "phone": user_data.get("phone"),
        }

    # Fallback to JWT data if database is unavailable
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
    """Update current user's profile (full_name / email / phone)"""
    updates = profile.model_dump(exclude_none=True)
    if updates:
        from ..dependencies import get_user_repository

        user_repo = get_user_repository()
        if user_repo is not None:
            user_repo.update(current_user.get("user_id"), updates)
    return {
        "message": "Profile updated successfully",
        "updated_fields": updates,
    }


@router.post("/profile/change-password")
async def change_password(
    passwords: PasswordChange, current_user: dict = Depends(get_current_user)
):
    """Change current user's password"""
    from ..dependencies import get_user_repository

    user_repo = get_user_repository()

    if user_repo is not None:
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
    payload = settings.model_dump()
    collection = _get_settings_collection("business_settings")
    if collection is not None:
        collection.update_one({"_id": "default"}, {"$set": payload}, upsert=True)
        return {"message": "Business settings updated", "settings": payload}
    return {"message": "Business settings updated (no DB)", "settings": payload}


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
    """Update tax settings (SUPERADMIN/ADMIN/ACCOUNTANT)"""
    if not any(
        role in current_user["roles"] for role in ["SUPERADMIN", "ADMIN", "ACCOUNTANT"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    payload = settings.model_dump()
    collection = _get_settings_collection("tax_settings")
    if collection is not None:
        collection.update_one({"_id": "default"}, {"$set": payload}, upsert=True)
        return {"message": "Tax settings updated", "settings": payload}
    return {"message": "Tax settings updated (no DB)", "settings": payload}


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
    """Update invoice settings (SUPERADMIN/ADMIN/ACCOUNTANT)"""
    if not any(
        role in current_user["roles"] for role in ["SUPERADMIN", "ADMIN", "ACCOUNTANT"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    payload = settings.model_dump()
    collection = _get_settings_collection("invoice_settings")
    if collection is not None:
        collection.update_one({"_id": "default"}, {"$set": payload}, upsert=True)
        return {"message": "Invoice settings updated", "settings": payload}
    return {"message": "Invoice settings updated (no DB)", "settings": payload}


# ============================================================================
# NOTIFICATION SETTINGS ENDPOINTS
# ============================================================================


@router.get("/notifications/providers")
async def get_notification_providers(
    current_user: dict = Depends(require_roles("ADMIN")),
):
    """Channel provider config (SMS / WhatsApp / Email). Frontend
    settingsApi.getNotificationProviders was 404'ing. Reads the
    `notification_providers` singleton; falls back to env-driven
    defaults so the Settings → Notifications tab always renders."""
    import os

    coll = _get_settings_collection("notification_providers")
    defaults = {
        "whatsapp": {
            "provider": "MSG91",
            "enabled": bool(os.getenv("MSG91_API_KEY")),
            "sender": os.getenv("MSG91_WHATSAPP_INTEGRATED_NUMBER", ""),
        },
        "sms": {
            "provider": "MSG91",
            "enabled": bool(os.getenv("MSG91_API_KEY")),
            "sender": os.getenv("MSG91_SENDER", "BVOPTL"),
        },
        "email": {"provider": "SMTP", "enabled": False, "sender": ""},
        "dispatch_mode": os.getenv("DISPATCH_MODE", "off"),
    }
    if coll is not None:
        doc = coll.find_one({"_id": "notification_providers"})
        if doc:
            doc.pop("_id", None)
            defaults.update(doc)
    return defaults


@router.put("/notifications/providers")
async def update_notification_providers(
    providers: dict,
    current_user: dict = Depends(require_roles("ADMIN")),
):
    coll = _get_settings_collection("notification_providers")
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    providers = {k: v for k, v in providers.items() if k != "_id"}
    providers["updated_at"] = datetime.now().isoformat()
    coll.update_one({"_id": "notification_providers"}, {"$set": providers}, upsert=True)
    doc = coll.find_one({"_id": "notification_providers"})
    if doc:
        doc.pop("_id", None)
    return doc or {}


@router.get("/notifications/logs")
async def get_notification_logs(
    limit: int = Query(100, ge=1, le=500),
    current_user: dict = Depends(get_current_user),
):
    """Recent outbound notification log. Frontend
    settingsApi.getNotificationLogs was 404'ing. Reads the
    `notification_logs` collection (written by MEGAPHONE / marketing
    dispatch)."""
    coll = _get_settings_collection("notification_logs")
    if coll is None:
        return {"logs": [], "total": 0}
    docs = list(coll.find({}).sort("sent_at", -1).limit(limit))
    for d in docs:
        d.pop("_id", None)
    return {"logs": docs, "total": len(docs)}


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
    """Update (or upsert) a notification template (SUPERADMIN/ADMIN only).

    Persists one document per template_id in the `notification_templates`
    collection. Previously this returned success WITHOUT writing -- so every
    toggle/edit on the Settings -> Notification Templates tab silently reverted
    on reload (same data-loss class fixed for the other settings panels).
    """
    if not any(role in current_user["roles"] for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    payload = template.model_dump()
    payload["template_id"] = template_id  # path is canonical
    collection = _get_settings_collection("notification_templates")
    if collection is not None:
        collection.update_one(
            {"template_id": template_id}, {"$set": payload}, upsert=True
        )
        return {"message": "Template updated", "template": payload}
    return {"message": "Template updated (no DB)", "template": payload}


@router.post("/notifications/templates")
async def create_notification_template(
    template: NotificationTemplate, current_user: dict = Depends(get_current_user)
):
    """Create a notification template (SUPERADMIN/ADMIN only). Upsert by
    template_id so a repeat create is idempotent rather than duplicating."""
    if not any(role in current_user["roles"] for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    payload = template.model_dump()
    collection = _get_settings_collection("notification_templates")
    if collection is not None:
        collection.update_one(
            {"template_id": payload["template_id"]}, {"$set": payload}, upsert=True
        )
        return {"message": "Template created", "template": payload}
    return {"message": "Template created (no DB)", "template": payload}


@router.delete("/notifications/templates/{template_id}")
async def delete_notification_template(
    template_id: str, current_user: dict = Depends(get_current_user)
):
    """Delete a notification template (SUPERADMIN/ADMIN only)."""
    if not any(role in current_user["roles"] for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    collection = _get_settings_collection("notification_templates")
    if collection is not None:
        result = collection.delete_one({"template_id": template_id})
        return {"message": "Template deleted", "deleted_count": result.deleted_count}
    return {"message": "Template deleted (no DB)"}


@router.post("/notifications/test")
async def test_notification(
    template_id: str,
    test_phone: Optional[str] = None,
    test_email: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """Probe a notification template by attempting a real provider send.

    Previously this ALWAYS returned "Test notification sent" without calling any
    provider -- a silent lie: the operator believed a message went out when none
    did. Now we run the message through the same DISPATCH_MODE-gated provider the
    drain uses and report the HONEST result:

      - DISPATCH_MODE=off (default): nothing is dispatched. status SIMULATED,
        dispatched=False, message "not dispatched (DISPATCH_MODE=off)".
      - DISPATCH_MODE=test: only the configured TEST_PHONE actually receives;
        any other number is SIMULATED/suppressed.
      - DISPATCH_MODE=live: a real WhatsApp/SMS is sent to test_phone.

    Gated to SUPERADMIN/ADMIN because in live mode it can trigger a real send.
    """
    if not any(role in current_user["roles"] for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    if not test_phone:
        # Nothing to send to. Be explicit rather than claiming success.
        return {
            "message": "No test_phone provided -- nothing dispatched",
            "template_id": template_id,
            "dispatched": False,
            "status": "SKIPPED",
        }

    try:
        from agents.providers import send_whatsapp, dispatch_mode
    except Exception as exc:  # pragma: no cover - provider import failure
        raise HTTPException(
            status_code=503, detail=f"Notification provider unavailable: {exc}"
        ) from exc

    mode = dispatch_mode()
    body = f"[IMS test] template {template_id}"
    result = await send_whatsapp(test_phone, body, template_id=template_id)
    dispatched = result.status == "SENT"

    if result.status == "SIMULATED":
        human = f"Not dispatched (DISPATCH_MODE={mode}); message simulated only"
    elif result.status == "SENT":
        human = "Test notification dispatched to provider"
    elif result.status == "FAILED":
        human = f"Test notification FAILED: {result.error or 'unknown error'}"
    else:
        human = f"Test notification {result.status}"

    return {
        "message": human,
        "template_id": template_id,
        "dispatch_mode": mode,
        "dispatched": dispatched,
        "status": result.status,
        "provider_id": result.provider_id,
        "error": result.error,
    }


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
    """Update printer settings (SUPERADMIN/ADMIN/STORE_MANAGER).

    Printer config is store-management territory: every other settings write is
    role-gated, but this one only required a valid login -- so any authenticated
    user (e.g. workshop staff or a cashier) could overwrite the store's printer
    configuration. Gate it consistently with the rest of the settings router.
    """
    if not any(
        role in current_user["roles"]
        for role in ["SUPERADMIN", "ADMIN", "STORE_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    payload = settings.model_dump()
    collection = _get_settings_collection("printer_settings")
    if collection is not None:
        collection.update_one({"_id": "default"}, {"$set": payload}, upsert=True)
        return {"message": "Printer settings updated", "settings": payload}
    return {"message": "Printer settings updated (no DB)", "settings": payload}


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
    """Update discount rules (SUPERADMIN/ADMIN/AREA_MANAGER).

    Persists the full rules dict under the 'discount_rules' singleton.
    Previously returned success without writing (silent data loss on reload).
    """
    if not any(
        role in current_user["roles"]
        for role in ["SUPERADMIN", "ADMIN", "AREA_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    collection = _get_settings_collection("discount_rules")
    if collection is not None:
        collection.update_one(
            {"_id": "default"},
            {"$set": {"rules": rules, "updated_at": datetime.utcnow().isoformat()}},
            upsert=True,
        )
        return {"message": "Discount rules updated", "rules": rules}
    return {"message": "Discount rules updated (no DB)", "rules": rules}


@router.post("/discount-rules")
async def set_discount_rule(
    rule: DiscountSettings, current_user: dict = Depends(get_current_user)
):
    """Set individual discount rule (SUPERADMIN/ADMIN/AREA_MANAGER).

    Merges one role+category entry into the 'discount_rules' singleton.
    Previously returned success without writing (silent data loss on reload).
    """
    if not any(
        role in current_user["roles"]
        for role in ["SUPERADMIN", "ADMIN", "AREA_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    payload = rule.model_dump()
    collection = _get_settings_collection("discount_rules")
    if collection is not None:
        # Merge: update the specific role->category key inside the rules map
        key = f"rules.{payload['role']}.{payload['category']}"
        collection.update_one(
            {"_id": "default"},
            {
                "$set": {
                    key: payload["max_discount"],
                    "updated_at": datetime.utcnow().isoformat(),
                }
            },
            upsert=True,
        )
        return {"message": "Discount rule updated", "rule": payload}
    return {"message": "Discount rule updated (no DB)", "rule": payload}


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
    """Get one integration's configuration (sensitive fields masked).

    Reads the canonical {type:<lower>} doc that the provider clients
    (nexus_providers.py, services/shiprocket.py) and the
    /api/v1/admin/integrations/* endpoints share, so this reflects what
    actually activates an integration. (Was previously a hardcoded stub
    that always returned is_configured=False.)
    """
    collection = _get_settings_collection("integrations")
    if collection is not None:
        doc = collection.find_one({"type": integration_type.lower()})
        if doc:
            config = doc.get("config") or {}
            masked = (
                _mask_config(_decrypt_config(config))
                if isinstance(config, dict)
                else {}
            )
            return {
                "type": integration_type.lower(),
                "is_configured": bool(config),
                "is_enabled": bool(doc.get("enabled")),
                "config": masked,
            }
    return {
        "type": integration_type.lower(),
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
    """Update integration configuration (SUPERADMIN/ADMIN only).

    Writes the canonical {type:<lower>, enabled, config} document that the
    provider clients (nexus_providers.py, services/shiprocket.py) and the
    /api/v1/admin/integrations/* endpoints read - so saving here actually
    activates the integration. Stored as plaintext so the providers can use
    it, mirroring routers/admin.py::_save_integration_config.

    NOTE: previously this wrote an encrypted, `integration_type`-keyed doc
    that the providers could not read (wrong key + `enc:` values), so the UI
    silently failed to turn anything on. This converges both write paths
    onto the same Mongo document.
    """
    if not any(role in current_user["roles"] for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    payload = config.model_dump()
    cfg = payload.get("config") if isinstance(payload.get("config"), dict) else {}

    collection = _get_settings_collection("integrations")
    if collection is not None:
        collection.update_one(
            {"type": integration_type.lower()},
            {
                "$set": {
                    "type": integration_type.lower(),
                    "enabled": bool(payload.get("enabled")),
                    "config": cfg,
                    "updated_at": datetime.now().isoformat(),
                }
            },
            upsert=True,
        )

    return {
        "message": f"{integration_type} integration updated",
        "type": integration_type.lower(),
        "enabled": bool(payload.get("enabled")),
        "config": _mask_config(cfg),
    }


@router.post("/integrations/{integration_type}/test")
async def test_integration(
    integration_type: str, current_user: dict = Depends(get_current_user)
):
    """Report integration readiness honestly (does NOT fake success).

    This does not perform a live third-party call - real syncs run via the
    NEXUS agent with DISPATCH_MODE=live. It reports whether credentials are
    present (presence only, never values) plus the current DISPATCH_MODE, so
    the operator can tell configured-vs-dormant. (Was previously a placebo
    that returned success unconditionally.)
    """
    collection = _get_settings_collection("integrations")
    doc = (
        collection.find_one({"type": integration_type.lower()})
        if collection is not None
        else None
    )
    configured = bool((doc or {}).get("config")) and bool((doc or {}).get("enabled"))
    mode = (os.getenv("DISPATCH_MODE", "off") or "off").lower()
    return {
        "status": "configured" if configured else "not_configured",
        "integration": integration_type.lower(),
        "enabled": bool((doc or {}).get("enabled")),
        "dispatch_mode": mode,
        "live": configured and mode == "live",
        "message": (
            f"{integration_type} configuration saved; live sync runs via the "
            "NEXUS agent when credentials are set and DISPATCH_MODE=live."
            if configured
            else f"{integration_type} is not configured (set credentials and enable it)."
        ),
    }


# ============================================================================
# MARKETPLACE CHANNELS ENDPOINTS (light scaffold)
# ============================================================================
# Per-channel (amazon, flipkart) config so the channels are WIREABLE later.
# This deliberately does NOT make any live Amazon/Flipkart API calls — the
# sync endpoint is a stub that returns SIMULATED when unconfigured, mirroring
# the NEXUS provider fail-soft pattern (backend/agents/nexus_providers.py).
# Stored in the `marketplace_channels` singleton; sensitive fields encrypted.


def _default_marketplace_channels() -> Dict[str, Any]:
    """All known channels disabled, no seller ids — the empty baseline."""
    return {
        ch: {"enabled": False, "seller_id": "", "config": {}}
        for ch in _MARKETPLACE_CHANNELS
    }


@router.get("/marketplace-channels")
async def get_marketplace_channels(current_user: dict = Depends(get_current_user)):
    """Get per-channel marketplace config (amazon, flipkart).

    Sensitive fields inside each channel's `config` are masked. Falls back
    to an all-disabled baseline so the Settings tab always renders even
    without a database.
    """
    result = _default_marketplace_channels()
    coll = _get_settings_collection("marketplace_channels")
    if coll is not None:
        try:
            doc = coll.find_one({"_id": "marketplace_channels"})
            if doc:
                doc.pop("_id", None)
                for ch, cfg in (doc.get("channels") or {}).items():
                    if not isinstance(cfg, dict):
                        continue
                    masked = dict(cfg)
                    if isinstance(masked.get("config"), dict):
                        masked["config"] = _mask_config(
                            _decrypt_config(masked["config"])
                        )
                    result[ch] = {**result.get(ch, {}), **masked}
        except Exception:
            # Fail soft — return the baseline rather than 500.
            pass
    return {"channels": result}


@router.put("/marketplace-channels")
async def update_marketplace_channels(
    payload: MarketplaceChannelsPayload,
    current_user: dict = Depends(require_roles("ADMIN")),
):
    """Update marketplace channel config (SUPERADMIN/ADMIN only).

    Unknown channel keys are ignored. Sensitive fields in each channel's
    `config` are encrypted before persisting. No secrets are echoed back
    (the response is re-masked via the GET path's logic).
    """
    channels_in = payload.channels or {}
    to_store: Dict[str, Any] = {}
    for ch, cfg in channels_in.items():
        if ch not in _MARKETPLACE_CHANNELS:
            continue  # ignore channels we don't support yet
        entry = cfg.model_dump()
        if isinstance(entry.get("config"), dict):
            entry["config"] = _encrypt_config(entry["config"])
        to_store[ch] = entry

    coll = _get_settings_collection("marketplace_channels")
    if coll is not None:
        try:
            coll.update_one(
                {"_id": "marketplace_channels"},
                {
                    "$set": {
                        "channels": to_store,
                        "updated_at": datetime.utcnow().isoformat(),
                    }
                },
                upsert=True,
            )
        except Exception:
            pass

    # Re-read so the response carries masked values + the persisted baseline.
    result = _default_marketplace_channels()
    for ch, cfg in to_store.items():
        masked = dict(cfg)
        if isinstance(masked.get("config"), dict):
            masked["config"] = _mask_config(_decrypt_config(masked["config"]))
        result[ch] = {**result.get(ch, {}), **masked}
    return {"message": "Marketplace channels updated", "channels": result}


@router.post("/marketplace-channels/{channel}/sync")
async def sync_marketplace_channel(
    channel: str,
    current_user: dict = Depends(require_roles("ADMIN")),
):
    """Trigger a marketplace sync for one channel — STUB.

    Returns {status: "SIMULATED"} when the channel is missing/disabled or
    has no seller_id, mirroring the NEXUS provider fail-soft contract
    (no credentials -> no outbound call, structured non-error result).
    Real Amazon/Flipkart sync is intentionally not implemented here; this
    is the wireable seam for a future provider.
    """
    ch = (channel or "").strip().lower()
    if ch not in _MARKETPLACE_CHANNELS:
        raise HTTPException(status_code=404, detail=f"Unknown channel: {channel}")

    configured = False
    coll = _get_settings_collection("marketplace_channels")
    if coll is not None:
        try:
            doc = coll.find_one({"_id": "marketplace_channels"})
            cfg = ((doc or {}).get("channels") or {}).get(ch) or {}
            configured = bool(cfg.get("enabled")) and bool(cfg.get("seller_id"))
        except Exception:
            configured = False

    if not configured:
        return {
            "status": "SIMULATED",
            "channel": ch,
            "items_synced": 0,
            "notes": "channel not configured (enabled + seller_id required)",
        }

    # Configured but no real provider wired yet — still a stub, but report
    # it distinctly so the UI can show "pending implementation" vs "off".
    return {
        "status": "SIMULATED",
        "channel": ch,
        "items_synced": 0,
        "notes": "marketplace provider not yet implemented",
    }


# ============================================================================
# SYSTEM SETTINGS ENDPOINTS
# ============================================================================


@router.get("/system")
async def get_system_settings(current_user: dict = Depends(get_current_user)):
    """Get system settings (SUPERADMIN/ADMIN only)"""
    if not any(role in current_user["roles"] for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    collection = _get_settings_collection("system_settings")
    if collection is not None:
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
    """Update system settings (SUPERADMIN only).

    Persists to the 'system_settings' singleton.
    Previously returned success without writing (silent data loss on reload).
    """
    if "SUPERADMIN" not in current_user["roles"]:
        raise HTTPException(status_code=403, detail="Superadmin access required")
    # Strip any attempt to pass internal Mongo _id
    settings_clean = {k: v for k, v in settings.items() if k != "_id"}
    collection = _get_settings_collection("system_settings")
    if collection is not None:
        collection.update_one(
            {"_id": "default"},
            {
                "$set": {
                    **settings_clean,
                    "updated_at": datetime.utcnow().isoformat(),
                }
            },
            upsert=True,
        )
        return {"message": "System settings updated", "settings": settings_clean}
    return {"message": "System settings updated (no DB)", "settings": settings_clean}


# ============================================================================
# AUDIT LOG ENDPOINTS
# ============================================================================


def _audit_time_filter(start_date: Optional[str], end_date: Optional[str]) -> dict:
    """Build a Mongo `timestamp` range clause from inclusive YYYY-MM-DD bounds.

    Returns {} when neither bound parses, so a malformed date silently widens
    the query rather than 500ing it. `end_date` is taken to the END of that day
    (23:59:59.999999) so a single-day from==to range returns that whole day.
    """
    clause: dict = {}
    if start_date:
        try:
            clause["$gte"] = datetime.strptime(start_date, "%Y-%m-%d")
        except (ValueError, TypeError):
            pass
    if end_date:
        try:
            clause["$lte"] = datetime.strptime(end_date, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, microsecond=999999
            )
        except (ValueError, TypeError):
            pass
    return {"timestamp": clause} if clause else {}


@router.get("/audit-logs")
async def get_audit_logs(
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    user_id: Optional[str] = None,
    action: Optional[str] = None,
    store_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    current_user: dict = Depends(get_current_user),
):
    """Query the audit trail (SUPERADMIN/ADMIN only).

    Powers the SUPERADMIN Activity Log screen: filter by user, action, entity,
    store, and an inclusive YYYY-MM-DD date range — newest first. Every filter
    is optional and ANDs together; an unparseable date is ignored, never fatal.
    """
    if not any(role in current_user["roles"] for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))

    audit_repo = get_audit_repository()
    if audit_repo is not None:
        # Build filter — all clauses AND together.
        filter_dict: dict = {}
        if entity_type:
            filter_dict["entity_type"] = entity_type
        if entity_id:
            filter_dict["entity_id"] = entity_id
        if user_id:
            filter_dict["user_id"] = user_id
        if action:
            filter_dict["action"] = action
        if store_id:
            filter_dict["store_id"] = store_id
        filter_dict.update(_audit_time_filter(start_date, end_date))

        logs = audit_repo.find_many(
            filter_dict, sort=[("timestamp", -1)], skip=offset, limit=limit
        )
        total = audit_repo.count(filter_dict)

        # Clean up MongoDB _id fields
        for log in logs:
            log.pop("_id", None)

        return {"logs": logs, "total": total, "limit": limit, "offset": offset}

    # Return empty when no database
    return {"logs": [], "total": 0, "limit": limit, "offset": offset}


# ============================================================================
# ADMIN CONTROL PANEL ENDPOINTS
# ============================================================================


@router.get("/admin-controls")
async def get_admin_controls(current_user: dict = Depends(get_current_user)):
    """Get admin control panel settings (SUPERADMIN only)"""
    if "SUPERADMIN" not in current_user["roles"]:
        raise HTTPException(status_code=403, detail="Superadmin access required")
    collection = _get_settings_collection("admin_controls")
    if collection is not None:
        settings = collection.find_one({"_id": "default"})
        if settings:
            settings.pop("_id", None)
            return settings
    return {
        "store_modules": {},
        "discount_limits": [],
        "operational_rules": {},
    }


@router.put("/admin-controls")
async def update_admin_controls(
    controls: Dict, current_user: dict = Depends(get_current_user)
):
    """Update admin control panel settings (SUPERADMIN only)"""
    if "SUPERADMIN" not in current_user["roles"]:
        raise HTTPException(status_code=403, detail="Superadmin access required")
    collection = _get_settings_collection("admin_controls")
    if collection is not None:
        collection.update_one(
            {"_id": "default"},
            {"$set": controls},
            upsert=True,
        )
        return {"message": "Admin controls saved successfully", "controls": controls}
    return {"message": "Admin controls saved (no DB)", "controls": controls}


# ============================================================================
# APPROVAL WORKFLOWS ENDPOINTS
# ============================================================================

DEFAULT_APPROVAL_WORKFLOWS = [
    {
        "id": "wf-001",
        "type": "DISCOUNT_APPROVAL",
        "name": "Discount Approval",
        "description": "Requires manager approval when a discount exceeds the configured threshold.",
        "isEnabled": True,
        "thresholdType": "PERCENTAGE",
        "thresholdValue": 15,
        "approverRoles": ["ADMIN", "STORE_MANAGER"],
        "escalationTimeout": 2,
        "notifyOnRequest": True,
        "notifyOnApproval": True,
    },
    {
        "id": "wf-002",
        "type": "REFUND_APPROVAL",
        "name": "Refund Approval",
        "description": "All refund requests must be approved by an authorized manager before processing.",
        "isEnabled": True,
        "thresholdType": "ALWAYS",
        "thresholdValue": None,
        "approverRoles": ["ADMIN", "STORE_MANAGER", "ACCOUNTANT"],
        "escalationTimeout": 4,
        "notifyOnRequest": True,
        "notifyOnApproval": True,
    },
    {
        "id": "wf-003",
        "type": "PO_APPROVAL",
        "name": "Purchase Order Approval",
        "description": "Purchase orders exceeding the configured amount threshold require approval.",
        "isEnabled": True,
        "thresholdType": "AMOUNT",
        "thresholdValue": 50000,
        "approverRoles": ["SUPERADMIN", "ADMIN", "AREA_MANAGER"],
        "escalationTimeout": 8,
        "notifyOnRequest": True,
        "notifyOnApproval": False,
    },
    {
        "id": "wf-004",
        "type": "STOCK_ADJUSTMENT",
        "name": "Stock Write-off Approval",
        "description": "Stock write-offs and manual adjustments require approval.",
        "isEnabled": False,
        "thresholdType": "ALWAYS",
        "thresholdValue": None,
        "approverRoles": ["ADMIN", "STORE_MANAGER"],
        "escalationTimeout": 24,
        "notifyOnRequest": True,
        "notifyOnApproval": False,
    },
    {
        "id": "wf-005",
        "type": "CREDIT_SALE",
        "name": "Credit Sale Approval",
        "description": "Credit sales require manager approval before processing.",
        "isEnabled": False,
        "thresholdType": "AMOUNT",
        "thresholdValue": 5000,
        "approverRoles": ["ADMIN", "STORE_MANAGER", "ACCOUNTANT"],
        "escalationTimeout": 1,
        "notifyOnRequest": True,
        "notifyOnApproval": True,
    },
]


class ApprovalWorkflowItem(BaseModel):
    id: str
    type: str
    name: str
    description: str
    isEnabled: bool
    thresholdType: str  # AMOUNT, PERCENTAGE, ALWAYS
    thresholdValue: Optional[float] = None
    approverRoles: List[str]
    escalationTimeout: Optional[int] = None
    notifyOnRequest: bool = True
    notifyOnApproval: bool = True


class ApprovalWorkflowsPayload(BaseModel):
    workflows: List[ApprovalWorkflowItem]


@router.get("/approval-workflows")
async def get_approval_workflows(current_user: dict = Depends(get_current_user)):
    """Get approval workflow configurations"""
    if not any(role in current_user["roles"] for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    collection = _get_settings_collection("approval_workflows")
    if collection is not None:
        doc = collection.find_one({"_id": "default"})
        if doc:
            doc.pop("_id", None)
            return doc
    return {"workflows": DEFAULT_APPROVAL_WORKFLOWS}


@router.put("/approval-workflows")
async def update_approval_workflows(
    payload: ApprovalWorkflowsPayload,
    current_user: dict = Depends(get_current_user),
):
    """Update approval workflow configurations (SUPERADMIN/ADMIN only)"""
    if not any(role in current_user["roles"] for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    data = {"workflows": [w.model_dump() for w in payload.workflows]}
    collection = _get_settings_collection("approval_workflows")
    if collection is not None:
        collection.update_one(
            {"_id": "default"},
            {"$set": {**data, "updated_at": datetime.utcnow().isoformat()}},
            upsert=True,
        )
        return {"message": "Approval workflows saved successfully", **data}
    return {"message": "Approval workflows saved (no DB)", **data}


# ============================================================================
# FEATURE TOGGLES ENDPOINTS
# ============================================================================

DEFAULT_FEATURE_TOGGLES: Dict[str, bool] = {
    "pos-quick-sale": True,
    "eye-test-module": True,
    "workshop-module": True,
    "loyalty-points": False,
    "split-payments": True,
    "credit-billing": True,
    "emi-payments": False,
    "storefront": False,
}


class FeatureTogglesUpdate(BaseModel):
    features: Dict[str, bool]


@router.get("/feature-toggles/{store_id}")
async def get_feature_toggles(
    store_id: str, current_user: dict = Depends(get_current_user)
):
    """Get feature toggle states for a store (SUPERADMIN or STORE_MANAGER of that store)"""
    roles = current_user.get("roles", [])
    user_stores = current_user.get("store_ids", [])
    is_super = "SUPERADMIN" in roles
    is_store_mgr = "STORE_MANAGER" in roles and store_id in user_stores
    if not is_super and not is_store_mgr:
        raise HTTPException(
            status_code=403, detail="Superadmin or store manager access required"
        )

    # Check cache first
    from ..services.cache import cache

    cache_key = f"feature_toggles:{store_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    collection = _get_settings_collection("feature_toggles")
    if collection is not None:
        doc = collection.find_one({"_id": store_id})
        if doc:
            doc.pop("_id", None)
            result = {
                "store_id": store_id,
                "features": doc.get("features", DEFAULT_FEATURE_TOGGLES),
            }
            cache.set(cache_key, result, ttl=cache.TTL_LONG)
            return result
    result = {"store_id": store_id, "features": DEFAULT_FEATURE_TOGGLES}
    cache.set(cache_key, result, ttl=cache.TTL_LONG)
    return result


def _save_feature_toggles(store_id: str, features: Dict[str, bool]) -> dict:
    """Persist feature toggles to Mongo and invalidate the cache entry.

    Extracted so PUT and PATCH share identical write + cache-invalidation logic.
    Previously the PUT/PATCH endpoints wrote to Mongo but never cleared the
    TTL_LONG (15 min) cache, so callers that hit GET within 15 minutes of a
    save would see stale values.
    """
    from ..services.cache import cache

    collection = _get_settings_collection("feature_toggles")
    if collection is not None:
        collection.update_one(
            {"_id": store_id},
            {
                "$set": {
                    "features": features,
                    "updated_at": datetime.utcnow().isoformat(),
                }
            },
            upsert=True,
        )
        # Invalidate cached GET so the next read reflects the change immediately.
        cache.delete(f"feature_toggles:{store_id}")
        return {
            "message": "Feature toggles updated successfully",
            "store_id": store_id,
            "features": features,
        }
    return {
        "message": "Feature toggles saved (no DB)",
        "store_id": store_id,
        "features": features,
    }


@router.put("/feature-toggles/{store_id}")
async def update_feature_toggles_put(
    store_id: str,
    payload: FeatureTogglesUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Update feature toggle states for a store (SUPERADMIN or STORE_MANAGER of that store).

    Invalidates the cache so GET immediately returns the new values.
    """
    roles = current_user.get("roles", [])
    user_stores = current_user.get("store_ids", [])
    is_super = "SUPERADMIN" in roles
    is_store_mgr = "STORE_MANAGER" in roles and store_id in user_stores
    if not is_super and not is_store_mgr:
        raise HTTPException(
            status_code=403, detail="Superadmin or store manager access required"
        )
    return _save_feature_toggles(store_id, payload.features)


@router.patch("/feature-toggles/{store_id}")
async def update_feature_toggles_patch(
    store_id: str,
    payload: FeatureTogglesUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Update feature toggle states for a store (SUPERADMIN or STORE_MANAGER of that store).

    Invalidates the cache so GET immediately returns the new values.
    """
    roles = current_user.get("roles", [])
    user_stores = current_user.get("store_ids", [])
    is_super = "SUPERADMIN" in roles
    is_store_mgr = "STORE_MANAGER" in roles and store_id in user_stores
    if not is_super and not is_store_mgr:
        raise HTTPException(
            status_code=403, detail="Superadmin or store manager access required"
        )
    return _save_feature_toggles(store_id, payload.features)


@router.get("/audit-logs/summary")
async def get_audit_summary(current_user: dict = Depends(get_current_user)):
    """Get audit log summary for dashboard"""
    if not any(role in current_user["roles"] for role in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    audit_repo = get_audit_repository()
    if audit_repo is not None:
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
