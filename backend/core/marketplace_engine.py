"""
IMS 2.0 - Marketplace Integration Engine
========================================
Features:
1. Shopify integration
2. Amazon/Flipkart sync
3. Order import
4. Inventory sync
5. Shiprocket integration
"""
from dataclasses import dataclass, field
from datetime import datetime, date
from decimal import Decimal
from enum import Enum
from typing import List, Optional, Dict, Tuple
import uuid

class MarketplaceType(Enum):
    SHOPIFY = "SHOPIFY"
    AMAZON = "AMAZON"
    FLIPKART = "FLIPKART"
    WEBSITE = "WEBSITE"

class MarketplaceOrderStatus(Enum):
    NEW = "NEW"
    CONFIRMED = "CONFIRMED"
    PROCESSING = "PROCESSING"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"
    RETURNED = "RETURNED"

class SyncStatus(Enum):
    PENDING = "PENDING"
    SYNCED = "SYNCED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"

@dataclass
class MarketplaceConfig:
    id: str
    marketplace: MarketplaceType
    store_url: str
    api_key: str
    api_secret: str
    access_token: Optional[str] = None
    is_active: bool = True
    last_sync: Optional[datetime] = None
    sync_inventory: bool = True
    sync_orders: bool = True
    sync_prices: bool = True

@dataclass
class MarketplaceProduct:
    id: str
    ims_product_id: str
    marketplace: MarketplaceType
    marketplace_product_id: str
    marketplace_sku: str
    title: str
    price: Decimal
    compare_price: Optional[Decimal] = None
    inventory_qty: int = 0
    is_active: bool = True
    last_synced: Optional[datetime] = None
    sync_status: SyncStatus = SyncStatus.PENDING

@dataclass
class MarketplaceOrder:
    id: str
    marketplace: MarketplaceType
    marketplace_order_id: str
    marketplace_order_number: str
    order_date: datetime
    
    # Customer
    customer_name: str
    customer_email: str
    customer_phone: str
    
    # Shipping
    shipping_name: str
    shipping_address: str
    shipping_city: str
    shipping_state: str
    shipping_pincode: str
    shipping_country: str = "India"
    
    # Items
    items: List[dict] = field(default_factory=list)
    
    # Amounts
    subtotal: Decimal = Decimal("0")
    shipping_charge: Decimal = Decimal("0")
    discount: Decimal = Decimal("0")
    total: Decimal = Decimal("0")
    
    # Status
    marketplace_status: str = ""
    ims_status: MarketplaceOrderStatus = MarketplaceOrderStatus.NEW
    
    # IMS linking
    ims_order_id: Optional[str] = None
    ims_invoice_id: Optional[str] = None
    
    # Shipping
    courier_name: Optional[str] = None
    tracking_number: Optional[str] = None
    shipped_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    
    imported_at: datetime = field(default_factory=datetime.now)

@dataclass
class ShipmentRequest:
    id: str
    order_id: str
    marketplace_order_id: str
    
    # Package
    length: float
    width: float
    height: float
    weight: float
    
    # Destination
    name: str
    phone: str
    address: str
    city: str
    state: str
    pincode: str
    
    # Status
    status: str = "PENDING"  # PENDING, CREATED, PICKED, IN_TRANSIT, DELIVERED
    awb_number: Optional[str] = None
    courier: Optional[str] = None
    label_url: Optional[str] = None
    tracking_url: Optional[str] = None
    
    created_at: datetime = field(default_factory=datetime.now)

class MarketplaceEngine:
    def __init__(self):
        self.configs: Dict[str, MarketplaceConfig] = {}
        self.products: Dict[str, MarketplaceProduct] = {}
        self.orders: Dict[str, MarketplaceOrder] = {}
        self.shipments: Dict[str, ShipmentRequest] = {}
        self._order_counter = 0
    
    def add_marketplace_config(
        self, marketplace: MarketplaceType, store_url: str,
        api_key: str, api_secret: str
    ) -> MarketplaceConfig:
        config = MarketplaceConfig(
            id=str(uuid.uuid4()),
            marketplace=marketplace,
            store_url=store_url,
            api_key=api_key,
            api_secret=api_secret
        )
        self.configs[config.id] = config
        return config
    
    def sync_product_to_marketplace(
        self, ims_product_id: str, marketplace: MarketplaceType,
        title: str, price: Decimal, inventory: int
    ) -> Tuple[bool, str, Optional[MarketplaceProduct]]:
        """Sync a product to marketplace"""
        
        # In real implementation, would call marketplace API
        mp_product = MarketplaceProduct(
            id=str(uuid.uuid4()),
            ims_product_id=ims_product_id,
            marketplace=marketplace,
            marketplace_product_id=f"MP-{uuid.uuid4().hex[:8].upper()}",
            marketplace_sku=f"SKU-{ims_product_id[:8]}",
            title=title,
            price=price,
            inventory_qty=inventory,
            is_active=True,
            last_synced=datetime.now(),
            sync_status=SyncStatus.SYNCED
        )
        self.products[mp_product.id] = mp_product
        return True, f"Product synced to {marketplace.value}", mp_product
    
    def import_marketplace_order(
        self, marketplace: MarketplaceType,
        order_data: dict
    ) -> Tuple[bool, str, Optional[MarketplaceOrder]]:
        """Import order from marketplace"""
        
        self._order_counter += 1
        
        order = MarketplaceOrder(
            id=str(uuid.uuid4()),
            marketplace=marketplace,
            marketplace_order_id=order_data.get("order_id", f"ORD-{self._order_counter:05d}"),
            marketplace_order_number=order_data.get("order_number", f"#{self._order_counter}"),
            order_date=order_data.get("order_date", datetime.now()),
            customer_name=order_data.get("customer_name", ""),
            customer_email=order_data.get("customer_email", ""),
            customer_phone=order_data.get("customer_phone", ""),
            shipping_name=order_data.get("shipping_name", ""),
            shipping_address=order_data.get("shipping_address", ""),
            shipping_city=order_data.get("shipping_city", ""),
            shipping_state=order_data.get("shipping_state", ""),
            shipping_pincode=order_data.get("shipping_pincode", ""),
            items=order_data.get("items", []),
            subtotal=Decimal(str(order_data.get("subtotal", 0))),
            shipping_charge=Decimal(str(order_data.get("shipping", 0))),
            discount=Decimal(str(order_data.get("discount", 0))),
            total=Decimal(str(order_data.get("total", 0))),
            marketplace_status=order_data.get("status", "pending")
        )
        
        self.orders[order.id] = order
        return True, f"Order {order.marketplace_order_number} imported", order
    
    def update_order_status(
        self, order_id: str, status: MarketplaceOrderStatus,
        tracking_number: Optional[str] = None, courier: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Update marketplace order status"""
        
        order = self.orders.get(order_id)
        if not order:
            return False, "Order not found"
        
        order.ims_status = status
        
        if status == MarketplaceOrderStatus.SHIPPED:
            order.tracking_number = tracking_number
            order.courier_name = courier
            order.shipped_at = datetime.now()
        elif status == MarketplaceOrderStatus.DELIVERED:
            order.delivered_at = datetime.now()
        
        # In real implementation, would update marketplace via API
        return True, f"Order status updated to {status.value}"
    
    def create_shipment(
        self, order_id: str, length: float, width: float, height: float, weight: float
    ) -> Tuple[bool, str, Optional[ShipmentRequest]]:
        """Create shipment request (Shiprocket integration)"""
        
        order = self.orders.get(order_id)
        if not order:
            return False, "Order not found", None
        
        shipment = ShipmentRequest(
            id=str(uuid.uuid4()),
            order_id=order_id,
            marketplace_order_id=order.marketplace_order_id,
            length=length, width=width, height=height, weight=weight,
            name=order.shipping_name,
            phone=order.customer_phone,
            address=order.shipping_address,
            city=order.shipping_city,
            state=order.shipping_state,
            pincode=order.shipping_pincode
        )
        
        # Simulate Shiprocket response
        shipment.awb_number = f"AWB{uuid.uuid4().hex[:10].upper()}"
        shipment.courier = "Delhivery"
        shipment.status = "CREATED"
        shipment.tracking_url = f"https://track.shiprocket.co/{shipment.awb_number}"
        
        self.shipments[shipment.id] = shipment
        
        # Update order
        order.tracking_number = shipment.awb_number
        order.courier_name = shipment.courier
        
        return True, f"Shipment created. AWB: {shipment.awb_number}", shipment
    
    def sync_inventory_to_all(self, ims_product_id: str, new_qty: int) -> List[dict]:
        """Sync inventory change to all marketplaces"""
        results = []
        
        for prod in self.products.values():
            if prod.ims_product_id == ims_product_id and prod.is_active:
                old_qty = prod.inventory_qty
                prod.inventory_qty = new_qty
                prod.last_synced = datetime.now()
                prod.sync_status = SyncStatus.SYNCED
                
                results.append({
                    "marketplace": prod.marketplace.value,
                    "old_qty": old_qty,
                    "new_qty": new_qty,
                    "status": "SYNCED"
                })
        
        return results
    
    def get_pending_orders(self) -> List[MarketplaceOrder]:
        return [o for o in self.orders.values() if o.ims_status == MarketplaceOrderStatus.NEW]
    
    def get_marketplace_summary(self) -> dict:
        summary = {
            "total_orders": len(self.orders),
            "pending_orders": len([o for o in self.orders.values() if o.ims_status == MarketplaceOrderStatus.NEW]),
            "shipped_orders": len([o for o in self.orders.values() if o.ims_status == MarketplaceOrderStatus.SHIPPED]),
            "by_marketplace": {}
        }
        
        for mp in MarketplaceType:
            mp_orders = [o for o in self.orders.values() if o.marketplace == mp]
            summary["by_marketplace"][mp.value] = {
                "orders": len(mp_orders),
                "revenue": sum(o.total for o in mp_orders)
            }
        
        return summary

def demo_marketplace():
    print("=" * 60)
    print("IMS 2.0 MARKETPLACE ENGINE DEMO")
    print("=" * 60)
    
    engine = MarketplaceEngine()
    
    print("\n⚙️ Configure Shopify")
    config = engine.add_marketplace_config(
        MarketplaceType.SHOPIFY,
        "bettervision.myshopify.com",
        "api_key_xxx", "api_secret_xxx"
    )
    print(f"  Marketplace: {config.marketplace.value}")
    print(f"  Store: {config.store_url}")
    
    print("\n📦 Sync Product")
    success, msg, prod = engine.sync_product_to_marketplace(
        ims_product_id="prod-rayban-001",
        marketplace=MarketplaceType.SHOPIFY,
        title="Ray-Ban RB5154 Clubmaster",
        price=Decimal("6890"),
        inventory=15
    )
    print(f"  {msg}")
    print(f"  Marketplace SKU: {prod.marketplace_sku}")
    
    print("\n📥 Import Order from Shopify")
    success, msg, order = engine.import_marketplace_order(
        MarketplaceType.SHOPIFY,
        {
            "order_id": "SHP-12345",
            "order_number": "#1001",
            "customer_name": "Amit Shah",
            "customer_email": "amit@email.com",
            "customer_phone": "9876543210",
            "shipping_name": "Amit Shah",
            "shipping_address": "123 Main Street",
            "shipping_city": "Mumbai",
            "shipping_state": "Maharashtra",
            "shipping_pincode": "400001",
            "items": [{"sku": "SKU-prod-ray", "qty": 1, "price": 6890}],
            "subtotal": 6890,
            "shipping": 99,
            "discount": 0,
            "total": 6989,
            "status": "paid"
        }
    )
    print(f"  {msg}")
    print(f"  Customer: {order.customer_name}")
    print(f"  Total: ₹{order.total}")
    
    print("\n🚚 Create Shipment (Shiprocket)")
    success, msg, shipment = engine.create_shipment(
        order.id, length=30, width=20, height=10, weight=0.5
    )
    print(f"  {msg}")
    print(f"  Courier: {shipment.courier}")
    print(f"  Tracking: {shipment.tracking_url}")
    
    print("\n✅ Update Order Status")
    success, msg = engine.update_order_status(
        order.id, MarketplaceOrderStatus.SHIPPED,
        tracking_number=shipment.awb_number, courier=shipment.courier
    )
    print(f"  {msg}")
    
    print("\n🔄 Sync Inventory to All")
    results = engine.sync_inventory_to_all("prod-rayban-001", 14)
    for r in results:
        print(f"  {r['marketplace']}: {r['old_qty']} → {r['new_qty']} [{r['status']}]")
    
    print("\n📊 Marketplace Summary")
    summary = engine.get_marketplace_summary()
    print(f"  Total Orders: {summary['total_orders']}")
    print(f"  Pending: {summary['pending_orders']}")
    print(f"  Shipped: {summary['shipped_orders']}")
    
    print("\n" + "=" * 60)

if __name__ == "__main__":
    demo_marketplace()
