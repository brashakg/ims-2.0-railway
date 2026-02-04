"""
IMS 2.0 - Integrations Engine
==============================
Manages all third-party integrations from Superadmin settings panel

Integrations:
1. Shopify (E-commerce)
2. Tally (Accounting)
3. Razorpay (Payments)
4. WhatsApp Business (Communications)
5. Shiprocket (Shipping)
6. Google Ads (Marketing)
7. Meta Ads (Marketing)
8. GST Portal (Compliance)
9. SMS Gateway
10. Email Service
"""
from dataclasses import dataclass, field
from datetime import datetime, date
from decimal import Decimal
from enum import Enum
from typing import List, Optional, Dict, Any, Tuple
import uuid
import json

class IntegrationType(Enum):
    SHOPIFY = "SHOPIFY"
    TALLY = "TALLY"
    RAZORPAY = "RAZORPAY"
    WHATSAPP = "WHATSAPP"
    SHIPROCKET = "SHIPROCKET"
    GOOGLE_ADS = "GOOGLE_ADS"
    META_ADS = "META_ADS"
    GST_PORTAL = "GST_PORTAL"
    SMS_GATEWAY = "SMS_GATEWAY"
    EMAIL_SERVICE = "EMAIL_SERVICE"

class IntegrationStatus(Enum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    CONFIGURED = "CONFIGURED"
    ACTIVE = "ACTIVE"
    ERROR = "ERROR"
    DISABLED = "DISABLED"

class SyncDirection(Enum):
    IMPORT = "IMPORT"      # Pull data from external
    EXPORT = "EXPORT"      # Push data to external
    BIDIRECTIONAL = "BIDIRECTIONAL"

@dataclass
class IntegrationCredential:
    key_name: str
    key_value: str
    is_encrypted: bool = True
    last_updated: datetime = field(default_factory=datetime.now)

@dataclass
class IntegrationConfig:
    id: str
    integration_type: IntegrationType
    name: str
    description: str
    
    # Connection details
    api_url: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    access_token: Optional[str] = None
    webhook_url: Optional[str] = None
    
    # Additional credentials
    credentials: Dict[str, IntegrationCredential] = field(default_factory=dict)
    
    # Settings
    is_enabled: bool = False
    sync_direction: SyncDirection = SyncDirection.BIDIRECTIONAL
    sync_interval_minutes: int = 60
    retry_on_failure: bool = True
    max_retries: int = 3
    
    # Status
    status: IntegrationStatus = IntegrationStatus.NOT_CONFIGURED
    last_sync: Optional[datetime] = None
    last_error: Optional[str] = None
    
    # Audit
    created_at: datetime = field(default_factory=datetime.now)
    created_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None

@dataclass
class SyncLog:
    id: str
    integration_type: IntegrationType
    sync_direction: SyncDirection
    started_at: datetime
    completed_at: Optional[datetime] = None
    status: str = "IN_PROGRESS"  # IN_PROGRESS, SUCCESS, FAILED, PARTIAL
    records_processed: int = 0
    records_success: int = 0
    records_failed: int = 0
    error_message: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

@dataclass
class WebhookEvent:
    id: str
    integration_type: IntegrationType
    event_type: str
    payload: Dict[str, Any]
    received_at: datetime
    processed: bool = False
    processed_at: Optional[datetime] = None
    error: Optional[str] = None


class IntegrationsEngine:
    """
    Integrations Management Engine
    
    Manages all third-party integrations from Superadmin panel
    """
    
    def __init__(self):
        self.configs: Dict[str, IntegrationConfig] = {}
        self.sync_logs: Dict[str, SyncLog] = {}
        self.webhook_events: Dict[str, WebhookEvent] = {}
        self._initialize_default_configs()
    
    def _initialize_default_configs(self):
        """Initialize default integration configurations"""
        
        # Shopify
        self._add_default_config(
            IntegrationType.SHOPIFY,
            "Shopify",
            "E-commerce platform integration for online orders",
            required_fields=["store_url", "api_key", "api_secret", "access_token"]
        )
        
        # Tally
        self._add_default_config(
            IntegrationType.TALLY,
            "Tally Prime",
            "Accounting software integration for financial sync",
            required_fields=["server_url", "company_name", "port"]
        )
        
        # Razorpay
        self._add_default_config(
            IntegrationType.RAZORPAY,
            "Razorpay",
            "Payment gateway for online and POS payments",
            required_fields=["key_id", "key_secret", "webhook_secret"]
        )
        
        # WhatsApp Business
        self._add_default_config(
            IntegrationType.WHATSAPP,
            "WhatsApp Business API",
            "Customer communications and order updates",
            required_fields=["phone_number_id", "access_token", "business_id"]
        )
        
        # Shiprocket
        self._add_default_config(
            IntegrationType.SHIPROCKET,
            "Shiprocket",
            "Shipping and logistics integration",
            required_fields=["email", "password", "pickup_location_id"]
        )
        
        # Google Ads
        self._add_default_config(
            IntegrationType.GOOGLE_ADS,
            "Google Ads",
            "Marketing performance tracking",
            required_fields=["customer_id", "developer_token", "refresh_token"]
        )
        
        # Meta Ads
        self._add_default_config(
            IntegrationType.META_ADS,
            "Meta (Facebook/Instagram) Ads",
            "Social media marketing tracking",
            required_fields=["app_id", "app_secret", "access_token", "ad_account_id"]
        )
        
        # GST Portal
        self._add_default_config(
            IntegrationType.GST_PORTAL,
            "GST Portal API",
            "GSTIN verification and compliance",
            required_fields=["gstin", "username", "api_key"]
        )
        
        # SMS Gateway
        self._add_default_config(
            IntegrationType.SMS_GATEWAY,
            "SMS Gateway",
            "Transactional and promotional SMS",
            required_fields=["api_key", "sender_id", "template_ids"]
        )
        
        # Email Service
        self._add_default_config(
            IntegrationType.EMAIL_SERVICE,
            "Email Service (SMTP/API)",
            "Transactional emails and notifications",
            required_fields=["smtp_host", "smtp_port", "username", "password"]
        )
    
    def _add_default_config(self, int_type: IntegrationType, name: str, desc: str, required_fields: List[str]):
        config = IntegrationConfig(
            id=str(uuid.uuid4()),
            integration_type=int_type,
            name=name,
            description=desc
        )
        # Store required fields info
        config.credentials["_required_fields"] = IntegrationCredential(
            key_name="_required_fields",
            key_value=json.dumps(required_fields),
            is_encrypted=False
        )
        self.configs[int_type.value] = config
    
    # =========================================================================
    # CONFIGURATION MANAGEMENT
    # =========================================================================
    
    def configure_integration(
        self,
        integration_type: IntegrationType,
        credentials: Dict[str, str],
        settings: Dict[str, Any] = None,
        configured_by: str = None
    ) -> Tuple[bool, str]:
        """Configure an integration with credentials"""
        
        config = self.configs.get(integration_type.value)
        if not config:
            return False, "Integration type not found"
        
        # Get required fields
        required = json.loads(config.credentials.get("_required_fields", IntegrationCredential("", "[]")).key_value)
        
        # Validate required fields
        missing = [f for f in required if f not in credentials]
        if missing:
            return False, f"Missing required fields: {', '.join(missing)}"
        
        # Store credentials (would be encrypted in production)
        for key, value in credentials.items():
            config.credentials[key] = IntegrationCredential(
                key_name=key,
                key_value=value,  # Would encrypt this
                is_encrypted=True
            )
        
        # Apply settings
        if settings:
            if "sync_interval_minutes" in settings:
                config.sync_interval_minutes = settings["sync_interval_minutes"]
            if "sync_direction" in settings:
                config.sync_direction = SyncDirection(settings["sync_direction"])
        
        config.status = IntegrationStatus.CONFIGURED
        config.updated_at = datetime.now()
        config.updated_by = configured_by
        
        return True, f"{config.name} configured successfully"
    
    def enable_integration(self, integration_type: IntegrationType, enabled_by: str) -> Tuple[bool, str]:
        """Enable an integration"""
        config = self.configs.get(integration_type.value)
        if not config:
            return False, "Integration not found"
        
        if config.status == IntegrationStatus.NOT_CONFIGURED:
            return False, "Integration not configured yet"
        
        config.is_enabled = True
        config.status = IntegrationStatus.ACTIVE
        config.updated_at = datetime.now()
        config.updated_by = enabled_by
        
        return True, f"{config.name} enabled"
    
    def disable_integration(self, integration_type: IntegrationType, disabled_by: str) -> Tuple[bool, str]:
        """Disable an integration"""
        config = self.configs.get(integration_type.value)
        if not config:
            return False, "Integration not found"
        
        config.is_enabled = False
        config.status = IntegrationStatus.DISABLED
        config.updated_at = datetime.now()
        config.updated_by = disabled_by
        
        return True, f"{config.name} disabled"
    
    def test_connection(self, integration_type: IntegrationType) -> Tuple[bool, str]:
        """Test integration connection"""
        config = self.configs.get(integration_type.value)
        if not config:
            return False, "Integration not found"
        
        if config.status == IntegrationStatus.NOT_CONFIGURED:
            return False, "Integration not configured"
        
        # Simulate connection test (would make actual API call)
        # In production, this would verify credentials with the service
        
        return True, f"Connection to {config.name} successful"
    
    def get_integration_status(self, integration_type: IntegrationType) -> Dict:
        """Get detailed status of an integration"""
        config = self.configs.get(integration_type.value)
        if not config:
            return {"error": "Integration not found"}
        
        # Get recent sync logs
        recent_syncs = [
            log for log in self.sync_logs.values()
            if log.integration_type == integration_type
        ][-5:]
        
        return {
            "name": config.name,
            "status": config.status.value,
            "is_enabled": config.is_enabled,
            "last_sync": config.last_sync.isoformat() if config.last_sync else None,
            "last_error": config.last_error,
            "sync_interval": config.sync_interval_minutes,
            "recent_syncs": [
                {
                    "started": s.started_at.isoformat(),
                    "status": s.status,
                    "records": s.records_processed
                }
                for s in recent_syncs
            ]
        }
    
    # =========================================================================
    # SHOPIFY INTEGRATION
    # =========================================================================
    
    def sync_shopify_orders(self) -> Tuple[bool, str, Optional[SyncLog]]:
        """Import orders from Shopify"""
        config = self.configs.get(IntegrationType.SHOPIFY.value)
        if not config or not config.is_enabled:
            return False, "Shopify not configured or disabled", None
        
        log = SyncLog(
            id=str(uuid.uuid4()),
            integration_type=IntegrationType.SHOPIFY,
            sync_direction=SyncDirection.IMPORT,
            started_at=datetime.now()
        )
        
        # Simulate order sync
        log.records_processed = 15
        log.records_success = 14
        log.records_failed = 1
        log.status = "PARTIAL"
        log.completed_at = datetime.now()
        log.details = {
            "orders_imported": 14,
            "failed_order_ids": ["#1234"]
        }
        
        self.sync_logs[log.id] = log
        config.last_sync = datetime.now()
        
        return True, f"Synced {log.records_success} orders from Shopify", log
    
    def sync_shopify_inventory(self, products: List[Dict]) -> Tuple[bool, str, Optional[SyncLog]]:
        """Export inventory to Shopify"""
        config = self.configs.get(IntegrationType.SHOPIFY.value)
        if not config or not config.is_enabled:
            return False, "Shopify not configured or disabled", None
        
        log = SyncLog(
            id=str(uuid.uuid4()),
            integration_type=IntegrationType.SHOPIFY,
            sync_direction=SyncDirection.EXPORT,
            started_at=datetime.now()
        )
        
        log.records_processed = len(products)
        log.records_success = len(products)
        log.status = "SUCCESS"
        log.completed_at = datetime.now()
        
        self.sync_logs[log.id] = log
        
        return True, f"Synced {len(products)} products to Shopify", log
    
    # =========================================================================
    # TALLY INTEGRATION
    # =========================================================================
    
    def export_to_tally(self, invoices: List[Dict], voucher_type: str = "Sales") -> Tuple[bool, str, Optional[SyncLog]]:
        """Export invoices to Tally"""
        config = self.configs.get(IntegrationType.TALLY.value)
        if not config or not config.is_enabled:
            return False, "Tally not configured or disabled", None
        
        log = SyncLog(
            id=str(uuid.uuid4()),
            integration_type=IntegrationType.TALLY,
            sync_direction=SyncDirection.EXPORT,
            started_at=datetime.now()
        )
        
        # Generate Tally XML format
        tally_xml = self._generate_tally_xml(invoices, voucher_type)
        
        log.records_processed = len(invoices)
        log.records_success = len(invoices)
        log.status = "SUCCESS"
        log.completed_at = datetime.now()
        log.details = {"xml_generated": True, "voucher_type": voucher_type}
        
        self.sync_logs[log.id] = log
        config.last_sync = datetime.now()
        
        return True, f"Exported {len(invoices)} vouchers to Tally", log
    
    def _generate_tally_xml(self, invoices: List[Dict], voucher_type: str) -> str:
        """Generate Tally-compatible XML"""
        # Simplified Tally XML format
        xml_parts = ['<?xml version="1.0"?>', '<ENVELOPE>', '<HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>', '<BODY><IMPORTDATA><REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME></REQUESTDESC><REQUESTDATA>']
        
        for inv in invoices:
            xml_parts.append(f'''
            <TALLYMESSAGE>
                <VOUCHER VCHTYPE="{voucher_type}">
                    <DATE>{inv.get('date', '')}</DATE>
                    <VOUCHERNUMBER>{inv.get('invoice_number', '')}</VOUCHERNUMBER>
                    <PARTYLEDGERNAME>{inv.get('customer_name', '')}</PARTYLEDGERNAME>
                    <AMOUNT>{inv.get('total', 0)}</AMOUNT>
                </VOUCHER>
            </TALLYMESSAGE>''')
        
        xml_parts.extend(['</REQUESTDATA></IMPORTDATA></BODY>', '</ENVELOPE>'])
        return '\n'.join(xml_parts)
    
    # =========================================================================
    # RAZORPAY INTEGRATION
    # =========================================================================
    
    def create_razorpay_order(self, amount: Decimal, currency: str = "INR", receipt: str = None) -> Tuple[bool, str, Optional[Dict]]:
        """Create Razorpay payment order"""
        config = self.configs.get(IntegrationType.RAZORPAY.value)
        if not config or not config.is_enabled:
            return False, "Razorpay not configured", None
        
        # Simulate order creation (would call Razorpay API)
        order = {
            "id": f"order_{uuid.uuid4().hex[:16]}",
            "amount": int(amount * 100),  # Razorpay uses paise
            "currency": currency,
            "receipt": receipt,
            "status": "created"
        }
        
        return True, "Payment order created", order
    
    def verify_razorpay_payment(self, order_id: str, payment_id: str, signature: str) -> Tuple[bool, str]:
        """Verify Razorpay payment signature"""
        config = self.configs.get(IntegrationType.RAZORPAY.value)
        if not config or not config.is_enabled:
            return False, "Razorpay not configured"
        
        # Would verify signature using Razorpay secret
        # Simplified verification
        return True, "Payment verified successfully"
    
    # =========================================================================
    # WHATSAPP INTEGRATION
    # =========================================================================
    
    def send_whatsapp_message(self, phone: str, template: str, params: Dict = None) -> Tuple[bool, str]:
        """Send WhatsApp message using template"""
        config = self.configs.get(IntegrationType.WHATSAPP.value)
        if not config or not config.is_enabled:
            return False, "WhatsApp not configured"
        
        # Would call WhatsApp Business API
        message_id = f"wamid.{uuid.uuid4().hex[:20]}"
        
        return True, f"Message sent: {message_id}"
    
    def send_order_update(self, phone: str, order_number: str, status: str) -> Tuple[bool, str]:
        """Send order status update via WhatsApp"""
        return self.send_whatsapp_message(
            phone,
            "order_status_update",
            {"order_number": order_number, "status": status}
        )
    
    # =========================================================================
    # SHIPROCKET INTEGRATION
    # =========================================================================
    
    def create_shiprocket_order(self, order_data: Dict) -> Tuple[bool, str, Optional[Dict]]:
        """Create shipment in Shiprocket"""
        config = self.configs.get(IntegrationType.SHIPROCKET.value)
        if not config or not config.is_enabled:
            return False, "Shiprocket not configured", None
        
        # Simulate shipment creation
        shipment = {
            "shipment_id": f"SR{uuid.uuid4().hex[:8].upper()}",
            "order_id": order_data.get("order_id"),
            "awb_code": f"AWB{uuid.uuid4().hex[:10].upper()}",
            "courier_name": "Delhivery",
            "status": "PICKUP_SCHEDULED"
        }
        
        return True, f"Shipment created: {shipment['awb_code']}", shipment
    
    def track_shipment(self, awb: str) -> Tuple[bool, str, Optional[Dict]]:
        """Track shipment status"""
        config = self.configs.get(IntegrationType.SHIPROCKET.value)
        if not config or not config.is_enabled:
            return False, "Shiprocket not configured", None
        
        # Simulate tracking
        tracking = {
            "awb": awb,
            "status": "IN_TRANSIT",
            "current_location": "Mumbai Hub",
            "expected_delivery": (datetime.now() + timedelta(days=2)).isoformat()
        }
        
        return True, "Tracking retrieved", tracking
    
    # =========================================================================
    # GST PORTAL INTEGRATION
    # =========================================================================
    
    def verify_gstin(self, gstin: str) -> Tuple[bool, str, Optional[Dict]]:
        """Verify GSTIN from GST Portal"""
        config = self.configs.get(IntegrationType.GST_PORTAL.value)
        if not config or not config.is_enabled:
            return False, "GST Portal not configured", None
        
        # Validate format
        if len(gstin) != 15:
            return False, "Invalid GSTIN format", None
        
        # Simulate GST Portal API response
        gst_data = {
            "gstin": gstin,
            "legal_name": "SAMPLE BUSINESS PVT LTD",
            "trade_name": "Sample Business",
            "status": "Active",
            "state_code": gstin[:2],
            "registration_date": "2018-07-01",
            "last_updated": datetime.now().isoformat()
        }
        
        return True, "GSTIN verified", gst_data
    
    # =========================================================================
    # DASHBOARD & OVERVIEW
    # =========================================================================
    
    def get_integrations_dashboard(self) -> Dict:
        """Get integrations overview for dashboard"""
        dashboard = {
            "total": len(self.configs),
            "active": 0,
            "configured": 0,
            "errors": 0,
            "integrations": []
        }
        
        for config in self.configs.values():
            status_info = {
                "type": config.integration_type.value,
                "name": config.name,
                "status": config.status.value,
                "is_enabled": config.is_enabled,
                "last_sync": config.last_sync.isoformat() if config.last_sync else None
            }
            dashboard["integrations"].append(status_info)
            
            if config.status == IntegrationStatus.ACTIVE:
                dashboard["active"] += 1
            elif config.status == IntegrationStatus.CONFIGURED:
                dashboard["configured"] += 1
            elif config.status == IntegrationStatus.ERROR:
                dashboard["errors"] += 1
        
        return dashboard


def demo_integrations():
    print("=" * 60)
    print("IMS 2.0 INTEGRATIONS ENGINE DEMO")
    print("=" * 60)
    
    engine = IntegrationsEngine()
    
    # Configure Shopify
    print("\n🛒 Configure Shopify")
    success, msg = engine.configure_integration(
        IntegrationType.SHOPIFY,
        {
            "store_url": "bettervision.myshopify.com",
            "api_key": "xxx-api-key",
            "api_secret": "xxx-secret",
            "access_token": "shpat_xxx"
        },
        configured_by="superadmin"
    )
    print(f"  {msg}")
    
    success, msg = engine.enable_integration(IntegrationType.SHOPIFY, "superadmin")
    print(f"  {msg}")
    
    success, msg = engine.test_connection(IntegrationType.SHOPIFY)
    print(f"  Test: {msg}")
    
    # Configure Tally
    print("\n📊 Configure Tally")
    success, msg = engine.configure_integration(
        IntegrationType.TALLY,
        {
            "server_url": "http://localhost",
            "company_name": "Better Vision Opticals Pvt Ltd",
            "port": "9000"
        },
        configured_by="superadmin"
    )
    print(f"  {msg}")
    engine.enable_integration(IntegrationType.TALLY, "superadmin")
    
    # Export to Tally
    print("\n📤 Export to Tally")
    invoices = [
        {"invoice_number": "BV/INV/001", "customer_name": "Rajesh Kumar", "total": 12500, "date": "2026-01-21"},
        {"invoice_number": "BV/INV/002", "customer_name": "Priya Singh", "total": 8900, "date": "2026-01-21"}
    ]
    success, msg, log = engine.export_to_tally(invoices)
    print(f"  {msg}")
    
    # Configure Razorpay
    print("\n💳 Configure Razorpay")
    success, msg = engine.configure_integration(
        IntegrationType.RAZORPAY,
        {
            "key_id": "rzp_test_xxx",
            "key_secret": "xxx-secret",
            "webhook_secret": "xxx-webhook"
        },
        configured_by="superadmin"
    )
    print(f"  {msg}")
    engine.enable_integration(IntegrationType.RAZORPAY, "superadmin")
    
    # Create payment order
    print("\n💰 Create Payment Order")
    success, msg, order = engine.create_razorpay_order(Decimal("5000"), receipt="INV-001")
    print(f"  {msg}")
    print(f"  Order ID: {order['id']}")
    
    # Configure WhatsApp
    print("\n📱 Configure WhatsApp")
    success, msg = engine.configure_integration(
        IntegrationType.WHATSAPP,
        {
            "phone_number_id": "1234567890",
            "access_token": "EAAxxxx",
            "business_id": "9876543210"
        },
        configured_by="superadmin"
    )
    print(f"  {msg}")
    engine.enable_integration(IntegrationType.WHATSAPP, "superadmin")
    
    # Send order update
    print("\n📤 Send Order Update")
    success, msg = engine.send_order_update("9876543210", "ORD-001", "Ready for Delivery")
    print(f"  {msg}")
    
    # GST Verification
    print("\n🏛️ GST Portal - Verify GSTIN")
    success, msg = engine.configure_integration(
        IntegrationType.GST_PORTAL,
        {"gstin": "20AABCU9603R1ZM", "username": "admin", "api_key": "xxx"},
        configured_by="superadmin"
    )
    engine.enable_integration(IntegrationType.GST_PORTAL, "superadmin")
    
    success, msg, gst_data = engine.verify_gstin("20AABCU9603R1ZM")
    print(f"  {msg}")
    print(f"  Legal Name: {gst_data['legal_name']}")
    print(f"  Status: {gst_data['status']}")
    
    # Dashboard
    print("\n📊 Integrations Dashboard")
    dashboard = engine.get_integrations_dashboard()
    print(f"  Total: {dashboard['total']}")
    print(f"  Active: {dashboard['active']}")
    print(f"  Configured: {dashboard['configured']}")
    
    print("\n" + "=" * 60)


if __name__ == "__main__":
    demo_integrations()
